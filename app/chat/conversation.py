"""기존 자연어 흐름 (방식 선택 없음 / 장소 추천): 자연어 → (Local Router | LLM) → 검색 → 통합 → 추천."""

import re
import time

from fastapi import HTTPException

from app.chat.followup_handler import handle_followup
from app.chat.intents import (
    GENERIC_FOLLOWUP_QUERIES,
    SEARCHABLE_INTENTS,
    needs_location,
    normalize_search_modes,
    parse_intent,
)
from app.chat.messages import LLM_FALLBACK_NOTICE, MISSING_LOCATION_QUESTION, ROUTE_NEEDS_PLACE
from app.chat.responses import simple_response
from app.schemas.api import ChatRequest, Link, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.context_service import mentions_rain
from app.services.followup import FollowupPlan
from app.services.local_router import format_route, route_place_query
from app.services.response_writer import ResponseWriter


async def handle_conversation(ctx, request: ChatRequest, message: str, started: float) -> RecommendResponse:
    sessions = ctx.sessions
    session_id = request.session_id
    state = sessions.get(session_id)
    previous_intent = state.intent if state else None
    recent_places = state.last_places if state and state.last_places else []
    pending = state.pending if state else None

    # Local Router: 명확한 장소 질의는 LLM 없이 Intent를 만든다.
    route = route_place_query(
        message,
        previous_intent=previous_intent,
        recent_places=recent_places,
        pending_intent=pending,
    )

    if route.kind == "clarify":
        sessions.save_pending(session_id, route.intent)
        return simple_response(
            route.message,
            UserIntent(intent="clarify", question=route.message),
            started,
            "local",
        )

    if route.kind == "unsupported":
        sessions.save_pending(session_id, None)
        return simple_response(None, route.intent or UserIntent(intent="unsupported"), started, "local", notice=route.message)

    if route.kind == "compare":
        reply, response_llm_ms = await ctx.response_writer.compare(message, recent_places)
        if state:
            state.updated_at = time.monotonic()
        return simple_response(
            reply,
            UserIntent(intent="compare_results"),
            started,
            "llm" if response_llm_ms else "local",
            response_llm_ms=response_llm_ms,
        )

    route_type = "local"
    fallback_notice: str | None = None
    llm_ms = 0.0
    if route.kind == "direct" and route.intent is not None:
        intent = normalize_search_modes(route.intent)
        sessions.save_search_intent(session_id, intent)
    else:
        # 애매한 질의만 LLM으로 해석한다.
        try:
            intent, llm_ms = await parse_intent(ctx, message, session_id)
            route_type = "llm"
        except HTTPException:
            if route.fallback is None:
                raise
            # LLM 실패 시에도 코드로 확인된 지역·장소 종류가 있으면 기본 검색을 이어간다.
            intent = normalize_search_modes(route.fallback)
            sessions.save_search_intent(session_id, intent)
            route_type = "llm_fallback"
            fallback_notice = LLM_FALLBACK_NOTICE
    sessions.save_pending(session_id, None)

    if intent.intent == "clarify":
        question = intent.question or MISSING_LOCATION_QUESTION
        return simple_response(question, intent, started, route_type, intent_llm_ms=llm_ms)

    if intent.intent == "smalltalk":
        natural_reply, response_llm_ms = await ctx.response_writer.chat(message)
        return simple_response(natural_reply, intent, started, route_type,
                               intent_llm_ms=llm_ms, response_llm_ms=response_llm_ms)

    if intent.intent == "place_detail":
        natural_reply, response_llm_ms = await ctx.response_writer.write_place_detail(message, recent_places)
        return simple_response(natural_reply, intent, started, route_type,
                               intent_llm_ms=llm_ms, response_llm_ms=response_llm_ms)

    # LLM이 '다른 곳'처럼 뜻 없는 검색어를 만들면 직전 검색을 이어서 다른 곳을 찾는다.
    if (
        intent.intent in SEARCHABLE_INTENTS
        and state is not None
        and state.last_search is not None
        and intent.requests
        and all(re.sub(r"\s+", "", r.query) in GENERIC_FOLLOWUP_QUERIES for r in intent.requests)
    ):
        plan = FollowupPlan(
            requests=[r.model_dump() for r in state.last_search.intent.requests], same_query=True, expand=False,
        )
        return await handle_followup(ctx, request, plan, state, started)

    if intent.intent == "get_route":
        # LLM이 길찾기로 해석한 경우: 직전 결과 장소로 카카오맵 링크를 준다.
        route_link = format_route(message, recent_places, origin=state.origin if state else None, force=True)
        if route_link is None:
            return simple_response(ROUTE_NEEDS_PLACE, intent, started, route_type, intent_llm_ms=llm_ms)
        response = simple_response(route_link[0], intent, started, route_type, intent_llm_ms=llm_ms)
        response.links = [Link(**link) for link in route_link[1]]
        return response

    # 위치를 알 수 없는 일반 카테고리 검색은 전국 결과가 섞이지 않도록 되묻는다.
    if needs_location(intent):
        sessions.save_pending(session_id, intent)
        clarify = UserIntent(intent="clarify", question=MISSING_LOCATION_QUESTION)
        return simple_response(MISSING_LOCATION_QUESTION, clarify, started, route_type, intent_llm_ms=llm_ms)

    # "다른 영화관/카페" 같은 후속 요청은 직전 카드와 같은 장소를 다시 보여주지 않는다.
    exclude_names: set[str] = set()
    if "다른" in message and recent_places:
        exclude_names = {str(place.get("name") or "").strip() for place in recent_places if place.get("name")}

    response = await ctx.pipeline.run(
        intent,
        request.limit,
        message=message,
        exclude_names=exclude_names,
        rain_hint=mentions_rain(message),
    )
    response.route = route_type
    if fallback_notice and not response.notice:
        response.notice = fallback_notice

    # 결과 설명: 명확한 질의는 템플릿 문장, LLM이 해석한 복합 조건은 LLM이 설명한다.
    response_llm_ms = 0.0
    summary = None
    if intent.intent != "unsupported" and response.results:
        if route_type == "llm":
            summary, response_llm_ms = await ctx.response_writer.write(
                user_message=message,
                intent=intent,
                results=response.results,
            )
        summary = summary or ResponseWriter.template_summary(intent, response.results)
    # 결과가 없으면 notice가 이유를 설명하므로 사용자 문장을 그대로 되돌려주지 않는다.
    response.message = summary

    sessions.save_last_places(session_id, response)
    sessions.remember_search(session_id, intent, response, request.mode)

    response.timing_ms = {
        "intent_llm_ms": llm_ms,
        **response.timing_ms,
        "response_llm_ms": response_llm_ms,
        "total_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return response
