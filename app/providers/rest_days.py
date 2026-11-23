"""전시장 정기 휴관일 (SRS FR-042 '오늘 관람할 수 있는 전시').

출처: 한국관광공사 TourAPI 문화시설(콘텐츠 타입 14) 소개정보의 쉬는 날(restdateculture).
- 전시장 이름으로 키워드 검색(searchKeyword2)해 같은 이름의 문화시설을 찾고, 소개정보(detailIntro2)의 쉬는 날을 읽는다.
- '매주 월요일', '월요일 휴관', '1월 1일' 같은 규칙만 읽는다. 임시 휴관(전시 교체·공사)은 API에 없어 알 수 없다.
- 월요일이 공휴일이면 문을 여는 곳이 많지만 공휴일 달력은 쓰지 않으므로, 그날은 휴관으로 잘못 볼 수 있다.
- 한 번 찾은 결과(못 찾은 것 포함)는 파일(.cache/rest_days.json)에 7일 보관해 서버를 다시 켜도 API를 다시 부르지 않는다.
- 요청 처리 중에는 저장된 결과만 쓰고, 없는 전시장은 뒤에서 받아 다음 요청부터 반영한다(응답 시간 NFR-001).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).resolve().parents[2] / ".cache" / "rest_days.json"
TTL_SECONDS = 7 * 24 * 3600
REQUEST_INTERVAL_SECONDS = 0.25
WEEKDAYS = "월화수목금토일"
# 2: '동래문화회관 전시실'처럼 전시실 이름이면 건물 이름으로도 찾는다. 예전 판(1)에서 못 찾은 이름은 다시 찾는다.
CACHE_VERSION = 2
_ROOM = re.compile(r"\s*(\(.*?\)|제?\d+\s*(전시실|층)|[대소]?전시실|전시관|전시장|갤러리|본관|신관|별관|기획전시실)\s*$")


def lookup_names(name: str) -> list[str]:
    """찾아볼 이름: 원래 이름, 그다음 전시실·층 같은 꼬리를 뗀 건물 이름 ('동래문화회관 전시실' → '동래문화회관')."""
    names = [name]
    base = name
    while True:
        trimmed = _ROOM.sub("", base).strip()
        if not trimmed or trimmed == base:
            break
        base = trimmed
        names.append(base)
    return list(dict.fromkeys(n for n in names if len(_norm(n)) >= 2))

_cache: dict[str, dict] | None = None
_task: asyncio.Task | None = None


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (name or "").lower())


def _load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save() -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(_load(), ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("휴관일 캐시 저장 실패: %s", exc)


def cached_rest(name: str) -> tuple[bool, str | None]:
    """(찾아본 적 있음, 쉬는 날 문장). 못 찾은 전시장은 (True, None)."""
    item = _load().get(_norm(name))
    if not item or time.time() - item.get("at", 0) > TTL_SECONDS:
        return False, None
    if item.get("rest") is None and item.get("v", 1) < CACHE_VERSION:
        return False, None  # 예전 방식으로 못 찾은 이름은 건물 이름으로 다시 찾아본다
    return True, item.get("rest") or None


def clean_rest(text: str | None) -> str | None:
    text = html.unescape(html.unescape(text or ""))
    text = re.sub(r"<br\s*/?>", " / ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip(" /")
    return text or None


def closed_on(rest: str | None, day: date) -> bool | None:
    """쉬는 날 문장으로 그날 정기 휴관인지. 문장이 없으면 None(모름)."""
    if not rest:
        return None
    weekday = WEEKDAYS[day.weekday()]
    closed_days: set[str] = set()
    # '매주 월요일', '매주 월·화요일', '매주 월, 화요일'
    for group in re.findall(r"매주\s*((?:[월화수목금토일](?:요일)?\s*[,·/및]?\s*)+)", rest):
        closed_days.update(ch for ch in group if ch in WEEKDAYS)
    # '월요일 휴관', '화요일 휴무' (단, '월요일이 공휴일인 경우'는 규칙이 아니라 예외라 읽지 않는다)
    closed_days.update(re.findall(r"([월화수목금토일])요일\s*(?:정기\s*)?(?:휴관|휴무|휴장)", rest))
    if weekday in closed_days:
        return True
    for month, dom in re.findall(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", rest):
        if (int(month), int(dom)) == (day.month, day.day):
            return True
    return False


async def _lookup_one(client: httpx.AsyncClient, base: str, api_key: str, name: str) -> str | None:
    common = {"serviceKey": api_key, "MobileOS": "ETC", "MobileApp": "sw_team10", "_type": "json"}
    response = await client.get(f"{base}/searchKeyword2", params={
        **common, "keyword": name, "contentTypeId": "14", "lDongRegnCd": "26", "numOfRows": 10, "pageNo": 1})
    response.raise_for_status()
    from app.providers.tour import parse_items  # 순환 참조 방지
    items = parse_items(response.json())
    target = _norm(name)
    match = next((i for i in items if _norm(str(i.get("title") or "")) == target), None) or \
        next((i for i in items if target and target in _norm(str(i.get("title") or ""))), None)
    if match is None:
        return None
    await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
    response = await client.get(f"{base}/detailIntro2", params={
        **common, "contentId": match.get("contentid"), "contentTypeId": "14"})
    response.raise_for_status()
    intro = parse_items(response.json())
    return clean_rest(intro[0].get("restdateculture")) if intro else None


async def fetch(names: list[str]) -> None:
    """아직 모르는 전시장의 쉬는 날을 하나씩 받아 파일에 저장한다. 실패한 이름은 저장하지 않아 다음에 다시 찾는다."""
    api_key, base = settings.public_data_service_key, (settings.tour_api_url or "").rstrip("/")
    todo = [n for n in dict.fromkeys(names) if n and not cached_rest(n)[0]]
    if not api_key or not base or not todo:
        return
    async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
        for index, name in enumerate(todo):
            if index:
                await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
            try:
                rest = None
                for candidate in lookup_names(name):
                    rest = await _lookup_one(client, base, api_key, candidate)
                    if rest:
                        break
                    await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.info("휴관일 조회 실패 %s: %s", name, type(exc).__name__)
                continue
            _load()[_norm(name)] = {"rest": rest, "at": time.time(), "v": CACHE_VERSION}
    _save()


def fetch_in_background(names: list[str]) -> None:
    """요청을 기다리게 하지 않고 뒤에서 받는다. 한 번에 하나만 돈다."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(fetch(names))
