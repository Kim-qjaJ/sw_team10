"""후보 장소에 점수를 매겨 순위를 정한다.

100점 구조: 종류 30 + 접근성 25 + 사용자 조건 15 + 날씨 10 + 교차정보 10 + 부산 특화 10.
데이터가 없는 항목은 제외하고 남은 항목의 가중치로 100점 만점 환산한다(base_score).
환산 덕분에 정보가 적은 장소가 위로 오르지 않도록 데이터 신뢰도(data_confidence)로 약간 낮춘 값이 최종 추천 점수(score)다.
화면에 보이는 '추천 점수'와 순위 기준은 같은 score다 (rank_score는 예전 이름으로 score와 같은 값).
점수 계산이 실패하면 기본 순위(인기 신호 → 거리 → 입력 순서)로 상위 N개를 돌려준다.
장소 추천에서는 이미 고른 시설의 세부 시설('벡스코' 뒤의 '벡스코 제1전시장')을 건너뛰고 다음 장소로 채운다.
"""

import logging

from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.scoring import (
    access_score, busan_score, category_path, category_score, condition_score, cross_score, haversine_m,
    infer_indoor_with_name, weather_score, weights_for,
)
from app.services.search_radius import POINT_RADIUS_M

logger = logging.getLogger(__name__)

# 기준 시설(역, 구청 등) 주변 추천의 기본 반경은 search_radius.py의 POINT_RADIUS_M을 쓴다.

FALLBACK_REASON = "점수 계산에 실패해 기본 순위(인기·거리순)로 보여줍니다"

# 세부 시설로 볼 최대 거리(m). 같은 시설 안의 전시장·야외 공간은 이 안에 있다 (벡스코 본관~제2전시장 약 200m).
SUB_FACILITY_M = 500


