"""추천 알고리즘 단위 테스트: python -m unittest tests.test_recommendation"""
import random
import time
import unittest
from unittest import mock

from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.hard_filter import HardFilter
from app.services.recommendation import POINT_RADIUS_M, RecommendationService
from app.services.scoring import (
    WEIGHTS, access_score, busan_score, category_path, category_score, condition_score, cross_score, infer_categories,
    infer_indoor, is_amenity, weather_score, weights_for, NEAR_M, RAIN_WEATHER_WEIGHT,
)

ANCHOR = (35.1577, 129.0596)  # 서면역 근처
REQ = PlaceRequest(query="카페", category="cafe")
INTENT = UserIntent(intent="recommend_place", requests=[REQ])


def place(name, dlat=0.0, **kw):
    return Place(name=name, latitude=ANCHOR[0] + dlat, longitude=ANCHOR[1], **kw)


class RankTest(unittest.TestCase):
    def test_radius_filters_far_places(self):
        self.assertEqual(POINT_RADIUS_M, 1500)
        near, far = place("near", 0.005), place("far", 0.03)  # ~550m / ~3.3km
        result = RecommendationService().rank(INTENT, REQ, [near, far], anchor=ANCHOR)
        self.assertEqual([p.name for p in result], ["near"])

    def test_closer_and_matching_category_score_higher(self):
        a = place("A", 0.001, source_categories={"kakao": "음식점 > 카페"})
        b = place("B", 0.010)  # ~1.1km, 기본 반경 안
        result = RecommendationService().rank(INTENT, REQ, [b, a], anchor=ANCHOR)
        self.assertEqual(result[0].name, "A")
        self.assertGreater(result[0].score, result[1].score)
        self.assertTrue(result[0].score_breakdown["access"]["available"])

    def test_sort_by_distance(self):
        far, near = place("far", 0.015), place("near", 0.001)
        result = RecommendationService().rank(INTENT, REQ, [far, near], anchor=ANCHOR, sort_by_distance=True)
        self.assertEqual(result[0].name, "near")

    def test_limit(self):
        places = [place(str(i), 0.001 * i) for i in range(10)]
        self.assertEqual(len(RecommendationService().rank(INTENT, REQ, places, limit=3, anchor=ANCHOR)), 3)


class SearchBaselineTest(unittest.TestCase):
    """추천은 '그냥 검색'(카카오·네이버 순서)보다 나빠지면 안 된다 (2026-11 검색 대비 비교 28개 질문)."""

    def test_near_places_get_same_access(self):
        near = access_score(None, 150, 1500, None, None, None)[0]
        self.assertEqual(near, access_score(None, NEAR_M, 1500, None, None, None)[0])
        self.assertGreater(near, access_score(None, NEAR_M + 300, 1500, None, None, None)[0])

    def test_top_search_result_not_pushed_down_by_small_distance(self):
        # 같은 카페 업종이면 '바로 근처' 안의 거리 차이보다 카카오 1위가 앞선다.
        cafe = {"kakao": "음식점 > 카페"}
        top = place("카카오 1위", 380 / 111_000, sources=["kakao"], source_categories=cafe, popularity_signal=1.0)
        low = place("카카오 12위", 100 / 111_000, sources=["kakao"], source_categories=cafe, popularity_signal=0.27)
        result = RecommendationService().rank(INTENT, REQ, [low, top], anchor=ANCHOR)
        self.assertEqual(result[0].name, "카카오 1위")
        self.assertIn("검색 순위", result[0].score_breakdown["cross"]["reason"])

    def test_meal_search_scores_bar_lower(self):
        bar = {"naver": "술집>요리주점"}
        meal = {"kakao": "음식점 > 한식 > 육류,고기 > 닭요리"}
        self.assertEqual(category_score("restaurant", None, "맛집", bar, None)[0], 0.4)
        self.assertGreater(category_score("restaurant", None, "맛집", meal, None)[0], 0.4)
        # 술집을 찾을 때는 낮추지 않는다.
        self.assertGreater(category_score("restaurant", None, "술집", bar, None)[0], 0.4)

    def test_generic_meal_query_ignores_shop_name_in_path(self):
        # 실제 Kakao 경로: 업종 끝에 가게 이름이 붙는다. 이름에 '식당'이 있다고 더 맞는 식당은 아니다.
        named = {"kakao": "음식점 > 일식 > 후라토식당"}
        plain = {"kakao": "음식점 > 한식 > 육류,고기 > 갈비"}
        self.assertEqual(category_score("restaurant", None, "식당", named, None),
                         category_score("restaurant", None, "식당", plain, None))
        # 구체적인 검색어는 지금처럼 업종 일치를 가산한다.
        self.assertGreater(category_score("restaurant", None, "갈비", plain, None)[0],
                           category_score("restaurant", None, "갈비", named, None)[0])


