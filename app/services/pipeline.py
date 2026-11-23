import asyncio
import re
import time

from app.config import settings
from app.schemas.api import RecommendResponse, RequestResult
from app.schemas.intent import UserIntent
from app.services.api_router import APIRouterService
from app.providers.busan import wants_exhibit
from app.providers.tour import wants_festival
from app.services.search_keyword import search_keywords, unverifiable_condition
from app.services.context_service import ContextService, reference_point
from app.services.place_merger import PlaceMerger
from app.services.location_resolver import LocationResolver
from app.schemas.place import Place
from app.services.recommendation import POINT_RADIUS_M, RecommendationService
from app.services.scoring import haversine_m
from app.services.exact_place_matcher import ExactPlaceMatcher
from app.services.hard_filter import HardFilter
from app.services.region_registry import get_region_registry
from app.services.router_lexicon import INDOOR_WORDS
from app.services.route_distance import ROUTE_MIN_M, RouteBudget, RouteDistanceService
from app.services.mode_router import format_radius
from app.services.travel_limit import LABELS, apply_travel_limit, estimated_minutes, within_limit

UNSUPPORTED_NOTICE = (
    "장소 추천이나 검색 요청으로 이해하지 못했습니다. "
    "지역과 가고 싶은 장소를 함께 적어 주세요. 예: 서면에서 조용한 카페 추천해줘"
)
RATE_LIMIT_NOTICE = "장소 API 호출 한도를 초과해 결과를 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
PROVIDER_ERROR_NOTICE = "장소 API 오류로 결과를 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
NOT_CONFIGURED_NOTICE = "장소 검색 API 키가 설정되지 않았어요. .env의 KAKAO/NAVER 키를 확인해 주세요."


def location_not_found_notice(location_ref, fallback: str | None) -> str:
    """기준 장소를 못 찾았을 때 이유와 다음에 적을 수 있는 말을 함께 알려 준다."""
    name = location_ref.resolved_name or fallback
    text = f"'{name}' 위치를 부산에서 찾지 못했어요. 지도 검색에 부산 안의 같은 이름 장소가 없어요."
    if location_ref.similar_name:
        text += f" 비슷한 곳으로 '{location_ref.similar_name}'이 있어요."
    return text + " 가까운 역·해수욕장·동 이름으로 다시 알려 주세요. (예: 가덕도동, 하단역)"


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


