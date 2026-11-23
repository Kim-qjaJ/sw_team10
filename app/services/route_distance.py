"""추천 전에 후보의 TMAP 이동 거리를 채운다. 네트워크 대기는 점수 계산과 분리한다."""

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.providers.tmap import Point, TmapProvider, valid_point
from app.schemas.place import Place
from app.services.scoring import haversine_m

# 기준점과 거의 같은 자리(예: '해운대' 기준의 해운대해수욕장)는 경로를 물을 필요가 없고, TMAP도 출발=도착이면 실패한다.
ROUTE_MIN_M = 50


@dataclass
class RouteBudget:
    deadline: float
    remaining: int

    @classmethod
    def start(cls, limit_seconds: float | None = None):
        """limit_seconds: 응답 목표 시간까지 남은 시간. TMAP 예산(4초)보다 짧으면 그만큼만 기다린다."""
        seconds = max(0, settings.tmap_total_timeout_seconds)
        if limit_seconds is not None:
            seconds = max(0.0, min(seconds, limit_seconds))
        return cls(time.monotonic() + seconds, max(0, settings.tmap_max_routes))


class RouteDistanceService:
    def __init__(self):
        self.provider = TmapProvider()

    async def enrich(self, places: list[Place], origin: Point | None, mode: str | None,
                     budget: RouteBudget) -> tuple[list[Place], dict]:
        mode = mode or "walk"
        result = [p.model_copy(deep=True) for p in places]
        info = {"mode": mode, "status": "not_configured", "requested": 0, "routed": 0,
                "fallback": 0}
        if not self.provider.configured(mode):
            return result, info
        if not valid_point(origin):
            info["status"] = "no_origin"
            return result, info

        eligible = []
        for place in result:
            destination = (place.latitude, place.longitude) if place.latitude is not None and place.longitude is not None else None
            if valid_point(destination):
                place.route_distance_m = place.route_duration_minutes = place.route_mode = None
                place.straight_distance_m = round(haversine_m(*origin, place.latitude, place.longitude), 1)
                place.distance_m = place.straight_distance_m
                place.distance_source = "straight"
                if place.straight_distance_m >= ROUTE_MIN_M:
                    eligible.append(place)
        if eligible and time.monotonic() >= budget.deadline:
            # 응답 목표 시간까지 남은 시간이 없으면 경로를 묻지 않는다 (검색이 늦어진 경우, NFR-001).
            info.update(status="no_time", fallback=len(eligible))
            return result, info
        if eligible and self.provider.paused(mode):
            # 호출 한도·인증 오류로 쉬는 중이면 부르지 않고 모두 직선거리로 둔다.
            info.update(status="paused", fallback=len(eligible))
            return result, info
        # 호출 상한을 넘는 경우 우선 가까운 후보를 조회한다. 미조회·실패는 추정임을 점수 근거에 남긴다.
        eligible.sort(key=lambda p: p.straight_distance_m)
        semaphore = asyncio.Semaphore(max(1, settings.tmap_concurrency))
        lookups = {}
        async with httpx.AsyncClient() as client:
            async def fetch(destination):
                async with semaphore:
                    remaining = budget.deadline - time.monotonic()
                    if remaining <= 0 or budget.remaining <= 0 or self.provider.paused(mode):
                        return None  # 한도 오류로 쉬는 중이면 남은 후보는 부르지도, 세지도 않는다
                    budget.remaining -= 1
                    info["requested"] += 1
                    try:
                        return await asyncio.wait_for(self.provider.route(client, origin, destination, mode), remaining)
                    except asyncio.TimeoutError:
                        return None

            for place in eligible:
                destination = (place.latitude, place.longitude)
                if destination not in lookups:
                    cached = self.provider.cached(origin, destination, mode)
                    lookups[destination] = cached if cached is not None else asyncio.create_task(fetch(destination))
            try:
                tasks = [value for value in lookups.values() if isinstance(value, asyncio.Task)]
                if tasks:
                    await asyncio.gather(*tasks)
            finally:
                for value in lookups.values():
                    if isinstance(value, asyncio.Task) and not value.done():
                        value.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
        for place in eligible:
            value = lookups[(place.latitude, place.longitude)]
            route = value.result() if isinstance(value, asyncio.Task) else value
            if route is None:
                continue
            place.route_distance_m = place.distance_m = round(route.distance_m, 1)
            place.route_duration_minutes = round(route.duration_seconds / 60, 1) if route.duration_seconds is not None else None
            place.route_mode = mode
            place.distance_source = "tmap"
            if place.route_duration_minutes is not None:
                field = {"walk": "walking_minutes", "car": "driving_minutes", "transit": "transit_minutes"}[mode]
                setattr(place, field, place.route_duration_minutes)
            info["routed"] += 1
        info["fallback"] = len(eligible) - info["routed"]
        info["status"] = "ok" if not info["fallback"] else "partial" if info["routed"] else "unavailable"
        return result, info
