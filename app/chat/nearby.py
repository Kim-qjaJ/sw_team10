"""주변 시설 찾기 모드: 현재 위치 좌표 + 반경 검색 (결과가 없으면 반경 확장)."""

from app.chat.messages import NEARBY_KEYWORD_QUESTION, NEARBY_LOCATION_QUESTION, NEARBY_OUTSIDE_BUSAN
from app.chat.responses import clarify_response, finish_search, simple_response
from app.chat.search import keyword_request
from app.chat.session import has_places
from app.config import settings
from app.schemas.api import ChatRequest, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.local_router import SERVICE_AREA_REPLY, split_query
from app.services.mode_router import NEARBY_RADII_M, format_radius, is_in_busan
from app.services.pipeline import provider_failure_notice


async def handle_nearby(ctx, request: ChatRequest, started: float) -> RecommendResponse:
    session_id = request.session_id
    state = ctx.sessions.get(session_id)
    pending = state.pending if state else None
    query = split_query(request.message, state.intent.location if state and state.intent else None,
                        pending.location if pending else None)
    if query.question:
        if not query.keyword:
            return clarify_response(query.question, started)
        waiting = UserIntent.model_validate({"intent": "search_place", "location": query.location,
                                             "requests": [keyword_request(query, "category")]})
        ctx.sessions.save_pending(session_id, waiting)
        return clarify_response(query.question, started)
    coords = (
        (request.latitude, request.longitude)
        if request.latitude is not None and request.longitude is not None
        else None
    )

    if query.non_busan and not query.location:
        ctx.sessions.save_pending(session_id, None)
        return simple_response(
            None, UserIntent(intent="unsupported", location=query.non_busan[0]), started, "local",
            notice=SERVICE_AREA_REPLY,
        )

    if not query.keyword:
        # 재질문에 기준 장소만 답한 경우('서면역'): 보류 중이던 시설을 그 위치에서 찾는다.
        if pending is not None and pending.requests and query.location:
            intent = pending.model_copy(update={"location": query.location})
            response = await ctx.pipeline.run(intent, request.limit)
            return finish_search(ctx, request, intent, response, started, "api")
        return clarify_response(NEARBY_KEYWORD_QUESTION, started)

    facility = keyword_request(query, "category")
    if facility["search_mode"] == "exact_place":
        facility["search_mode"] = "category"

    # 1) '서면역 약국'처럼 기준 장소를 직접 적으면 현재 위치보다 그 장소를 우선한다.
    if query.location:
        intent = UserIntent.model_validate({
            "intent": "search_place", "location": query.location, "requests": [facility],
        })
        response = await ctx.pipeline.run(intent, request.limit)
        return finish_search(ctx, request, intent, response, started, "api")

    waiting = UserIntent.model_validate({"intent": "search_place", "location": None, "requests": [facility]})

    # 2) 위치 권한이 없으면 기준 장소를 묻고, 답이 오면 위 재질문 처리로 이어서 검색한다.
    if coords is None:
        ctx.sessions.save_pending(session_id, waiting)
        return clarify_response(NEARBY_LOCATION_QUESTION, started)
    if settings.nearby_busan_only and not is_in_busan(*coords):
        ctx.sessions.save_pending(session_id, waiting)
        return clarify_response(NEARBY_OUTSIDE_BUSAN, started)

    # 3) 현재 위치 기준 1km → 3km → 5km 순으로 넓혀 가며 찾는다.
    response: RecommendResponse | None = None
    radius = NEARBY_RADII_M[0]
    for radius in NEARBY_RADII_M:
        response = await ctx.pipeline.run(waiting, request.limit, anchor_override=coords, radius_m=radius)
        if has_places(response) or provider_failure_notice(response.results):
            break

    label = format_radius(radius)
    response.radius_m = radius
    # 화면의 '이렇게 이해했어요'에는 현재 위치 기준임을 보여주고, 세션에는 위치 없이 저장한다.
    response.intent = waiting.model_copy(update={"location": f"현재 위치 {label} 이내"})
    count = sum(len(result.places) for result in response.results)
    if count:
        expanded = f"{format_radius(NEARBY_RADII_M[0])} 안에는 없어서 범위를 넓혔어요. " if radius != NEARBY_RADII_M[0] else ""
        response.message = f"{expanded}현재 위치에서 {label} 안의 {facility['query']} {count}곳을 가까운 순으로 보여드려요."
    elif not provider_failure_notice(response.results):
        response.notice = (
            f"현재 위치에서 {label} 안에 '{facility['query']}' 검색 결과가 없어요. "
            "다른 이름으로 검색하거나 기준 장소를 함께 적어 주세요. (예: 서면역 편의점)"
        )
    return finish_search(ctx, request, waiting, response, started, "api", radius_m=radius, from_user_location=True)
