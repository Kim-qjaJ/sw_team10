from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place

ProviderState = Literal["ok", "not_configured", "timeout", "error"]


class ChatRequest(BaseModel):
    """GUI에서 보내는 사용자 자연어 문장."""

    message: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


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


class RecommendResponse(BaseModel):
    message: str | None = None
    intent: UserIntent
    results: list[RequestResult]
    notice: str | None = None
    mock_data: bool = False
    timing_ms: dict[str, float]
