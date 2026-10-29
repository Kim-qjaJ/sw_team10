"""'부산대학교', '이재모피자 본점'처럼 특정 장소를 찾는 검색에서 결과를 고른다.

pipeline.py의 exact_place 검색에서 아래 순서로 호출합니다.
1) institution_locations → 2) match → 3) representative('본점'이면 채택) → 4) related_branches
"""

from app.schemas.place import Place

_CAMPUS_WORDS = ("캠퍼스", "본교", "대학교", "대학")


def _norm(text: str) -> str:
    return "".join(text.split()).casefold()


class ExactPlaceMatcher:
    def institution_locations(self, query: str, places: list[Place]) -> list[Place]:
        """상위 기관명(예: 동아대학교)이면 대표 캠퍼스를 첫 번째로 둔 캠퍼스 목록. 아니면 빈 목록."""
        key = _norm(query)
        if not key or not key.endswith(("대학교", "대학")):
            return []
        # 이름이 정확히 같은 장소(예: '동아대학교')가 있으면 단일 장소 검색으로 처리한다.
        if any(_norm(p.name) == key for p in places):
            return []
        campuses = [p for p in places if key in _norm(p.name) and any(w in p.name for w in _CAMPUS_WORDS)]
        if len(campuses) < 2:
            return []
        # '본교'·'본관'·'캠퍼스' 없는 이름을 대표로 우선하고 나머지는 입력 순서를 유지한다.
        campuses.sort(key=lambda p: 0 if "본" in p.name else 1)
        return campuses

    def match(self, query: str, places: list[Place]) -> Place | None:
        """이름이 정확히 일치하는 장소 하나. 없으면 None."""
        key = _norm(query)
        return next((p for p in places if _norm(p.name) == key), None)

    def representative(self, query: str, places: list[Place]) -> Place | None:
        """정확히 일치하지 않을 때 대표 장소(예: 본점). 없으면 None."""
        key = _norm(query)
        if not key:
            return None
        return next((p for p in places if key in _norm(p.name) and "본점" in p.name), None)

    def related_branches(self, query: str, places: list[Place]) -> list[Place]:
        """같은 이름의 지점들. 이름에 query가 포함된 장소만 남긴다."""
        key = _norm(query)
        return [p for p in places if key and key in _norm(p.name)]
