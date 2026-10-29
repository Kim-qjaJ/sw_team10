from abc import ABC, abstractmethod

from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class ProviderNotConfigured(Exception):
    """API 키 등 필수 설정이 없어 Provider를 호출하지 않은 경우."""


class PlaceProvider(ABC):
    name: str
    # 좌표 + 반경 검색을 지원하는 Provider만 '주변 시설 찾기'에 사용한다.
    # 지원 Provider는 search()에 radius_m 키워드 인자를 추가로 받는다.
    supports_radius: bool = False

    @abstractmethod
    async def search(self, request: PlaceRequest, location: str | None = None, anchor: tuple[float, float] | None = None) -> list[Place]:
        """Provider를 호출하고 공통 Place 목록으로 정규화해 반환한다.

        설정이 없으면 빈 목록 대신 ProviderNotConfigured를 발생시켜
        '결과 0건'과 '호출하지 않음'을 구분한다.
        """
        raise NotImplementedError
