"""부산광역시 공공데이터 Provider. 결과는 모두 부산 특화 데이터(busan_verified=True)로 표시한다.

요청 종류에 따라 하나만 부른다.
- 관광 요청(tourism)           : 부산명소정보 서비스 (AttractionService/getAttractionKr)
- 전시 요청(전시·전시회)     : 한국문화정보원 한눈에보는문화정보조회서비스 (cultureinfo, 지역=부산, 분야=전시)
  · 미술관·갤러리·박물관 같은 장소 찾기는 카카오·네이버가 맡고, 같은 장소면 병합 단계에서 전시 정보가 붙는다.
  · 지금 진행 중인 전시만 쓰고, 같은 전시장의 전시는 장소 하나의 events로 묶는다.
  · 목록에 전시장 좌표가 있고, 가격(무료 여부)·주소·전화·홈페이지는 상세 조회로 채운다.
  · 2026-11 부산 전시 목록 서비스(BusanCultureExhibitService)가 응답하지 않아 이 서비스로 바꿨다.
- 그 밖의 요청(음식점·카페 등) : 부르지 않는다 (카카오·네이버가 맡는다)

공연장·전시장 대관 정보(전시공간 목록 서비스)는 관광 추천 취지에 맞지 않아 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.providers import rest_days
from app.providers.districts import district_name
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.scoring import haversine_m

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# 데이터가 자주 바뀌지 않으므로 한 번 받은 목록을 재사용한다.
ATTRACTION_TTL_SECONDS = 24 * 3600
EXHIBIT_TTL_SECONDS = 3600
DETAIL_TTL_SECONDS = 24 * 3600
PAGE_SIZE = 1000
# 목록 전체를 받아 캐시하는 호출은 일반 Provider 제한(5초)보다 조금 더 기다린다. 받은 뒤에는 캐시를 쓴다.
LIST_TIMEOUT_SECONDS = 8.0
# 목록 조회가 실패하면 이 시간 동안은 다시 부르지 않는다 (2026-11 전시 목록 서버가 응답 없이 멈춘 적이 있다).
FAILURE_TTL_SECONDS = 600
MAX_PAGES = 3
MAX_RESULTS = 50
# 기준점 없이 부산 전체를 찾을 때 넘기는 최대 개수. 부산명소정보는 2026-11 기준 213건이라 전부 넘긴다.
MAX_UNANCHORED_RESULTS = 300
# 장소 하나에 저장하는 전시 수 (화면에는 앞의 몇 개만 보여주고 나머지는 '외 N개'로 표시)
MAX_EVENTS_PER_PLACE = 20
# 공공데이터포털은 짧은 시간에 많이 부르면 429(요청 과다)로 거절하므로 상세 조회는 하나씩 간격을 두고 부른다.
# 그래서 상세는 뒤에서 따로 받아 하루 동안 저장해 두고, 사용자 요청은 아래 시간까지만 기다린다.
DETAIL_INTERVAL_SECONDS = 0.25
DETAIL_ATTEMPTS = 3
DETAIL_RETRY_DELAY_SECONDS = 1.0
DETAIL_WAIT_SECONDS = 2.5
# 문화정보 목록은 한 번에 30건 남짓만 주므로 쪽을 넘겨 받는다.
CULTURE_PAGE_SIZE = 100
CULTURE_MAX_PAGES = 5
# 부산 밖 좌표가 잘못 들어온 전시는 뺀다.
BUSAN_LAT = (34.85, 35.45)
BUSAN_LNG = (128.70, 129.35)

EXHIBIT_WORDS = ("전시",)
# 이 검색어면 이름 필터 없이 전체를 후보로 쓴다.
GENERIC_ATTRACTION_QUERIES = {"관광지", "관광명소", "명소", "볼거리", "여행지", "가볼만한곳", "가볼곳", "갈만한곳",
                              "놀거리", "놀곳", "구경거리", "관광"}
GENERIC_EXHIBIT_QUERIES = {"전시", "전시회", "전시장", "미술관", "갤러리", "박물관", "무료전시", "무료", "전시추천"}
# 관광 하위 종류는 이름·설명에 이 단어가 있는 명소만 남긴다.
SUBCATEGORY_WORDS = {
    "park": ("공원", "숲", "수목원"),
    "beach": ("해수욕장", "해변", "해안"),
    "viewpoint": ("전망", "스카이", "타워"),
}

_cache: dict[str, tuple[float, object]] = {}
# 전시 상세를 받는 뒤쪽 작업 (한 번에 하나만 돈다)
_detail_task: asyncio.Task | None = None


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
    global _detail_task
    _detail_task = None


def _to_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number else None


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


# 검색어에서 뗄 지역·꾸밈말. 남는 말이 있을 때만 이름 필터를 건다.
FILLER_WORDS = ("부산광역시", "부산시", "부산", "추천", "근처", "주변", "요즘", "인기", "유명한", "좋은", "알려줘")


def keywords(query: str, generic: set[str]) -> list[str]:
    """이름 필터에 쓸 핵심어. 일반 검색어·지역·꾸밈말을 떼고 두 글자 이상 남은 말만.

    '부산 관광지' → [], '무료 전시회' → [], '가볼 만한 곳' → [], '광안리 사진 전시' → ['광안리', '사진'],
    '부산시립미술관 전시' → ['부산시립'] (이름에 붙은 '부산'은 떼지 않는다)
    """
    fillers = {_compact(w) for w in FILLER_WORDS}
    whole = "".join(t for t in (query or "").split() if _compact(t) not in fillers)
    if not whole or _compact(whole) in generic:
        return []
    by_length = sorted(generic, key=len, reverse=True)
    words = []
    for token in (query or "").split():
        word = _compact(token)
        if word in fillers:
            continue
        for w in by_length:
            word = word.replace(w, "")
        if len(word) >= 2 and word not in fillers:
            words.append(word)
    return words


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


def clean_text(value) -> str:
    """'&amp;lt;' 처럼 두 번 바뀐 기호까지 원래 글자로 되돌린다."""
    text = str(value or "").strip()
    for _ in range(2):
        text = html.unescape(text)
    return text


# 전시 상세 글에서 쉬는 날 문장을 찾는다 ('매주 월요일 휴관', '휴관일: 1월 1일').
_REST_SENTENCE = re.compile(r"[^.\n/·]*?(?:휴관|휴무|휴장|관람\s*불가)[^.\n/]*")


def exhibit_rest(detail: dict) -> str | None:
    """전시 상세(소개 글 등)에 적힌 쉬는 날. 전시장 정보보다 전시 자체의 일정이 먼저다 (2026-11-23 검토)."""
    texts = []
    for key, value in detail.items():
        if isinstance(value, str) and not key.lower().endswith(("url", "addr", "phone", "gpsx", "gpsy")):
            texts.append(rest_days.clean_rest(value) or "")
    found = []
    for text in texts:
        for sentence in _REST_SENTENCE.findall(text):
            sentence = re.sub(r"\s+", " ", sentence).strip(" :-")
            if sentence and sentence not in found and len(sentence) <= 80:
                found.append(sentence)
    return " / ".join(found[:2]) or None


def is_free(price: str | None) -> bool | None:
    """상세 조회의 가격 문구로 무료 여부를 판단한다. 문구가 없으면 모름(None)."""
    text = clean_text(price)
    if not text:
        return None
    return "무료" in text and not re.search(r"\d+\s*원", text)


def in_busan(lat: float | None, lng: float | None) -> bool:
    return lat is not None and lng is not None and BUSAN_LAT[0] <= lat <= BUSAN_LAT[1] and BUSAN_LNG[0] <= lng <= BUSAN_LNG[1]


def today_kst() -> date:
    return datetime.now(KST).date()


def wants_exhibit(request: PlaceRequest) -> bool:
    """전시 자체를 찾는 요청만. '미술관 추천', '박물관 추천'은 장소 찾기라 카카오·네이버가 맡는다."""
    if request.category != "culture":
        return False
    return request.subcategory == "exhibition" or any(word in request.query for word in EXHIBIT_WORDS)


def within_radius(places: list[Place], radius_m: int | None) -> list[Place]:
    """반경 검색이면 기준점에서 radius_m 안의 장소만 남긴다."""
    if not radius_m:
        return places
    return [p for p in places if p.distance_m is not None and p.distance_m <= radius_m]


class BusanProvider(PlaceProvider):
    name = "busan"
    # 두 데이터 모두 좌표가 있으므로 주변 시설 찾기·범위 넓히기에도 쓴다.
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
            places = await self._attractions(request, anchor, api_key, location)
        elif wants_exhibit(request):
            places = await self._exhibits(request, anchor, api_key)
        else:
            return []
        return within_radius(places, radius_m if anchor else None)

    # ------------------------------------------------------------------
    # 부산명소정보
    # ------------------------------------------------------------------
    async def _fetch_all(self, url: str, params: dict, marker: str, cache_key: str, ttl: float) -> list[dict]:
        cached = _cached(cache_key, ttl)
        if cached is not None:
            return cached
        failed = _cached(f"failed:{cache_key}", FAILURE_TTL_SECONDS)
        if failed is not None:
            raise httpx.TimeoutException(f"부산 {cache_key} 목록이 최근 응답하지 않아 잠시 건너뜁니다 ({failed})")
        try:
            return await self._download(url, params, marker, cache_key)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            _store(f"failed:{cache_key}", type(exc).__name__)
            raise

    async def _download(self, url: str, params: dict, marker: str, cache_key: str) -> list[dict]:
        items: list[dict] = []
        started = time.monotonic()
        timeout = httpx.Timeout(LIST_TIMEOUT_SECONDS, connect=settings.provider_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for page in range(1, MAX_PAGES + 1):
                page_started = time.monotonic()
                try:
                    response = await client.get(url, params={**params, "pageNo": page, "numOfRows": PAGE_SIZE})
                except httpx.TimeoutException:
                    logger.warning("부산 %s 목록 %d쪽 시간 초과 (%.1f초)", cache_key, page, time.monotonic() - page_started)
                    raise
                response.raise_for_status()
                page_items, total = _parse(response, marker)
                items.extend(page_items)
                if not page_items or total is None or len(items) >= total:
                    break
        elapsed = time.monotonic() - started
        # 첫 조회가 느리면 원인 파악용으로 남긴다 (이후에는 캐시를 쓴다).
        log = logger.warning if elapsed > settings.provider_timeout_seconds else logger.info
        log("부산 %s 목록 %d건 받음 (%d쪽, %.1f초)", cache_key, len(items), page, elapsed)
        _store(cache_key, items)
        return items

    async def _attractions(self, request: PlaceRequest, anchor, api_key: str, location: str | None = None) -> list[Place]:
        items = await self._fetch_all(
            settings.busan_attraction_url,
            {"serviceKey": api_key, "resultType": "json"},
            marker="UC_SEQ",
            cache_key="attractions",
            ttl=ATTRACTION_TTL_SECONDS,
        )
        # 하위 종류(공원·해수욕장 등)가 있으면 이름 대신 하위 종류 단어로 거른다.
        words = [] if request.subcategory else keywords(request.query, GENERIC_ATTRACTION_QUERIES)
        sub_words = SUBCATEGORY_WORDS.get(request.subcategory or "", ())
        # 구·군을 말했으면 그 구·군 명소만 (기준점이 있으면 거리로 고르므로 거르지 않는다)
        district = None if anchor else district_name(location)

        places: list[Place] = []
        for item in items:
            name = str(item.get("PLACE") or item.get("MAIN_TITLE") or "").strip()
            lat, lng = _to_float(item.get("LAT")), _to_float(item.get("LNG"))
            if not name or lat is None or lng is None:
                continue
            text = " ".join(str(item.get(k) or "") for k in ("PLACE", "MAIN_TITLE", "TITLE", "SUBTITLE"))
            if words and not any(w in _compact(f"{text} {item.get('ADDR1') or ''}") for w in words):
                continue
            if sub_words and not any(word in text for word in sub_words):
                continue
            if district and district not in f"{item.get('GUGUN_NM') or ''} {item.get('ADDR1') or ''}":
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
                image_url=str(item.get("MAIN_IMG_NORMAL") or item.get("MAIN_IMG_THUMB") or "").strip() or None,
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
    # ------------------------------------------------------------------
    # 전시 (한국문화정보원 문화정보)
    # ------------------------------------------------------------------
    async def _exhibits(self, request: PlaceRequest, anchor, api_key: str) -> list[Place]:
        items = await self._fetch_culture_list(api_key)
        today = today_kst()
        free_only = "무료" in request.query
        words = keywords(request.query, GENERIC_EXHIBIT_QUERIES | {"전시회", "무료"})

        selected = []
        for item in items:
            if not item["place"] or not item["title"] or not is_ongoing(item, today):
                continue
            if not in_busan(item["lat"], item["lng"]):
                continue
            if words and not any(w in _compact(f"{item['title']} {item['place']}") for w in words):
                continue
            selected.append(item)
        if not selected:
            return []

        details = await self._culture_details([item["seq"] for item in selected], api_key)
        # 전시장별로 진행 중인 전시를 묶는다.
        venues: dict[str, dict] = {}
        for item in selected:
            detail = details.get(item["seq"]) or {}
            free = is_free(detail.get("price"))
            if free_only and free is not True:
                continue
            # 전시마다 오늘 관람할 수 있는지 먼저 본다. 전시 글에 오늘이 쉬는 날로 적혀 있으면 그 전시는 뺀다.
            event_rest = exhibit_rest(detail)
            if rest_days.closed_on(event_rest, today):
                continue
            start, end = parse_date(item.get("op_st_dt")), parse_date(item.get("op_ed_dt"))
            group = venues.setdefault(item["place"], {"item": item, "detail": detail, "events": []})
            # 같은 전시장의 전시 중 주소가 있는 상세를 쓴다.
            if detail.get("placeAddr") and not group["detail"].get("placeAddr"):
                group["detail"] = detail
            group["events"].append({
                "title": item["title"],
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "open_run": False,
                "free": free,
                "rest": event_rest,
            })

        places: list[Place] = []
        for venue, group in venues.items():
            item, detail, events = group["item"], group["detail"], group["events"]
            lat, lng = item["lat"], item["lng"]
            events.sort(key=lambda e: (e["end"] is None, e["end"] or ""))
            places.append(Place(
                id=f"busan_exhibit_{venue}",
                name=venue,
                category=request.category,
                subcategory="exhibition",
                address=clean_text(detail.get("placeAddr")) or (f"부산 {item['sigungu']}" if item["sigungu"] else None),
                phone=_phone(detail.get("phone")),
                url=clean_text(detail.get("placeUrl")) or None,
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
        # 정기 휴관일: 저장된 것만 바로 쓰고, 모르는 전시장은 뒤에서 받아 다음 요청부터 반영한다.
        unknown = []
        for place in places:
            known, rest = rest_days.cached_rest(place.name)
            if not known:
                unknown.append(place.name)
            # 전시장 쉬는 날을 모르면 전시 글에 적힌 쉬는 날을 쓴다.
            rest = rest or next((e["rest"] for e in place.events if e.get("rest")), None)
            place.rest_info = rest
            place.closed_today = rest_days.closed_on(rest, today)
        if unknown:
            rest_days.fetch_in_background(unknown)
        return _nearest(places, anchor)

    async def _fetch_culture_list(self, api_key: str) -> list[dict]:
        """부산 지역 문화정보 목록에서 전시만 꺼내 기존 전시 항목 형식으로 바꾼다 (1시간 캐시)."""
        cache_key = "exhibits"
        cached = _cached(cache_key, EXHIBIT_TTL_SECONDS)
        if cached is not None:
            return cached
        failed = _cached(f"failed:{cache_key}", FAILURE_TTL_SECONDS)
        if failed is not None:
            raise httpx.TimeoutException(f"문화정보 전시 목록이 최근 응답하지 않아 잠시 건너뜁니다 ({failed})")

        url = f"{settings.culture_info_url.rstrip('/')}/area2"
        raw: list[dict] = []
        started = time.monotonic()
        timeout = httpx.Timeout(LIST_TIMEOUT_SECONDS, connect=settings.provider_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                for page in range(1, CULTURE_MAX_PAGES + 1):
                    response = await client.get(url, params={
                        "serviceKey": api_key, "sido": "부산", "PageNo": page, "numOfrows": CULTURE_PAGE_SIZE,
                    })
                    response.raise_for_status()
                    page_items, total = _parse(response, "seq")
                    raw.extend(page_items)
                    if not page_items or total is None or len(raw) >= total:
                        break
        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            _store(f"failed:{cache_key}", type(exc).__name__)
            raise

        items = []
        for item in raw:
            if clean_text(item.get("realmName")) != "전시" and clean_text(item.get("serviceName")) != "전시":
                continue
            items.append({
                "seq": str(item.get("seq") or "").strip(),
                "title": clean_text(item.get("title")),
                "place": clean_text(item.get("place")),
                "op_st_dt": item.get("startDate"),
                "op_ed_dt": item.get("endDate"),
                # 이 서비스는 기간이 항상 있어 상시 전시도 종료일로 판단한다.
                "op_at": "N",
                "sigungu": clean_text(item.get("sigungu")),
                "lat": _to_float(item.get("gpsY")),
                "lng": _to_float(item.get("gpsX")),
            })
        logger.info("문화정보 부산 목록 %d건 중 전시 %d건 (%.1f초)", len(raw), len(items), time.monotonic() - started)
        _store(cache_key, items)
        return items

    async def _culture_details(self, seqs: list[str], api_key: str) -> dict[str, dict]:
        """전시 번호 → 상세(가격·주소·전화·홈페이지). 아직 못 받은 전시는 빈 값으로 두고 목록 정보만 쓴다."""
        missing = [seq for seq in dict.fromkeys(seqs) if _cached(f"detail:{seq}", DETAIL_TTL_SECONDS) is None]
        if missing:
            task = _detail_fetch_task(missing, api_key)
            try:
                await asyncio.wait_for(asyncio.shield(task), DETAIL_WAIT_SECONDS)
            except asyncio.TimeoutError:
                logger.info("문화정보 전시 상세를 아직 받는 중이라 받은 것까지만 씁니다")
        return {seq: _cached(f"detail:{seq}", DETAIL_TTL_SECONDS) or {} for seq in dict.fromkeys(seqs)}


def _detail_fetch_task(seqs: list[str], api_key: str) -> asyncio.Task:
    """상세 받기 작업을 하나만 돌린다. 이미 돌고 있으면 그 작업을 기다린다."""
    global _detail_task
    if _detail_task is None or _detail_task.done():
        _detail_task = asyncio.create_task(_fetch_details(seqs, api_key))
    return _detail_task


async def _fetch_details(seqs: list[str], api_key: str) -> None:
    """상세를 하나씩 간격을 두고 받아 저장한다. 429나 일시 오류면 잠시 쉬고 다시 시도하고,
    그래도 실패한 전시는 저장하지 않아 다음 요청 때 다시 받는다."""
    url = f"{settings.culture_info_url.rstrip('/')}/detail2"
    failed = 0
    async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
        for index, seq in enumerate(seqs):
            if _cached(f"detail:{seq}", DETAIL_TTL_SECONDS) is not None:
                continue
            if index:
                await asyncio.sleep(DETAIL_INTERVAL_SECONDS)
            detail = None
            for attempt in range(DETAIL_ATTEMPTS):
                try:
                    response = await client.get(url, params={"serviceKey": api_key, "seq": seq})
                    response.raise_for_status()
                    found, _ = _parse(response, "seq")
                    if found:
                        detail = found[0]
                        break
                except (httpx.HTTPError, ET.ParseError, ValueError):
                    pass
                if attempt + 1 < DETAIL_ATTEMPTS:
                    await asyncio.sleep(DETAIL_RETRY_DELAY_SECONDS)
            if detail is None:
                failed += 1
            else:
                _store(f"detail:{seq}", detail)
    if failed:
        logger.warning("문화정보 전시 상세 %d건 중 %d건 조회 실패 (무료 여부·주소 없이 보여줌)", len(seqs), failed)


async def prefetch_exhibits() -> None:
    """서버를 켤 때 전시 목록과 상세를 미리 받아 둔다. 실패해도 서비스에는 영향이 없다."""
    api_key = settings.public_data_service_key
    if not api_key or settings.use_mock_places:
        return
    try:
        items = await BusanProvider()._fetch_culture_list(api_key)
        today = today_kst()
        ongoing = [item for item in items if is_ongoing(item, today) and in_busan(item["lat"], item["lng"])]
        await _detail_fetch_task([item["seq"] for item in ongoing], api_key)
        await rest_days.fetch([item["place"] for item in ongoing])
    except Exception as exc:  # noqa: BLE001 - 미리 받기는 실패해도 넘어간다
        logger.warning("전시 정보 미리 받기 실패: %s", type(exc).__name__)


def _phone(value) -> str | None:
    """'부산현대미술관 051-220-7400~1' → '051-220-7400~1'."""
    text = clean_text(value)
    match = re.search(r"\d{2,4}-\d{3,4}-\d{4}(?:~\d+)?", text)
    return match.group(0) if match else (text or None)


def _nearest(places: list[Place], anchor) -> list[Place]:
    """기준점이 있으면 가까운 순으로 자른다. 없으면 지역 필터를 파이프라인에 맡기고 그대로 둔다."""
    if anchor:
        places.sort(key=lambda p: p.distance_m if p.distance_m is not None else float("inf"))
        return places[:MAX_RESULTS]
    # 기준점이 없으면 순서 기준이 없어 원본 순서대로 자른다. 병합 단계가 모든 쌍을 비교하므로 상한을 둔다.
    return places[:MAX_UNANCHORED_RESULTS]
