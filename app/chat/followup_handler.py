"""후속 질문 처리: 직전 검색 조건(검색어·기준 위치·반경)을 이어서 다시 검색한다.

후속 질문인지 판단하는 규칙은 app/services/followup.py(plan_followup)에 있다.
"""

from app.chat.messages import EXPAND_NOT_POSSIBLE, FOLLOWUP_EXHAUSTED
from app.chat.intents import SEARCHABLE_INTENTS
from app.chat.responses import finish_search, simple_response
from app.chat.session import SessionState, has_places
from app.schemas.api import ChatRequest, RecommendResponse
from app.schemas.intent import PlaceRequest
from app.services.context_service import mentions_rain
from app.services.followup import MAX_EXPAND_M, FollowupPlan, next_radius
from app.services.mode_router import NEARBY_RADII_M, format_radius
from app.services.pipeline import provider_failure_notice


async def handle_followup(
    ctx,
    request: ChatRequest,
    plan: FollowupPlan,
    state: SessionState,
    started: float,
) -> RecommendResponse:
    last = state.last_search
    requests = [PlaceRequest.model_validate(item) for item in plan.requests]
    intent_type = last.intent.intent if last.intent.intent in SEARCHABLE_INTENTS else "search_place"
    if not plan.same_query and intent_type == "search_place" and all(r.search_mode == "category" for r in requests):
        intent_type = "recommend_place"
    intent = last.intent.model_copy(update={"intent": intent_type, "requests": requests})

    anchor, radius = last.anchor, last.radius_m
    display_location = intent.location
    # '부산역' 같은 특정 장소 검색 직후 '이 근처 카페'면 그 장소 좌표를 기준점으로 쓴다.
    if anchor is None and not plan.same_query and any(r.search_mode == "exact_place" for r in last.intent.requests):
        first = next((p for p in (state.last_places or []) if p.get("latitude") is not None), None)
        if first is not None:
            anchor, radius = (first["latitude"], first["longitude"]), 1500
            display_location = f"{first['name']} 근처"
            intent = intent.model_copy(update={"location": first["name"]})

    if plan.expand:
        if anchor is None:
            return simple_response(None, intent, started, "local", notice=EXPAND_NOT_POSSIBLE)
        radius = next_radius(radius)
        if radius is None:
            return simple_response(None, intent, started, "local",
                                   notice=f"이미 최대 범위({format_radius(MAX_EXPAND_M)})까지 찾았어요.")

    exclude = set(last.shown) if plan.same_query else set()
    nearby_like = last.from_user_location or last.mode == "nearby"
    response: RecommendResponse | None = None
    # 결과가 없으면(기준점이 있을 때) 반경을 단계적으로 넓혀 다시 찾는다.
    while True:
        response = await ctx.pipeline.run(
            intent, request.limit, exclude_names=exclude,
            anchor_override=anchor, radius_m=radius if anchor else None,
            rain_hint=mentions_rain(request.message), sort_by_distance=nearby_like,
            raw_order=last.mode == "search",
        )
        if has_places(response) or provider_failure_notice(response.results) or anchor is None:
            break
        bigger = next_radius(radius)
        if bigger is None or bigger > NEARBY_RADII_M[-1] * 2:
            break
        radius = bigger

    response.radius_m = radius if anchor else None
    if last.from_user_location and anchor is not None:
        display_location = f"현재 위치 {format_radius(radius or 1500)} 이내"
    count = sum(len(result.places) for result in response.results)
    query = ", ".join(r.query for r in requests)
    if count:
        widened = f"범위를 {format_radius(radius)}까지 넓혀 " if radius and radius != last.radius_m and anchor else ""
        other = "다른 " if exclude else ""
        response.message = f"{widened}{other}{query} {count}곳을 찾았어요."
    elif not provider_failure_notice(response.results):
        response.notice = FOLLOWUP_EXHAUSTED if exclude else f"'{query}' 검색 결과가 없어요."

    finished = finish_search(
        ctx, request, intent, response, started, "local",
        radius_m=radius if anchor else None,
        from_user_location=last.from_user_location,
        keep_shown=plan.same_query,
    )
    if display_location:
        finished.intent = intent.model_copy(update={"location": display_location})
    # finish_search가 저장한 기준점은 화면용 좌표 대신 실제 검색 기준점으로 맞춘다.
    if state.last_search is not None and anchor is not None:
        state.last_search.anchor = anchor
    return finished
