import httpx
from app.config import settings
from app.providers.base import PlaceProvider, ProviderNotConfigured
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place

KAKAO_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"

class KakaoProvider(PlaceProvider):
    name = "kakao"
    supports_radius = True

    async def search(
        self,
        request: PlaceRequest,
        location: str | None = None,
        anchor: tuple[float, float] | None = None,
        radius_m: int | None = None,
    ) -> list[Place]:
        # 1. API 키 확인 (키가 없으면 ProviderNotConfigured 발생)
        api_key = settings.KAKAO_REST_API_KEY
        if not api_key:
            raise ProviderNotConfigured("Kakao API 키가 설정되지 않았습니다.")

        # 2. 검색 키워드(query) 조합
        query_text = request.query
        if location and location not in query_text:
            query_text = f"{location} {query_text}"

        headers = {"Authorization": f"KakaoAK {api_key}"}
        params: dict[str, str | int | float] = {
            "query": query_text,
            "size": 15,
        }

        # 기준 좌표(anchor) 및 반경(radius_m) 설정
        if anchor:
            params["x"] = anchor[1]  # 경도 (Longitude)
            params["y"] = anchor[0]  # 위도 (Latitude)
            if radius_m:
                params["radius"] = min(radius_m, 20000)

        # 3. 비동기 HTTP 요청
        async with httpx.AsyncClient(timeout=settings.PROVIDER_TIMEOUT_SECONDS) as client:
            try:
                response = await client.get(KAKAO_SEARCH_URL, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                # HTTP 오류나 네트워크 문제 발생 시 빈 리스트 반환
                return []

        # 4. 카카오 응답을 공통 Place 객체 리스트로 변환
        documents = data.get("documents", [])
        places: list[Place] = []

        for doc in documents:
            # 원본 카테고리 정보 보존
            category_name = doc.get("category_name", "")
            source_cats = [c.strip() for c in category_name.split(">") if c.strip()]
            category_group_code = doc.get("category_group_code")
            source_codes = [category_group_code] if category_group_code else []

            place = Place(
                id=f"kakao_{doc.get('id')}",
                name=doc.get("place_name", ""),
                category=request.category or (source_cats[-1] if source_cats else "장소"),
                address=doc.get("road_address_name") or doc.get("address_name") or "",
                latitude=float(doc["y"]) if doc.get("y") else None,
                longitude=float(doc["x"]) if doc.get("x") else None,
                phone=doc.get("phone") or None,
                url=doc.get("place_url") or None,
                source="kakao",
                source_categories=source_cats,
                source_category_codes=source_codes,
            )
            places.append(place)

        return places