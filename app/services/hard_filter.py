"""명시적 조건을 어긴 후보를 제거한다. 정보가 없는(None) 항목은 제거하지 않는다.

종류 조건은 Provider 원본 업종(source_categories)으로 판단한다. Provider가 Place.category에 요청 종류를
그대로 넣는 경우가 있어서, 원본 업종이 없을 때만 Place.category를 비교한다.
- 원본 업종으로 종류를 알 수 없으면(키워드 표에 없는 업종) 제거하지 않는다.
- 관광·문화·체험은 서로 '비슷한 종류'라 제거하지 않는다 (scoring.RELATED_CATEGORIES).
- 주차장·정문·ATM 같은 부대시설은 공공시설 요청이 아니면 제거한다 (scoring.AMENITY_WORDS).

실내/야외 조건은 사용자가 직접 말했을 때(request.indoor가 None이 아닐 때)만 적용한다.
Place.indoor가 없으면 업종 경로·장소 이름으로 추정하고(scoring.infer_indoor_with_name), 추정이 애매하면(None) 제거하지 않는다.

전시 요청은 진행 중인 전시(Place.events)가 있는 장소만 남긴다. 그런 장소가 하나도 없으면 거르지 않는다.
"""

from app.providers.busan import wants_exhibit
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.scoring import (
    category_path, infer_categories, infer_indoor_with_name, is_amenity, related_categories,
)


class HardFilter:
    def apply(self, request: PlaceRequest, places: list[Place]) -> list[Place]:
        kept: list[Place] = []
        for place in places:
            if place.is_available is False:
                continue
            if request.indoor is not None and self._other_indoor(request.indoor, place):
                continue
            # other(기타)는 종류를 정하지 못했다는 뜻이라 종류로 거르지 않고, 주차장·숙박 같은 부대시설만 뺀다.
            if request.category == "other":
                if is_amenity(place.source_categories):
                    continue
            elif request.category and self._other_category(request.category, place):
                continue
            kept.append(place)
        # 전시 자체를 찾는 요청이면, 오늘 볼 수 있는 전시가 붙은 장소만 남긴다 (FR-042).
        # 진행 중인 전시를 하나도 못 찾았으면 장소 결과라도 보여주도록 그대로 둔다.
        if wants_exhibit(request) and any(place.events for place in kept):
            kept = [place for place in kept if place.events]
        return kept

    @staticmethod
    def _other_indoor(wanted: bool, place: Place) -> bool:
        indoor = place.indoor
        if indoor is None:
            indoor = infer_indoor_with_name(category_path(place.source_categories, place.subcategory), place.name)
        return indoor is not None and indoor != wanted

    @staticmethod
    def _other_category(category: str, place: Place) -> bool:
        if category != "public_facility" and is_amenity(place.source_categories):
            return True
        actual = infer_categories(place.source_categories)
        if actual:
            return category not in actual and not actual & related_categories(category)
        if place.source_categories:
            return False
        return place.category is not None and place.category != category
