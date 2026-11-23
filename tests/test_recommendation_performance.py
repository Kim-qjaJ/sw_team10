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


def test_slow_provider_and_slow_tmap_still_answer_within_6_seconds(monkeypatch):
    """NFR-001·AC-014 (TMAP 포함): 장소 Provider 하나가 응답하지 않고, 날씨·TMAP 경로도 느릴 때
    실제 설정값(Provider 대기 5.5초, TMAP 4초, 응답 목표 5.8초) 그대로 6초 안에 받은 결과로 응답한다."""
    import asyncio
    import time

    from app.config import settings
    from app.providers.tmap import RouteDistance
    from app.schemas.intent import PlaceRequest, UserIntent
    from app.schemas.place import Place
    from app.services.pipeline import RecommendationPipeline

    origin = (35.1578, 129.0597)  # 서면 부근

    class Fast:
        name = "kakao"

        async def search(self, request, location, anchor=None):
            return [Place(name=f"카페 {i}", category="cafe", latitude=origin[0] + i * 0.001, longitude=origin[1],
                          address="부산 부산진구 부전동", sources=["kakao"], source_ids={"kakao": str(i)},
                          source_categories={"kakao": "음식점 > 카페"}) for i in range(1, 9)]

    class Hanging:
        name = "naver"

        async def search(self, request, location, anchor=None):
            await asyncio.sleep(30)  # 응답 없음
            return []

    async def slow_weather(point):
        await asyncio.sleep(3)
        return {"source": "kma", "is_raining": False, "temperature": 5}

    async def slow_stations(point):
        await asyncio.sleep(3)
        return []

    async def slow_route(client, a, b, mode):
        await asyncio.sleep(3)
        return RouteDistance(500, 400)

    monkeypatch.setattr(settings, "use_mock_places", False)
    monkeypatch.setattr(settings, "tmap_enabled", True)
    monkeypatch.setattr(settings, "tmap_api_key", "test-key")
    pipeline = RecommendationPipeline()
    pipeline.api_router.providers = [Fast(), Hanging()]
    monkeypatch.setattr(pipeline.context, "weather", slow_weather)
    monkeypatch.setattr(pipeline.context, "stations", slow_stations)
    monkeypatch.setattr(pipeline.route_distance.provider, "route", slow_route)

    intent = UserIntent(intent="recommend_place", location="부산 서면",
                        requests=[PlaceRequest(query="카페", category="cafe")])
    started = time.perf_counter()
    response = asyncio.run(pipeline.run(intent, 5, message="서면 카페 추천해줘"))
    elapsed = time.perf_counter() - started
    print(f"\n지연 주입 응답 시간: {elapsed:.2f}초, timing_ms={response.timing_ms}")
    assert elapsed < 6.0, elapsed
    assert response.results[0].places, "빠른 Provider 결과로 응답해야 한다"
    assert response.results[0].providers["naver"].status == "timeout"
