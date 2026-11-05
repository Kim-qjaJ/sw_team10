"""부산 Provider(부산명소정보·전시 목록) 테스트. 실제 API 없이 저장한 응답 형태로 확인한다.

python -m unittest tests.test_busan_provider
"""

import unittest
from datetime import date
from unittest.mock import patch

from app.providers import busan
from app.providers.busan import BusanProvider, is_ongoing, parse_date
from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.place_merger import PlaceMerger

ANCHOR = (35.0789, 129.0444)  # 영도 흰여울문화마을 근처

ATTRACTIONS = {"getAttractionKr": {"header": {"code": "00"}, "totalCount": 3, "item": [
    {"UC_SEQ": 1, "PLACE": "흰여울문화마을", "MAIN_TITLE": "영도 흰여울문화마을", "TITLE": "바다 옆 골목",
     "LAT": 35.0789, "LNG": 129.0444, "ADDR1": "부산광역시 영도구 영선동4가", "GUGUN_NM": "영도구",
     "CNTCT_TEL": "051-000-0000", "HOMEPAGE_URL": "https://example.test/1"},
    {"UC_SEQ": 2, "PLACE": "용두산공원", "MAIN_TITLE": "용두산공원", "TITLE": "부산타워 전망",
     "LAT": 35.1009, "LNG": 129.0325, "ADDR1": "부산광역시 중구 용두산길", "GUGUN_NM": "중구"},
    {"UC_SEQ": 3, "PLACE": "좌표없는명소", "MAIN_TITLE": "좌표없는명소", "LAT": "", "LNG": "", "ADDR1": "부산광역시 중구"},
]}}

EXHIBITS = {"getBusanCultureExhibit": {"body": {"totalCount": 5, "items": {"item": [
    {"res_no": "1", "title": "바다 사진전", "op_st_dt": "2026-11-01", "op_ed_dt": "2026-11-30", "op_at": "N",
     "place_nm": "부산시립미술관", "pay_at": "N"},
    {"res_no": "2", "title": "현대미술 소장품전", "op_st_dt": "20261001", "op_ed_dt": "20261231", "op_at": "N",
     "place_nm": "부산시립미술관", "pay_at": "Y"},
    {"res_no": "3", "title": "지난 전시", "op_st_dt": "2026-09-01", "op_ed_dt": "2026-10-01", "op_at": "N",
     "place_nm": "부산시립미술관", "pay_at": "N"},
    {"res_no": "4", "title": "상설 전시", "op_st_dt": "2020-01-01", "op_ed_dt": "", "op_at": "Y",
     "place_nm": "부산박물관", "pay_at": "N"},
    {"res_no": "5", "title": "서울 전시", "op_st_dt": "2026-11-01", "op_ed_dt": "2026-11-30", "op_at": "N",
     "place_nm": "부산 밖 갤러리", "pay_at": "N"},
]}}}}