class ScoringTest(unittest.TestCase):
    def test_weights_sum_to_100(self):
        self.assertEqual(sum(WEIGHTS.values()), 100)

    def test_category(self):
        exact = category_score("cafe", None, "카페", {"k": "음식점 > 카페"}, None)
        other = category_score("cafe", None, "카페", {"k": "음식점 > 한식"}, None)
        self.assertGreater(exact[0], other[0])
        self.assertIsNone(category_score("cafe", None, "카페", {}, None))

    def test_access_by_mode(self):
        walk = access_score("walk", 3000, 1000, None, None, None)[0]
        car = access_score("car", 3000, 1000, None, None, None)[0]
        self.assertGreater(car, walk)
        self.assertIsNone(access_score(None, None, 1000, None, None, None))
        by_minutes = access_score("walk", None, 1000, None, None, None, {"walk": 5})
        self.assertIsNotNone(by_minutes)

    def test_transit_uses_station(self):
        near = access_score("transit", 800, 1000, 35.1577, 129.0596, [("서면", 35.1578, 129.0597)])[0]
        far = access_score("transit", 800, 1000, 35.1577, 129.0596, [("서면", 35.2, 129.1)])[0]
        self.assertGreater(near, far)

    def test_condition(self):
        self.assertIsNone(condition_score(None, None, True, ""))
        self.assertEqual(condition_score(None, True, False, "")[0], 0.0)

    def test_condition_differs_per_place(self):
        # 같은 '음식점' 요청이라도 가족이면 술집은 낮고, 친구면 술집이 높다
        pub = "음식점 > 술집 > 호프,요리주점"
        kids = "음식점 > 패밀리레스토랑 > 키즈"
        plain = "음식점 > 한식 > 국밥"
        self.assertGreater(condition_score("family", None, None, kids)[0], condition_score("family", None, None, plain)[0])
        self.assertGreater(condition_score("family", None, None, plain)[0], condition_score("family", None, None, pub)[0])
        self.assertGreater(condition_score("friend", None, None, pub)[0], condition_score("friend", None, None, plain)[0])

    def test_infer_indoor(self):
        self.assertFalse(infer_indoor("여행 > 관광,명소 > 해수욕장"))
        self.assertFalse(infer_indoor("여행 > 관광,명소 > 산"))
        self.assertTrue(infer_indoor("문화,예술 > 문화시설 > 미술관"))
        self.assertTrue(infer_indoor("음식점 > 카페 > 커피전문점"))
        self.assertIsNone(infer_indoor("가정,생활 > 수산물"))  # '산'이 들어가도 산으로 보지 않는다
        self.assertIsNone(infer_indoor(""))

    def test_category_path(self):
        self.assertEqual(category_path({"kakao": "음식점 > 카페"}, "디저트"), "음식점 > 카페 > 디저트")

    def test_public_facility_word_not_too_broad(self):
        history = category_score("public_facility", None, "공공", {"k": "문화,예술 > 역사관"}, None)
        self.assertLess(history[0], 0.75)

    def test_weights_for_weather(self):
        self.assertEqual(weights_for(None), WEIGHTS)
        self.assertEqual(weights_for({"is_raining": False}), WEIGHTS)
        self.assertEqual(weights_for({"is_raining": True})["weather"], RAIN_WEATHER_WEIGHT)
        self.assertEqual(WEIGHTS["weather"], 10)  # 기본 배점은 바꾸지 않는다

    def test_weather(self):
        self.assertGreater(weather_score(True, {"is_raining": True})[0], weather_score(False, {"is_raining": True})[0])
        self.assertIsNone(weather_score(True, None))

    def test_cross_and_busan(self):
        self.assertGreater(cross_score(["kakao", "naver"])[0], cross_score(["kakao"])[0])
        self.assertIsNone(cross_score([]))
        self.assertGreater(busan_score(True, None)[0], busan_score(False, "unverified")[0])
        self.assertIsNone(busan_score(False, None))

    def test_breakdown_sums_to_score_scale(self):
        req = PlaceRequest(query="카페", category="cafe")
        p = place("A", 0.001, source_categories={"k": "카페"}, sources=["kakao", "naver"], busan_verified=True)
        out = RecommendationService().rank(UserIntent(intent="recommend_place", requests=[req]), req, [p], anchor=ANCHOR)[0]
        self.assertLessEqual(out.score, 100)
        self.assertEqual(set(out.score_breakdown), set(WEIGHTS))
        self.assertGreater(out.data_confidence, 0)


