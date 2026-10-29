"""같은 장소 통합 단위 테스트: python -m unittest tests.test_place_merger"""
import unittest

from app.schemas.place import Place
from app.services.place_merger import PlaceMerger

LAT, LNG = 35.1577, 129.0596  # 서면역 근처
M_PER_DEG = 111_000


def kakao(name, dlat=0.0, **kw):
    return Place(name=name, latitude=LAT + dlat, longitude=LNG, sources=["kakao"], source_ids={"kakao": f"k-{name}"},
                 source_categories={"kakao": "음식점 > 피자"}, source_category_codes={"kakao": "FD6"}, **kw)


def naver(name, dlat=0.0, **kw):
    return Place(name=name, latitude=LAT + dlat, longitude=LNG, sources=["naver"], source_ids={"naver": f"n-{name}"},
                 source_categories={"naver": "양식>피자"}, **kw)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.merger = PlaceMerger()

    def test_same_place_from_two_providers(self):
        result = self.merger.merge([kakao("이재모피자 본점", phone="051-000-0000"),
                                    naver("이재모 피자", 50 / M_PER_DEG, url="https://example.com")])
        self.assertEqual(len(result), 1)
        place = result[0]
        self.assertEqual(place.name, "이재모피자 본점")  # 먼저 온 결과(Kakao)가 기준
        self.assertEqual(place.sources, ["kakao", "naver"])
        self.assertEqual(set(place.source_ids), {"kakao", "naver"})
        self.assertEqual(place.source_categories, {"kakao": "음식점 > 피자", "naver": "양식>피자"})
        self.assertEqual(place.source_category_codes, {"kakao": "FD6"})
        self.assertEqual(place.phone, "051-000-0000")
        self.assertEqual(place.url, "https://example.com")  # 비어 있던 값은 Naver에서 채움

    def test_far_apart_is_different_branch(self):
        result = self.merger.merge([kakao("스타벅스"), naver("스타벅스", 500 / M_PER_DEG)])
        self.assertEqual(len(result), 2)

    def test_different_branch_names_not_merged(self):
        result = self.merger.merge([kakao("이재모피자 본점"), naver("이재모피자 서면점", 30 / M_PER_DEG)])
        self.assertEqual(len(result), 2)

    def test_same_provider_not_merged(self):
        result = self.merger.merge([kakao("카페A"), kakao("카페A", 10 / M_PER_DEG)])
        self.assertEqual(len(result), 2)

    def test_address_used_when_coordinates_missing(self):
        a = Place(name="부산시립미술관", address="부산광역시 해운대구 APEC로 58", sources=["busan"])
        b = Place(name="부산시립미술관", address="부산 해운대구 APEC로 58", sources=["tour"], latitude=35.1665, longitude=129.137)
        c = Place(name="부산시립미술관", address="부산 해운대구 다른로 1", sources=["naver"])
        result = self.merger.merge([a, b, c])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].sources, ["busan", "tour"])
        self.assertEqual((result[0].latitude, result[0].longitude), (35.1665, 129.137))  # 좌표는 쌍으로 채움

    def test_signals_combined(self):
        result = self.merger.merge([kakao("해운대해수욕장", popularity_signal=0.4),
                                    naver("해운대 해수욕장", popularity_signal=0.8, busan_verified=True)])
        self.assertEqual(result[0].popularity_signal, 0.8)
        self.assertTrue(result[0].busan_verified)

    def test_order_kept_and_input_not_modified(self):
        first, dup, other = kakao("A카페"), naver("A카페"), kakao("B식당", 0.01)
        result = self.merger.merge([first, dup, other])
        self.assertEqual([p.name for p in result], ["A카페", "B식당"])
        self.assertEqual(first.sources, ["kakao"])

    def test_one_char_name_does_not_absorb_others(self):
        result = self.merger.merge([kakao("A"), naver("A카페")])
        self.assertEqual(len(result), 2)

    def test_empty(self):
        self.assertEqual(self.merger.merge([]), [])


if __name__ == "__main__":
    unittest.main()
