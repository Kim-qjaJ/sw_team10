"""네이버 지역 검색 Provider.

[API 담당] 인터페이스만 정의된 뼈대입니다. 구현 전에는 ProviderNotConfigured를 발생시켜
APIRouterService가 '설정 안 됨'으로 표시합니다. 형식은 app/providers/base.py의 PlaceProvider를 따릅니다.
"""

from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class NaverProvider(PlaceProvider):
    name = "naver"
    supports_radius = False

    async def search(self, request: PlaceRequest, location: str | None = None, anchor: tuple[float, float] | None = None) -> list[Place]:
        # TODO: API를 호출하고 결과를 Place 목록으로 정규화한다.
        #  - location: '부산 해운대구'처럼 텍스트 지역 (anchor가 없을 때 검색어에 붙인다)
        #  - anchor: (위도, 경도) 기준점. 있으면 좌표 반경 검색을 우선한다
        #  - Place.source_categories / source_category_codes에 원본 업종을 보존한다 (종류 점수에 사용)
        raise ProviderNotConfigured("NaverProvider는 아직 구현되지 않았습니다.")
