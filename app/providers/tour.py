from app.providers.base import PlaceProvider
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class TourProvider(PlaceProvider):
    name = "tour"

    async def search(
        self,
        request: PlaceRequest,
        location_name: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> list[Place]:
        # TODO: Connect the external API.
        return []