class ConfidenceTest(unittest.TestCase):
    def test_missing_data_does_not_outrank(self):
        # 같은 거리·종류인데 B만 교차정보·부산 데이터가 있으면 B가 위
        a = place("A", 0.001, category="cafe")
        b = place("B", 0.001, category="cafe", sources=["kakao", "naver"], busan_verified=True)
        result = RecommendationService().rank(INTENT, REQ, [a, b], anchor=ANCHOR)
        self.assertEqual(result[0].name, "B")
        self.assertGreater(result[0].rank_score, result[1].rank_score)


class RealCategoryRankTest(unittest.TestCase):
    """Kakao 업종 경로 형태의 데이터로 사용자 조건·날씨가 순위를 가르는지 확인한다."""

    def test_rain_prefers_indoor_by_category(self):
        req = PlaceRequest(query="관광지", category="tourism")
        intent = UserIntent(intent="recommend_place", requests=[req])
        beach = place("해변", 0.001, source_categories={"kakao": "여행 > 관광,명소 > 해수욕장"})
        aquarium = place("아쿠아리움", 0.001, source_categories={"kakao": "여행 > 관광,명소 > 아쿠아리움"})
        result = RecommendationService().rank(intent, req, [beach, aquarium], anchor=ANCHOR, weather={"is_raining": True})
        self.assertEqual(result[0].name, "아쿠아리움")
        self.assertIn("추정", result[0].score_breakdown["weather"]["reason"])
        self.assertIsNone(result[0].indoor)  # 원본 Place.indoor는 바꾸지 않는다

    def test_rain_beats_nearby_beach(self):
        # 2026-11 실제 '해운대 관광 + 비': 기준점 바로 앞 해수욕장이 아쿠아리움보다 위에 남던 경우
        req = PlaceRequest(query="관광명소", category="tourism")
        intent = UserIntent(intent="recommend_place", requests=[req])
        beach = place("해수욕장", 0.0, source_categories={"kakao": "여행 > 관광,명소 > 해수욕장,해변"})
        aquarium = place("아쿠아리움", 0.004, source_categories={"kakao": "문화,예술 > 문화시설 > 아쿠아리움"})
        rain = RecommendationService().rank(intent, req, [beach, aquarium], anchor=ANCHOR, weather={"is_raining": True})
        clear = RecommendationService().rank(intent, req, [beach, aquarium], anchor=ANCHOR, weather={"is_raining": False})
        self.assertEqual(rain[0].name, "아쿠아리움")
        self.assertEqual(clear[0].name, "해수욕장")
        self.assertEqual(rain[0].score_breakdown["weather"]["max"], RAIN_WEATHER_WEIGHT)

    def test_family_avoids_pub(self):
        req = PlaceRequest(query="맛집", category="restaurant")
        intent = UserIntent(intent="recommend_place", requests=[req], companion="family")
        pub = place("포차", 0.001, source_categories={"kakao": "음식점 > 술집 > 포장마차"})
        kids = place("키즈", 0.001, source_categories={"kakao": "음식점 > 패밀리레스토랑 > 키즈"})
        result = RecommendationService().rank(intent, req, [pub, kids], anchor=ANCHOR)
        self.assertEqual(result[0].name, "키즈")


