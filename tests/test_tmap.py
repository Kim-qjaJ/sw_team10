"""TMAP HTTP 계약, 이동 거리 순위, 실패 대체 및 요청 전체 호출 예산. 실제 키 불필요."""

import asyncio
import json
import time

import httpx
import pytest

from app.config import settings
from app.providers.tmap import RouteDistance, TmapProvider
from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.pipeline import RecommendationPipeline
from app.services.recommendation import RecommendationService
from app.services.route_distance import RouteBudget, RouteDistanceService

ORIGIN = (35.1578, 129.0597)
REQ = PlaceRequest(query="카페", category="cafe")


@pytest.fixture(autouse=True)
def route_settings(monkeypatch):
    monkeypatch.setattr(settings, "tmap_enabled", True)
    monkeypatch.setattr(settings, "tmap_api_key", "test-road-key")
    monkeypatch.setattr(settings, "tmap_transit_api_key", "test-transit-key")
    monkeypatch.setattr(settings, "tmap_max_routes", 40)
    monkeypatch.setattr(settings, "tmap_concurrency", 2)
    monkeypatch.setattr(settings, "tmap_total_timeout_seconds", 1)
    monkeypatch.setattr(settings, "tmap_cache_ttl_seconds", 300)


def cafe(name, offset):
    return Place(name=name, category="cafe", latitude=ORIGIN[0] + offset, longitude=ORIGIN[1],
                 address="부산 부산진구", source_categories={"kakao": "음식점 > 카페"}, sources=["kakao"])


def summary(distance=1500, seconds=600):
    return {"features": [{"properties": {"totalDistance": distance, "totalTime": seconds}},
                         {"properties": {"distance": 123}}]}


@pytest.mark.parametrize("mode,path", [("walk", "/tmap/routes/pedestrian"), ("car", "/tmap/routes"),
                                       ("transit", "/transit/routes/sub")])
def test_http_contract_and_directional_cache(mode, path):
    seen = []
    destination = (35.16, 129.07)

    def respond(request):
        seen.append(request)
        body = json.loads(request.content)
        assert request.method == "POST" and request.url.path == path
        assert float(body["startX"]) == ORIGIN[1] and float(body["startY"]) == ORIGIN[0]
        assert float(body["endX"]) == destination[1] and float(body["endY"]) == destination[0]
        assert request.headers["appKey"] == ("test-transit-key" if mode == "transit" else "test-road-key")
        assert "appKey" not in str(request.url)
        if mode == "walk":
            assert body["startName"].startswith("%") and body["endName"].startswith("%")
            assert body["searchOption"] == "10"
        if mode == "car":
            assert body["totalValue"] == 2 and body["searchOption"] == 10
        data = {"metaData": {"plan": {"itineraries": [{"totalDistance": 1500, "totalTime": 600}]}}} if mode == "transit" else summary()
        return httpx.Response(200, json=data)

    async def run():
        provider = TmapProvider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await provider.route(client, ORIGIN, destination, mode) == RouteDistance(1500, 600)
            assert await provider.route(client, ORIGIN, destination, mode) == RouteDistance(1500, 600)
        assert provider.cached(destination, ORIGIN, mode) is None
    asyncio.run(run())
    assert len(seen) == 1


@pytest.mark.parametrize("payload", [summary(-1), summary("nan"), summary(True), {"features": []},
                                    {"error": {"message": "no route"}}, summary(None)])
def test_invalid_response_is_not_a_zero_distance_or_cached(payload):
    async def run():
        provider = TmapProvider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
            assert await provider.route(client, ORIGIN, (35.16, 129.07), "walk") is None
        assert provider.status()["cache_entries"] == 0
    asyncio.run(run())


def test_rate_limit_pauses_same_key_without_exposing_response():
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, text="private key or coordinates must not be exposed")
    async def run():
        provider = TmapProvider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await provider.route(client, ORIGIN, (35.16, 129.07), "walk") is None
            assert await provider.route(client, ORIGIN, (35.17, 129.08), "car") is None
        assert provider.status()["last_error"] == "HTTP 429"
        assert "test-road-key" not in json.dumps(provider.status())
    asyncio.run(run())
    assert len(calls) == 1


