from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class HardFilter:
    """Remove candidates that violate explicit user/business constraints."""

    def apply(self, request: PlaceRequest, places: list[Place]) -> list[Place]:
        filtered: list[Place] = []

        for place in places:
            if place.available is False:
                continue

            if (
                request.category
                and place.category
                and request.category != place.category
            ):
                continue

            if (
                request.indoor is not None
                and place.indoor is not None
                and request.indoor != place.indoor
            ):
                continue

            # TODO: Add Busan service-scope validation after location metadata
            # and actual providers are connected.
            filtered.append(place)

        return filtered
