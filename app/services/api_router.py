import asyncio
import re

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
from app.services.safe_error import redact

# 대기 시간을 넘긴 뒤에도 뒤에서 마저 실행 중인 Provider 호출 (작업이 중간에 사라지지 않게 참조를 둔다)
_BACKGROUND: set[asyncio.Future] = set()


def error_message(body: str | None) -> str:
    """오류 응답 본문에서 원인 메시지. 공공데이터포털은 errMsg·returnAuthMsg에 원인을 담는다."""
    text = body or ""
    found = [m.group(2) for m in re.finditer(r'"?(errMsg|returnAuthMsg)"?\s*[:>]\s*"?([^"<,}]+)', text)]
    if found:
        return " / ".join(dict.fromkeys(f.strip() for f in found if f.strip()))[:160]
    return " ".join(text.split())[:120]


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
                # 공공데이터포털은 활용 신청 전·키 오류를 본문 메시지로 알려준다. 원인 파악용으로 앞부분만 남긴다.
                detail = redact(f"HTTP {code} {error_message(e.response.text)}".strip())
                return provider.name, [], ProviderStatus(status=state, detail=detail)
            except Exception as e:
                # 예외 문장에 요청 주소(serviceKey 포함)가 들어갈 수 있어 키·주소를 지운다 (추천 응답에 그대로 실린다).
                return provider.name, [], ProviderStatus(status="error", detail=redact(f"{type(e).__name__}: {e}"))

        async def fetch_with_cap(provider):
            # 늦는 Provider 하나 때문에 전체 응답이 늦어지지 않게 기다리는 시간을 제한한다 (NFR-001).
            # shield: 제한 시간이 지나도 진행 중인 호출은 끊지 않는다 (부산 목록처럼 받아 두면 캐시로 남는 경우).
            task = asyncio.ensure_future(fetch_provider(provider))
            _BACKGROUND.add(task)
            task.add_done_callback(_BACKGROUND.discard)
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=settings.provider_wait_seconds)
            except asyncio.TimeoutError:
                detail = f"{settings.provider_wait_seconds:g}초 안에 응답이 없어 이 출처 없이 결과를 보여줬어요"
                return provider.name, [], ProviderStatus(status="timeout", detail=detail)

        # 모든 active_providers를 동시에 비동기(asyncio.gather)로 호출
        results = await asyncio.gather(
            *[fetch_with_cap(p) for p in active_providers],
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