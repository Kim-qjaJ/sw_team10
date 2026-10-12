import asyncio
import time

from app.config import settings
from app.schemas.api import RecommendResponse, RequestResult
from app.schemas.intent import UserIntent
from app.services.api_router import APIRouterService
from app.services.context_service import ContextService, reference_point
from app.services.place_merger import PlaceMerger
from app.services.location_resolver import LocationResolver
from app.schemas.place import Place
from app.services.recommendation import POINT_RADIUS_M, RecommendationService
from app.services.scoring import haversine_m
from app.services.exact_place_matcher import ExactPlaceMatcher
from app.services.hard_filter import HardFilter
from app.services.region_registry import get_region_registry

UNSUPPORTED_NOTICE = (
    "장소 추천이나 검색 요청으로 이해하지 못했습니다. "
    "지역과 가고 싶은 장소를 함께 적어 주세요. 예: 서면에서 조용한 카페 추천해줘"
)
RATE_LIMIT_NOTICE = "장소 API 호출 한도를 초과해 결과를 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
PROVIDER_ERROR_NOTICE = "장소 API 오류로 결과를 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
NOT_CONFIGURED_NOTICE = "장소 검색 API 키가 설정되지 않았어요. .env의 KAKAO/NAVER 키를 확인해 주세요."


def provider_failure_notice(results: list[RequestResult]) -> str | None:
    """결과가 없을 때 '0건'과 'API 실패'를 구분해 안내한다."""
    statuses = [status.status for result in results for status in result.providers.values()]
    if not statuses or "ok" in statuses:
        return None
    if "rate_limited" in statuses:
        return RATE_LIMIT_NOTICE
    if all(status == "not_configured" for status in statuses):
        return NOT_CONFIGURED_NOTICE
    return PROVIDER_ERROR_NOTICE


def result_type_for(search_mode: str, raw_order: bool = False) -> str:
    if raw_order and search_mode == "category":
        return "search"
    return {"exact_place": "exact", "brand": "brand"}.get(search_mode, "recommend")


def in_provider_order(
    places: list[Place],
    limit: int,
    anchor: tuple[float, float] | None = None,
    radius_m: int | None = None,
) -> list[Place]:
    """장소 검색은 추천 점수 없이 Provider가 준 순서(카카오 정확도 → 네이버) 그대로 보여준다."""
    ordered: list[Place] = []
    for original in places:
        place = original.model_copy(deep=True)
        if anchor is not None and place.latitude is not None and place.longitude is not None:
            place.distance_m = round(haversine_m(anchor[0], anchor[1], place.latitude, place.longitude), 1)
            if place.distance_m > (radius_m or POINT_RADIUS_M):
                continue
        ordered.append(place)
    return ordered[:limit]


def in_busan(place: Place) -> bool:
    """주소가 있는 후보는 부산 주소인지 확인한다. 주소가 없으면 판단하지 않고 남긴다."""
    return not place.address or "부산" in place.address


