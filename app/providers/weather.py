import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from app.config import settings
from app.services.safe_error import public_error

KST = ZoneInfo("Asia/Seoul")
# 초단기실황은 한 시간에 한 번 갱신되므로 (격자, 기준 시각)이 같으면 다시 호출하지 않는다.
CACHE_MAX_ENTRIES = 256
# 강수형태(PTY) 코드 → 표현. 0은 강수 없음.
PRECIPITATION_TYPES = {"1": "rain", "2": "sleet", "3": "snow", "5": "rain", "6": "sleet", "7": "snow"}


def kma_endpoint(url: str) -> str:
    """KMA_API_URL에 기능 이름이 빠져 있으면(서비스 주소까지만 적은 경우) 초단기실황 조회를 붙인다.
    서비스 주소만 부르면 '해당 오픈API 서비스가 없거나 폐기됨'(HTTP 400)이 온다."""
    url = url.rstrip("/")
    return url if url.rsplit("/", 1)[-1].startswith("get") else f"{url}/getUltraSrtNcst"


class WeatherProvider:
    name = "weather"

    def __init__(self) -> None:
        self._cache: dict[tuple[int, int, str, str], dict] = {}
        # 날씨는 실패해도 추천을 막지 않고 None을 돌려주므로, 원인을 /api/v1/status에서 볼 수 있게 남긴다.
        self.last_error: str | None = None
        self.last_ok: str | None = None

    def status(self) -> dict:
        return {"last_ok": self.last_ok, "last_error": self.last_error, "cached": len(self._cache)}

    def _dfs_xy(self, lat: float, lon: float) -> tuple[int, int]:
        """위도/경도를 기상청 격자 좌표(nx, ny)로 변환하는 내부 격자 변환 함수"""
        RE = 6371.00877  # 지구 반경(km)
        GRID = 5.0       # 격자 간격(km)
        SLAT1 = 30.0     # 투영 위도1(degree)
        SLAT2 = 60.0     # 투영 위도2(degree)
        OLON = 126.0     # 기준점 경도(degree)
        OLAT = 38.0      # 기준점 위도(degree)
        XO = 43          # 기준점 X좌표(GRID)
        YO = 136         # 기준점 Y좌표(GRID)

        DEGRAD = math.pi / 180.0
        re = RE / GRID
        slat1 = SLAT1 * DEGRAD
        slat2 = SLAT2 * DEGRAD
        olon = OLON * DEGRAD
        olat = OLAT * DEGRAD

        sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
        sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
        sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
        sf = math.pow(sf, sn) * math.cos(slat1) / sn
        ro = math.tan(math.pi * 0.25 + olat * 0.5)
        ro = re * sf / math.pow(ro, sn)

        ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
        ra = re * sf / math.pow(ra, sn)
        theta = lon * DEGRAD - olon
        if theta > math.pi:
            theta -= 2.0 * math.pi
        if theta < -math.pi:
            theta += 2.0 * math.pi
        theta *= sn

        nx = int(math.floor(ra * math.sin(theta) + XO + 0.5))
        ny = int(math.floor(ro - ra * math.cos(theta) + YO + 0.5))
        return nx, ny

    async def get_weather(self, latitude: float, longitude: float) -> dict | None:
        # 1. API 키 확인
        api_key = settings.public_data_service_key
        if not api_key or not settings.kma_api_url:
            return None

        nx, ny = self._dfs_xy(latitude, longitude)
        url = kma_endpoint(settings.kma_api_url)

        # 2. 기준 시간 계산 (초단기실황은 매시각 40분 이후 정시 데이터 제공)
        # 서버 시간대와 관계없이 한국 시간으로 계산한다.
        now = datetime.now(KST)
        if now.minute < 45:
            now -= timedelta(hours=1)
        base_date = now.strftime("%Y%m%d")
        base_time = now.strftime("%H00")

        cache_key = (nx, ny, base_date, base_time)
        if cache_key in self._cache:
            return dict(self._cache[cache_key])

        params = {
            "serviceKey": api_key,
            "numOfRows": 10,
            "pageNo": 1,
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": nx,
            "ny": ny,
        }

        # 3. 비동기 HTTP 요청
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                # 예외 문자열에는 serviceKey가 든 요청 주소가 들어갈 수 있어 종류·상태 코드만 남긴다.
                self.last_error = public_error(exc, "초단기실황")
                return None

        # 4. 응답 해석
        try:
            items = data["response"]["body"]["items"]["item"]
        except (KeyError, TypeError):
            header = (data.get("response") or {}).get("header") if isinstance(data, dict) else None
            code = (header or {}).get("resultCode") if isinstance(header, dict) else None
            self.last_error = f"응답에 관측값 없음 ({base_date} {base_time}, 격자 {nx},{ny}, 결과 코드 {code or '없음'})"
            return None

        is_raining = False
        precipitation = None
        temperature = None

        for item in items:
            category = item.get("category")
            obsr_val = item.get("obsrValue")
            if category == "PTY":  # 강수 형태 (0: 없음, 1: 비, 2: 비/눈, 3: 눈 등)
                if obsr_val and str(obsr_val) != "0":
                    is_raining = True
                    precipitation = PRECIPITATION_TYPES.get(str(obsr_val), "rain")
            elif category == "T1H":  # 기온
                if obsr_val:
                    try:
                        temperature = float(obsr_val)
                    except ValueError:
                        pass

        result = {
            "is_raining": is_raining,
            "precipitation": precipitation,
            "temperature": temperature,
            "source": "kma",
        }
        self.last_ok = f"{base_date} {base_time} 격자 {nx},{ny}"
        self.last_error = None
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache.clear()
        self._cache[cache_key] = result
        return dict(result)

