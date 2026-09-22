from typing import Literal

from pydantic import BaseModel, Field


class Place(BaseModel):
    """Normalized place model shared by providers and recommendation logic."""

    id: str | None = None
    name: str
    category: str | None = None
    subcategory: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_m: float | None = None
    indoor: bool | None = None

    sources: list[str] = Field(default_factory=list)
    source_ids: dict[str, str] = Field(default_factory=dict)

    is_available: bool | None = None
    busan_verified: bool = False
    popularity_signal: float | None = None

    walking_minutes: float | None = None
    transit_minutes: float | None = None
    driving_minutes: float | None = None
    transit_transfers: int | None = None

    score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class RecommendationContext(BaseModel):
    transport_mode: Literal["walk", "transit", "car"] | None = None
    anchor_latitude: float | None = None
    anchor_longitude: float | None = None
    weather: dict | None = None
