"""TMAP 보행자·자동차·대중교통 경로의 총 거리(m)와 시간(초).

공식 규격: https://tmap-skopenapi.readme.io/reference/보행자-경로안내
https://tmap-skopenapi.readme.io/reference/자동차-경로안내
https://transit.tmapmobility.com/docs/routes/sub
키와 좌표를 로그에 남기지 않고, 경로 요약만 짧게 메모리에 캐시한다.
"""

import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.config import settings

Point = tuple[float, float]  # 위도, 경도
MAX_CACHE_ENTRIES = 2048
URLS = {
    "walk": "https://apis.openapi.sk.com/tmap/routes/pedestrian",
    "car": "https://apis.openapi.sk.com/tmap/routes",
    "transit": "https://apis.openapi.sk.com/transit/routes/sub",
}


@dataclass(frozen=True)
class RouteDistance:
    distance_m: float
    duration_seconds: float | None = None


def valid_point(point: Point | None) -> bool:
    return bool(point is not None and len(point) == 2
                and all(math.isfinite(v) for v in point)
                and -90 <= point[0] <= 90 and -180 <= point[1] <= 180)


def nonnegative(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


class TmapProvider:
    def __init__(self):
        self._cache: OrderedDict[tuple, tuple[float, RouteDistance]] = OrderedDict()
        self._paused_until: dict[str, float] = {}
        # 인증·한도 오류가 이어지면(일일 호출량 소진 등) 쉬는 시간을 30초에서 최대 10분까지 늘린다.
        self._failures: dict[str, int] = {}
        self.last_error: str | None = None

    @staticmethod
    def key(mode: str) -> str | None:
        value = settings.tmap_transit_api_key if mode == "transit" else settings.tmap_api_key
        return (value or "").strip() or None

    def configured(self, mode: str) -> bool:
        return settings.tmap_enabled and mode in URLS and bool(self.key(mode))

    def status(self) -> dict:
        return {"enabled": settings.tmap_enabled,
                "road_configured": bool(self.key("walk")),
                "transit_configured": bool(self.key("transit")),
                "cache_entries": len(self._cache), "last_error": self.last_error,
                "paused_seconds": {group: round(until - time.monotonic())
                                   for group, until in self._paused_until.items() if until > time.monotonic()}}

    @staticmethod
    def key_group(mode: str) -> str:
        return "transit" if mode == "transit" else "road"

    def paused(self, mode: str) -> bool:
        return time.monotonic() < self._paused_until.get(self.key_group(mode), 0)

    @staticmethod
    def cache_key(origin: Point, destination: Point, mode: str) -> tuple:
        # 일방통행·상하행 때문에 출발지와 목적지의 방향을 구분한다.
        return (mode, *origin, *destination)

    def cached(self, origin: Point, destination: Point, mode: str) -> RouteDistance | None:
        key = self.cache_key(origin, destination, mode)
        item = self._cache.get(key)
        if item is None:
            return None
        if time.monotonic() - item[0] >= min(settings.tmap_cache_ttl_seconds, 3600.0):
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return item[1]

    async def route(self, client: httpx.AsyncClient, origin: Point, destination: Point,
                    mode: str) -> RouteDistance | None:
        if not self.configured(mode) or not valid_point(origin) or not valid_point(destination):
            return None
        cached = self.cached(origin, destination, mode)
        if cached is not None:
            return cached
        # 인증·호출 한도 오류 뒤 같은 키를 반복해서 두드리지 않는다.
        key_group = self.key_group(mode)
        if self.paused(mode):
            return None
        body = {"startX": origin[1], "startY": origin[0],
                "endX": destination[1], "endY": destination[0]}
        params = None
        if mode == "transit":
            body = {k: str(v) for k, v in body.items()}
            body.update(count=1, format="json")
        else:
            params = {"version": "1"}
            body.update(reqCoordType="WGS84GEO", resCoordType="WGS84GEO",
                        searchOption="10" if mode == "walk" else 10)
            if mode == "walk":
                body.update(startName=quote("출발지"), endName=quote("도착지"))
            else:
                body["totalValue"] = 2
        try:
            response = await client.post(URLS[mode], params=params, json=body,
                                         headers={"appKey": self.key(mode), "Accept": "application/json"},
                                         timeout=max(0.05, settings.tmap_timeout_seconds))
            response.raise_for_status()
            data = response.json()
            if mode == "transit":
                summaries = data["metaData"]["plan"]["itineraries"]
            else:
                summaries = [feature.get("properties", {}) for feature in data["features"]]
            routes = [RouteDistance(distance, nonnegative(item.get("totalTime")))
                      for item in summaries
                      if (distance := nonnegative(item.get("totalDistance"))) is not None]
            if not routes:
                raise ValueError("경로 요약 없음")
            result = min(routes, key=lambda r: r.distance_m)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            self.last_error = f"HTTP {code}"
            if code in {401, 403, 429}:
                failures = self._failures.get(key_group, 0) + 1
                self._failures[key_group] = failures
                self._paused_until[key_group] = time.monotonic() + min(600.0, 30.0 * 2 ** (failures - 1))
            return None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            self.last_error = "경로 조회 실패 또는 응답 형식 오류"
            return None
        self.last_error = None
        self._failures.pop(key_group, None)
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)
        self._cache[self.cache_key(origin, destination, mode)] = (time.monotonic(), result)
        return result
