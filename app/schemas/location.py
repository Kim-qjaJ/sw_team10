from typing import Literal

from pydantic import BaseModel

LocationMode = Literal[
    "current_location",
    "specific_place",
    "area_anchor",
    "unresolved",
]


class LocationReference(BaseModel):
    """Resolved reference point used for nearby search and accessibility scoring."""

    mode: LocationMode
    original_text: str | None = None
    resolved_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source: str | None = None
