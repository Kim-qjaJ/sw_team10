"""명시적 조건을 어긴 후보를 제거한다. 정보가 없는(None) 항목은 제거하지 않는다."""

from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class HardFilter:
    def apply(self, request: PlaceRequest, places: list[Place]) -> list[Place]:
        kept: list[Place] = []
        for place in places:
            if place.is_available is False:
                continue
            if request.indoor is not None and place.indoor is not None and place.indoor != request.indoor:
                continue
            if request.category and place.category and place.category != request.category:
                continue
            kept.append(place)
        return kept
