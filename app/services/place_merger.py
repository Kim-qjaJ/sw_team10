"""여러 Provider의 같은 장소를 하나로 합친다.

[API 담당] 인터페이스만 정의된 뼈대입니다. 지금은 입력을 그대로 돌려줍니다.
"""

from app.schemas.place import Place


class PlaceMerger:
    def merge(self, places: list[Place]) -> list[Place]:
        """TODO: 이름·주소·좌표(예: 200m 이내)로 같은 장소를 찾아 하나로 합친다.

        합칠 때 sources, source_ids, source_categories, source_category_codes는 모두 보존한다.
        """
        return list(places)
