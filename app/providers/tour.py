"""한국관광공사 TourAPI(KorService2) Provider.

관광·문화·체험 요청에만 쓴다. 음식점·카페·공공시설은 카카오·네이버가 더 정확해서 부르지 않는다.

호출 방식 (검색어에 따라 하나를 고른다)
- 일반 검색어('관광지', '명소', '가볼만한곳'처럼 장소 이름에 들어가지 않는 말)
  · 기준점이 있으면 위치기반 목록(locationBasedList2): 기준점 반경 안을 가까운 순으로
  · 기준점이 없으면 지역기반 목록(areaBasedList2): 부산(lDongRegnCd=26), 구·군을 말했으면 그 구·군만
  · 요청 종류에 맞는 콘텐츠 타입(관광지 12, 문화시설 14, 레포츠 28)으로 거른다.
- 그 밖의 검색어('해수욕장', '감천문화마을')는 키워드 검색(searchKeyword2): 부산 + 구·군
  · TourAPI 키워드 검색은 '부산 해수욕장'처럼 지역명을 붙이면 0건이라 검색어만 보낸다.

TourAPI 목록에는 홈페이지 주소가 없다. firstimage는 대표 사진이라 url이 아니라 image_url에 둔다.
"""

from __future__ import annotations

import re

import httpx

from app.config import settings
from app.providers.districts import district_code
from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.scoring import haversine_m

# TourAPI 콘텐츠 타입 ID → 원본 업종 이름 (scoring.CATEGORY_WORDS가 알아보는 단어로 둔다)
CONTENT_TYPES = {
    "12": "관광지", "14": "문화시설", "15": "축제공연행사", "25": "여행코스",
    "28": "레포츠", "32": "숙박", "38": "쇼핑", "39": "음식점",
}
# 요청 종류 → 목록 검색에 쓸 콘텐츠 타입
CATEGORY_CONTENT_TYPE = {"tourism": "12", "culture": "14", "activity": "28"}
# 이 종류는 TourAPI를 부르지 않는다 (카카오·네이버가 맡는다)
SKIP_CATEGORIES = {"restaurant", "cafe", "public_facility", "education"}
# TourAPI 옛 분류 cat3: 카페/전통찻집
CAFE_CAT3 = "A05020900"

# 장소 이름에 들어가지 않아 키워드 검색으로는 찾을 수 없는 일반 검색어
GENERIC_QUERIES = {
    "관광지", "관광명소", "명소", "관광", "볼거리", "여행지", "가볼만한곳", "가볼곳", "갈만한곳", "놀거리",
    "놀곳", "구경거리", "데이트", "데이트코스", "문화시설", "문화공간", "체험", "레포츠", "액티비티", "추천",
}
# 일반 검색어에서 떼어낼 말 ('부산 관광지 추천' → '관광지')
FILLER_WORDS = ("부산광역시", "부산시", "부산", "추천", "근처", "주변", "요즘", "인기", "유명한", "좋은")

NUM_ROWS = 30
from app.services.search_radius import TOUR_DEFAULT_RADIUS_M as DEFAULT_RADIUS_M  # noqa: E402
from app.services.search_radius import TOUR_MAX_RADIUS_M as MAX_RADIUS_M  # noqa: E402


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def core_query(query: str) -> str:
    """검색어에서 띄어 쓴 지역·꾸밈말을 뗀 핵심어. '부산 관광지 추천' → '관광지'.

    붙어 있는 이름은 그대로 둔다 ('부산시립미술관'의 '부산'은 이름의 일부다).
    """
    tokens = [t for t in (query or "").split() if t not in FILLER_WORDS]
    return "".join(tokens)


def is_generic(query: str) -> bool:
    core = core_query(query)
    if not core:
        return True
    stripped = re.sub(r"^(부산광역시|부산시|부산)", "", core)
    return core in GENERIC_QUERIES or stripped in GENERIC_QUERIES


def _to_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number or None


