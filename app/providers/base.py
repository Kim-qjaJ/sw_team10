from abc import ABC, abstractmethod

from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class PlaceProvider(ABC):
    name: str

    @abstractmethod
    async def search(
        self,
        request: PlaceRequest,
        location_name: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> list[Place]:
        raise NotImplementedError
