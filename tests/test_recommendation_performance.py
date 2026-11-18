"""NFR-006: 후보 50개 순위 계산 100ms 이내 (p95 기준)."""

from scripts.benchmark_recommendation import LIMIT_MS, run


def test_ranking_50_candidates_within_100ms():
    result = run(repeat=30)
    assert result["p95_ms"] <= LIMIT_MS, result


def test_slow_provider_does_not_hold_the_response(monkeypatch):
    """NFR-001: 한 Provider가 늦어도 대기 상한 안에 나머지 결과로 응답한다."""
    import asyncio
    import time

    from app.config import settings
    from app.schemas.intent import PlaceRequest
    from app.schemas.place import Place
    from app.services.api_router import APIRouterService

    class Fast:
        name = "fast"

        async def search(self, request, location, anchor=None):
            return [Place(name="빠른 곳")]

    class Slow:
        name = "slow"

        async def search(self, request, location, anchor=None):
            await asyncio.sleep(2)
            return [Place(name="느린 곳")]

    monkeypatch.setattr(settings, "use_mock_places", False)
    monkeypatch.setattr(settings, "provider_wait_seconds", 0.2)
    router = APIRouterService.__new__(APIRouterService)
    router.providers = [Fast(), Slow()]
    started = time.perf_counter()
    places, statuses = asyncio.run(router.search(request=PlaceRequest(query="카페", category="cafe"), location="부산 서면"))
    assert time.perf_counter() - started < 1.0
    assert [p.name for p in places] == ["빠른 곳"]
    assert statuses["slow"].status == "timeout"
