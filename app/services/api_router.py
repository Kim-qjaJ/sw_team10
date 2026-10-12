"""요청 종류에 맞는 Provider를 골라 병렬로 검색한다.

[API 담당] 인터페이스만 정의된 뼈대입니다. pipeline.py가 아래 형식으로 호출합니다.
현재는 USE_MOCK_PLACES=true일 때 샘플 데이터(MockProvider)만 돌려줘 GUI·추천 흐름을 확인할 수 있습니다.
"""

from app.config import settings
from app.providers.mock import MockProvider
from app.schemas.api import ProviderStatus
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class APIRouterService:
    async def search(
        self,
        request: PlaceRequest,
        location: str | None,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
    ) -> tuple[list[Place], dict[str, ProviderStatus]]:
        """(장소 목록, Provider별 상태)를 반환한다.

        - 상태 status: ok / not_configured / timeout / rate_limited / error (schemas/api.py ProviderState)
        - radius_m가 있으면(주변 시설 찾기) 좌표 반경을 지원하는 Provider만 사용한다.
        TODO: kakao/naver/busan/tour Provider를 요청 종류에 맞게 골라 asyncio.gather로 호출하고,
              한 Provider 오류가 전체를 막지 않도록 Provider별 상태를 기록한다.
        """
        if settings.use_mock_places:
            places = await MockProvider().search(request=request, location=location, anchor=anchor, radius_m=radius_m)
            return places, {"mock": ProviderStatus(status="ok", count=len(places))}
        return [], {"kakao": ProviderStatus(status="not_configured", detail="Provider 미구현")}
