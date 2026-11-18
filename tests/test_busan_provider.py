"""부산 Provider(부산명소정보·문화정보 전시) 테스트. 실제 API 없이 저장한 응답 형태로 확인한다.

python -m unittest tests.test_busan_provider
"""

import unittest
from datetime import date
from unittest.mock import patch

from app.chat.intents import keep_free_condition
from app.providers import busan
from app.providers.busan import (
    GENERIC_ATTRACTION_QUERIES, BusanProvider, clean_text, is_free, is_ongoing, keywords, parse_date,
)
from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.hard_filter import HardFilter
from app.services.place_merger import PlaceMerger

ANCHOR = (35.0789, 129.0444)  # 영도 흰여울문화마을 근처

ATTRACTIONS = {"getAttractionKr": {"header": {"code": "00"}, "totalCount": 3, "item": [
    {"UC_SEQ": 1, "PLACE": "흰여울문화마을", "MAIN_TITLE": "영도 흰여울문화마을", "TITLE": "바다 옆 골목",
     "LAT": 35.0789, "LNG": 129.0444, "ADDR1": "부산광역시 영도구 영선동4가", "GUGUN_NM": "영도구",
     "CNTCT_TEL": "051-000-0000", "HOMEPAGE_URL": "https://example.test/1",
     "MAIN_IMG_NORMAL": "https://www.visitbusan.net/uploadImgs/files/cntnts/1.jpg"},
    {"UC_SEQ": 2, "PLACE": "용두산공원", "MAIN_TITLE": "용두산공원", "TITLE": "부산타워 전망",
     "LAT": 35.1009, "LNG": 129.0325, "ADDR1": "부산광역시 중구 용두산길", "GUGUN_NM": "중구"},
    {"UC_SEQ": 3, "PLACE": "좌표없는명소", "MAIN_TITLE": "좌표없는명소", "LAT": "", "LNG": "", "ADDR1": "부산광역시 중구"},
]}}

def _xml(items, total=None):
    body = "".join("<item>" + "".join(f"<{k}>{v}</{k}>" for k, v in item.items()) + "</item>" for item in items)
    count = f"<totalCount>{total}</totalCount>" if total is not None else ""
    return ('<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00</resultCode></header>'
            f"<body>{count}<items>{body}</items></body></response>")


def _culture(seq, title, start, end, place, lat="35.1667", lng="129.1370", realm="전시", service="전시", sigungu="해운대구"):
    return {"serviceName": service, "seq": seq, "title": title, "startDate": start, "endDate": end, "place": place,
            "realmName": realm, "area": "부산", "sigungu": sigungu, "gpsX": lng, "gpsY": lat}


# 한국문화정보원 문화정보 목록(지역=부산). 두 쪽으로 나눠 받는다.
CULTURE = [
    _culture("1", "바다 사진전", "20261101", "20261130", "부산시립미술관"),
    _culture("2", "현대미술 소장품전", "20261001", "20261231", "부산시립미술관"),
    _culture("3", "지난 전시", "20260901", "20261001", "부산시립미술관"),
    _culture("5", "서울 전시", "20261101", "20261130", "부산 밖 갤러리", lat="37.5", lng="127.0"),
    _culture("6", "다음 달 전시", "20261201", "20261231", "부산현대미술관", lat="35.1092", lng="128.9427"),
    _culture("7", "연극 한 편", "20261101", "20261130", "소극장", realm="연극", service="공연"),
    # 제목 기호가 두 번 바뀌어 들어온다.
    _culture("8", "&amp;lt;호랑이&amp;gt; 기획전", "20261101", "20261120", "부산박물관",
             lat="35.1290", lng="129.0963", sigungu="남구"),
    _culture("4", "상설 전시", "20200101", "20301231", "부산박물관", lat="35.1290", lng="129.0963", sigungu="남구"),
    _culture("9", "좌표 없는 전시", "20261101", "20261130", "어딘가 갤러리", lat="", lng=""),
]
DETAILS = {
    "1": {"seq": "1", "price": "무료", "placeAddr": "부산광역시 해운대구 APEC로 58", "phone": "부산시립미술관 051-744-2602",
          "placeUrl": "https://art.busan.go.kr"},
    "2": {"seq": "2", "price": "성인 5,000원", "placeAddr": "부산광역시 해운대구 APEC로 58"},
    "4": {"seq": "4", "price": "무료", "placeAddr": "부산광역시 남구 유엔평화로 63"},
    "8": {"seq": "8", "price": ""},
}