class FallbackTest(unittest.TestCase):
    """AI-REQ-05: 점수 계산이 실패하면 기본 순위(인기 → 거리)로 상위 N개를 반환한다."""

    def test_fallback_on_scoring_error(self):
        places = [
            place("far-popular", 0.010, popularity_signal=0.9),
            place("near-unknown", 0.001),
            place("near-popular", 0.002, popularity_signal=0.5),
            place("outside", 0.030, popularity_signal=1.0),  # 기본 반경(1.5km) 밖
        ]
        with mock.patch("app.services.recommendation.category_score", side_effect=RuntimeError("boom")),                 self.assertLogs("app.services.recommendation", level="ERROR"):
            result = RecommendationService().rank(INTENT, REQ, places, limit=3, anchor=ANCHOR)
        self.assertEqual([p.name for p in result], ["far-popular", "near-popular", "near-unknown"])
        self.assertIn("fallback", result[0].score_breakdown)
        self.assertEqual(result[0].score, 0.0)

    def test_fallback_does_not_modify_input(self):
        original = place("a", 0.001)
        with mock.patch("app.services.recommendation.category_score", side_effect=RuntimeError),                 self.assertLogs("app.services.recommendation", level="ERROR"):
            RecommendationService().rank(INTENT, REQ, [original], anchor=ANCHOR)
        self.assertEqual(original.score_breakdown, {})
        self.assertIsNone(original.distance_m)


class ConsistencyTest(unittest.TestCase):
    """NFR-007: 같은 입력이면 항상 같은 순위."""

    def _places(self):
        rng = random.Random(7)
        paths = ["음식점 > 카페", "음식점 > 한식", "여행 > 관광,명소 > 해수욕장", "문화,예술 > 미술관"]
        return [place(f"p{i}", rng.uniform(-0.015, 0.015), source_categories={"kakao": rng.choice(paths)},
                      sources=rng.choice([["kakao"], ["kakao", "naver"]]), busan_verified=rng.random() < 0.3)
                for i in range(30)]

    def test_same_input_same_order(self):
        intent = UserIntent(intent="recommend_place", requests=[REQ], companion="friend", transport_mode="walk")
        runs = [[p.name for p in RecommendationService().rank(intent, REQ, self._places(), limit=10, anchor=ANCHOR,
                                                              weather={"is_raining": True})] for _ in range(5)]
        self.assertTrue(all(r == runs[0] for r in runs))

    def test_ties_keep_input_order(self):
        same = [place(name, 0.001, category="cafe") for name in ("first", "second", "third")]
        self.assertEqual([p.name for p in RecommendationService().rank(INTENT, REQ, same, anchor=ANCHOR)],
                         ["first", "second", "third"])


class PerformanceTest(unittest.TestCase):
    """NFR-006: 후보 50개의 순위 계산은 100ms 이내."""

    def test_rank_50_under_100ms(self):
        places = ConsistencyTest()._places() + ConsistencyTest()._places()[:20]
        stations = [("서면", 35.1578, 129.0597), ("전포", 35.1530, 129.0650), ("부전", 35.1620, 129.0600)]
        service = RecommendationService()
        runs = []
        for _ in range(20):
            started = time.perf_counter()
            service.rank(INTENT, REQ, places, limit=5, anchor=ANCHOR, stations=stations, weather={"is_raining": False})
            runs.append((time.perf_counter() - started) * 1000)
        self.assertEqual(len(places), 50)
        self.assertLess(sum(runs) / len(runs), 100)


