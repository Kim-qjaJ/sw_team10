"""후속 질문 처리: '다른 곳 더', '여기말고 다른 영화관', '이 근처 카페', '범위 넓혀줘'.

직전 검색의 조건(검색어, 기준 위치, 반경)을 세션에 저장해 두고, 후속 질문은 그 조건에서
바뀐 부분만 반영해 다시 검색한다. 사전에 없는 새 조건('조용한 곳')이 섞이면 기존 흐름(LLM)에 맡긴다.
"""

import re
from dataclasses import dataclass, field

from app.schemas.intent import UserIntent
from app.services.local_router import COMPARE_PATTERN, build_category_requests, parse_message, requested_route

EXPAND_PATTERN = re.compile(r"넓|멀리|반경|범위")
# 반경을 넓힐 때 쓰는 단계(m). 현재 반경보다 큰 첫 값으로 넓힌다.
EXPAND_STEPS_M = (1000, 1500, 3000, 5000, 10000)
MAX_EXPAND_M = 10000


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
    current = current or 1500
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
    if not (parsed.followup or parsed.reference or expand):
        return None
    # 새 위치를 직접 말했거나 사전에 없는 조건이 섞이면 일반 흐름에서 다시 해석한다.
    if [loc for loc in parsed.locations if loc != "부산"] or parsed.non_busan or parsed.unknown:
        return None

    requests = build_category_requests(parsed)
    if not requests:
        requests = [request.model_dump() for request in last.intent.requests]
    same_query = {r["query"] for r in requests} == {r.query for r in last.intent.requests}
    return FollowupPlan(requests=requests, same_query=same_query, expand=expand)
