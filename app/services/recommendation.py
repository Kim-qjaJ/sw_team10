"""후보 장소에 점수를 매겨 순위를 정한다.

[추천 알고리즘 담당] 인터페이스만 정의된 뼈대입니다. pipeline.py가 아래 형식으로 호출합니다.
지금은 점수 없이 반경 확인과 거리 계산만 하고 받은 순서대로 돌려줍니다.
"""

from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.scoring import haversine_m

# 기준 시설(역, 구청 등) 주변 추천의 기본 반경(m)
POINT_RADIUS_M = 1500


class RecommendationService:
    def rank(
        self,
        intent: UserIntent,
        request: PlaceRequest,
        places: list[Place],
        limit: int = 5,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
        sort_by_distance: bool = False,
        stations: list[tuple[str, float, float]] | None = None,
        weather: dict | None = None,
        score_anchor: tuple[float, float] | None = None,
        distance_scale_m: int | None = None,
    ) -> list[Place]:
        """상위 limit개 장소를 점수순으로 반환한다.

        - anchor: 기준 시설·현재 위치·생활권 기준점. 좌표가 있는 후보는 radius_m(기본 POINT_RADIUS_M) 밖이면 제외
        - score_anchor: 동 단위 지역의 대표점. 제외 기준이 아니라 거리 점수에만 쓴다 (distance_scale_m으로 감쇠)
        - sort_by_distance: 주변 시설 찾기에서는 점수보다 가까운 순
        - stations / weather: ContextService가 준 교통·날씨 데이터
        - 결과 Place에 score, rank_score, data_confidence, score_breakdown을 채운다
          (score_breakdown 예: {"distance": {"score": 24.1, "max": 30, "reason": "기준점에서 320m", "available": True}})
        TODO: scoring.py의 항목별 점수로 순위를 정한다.
        """
        ranked: list[Place] = []
        for original in places:
            place = original.model_copy(deep=True)
            if anchor is not None and place.latitude is not None and place.longitude is not None:
                place.distance_m = round(haversine_m(anchor[0], anchor[1], place.latitude, place.longitude), 1)
                if place.distance_m > (radius_m or POINT_RADIUS_M):
                    continue
            ranked.append(place)
        if anchor is not None and sort_by_distance:
            ranked.sort(key=lambda p: p.distance_m if p.distance_m is not None else float("inf"))
        return ranked[:limit]