# 단기예보 발표 시각 (매일 02·05·08·11·14·17·20·23시, 발표 10분 뒤부터 조회 가능)
FORECAST_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)


def forecast_endpoint(url: str) -> str:
    """같은 서비스(VilageFcstInfoService_2.0)의 단기예보 조회 주소."""
    url = url.rstrip("/")
    if url.rsplit("/", 1)[-1].startswith("get"):
        url = url.rsplit("/", 1)[0]
    return f"{url}/getVilageFcst"


def forecast_base(now: datetime) -> datetime:
    """지금 조회할 수 있는 가장 최근 단기예보 발표 시각."""
    ready = now - timedelta(minutes=10)
    hours = [h for h in FORECAST_HOURS if h <= ready.hour]
    if hours:
        return ready.replace(hour=hours[-1], minute=0, second=0, microsecond=0)
    return (ready - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)


def summarize_forecast(items: list[dict]) -> dict[str, dict]:
    """예보 항목을 날짜별로 묶는다: 최대 강수확률, 비·눈 예보 시각, 최저·최고 기온."""
    days: dict[str, dict] = {}
    for item in items:
        day = days.setdefault(str(item.get("fcstDate")), {"pop": None, "wet": [], "kinds": set(), "temps": [],
                                                           "tmn": None, "tmx": None})
        category, value, hour = item.get("category"), str(item.get("fcstValue", "")), str(item.get("fcstTime", ""))
        try:
            number = float(value)
        except ValueError:
            number = None
        if category == "POP" and number is not None:
            day["pop"] = max(day["pop"] or 0, int(number))
        elif category == "PTY" and value not in {"", "0"}:
            day["wet"].append(hour)
            day["kinds"].add(PRECIPITATION_TYPES.get(value, "rain"))
        elif category == "TMP" and number is not None:
            day["temps"].append(number)
        elif category == "TMN" and number is not None:
            day["tmn"] = number
        elif category == "TMX" and number is not None:
            day["tmx"] = number
    for day in days.values():
        temps = day.pop("temps")
        if day["tmn"] is None and temps:
            day["tmn"] = min(temps)
        if day["tmx"] is None and temps:
            day["tmx"] = max(temps)
        day["wet"] = sorted(set(day["wet"]))
        day["kinds"] = sorted(day["kinds"])
    return days


async def get_forecast(provider: "WeatherProvider", latitude: float, longitude: float) -> dict | None:
    """기상청 단기예보(오늘~3일 뒤)를 날짜별로 요약한다. 실패하면 None."""
    api_key = settings.public_data_service_key
    if not api_key or not settings.kma_api_url:
        return None
    nx, ny = provider._dfs_xy(latitude, longitude)
    base = forecast_base(datetime.now(KST))
    base_date, base_time = base.strftime("%Y%m%d"), base.strftime("%H00")
    cache_key = (nx, ny, base_date, "fcst" + base_time)
    if cache_key in provider._cache:
        return provider._cache[cache_key]
    params = {"serviceKey": api_key, "numOfRows": 1000, "pageNo": 1, "dataType": "JSON",
              "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny}
    try:
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.get(forecast_endpoint(settings.kma_api_url), params=params)
            response.raise_for_status()
            items = response.json()["response"]["body"]["items"]["item"]
    except Exception as exc:
        provider.last_error = public_error(exc, "단기예보")
        return None
    result = {"days": summarize_forecast(items), "issued": base.strftime("%Y-%m-%d %H:00"), "source": "kma_forecast"}
    if len(provider._cache) >= CACHE_MAX_ENTRIES:
        provider._cache.clear()
    provider._cache[cache_key] = result
    return result
