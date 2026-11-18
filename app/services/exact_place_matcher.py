"""'부산대학교', '이재모피자 본점'처럼 특정 장소를 찾는 검색에서 결과를 고른다.

pipeline.py의 exact_place 검색에서 아래 순서로 호출합니다.
1) institution_locations → 2) match → 3) representative('본점'이면 채택) → 4) related_branches

실제 데이터 기준 규칙:
- 캠퍼스는 이름이 '캠퍼스'로 끝나는 것만 본다 ('부산캠퍼스 정문', '부민캠퍼스 법학전문대학원' 제외).
- 정확히 일치하는 이름이 없으면 '광역시'→'시', '부산'을 뺀 이름으로 한 번 더 비교한다
  ('부산시청' = '부산광역시청', '스타벅스 서면점' = '스타벅스 부산서면점').
- 본점·지점은 이름이 검색어로 시작해야 한다 ('미남스시 부산시청 본점'은 '부산시청'의 본점이 아니다).
- 지점 목록에서 주차장·화장실 같은 부대시설은 뺀다 ('광안리 공영주차장'). 검색어가 부대시설이면 남긴다.
"""

from app.schemas.place import Place
from app.services.scoring import AMENITY_WORDS, is_amenity


def _norm(text: str) -> str:
    return "".join(text.split()).casefold()


def _loose(text: str) -> str:
    """지역 표기 차이를 무시한 이름. '부산광역시청' → '시청', '스타벅스 부산서면점' → '스타벅스서면점'."""
    return _norm(text).replace("광역시", "시").replace("부산", "")


class ExactPlaceMatcher:
    def institution_locations(self, query: str, places: list[Place]) -> list[Place]:
        """상위 기관명(예: 동아대학교)이면 대표 캠퍼스를 첫 번째로 둔 캠퍼스 목록. 아니면 빈 목록."""
        key = _norm(query)
        if not key or not key.endswith(("대학교", "대학")):
            return []
        # 이름이 정확히 같은 장소(예: '동아대학교')가 있으면 단일 장소 검색으로 처리한다.
        if any(_norm(p.name) == key for p in places):
            return []
        campuses = [p for p in places if _norm(p.name).startswith(key) and _norm(p.name).endswith("캠퍼스")]
        # '본교'·'본캠퍼스'를 대표로 우선하고, 나머지는 입력(Provider 정확도) 순서를 유지한다.
        campuses.sort(key=lambda p: 0 if "본" in p.name else 1)
        return campuses

    def match(self, query: str, places: list[Place]) -> Place | None:
        """이름이 정확히 일치하는 장소 하나. 없으면 지역 표기 차이를 무시하고 비교한다. 없으면 None."""
        key = _norm(query)
        exact = next((p for p in places if _norm(p.name) == key), None)
        if exact is not None:
            return exact
        loose = _loose(query)
        if len(loose) < 2:
            return None
        return next((p for p in places if _loose(p.name) == loose), None)

    def representative(self, query: str, places: list[Place]) -> Place | None:
        """정확히 일치하지 않을 때 대표 장소(예: 본점). 이름이 검색어로 시작해야 한다. 없으면 None."""
        key = _norm(query)
        if not key:
            return None
        return next((p for p in places if _norm(p.name).startswith(key) and "본점" in p.name), None)

    def related_branches(self, query: str, places: list[Place]) -> list[Place]:
        """같은 이름의 지점들. 이름이 검색어로 시작하는 장소만 남기고, 부대시설은 뺀다."""
        key = _norm(query)
        keep_amenity = any(w in query for w in AMENITY_WORDS)
        return [p for p in places if key and _norm(p.name).startswith(key)
                and (keep_amenity or not is_amenity(p.source_categories))]