def test_cache_expires_and_transport_modes_are_separate(monkeypatch):
    async def run():
        provider = TmapProvider()
        destination = (35.16, 129.07)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=summary()))) as client:
            await provider.route(client, ORIGIN, destination, "walk")
        assert provider.cached(ORIGIN, destination, "car") is None
        monkeypatch.setattr(settings, "tmap_cache_ttl_seconds", 0)
        assert provider.cached(ORIGIN, destination, "walk") is None
    asyncio.run(run())


def test_disabled_missing_key_and_origin_do_not_call_network(monkeypatch):
    service = RouteDistanceService()
    async def unexpected(*args, **kwargs):
        raise AssertionError("No route call expected")
    monkeypatch.setattr(service.provider, "route", unexpected)
    async def run():
        places = [cafe("A", 0.001)]
        _, info = await service.enrich(places, None, None, RouteBudget.start())
        assert info["status"] == "no_origin"
        monkeypatch.setattr(settings, "tmap_api_key", None)
        _, info = await service.enrich(places, ORIGIN, "walk", RouteBudget.start())
        assert info["status"] == "not_configured"
        monkeypatch.setattr(settings, "tmap_enabled", False)
        _, info = await service.enrich(places, ORIGIN, "transit", RouteBudget.start())
        assert info["status"] == "not_configured"
    asyncio.run(run())


def test_actual_distance_reverses_straight_order_and_is_not_replaced_by_time(monkeypatch):
    service = RouteDistanceService()
    async def route(client, origin, destination, mode):
        # 직선으로 가까운 곳이 실제로는 우회해야 한다. 시간 순위도 거리와 다르게 만든다.
        return RouteDistance(3000, 60) if destination[0] < ORIGIN[0] + 0.002 else RouteDistance(900, 1200)
    monkeypatch.setattr(service.provider, "route", route)
    original = [cafe("가깝지만 우회", 0.001), cafe("실제 가까움", 0.003)]
    enriched, info = asyncio.run(service.enrich(original, ORIGIN, None, RouteBudget.start()))
    ranked = RecommendationService().rank(UserIntent(intent="recommend_place", requests=[REQ]), REQ,
                                        enriched, anchor=ORIGIN, sort_by_distance=True)
    assert info["routed"] == 2 and info["mode"] == "walk"
    assert [p.name for p in ranked] == ["실제 가까움", "가깝지만 우회"]
    assert ranked[1].distance_m == 3000  # 1.5km 검색 반경은 직선 기준이므로 후보는 유지
    assert ranked[0].score_breakdown["access"]["score"] > ranked[1].score_breakdown["access"]["score"]
    assert "TMAP 도보 이동 거리 900m" == ranked[0].score_breakdown["access"]["reason"]
    assert all(p.distance_source == "tmap" for p in ranked)
    assert original[0].route_distance_m is None and original[0].distance_m is None


def test_shared_budget_and_concurrency_and_failure_fallback(monkeypatch):
    monkeypatch.setattr(settings, "tmap_max_routes", 2)
    service = RouteDistanceService()
    calls = []
    active = peak = 0
    async def route(client, origin, destination, mode):
        nonlocal active, peak
        calls.append(destination)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return None
    monkeypatch.setattr(service.provider, "route", route)
    async def run():
        budget = RouteBudget.start()
        places, info = await service.enrich([cafe(str(i), i * .001) for i in range(1, 5)], ORIGIN, "walk", budget)
        assert info["requested"] == 2 and info["fallback"] == 4
        assert all(p.distance_source == "straight" for p in places)
        ranked = RecommendationService().rank(UserIntent(intent="recommend_place", requests=[REQ]), REQ, places, anchor=ORIGIN)
        assert "직선거리" in ranked[0].score_breakdown["access"]["reason"]
        _, info = await service.enrich([cafe("다음 묶음", .005)], ORIGIN, "walk", budget)
        assert info["requested"] == 0
    asyncio.run(run())
    assert len(calls) == 2 and peak <= 2


