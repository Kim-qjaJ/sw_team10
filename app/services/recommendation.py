"""후보 장소에 점수를 매겨 순위를 정한다.

100점 구조: 종류 30 + 접근성 25 + 사용자 조건 15 + 날씨 10 + 교차정보 10 + 부산 특화 10.
데이터가 없는 항목은 제외하고 남은 항목의 가중치로 100점 만점 환산한다(score).
순위(rank_score)는 데이터 신뢰도(data_confidence)를 반영해 정보가 적은 장소를 약간 낮춘다.
점수 계산이 실패하면 기본 순위(인기 신호 → 거리 → 입력 순서)로 상위 N개를 돌려준다.
"""

import logging

from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.scoring import (
    WEIGHTS, access_score, busan_score, category_path, category_score, condition_score, cross_score, haversine_m,
    infer_indoor, weather_score,
)

logger = logging.getLogger(__name__)

# 기준 시설(역, 구청 등) 주변 추천의 기본 반경(m)
POINT_RADIUS_M = 1500

FALLBACK_REASON = "점수 계산에 실패해 기본 순위(인기·거리순)로 보여줍니다"


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
            ranked.sort(key=lambda p: (p.rank_score, p.data_confidence), reverse=True)
        return ranked[:limit]

    @staticmethod
    def _within_radius(places: list[Place], anchor: tuple[float, float] | None, radius: float) -> list[Place]:
        """원본을 복사하고, 기준점이 있으면 거리를 채워 반경 밖 후보를 제외한다."""
        kept: list[Place] = []
        for original in places:
            place = original.model_copy(deep=True)
            if anchor is not None and place.latitude is not None and place.longitude is not None:
                place.distance_m = round(haversine_m(anchor[0], anchor[1], place.latitude, place.longitude), 1)
                if place.distance_m > radius:
                    continue
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
        scored: list[Place] = []
        for original in places:
            place = original.model_copy(deep=True)
            has_xy = place.latitude is not None and place.longitude is not None
            if anchor is not None:
                dist = place.distance_m
            elif score_anchor is not None and has_xy:
                dist = haversine_m(score_anchor[0], score_anchor[1], place.latitude, place.longitude)
            else:
                dist = None

            path = category_path(place.source_categories, place.subcategory)
            # 실내/야외 정보가 없으면 업종으로 추정해 점수에만 쓴다 (Place.indoor는 바꾸지 않는다).
            indoor = place.indoor if place.indoor is not None else infer_indoor(path)
            inferred = place.indoor is None and indoor is not None
            items = {
                "category": category_score(request.category, request.subcategory, request.query,
                                           place.source_categories, place.category),
                "access": access_score(
                    intent.transport_mode, dist, scale, place.latitude, place.longitude, stations,
                    {"walk": place.walking_minutes, "transit": place.transit_minutes, "car": place.driving_minutes},
                ),
                "condition": condition_score(intent.companion, request.indoor, indoor, path, place.category),
                "weather": weather_score(indoor, weather, inferred),
                "cross": cross_score(place.sources, place.popularity_signal),
                "busan": busan_score(place.busan_verified, place.region_match),
            }
            total = used = 0.0
            for key, weight in WEIGHTS.items():
                item = items[key]
                if item is None:
                    place.score_breakdown[key] = {"score": 0.0, "max": weight, "reason": "정보 없음", "available": False}
                    continue
                value, reason = item
                total += value * weight
                used += weight
                place.score_breakdown[key] = {"score": round(value * weight, 1), "max": weight,
                                              "reason": reason, "available": True}
            place.score = round(total / used * 100, 1) if used else 0.0
            place.data_confidence = round(used / sum(WEIGHTS.values()), 2)
            # 정보가 적은 장소가 환산 덕분에 위로 오르지 않도록 순위에는 신뢰도를 반영한다.
            place.rank_score = round(place.score * (0.8 + 0.2 * place.data_confidence), 1)
            scored.append(place)
        return scored

    @staticmethod
    def _fallback(places: list[Place]) -> list[Place]:
        """기본 순위: 인기 신호가 높은 순 → 가까운 순 → 입력(Provider) 순서. 점수는 비워 둔다."""
        for place in places:
            place.score = place.rank_score = place.data_confidence = 0.0
            place.score_breakdown = {"fallback": {"score": 0.0, "max": 0, "reason": FALLBACK_REASON, "available": False}}
        return sorted(places, key=lambda p: (
            -(p.popularity_signal if p.popularity_signal is not None else -1.0),
            p.distance_m if p.distance_m is not None else float("inf"),
        ))
