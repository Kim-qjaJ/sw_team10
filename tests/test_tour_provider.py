"""TourAPI(KorService2) Provider 테스트. 실제 API 없이 저장한 응답 형태로 확인한다.

python -m unittest tests.test_tour_provider
"""

import unittest
from unittest.mock import patch

from app.providers import tour
from app.providers.tour import TourProvider, core_query, district_code, is_generic, parse_items, source_category
from app.schemas.intent import PlaceRequest

ANCHOR = (35.1587, 129.1604)  # 해운대해수욕장

ITEMS = [
    {"contentid": "126081", "contenttypeid": "12", "title": "해운대해수욕장", "addr1": "부산광역시 해운대구 우동",
     "mapx": "129.1604", "mapy": "35.1587", "tel": "", "firstimage": "http://tong.visitkorea.or.kr/a.jpg"},
    {"contentid": "2", "contenttypeid": "12", "title": "기장 먼곳", "addr1": "부산광역시 기장군",
     "mapx": "129.2200", "mapy": "35.2400", "firstimage": ""},
    {"contentid": "3", "contenttypeid": "39", "cat3": "A05020900", "title": "바다카페", "addr1": "부산광역시 해운대구",
     "mapx": "129.1610", "mapy": "35.1590"},
]


def payload(items):
    return {"response": {"header": {"resultCode": "0000", "resultMsg": "OK"},
                         "body": {"items": {"item": items} if items else "", "totalCount": len(items)}}}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeClient:
    calls: list[tuple[str, dict]] = []
    items = ITEMS

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        FakeClient.calls.append((url.rsplit("/", 1)[-1], dict(params or {})))
        return FakeResponse(payload(FakeClient.items))


class TourProviderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeClient.calls = []
        FakeClient.items = ITEMS
        self.patches = [
            patch.object(tour.httpx, "AsyncClient", FakeClient),
            patch.object(tour.settings, "public_data_api_key", "test-key"),
            patch.object(tour.settings, "tour_api_url", "https://apis.test/B551011/KorService2"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    async def test_generic_query_without_anchor_uses_area_list(self):
        await TourProvider().search(PlaceRequest(query="관광지", category="tourism"), location="부산 기장")
        endpoint, params = FakeClient.calls[0]
        self.assertEqual(endpoint, "areaBasedList2")
        self.assertEqual(params["lDongRegnCd"], "26")
        self.assertEqual(params["lDongSignguCd"], "710")
        self.assertEqual(params["contentTypeId"], "12")
        self.assertNotIn("keyword", params)

    async def test_generic_query_with_anchor_uses_location_list(self):
        places = await TourProvider().search(PlaceRequest(query="부산 관광지 추천", category="tourism"),
                                             location="해운대", anchor=ANCHOR)
        endpoint, params = FakeClient.calls[0]
        self.assertEqual(endpoint, "locationBasedList2")
        self.assertEqual((params["mapX"], params["mapY"]), (ANCHOR[1], ANCHOR[0]))
        self.assertEqual(params["radius"], tour.DEFAULT_RADIUS_M)
        self.assertEqual(places[0].distance_m, 0.0)

    async def test_specific_query_uses_keyword_without_region_words(self):
        await TourProvider().search(PlaceRequest(query="부산 해수욕장", category="tourism"), location="부산 해운대구")
        endpoint, params = FakeClient.calls[0]
        self.assertEqual(endpoint, "searchKeyword2")
        self.assertEqual(params["keyword"], "해수욕장")
        self.assertEqual(params["lDongSignguCd"], "350")
        self.assertNotIn("contentTypeId", params)

    async def test_radius_filters_keyword_results(self):
        places = await TourProvider().search(PlaceRequest(query="해수욕장", category="tourism"),
                                             anchor=ANCHOR, radius_m=1000)
        self.assertEqual({p.name for p in places}, {"해운대해수욕장", "바다카페"})

    async def test_place_fields(self):
        places = await TourProvider().search(PlaceRequest(query="해수욕장", category="tourism"))
        first = places[0]
        self.assertEqual(first.sources, ["tour"])
        self.assertEqual(first.source_ids, {"tour": "126081"})
        self.assertEqual(first.source_categories, {"tour": "관광지"})
        self.assertIsNone(first.url)  # 대표 사진은 장소 정보 링크가 아니다
        self.assertEqual(first.image_url, "http://tong.visitkorea.or.kr/a.jpg")
        self.assertIsNone(places[1].image_url)
        self.assertEqual(places[2].source_categories, {"tour": "음식점 > 카페"})

    async def test_restaurant_and_cafe_requests_do_not_call_tour(self):
        for request in (PlaceRequest(query="카페", category="cafe"), PlaceRequest(query="맛집", category="restaurant")):
            self.assertEqual(await TourProvider().search(request), [])
        self.assertEqual(FakeClient.calls, [])

    async def test_empty_result(self):
        FakeClient.items = []
        self.assertEqual(await TourProvider().search(PlaceRequest(query="없는곳", category="tourism")), [])


class HelperTest(unittest.TestCase):
    def test_core_query_and_generic(self):
        self.assertEqual(core_query("부산 해수욕장 추천"), "해수욕장")
        self.assertEqual(core_query("부산시립미술관"), "부산시립미술관")  # 붙은 이름은 그대로
        self.assertTrue(is_generic("관광지"))
        self.assertTrue(is_generic("부산 가볼만한곳"))
        self.assertTrue(is_generic("부산관광지"))
        self.assertFalse(is_generic("해수욕장"))
        self.assertFalse(is_generic("부산시립미술관"))

    def test_district_code(self):
        self.assertEqual(district_code("부산 해운대구"), "350")
        self.assertEqual(district_code("해운대"), "350")
        self.assertEqual(district_code("부산광역시 기장군"), "710")
        self.assertEqual(district_code("부산 중구"), "110")
        self.assertIsNone(district_code("부산 서면"))
        self.assertIsNone(district_code("부산"))
        self.assertIsNone(district_code(None))

    def test_parse_items(self):
        self.assertEqual(parse_items(payload([])), [])
        self.assertEqual(len(parse_items(payload(ITEMS[:1]))), 1)
        single = {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": ITEMS[0]}}}}
        self.assertEqual(parse_items(single)[0]["title"], "해운대해수욕장")
        with self.assertRaises(Exception):
            parse_items({"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED"}}})

    def test_source_category(self):
        self.assertEqual(source_category({"contenttypeid": "14"}), "문화시설")
        self.assertEqual(source_category({"contenttypeid": "39", "cat3": "A05020900"}), "음식점 > 카페")
        self.assertEqual(source_category({"contenttypeid": "99"}), "관광정보")


if __name__ == "__main__":
    unittest.main()


FESTIVALS = [
    {"contentid": "f1", "contenttypeid": "15", "title": "부산국제매직페스티벌", "addr1": "부산광역시 수영구",
     "mapx": "129.1180", "mapy": "35.1530", "eventstartdate": "20260628", "eventenddate": "20261226"},
    {"contentid": "f2", "contenttypeid": "15", "title": "부산 불꽃축제", "addr1": "부산광역시 수영구 광안해변로",
     "mapx": "129.1186", "mapy": "35.1532", "eventstartdate": "20261114", "eventenddate": "20261114"},   # 끝남
    {"contentid": "f3", "contenttypeid": "15", "title": "해운대 빛축제", "addr1": "부산광역시 해운대구",
     "mapx": "129.1604", "mapy": "35.1587", "eventstartdate": "20261128", "eventenddate": "20270131"},  # 아직 시작 전
    {"contentid": "f4", "contenttypeid": "15", "title": "날짜 없는 행사", "addr1": "부산광역시 중구",
     "mapx": "129.0300", "mapy": "35.1000"},
]


class FestivalTest(unittest.IsolatedAsyncioTestCase):
    """축제·행사 요청: 오늘 열리고 있는 것만 (2026-11-23 '끝난 축제가 나온다' 지적)."""

    def setUp(self):
        FakeClient.calls = []
        FakeClient.items = FESTIVALS
        self.patches = [patch.object(tour.httpx, "AsyncClient", FakeClient),
                        patch.object(tour.settings, "public_data_api_key", "test-key"),
                        patch.object(tour.settings, "tour_api_url", "https://example.test/KorService2"),
                        patch.object(tour, "today_kst", lambda: tour.date(2026, 11, 23))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        FakeClient.items = ITEMS

    async def test_only_ongoing_festivals_with_period(self):
        places = await TourProvider().search(PlaceRequest(query="진행중인 축제", category="culture"), location="부산")
        self.assertEqual(FakeClient.calls[-1][0], "searchFestival2")
        params = FakeClient.calls[-1][1]
        self.assertEqual(params["lDongRegnCd"], "26")
        self.assertLessEqual(params["eventStartDate"], "20260628")
        self.assertEqual([p.name for p in places], ["부산국제매직페스티벌"])
        self.assertEqual(places[0].events[0]["end"], "2026-12-26")
        self.assertEqual(places[0].events[0]["kind"], "festival")

    async def test_name_words_narrow_the_list(self):
        places = await TourProvider().search(PlaceRequest(query="매직 페스티벌", category="culture"), location="부산")
        self.assertEqual([p.name for p in places], ["부산국제매직페스티벌"])
        places = await TourProvider().search(PlaceRequest(query="불꽃 축제", category="culture"), location="부산")
        self.assertEqual(places, [])  # 끝난 불꽃축제는 이름이 맞아도 빼야 한다

    def test_festival_places_without_period_are_dropped(self):
        from app.schemas.place import Place
        from app.services.hard_filter import HardFilter

        req = PlaceRequest(query="축제", category="culture")
        stale = Place(name="부산 불꽃축제", category="culture", sources=["naver"], source_categories={"naver": "축제"})
        live = Place(name="부산국제매직페스티벌", category="culture", sources=["tour"],
                     events=[{"title": "부산국제매직페스티벌", "start": "2026-06-28", "end": "2026-12-26", "kind": "festival"}])
        self.assertEqual([p.name for p in HardFilter().apply(req, [stale, live])], ["부산국제매직페스티벌"])
        self.assertEqual(HardFilter().apply(req, [stale]), [])