class RealProviderCategoryTest(unittest.TestCase):
    """실제 Provider는 Place.category에 요청 종류를 그대로 넣는다. 원본 업종으로 종류를 판단하는지 확인한다."""

    def test_infer_categories_uses_deepest_segment(self):
        self.assertEqual(infer_categories({"kakao": "음식점 > 카페 > 커피전문점"}), {"cafe"})
        self.assertEqual(infer_categories({"kakao": "음식점 > 한식 > 국밥"}), {"restaurant"})
        self.assertEqual(infer_categories({"naver": "한식>국밥"}), {"restaurant"})
        self.assertEqual(infer_categories({"naver": "카페,디저트"}), {"cafe"})
        self.assertEqual(infer_categories({"kakao": "교통,수송 > 지하철,전철 > 부산1호선"}), {"public_facility"})
        self.assertEqual(infer_categories({"busan": "문화시설 > 전시공간"}), {"culture"})
        self.assertEqual(infer_categories({"kakao": "가정,생활 > 생활용품"}), set())
        self.assertEqual(infer_categories({}), set())

    def test_category_score_ignores_echoed_category(self):
        # 카페를 요청했는데 Kakao가 국밥집을 주고 category='cafe'로 채운 경우
        wrong = category_score("cafe", None, "카페", {"kakao": "음식점 > 한식 > 국밥"}, "cafe")
        right = category_score("cafe", None, "카페", {"kakao": "음식점 > 카페 > 커피전문점"}, "cafe")
        unknown = category_score("cafe", None, "카페", {"kakao": "가정,생활 > 생활용품"}, "cafe")
        self.assertEqual(wrong[0], 0.1)
        self.assertIn("국밥", wrong[1])
        self.assertGreaterEqual(right[0], 0.75)
        self.assertEqual(unknown[0], 0.5)

    def test_restaurant_request_does_not_count_cafe(self):
        # Kakao는 카페를 '음식점 > 카페' 아래에 둔다. 맛집 요청에서 카페는 다른 종류로 본다.
        self.assertEqual(category_score("restaurant", None, "맛집", {"kakao": "음식점 > 카페 > 커피전문점"}, "restaurant")[0], 0.1)

    def test_hard_filter_uses_source_categories(self):
        req = PlaceRequest(query="카페", category="cafe")
        places = [
            Place(name="커피", category="cafe", source_categories={"kakao": "음식점 > 카페 > 커피전문점"}),
            Place(name="국밥", category="cafe", source_categories={"kakao": "음식점 > 한식 > 국밥"}),
            Place(name="생활용품", category="cafe", source_categories={"kakao": "가정,생활 > 생활용품"}),
            Place(name="디저트", category="cafe", source_categories={"naver": "카페,디저트"}),
            Place(name="원본없음", category="cafe"),
        ]
        self.assertEqual([p.name for p in HardFilter().apply(req, places)], ["커피", "생활용품", "디저트", "원본없음"])

    def test_rank_with_echoed_category(self):
        coffee = place("커피", 0.003, category="cafe", source_categories={"kakao": "음식점 > 카페 > 커피전문점"})
        gukbap = place("국밥", 0.001, category="cafe", source_categories={"kakao": "음식점 > 한식 > 국밥"})
        result = RecommendationService().rank(INTENT, REQ, [gukbap, coffee], anchor=ANCHOR)
        self.assertEqual(result[0].name, "커피")  # 국밥집이 더 가까워도 카페가 위


