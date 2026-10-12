"""답변 아래 후속 버튼. 규칙으로 만들고(LLM 미사용) 최대 3개만 보여준다."""

from app.chat.messages import (
    AREA_CHOICES,
    FACILITY_CHOICES,
    NEARBY_KEYWORD_QUESTION,
    SEARCH_EMPTY_QUESTION,
    SEARCH_EXAMPLES,
)
from app.chat.session import SessionState
from app.schemas.api import ChatRequest, RecommendResponse, Suggestion
from app.services.followup import MAX_EXPAND_M, next_radius
from app.services.local_router import route_local_reply


def _sends(*texts: str) -> list[Suggestion]:
    return [Suggestion(label=text, message=text) for text in texts]


def conversation_suggestions(response: RecommendResponse, request: ChatRequest) -> list[Suggestion]:
    """인사·재질문 같은 대화형 답변에도 다음에 할 수 있는 말을 버튼으로 제안한다."""
    intent_type = response.intent.intent
    mode = request.mode
    question = response.message or ""
    if intent_type == "clarify":
        if question == NEARBY_KEYWORD_QUESTION:
            return _sends(*FACILITY_CHOICES)
        if question == SEARCH_EMPTY_QUESTION:
            return _sends(*SEARCH_EXAMPLES)
        if "어느 지역" in question or "기준" in question or "어디" in question:
            return _sends("서면", "해운대", "부산역") if mode != "nearby" else _sends("서면역", "부산역", "해운대역")
        return []
    if intent_type == "smalltalk":
        # 마무리 인사('이제 없어', '고마워', '잘가')에는 더 할 일을 권하지 않는다.
        if getattr(route_local_reply(request.message), "kind", None) in {"closing", "goodbye", "thanks"}:
            return []
        if mode == "search":
            return _sends(*SEARCH_EXAMPLES)
        if mode == "nearby":
            return _sends(*FACILITY_CHOICES)
        if mode == "recommend":
            if request.category:
                return _sends(*AREA_CHOICES)
            return _sends("서면 카페 추천해줘", "해운대 맛집 추천해줘", "광안리 놀거리 추천해줘")
        return [
            Suggestion(label="🔍 장소 검색", mode="search"),
            Suggestion(label="✨ 장소 추천", mode="recommend"),
            Suggestion(label="📍 주변 시설 찾기", mode="nearby"),
        ]
    return []


def build_suggestions(
    response: RecommendResponse,
    state: SessionState | None,
    request: ChatRequest | None = None,
) -> list[Suggestion]:
    intent_type = response.intent.intent
    last = state.last_search if state else None
    more = Suggestion(label="다른 곳 더 보기", message="다른 곳 더 보여줘")

    if intent_type in {"place_detail", "get_route"}:
        return [more] if last else []
    if intent_type in {"clarify", "smalltalk"}:
        return conversation_suggestions(response, request) if request else []
    if intent_type in {"unsupported", "compare_results"}:
        return []

    found = [result for result in response.results if result.places]
    expandable = (
        last is not None
        and last.anchor is not None
        and (next_radius(last.radius_m) or MAX_EXPAND_M + 1) <= MAX_EXPAND_M
    )
    if not found:
        return [Suggestion(label="범위 넓히기", message="범위 넓혀서 찾아줘")] if expandable else []

    first = found[0]
    suggestions: list[Suggestion] = []
    if first.result_type == "exact":
        suggestions += [
            Suggestion(label="이 근처 맛집", message="이 근처 맛집"),
            Suggestion(label="이 근처 카페", message="이 근처 카페"),
        ]
    elif first.result_type == "brand":
        suggestions.append(Suggestion(label="다른 지점 더 보기", message="다른 지점 더 보여줘"))
    else:
        suggestions.append(more)
        if expandable and response.mode == "nearby":
            suggestions.append(Suggestion(label="범위 넓히기", message="범위 넓혀서 찾아줘"))
    suggestions.append(Suggestion(label="1번 길찾기", message="1번 길찾기"))
    if len(suggestions) < 3:
        suggestions.append(Suggestion(label="1번 상세 정보", message="1번 상세 정보 알려줘"))
    return suggestions[:3]
