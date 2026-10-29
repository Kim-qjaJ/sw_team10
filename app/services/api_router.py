import asyncio

import httpx
from app.config import settings
from app.providers.base import ProviderNotConfigured
from app.providers.busan import BusanProvider
from app.providers.kakao import KakaoProvider
from app.providers.mock import MockProvider
from app.providers.naver import NaverProvider
from app.providers.tour import TourProvider
from app.schemas.api import ProviderStatus
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place

class APIRouterService:
    def __init__(self):
        self.providers = [
            KakaoProvider(),
            NaverProvider(),
            BusanProvider(),
            TourProvider(),
        ]

    async def search(
        self,
        request: PlaceRequest,
        location: str | None = None,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
    ) -> tuple[list[Place], dict[str, ProviderStatus]]:
        # USE_MOCK_PLACES 설정이 True인 경우 가짜(Mock) 데이터 반환
        if settings.use_mock_places:
            mock_provider = MockProvider()
            places = await mock_provider.search(
                request=request, location=location, anchor=anchor, radius_m=radius_m
            )
            statuses = {
                "mock": ProviderStatus(status="ok", count=len(places))
            }
            return places, statuses

        # 반경 검색(radius_m) 요청인 경우, 좌표 반경 검색을 지원하는 Provider만 필터링
        active_providers = self.providers
        if radius_m is not None:
            active_providers = [p for p in self.providers if getattr(p, "supports_radius", False)]

        all_places: list[Place] = []
        statuses: dict[str, ProviderStatus] = {}

        # 각 Provider 개별 비동기 실행 함수
        async def fetch_provider(provider):
            try:
                # supports_radius 지원 여부에 따라 인자 전달
                if getattr(provider, "supports_radius", False):
                    res = await provider.search(
                        request=request, location=location, anchor=anchor, radius_m=radius_m
                    )
                else:
                    res = await provider.search(
                        request=request, location=location, anchor=anchor
                    )
                return provider.name, res, None
            except ProviderNotConfigured as e:
                return provider.name, [], ProviderStatus(status="not_configured", detail=str(e))
            except httpx.TimeoutException:
                return provider.name, [], ProviderStatus(status="timeout", detail="응답 시간 초과")
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                state = "rate_limited" if code == 429 else "error"
                return provider.name, [], ProviderStatus(status=state, detail=f"HTTP {code}")
            except Exception as e:
                return provider.name, [], ProviderStatus(status="error", detail=f"{type(e).__name__}: {e}")

        # 모든 active_providers를 동시에 비동기(asyncio.gather)로 호출
        results = await asyncio.gather(
            *[fetch_provider(p) for p in active_providers],
            return_exceptions=True
        )

        for result in results:
            if isinstance(result, Exception):
                continue
            p_name, places, status = result
            if status is None:
                # 정상 호출 성공 시
                statuses[p_name] = ProviderStatus(status="ok", count=len(places))
                all_places.extend(places)
            else:
                # 설정 미비(not_configured) 또는 오류(error) 발생 시
                statuses[p_name] = status

        return all_places, statuses