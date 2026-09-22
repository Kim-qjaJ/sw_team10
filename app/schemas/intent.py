from typing import Literal

from pydantic import BaseModel, Field, model_validator

IntentType = Literal[
    "recommend_place",
    "search_place",
    "get_event",
    "get_route",
    "unsupported",
]

PlaceCategory = Literal[
    "restaurant",
    "cafe",
    "tourism",
    "culture",
    "activity",
    "public_facility",
    "education",
    "other",
]

TransportMode = Literal["walk", "transit", "car"]


class PlaceRequest(BaseModel):
    """One place/data request extracted from the user's natural language."""

    query: str = Field(min_length=1, max_length=200)
    category: PlaceCategory | None = None
    subcategory: str | None = None
    indoor: bool | None = None


class UserIntent(BaseModel):
    intent: IntentType
    location: str | None = None
    requests: list[PlaceRequest] = Field(default_factory=list)
    companion: Literal["alone", "friend", "family"] | None = None
    transport_mode: TransportMode | None = None

    @model_validator(mode="after")
    def check_requests(self) -> "UserIntent":
        if self.intent != "unsupported" and not self.requests:
            raise ValueError("requests must contain at least one item")
        return self
