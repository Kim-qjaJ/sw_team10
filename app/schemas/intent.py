from typing import Literal

from pydantic import BaseModel


class UserIntent(BaseModel):
    intent: Literal["recommend_place", "search_place", "get_event", "get_route"]
    location: str | None = None
    category: Literal["restaurant", "cafe", "tourism", "culture", "activity"] | None = None
    cost: Literal["low", "medium", "high", "any"] | None = None
    indoor: bool | None = None
    companion: Literal["alone", "friend", "family"] | None = None
