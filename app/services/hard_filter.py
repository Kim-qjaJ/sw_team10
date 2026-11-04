"""명시적 조건을 어긴 후보를 제거한다.

[추천 알고리즘 담당] 인터페이스만 정의된 뼈대입니다. 지금은 입력을 그대로 돌려줍니다.
"""

from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class HardFilter:
    def apply(self, request: PlaceRequest, places: list[Place]) -> list[Place]:
        """TODO: 영업 불가(is_available=False), 실내/야외 조건(request.indoor),
        요청 종류와 다른 업종(request.category vs place.category)을 제거한다.
        정보가 없는(None) 항목은 제거하지 않는다.
        """
        return list(places)