class RealPathKeywordTest(unittest.TestCase):
    """2026-11 실제 Kakao·Naver 업종 경로로 키워드 표를 보완한 결과를 고정한다."""

    def _kept(self, category, paths):
        req = PlaceRequest(query="q", category=category)
        places = [Place(name=name, category=category, source_categories=src) for name, src in paths]
        return [p.name for p in HardFilter().apply(req, places)]

    def test_tourism_keeps_related_and_drops_amenities(self):
        kept = self._kept("tourism", [
            ("아쿠아리움", {"naver": "관람,체험>아쿠아리움"}),
            ("해양박물관", {"naver": "여행,명소>박물관"}),
            ("케이블카", {"naver": "레저,테마>케이블카"}),
            ("국제시장", {"kakao": "가정,생활 > 시장"}),
            ("용궁사", {"naver": "불교>절,사찰"}),
            ("공영주차장", {"kakao": "교통,수송 > 교통시설 > 주차장 > 공영주차장"}),
            ("호텔", {"kakao": "여행 > 숙박 > 호텔"}),
            ("국밥집", {"kakao": "음식점 > 한식 > 국밥"}),
        ])
        self.assertEqual(kept, ["아쿠아리움", "해양박물관", "케이블카", "국제시장", "용궁사"])

    def test_activity_keeps_theme_park_and_noraebang(self):
        kept = self._kept("activity", [
            ("롯데월드", {"kakao": "여행 > 관광,명소 > 테마파크"}),
            ("씨라이프", {"kakao": "문화,예술 > 문화시설 > 아쿠아리움"}),
            ("코인노래방", {"kakao": "가정,생활 > 여가시설 > 노래방 > 코인노래방"}),
            ("찜질방", {"naver": "생활,편의>찜질방"}),
            ("ATM", {"kakao": "금융,보험 > 금융서비스 > 은행 > ATM"}),
        ])
        self.assertEqual(kept, ["롯데월드", "씨라이프", "코인노래방", "찜질방"])

    def test_escape_room_theme_cafe_kept_for_activity(self):
        # '방탈출'은 activity로 해석되는데 Kakao는 방탈출 카페를 테마카페로 분류한다
        kept = self._kept("activity", [("비트포비아", {"kakao": "음식점 > 카페 > 테마카페"}),
                                       ("디저트", {"kakao": "음식점 > 카페 > 테마카페 > 디저트카페"})])
        self.assertEqual(kept, ["비트포비아"])
        self.assertEqual(self._kept("cafe", [("비트포비아", {"kakao": "음식점 > 카페 > 테마카페"})]), ["비트포비아"])

    def test_culture_drops_car_showroom(self):
        # '전시,판매'의 '전시' 때문에 문화로 잡히던 자동차 전시장
        self.assertEqual(self._kept("culture", [("BMW", {"naver": "자동차>전시,판매"})]), [])

    def test_public_facility_keeps_station_and_parking(self):
        kept = self._kept("public_facility", [
            ("서면역", {"kakao": "교통,수송 > 지하철,전철 > 부산1호선"}),
            ("공영주차장", {"kakao": "교통,수송 > 교통시설 > 주차장 > 공영주차장"}),
        ])
        self.assertEqual(kept, ["서면역", "공영주차장"])

    def test_tourism_drops_restroom_and_intersection(self):
        # 2026-11 '해운대 해수욕장' 실제 결과에 섞였던 공중화장실·교차로
        kept = self._kept("tourism", [
            ("해운대해수욕장", {"kakao": "여행 > 관광,명소 > 해수욕장,해변"}),
            ("해운대해수욕장 1호 공중화장실", {"kakao": "가정,생활 > 화장실"}),
            ("해운대해수욕장삼거리", {"kakao": "교통,수송 > 도로시설 > 교차로"}),
        ])
        self.assertEqual(kept, ["해운대해수욕장"])
        # 공공시설 요청에서는 화장실을 남긴다
        self.assertEqual(self._kept("public_facility", [("공중화장실", {"kakao": "가정,생활 > 화장실"})]), ["공중화장실"])

    def test_tour_content_types(self):
        # TourAPI는 콘텐츠 타입 이름을 업종으로 준다 (예: {"tour": "관광지"})
        self.assertEqual(infer_categories({"tour": "관광지"}), {"tourism"})
        self.assertEqual(infer_categories({"tour": "여행코스"}), {"tourism"})
        self.assertEqual(infer_categories({"tour": "문화시설"}), {"culture"})
        self.assertEqual(infer_categories({"tour": "축제공연행사"}), {"culture"})
        self.assertEqual(infer_categories({"tour": "레포츠"}), {"activity"})
        kept = self._kept("tourism", [(name, {"tour": name}) for name in
                                      ("관광지", "문화시설", "레포츠", "여행코스", "숙박", "음식점")])
        self.assertEqual(kept, ["관광지", "문화시설", "레포츠", "여행코스"])

    def test_event_is_scored_low_unless_asked(self):
        # 2026-11 '해운대 해수욕장' 실제 결과 2~3위였던 축제
        festival = {"kakao": "이슈 > 이벤트 > 페스티벌"}
        self.assertEqual(category_score("tourism", "beach", "해수욕장", festival, "tourism")[0], 0.2)
        self.assertEqual(category_score("culture", None, "미술관", {"tour": "축제공연행사"}, "culture")[0], 0.2)
        # 행사를 찾는 검색이면 낮추지 않는다
        self.assertGreater(category_score("tourism", None, "불꽃축제", festival, "tourism")[0], 0.2)
        # 후보에서 빼지는 않는다
        self.assertEqual(self._kept("tourism", [("해운대모래축제", festival)]), ["해운대모래축제"])

    def test_related_category_score(self):
        related = category_score("tourism", None, "관광지", {"naver": "관람,체험>아쿠아리움"}, "tourism")
        exact = category_score("tourism", None, "관광지", {"kakao": "여행 > 관광,명소 > 전망대"}, "tourism")
        amenity = category_score("tourism", None, "관광지", {"kakao": "교통,수송 > 교통시설 > 주차장"}, "tourism")
        self.assertEqual(related[0], 0.6)
        self.assertEqual(exact[0], 0.75)
        self.assertEqual(amenity[0], 0.1)
        self.assertTrue(is_amenity({"kakao": "교통,수송 > 입출구"}))

    def test_indoor_outdoor_from_real_paths(self):
        self.assertFalse(infer_indoor("여행 > 관광,명소 > 섬 > 섬(내륙)"))
        self.assertFalse(infer_indoor("여행 > 관광,명소 > 도보여행 > 갈맷길"))
        self.assertFalse(infer_indoor("스포츠,레저 > 수영,수상 > 수상스포츠"))
        self.assertTrue(infer_indoor("술집>요리주점"))
        self.assertTrue(infer_indoor("한식>감자탕"))
        self.assertTrue(infer_indoor("가정,생활 > 목욕탕,사우나 > 찜질방"))
        self.assertIsNone(infer_indoor("여행 > 관광,명소 > 테마거리 > 카페거리"))  # 거리(야외) + 카페(실내)

    def test_companion_from_real_paths(self):
        kids = "가정,생활 > 유아 > 놀이시설 > 키즈카페"
        family_rest = "음식점 > 패밀리레스토랑 > 빕스"
        self.assertEqual(condition_score("alone", None, None, kids)[0], 0.1)
        self.assertEqual(condition_score("family", None, None, kids)[0], 1.0)
        self.assertEqual(condition_score("family", None, None, family_rest)[0], 1.0)
        self.assertEqual(condition_score("family", None, None, "음식점 > 술집 > 칵테일바")[0], 0.1)
        self.assertEqual(condition_score("alone", None, None, "문화,예술 > 영화,영상 > 영화관 > CGV")[0], 1.0)