def apply_rain_hint(weather: dict | None, rain_hint: bool) -> dict | None:
    """사용자가 '비 오는데'라고 말했으면 실제 관측과 관계없이 비 오는 날로 계산한다.

    관측상 비가 오지 않는데 사용자 말로 바꾼 경우 user_hint=True를 남겨,
    답변에서 '지금 비가 와서'가 아니라 '비가 온다고 하셔서'라고 말하게 한다.
    """
    if not rain_hint:
        return weather
    if weather and weather.get("is_raining"):
        return weather
    return {**(weather or {}), "is_raining": True, "precipitation": "rain", "user_hint": True,
            "source": (weather or {}).get("source", "user")}


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
        self.route_distance = RouteDistanceService()


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

    async def _search_words(self, req, companion, location, anchor, radius_m):
        """검색어 여러 개로 찾아 합친다. 같은 Provider의 같은 장소가 두 번 나오면 앞의 것만 남긴다."""
        words = search_keywords(req, companion)
        found = await asyncio.gather(*(
            self.api_router.search(request=req.model_copy(update={"query": word}),
                                   location=location, anchor=anchor, radius_m=radius_m)
            for word in words
        ))
        places, seen = [], set()
        for result_places, _ in found:
            for place in result_places:
                key = tuple(sorted(place.source_ids.items())) or (place.name, place.latitude, place.longitude)
                if key in seen:
                    continue
                seen.add(key)
                places.append(place)
        statuses = {}
        for _, result_statuses in found:
            for name, status in result_statuses.items():
                current = statuses.get(name)
                if current is None:
                    statuses[name] = status.model_copy()
                else:
                    current.count += status.count
                    current.elapsed_ms = max(current.elapsed_ms, status.elapsed_ms)
                    if current.status != "ok" and status.status == "ok":
                        current.status, current.detail = status.status, status.detail
        return places, statuses

    async def run(
        self,
        intent: UserIntent,
        limit: int,
        message: str | None = None,
        exclude_names: set[str] | None = None,
        exclude_words: list[str] | None = None,
        anchor_override: tuple[float, float] | None = None,
        radius_m: int | None = None,
        rain_hint: bool = False,
        sort_by_distance: bool | None = None,
        raw_order: bool = False,
    ) -> RecommendResponse:
        """anchor_override/radius_m: 주변 시설 찾기처럼 좌표를 이미 알고 있을 때 위치 해석을 건너뛰고
        해당 좌표 기준 반경 검색을 한다. 이 경우 결과는 가까운 순으로 정렬한다."""
        started = time.perf_counter()
        # 응답 목표 시간까지 남은 초. 검색이 늦어지면 날씨·경로 조회에 쓸 시간이 줄어든다 (NFR-001).
        def time_left() -> float:
            return settings.response_deadline_seconds - (time.perf_counter() - started)

        # '비 오는 날'은 날씨 점수(비 오는 날 실내 가점)로 반영한다. LLM이 이것을 실내 조건(indoor=true)으로
        # 바꾸면 하드 필터가 해수욕장·공원을 후보에서 지워 버려서, 사용자가 '실내'를 직접 말했을 때만 남긴다.
        if rain_hint and not any(word in (message or "") for word in INDOOR_WORDS):
            intent = intent.model_copy(deep=True)
            for req in intent.requests:
                if req.indoor is True:
                    req.indoor = None

        # '걸어서 5분 이내'처럼 이동 시간 상한을 말했으면 결과에 실제로 적용한다.
        # 조건은 Intent 필드(max_travel_minutes)에 두어 후속 질문·요약에서도 같은 값을 쓴다. 대화 흐름에서 채우지
        # 않은 경로(검색·주변 모드)는 문장에서 읽는다.
        intent = apply_travel_limit(intent, message)
        limit_rule = ((intent.transport_mode or "walk", intent.max_travel_minutes)
                      if intent.max_travel_minutes else None)
        limit_met: list[bool] = []
        limit_estimated = False

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
                notice=location_not_found_notice(location_ref, intent.location),
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
            *(
                # '책 읽을 곳'·'조용한 카페' 같은 설명형 검색어는 검색할 때만 업종 단어로 바꾸거나 나눠 찾는다
                # (표시·점수는 원래 검색어).
                self._search_words(req, intent.companion, search_location, anchor, radius_m)
                for req in intent.requests
            )
        )
        search_ms = (time.perf_counter() - search_started) * 1000

        results: list[RequestResult] = []
        merge_ms = 0.0
        ranking_ms = 0.0
        context_ms = 0.0
        routing_ms = 0.0
        routing_info = []
        route_budget = None
        # 날씨는 요청 전체에 한 번만 조회한다. 사용자가 '비 오는데'라고 말했으면 그 말을 우선한다.
        weather: dict | None = None
        weather_checked = False
        any_found = False
        for req, (places, statuses) in zip(intent.requests, searches):
            step = time.perf_counter()
            merged = self.merger.merge(places)
            merged_count = len(merged)
            any_found = any_found or merged_count > 0
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
            if exclude_words:
                # '스타벅스는 빼고': 이름에 그 말이 들어간 지점은 모두 뺀다 (공백·대소문자 무시).
                words = [re.sub(r"\s+", "", w).casefold() for w in exclude_words if w.strip()]
                merged = [p for p in merged if not any(w in re.sub(r"\s+", "", p.name).casefold() for w in words)]
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
                    # 날씨와 지하철역은 서로 기다릴 필요가 없으므로 동시에 조회한다 (NFR-001 응답 시간).
                    try:
                        weather, stations = await asyncio.wait_for(
                            asyncio.gather(self.context.weather(point), self.context.stations(point)),
                            timeout=max(0.05, time_left() - 0.3),
                        )
                    except asyncio.TimeoutError:
                        weather, stations = None, []  # 늦으면 날씨·역 없이 점수를 계산한다
                    weather = apply_rain_hint(weather, rain_hint)
                    weather_checked = True
                else:
                    try:
                        stations = await asyncio.wait_for(self.context.stations(point), timeout=max(0.05, time_left() - 0.3))
                    except asyncio.TimeoutError:
                        stations = []
                context_ms += (time.perf_counter() - context_started) * 1000
                # 경로 API 대기는 순위 계산 시간에서 분리한다. 실제 기준점 또는 동 대표점을 사용한다.
                # 후보 중앙값은 사용자의 출발지가 아니므로 경로 계산 기준으로 쓰지 않는다.
                route_started = time.perf_counter()
                route_origin = anchor if anchor is not None else score_anchor
                candidates = self.recommender._within_radius(merged, anchor, rank_radius or POINT_RADIUS_M)
                if route_budget is None:
                    route_budget = RouteBudget.start(time_left() - 0.2)
                candidates, route_info = await self.route_distance.enrich(
                    candidates, route_origin, intent.transport_mode, route_budget,
                )
                route_info["origin_type"] = location_ref.mode
                routing_info.append(route_info)
                routing_ms += (time.perf_counter() - route_started) * 1000
                if limit_rule and route_origin is not None:
                    inside = within_limit(candidates, *limit_rule)
                    limit_met.append(bool(inside))
                    if inside:
                        candidates = inside
                    for place in candidates:
                        # 경로 시간이 없어 직선거리로 추정한 곳은 카드에 '약 N분 (추정)'으로 보여준다.
                        value, guessed = estimated_minutes(place, limit_rule[0])
                        if guessed and value is not None:
                            place.estimated_travel_minutes = round(value, 1)
                            limit_estimated = limit_estimated or bool(inside)
                ranking_started = time.perf_counter()
                ranked = self.recommender.rank(
                    intent=intent, request=req, places=candidates, limit=effective_limit, anchor=anchor,
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
        widened = None
        if location_ref.widened_from:
            # 기준이 바뀐 사실은 결과가 있든 없든 먼저 밝힌다.
            similar = f" (비슷한 곳: {location_ref.similar_name})" if location_ref.similar_name else ""
            widened = (f"'{location_ref.widened_from}' 위치를 찾지 못해 기준을 '{location_ref.resolved_name}' 전체로 "
                       f"넓혀 찾았어요{similar}.")
        if notice is None and all(not result.places for result in results):
            has_exact = any(req.search_mode == "exact_place" for req in intent.requests)
            if has_exact:
                notice = "요청한 이름과 일치하는 장소나 지점을 찾지 못했습니다. 장소 이름이나 지역을 다시 확인해 주세요."
            elif any(wants_festival(req) for req in intent.requests):
                # 끝났거나 아직 시작하지 않은 축제는 일부러 빼므로, 없다고만 하지 않고 그 이유를 알린다.
                notice = ("오늘 열리고 있는 축제·행사를 찾지 못했어요. 이미 끝났거나 아직 시작하지 않은 축제는 보여주지 않아요. "
                          "'부산 축제'처럼 지역을 넓혀 볼 수 있어요.")
            elif not any_found and anchor is not None:
                # 후보가 하나도 없었는데 '조건을 만족하지 않는다'고 하면 이유가 틀린다 (2026-11-23).
                radius = format_radius(rank_radius or POINT_RADIUS_M)
                notice = (f"'{location_ref.resolved_name or intent.location}' 주변 {radius} 안에서 검색 결과가 없어요. "
                          "범위를 넓히거나 다른 기준 장소·지역 이름으로 다시 물어봐 주세요.")
            elif not any_found:
                notice = "검색 결과가 없어요. 다른 이름이나 지역으로 다시 물어봐 주세요."
            else:
                notice = "검색 결과는 있었지만 현재 조건을 만족하는 장소가 없습니다."

        # 전시 결과에는 휴관 반영 범위를 알린다: 정기 휴관은 뺐지만 임시 휴관은 알 수 없다.
        if any(wants_exhibit(r.request) and r.places for r in results):
            exhibit_notice = "오늘 정기 휴관인 전시장은 뺐어요. 임시 휴관은 확인할 수 없으니 방문 전 시설 공지를 확인해 주세요."
            notice = f"{notice} {exhibit_notice}" if notice else exhibit_notice

        # '조용한 카페'의 '조용한'처럼 장소 데이터로 확인할 수 없는 조건은 확인한 것처럼 보이지 않게 알린다.
        conditions = [c for c in (unverifiable_condition(r.request) for r in results if r.places) if c]
        if conditions:
            names = ", ".join(f"‘{c}’" for c in dict.fromkeys(conditions))
            condition_notice = f"{names} 같은 조건은 장소 정보로 확인할 수 없어 검색어로만 반영했어요."
            notice = f"{notice} {condition_notice}" if notice else condition_notice

        # 경로를 못 받은 후보가 있어도, 실제로 보여주는 장소에 해당할 때만 알린다.
        estimated = sum(
            1 for result in results for place in result.places
            if place.distance_source == "straight" and (place.straight_distance_m or 0) >= ROUTE_MIN_M
        )
        if estimated and any(info["fallback"] for info in routing_info):
            route_notice = f"{estimated}곳은 실제 길 거리를 받지 못해 직선거리 추정으로 계산했어요."
            notice = f"{notice} {route_notice}" if notice else route_notice
        if widened:
            notice = f"{widened} {notice}" if notice else widened
        time_limit = None
        if limit_rule and any(r.places for r in results):
            mode, minutes = limit_rule
            condition = f"{LABELS[mode]} {minutes}분"
            if not limit_met:
                limit_notice = (f"기준 장소가 정해지지 않아 '{condition} 이내' 조건은 적용하지 못했어요. "
                                "역이나 건물 이름을 함께 적어 주세요. (예: 부산역에서 걸어서 5분)")
            elif not all(limit_met):
                limit_notice = (f"{condition} 안에 드는 곳을 찾지 못해 기준 장소 주변 결과를 보여 드려요. "
                                "카드의 예상 소요 시간을 확인해 주세요.")
            elif limit_estimated:
                limit_notice = f"{condition} 안에 드는 곳만 골랐어요. 경로 시간을 받지 못한 곳은 직선거리로 추정했어요."
            else:
                limit_notice = None
            notice = f"{limit_notice} {notice}" if notice and limit_notice else (limit_notice or notice)
            time_limit = {"mode": mode, "minutes": minutes, "met": bool(limit_met) and all(limit_met)}

        return RecommendResponse(
            message=message,
            intent=intent,
            results=results,
            notice=notice,
            mock_data=settings.use_mock_places,
            context={
                key: value
                for key, value in (("weather", weather), ("routing", routing_info), ("time_limit", time_limit),
                                   ("anchor", list(anchor) if anchor else None),
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
                "routing_ms": round(routing_ms, 2),
                "pipeline_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
