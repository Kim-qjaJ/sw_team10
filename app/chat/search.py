"""장소 검색 모드: 키워드 → 장소 API 직행 (추천 점수 없이 검색된 순서대로)."""

from fastapi import HTTPException

from app.chat.conversation import handle_conversation
from app.chat.intents import SEARCHABLE_INTENTS, needs_location, normalize_search_modes, parse_intent
from app.chat.messages import SEARCH_EMPTY_QUESTION, SEARCH_RETRY_NOTICE
from app.chat.responses import clarify_response, finish_search
from app.chat.session import has_places
from app.schemas.api import ChatRequest, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.local_router import KeywordQuery, route_place_query, split_query
from app.services.pipeline import provider_failure_notice


def keyword_request(query: KeywordQuery, search_mode: str) -> dict:
    """정리된 키워드를 PlaceRequest 형태로 만든다. 사전의 장소 종류면 그 검색어/카테고리를 쓴다."""
    if query.category is not None:
        text, category, subcategory = query.category
        return {"query": text, "category": category, "subcategory": subcategory, "indoor": None,
                "result_scope": "default", "search_mode": "category"}
    if query.brand is not None:
        search_mode = "brand"
    return {"query": query.keyword, "category": None, "subcategory": None, "indoor": None,
            "result_scope": "default", "search_mode": search_mode}


async def handle_search(ctx, request: ChatRequest, started: float) -> RecommendResponse:
    session_id = request.session_id
    state = ctx.sessions.get(session_id)
    message = request.message

    route = route_place_query(
        message,
        previous_intent=state.intent if state else None,
        recent_places=state.last_places if state and state.last_places else [],
        pending_intent=state.pending if state else None,
    )
    # '그중 ~' 비교, 부산 외 지역은 기존 대화 흐름과 같은 방식으로 답한다.
    if route.kind in {"compare", "unsupported"}:
        return await handle_conversation(ctx, request, message, started)

    if route.kind == "clarify" and (route.intent is not None or route.reason != "지역 이름 또는 범위 중복"):
        ctx.sessions.save_pending(session_id, route.intent)
        return clarify_response(route.message if route.intent is not None else SEARCH_EMPTY_QUESTION, started)

    # 1) 사전으로 확실히 해석되는 문장('부산역 어디야', '서면 스타벅스')은 그 Intent로 바로 검색한다.
    if route.kind == "direct" and route.intent is not None:
        intent = normalize_search_modes(route.intent)
        if intent.intent == "recommend_place":
            intent = intent.model_copy(update={"intent": "search_place"})
        response = await ctx.pipeline.run(intent, request.limit, raw_order=True)
        return finish_search(ctx, request, intent, response, started, "api")

    # 2) 사전에 없는 가게 이름 등은 요청 어미만 떼고 키워드로 검색한다.
    query = split_query(message, state.intent.location if state and state.intent else None,
                        state.pending.location if state and state.pending else None)
    if query.question:
        if not query.keyword:
            return clarify_response(query.question, started)
        waiting = UserIntent.model_validate({"intent": "search_place", "location": query.location,
                                             "requests": [keyword_request(query, "exact_place")]})
        ctx.sessions.save_pending(session_id, waiting)
        return clarify_response(query.question, started)
    if not query.keyword and query.location:
        # '해운대 위치 알려줘'처럼 지명만 남으면 그 장소 자체를 찾는다.
        query = KeywordQuery(location=None, keyword=query.location.removeprefix("부산 ").strip())
    if not query.keyword:
        return clarify_response(SEARCH_EMPTY_QUESTION, started)

    intent: UserIntent | None = None
    response: RecommendResponse | None = None
    # 정확한 상호 일치를 먼저 보고, 없으면 같은 키워드의 일반 검색 결과를 보여준다.
    for search_mode in ("exact_place", "category"):
        intent = UserIntent.model_validate({
            "intent": "search_place",
            "location": query.location,
            "requests": [keyword_request(query, search_mode)],
        })
        response = await ctx.pipeline.run(intent, request.limit, raw_order=True)
        if has_places(response) or provider_failure_notice(response.results) or query.category or query.brand:
            break

    # 3) 키워드로 0건이고 API 자체는 정상이면, 그때만 LLM이 문장을 다시 해석한다.
    llm_ms = 0.0
    if not has_places(response) and not provider_failure_notice(response.results):
        try:
            llm_intent, llm_ms = await parse_intent(ctx, message, session_id)
        except HTTPException:
            llm_intent = None
        if (
            llm_intent is not None
            and llm_intent.intent in SEARCHABLE_INTENTS
            and llm_intent.requests
            and not needs_location(llm_intent)
        ):
            retry = await ctx.pipeline.run(llm_intent, request.limit, raw_order=True)
            if has_places(retry):
                retry.notice = retry.notice or SEARCH_RETRY_NOTICE
                return finish_search(ctx, request, llm_intent, retry, started, "llm", llm_ms)

    return finish_search(ctx, request, intent, response, started, "api", llm_ms)