def _near(a: Place, b: Place) -> bool:
    """두 장소가 SUB_FACILITY_M 안인지. 좌표가 없으면 주소가 같을 때만 가깝다고 본다."""
    if None not in (a.latitude, a.longitude, b.latitude, b.longitude):
        return haversine_m(a.latitude, a.longitude, b.latitude, b.longitude) <= SUB_FACILITY_M
    return bool(a.address) and a.address == b.address


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
        """상위 limit개 장소를 점수순으로 반환한다. (인자 의미는 README 참고)"""
        radius = radius_m or POINT_RADIUS_M
        candidates = self._within_radius(places, anchor, radius)
        try:
            ranked = self._score(intent, request, candidates, anchor, distance_scale_m or radius,
                                 stations, weather, score_anchor)
        except Exception:
            logger.exception("추천 점수 계산 실패, 기본 순위로 대체")
            return self._fallback(candidates)[:limit]

        if anchor is not None and sort_by_distance:
            ranked.sort(key=lambda p: p.distance_m if p.distance_m is not None else float("inf"))
        else:
            ranked.sort(key=lambda p: (p.score, p.data_confidence), reverse=True)
        # 같은 시설의 세부 시설이 결과를 채우지 않게 한다 (2026-11 '센텀 전시' 8곳 중 4곳이 벡스코).
        # 특정 장소·브랜드 검색은 지점·세부 시설 목록이 필요한 결과라 적용하지 않는다.
        if request.search_mode == "category":
            ranked = self._skip_sub_facilities(ranked)
        return ranked[:limit]

    @staticmethod
    def _skip_sub_facilities(places: list[Place]) -> list[Place]:
        """이미 고른 장소 이름 + 띄어쓰기로 시작하고 가까운 장소('벡스코 제1전시장')는 뺀다. 순서는 유지한다."""
        kept: list[Place] = []
        for place in places:
            name = place.name.strip()
            parent = next((k for k in kept if name.startswith(k.name.strip() + " ") and _near(k, place)), None)
            if parent is None:
                kept.append(place)
        return kept

    @staticmethod
    def _within_radius(places: list[Place], anchor: tuple[float, float] | None, radius: float) -> list[Place]:
        """원본을 복사하고, 기준점이 있으면 거리를 채워 반경 밖 후보를 제외한다."""
        kept: list[Place] = []
        for original in places:
            place = original.model_copy(deep=True)
            if anchor is not None and place.latitude is not None and place.longitude is not None:
                straight = round(haversine_m(anchor[0], anchor[1], place.latitude, place.longitude), 1)
                place.straight_distance_m = straight
                # 반경은 지리적 검색 범위다. 도로가 돌아가더라도 검색 후보에서 지우지 않는다.
                if straight > radius:
                    continue
                place.distance_m = place.route_distance_m if place.route_distance_m is not None else straight
                place.distance_source = "tmap" if place.route_distance_m is not None else "straight"
            kept.append(place)
        return kept

    @staticmethod
    def _score(
        intent: UserIntent,
        request: PlaceRequest,
        places: list[Place],
        anchor: tuple[float, float] | None,
        scale: float,
        stations: list[tuple[str, float, float]] | None,
        weather: dict | None,
        score_anchor: tuple[float, float] | None,
    ) -> list[Place]:
        weights = weights_for(weather)
        scored: list[Place] = []
        for original in places:
            place = original.model_copy(deep=True)
            has_xy = place.latitude is not None and place.longitude is not None
            route_distance = place.route_distance_m if place.route_mode == (intent.transport_mode or "walk") else None
            if route_distance is not None and (anchor is not None or score_anchor is not None):
                dist = route_distance
                place.distance_m = route_distance
                place.distance_source = "tmap"
            elif anchor is not None:
                # 다른 이동수단으로 조회한 경로가 남아 있으면 그 값을 재사용하지 않는다.
                dist = place.straight_distance_m if place.route_distance_m is not None else place.distance_m
                place.distance_m = dist
                place.distance_source = "straight"
            elif score_anchor is not None and has_xy:
                dist = haversine_m(score_anchor[0], score_anchor[1], place.latitude, place.longitude)
            else:
                dist = None

            path = category_path(place.source_categories, place.subcategory)
            # 실내/야외 정보가 없으면 업종으로 추정해 점수에만 쓴다 (Place.indoor는 바꾸지 않는다).
            indoor = place.indoor if place.indoor is not None else infer_indoor_with_name(path, place.name)
            inferred = place.indoor is None and indoor is not None
            items = {
                "category": category_score(request.category, request.subcategory, request.query,
                                           place.source_categories, place.category),
                "access": access_score(
                    intent.transport_mode, dist, scale, place.latitude, place.longitude, stations,
                    {"walk": place.walking_minutes, "transit": place.transit_minutes, "car": place.driving_minutes},
                    route_distance_m=route_distance if anchor is not None or score_anchor is not None else None,
                ),
                "condition": condition_score(intent.companion, request.indoor, indoor, path, place.category),
                "weather": weather_score(indoor, weather, inferred),
                "cross": cross_score(place.sources, place.popularity_signal),
                "busan": busan_score(place.busan_verified, place.region_match),
            }
            total = used = 0.0
            for key, weight in weights.items():
                item = items[key]
                if item is None:
                    place.score_breakdown[key] = {"score": 0.0, "max": weight, "reason": "정보 없음", "available": False}
                    continue
                value, reason = item
                total += value * weight
                used += weight
                place.score_breakdown[key] = {"score": round(value * weight, 1), "max": weight,
                                              "reason": reason, "available": True}
            place.base_score = round(total / used * 100, 1) if used else 0.0
            place.data_confidence = round(used / sum(weights.values()), 2)
            # 정보가 적은 장소가 환산 덕분에 위로 오르지 않도록 신뢰도를 반영한 값을 최종 점수로 쓴다.
            # (화면 점수와 순위가 어긋나지 않게 표시·정렬 모두 이 값을 쓴다)
            place.score = place.rank_score = round(place.base_score * (0.8 + 0.2 * place.data_confidence), 1)
            scored.append(place)
        return scored

    @staticmethod
    def _fallback(places: list[Place]) -> list[Place]:
        """기본 순위: 인기 신호가 높은 순 → 가까운 순 → 입력(Provider) 순서. 점수는 비워 둔다."""
        for place in places:
            place.score = place.rank_score = place.base_score = place.data_confidence = 0.0
            place.score_breakdown = {"fallback": {"score": 0.0, "max": 0, "reason": FALLBACK_REASON, "available": False}}
        return sorted(places, key=lambda p: (
            -(p.popularity_signal if p.popularity_signal is not None else -1.0),
            p.distance_m if p.distance_m is not None else float("inf"),
        ))