class RecommendationPipeline:
    """Intent 이후의 검색 → 통합 → 점수 계산 흐름을 담당한다."""

    def __init__(self) -> None:
        self.api_router = APIRouterService()
        self.merger = PlaceMerger()
        self.location_resolver = LocationResolver()
        self.recommender = RecommendationService()
        self.exact_matcher = ExactPlaceMatcher()
        self.hard_filter = HardFilter()
        self.context = ContextService()


    @staticmethod
    def _filter_explicit_area(places, location: str | None, anchor):
        if anchor is not None or not location:
            return places
        import re
        text = re.sub(r"^(부산광역시|부산시|부산)\s*", "", location.strip()).strip()
        # 동/읍/면은 도로명 주소에 나타나지 않아 정상 결과까지 지워지므로 구/군만 검증한다.
        match = re.search(r"([가-힣]+(?:구|군))$", text)
        if not match:
            return places
        area = match.group(1)
        # '해운대' 같은 생활권은 여기 들어오지 않고, '해운대구'처럼 명시된 행정구역만 검증한다.
        verified = [p for p in places if p.address and area in p.address]
        return verified

    async def run(
        self,
        intent: UserIntent,
        limit: int,
        message: str | None = None,
        exclude_names: set[str] | None = None,
        anchor_override: tuple[float, float] | None = None,
        radius_m: int | None = None,
        rain_hint: bool = False,
        sort_by_distance: bool | None = None,
        raw_order: bool = False,
    ) -> RecommendResponse:
        """anchor_override/radius_m: 주변 시설 찾기처럼 좌표를 이미 알고 있을 때 위치 해석을 건너뛰고
        해당 좌표 기준 반경 검색을 한다. 이 경우 결과는 가까운 순으로 정렬한다."""
        started = time.perf_counter()

        if intent.intent == "unsupported":
            return RecommendResponse(
                message=message,
                intent=intent,
                results=[],
                notice=UNSUPPORTED_NOTICE,
                mock_data=settings.use_mock_places,
                timing_ms={"pipeline_ms": round((time.perf_counter() - started) * 1000, 2)},
            )

        # "OO 근처"처럼 특정 기준점이 있으면 Kakao에서 좌표를 먼저 얻는다.
        # 지역 대표 좌표는 특정 시설의 반경 검색 기준점으로 쓰지 않는다.
        if anchor_override is not None:
            location_ref = self.location_resolver.from_current_location(*anchor_override)
        else:
            location_ref = await self.location_resolver.resolve_text(intent.location)
        if location_ref.mode == "ambiguous":
            return RecommendResponse(
                message=location_ref.question, intent=UserIntent(intent="clarify", question=location_ref.question),
                results=[], mock_data=settings.use_mock_places,
                timing_ms={"pipeline_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
        if location_ref.mode == "not_found":
            return RecommendResponse(
                message=message,
                intent=intent,
                results=[],
                notice=f"'{intent.location}' 위치를 부산에서 찾지 못했어요. 지역명이나 장소 이름을 다시 확인해 주세요.",
                mock_data=settings.use_mock_places,
                timing_ms={"pipeline_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
        anchor = None
        has_point = location_ref.latitude is not None and location_ref.longitude is not None
        if location_ref.mode in {"specific_place", "current_location", "lifestyle_area"} and has_point:
            anchor = (location_ref.latitude, location_ref.longitude)
        # 생활권은 기준 시설에서 생활권 반경까지만 추천한다 (Provider 반경 검색 설정은 바꾸지 않는다).
        rank_radius = location_ref.radius_m if location_ref.mode == "lifestyle_area" else radius_m
        registry = get_region_registry()
        region = registry.regions.get(location_ref.region_code or "")
        # 동 단위 지역은 경계로 거른 뒤, 대표점까지의 거리를 약한 점수로만 쓴다(제외 기준 아님).
        # 구 전체나 리(읍·면 중심 근사값)는 중심 거리가 의미 없어 적용하지 않는다.
        score_anchor = None
        if anchor is None and region is not None and region.level in {"hdong", "bdong"} and has_point:
            score_anchor = (location_ref.latitude, location_ref.longitude)
        distance_scale_m = location_ref.radius_m if (score_anchor or location_ref.mode == "lifestyle_area") else None

        # 위치 없이 들어온 특정 장소/브랜드 검색은 부산 범위로 한정한다.
        search_location = None if anchor_override is not None else (location_ref.resolved_name or intent.location or "부산")

        # requests[]의 각 장소 요구를 병렬로 검색한다.
        search_started = time.perf_counter()
        searches = await asyncio.gather(
            *(self.api_router.search(request=req, location=search_location, anchor=anchor, radius_m=radius_m) for req in intent.requests)
        )
        search_ms = (time.perf_counter() - search_started) * 1000

        results: list[RequestResult] = []
        merge_ms = 0.0
        ranking_ms = 0.0
        context_ms = 0.0
        # 날씨는 요청 전체에 한 번만 조회한다. 사용자가 '비 오는데'라고 말했으면 그 말을 우선한다.
        weather: dict | None = None
        weather_checked = False
        for req, (places, statuses) in zip(intent.requests, searches):
            step = time.perf_counter()
            merged = self.merger.merge(places)
            merged_count = len(merged)
            # 명시적인 행정구역(구/군)은 Provider의 텍스트 관련도만 믿지 않고
            # 최종 주소로 다시 검증한다. 생활권(서면/해운대 등)은 행정구역으로 추측하지 않는다.
            merged = self._filter_explicit_area(merged, intent.location, anchor)
            # 서비스 지역은 부산이다. '부산 강서고 카페'에 서울 강서구 결과가 섞이지 않게 주소로 거른다.
            # (현재 위치 반경 검색은 좌표 자체를 이미 부산으로 검증했다.)
            if anchor_override is None:
                merged = [p for p in merged if in_busan(p)]
            if region is not None:
                region_places = []
                for place in merged:
                    if place.latitude is not None and place.longitude is not None:
                        inside = registry.contains(region, place.latitude, place.longitude)
                        if inside is False:
                            continue
                        exact_boundary = registry.boundary_codes(region)[1] == "administrative"
                        place.region_match = "inside" if inside is True and exact_boundary else "unverified"
                    else:
                        if place.address and region.sgg not in place.address:
                            continue
                        place.region_match = "unverified"
                    region_places.append(place)
                merged = region_places
            # 명시적 조건(영업 불가, 실내/야외, 카테고리)을 위반한 후보를 제거한다.
            merged = self.hard_filter.apply(req, merged)
            if exclude_names:
                normalized_excludes = {name.strip().casefold() for name in exclude_names}
                merged = [p for p in merged if p.name.strip().casefold() not in normalized_excludes]
            filtered_count = len(merged)
            merge_ms += (time.perf_counter() - step) * 1000

            step = time.perf_counter()
            if req.search_mode == "exact_place":
                # '동아대학교' 같은 상위 기관명은 exact 1건으로 축소하지 않고
                # 대표 캠퍼스를 첫 번째로 둔 뒤 관련 캠퍼스들을 함께 보여준다.
                institution_locations = self.exact_matcher.institution_locations(req.query, merged)
                if institution_locations:
                    effective_limit = 20 if req.result_scope == "all" else limit
                    ranked = institution_locations[:effective_limit]
                else:
                    matched = self.exact_matcher.match(req.query, merged)
                    if matched is not None:
                        ranked = [matched]
                    else:
                        representative = self.exact_matcher.representative(req.query, merged)
                        if representative is not None and "본점" in representative.name:
                            ranked = [representative]
                        else:
                            branches = self.exact_matcher.related_branches(req.query, merged)
                            if raw_order:
                                ranked = in_provider_order(branches, 20 if req.result_scope == "all" else limit, anchor)
                            else:
                                ranked = self.recommender.rank(
                                    intent=intent, request=req, places=branches,
                                    limit=(20 if req.result_scope == "all" else limit), anchor=anchor,
                                ) if branches else []
            elif raw_order:
                ranked = in_provider_order(merged, 20 if req.result_scope == "all" else limit, anchor, rank_radius)
            else:
                effective_limit = 20 if req.result_scope == "all" else limit
                # 추천 검색에는 교통(지하철역)·상황(날씨) 데이터를 붙여 점수를 계산한다.
                context_started = time.perf_counter()
                point = reference_point(anchor, merged)
                if point is None and region is not None:
                    point = (region.latitude, region.longitude)
                if not weather_checked:
                    weather = await self.context.weather(point)
                    if rain_hint:
                        weather = {**(weather or {}), "is_raining": True, "source": (weather or {}).get("source", "user")}
                    weather_checked = True
                stations = await self.context.stations(point)
                context_ms += (time.perf_counter() - context_started) * 1000
                ranking_started = time.perf_counter()
                ranked = self.recommender.rank(
                    intent=intent, request=req, places=merged, limit=effective_limit, anchor=anchor,
                    radius_m=rank_radius,
                    sort_by_distance=(anchor_override is not None) if sort_by_distance is None else sort_by_distance,
                    stations=stations, weather=weather,
                    score_anchor=score_anchor, distance_scale_m=distance_scale_m,
                )
                step = ranking_started
            ranking_ms += (time.perf_counter() - step) * 1000

            # 개발자 모드에서 Provider 검색 이후 어느 단계에서 후보가 줄었는지 추적한다.
            for status in statuses.values():
                if status.detail:
                    status.detail += f" | merged={merged_count}, filtered={filtered_count}, final={len(ranked)}"
                else:
                    status.detail = f"merged={merged_count}, filtered={filtered_count}, final={len(ranked)}"
            results.append(RequestResult(
                request=req,
                places=ranked,
                providers=statuses,
                result_type=result_type_for(req.search_mode, raw_order),
            ))

        notice = None
        if all(not result.places for result in results):
            notice = provider_failure_notice(results)
        if notice is None and all(not result.places for result in results):
            has_exact = any(req.search_mode == "exact_place" for req in intent.requests)
            notice = (
                "요청한 이름과 일치하는 장소나 지점을 찾지 못했습니다. 장소 이름이나 지역을 다시 확인해 주세요."
                if has_exact
                else "검색 결과는 있었지만 현재 조건을 만족하는 장소가 없습니다."
            )

        return RecommendResponse(
            message=message,
            intent=intent,
            results=results,
            notice=notice,
            mock_data=settings.use_mock_places,
            context={
                key: value
                for key, value in (("weather", weather), ("anchor", list(anchor) if anchor else None),
                                   ("region", registry.public_info(region) if region else None),
                                   ("lifestyle", {"name": location_ref.resolved_name, "radius_m": location_ref.radius_m,
                                                  "coordinate_accuracy": location_ref.coordinate_accuracy,
                                                  "source": location_ref.source}
                                    if location_ref.mode == "lifestyle_area" else None))
                if value
            } or None,
            timing_ms={
                "search_ms": round(search_ms, 2),
                "context_ms": round(context_ms, 2),
                "merge_ms": round(merge_ms, 2),
                "ranking_ms": round(ranking_ms, 2),
                "pipeline_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
