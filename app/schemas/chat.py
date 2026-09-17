from pydantic import BaseModel, Field

from app.schemas.intent import UserIntent


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    latitude: float | None = None
    longitude: float | None = None


class ChatResponse(BaseModel):
    message: str
    intent: UserIntent
    places: list[dict] = Field(default_factory=list)
