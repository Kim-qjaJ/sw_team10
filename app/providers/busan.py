import math
import xml.etree.ElementTree as ET

import httpx
from app.config import settings
from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place

# 부산광역시 문화전시 장소 정보 서비스 (공공데이터포털).
# 기존 BusanExhibitionSpaceService 주소는 'NO_OPENAPI_SERVICE_ERROR(서비스가 없거나 폐기됨)'로 응답한다.
BUSAN_EXHIBITION_URL = "https://apis.data.go.kr/6260000/BusanCultureExhibitPlaceService/getBusanCultureExhibitPlace"

# 이 단어로 검색하면 이름 필터 없이 전시 장소 전체를 후보로 쓴다 (예: '서면 전시', '미술관 추천').
GENERIC_QUERIES = ("전시", "전시회", "전시장", "전시공간", "미술관", "갤러리", "박물관", "공연장", "문화", "문화시설")
MAX_RESULTS = 30
# 전시·문화 장소 데이터라 이 종류의 요청에서만 후보로 쓴다. (카페·음식점 검색에 섞이지 않게)
CULTURE_CATEGORIES = {"culture", "tourism", "activity"}


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _parse_items(response: httpx.Response) -> list[dict]:
    """JSON/XML 어느 형식으로 와도 장소 항목(placeNm이 있는 묶음)을 꺼낸다."""
    try:
        payload = response.json()
    except ValueError:
        items: list[dict] = []
        for element in ET.fromstring(response.text).iter():
            if element.tag.rsplit("}", 1)[-1] == "item":
                items.append({child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in element})
        return items

    items = []

    def walk(node):
        if isinstance(node, dict):
            if "placeNm" in node or "placeId" in node:
                items.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return items


class BusanProvider(PlaceProvider):
    name = "busan"
    supports_radius = False

    async def search(
        self,
        request: PlaceRequest,
        location: str | None = None,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
    ) -> list[Place]:
        # 1. API 키 확인
        api_key = settings.public_data_service_key
        if not api_key:
            raise ProviderNotConfigured("공공데이터포털 API 키가 설정되지 않았습니다.")

        params = {
            "serviceKey": api_key,
            "pageNo": 1,
            # 전체 데이터가 수백 건이라 한 번에 받아 로컬에서 거른다.
            "numOfRows": 999,
            "resultType": "json",
        }

        query_text = (request.query or "").strip().lower()
        if request.category not in CULTURE_CATEGORIES and not any(word in query_text for word in GENERIC_QUERIES):
            return []

        # 2. 비동기 HTTP 요청
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.get(BUSAN_EXHIBITION_URL, params=params)
            response.raise_for_status()
            items = _parse_items(response)

        # 3. 응답 데이터를 공통 Place 객체로 변환
        filter_by_name = bool(query_text) and not any(word in query_text for word in GENERIC_QUERIES)

        places: list[Place] = []
        for item in items:
            title = str(item.get("placeNm") or "").strip()
            if not title:
                continue
            address = str(item.get("addr") or "").strip()
            # 특정 이름 검색이면 이름이나 주소에 검색어가 포함된 곳만 남긴다.
            if filter_by_name and query_text not in title.lower() and query_text not in address.lower():
                continue

            lat, lon = _to_float(item.get("lttd")), _to_float(item.get("lngt"))
            distance = _distance_m(anchor[0], anchor[1], lat, lon) if anchor and lat is not None and lon is not None else None
            source_id = str(item.get("placeId") or title)
            places.append(Place(
                id=f"busan_{source_id}",
                name=title,
                category=request.category or "culture",
                subcategory=request.subcategory,
                address=address or None,
                latitude=lat,
                longitude=lon,
                distance_m=round(distance, 1) if distance is not None else None,
                sources=[self.name],
                source_ids={self.name: source_id},
                source_categories={self.name: "문화시설 > 전시공간"},
                busan_verified=True,
            ))

        # 기준점이 있으면 가까운 순으로 자른다.
        if anchor:
            places.sort(key=lambda p: p.distance_m if p.distance_m is not None else float("inf"))
        return places[:MAX_RESULTS]
