"""채팅 요청 진입점: 공통 처리(개수, 바로 답할 질문, 후속 질문) 후 방식(mode)별 핸들러로 보낸다.

    - mode 없음   : 기존처럼 문장만 보고 Local Router / LLM이 판단 (conversation)
    - recommend  : 장소 추천. 2단계에서 고른 종류를 문장에 반영한 뒤 conversation 흐름
    - search     : 장소 검색. 키워드를 장소 API로 바로 검색 (0건일 때만 LLM 재해석)
    - nearby     : 주변 시설. 현재 위치 좌표 기준 반경 검색 (결과가 없으면 반경 확장)
"""

import time

from app.chat.conversation import handle_conversation
from app.chat.followup_handler import handle_followup
from app.chat.messages import MODE_GUIDE
from app.chat.nearby import handle_nearby
from app.chat.responses import simple_response
from app.chat.search import handle_search
from app.chat.session import SessionState
from app.chat.suggestions import build_suggestions
from app.schemas.api import ChatRequest, Link, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.followup import plan_followup
from app.services.local_router import format_place_fields, format_route, requested_count, route_local_reply
from app.services.mode_router import apply_category_hint


async def handle_chat(ctx, request: ChatRequest) -> RecommendResponse:
    started = time.perf_counter()
    # '3개만 추천해줘'처럼 개수를 말하면 그만큼만 보여준다.
    if (count := requested_count(request.message)) is not None:
        request = request.model_copy(update={"limit": count})
    state = ctx.sessions.get(request.session_id)
    if request.latitude is not None and request.longitude is not None:
        if (session := ctx.sessions.ensure(request.session_id)) is not None:
            session.origin = (request.latitude, request.longitude)
            state = session

    response = quick_reply(request, state, started)
    plan = plan_followup(request.message, state.last_search if state else None) if response is None else None
    if response is None and plan is not None:
        # '다른 곳 더', '여기말고 다른 영화관', '이 근처 카페', '범위 넓혀줘'는 직전 검색 조건을 이어서 쓴다.
        response = await handle_followup(ctx, request, plan, state, started)
    if response is None:
        if request.mode == "search":
            response = await handle_search(ctx, request, started)
        elif request.mode == "nearby":
            response = await handle_nearby(ctx, request, started)
        else:
            message = request.message
            if request.mode == "recommend":
                message = apply_category_hint(message, request.category)
            response = await handle_conversation(ctx, request, message, started)

    response.mode = request.mode
    local_kind = getattr(route_local_reply(request.message), "kind", None)
    if response.intent.intent == "smalltalk" and request.mode and response.message and local_kind in {"greeting", "help"}:
        # 인사·도움말에도 지금 고른 방식에서 무엇을 입력하면 되는지 이어서 안내한다.
        response.message = f"{response.message} {MODE_GUIDE[request.mode]}"
    if not response.suggestions:
        response.suggestions = build_suggestions(response, ctx.sessions.get(request.session_id), request)
    return response


def quick_reply(request: ChatRequest, state: SessionState | None, started: float) -> RecommendResponse | None:
    """모드와 상관없이 코드로 바로 답할 수 있는 질문(직전 결과 상세, 길찾기, 인사)을 처리한다."""
    recent_places = state.last_places if state and state.last_places else []

    # 직전 추천 결과의 주소/전화/URL 조회는 저장된 API 결과에서 바로 답한다.
    local_detail = format_place_fields(request.message, recent_places)
    if local_detail is not None:
        if state:
            state.updated_at = time.monotonic()
        return simple_response(local_detail, UserIntent(intent="place_detail"), started, "local")

    # '1번 길찾기', '거기 어떻게 가' → 직전 결과 장소의 카카오맵 길찾기 링크
    route = format_route(request.message, recent_places, origin=state.origin if state else None)
    if route is not None:
        response = simple_response(route[0], UserIntent(intent="place_detail"), started, "local")
        response.links = [Link(**link) for link in route[1]]
        return response

    # 확실한 인사/감사/도움말은 LLM을 호출하지 않는다.
    local_reply = route_local_reply(request.message)
    if local_reply is not None:
        return simple_response(local_reply.message, UserIntent(intent="smalltalk"), started, "local")
    return None
