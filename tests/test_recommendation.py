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
    WEIGHTS, access_score, busan_score, category_path, category_score, condition_score, cross_score, infer_indoor,
    weather_score,
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


if __name__ == "__main__":
    unittest.main()
