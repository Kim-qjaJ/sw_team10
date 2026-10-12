"""지역 대표점과 특정 시설 좌표를 구분하고 반복 시설 조회를 캐시한다."""

from collections import OrderedDict
import re
import time

import httpx

from app.config import settings
from app.schemas.location import LocationReference
from app.services.region_registry import LifestyleArea, get_region_registry

_ADMIN_SUFFIX = ("구", "군", "동", "읍", "면", "리")
_POINT_HINTS = ("역", "해수욕장", "해변", "대교", "공원", "시장", "백화점", "대학교", "대학", "학교", "병원", "시청", "구청", "터미널", "공항", "박물관", "미술관", "도서관")
_AREA_NAMES = {"덕천", "구포", "전포", "남포동", "송정", "기장"}


def _clean_location(text: str) -> str:
    return re.sub(r"^(부산광역시|부산시|부산)\s*", "", text.strip()).strip()


class LocationResolver:
    SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
    CACHE_TTL_SECONDS = 24 * 60 * 60
    MAX_CACHE_ENTRIES = 256

    def __init__(self):
        self._cache: OrderedDict[str, tuple[float, LocationReference]] = OrderedDict()
        self.cache_hits = 0

    def cache_stats(self) -> dict:
        return {"entries": len(self._cache), "hits": self.cache_hits}

    def from_current_location(self, latitude: float, longitude: float) -> LocationReference:
        return LocationReference(mode="current_location", resolved_name="현재 위치", latitude=latitude, longitude=longitude, source="browser_geolocation")

    async def resolve_text(self, location: str | None) -> LocationReference:
        if not location:
            return LocationReference(mode="unresolved", original_text=location)
        raw = location.strip()
        text = _clean_location(raw)
        registry = get_region_registry()

        area = registry.lifestyle(raw)
        if area is not None:
            return await self._resolve_lifestyle(raw, area)

        match = registry.resolve(raw)
        if match.candidates:
            return LocationReference(mode="ambiguous", original_text=raw, question=match.question, source="region_dictionary")
        if match.region:
            region = match.region
            return LocationReference(
                mode="area_anchor", original_text=raw, resolved_name=region.qualified_name,
                latitude=region.latitude, longitude=region.longitude, source="region_dictionary",
                region_code=region.code, region_level=region.level, coordinate_accuracy=region.accuracy,
                boundary_scope=registry.boundary_codes(region)[1], radius_m=region.radius_m or None,
            )
        if text.endswith(_ADMIN_SUFFIX) or text in _AREA_NAMES or not any(hint in text for hint in _POINT_HINTS):
            return LocationReference(mode="area_anchor", original_text=raw, resolved_name=raw, source="text")
        found = await self._kakao_lookup(text)
        if found is None:
            return LocationReference(mode="unresolved", original_text=raw)
        return found.model_copy(update={"original_text": raw})

    async def _resolve_lifestyle(self, raw: str, area: LifestyleArea) -> LocationReference:
        """생활권: 기준 시설 좌표(카카오) → 실패하면 대표 행정구역 중심(근사)."""
        registry = get_region_registry()
        fallback = area.fallback
        if area.anchor_query is None:
            # 기준 시설이 없는 생활권은 대표 행정구역 그대로 검색한다.
            return LocationReference(
                mode="area_anchor", original_text=raw, resolved_name=fallback.qualified_name,
                latitude=fallback.latitude, longitude=fallback.longitude, source="lifestyle_dictionary",
                region_code=fallback.code, region_level=fallback.level, coordinate_accuracy=fallback.accuracy,
                boundary_scope=registry.boundary_codes(fallback)[1], radius_m=fallback.radius_m or None,
            )
        found = await self._kakao_lookup(area.anchor_query, required_district=area.sgg)
        common = {"mode": "lifestyle_area", "original_text": raw, "resolved_name": f"부산 {area.name}",
                  "region_level": "lifestyle", "radius_m": area.radius_m}
        if found is not None and found.mode == "specific_place":
            return LocationReference(**common, latitude=found.latitude, longitude=found.longitude,
                                     source=f"lifestyle:{found.source}", coordinate_accuracy="facility")
        return LocationReference(**common, latitude=fallback.latitude, longitude=fallback.longitude,
                                 source="lifestyle_fallback", coordinate_accuracy="approximate")

    async def _kakao_lookup(self, text: str, required_district: str | None = None) -> LocationReference | None:
        """카카오 키워드 검색으로 부산 안의 첫 시설 좌표를 얻는다. 성공한 결과는 캐시한다.

        None: 키 없음/호출 실패, mode=not_found: 부산(또는 지정한 구) 안에서 찾지 못함.
        """
        key = re.sub(r"\s+", " ", text).casefold() + (f"@{required_district}" if required_district else "")
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.CACHE_TTL_SECONDS:
            self.cache_hits += 1
            self._cache.move_to_end(key)
            return cached[1].model_copy(update={"source": "kakao_cache"})
        if cached:
            self._cache.pop(key)
        if not settings.kakao_rest_api_key:
            return None
        headers = {"Authorization": f"KakaoAK {settings.kakao_rest_api_key.strip()}"}
        query = text if "부산" in text else f"부산 {text}"
        try:
            async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
                response = await client.get(self.SEARCH_URL, headers=headers, params={"query": query, "size": 5})
                response.raise_for_status()
                docs = response.json().get("documents", [])
        except Exception:
            return None
        for item in docs:
            address = f"{item.get('road_address_name', '')} {item.get('address_name', '')}"
            if "부산" not in address or (required_district and required_district not in address):
                continue
            try:
                lat, lng = float(item["y"]), float(item["x"])
            except (KeyError, TypeError, ValueError):
                continue
            resolved = LocationReference(mode="specific_place", original_text=text, resolved_name=item.get("place_name") or text,
                                         latitude=lat, longitude=lng, source="kakao")
            self._cache[key] = (time.monotonic(), resolved)
            self._cache.move_to_end(key)
            while len(self._cache) > self.MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)
            return resolved
        return LocationReference(mode="not_found", original_text=text, source="kakao")
