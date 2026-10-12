"""추천 점수에 쓰는 주변 상황 데이터(날씨, 가까운 지하철역).

[API 담당] 인터페이스만 정의된 뼈대입니다.
mentions_rain / reference_point는 채팅 흐름에서도 쓰는 공용 함수라 동작하는 상태로 둡니다.
"""

import statistics

from app.providers.weather import WeatherProvider
from app.schemas.place import Place

RAIN_WORDS = ("비 오", "비오", "비가", "비 와", "비와", "우산", "장마", "소나기", "눈 오", "눈오", "눈이 와")


def mentions_rain(text: str | None) -> bool:
    """사용자 문장에 비·눈 표현이 있는지 확인한다."""
    return bool(text) and any(word in text for word in RAIN_WORDS)


def reference_point(anchor: tuple[float, float] | None, places: list[Place]) -> tuple[float, float] | None:
    """기준점이 없으면 후보들의 좌표 중앙값을 기준으로 쓴다(지역 검색)."""
    if anchor is not None:
        return anchor
    coords = [(p.latitude, p.longitude) for p in places if p.latitude is not None and p.longitude is not None]
    if not coords:
        return None
    return statistics.median(c[0] for c in coords), statistics.median(c[1] for c in coords)


class ContextService:
    def __init__(self) -> None:
        self.weather_provider = WeatherProvider()

    async def stations(self, point: tuple[float, float] | None) -> list[tuple[str, float, float]]:
        """기준점 주변 지하철역 [(역 이름, 위도, 경도), ...]. 교통 점수에 쓴다.

        TODO: 카카오 카테고리 검색(SW8)으로 반경 내 역을 조회하고 캐시한다.
        """
        return []

    async def weather(self, point: tuple[float, float] | None) -> dict | None:
        """기준점의 현재 날씨. 실패하면 None (점수에서 날씨 항목을 제외한다)."""
        if point is None:
            return None
        return await self.weather_provider.get_weather(latitude=point[0], longitude=point[1])
