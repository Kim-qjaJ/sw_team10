"""후속 질문 처리: '다른 곳 더', '여기말고 다른 영화관', '이 근처 카페', '범위 넓혀줘'.

직전 검색의 조건(검색어, 기준 위치, 반경)을 세션에 저장해 두고, 후속 질문은 그 조건에서
바뀐 부분만 반영해 다시 검색한다. 사전에 없는 새 조건('조용한 곳')이 섞이면 기존 흐름(LLM)에 맡긴다.
"""

import re
from dataclasses import dataclass, field

from app.schemas.intent import UserIntent
from app.services.search_radius import EXPAND_STEPS_M, MAX_EXPAND_M, POINT_RADIUS_M  # noqa: F401
from app.services.local_router import COMPARE_PATTERN, build_category_requests, parse_message, requested_route

EXPAND_PATTERN = re.compile(r"넓|멀리|반경|범위")
# '3번 근처 맛집'처럼 직전 결과의 한 장소 주변을 찾는 표현
OTHER_PATTERN = re.compile(r"다른|말고|딴|또|더\s?보")
NEAR_PATTERN = re.compile(r"근처|주변|부근|가까운")
ORDINAL_TOKEN = re.compile(r"\d{1,2}번(째)?|(첫|두|세|네|다섯)(번째)?|번째")
# '스타벅스는 빼고', '이디야 제외하고', '스타벅스 말고' → 앞의 이름을 결과에서 뺀다.
EXCLUDE_PATTERN = re.compile(r"([0-9A-Za-z가-힣]+?)(?:은|는|을|를|이|가|도)?\s*(?:빼고|빼줘|빼서|제외하고|제외해|제외|말고)")
# '해운대로 바꿔줘', '전포에서 다시', '서면 대신 전포' → 지역만 바꾸고 종류·개수는 그대로 둔다.
SWAP_PATTERN = re.compile(r"바꿔|바꾸|변경|옮겨|대신|다시\s?(찾|추천|보여|알려)")
SWAP_WORDS = re.compile(r"^(바꿔|바꾸|변경|옮겨|대신|다시)[가-힣]*$")
_REFERENCE_WORDS = {"여기", "거기", "저기", "이곳", "그곳", "저곳", "이거", "그거", "이건", "그건", "방금", "아까", "위", "다른"}
PLACE_NUMBER_PATTERN = re.compile(r"(?<!\d)\d{1,2}\s?번|(다섯|첫|두|세|네)\s?번\s?째")
# 반경을 넓힐 때는 현재 반경보다 큰 첫 단계로 넓힌다. 단계 값은 search_radius.py에 있다.


@dataclass
class LastSearch:
    """세션에 저장하는 직전 검색 조건."""

    intent: UserIntent
    mode: str | None = None
    anchor: tuple[float, float] | None = None
    radius_m: int | None = None
    # 같은 검색어로 지금까지 보여준 장소 이름. '다른 곳 더'에서 제외한다.
    shown: list[str] = field(default_factory=list)
    # 사용자의 현재 위치로 검색했는지 (화면 표시와 길찾기 출발지에 쓴다)
    from_user_location: bool = False
    # 보여 준 개수 ('한 곳만 추천해줘' 다음 '해운대로 바꿔줘'도 한 곳만)
    limit: int | None = None
    # 결과에서 빼기로 한 이름 ('스타벅스는 빼고' 다음 '다른 곳 더'에도 계속 뺀다. 새 검색이면 비운다)
    exclude_words: list[str] = field(default_factory=list)


@dataclass
class FollowupPlan:
    requests: list[dict]
    same_query: bool
    expand: bool
    # 결과에서 뺄 이름 ('스타벅스는 빼고')
    exclude_words: list[str] = field(default_factory=list)
    # 지역만 바꿔 다시 찾을 때의 새 지역 ('해운대로 바꿔줘')
    location: str | None = None


def next_radius(current: int | None) -> int | None:
    current = current or POINT_RADIUS_M
    for step in EXPAND_STEPS_M:
        if step > current:
            return step
    return None