class HardFilterTest(unittest.TestCase):
    def test_filters(self):
        places = [
            Place(name="closed", is_available=False),
            Place(name="outdoor", indoor=False),
            Place(name="indoor", indoor=True),
            Place(name="unknown"),
            Place(name="wrong-cat", category="tourism", indoor=True),
        ]
        req = PlaceRequest(query="카페", category="cafe", indoor=True)
        names = [p.name for p in HardFilter().apply(req, places)]
        self.assertEqual(names, ["indoor", "unknown"])

    def test_indoor_request_uses_inferred_indoor(self):
        """실내/야외를 직접 말했을 때만, 업종으로 확실히 반대인 장소를 뺀다. 애매하면 남긴다."""
        places = [
            Place(name="해수욕장", source_categories={"kakao": "여행 > 관광,명소 > 해수욕장,해변"}),
            Place(name="동백섬", source_categories={"kakao": "여행 > 관광,명소 > 섬 > 섬(내륙)"}),
            Place(name="아쿠아리움", source_categories={"naver": "관람,체험>아쿠아리움"}),
            Place(name="방탈출", source_categories={"kakao": "가정,생활 > 여가시설 > 방탈출카페"}),
            Place(name="카페거리", source_categories={"kakao": "여행 > 관광,명소 > 테마거리 > 카페거리"}),
            Place(name="업종없음"),
            Place(name="명시 실내 해변", indoor=True, source_categories={"kakao": "여행 > 해수욕장"}),
        ]

        def kept(indoor):
            return [p.name for p in HardFilter().apply(PlaceRequest(query="놀거리", indoor=indoor), places)]

        self.assertEqual(kept(True), ["아쿠아리움", "방탈출", "카페거리", "업종없음", "명시 실내 해변"])
        self.assertEqual(kept(False), ["해수욕장", "동백섬", "카페거리", "업종없음"])
        self.assertEqual(len(kept(None)), len(places))


if __name__ == "__main__":
    unittest.main()
