"""명시적 조건을 어긴 후보를 제거한다. 정보가 없는(None) 항목은 제거하지 않는다.

종류 조건은 Provider 원본 업종(source_categories)으로 판단한다. Provider가 Place.category에 요청 종류를
그대로 넣는 경우가 있어서, 원본 업종이 없을 때만 Place.category를 비교한다.
- 원본 업종으로 종류를 알 수 없으면(키워드 표에 없는 업종) 제거하지 않는다.
- 관광·문화·체험은 서로 '비슷한 종류'라 제거하지 않는다 (scoring.RELATED_CATEGORIES).
- 주차장·정문·ATM 같은 부대시설은 공공시설 요청이 아니면 제거한다 (scoring.AMENITY_WORDS).

실내/야외 조건은 사용자가 직접 말했을 때(request.indoor가 None이 아닐 때)만 적용한다.
Place.indoor가 없으면 업종 경로·장소 이름으로 추정하고(scoring.infer_indoor_with_name), 추정이 애매하면(None) 제거하지 않는다.

전시 요청은 진행 중인 전시(Place.events)가 있는 장소만 남긴다. 그런 장소가 하나도 없으면 거르지 않는다.
'해수욕장'·'박물관'처럼 장소 종류가 분명한 검색어(PLACE_KIND_QUERIES)면, 업종이나 이름에 그 말이 든 장소만 남긴다.
맞는 곳이 2곳 미만이면 거르지 않는다 (비슷한 관광지로 채우던 '기장 해수욕장' → 오랑대·루지, 2026-11).
축제·행사 요청은 오늘 열리고 있다고 기간이 확인된 곳(Place.events)만 남긴다. 하나도 없으면 결과 없음으로 둔다
(카카오·네이버의 '○○축제' 장소는 기간이 없어 이미 끝난 축제일 수 있다).
"""

from app.providers.busan import wants_exhibit
from app.providers.tour import wants_festival
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.scoring import (
    AMENITY_WORDS, category_path, infer_categories, infer_indoor_with_name, is_amenity, related_categories,
)

# 장소 종류가 분명한 검색어 → 업종·이름에서 찾을 말. 이 목록에 있는 검색어에만 '맞는 장소만 남기기'를 적용한다.
# ('서핑'은 카카오 업종이 '수상스포츠'뿐이고 '전시'는 미술관에서도 열려서, 목록 밖 검색어는 거르지 않는다.)
PLACE_KIND_QUERIES = {
    "해수욕장": ("해수욕장", "해변"), "해변": ("해수욕장", "해변"),
    "박물관": ("박물관",), "미술관": ("미술관", "갤러리"), "과학관": ("과학관",), "도서관": ("도서관",),
    "공원": ("공원",), "수목원": ("수목원",), "전망대": ("전망대",), "등대": ("등대",), "시장": ("시장",),
    "사찰": ("사찰",), "절": ("사찰",), "아쿠아리움": ("아쿠아리움",), "테마파크": ("테마파크", "놀이공원"),
    "방탈출": ("방탈출",), "볼링장": ("볼링",), "찜질방": ("찜질방", "사우나"),
}
# 맞는 장소가 이보다 적으면 거르지 않는다 (한 곳만 보여주기보다 비슷한 곳도 함께 보여준다)
MIN_KIND_MATCHES = 2


class HardFilter:
    def apply(self, request: PlaceRequest, places: list[Place]) -> list[Place]:
        kept: list[Place] = []
        # '편의점'·'주차장'처럼 부대시설 자체를 찾는 요청이면 부대시설을 빼지 않는다.
        # 예전에는 '해운대해수욕장 근처 편의점'에서 편의점이 모두 빠져 결과가 0건이었다 (2026-11-23).
        wanted = [word for word in AMENITY_WORDS if word in request.query]
        for place in places:
            path = " ".join(place.source_categories.values())
            wants_amenity = any(word in path for word in wanted)
            if place.is_available is False:
                continue
            if request.indoor is not None and self._other_indoor(request.indoor, place):
                continue
            # other(기타)는 종류를 정하지 못했다는 뜻이라 종류로 거르지 않고, 주차장·숙박 같은 부대시설만 뺀다.
            if request.category == "other":
                if is_amenity(place.source_categories) and not wants_amenity:
                    continue
            elif request.category and not wants_amenity and self._other_category(request.category, place):
                continue
            kept.append(place)
        # 전시 자체를 찾는 요청이면, 오늘 볼 수 있는 전시가 붙은 장소만 남긴다 (FR-042).
        # 진행 중인 전시를 하나도 못 찾았으면 장소 결과라도 보여주도록 그대로 둔다.
        if wants_exhibit(request) and any(place.events for place in kept):
            kept = [place for place in kept if place.events]
        # 전시를 보러 가는 요청이면 오늘 정기 휴관인 전시장은 뺀다 (모르는 곳은 남기고 화면에서 안내).
        if wants_exhibit(request):
            kept = [place for place in kept if place.closed_today is not True]
        if wants_festival(request):
            kept = [place for place in kept if any(e.get("kind") == "festival" for e in place.events)]
        return self._keep_requested_kind(request, kept)

    @staticmethod
    def _keep_requested_kind(request: PlaceRequest, places: list[Place]) -> list[Place]:
        """'해수욕장'처럼 장소 종류가 분명한 검색어면 그 말이 업종·이름에 든 곳만 남긴다.

        메뉴 이름('텐동')이나 활동('서핑')은 업종에 그 말이 없는 맞는 장소가 많아 목록에 넣지 않는다.
        """
        words = PLACE_KIND_QUERIES.get(request.query.strip())
        if request.search_mode != "category" or not words:
            return places
        matched = [p for p in places
                   if any(w in f"{p.name} {category_path(p.source_categories, p.subcategory)}" for w in words)]
        return matched if len(matched) >= MIN_KIND_MATCHES else places

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
