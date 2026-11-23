"""주변 시설 찾기 모드: 현재 위치 좌표 + 반경 검색 (결과가 없으면 반경 확장)."""

import re
import time

from app.chat.messages import NEARBY_KEYWORD_QUESTION, NEARBY_LOCATION_QUESTION, NEARBY_OUTSIDE_BUSAN, max_radius_reached
from app.chat.responses import clarify_response, finish_search, simple_response
from app.chat.search import keyword_request
from app.chat.session import has_places
from app.config import settings
from app.schemas.api import ChatRequest, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.local_router import SERVICE_AREA_REPLY, split_query
from app.services.followup import EXPAND_PATTERN
from app.services.mode_router import NEARBY_RADII_M, format_radius, is_in_busan
from app.services.pipeline import provider_failure_notice


def _reply_place(query) -> str:
    """기준 장소 재질문에 대한 답('대항항이요', '가덕도 대항항')을 위치 문구로 만든다."""
    area = re.sub(r"^부산\s*", "", query.location or "").strip()
    word = re.sub(r"(이에요|예요|이요|요|에서|기준으로|기준|근처|주변|부근)$", "", query.keyword.strip()).strip()
    return " ".join(part for part in (area, word) if part)


async def _search_at(ctx, request: ChatRequest, intent: UserIntent, started: float) -> RecommendResponse:
    """기준 장소에서 찾는다. 기준 장소를 못 찾으면 시설은 그대로 두고 다음 답을 기준 장소로 받는다."""
    response = await ctx.pipeline.run(intent, request.limit)
    if not response.results and response.intent and response.intent.intent != "clarify":
        # 위치를 못 찾은 경우: 같은 질문을 되풀이하지 않고 이유(notice)를 보여 준 뒤, 다음 답을 기준 장소로 쓴다.
        ctx.sessions.save_pending(request.session_id, intent.model_copy(update={"location": None}))
        response.route = "api"
        response.timing_ms = {**response.timing_ms,
                              "total_ms": round((time.perf_counter() - started) * 1000, 2)}
        return response
    # 기준 장소 주변에 없으면 현재 위치 검색처럼 1.5km → 3km → 5km로 넓히고, 넓힌 사실을 알린다.
    radius = None
    if (response.context or {}).get("anchor") and not has_places(response) and not provider_failure_notice(response.results):
        for radius in NEARBY_RADII_M[1:]:
            response = await ctx.pipeline.run(intent, request.limit, radius_m=radius)
            if has_places(response) or provider_failure_notice(response.results):
                break
        if has_places(response):
            widened = (f"기준 장소 주변 {format_radius(NEARBY_RADII_M[0])} 안에는 없어서 "
                       f"{format_radius(radius)}까지 넓혀 찾았어요.")
            response.notice = f"{widened} {response.notice}" if response.notice else widened
        elif not provider_failure_notice(response.results):
            # 이미 최대 반경이므로 '범위를 넓혀 보라'고 하지 않는다 (2026-11-23 검토).
            place = re.sub(r"\s*(근처|주변|부근)$", "", intent.location or "") or None
            response.notice = max_radius_reached(intent.requests[0].query, place, format_radius(NEARBY_RADII_M[-1]))
    result = finish_search(ctx, request, intent, response, started, "api", radius_m=radius)
    if not has_places(result):
        # 결과가 없으면 시설은 그대로 두고, 다음 답('대항항', '가덕도동')을 새 기준 장소로 받는다.
        ctx.sessions.save_pending(request.session_id, intent.model_copy(update={"location": None}))
    return result


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

    awaiting_place = pending is not None and bool(pending.requests) and not pending.location
    if awaiting_place and EXPAND_PATTERN.search(request.message) and query.category is None:
        # 최대 반경까지 찾고 기준 장소를 기다리는 중에 '범위 넓혀줘': 시설을 묻지 않고 기준 장소를 다시 청한다.
        return simple_response(None, pending, started, "local",
                               notice=max_radius_reached(pending.requests[0].query, None, format_radius(NEARBY_RADII_M[-1])))
    if not query.keyword:
        # 재질문에 기준 장소만 답한 경우('서면역'): 보류 중이던 시설을 그 위치에서 찾는다.
        if pending is not None and pending.requests and query.location:
            return await _search_at(ctx, request, pending.model_copy(update={"location": query.location}), started)
        return clarify_response(NEARBY_KEYWORD_QUESTION, started)
    if awaiting_place and query.category is None and query.brand is None:
        # 사전에 없는 기준 장소만 답한 경우('대항항'): 시설 이름이 아니라 기준 장소로 본다.
        # 예전에는 이 답을 검색어로 받아 '현재 위치를 확인하지 못했어요'를 되풀이했다 (2026-11-23).
        place = _reply_place(query)
        if place:
            return await _search_at(ctx, request, pending.model_copy(update={"location": f"{place} 근처"}), started)

    facility = keyword_request(query, "category")
    if facility["search_mode"] == "exact_place":
        facility["search_mode"] = "category"

    # 1) '서면역 약국'처럼 기준 장소를 직접 적으면 현재 위치보다 그 장소를 우선한다.
    if query.location:
        intent = UserIntent.model_validate({
            "intent": "search_place", "location": query.location, "requests": [facility],
        })
        return await _search_at(ctx, request, intent, started)

    waiting = UserIntent.model_validate({"intent": "search_place", "location": None, "requests": [facility]})

    # 2) 위치 권한이 없으면 기준 장소를 묻고, 답이 오면 위 재질문 처리로 이어서 검색한다.
    if coords is None:
        ctx.sessions.save_pending(session_id, waiting)
        return clarify_response(NEARBY_LOCATION_QUESTION, started)
    if settings.nearby_busan_only and not is_in_busan(*coords):
        ctx.sessions.save_pending(session_id, waiting)
        return clarify_response(NEARBY_OUTSIDE_BUSAN, started)

    # 3) 현재 위치 기준 1.5km → 3km → 5km 순으로 넓혀 가며 찾는다.
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
