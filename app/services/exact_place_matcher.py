"""'부산대학교', '이재모피자 본점'처럼 특정 장소를 찾는 검색에서 결과를 고른다.

[추천 알고리즘 담당] 인터페이스만 정의된 뼈대입니다. pipeline.py의 exact_place 검색에서 아래 순서로 호출합니다.
1) institution_locations → 2) match → 3) representative('본점'이면 채택) → 4) related_branches
"""

from app.schemas.place import Place


class ExactPlaceMatcher:
    def institution_locations(self, query: str, places: list[Place]) -> list[Place]:
        """상위 기관명(예: 동아대학교)이면 대표 캠퍼스를 첫 번째로 둔 캠퍼스 목록. 아니면 빈 목록."""
        return []

    def match(self, query: str, places: list[Place]) -> Place | None:
        """이름이 정확히 일치하는 장소 하나. 없으면 None."""
        return None

    def representative(self, query: str, places: list[Place]) -> Place | None:
        """정확히 일치하지 않을 때 대표 장소(예: 본점). 없으면 None."""
        return None

    def related_branches(self, query: str, places: list[Place]) -> list[Place]:
        """같은 이름의 지점들. TODO: 이름에 query가 포함된 장소만 남긴다."""
        return list(places)
