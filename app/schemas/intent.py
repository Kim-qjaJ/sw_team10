from typing import Literal

from pydantic import BaseModel, Field


class PlaceRequest(BaseModel):
    """One place/data request extracted from the user's natural language."""

    query: str = Field(min_length=1, max_length=200)
    category: Literal[
        "restaurant",
        "cafe",
        "tourism",
        "culture",
        "activity",
        "public_facility",
        "education",
        "other",
    ] | None = None
    subcategory: str | None = None
    indoor: bool | None = None


class UserIntent(BaseModel):
    intent: Literal["recommend_place", "search_place", "get_event", "get_route"]
    location: str | None = None
    requests: list[PlaceRequest] = Field(default_factory=list, min_length=1)
    companion: Literal["alone", "friend", "family"] | None = None
