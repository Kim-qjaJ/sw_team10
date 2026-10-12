"""LLM Intent 해석과 해석 결과 보정."""

from fastapi import HTTPException

from app.llm.base import IntentParseError, LLMRateLimitedError, LLMUnavailableError
from app.schemas.intent import UserIntent
from app.services.local_router import needs_session_context
from app.services.router_lexicon import KNOWN_BRANDS

SEARCHABLE_INTENTS = {"recommend_place", "search_place", "get_event"}
GENERIC_CATEGORY_QUERIES = {"지하철역", "영화관", "카페", "음식점", "술집", "도서관", "병원", "맛집"}
# LLM이 만든 이런 검색어는 뜻이 없으므로 직전 검색을 이어서 '다른 곳'으로 처리한다.
GENERIC_FOLLOWUP_QUERIES = {"다른곳", "다른장소", "다른데", "곳", "장소", "여기", "거기", "그곳", "딴곳", "딴데"}

# 새 장소명이 명시된 질문을 직전 추천의 place_detail로 오인하지 않도록 한다.
NEW_PLACE_SEARCH_HINTS = (
    "대학교", "대학", "캠퍼스", "학교", "영화관", "카페", "음식점", "식당",
    "술집", "도서관", "병원", "공원", "해수욕장", "박물관", "미술관",
)


def should_force_new_search(message: str, intent: UserIntent) -> bool:
    """새 장소/시설명이 직접 언급되면 place_detail보다 새 검색을 우선한다."""
    if intent.intent != "place_detail":
        return False
    text = message.strip()
    # '1번/거기/그곳' 등 명시적 직전결과 참조는 상세질문으로 유지한다.
    if any(marker in text for marker in ("1번", "2번", "3번", "4번", "5번", "거기", "그곳", "그 가게", "그 카페", "그 식당")):
        return False
    return any(hint in text for hint in NEW_PLACE_SEARCH_HINTS)


def rebuild_search_intent(message: str, previous: UserIntent | None, parsed: UserIntent) -> UserIntent:
    """LLM이 새 장소 질문을 place_detail로 오분류했을 때 최소한의 검색 Intent로 복구한다."""
    text = message.strip().rstrip("?!., ")
    query = text
    for suffix in ("가 어디있어", "가 어디 있어", "은 어디있어", "은 어디 있어", "는 어디있어", "는 어디 있어", " 위치", " 알려줘"):
        if query.endswith(suffix):
            query = query[:-len(suffix)].strip()
            break
    if not query:
        return parsed
    category = "education" if any(word in query for word in ("대학교", "대학", "캠퍼스", "학교")) else None
    return UserIntent.model_validate({
        "intent": "search_place",
        "location": None,
        "requests": [{
            "query": query,
            "category": category,
            "subcategory": None,
            "indoor": None,
            "result_scope": "default",
            "search_mode": "exact_place",
        }],
        "companion": None,
        "transport_mode": None,
    })


def normalize_search_modes(intent: UserIntent) -> UserIntent:
    """LLM 분류 흔들림 때문에 일반 시설/브랜드가 exact_place가 되는 것을 방지한다."""
    data = intent.model_dump()
    for request in data.get("requests", []):
        query = str(request.get("query") or "").strip()
        if query in GENERIC_CATEGORY_QUERIES:
            request["search_mode"] = "category"
        elif query in KNOWN_BRANDS:
            request["search_mode"] = "brand"
    return UserIntent.model_validate(data)


def merge_with_session(previous: UserIntent | None, current: UserIntent, message: str = "") -> UserIntent:
    """명시적인 새 검색에는 이전 조건을 섞지 않고, 참조형 후속 질문에만 문맥을 상속한다."""
    if previous is None or current.intent in {"smalltalk", "place_detail", "compare_results", "clarify", "unsupported"}:
        return current

    text = message.strip()
    followup_markers = ("그중", "그 중", "거기", "그곳", "아까", "그럼", "대신", "다른", "또", "나머지")
    is_followup = any(marker in text for marker in followup_markers)
    if not is_followup:
        return current

    data = current.model_dump()
    if current.location is None:
        data["location"] = previous.location
    if current.companion is None:
        data["companion"] = previous.companion
    if current.transport_mode is None:
        data["transport_mode"] = previous.transport_mode
    if not current.requests and previous.requests:
        data["requests"] = [request.model_dump() for request in previous.requests]
    return UserIntent.model_validate(data)


def needs_location(intent: UserIntent) -> bool:
    """위치 없이 일반 카테고리만 검색하면 전국 결과가 섞이므로 재질문한다."""
    return (
        intent.intent in SEARCHABLE_INTENTS
        and not intent.location
        and bool(intent.requests)
        and all(request.search_mode == "category" for request in intent.requests)
    )


async def parse_intent(ctx, message: str, session_id: str | None = None) -> tuple[UserIntent, float]:
    """LLM으로 문장을 Intent로 바꾼다. LLM 오류는 화면에서 구분할 수 있는 HTTP 오류로 바꾼다."""
    state = ctx.sessions.get(session_id)
    previous_intent = state.intent if state else None

    # 세션 정보는 참조 표현이 있거나 생략이 많은 후속 질문일 때만 LLM에 전달한다.
    context_intent = previous_intent if needs_session_context(message) else None

    try:
        parsed_intent, llm_ms = await ctx.intent_parser.parse(message, previous_intent=context_intent)
    except LLMRateLimitedError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "llm_rate_limited",
                "message": "LLM 호출 한도를 초과했어요. 잠시 후 다시 시도하거나 '서면 카페 추천해줘'처럼 "
                           "지역과 장소 종류를 함께 적으면 LLM 없이 바로 검색할 수 있어요.",
                "debug": str(exc)[:300],
            },
        )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail={"code": "llm_unavailable", "message": str(exc)})
    except IntentParseError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "intent_parse_failed",
                "message": "요청을 해석하지 못했습니다. 지역과 장소를 넣어 다시 표현해 주세요.",
                "debug": str(exc)[:500],
            },
        )

    if should_force_new_search(message, parsed_intent):
        parsed_intent = rebuild_search_intent(message, previous_intent, parsed_intent)
    intent = normalize_search_modes(merge_with_session(previous_intent, parsed_intent, message))
    ctx.sessions.save_search_intent(session_id, intent)
    return intent, llm_ms
