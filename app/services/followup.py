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


@dataclass
class FollowupPlan:
    requests: list[dict]
    same_query: bool
    expand: bool


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
    # '3번 근처 맛집': 직전 결과 번호로 가리킨 장소 주변 검색도 후속 질문이다.
    near_numbered = bool(NEAR_PATTERN.search(text) and PLACE_NUMBER_PATTERN.search(text))
    if not (parsed.followup or parsed.reference or expand or near_numbered):
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
    return FollowupPlan(requests=requests, same_query=same_query, expand=expand)