def plan_followup(message: str, last: LastSearch | None) -> FollowupPlan | None:
    if last is None or not last.intent.requests:
        return None
    text = message.strip()
    if COMPARE_PATTERN.search(text) or requested_route(text):
        return None

    parsed = parse_message(text)
    expand = bool(EXPAND_PATTERN.search(text))

    # 지역만 바꾸는 후속 질문: 직전 종류·개수를 그대로 두고 지역만 바꾼다 (2026-11-23 검토:
    # '전포로 바꿔줘'를 LLM이 '이전 검색 조건 유지'라는 검색어로 바꿔 0건이 됐다).
    swap_location = _swapped_location(text, parsed)
    if swap_location is not None:
        return FollowupPlan(requests=[r.model_dump() for r in last.intent.requests], same_query=False,
                            expand=False, location=swap_location)

    # 제외 조건: 뺄 이름을 검색어에서 지우고, 결과에서도 걸러 낸다 (예전에는 '스타벅스는 빼고'가 스타벅스 검색이 됐다).
    excluded = [w for w in EXCLUDE_PATTERN.findall(text) if w not in _REFERENCE_WORDS and not ORDINAL_TOKEN.fullmatch(w)]
    exclude_words = [w for w in excluded if w not in parsed.categories]
    if excluded:
        parsed.brands = [b for b in parsed.brands if b not in excluded]
        dropped = [c for c in parsed.categories if c in excluded]
        parsed.categories = [c for c in parsed.categories if c not in excluded]
        parsed.unknown = [w for w in parsed.unknown if w not in excluded]
        if dropped and not parsed.categories:
            # '카페 말고'만 말하면 무엇을 찾을지 모르므로 일반 흐름에 맡긴다.
            return None
    # '3번 근처 맛집': 직전 결과 번호로 가리킨 장소 주변 검색도 후속 질문이다.
    near_numbered = bool(NEAR_PATTERN.search(text) and PLACE_NUMBER_PATTERN.search(text))
    if not (parsed.followup or parsed.reference or expand or near_numbered or exclude_words):
        return None
    # 새 위치를 직접 말했거나 사전에 없는 조건이 섞이면 일반 흐름에서 다시 해석한다.
    # '3번', '두 번째' 같은 번호 표현은 사전에 없는 단어로 보지 않는다.
    unknown = [word for word in parsed.unknown if not ORDINAL_TOKEN.fullmatch(word)]
    if [loc for loc in parsed.locations if loc != "부산"] or parsed.non_busan or unknown:
        return None

    if near_numbered and len(parsed.categories) > 1:
        # '카페 2번 근처 맛집': 앞의 '카페'는 장소를 가리키는 말이고, 찾을 종류는 '근처' 뒤의 '맛집'이다.
        near_at = NEAR_PATTERN.search(text).start()
        after = [c for c in parsed.categories if text.find(c, near_at) >= 0]
        if after:
            parsed.categories = after
    requests = build_category_requests(parsed)
    if not requests:
        requests = [request.model_dump() for request in last.intent.requests]
    new_queries = {r["query"] for r in requests}
    old_queries = {r.query for r in last.intent.requests}
    # '맛집이랑 카페' 다음 '카페만 다른 곳'처럼 직전 묶음 중 일부만 다시 찾을 때도 이미 본 곳은 뺀다.
    same_query = new_queries == old_queries or (new_queries < old_queries and bool(OTHER_PATTERN.search(text)))
    return FollowupPlan(requests=requests, same_query=same_query and not exclude_words, expand=expand,
                        exclude_words=exclude_words)


def _swapped_location(text: str, parsed) -> str | None:
    """'해운대로 바꿔줘', '서면 대신 전포' → 새 지역. 종류·브랜드를 새로 말했으면 None."""
    locations = [loc for loc in parsed.locations if loc != "부산"]
    if not locations or parsed.categories or parsed.brands or parsed.non_busan:
        return None
    if [w for w in parsed.unknown if not SWAP_WORDS.match(w)]:
        return None
    if not SWAP_PATTERN.search(text):
        return None
    if "대신" in text:
        after = text.split("대신", 1)[1]
        later = [loc for loc in locations if loc in after]
        locations = later or locations
    elif len(locations) > 1:
        return None
    location = locations[-1]
    return location if location.startswith("부산") else f"부산 {location}"
