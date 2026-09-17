from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class APIRouterService:
    """Select external data providers for a normalized query.

    Provider implementations are intentionally left empty at this stage.
    """

    async def search(
        self,
        request: PlaceRequest,
        location_name: str | None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> list[Place]:
        # TODO: Route queries to Kakao/Naver/Busan/Tour providers.
        return []
