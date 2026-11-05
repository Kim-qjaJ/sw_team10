import html
import httpx
from app.config import settings
from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place

# .env의 NAVER_CLIENT_ID/SECRET은 네이버 클라우드(NCP) API Gateway 키이므로 NCP 지역 검색 주소를 쓴다.
NAVER_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/local"

class NaverProvider(PlaceProvider):
    name = "naver"
    supports_radius = False

    async def search(
        self,
        request: PlaceRequest,
        location: str | None = None,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
    ) -> list[Place]:
        client_id = settings.naver_client_id
        client_secret = settings.naver_client_secret
        if not client_id or not client_secret:
            raise ProviderNotConfigured("Naver API 키가 설정되지 않았습니다.")

        query_text = request.query
        if location and location not in query_text:
            query_text = f"{location} {query_text}"

        headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id.strip(),
            "X-NCP-APIGW-API-KEY": client_secret.strip(),
        }
        params = {
            "query": query_text,
            "display": 5,
            "sort": "random",
        }

        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.get(NAVER_SEARCH_URL, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

        items = data.get("items", [])
        places: list[Place] = []

        for item in items:
            clean_title = html.unescape(item.get("title", "")).replace("<b>", "").replace("</b>", "")
            raw_category = html.unescape(item.get("category", ""))
            source_cats = [c.strip() for c in raw_category.split(">") if c.strip()]

            lat, lon = None, None
            mapx = item.get("mapx")
            mapy = item.get("mapy")
            if mapx and mapy:
                try:
                    lon = float(mapx) / 10000000.0 if float(mapx) > 1000 else float(mapx)
                    lat = float(mapy) / 10000000.0 if float(mapy) > 1000 else float(mapy)
                except ValueError:
                    pass

            place = Place(
                id=f"naver_{item.get('link') or clean_title}",
                name=clean_title,
                category=request.category or (source_cats[-1] if source_cats else "장소"),
                address=item.get("roadAddress") or item.get("address") or "",
                latitude=lat,
                longitude=lon,
                phone=item.get("telephone") or None,
                url=item.get("link") or None,
                sources=[self.name],
                source_ids={self.name: f"{mapx}:{mapy}:{clean_title}"},
                source_categories={self.name: raw_category} if raw_category else {},
            )
            places.append(place)

        return places