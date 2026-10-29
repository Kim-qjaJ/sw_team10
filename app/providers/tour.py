import httpx
from app.config import settings
from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place


class TourProvider(PlaceProvider):
    name = "tour"
    supports_radius = False

    async def search(
        self,
        request: PlaceRequest,
        location: str | None = None,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
    ) -> list[Place]:
        # 1. API 키 및 URL 확인
        api_key = settings.public_data_service_key
        if not api_key:
            raise ProviderNotConfigured("TourAPI용 공공데이터포털 API 키가 설정되지 않았습니다.")
        if not settings.tour_api_url:
            raise ProviderNotConfigured("TOUR_API_URL이 설정되지 않았습니다.")
        tour_search_url = f"{settings.tour_api_url.rstrip('/')}/searchKeyword2"

        # 2. 검색 키워드 조합
        query_text = request.query
        if location and location not in query_text:
            query_text = f"{location} {query_text}"

        params = {
            "serviceKey": api_key,
            "numOfRows": 15,
            "pageNo": 1,
            "MobileOS": "ETC",
            "MobileApp": "sw_team10",
            "_type": "json",
            "keyword": query_text,
            "areaCode": "6",  # 부산 지역코드
        }

        # 3. 비동기 HTTP 요청
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.get(tour_search_url, params=params)
            response.raise_for_status()
            data = response.json()

        # 4. TourAPI 응답을 공통 Place 객체 리스트로 변환
        try:
            items = data["response"]["body"]["items"]["item"]
            if isinstance(items, dict):
                items = [items]
        except (KeyError, TypeError):
            return []

        places: list[Place] = []

        for item in items:
            title = item.get("title") or ""
            lat, lon = None, None
            if item.get("mapy"):
                try:
                    lat = float(item["mapy"])
                except ValueError:
                    pass
            if item.get("mapx"):
                try:
                    lon = float(item["mapx"])
                except ValueError:
                    pass

            place = Place(
                id=f"tour_{item.get('contentid') or title}",
                name=title,
                category=request.category or "관광지",
                address=item.get("addr1") or item.get("addr2") or "",
                latitude=lat,
                longitude=lon,
                phone=item.get("tel") or None,
                url=item.get("firstimage") or None,
                source="tour",
                source_categories=["관광지"],
            )
            places.append(place)

        return places