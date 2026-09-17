from app.schemas.location import LocationReference


class LocationResolver:
    """Resolve a user's location expression into one reference coordinate.

    Resolution policy:
    1. Explicit browser GPS -> current_location
    2. Concrete place name -> specific_place
    3. Broad Busan area name -> area_anchor (station/landmark)
    4. Otherwise -> unresolved

    External map/place APIs are intentionally not implemented here yet.
    """

    def from_current_location(
        self,
        latitude: float,
        longitude: float,
    ) -> LocationReference:
        return LocationReference(
            mode="current_location",
            resolved_name="현재 위치",
            latitude=latitude,
            longitude=longitude,
            source="browser_geolocation",
        )

    async def resolve_text(self, location: str | None) -> LocationReference:
        if not location:
            return LocationReference(mode="unresolved")

        # TODO:
        # - First try an exact/specific POI lookup without shortening the user's text.
        # - If it is not a concrete POI, resolve Busan area aliases to a representative
        #   station or landmark such as 덕천 -> 덕천역.
        # - If multiple candidates remain ambiguous, ask the user instead of guessing.
        return LocationReference(
            mode="unresolved",
            original_text=location,
        )
