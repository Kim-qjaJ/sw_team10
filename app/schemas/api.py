from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place

ProviderState = Literal["ok", "not_configured", "timeout", "rate_limited", "error"]
ResultType = Literal["exact", "brand", "recommend", "search"]
RouteType = Literal["local", "llm", "llm_fallback", "api"]
# 화면 상단 선택지로 고른 대화 모드. None이면 기존처럼 문장만 보고 판단한다.
#   search    : 특정 장소 검색 → 키워드를 정리해 장소 API로 바로 검색 (결과 0건일 때만 LLM 재해석)
#   recommend : 장소 추천 → Local Router / LLM이 조건을 해석해 추천
#   nearby    : 주변 시설 찾기 → 현재 위치 좌표 기준 반경 검색
ChatMode = Literal["search", "recommend", "nearby"]
# 장소 추천 2단계 선택지
RecommendCategory = Literal["restaurant", "cafe", "culture"]


class ChatRequest(BaseModel):
    """GUI에서 보내는 사용자 자연어 문장."""

    message: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)
    session_id: str | None = Field(default=None, min_length=1, max_length=100)
    mode: ChatMode | None = None
    category: RecommendCategory | None = None
    # 주변 시설 찾기에서 브라우저 위치 API로 얻은 현재 위치
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class IntentRecommendRequest(BaseModel):
    """LLM 없이 추천 로직만 테스트할 때 사용하는 요청."""

    intent: UserIntent
    limit: int = Field(default=5, ge=1, le=20)


class ProviderStatus(BaseModel):
    status: ProviderState
    count: int = 0
    elapsed_ms: float = 0.0
    detail: str | None = None


class RequestResult(BaseModel):
    request: PlaceRequest
    places: list[Place]
    providers: dict[str, ProviderStatus]
    # 특정 장소 검색(exact)과 추천 검색(recommend)은 화면 출력 방식을 다르게 한다.
    result_type: ResultType = "recommend"


class Suggestion(BaseModel):
    """답변 아래에 보여줄 후속 질문 버튼. 누르면 message가 그대로 전송된다."""

    label: str
    # 누르면 그대로 보낼 문장. 비어 있고 mode가 있으면 해당 방식 선택으로 동작한다.
    message: str = ""
    mode: ChatMode | None = None


class Link(BaseModel):
    label: str
    url: str


class RecommendResponse(BaseModel):
    message: str | None = None
    intent: UserIntent
    results: list[RequestResult]
    notice: str | None = None
    mock_data: bool = False
    timing_ms: dict[str, float]
    # local: Local Router가 LLM 없이 처리 / llm: LLM이 Intent 해석 / llm_fallback: LLM 실패 후 코드 해석으로 검색
    # api: 검색·주변 시설 모드에서 키워드를 장소 API로 바로 검색
    route: RouteType | None = None
    mode: ChatMode | None = None
    # 주변 시설 찾기에서 실제로 사용한 검색 반경(m)
    radius_m: int | None = None
    # 추천 점수에 반영한 주변 상황 (예: {"weather": {"is_raining": true, "temperature": 18.2, ...}})
    context: dict | None = None
    suggestions: list[Suggestion] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