def test_total_deadline_cancels_waiters(monkeypatch):
    monkeypatch.setattr(settings, "tmap_total_timeout_seconds", .03)
    service = RouteDistanceService()
    async def slow(*args):
        await asyncio.sleep(2)
    monkeypatch.setattr(service.provider, "route", slow)
    started = time.perf_counter()
    _, info = asyncio.run(service.enrich([cafe(str(i), i * .001) for i in range(1, 8)], ORIGIN, "walk", RouteBudget.start()))
    assert time.perf_counter() - started < .5
    assert info["status"] == "unavailable" and info["fallback"] == 7


def test_pipeline_keeps_route_distance_and_exposes_fallback(monkeypatch):
    pipeline = RecommendationPipeline()
    async def search(**kwargs):
        return [cafe("우회", .001), cafe("경로 실패", .003)], {}
    async def weather(point):
        return None
    async def stations(point):
        return []
    async def route(client, origin, destination, mode):
        return RouteDistance(2500, 600) if destination[0] < ORIGIN[0] + .002 else None
    monkeypatch.setattr(pipeline.api_router, "search", search)
    monkeypatch.setattr(pipeline.context, "weather", weather)
    monkeypatch.setattr(pipeline.context, "stations", stations)
    monkeypatch.setattr(pipeline.route_distance.provider, "route", route)
    intent = UserIntent(intent="recommend_place", requests=[REQ], transport_mode="walk")
    response = asyncio.run(pipeline.run(intent, 5, anchor_override=ORIGIN))
    places = {p.name: p for p in response.results[0].places}
    assert places["우회"].distance_m == 2500
    assert places["우회"].route_duration_minutes == 10
    assert places["경로 실패"].distance_source == "straight"
    assert response.context["routing"][0]["status"] == "partial"
    assert response.timing_ms["routing_ms"] >= 0
    assert "직선거리 추정" in response.notice


def test_place_at_origin_skips_route_and_notice(monkeypatch):
    """기준점 바로 그 자리(해운대 기준 해운대해수욕장)는 경로를 묻지 않고 '직선거리 추정' 안내에도 세지 않는다."""
    pipeline = RecommendationPipeline()
    calls = []
    async def search(**kwargs):
        return [cafe("기준점 자리", 0), cafe("근처", .002)], {}
    async def weather(point):
        return None
    async def stations(point):
        return []
    async def route(client, origin, destination, mode):
        calls.append(destination)
        return RouteDistance(300, 240)
    monkeypatch.setattr(pipeline.api_router, "search", search)
    monkeypatch.setattr(pipeline.context, "weather", weather)
    monkeypatch.setattr(pipeline.context, "stations", stations)
    monkeypatch.setattr(pipeline.route_distance.provider, "route", route)
    intent = UserIntent(intent="recommend_place", requests=[REQ], transport_mode="walk")
    response = asyncio.run(pipeline.run(intent, 5, anchor_override=ORIGIN))
    assert len(calls) == 1
    assert response.context["routing"][0]["status"] == "ok"
    assert "직선거리 추정" not in (response.notice or "")


def test_repeated_rate_limit_backs_off_and_skips_calls(monkeypatch):
    """한도 오류가 이어지면 쉬는 시간이 늘고, 쉬는 동안은 경로를 부르지 않는다(요청 수에도 세지 않음)."""
    now = [1000.0]
    monkeypatch.setattr("app.providers.tmap.time.monotonic", lambda: now[0])
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429)
    async def run():
        provider = TmapProvider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await provider.route(client, ORIGIN, (35.16, 129.07), "walk")
            assert provider.status()["paused_seconds"]["road"] == 30
            now[0] += 31
            await provider.route(client, ORIGIN, (35.16, 129.07), "walk")
            assert provider.status()["paused_seconds"]["road"] == 60
        service = RouteDistanceService()
        service.provider = provider
        _, info = await service.enrich([cafe("a", .002), cafe("b", .003)], ORIGIN, "walk", RouteBudget.start())
        assert info["status"] == "paused" and info["requested"] == 0 and info["fallback"] == 2
    asyncio.run(run())
    assert len(calls) == 2
