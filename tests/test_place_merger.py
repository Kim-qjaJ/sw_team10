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

    def test_digits_read_as_korean(self):
        # 실제 데이터에서 놓쳤던 경우: Naver '송정3대국밥', Kakao '송정삼대국밥' (4m)
        result = self.merger.merge([kakao("송정삼대국밥"), naver("송정3대국밥", 4 / M_PER_DEG)])
        self.assertEqual(len(result), 1)

    def test_similar_name_nearby_merged(self):
        # 한 글자 차이(오타 수준), 20m 이내
        result = self.merger.merge([kakao("서면놀이터카페"), naver("서면놀이터까페", 20 / M_PER_DEG)])
        self.assertEqual(len(result), 1)

    def test_similar_name_far_not_merged(self):
        result = self.merger.merge([kakao("서면놀이터카페"), naver("서면놀이터까페", 120 / M_PER_DEG)])
        self.assertEqual(len(result), 2)

    def test_different_shops_same_building_not_merged(self):
        # 실제 데이터: 23m 떨어진 다른 가게
        result = self.merger.merge([kakao("신촌양푼이"), naver("철판은 핑계고", 23 / M_PER_DEG)])
        self.assertEqual(len(result), 2)

    def test_empty(self):
        self.assertEqual(self.merger.merge([]), [])

    def test_provider_rank_becomes_signal(self):
        """Kakao·Naver가 준 순서를 검색 순위 신호로 남긴다. 합쳐지면 높은 값, 원본은 그대로."""
        k1, k2 = kakao("A카페"), kakao("B카페", 0.01)
        n1 = naver("B카페", 0.01)
        tour = Place(name="C관광지", sources=["tour"])
        result = {p.name: p for p in self.merger.merge([k1, k2, n1, tour])}
        self.assertEqual(result["A카페"].popularity_signal, 1.0)
        self.assertEqual(result["B카페"].popularity_signal, 1.0)  # Kakao 2위지만 Naver 1위
        # 순서가 관련도순이 아닌 Provider에만 있는 장소는 카카오·네이버 순위권 밖과 같게 0으로 본다.
        self.assertEqual(result["C관광지"].popularity_signal, 0.0)
        self.assertIsNone(k1.popularity_signal)
        self.assertLess(self.merger.merge([k1, k2])[1].popularity_signal, 1.0)

    def test_unranked_place_keeps_given_signal(self):
        """Provider가 직접 준 인기 신호는 그대로 쓰고, 카카오와 합쳐지면 카카오 순위를 따른다."""
        tour = Place(name="D관광지", sources=["tour"], popularity_signal=0.7)
        self.assertEqual(self.merger.merge([tour])[0].popularity_signal, 0.7)
        merged = self.merger.merge([kakao("E공원"), Place(name="E공원", sources=["tour"], latitude=35.1, longitude=129.0)])
        self.assertEqual(merged[0].popularity_signal, 1.0)

    def test_real_biff_duplicates_merged(self):
        """2026-11 실제 '남포동 관광' 데이터: 같은 장소가 표기·좌표 차이로 5번 나왔다."""
        def at(name, src, lat, lng):
            return Place(name=name, latitude=lat, longitude=lng, sources=[src], source_ids={src: name})
        result = self.merger.merge([
            at("BIFF광장", "kakao", 35.0988679, 129.0290456),
            at("BIFF거리", "kakao", 35.0985283, 129.0290898),
            at("비프광장", "naver", 35.098723, 129.029021),        # 영문/한글 표기, 16m
            at("부산BIFF거리", "naver", 35.098519, 129.02677),     # 넓은 장소, 211m
            at("BIFF 광장", "tour", 35.0980090, 129.0320043),      # 같은 이름, 286m
        ])
        self.assertEqual([sorted(p.sources) for p in result], [["kakao", "naver", "tour"], ["kakao", "naver"]])

    def test_wide_rule_needs_same_area_word(self):
        # 이름이 같은 넓은 장소 말로 끝나지 않으면 200m 기준 그대로 (입구 근처 가게는 별개)
        result = self.merger.merge([kakao("해운대해수욕장"), naver("해운대해수욕장입구점", 300 / M_PER_DEG)])
        self.assertEqual(len(result), 2)
        # 완전히 같은 이름이라도 500m를 넘으면 별개
        self.assertEqual(len(self.merger.merge([kakao("할매국밥"), naver("할매국밥", 600 / M_PER_DEG)])), 2)

    def test_same_core_name_merged(self):
        """2026-11 실제 '서면 방탈출': 같은 가게(3m, 같은 주소)가 이름 앞뒤 말이 달라 1위·5위로 두 번 나왔다."""
        result = self.merger.merge([kakao("서면방탈출 카타르시스이스케이프"), naver("카타르시스 이스케이프 서면점", 3 / M_PER_DEG)])
        self.assertEqual(len(result), 1)

    def test_core_name_rule_keeps_branches_apart(self):
        # 지점명이 둘 다 있고 다르면 다른 가게 (가까워도)
        self.assertEqual(len(self.merger.merge([kakao("이재모피자 본점"), naver("이재모피자 서면점", 30 / M_PER_DEG)])), 2)
        # 핵심 이름이 같아도 50m를 넘으면 별개
        self.assertEqual(len(self.merger.merge([kakao("서면방탈출 카타르시스이스케이프"),
                                                naver("카타르시스 이스케이프 서면점", 120 / M_PER_DEG)])), 2)


if __name__ == "__main__":
    unittest.main()