def source_category(item: dict) -> str:
    """콘텐츠 타입을 원본 업종 경로로. 음식점 중 카페는 '음식점 > 카페'."""
    type_id = str(item.get("contenttypeid") or "")
    name = CONTENT_TYPES.get(type_id, "관광정보")
    if type_id == "39" and item.get("cat3") == CAFE_CAT3:
        return "음식점 > 카페"
    return name


def parse_items(data: dict) -> list[dict]:
    """TourAPI 응답에서 항목 목록. 결과가 없으면 items가 빈 문자열로 온다."""
    header = (data.get("response") or {}).get("header") or {}
    code = str(header.get("resultCode") or "0000")
    if code not in {"0000", "00", "0"}:
        raise httpx.HTTPError(f"TourAPI {code} {header.get('resultMsg') or ''}".strip())
    try:
        items = data["response"]["body"]["items"]
    except (KeyError, TypeError):
        return []
    if not isinstance(items, dict):
        return []
    item = items.get("item") or []
    return [item] if isinstance(item, dict) else list(item)


def to_place(item: dict, request: PlaceRequest, anchor: tuple[float, float] | None, name: str) -> Place | None:
    title = str(item.get("title") or "").strip()
    lat, lng = _to_float(item.get("mapy")), _to_float(item.get("mapx"))
    if not title:
        return None
    type_id = str(item.get("contenttypeid") or "")
    distance = None
    if anchor and lat is not None and lng is not None:
        distance = round(haversine_m(anchor[0], anchor[1], lat, lng), 1)
    address = " ".join(str(item.get(k) or "").strip() for k in ("addr1", "addr2")).strip()
    return Place(
        id=f"tour_{item.get('contentid') or title}",
        name=title,
        category=request.category,
        address=address or None,
        latitude=lat,
        longitude=lng,
        distance_m=distance,
        phone=str(item.get("tel") or "").strip() or None,
        image_url=str(item.get("firstimage") or "").strip() or None,
        sources=[name],
        source_ids={name: str(item.get("contentid") or "")},
        source_categories={name: source_category(item)},
        source_category_codes={name: "/".join(c for c in (type_id, str(item.get("cat3") or "")) if c)},
    )


class TourProvider(PlaceProvider):
    name = "tour"
    # 위치기반 목록이 좌표 반경 검색을 지원한다. 키워드 검색 결과는 반경으로 다시 거른다.
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
            raise ProviderNotConfigured("TourAPI용 공공데이터포털 API 키가 설정되지 않았습니다.")
        if not settings.tour_api_url:
            raise ProviderNotConfigured("TOUR_API_URL이 설정되지 않았습니다.")
        if request.category in SKIP_CATEGORIES:
            return []

        base = settings.tour_api_url.rstrip("/")
        params: dict = {
            "serviceKey": api_key,
            "numOfRows": NUM_ROWS,
            "pageNo": 1,
            "MobileOS": "ETC",
            "MobileApp": "sw_team10",
            "_type": "json",
        }
        content_type = CATEGORY_CONTENT_TYPE.get(request.category or "")
        if is_generic(request.query):
            if content_type:
                params["contentTypeId"] = content_type
            if anchor:
                url = f"{base}/locationBasedList2"
                params.update(mapX=anchor[1], mapY=anchor[0], arrange="E",
                              radius=min(radius_m or DEFAULT_RADIUS_M, MAX_RADIUS_M))
            else:
                url = f"{base}/areaBasedList2"
                params.update(lDongRegnCd="26", arrange="Q")
                if code := district_code(location):
                    params["lDongSignguCd"] = code
        else:
            url = f"{base}/searchKeyword2"
            params.update(keyword=core_query(request.query) or request.query, lDongRegnCd="26")
            if not anchor and (code := district_code(location)):
                params["lDongSignguCd"] = code

        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        places = [p for item in parse_items(data) if (p := to_place(item, request, anchor, self.name))]
        if anchor and radius_m:
            places = [p for p in places if p.distance_m is not None and p.distance_m <= radius_m]
        return places
