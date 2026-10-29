"""응답 객체를 만드는 공통 함수."""

import time

from app.schemas.api import ChatRequest, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.response_writer import ResponseWriter


def simple_response(
    message: str | None,
    intent: UserIntent,
    started: float,
    route: str,
    notice: str | None = None,
    intent_llm_ms: float = 0.0,
    response_llm_ms: float = 0.0,
) -> RecommendResponse:
    """검색 없이 문장만 돌려주는 응답 (인사, 재질문, 상세 정보 등)."""
    return RecommendResponse(
        message=message,
        intent=intent,
        results=[],
        notice=notice,
        mock_data=False,
        route=route,
        timing_ms={
            "intent_llm_ms": intent_llm_ms,
            "response_llm_ms": response_llm_ms,
            "total_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )


def clarify_response(question: str, started: float, route: str = "local") -> RecommendResponse:
    return simple_response(question, UserIntent(intent="clarify", question=question), started, route)


def finish_search(
    ctx,
    request: ChatRequest,
    intent: UserIntent,
    response: RecommendResponse,
    started: float,
    route_type: str,
    llm_ms: float = 0.0,
    radius_m: int | None = None,
    from_user_location: bool = False,
    keep_shown: bool = False,
) -> RecommendResponse:
    """검색/주변 시설/후속 질문 공통 마무리: 세션 저장, 템플릿 요약, 처리시간."""
    sessions = ctx.sessions
    sessions.save_search_intent(request.session_id, intent)
    sessions.save_pending(request.session_id, None)
    sessions.save_last_places(request.session_id, response)
    sessions.remember_search(
        request.session_id, intent, response, request.mode,
        radius_m=radius_m, from_user_location=from_user_location, keep_shown=keep_shown,
    )
    response.route = route_type
    if response.message is None and response.results:
        response.message = ResponseWriter.template_summary(response.intent, response.results)
    response.timing_ms = {
        "intent_llm_ms": llm_ms,
        **response.timing_ms,
        "total_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return response
