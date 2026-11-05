"""부산광역시 공공데이터 Provider. 결과는 모두 부산 특화 데이터(busan_verified=True)로 표시한다.

요청 종류에 따라 하나만 부른다.
- 관광 요청(tourism)           : 부산명소정보 서비스 (AttractionService/getAttractionKr)
- 전시 요청(전시·전시회)     : 전시 목록 서비스 (BusanCultureExhibitService)
  · 미술관·갤러리·박물관 같은 장소 찾기는 카카오·네이버가 맡고, 같은 장소면 병합 단계에서 전시 정보가 붙는다.
  · 지금 진행 중인 전시만 쓰고, 같은 전시장의 전시는 장소 하나의 events로 묶는다.
  · 전시 목록에는 좌표가 없어 전시장 이름을 카카오 키워드 검색으로 찾아 좌표를 붙인다.
- 그 밖의 요청(음식점·카페 등) : 부르지 않는다 (카카오·네이버가 맡는다)

공연장·전시장 대관 정보(전시공간 목록 서비스)는 관광 추천 취지에 맞지 않아 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.scoring import haversine_m

KST = ZoneInfo("Asia/Seoul")
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

# 데이터가 자주 바뀌지 않으므로 한 번 받은 목록을 재사용한다.
ATTRACTION_TTL_SECONDS = 24 * 3600
EXHIBIT_TTL_SECONDS = 3600
VENUE_TTL_SECONDS = 24 * 3600
PAGE_SIZE = 1000
MAX_PAGES = 3
MAX_RESULTS = 50
# 장소 하나에 저장하는 전시 수 (화면에는 앞의 몇 개만 보여주고 나머지는 '외 N개'로 표시)
MAX_EVENTS_PER_PLACE = 20
GEOCODE_CONCURRENCY = 5

EXHIBIT_WORDS = ("전시",)
# 이 검색어면 이름 필터 없이 전체를 후보로 쓴다.
GENERIC_ATTRACTION_QUERIES = {"관광지", "관광명소", "명소", "볼거리", "여행지", "가볼만한곳", "가볼곳", "놀거리", "관광"}
GENERIC_EXHIBIT_QUERIES = {"전시", "전시회", "전시장", "미술관", "갤러리", "박물관", "무료전시", "무료", "전시추천"}
# 관광 하위 종류는 이름·설명에 이 단어가 있는 명소만 남긴다.
SUBCATEGORY_WORDS = {
    "park": ("공원", "숲", "수목원"),
    "beach": ("해수욕장", "해변", "해안"),
    "viewpoint": ("전망", "스카이", "타워"),
}

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float):
    entry = _cache.get(key)
    if entry and time.monotonic() - entry[0] < ttl:
        return entry[1]
    return None


def _store(key: str, value) -> None:
    _cache[key] = (time.monotonic(), value)


def clear_cache() -> None:
    """테스트용: 캐시를 비운다."""
    _cache.clear()


def _to_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number else None


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _find_items(payload, marker: str) -> list[dict]:
    """응답 구조가 달라도 marker 필드를 가진 묶음(장소·전시 한 건)을 모두 꺼낸다."""
    items: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if marker in node:
                items.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return items


def _parse(response: httpx.Response, marker: str) -> tuple[list[dict], int | None]:
    """JSON 또는 XML 응답에서 항목 목록과 전체 건수를 꺼낸다."""
    try:
        payload = response.json()
    except ValueError:
        root = ET.fromstring(response.text)
        items = [
            {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in element}
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "item"
        ]
        total = next((e.text for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "totalCount"), None)
        return [item for item in items if marker in item], int(total) if total and total.isdigit() else None

    totals = []

    def find_total(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "totalCount":
                    totals.append(value)
                else:
                    find_total(value)
        elif isinstance(node, list):
            for value in node:
                find_total(value)

    find_total(payload)
    total = totals[0] if totals else None
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    return _find_items(payload, marker), total


def parse_date(value) -> date | None:
    """'2026-11-05', '20261105', '2026.11.05' 형식을 모두 날짜로 바꾼다."""
    digits = re.sub(r"\D", "", str(value or ""))[:8]
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def is_ongoing(item: dict, today: date) -> bool:
    """오늘 관람할 수 있는 전시인지.

    - 시작일이 오늘 이후면 제외한다.
    - 종료일이 있으면 상시 전시(op_at=Y)라도 종료일이 지났을 때 제외한다.
    - 종료일이 없으면 상시 전시만 포함한다.
    """
    start, end = parse_date(item.get("op_st_dt")), parse_date(item.get("op_ed_dt"))
    open_run = str(item.get("op_at") or "").upper() == "Y"
    if start is not None and start > today:
        return False
    if end is not None:
        return end >= today and (start is not None or open_run)
    return open_run


def today_kst() -> date:
    return datetime.now(KST).date()


def wants_exhibit(request: PlaceRequest) -> bool:
    """전시 자체를 찾는 요청만. '미술관 추천', '박물관 추천'은 장소 찾기라 카카오·네이버가 맡는다."""
    if request.category != "culture":
        return False
    return request.subcategory == "exhibition" or any(word in request.query for word in EXHIBIT_WORDS)


def name_matches(venue: str, place_name: str) -> bool:
    """전시장 이름과 카카오 장소 이름이 같은 곳인지. 공백·괄호를 무시하고 한쪽이 다른 쪽을 포함하면 같다고 본다."""
    a = re.sub(r"[\s()\[\]·]", "", venue.lower())
    b = re.sub(r"[\s()\[\]·]", "", place_name.lower())
    if not a or not b:
        return False
    return a == b or (len(min(a, b, key=len)) >= 3 and (a in b or b in a))


def within_radius(places: list[Place], radius_m: int | None) -> list[Place]:
    """반경 검색이면 기준점에서 radius_m 안의 장소만 남긴다."""
    if not radius_m:
        return places
    return [p for p in places if p.distance_m is not None and p.distance_m <= radius_m]


class BusanProvider(PlaceProvider):
    name = "busan"
    # 두 데이터 모두 좌표가 있으므로(전시장은 카카오로 찾음) 주변 시설 찾기·범위 넓히기에도 쓴다.
    supports_radius = True

    async def search(
        self,
        request: PlaceRequest,
        location: str | None = None,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
    ) -> list[Place]:
        api_key = settings.public_data_service_key
        if not api_key:
            raise ProviderNotConfigured("공공데이터포털 API 키가 설정되지 않았습니다.")
        if request.category == "tourism":
            places = await self._attractions(request, anchor, api_key)
        elif wants_exhibit(request):
            places = await self._exhibits(request, anchor, api_key)
        else:
            return []
        return within_radius(places, radius_m if anchor else None)
        return []

    # ------------------------------------------------------------------
    # 부산명소정보
    # ------------------------------------------------------------------
    async def _fetch_all(self, url: str, params: dict, marker: str, cache_key: str, ttl: float) -> list[dict]:
        cached = _cached(cache_key, ttl)
        if cached is not None:
            return cached
        items: list[dict] = []
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            for page in range(1, MAX_PAGES + 1):
                response = await client.get(url, params={**params, "pageNo": page, "numOfRows": PAGE_SIZE})
                response.raise_for_status()
                page_items, total = _parse(response, marker)
                items.extend(page_items)
                if not page_items or total is None or len(items) >= total:
                    break
        _store(cache_key, items)
        return items

    async def _attractions(self, request: PlaceRequest, anchor, api_key: str) -> list[Place]:
        items = await self._fetch_all(
            settings.busan_attraction_url,
            {"serviceKey": api_key, "resultType": "json"},
            marker="UC_SEQ",
            cache_key="attractions",
            ttl=ATTRACTION_TTL_SECONDS,
        )
        query = _compact(request.query)
        name_filter = bool(query) and query not in GENERIC_ATTRACTION_QUERIES and not request.subcategory
        sub_words = SUBCATEGORY_WORDS.get(request.subcategory or "", ())

        places: list[Place] = []
        for item in items:
            name = str(item.get("PLACE") or item.get("MAIN_TITLE") or "").strip()
            lat, lng = _to_float(item.get("LAT")), _to_float(item.get("LNG"))
            if not name or lat is None or lng is None:
                continue
            text = " ".join(str(item.get(k) or "") for k in ("PLACE", "MAIN_TITLE", "TITLE", "SUBTITLE"))
            if name_filter and query not in _compact(text):
                continue
            if sub_words and not any(word in text for word in sub_words):
                continue
            uc_seq = str(item.get("UC_SEQ") or name)
            places.append(Place(
                id=f"busan_attraction_{uc_seq}",
                name=name,
                category=request.category,
                subcategory=request.subcategory,
                address=str(item.get("ADDR1") or "").strip() or None,
                phone=str(item.get("CNTCT_TEL") or "").strip() or None,
                url=str(item.get("HOMEPAGE_URL") or "").strip() or None,
                latitude=lat,
                longitude=lng,
                distance_m=round(haversine_m(anchor[0], anchor[1], lat, lng), 1) if anchor else None,
                sources=[self.name],
                source_ids={self.name: uc_seq},
                source_categories={self.name: "관광명소"},
                busan_verified=True,
            ))
        return _nearest(places, anchor)

    # ------------------------------------------------------------------
    # 전시 목록
    # ------------------------------------------------------------------
    async def _exhibits(self, request: PlaceRequest, anchor, api_key: str) -> list[Place]:
        items = await self._fetch_all(
            settings.busan_exhibit_url,
            {"serviceKey": api_key, "resultType": "json"},
            marker="res_no",
            cache_key="exhibits",
            ttl=EXHIBIT_TTL_SECONDS,
        )
        today = today_kst()
        query = _compact(request.query)
        free_only = "무료" in request.query
        name_filter = bool(query) and query not in GENERIC_EXHIBIT_QUERIES and not any(
            query == _compact(word) for word in EXHIBIT_WORDS
        )

        # 전시장별로 진행 중인 전시를 묶는다.
        venues: dict[str, list[dict]] = {}
        for item in items:
            venue = str(item.get("place_nm") or "").strip()
            title = str(item.get("title") or "").strip()
            if not venue or not title or not is_ongoing(item, today):
                continue
            if free_only and str(item.get("pay_at") or "").upper() != "N":
                continue
            if name_filter and query.replace("무료", "") not in _compact(f"{title} {venue}"):
                continue
            start, end = parse_date(item.get("op_st_dt")), parse_date(item.get("op_ed_dt"))
            venues.setdefault(venue, []).append({
                "title": title,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "open_run": str(item.get("op_at") or "").upper() == "Y",
                "free": str(item.get("pay_at") or "").upper() == "N",
            })
        if not venues:
            return []

        coords = await self._venue_coordinates(list(venues))
        places: list[Place] = []
        for venue, events in venues.items():
            found = coords.get(venue)
            if found is None:
                # 좌표가 없으면 지도·거리 계산을 할 수 없으므로 후보에서 뺀다.
                continue
            lat, lng, address, phone, url = found
            events.sort(key=lambda e: (e["end"] is None, e["end"] or ""))
            places.append(Place(
                id=f"busan_exhibit_{venue}",
                name=venue,
                category=request.category,
                subcategory="exhibition",
                address=address,
                phone=phone,
                url=url,
                latitude=lat,
                longitude=lng,
                distance_m=round(haversine_m(anchor[0], anchor[1], lat, lng), 1) if anchor else None,
                indoor=True,
                sources=[self.name],
                source_ids={self.name: venue},
                source_categories={self.name: "문화시설 > 전시"},
                busan_verified=True,
                events=events[:MAX_EVENTS_PER_PLACE],
            ))
        return _nearest(places, anchor)

    async def _venue_coordinates(self, venues: list[str]) -> dict[str, tuple | None]:
        """전시장 이름 → (위도, 경도, 주소, 전화, URL). 카카오 키가 없으면 빈 결과."""
        if not settings.kakao_rest_api_key:
            return {}
        result: dict[str, tuple | None] = {}
        missing: list[str] = []
        for venue in venues:
            cached = _cached(f"venue:{venue}", VENUE_TTL_SECONDS)
            if cached is not None:
                result[venue] = cached or None
            else:
                missing.append(venue)
        if not missing:
            return result

        semaphore = asyncio.Semaphore(GEOCODE_CONCURRENCY)
        headers = {"Authorization": f"KakaoAK {settings.kakao_rest_api_key.strip()}"}

        async def lookup(client: httpx.AsyncClient, venue: str):
            async with semaphore:
                try:
                    response = await client.get(KAKAO_KEYWORD_URL, headers=headers,
                                                params={"query": f"부산 {venue}", "size": 5})
                    response.raise_for_status()
                    documents = response.json().get("documents") or []
                except (httpx.HTTPError, ValueError):
                    return venue, None, False
            # 첫 결과를 그대로 쓰지 않고, 부산 주소이면서 이름이 맞는 장소만 쓴다. (정확히 같은 이름 우선)
            candidates = []
            for doc in documents:
                address = doc.get("road_address_name") or doc.get("address_name") or ""
                name = doc.get("place_name") or ""
                if "부산" in address and name_matches(venue, name) and doc.get("x") and doc.get("y"):
                    exact = re.sub(r"\s", "", name) == re.sub(r"\s", "", venue)
                    candidates.append((not exact, doc, address))
            if not candidates:
                return venue, (), True   # 맞는 장소 없음 (빈 값도 캐시해 다시 조회하지 않는다)
            _, doc, address = min(candidates, key=lambda c: c[0])
            return venue, (float(doc["y"]), float(doc["x"]), address, doc.get("phone") or None,
                           doc.get("place_url") or None), True

        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            for venue, found, cacheable in await asyncio.gather(*(lookup(client, v) for v in missing)):
                if cacheable:
                    _store(f"venue:{venue}", found)
                result[venue] = found or None
        return result


def _nearest(places: list[Place], anchor) -> list[Place]:
    """기준점이 있으면 가까운 순으로 자른다. 없으면 지역 필터를 파이프라인에 맡기고 그대로 둔다."""
    if anchor:
        places.sort(key=lambda p: p.distance_m if p.distance_m is not None else float("inf"))
        return places[:MAX_RESULTS]
    return places
