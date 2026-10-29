import httpx
from app.config import settings
from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place

BUSAN_EXHIBITION_URL = "https://apis.data.go.kr/6260000/BusanExhibitionSpaceService/getBusanExhibitionSpaceList"

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
            "numOfRows": 20,
            "resultType": "json",
        }

        # 2. 비동기 HTTP 요청
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.get(BUSAN_EXHIBITION_URL, params=params)
            response.raise_for_status()
            data = response.json()

        # 3. 응답 데이터를 공통 Place 객체로 변환
        try:
            items = data["getBusanExhibitionSpaceList"]["item"]
        except (KeyError, TypeError):
            return []

        places: list[Place] = []
        query_text = request.query.lower()

        for item in items:
            title = item.get("title") or item.get("facilityName") or ""
            # 검색어가 지정되었을 때 이름이나 주소에 해당 키워드가 포함되어 있는지 확인
            if query_text and (query_text not in title.lower() and query_text not in item.get("addr", "").lower()):
                continue

            lat, lon = None, None
            if item.get("lat"):
                try:
                    lat = float(item["lat"])
                except ValueError:
                    pass
            if item.get("lng"):
                try:
                    lon = float(item["lng"])
                except ValueError:
                    pass

            place = Place(
                id=f"busan_{item.get('fcltyId') or title}",
                name=title,
                category=request.category or "전시공간",
                address=item.get("addr") or "",
                latitude=lat,
                longitude=lon,
                phone=item.get("tel") or None,
                url=item.get("homepage") or None,
                source="busan",
                source_categories=["문화시설", "전시공간"],
            )
            places.append(place)

        return places