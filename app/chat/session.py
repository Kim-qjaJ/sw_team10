"""대화 세션 상태 (RAM 저장).

나중에 DB/Redis로 옮길 때는 SessionStore만 바꾸면 되도록 세션 읽기·쓰기를 이 파일에 모은다.
"""

import time
from dataclasses import dataclass

from app.schemas.api import RecommendResponse
from app.schemas.intent import UserIntent
from app.services.followup import LastSearch

SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 1000
# 대화성 Intent는 직전 검색 조건을 덮어쓰지 않는다.
NON_SEARCH_INTENTS = {"smalltalk", "place_detail", "compare_results", "clarify", "unsupported"}


@dataclass
class SessionState:
    intent: UserIntent | None
    updated_at: float
    last_places: list[dict] | None = None
    # 재질문(clarify) 후 사용자의 답을 기다리는 Intent. 위치만 채우면 바로 검색한다.
    pending: UserIntent | None = None
    # 후속 질문('다른 곳 더', '범위 넓혀줘')에 쓰는 직전 검색 조건
    last_search: LastSearch | None = None
    # 주변 시설 찾기에서 받은 사용자 현재 위치 (길찾기 출발지)
    origin: tuple[float, float] | None = None


def has_places(response: RecommendResponse) -> bool:
    return any(result.places for result in response.results)


def response_anchor(response: RecommendResponse) -> tuple[float, float] | None:
    anchor = (response.context or {}).get("anchor")
    return (anchor[0], anchor[1]) if anchor else None


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    def cleanup(self) -> None:
        """1시간 이상 사용하지 않은 세션을 제거하고 최대 개수를 제한한다."""
        now = time.monotonic()
        for session_id in [sid for sid, state in self._sessions.items() if now - state.updated_at > SESSION_TTL_SECONDS]:
            self._sessions.pop(session_id, None)
        if len(self._sessions) > MAX_SESSIONS:
            oldest = sorted(self._sessions.items(), key=lambda item: item[1].updated_at)
            for session_id, _ in oldest[: len(self._sessions) - MAX_SESSIONS]:
                self._sessions.pop(session_id, None)

    def get(self, session_id: str | None) -> SessionState | None:
        self.cleanup()
        return self._sessions.get(session_id) if session_id else None

    def ensure(self, session_id: str | None) -> SessionState | None:
        if not session_id:
            return None
        state = self._sessions.get(session_id)
        if state is None:
            state = SessionState(intent=None, updated_at=time.monotonic())
            self._sessions[session_id] = state
        return state

    def save_search_intent(self, session_id: str | None, intent: UserIntent) -> None:
        """새 검색 조건을 저장한다. 대화/상세질문은 직전 검색 상태를 덮어쓰지 않는다."""
        if not session_id:
            return
        state = self._sessions.get(session_id)
        if intent.intent in NON_SEARCH_INTENTS:
            if state:
                state.updated_at = time.monotonic()
            return
        state = self.ensure(session_id)
        # 재질문 대기·후속 질문 조건·현재 위치는 유지하고 검색 조건만 바꾼다.
        state.intent = intent
        state.updated_at = time.monotonic()

    def save_pending(self, session_id: str | None, pending: UserIntent | None) -> None:
        state = self.ensure(session_id)
        if state is None:
            return
        state.pending = pending
        state.updated_at = time.monotonic()

    def save_last_places(self, session_id: str | None, response: RecommendResponse) -> None:
        state = self._sessions.get(session_id) if session_id else None
        if not state or not response.results:
            return
        places = [
            {
                "rank": rank,
                "name": place.name,
                "category": place.category,
                "address": place.address,
                "phone": place.phone,
                "url": place.url,
                "distance_m": place.distance_m,
                "latitude": place.latitude,
                "longitude": place.longitude,
                "indoor": place.indoor,
                "sources": place.sources,
                "score": place.score,
            }
            for result in response.results
            for rank, place in enumerate(result.places, start=1)
        ]
        if places:
            state.last_places = places
            state.updated_at = time.monotonic()

    def remember_search(
        self,
        session_id: str | None,
        intent: UserIntent,
        response: RecommendResponse,
        mode: str | None,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
        from_user_location: bool = False,
        keep_shown: bool = False,
    ) -> None:
        """후속 질문에 쓰도록 직전 검색 조건과 보여준 장소를 저장한다. 결과가 없으면 이전 조건을 유지한다."""
        state = self.ensure(session_id)
        if state is None or not has_places(response):
            return
        names = [place.name for result in response.results for place in result.places]
        shown = (state.last_search.shown if keep_shown and state.last_search else []) + names
        state.last_search = LastSearch(
            intent=intent,
            mode=mode,
            anchor=anchor or response_anchor(response),
            radius_m=radius_m,
            shown=list(dict.fromkeys(shown)),
            from_user_location=from_user_location,
        )
