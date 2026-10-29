import math
from datetime import datetime, timedelta
import httpx
from app.config import settings

class WeatherProvider:
    name = "weather"

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

        # 2. 기준 시간 계산 (초단기실황은 매시각 40분 이후 정시 데이터 제공)
        now = datetime.now()
        if now.minute < 45:
            now -= timedelta(hours=1)
        base_date = now.strftime("%Y%m%d")
        base_time = now.strftime("%H00")

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
                response = await client.get(settings.kma_api_url, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception:
                return None

        # 4. 응답 해석
        try:
            items = data["response"]["body"]["items"]["item"]
        except (KeyError, TypeError):
            return None

        is_raining = False
        temperature = None

        for item in items:
            category = item.get("category")
            obsr_val = item.get("obsrValue")
            if category == "PTY":  # 강수 형태 (0: 없음, 1: 비, 2: 비/눈, 3: 눈 등)
                if obsr_val and obsr_val != "0":
                    is_raining = True
            elif category == "T1H":  # 기온
                if obsr_val:
                    try:
                        temperature = float(obsr_val)
                    except ValueError:
                        pass

        return {
            "is_raining": is_raining,
            "temperature": temperature,
            "source": "kma",
        }