KAKAO = {
    "부산 부산시립미술관": {"documents": [{"place_name": "부산시립미술관", "y": "35.1667", "x": "129.1370",
                                     "road_address_name": "부산 해운대구 APEC로 58", "place_url": "https://place.test/1"}]},
    "부산 부산박물관": {"documents": [{"place_name": "부산박물관", "y": "35.1290", "x": "129.0963",
                                  "road_address_name": "부산 남구 유엔평화로 63"}]},
    "부산 부산 밖 갤러리": {"documents": [{"place_name": "갤러리", "y": "37.5", "x": "127.0",
                                     "road_address_name": "서울 종로구"}]},
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        params = params or {}
        if "AttractionService" in url:
            FakeClient.calls.append("attraction")
            return FakeResponse(ATTRACTIONS)
        if "BusanCultureExhibitService" in url:
            FakeClient.calls.append("exhibit")
            return FakeResponse(EXHIBITS)
        FakeClient.calls.append(f"kakao:{params.get('query')}")
        return FakeResponse(KAKAO.get(params.get("query"), {"documents": []}))


class BusanProviderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        busan.clear_cache()
        FakeClient.calls = []
        self.patches = [
            patch.object(busan.httpx, "AsyncClient", FakeClient),
            patch.object(busan.settings, "public_data_api_key", "test-key"),
            patch.object(busan.settings, "kakao_rest_api_key", "kakao-key"),
            patch.object(busan, "today_kst", lambda: date(2026, 11, 5)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    async def test_tourism_request_uses_attractions(self):
        places = await BusanProvider().search(PlaceRequest(query="관광지", category="tourism"), anchor=ANCHOR)
        self.assertEqual([p.name for p in places], ["흰여울문화마을", "용두산공원"])  # 좌표 없는 명소 제외, 가까운 순
        first = places[0]
        self.assertTrue(first.busan_verified)
        self.assertEqual(first.sources, ["busan"])
        self.assertEqual(first.source_categories, {"busan": "관광명소"})
        self.assertEqual(first.distance_m, 0.0)
        self.assertEqual(FakeClient.calls, ["attraction"])

    async def test_attraction_name_and_subcategory_filters(self):
        provider = BusanProvider()
        named = await provider.search(PlaceRequest(query="흰여울", category="tourism"))
        self.assertEqual([p.name for p in named], ["흰여울문화마을"])
        parks = await provider.search(PlaceRequest(query="공원", category="tourism", subcategory="park"))
        self.assertEqual([p.name for p in parks], ["용두산공원"])

    async def test_attractions_are_cached(self):
        provider = BusanProvider()
        await provider.search(PlaceRequest(query="관광지", category="tourism"))
        await provider.search(PlaceRequest(query="명소", category="tourism"))
        self.assertEqual(FakeClient.calls.count("attraction"), 1)

    async def test_restaurant_and_cafe_requests_do_not_call_busan_api(self):
        for request in (PlaceRequest(query="카페", category="cafe"), PlaceRequest(query="공연장", category="culture")):
            self.assertEqual(await BusanProvider().search(request), [])
        self.assertEqual(FakeClient.calls, [])

    async def test_exhibits_only_ongoing_grouped_by_venue(self):
        places = await BusanProvider().search(PlaceRequest(query="전시", category="culture", subcategory="exhibition"))
        by_name = {p.name: p for p in places}
        # 부산 밖으로 좌표가 잡힌 전시장은 빠진다.
        self.assertEqual(set(by_name), {"부산시립미술관", "부산박물관"})
        museum = by_name["부산시립미술관"]
        self.assertEqual([e["title"] for e in museum.events], ["바다 사진전", "현대미술 소장품전"])  # 지난 전시 제외, 종료일 순
        self.assertEqual(museum.events[0], {"title": "바다 사진전", "start": "2026-11-01", "end": "2026-11-30",
                                            "open_run": False, "free": True})
        self.assertTrue(museum.indoor)
        self.assertTrue(museum.busan_verified)
        self.assertEqual(museum.latitude, 35.1667)
        self.assertTrue(by_name["부산박물관"].events[0]["open_run"])

    async def test_free_exhibits_only(self):
        places = await BusanProvider().search(PlaceRequest(query="무료 전시", category="culture", subcategory="exhibition"))
        titles = [e["title"] for p in places for e in p.events]
        self.assertIn("바다 사진전", titles)
        self.assertNotIn("현대미술 소장품전", titles)

    async def test_venue_lookup_is_cached(self):
        provider = BusanProvider()
        request = PlaceRequest(query="전시", category="culture", subcategory="exhibition")
        await provider.search(request)
        await provider.search(request)
        kakao_calls = [c for c in FakeClient.calls if c.startswith("kakao:")]
        self.assertEqual(len(kakao_calls), 3)  # 전시장 3곳을 한 번씩만 조회

    async def test_without_kakao_key_exhibits_are_skipped(self):
        with patch.object(busan.settings, "kakao_rest_api_key", None):
            places = await BusanProvider().search(PlaceRequest(query="미술관", category="culture", subcategory="gallery"))
        self.assertEqual(places, [])


class HelperTest(unittest.TestCase):
    def test_parse_date_formats(self):
        for value in ("2026-11-05", "20261105", "2026.11.05"):
            self.assertEqual(parse_date(value), date(2026, 11, 5))
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))

    def test_is_ongoing(self):
        today = date(2026, 11, 5)
        self.assertTrue(is_ongoing({"op_st_dt": "2026-11-01", "op_ed_dt": "2026-11-05"}, today))
        self.assertFalse(is_ongoing({"op_st_dt": "2026-11-06", "op_ed_dt": "2026-11-30"}, today))
        self.assertTrue(is_ongoing({"op_st_dt": "2020-01-01", "op_ed_dt": "", "op_at": "Y"}, today))
        self.assertFalse(is_ongoing({"op_st_dt": "", "op_ed_dt": ""}, today))

    def test_merger_keeps_events_from_busan(self):
        kakao = Place(name="부산시립미술관", latitude=35.1667, longitude=129.1370, sources=["kakao"],
                      source_ids={"kakao": "k1"})
        exhibit = Place(name="부산시립미술관", latitude=35.1667, longitude=129.1370, sources=["busan"],
                        source_ids={"busan": "부산시립미술관"}, busan_verified=True,
                        events=[{"title": "바다 사진전", "start": "2026-11-01", "end": "2026-11-30",
                                 "open_run": False, "free": True}])
        merged = PlaceMerger().merge([kakao, exhibit])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].events[0]["title"], "바다 사진전")
        self.assertTrue(merged[0].busan_verified)


if __name__ == "__main__":
    unittest.main()
