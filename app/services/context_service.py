import statistics
import httpx
from app.config import settings
from app.providers.weather import WeatherProvider
from app.schemas.place import Place

RAIN_WORDS = ("비 오", "비오", "비옴", "비가", "비 와", "비와", "우천", "우산", "장마", "소나기", "눈 오", "눈오", "눈옴", "눈이 와")

def mentions_rain(text: str | None) -> bool:
    """사용자 문장에 비/눈 관련 표현이 있는지 확인한다."""
    return bool(text) and any(word in text for word in RAIN_WORDS)

def reference_point(anchor: tuple[float, float] | None, places: list[Place]) -> tuple[float, float] | None:
    """기준점이 없으면 후보들의 좌표 중앙값을 기준으로 삼는다(지역 검색)."""
    if anchor is not None:
        return anchor
    coords = [(p.latitude, p.longitude) for p in places if p.latitude is not None and p.longitude is not None]
    if not coords:
        return None
    return statistics.median([c[0] for c in coords]), statistics.median([c[1] for c in coords])

class ContextService:
    def __init__(self):
        self.weather_provider = WeatherProvider()
        self._stations_cache: dict[tuple[float, float], list[tuple[str, float, float]]] = {}

    async def stations(self, point: tuple[float, float] | None) -> list[tuple[str, float, float]]:
        """기준점 주변 지하철역 [(역 이름, 위도, 경도), ...]을 카카오 API로 조회하고 캐싱한다."""
        if point is None:
            return []

        # 소수점 3자리로 둥글려서 캐시 키로 사용
        cache_key = (round(point[0], 3), round(point[1], 3))
        if cache_key in self._stations_cache:
            return self._stations_cache[cache_key]

        api_key = settings.kakao_rest_api_key
        if not api_key:
            return []

        url = "https://dapi.kakao.com/v2/local/search/category.json"
        headers = {"Authorization": f"KakaoAK {api_key}"}
        params = {
            "category_group_code": "SW8",  # 지하철역 카테고리 코드
            "y": point[0],  # 위도
            "x": point[1],  # 경도
            "radius": 2000, # 2km 반경
            "size": 5,
            "sort": "distance",
        }

        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            try:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception:
                return []

        documents = data.get("documents", [])
        stations_list: list[tuple[str, float, float]] = []

        for doc in documents:
            name = doc.get("place_name", "")
            lat = float(doc["y"]) if doc.get("y") else None
            lon = float(doc["x"]) if doc.get("x") else None
            if name and lat is not None and lon is not None:
                stations_list.append((name, lat, lon))

        self._stations_cache[cache_key] = stations_list
        return stations_list

    async def weather(self, point: tuple[float, float] | None) -> dict | None:
        """기준점의 현재 날씨. 실패하면 None을 반환한다."""
        if point is None:
            return None
        return await self.weather_provider.get_weather(latitude=point[0], longitude=point[1])