class FakeResponse:
    def __init__(self, payload=None, text=None):
        self.payload = payload
        self.text = text or ""

    def raise_for_status(self):
        return None

    def json(self):
        if self.payload is None:
            raise ValueError("not json")
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
        if url.endswith("/area2"):
            page = int(params.get("PageNo", 1))
            FakeClient.calls.append(f"list:{page}")
            chunk = CULTURE[:5] if page == 1 else CULTURE[5:]
            return FakeResponse(text=_xml(chunk, total=len(CULTURE)))
        if url.endswith("/detail2"):
            seq = params.get("seq")
            FakeClient.calls.append(f"detail:{seq}")
            return FakeResponse(text=_xml([DETAILS[seq]] if seq in DETAILS else []))
        raise AssertionError(f"unexpected url {url}")


class BusanProviderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        busan.clear_cache()
        FakeClient.calls = []
        self.patches = [
            patch.object(busan.httpx, "AsyncClient", FakeClient),
            patch.object(busan.settings, "public_data_api_key", "test-key"),
            patch.object(busan, "today_kst", lambda: date(2026, 11, 5)),
            patch.object(busan, "DETAIL_INTERVAL_SECONDS", 0),
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
        self.assertEqual(first.image_url, "https://www.visitbusan.net/uploadImgs/files/cntnts/1.jpg")
        self.assertIsNone(places[1].image_url)

    async def test_attraction_name_and_subcategory_filters(self):
        provider = BusanProvider()
        named = await provider.search(PlaceRequest(query="흰여울", category="tourism"))
        self.assertEqual([p.name for p in named], ["흰여울문화마을"])
        parks = await provider.search(PlaceRequest(query="공원", category="tourism", subcategory="park"))
        self.assertEqual([p.name for p in parks], ["용두산공원"])

    async def test_attraction_queries_with_region_or_filler_words(self):
        """검색어에 지역·꾸밈말이 붙어도 0건이 되지 않는다."""
        provider = BusanProvider()
        for query in ("부산 관광지", "관광지 추천", "가볼 만한 곳", "부산관광지"):
            places = await provider.search(PlaceRequest(query=query, category="tourism"))
            self.assertEqual(len(places), 2, query)
        # 구·군 이름은 주소로 확인한다.
        yeongdo = await provider.search(PlaceRequest(query="영도 관광지", category="tourism"))
        self.assertEqual([p.name for p in yeongdo], ["흰여울문화마을"])

    async def test_attractions_filtered_by_district(self):
        provider = BusanProvider()
        request = PlaceRequest(query="관광지", category="tourism")
        self.assertEqual([p.name for p in await provider.search(request, location="부산 중구")], ["용두산공원"])
        self.assertEqual([p.name for p in await provider.search(request, location="영도")], ["흰여울문화마을"])
        # 기준점이 있으면 거리로 고르므로 구·군으로 거르지 않는다.
        near = await provider.search(request, location="부산 중구", anchor=ANCHOR)
        self.assertEqual(len(near), 2)

    async def test_attractions_are_cached(self):
        provider = BusanProvider()
        await provider.search(PlaceRequest(query="관광지", category="tourism"))
        await provider.search(PlaceRequest(query="명소", category="tourism"))
        self.assertEqual(FakeClient.calls.count("attraction"), 1)

    async def test_attractions_filtered_by_radius(self):
        provider = BusanProvider()
        request = PlaceRequest(query="관광지", category="tourism")
        near = await provider.search(request, anchor=ANCHOR, radius_m=1500)
        self.assertEqual([p.name for p in near], ["흰여울문화마을"])
        wider = await provider.search(request, anchor=ANCHOR, radius_m=5000)
        self.assertEqual([p.name for p in wider], ["흰여울문화마을", "용두산공원"])
        self.assertTrue(BusanProvider.supports_radius)

    async def test_restaurant_cafe_and_venue_requests_do_not_call_busan_api(self):
        # 미술관·박물관·갤러리 같은 장소 찾기는 카카오·네이버가 맡는다.
        for request in (PlaceRequest(query="카페", category="cafe"), PlaceRequest(query="공연장", category="culture"),
                        PlaceRequest(query="미술관", category="culture", subcategory="gallery"),
                        PlaceRequest(query="박물관", category="culture", subcategory="museum")):
            self.assertEqual(await BusanProvider().search(request), [])
        self.assertEqual(FakeClient.calls, [])

    async def test_exhibits_only_ongoing_grouped_by_venue(self):
        places = await BusanProvider().search(PlaceRequest(query="전시", category="culture", subcategory="exhibition"))
        by_name = {p.name: p for p in places}
        # 지난 전시·시작 전 전시·공연·부산 밖 좌표·좌표 없는 전시는 빠진다.
        self.assertEqual(set(by_name), {"부산시립미술관", "부산박물관"})
        museum = by_name["부산시립미술관"]
        self.assertEqual([e["title"] for e in museum.events], ["바다 사진전", "현대미술 소장품전"])  # 종료일 순
        self.assertEqual(museum.events[0], {"title": "바다 사진전", "start": "2026-11-01", "end": "2026-11-30",
                                            "open_run": False, "free": True})
        self.assertFalse(museum.events[1]["free"])
        # 기호가 두 번 바뀐 제목도 원래 글자로 보이고, 가격이 비어 있으면 무료 여부는 모름.
        self.assertEqual([e["title"] for e in by_name["부산박물관"].events], ["<호랑이> 기획전", "상설 전시"])
        self.assertIsNone(by_name["부산박물관"].events[0]["free"])
        self.assertTrue(museum.indoor)
        self.assertTrue(museum.busan_verified)
        self.assertEqual((museum.latitude, museum.longitude), (35.1667, 129.1370))
        self.assertEqual(museum.address, "부산광역시 해운대구 APEC로 58")
        self.assertEqual(museum.phone, "051-744-2602")
        self.assertEqual(museum.url, "https://art.busan.go.kr")
        # 같은 전시장의 다른 전시 상세에 주소가 있으면 그 주소를 쓴다.
        self.assertEqual(by_name["부산박물관"].address, "부산광역시 남구 유엔평화로 63")

    async def test_free_exhibits_only(self):
        places = await BusanProvider().search(PlaceRequest(query="무료 전시", category="culture", subcategory="exhibition"))
        titles = [e["title"] for p in places for e in p.events]
        self.assertEqual(sorted(titles), ["바다 사진전", "상설 전시"])  # 유료·가격 모름은 뺀다

    async def test_exhibit_queries_with_filler_words(self):
        provider = BusanProvider()
        for query in ("무료 전시회", "요즘 전시", "부산 전시회", "전시 추천"):
            places = await provider.search(PlaceRequest(query=query, category="culture", subcategory="exhibition"))
            self.assertTrue(places, query)
        free = await provider.search(PlaceRequest(query="무료 전시회", category="culture"))
        titles = [e["title"] for p in free for e in p.events]
        self.assertIn("바다 사진전", titles)
        self.assertNotIn("현대미술 소장품전", titles)
        named = await provider.search(PlaceRequest(query="바다 사진 전시", category="culture"))
        self.assertEqual([e["title"] for p in named for e in p.events], ["바다 사진전"])

    async def test_list_timeout_is_not_retried_for_a_while(self):
        """전시 목록 서버가 멈추면 한 번 실패한 뒤에는 바로 건너뛴다 (요청마다 오래 기다리지 않게)."""
        calls = []

        class HangingClient(FakeClient):
            async def get(self, url, params=None, headers=None):
                calls.append(url)
                raise busan.httpx.ReadTimeout("no response")

        request = PlaceRequest(query="전시", category="culture", subcategory="exhibition")
        with patch.object(busan.httpx, "AsyncClient", HangingClient):
            for _ in range(3):
                with self.assertRaises(busan.httpx.TimeoutException):
                    await BusanProvider().search(request)
        self.assertEqual(len(calls), 1)

    async def test_list_pages_and_details_are_cached(self):
        provider = BusanProvider()
        request = PlaceRequest(query="전시", category="culture", subcategory="exhibition")
        await provider.search(request)
        await provider.search(request)
        self.assertEqual([c for c in FakeClient.calls if c.startswith("list:")], ["list:1", "list:2"])
        details = [c for c in FakeClient.calls if c.startswith("detail:")]
        self.assertEqual(sorted(details), ["detail:1", "detail:2", "detail:4", "detail:8"])  # 진행 중 전시만, 한 번씩

    async def test_failed_detail_is_retried_and_not_cached(self):
        """상세 조회가 한 번 실패해도 다시 시도하고, 계속 실패한 전시는 저장하지 않아 다음 요청 때 다시 부른다."""
        flaky = {"1": 1}

        class FlakyClient(FakeClient):
            async def get(self, url, params=None, headers=None):
                seq = (params or {}).get("seq")
                if url.endswith("/detail2") and flaky.get(seq, 0) > 0:
                    flaky[seq] -= 1
                    FakeClient.calls.append(f"detail-fail:{seq}")
                    raise busan.httpx.ReadTimeout("busy")
                if url.endswith("/detail2") and seq == "2":
                    FakeClient.calls.append("detail-fail:2")
                    raise busan.httpx.ReadTimeout("down")
                return await super().get(url, params, headers)

        request = PlaceRequest(query="전시", category="culture", subcategory="exhibition")
        with patch.object(busan.httpx, "AsyncClient", FlakyClient), patch.object(busan, "DETAIL_RETRY_DELAY_SECONDS", 0), \
                patch.object(busan, "DETAIL_INTERVAL_SECONDS", 0):
            places = await BusanProvider().search(request)
            events = {e["title"]: e for p in places for e in p.events}
            self.assertTrue(events["바다 사진전"]["free"])          # 재시도로 성공
            self.assertIsNone(events["현대미술 소장품전"]["free"])  # 두 번 다 실패 → 모름
            await BusanProvider().search(request)
        self.assertEqual(FakeClient.calls.count("detail-fail:2"), 6)  # 요청마다 3번 시도, 저장하지 않아 다음 요청 때 다시 시도
        self.assertEqual(FakeClient.calls.count("detail:1"), 1)

    async def test_exhibits_do_not_need_kakao_key(self):
        with patch.object(busan.settings, "kakao_rest_api_key", None):
            places = await BusanProvider().search(PlaceRequest(query="전시", category="culture", subcategory="exhibition"))
        self.assertTrue(places)


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
        # 상시 전시라도 종료일이 지났으면 제외, 시작 전이면 제외
        self.assertFalse(is_ongoing({"op_st_dt": "2020-01-01", "op_ed_dt": "2026-11-04", "op_at": "Y"}, today))
        self.assertFalse(is_ongoing({"op_st_dt": "2026-12-01", "op_ed_dt": "", "op_at": "Y"}, today))
        self.assertTrue(is_ongoing({"op_st_dt": "2020-01-01", "op_ed_dt": "2026-11-05", "op_at": "Y"}, today))

    def test_keywords(self):
        self.assertEqual(keywords("부산 관광지", GENERIC_ATTRACTION_QUERIES), [])
        self.assertEqual(keywords("가볼 만한 곳", GENERIC_ATTRACTION_QUERIES), [])
        self.assertEqual(keywords("해운대 관광지", GENERIC_ATTRACTION_QUERIES), ["해운대"])
        self.assertEqual(keywords("부산시립미술관 전시", {"전시", "미술관"}), ["부산시립"])

    def test_clean_text_and_is_free(self):
        self.assertEqual(clean_text("&amp;lt;푸른 심장&amp;gt;"), "<푸른 심장>")
        self.assertEqual(clean_text("뮤지엄&times;만나다"), "뮤지엄×만나다")
        self.assertTrue(is_free("무료"))
        self.assertFalse(is_free("성인 3,000원 / 어린이 무료"))
        self.assertIsNone(is_free(""))

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


    def test_exhibit_request_keeps_only_places_with_events(self):
        request = PlaceRequest(query="전시", category="culture", subcategory="exhibition")
        hall = Place(name="벡스코", category="culture", source_categories={"kakao": "문화,예술 > 전시관"})
        gallery = Place(name="갤러리 조이", category="culture", events=[{"title": "선으로 빚은 선"}],
                        source_categories={"busan": "문화시설 > 전시"})
        self.assertEqual([p.name for p in HardFilter().apply(request, [hall, gallery])], ["갤러리 조이"])
        # 진행 중인 전시를 못 찾았으면 장소라도 보여준다.
        self.assertEqual([p.name for p in HardFilter().apply(request, [hall])], ["벡스코"])
        # 미술관을 찾는 요청(전시 요청 아님)은 그대로 둔다.
        museum = PlaceRequest(query="미술관", category="culture")
        self.assertEqual(len(HardFilter().apply(museum, [hall, gallery])), 2)

    def test_free_word_survives_llm_query(self):
        """LLM이 '무료 전시'를 '전시'로 줄여도 무료 조건이 남는다."""
        parsed = UserIntent.model_validate({"intent": "get_event", "location": "부산", "requests": [
            {"query": "전시", "category": "culture", "subcategory": "exhibition"}]})
        self.assertEqual(keep_free_condition("부산 무료 전시 알려줘", parsed).requests[0].query, "무료 전시")
        self.assertEqual(keep_free_condition("부산 전시 알려줘", parsed).requests[0].query, "전시")
        cafe = UserIntent.model_validate({"intent": "recommend_place", "location": "서면", "requests": [
            {"query": "카페", "category": "cafe"}]})
        self.assertEqual(keep_free_condition("서면 무료 와이파이 카페", cafe).requests[0].query, "카페")


if __name__ == "__main__":
    unittest.main()
