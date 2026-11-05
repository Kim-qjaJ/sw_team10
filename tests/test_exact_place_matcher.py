"""특정 장소 검색 결과 선택 단위 테스트 (FR-013): python -m unittest tests.test_exact_place_matcher"""
import unittest

from app.schemas.place import Place
from app.services.exact_place_matcher import ExactPlaceMatcher


def names(places):
    return [p.name for p in places]


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.m = ExactPlaceMatcher()

    def test_exact_match_ignores_spaces_and_case(self):
        places = [Place(name="이재모피자 서면점"), Place(name="이재모 피자")]
        self.assertEqual(self.m.match("이재모피자", places).name, "이재모 피자")
        self.assertIsNone(self.m.match("없는가게", places))

    def test_representative_is_main_branch(self):
        places = [Place(name="이재모피자 서면점"), Place(name="이재모피자 본점")]
        self.assertEqual(self.m.representative("이재모피자", places).name, "이재모피자 본점")
        self.assertIsNone(self.m.representative("이재모피자", [Place(name="이재모피자 서면점")]))

    def test_related_branches_keep_only_same_name(self):
        places = [Place(name="이재모피자 서면점"), Place(name="피자헛 서면점"), Place(name="이재모피자 광안점")]
        self.assertEqual(names(self.m.related_branches("이재모피자", places)), ["이재모피자 서면점", "이재모피자 광안점"])
        self.assertEqual(self.m.related_branches("", places), [])

    def test_institution_campuses(self):
        places = [Place(name="동아대학교 부민캠퍼스"), Place(name="동아대학교 승학캠퍼스"), Place(name="동아마트")]
        self.assertEqual(names(self.m.institution_locations("동아대학교", places)),
                         ["동아대학교 부민캠퍼스", "동아대학교 승학캠퍼스"])

    def test_institution_not_used_for_exact_or_non_university(self):
        exact = [Place(name="부산대학교"), Place(name="부산대학교 양산캠퍼스"), Place(name="부산대학교 밀양캠퍼스")]
        self.assertEqual(self.m.institution_locations("부산대학교", exact), [])
        self.assertEqual(self.m.institution_locations("이재모피자", [Place(name="이재모피자 본점")]), [])



class RealDataTest(unittest.TestCase):
    """2026-11 실제 Kakao·Naver 검색에서 틀렸던 경우."""

    def setUp(self):
        self.m = ExactPlaceMatcher()

    def test_campus_list_excludes_gates_and_graduate_schools(self):
        places = [Place(name=n) for n in ("동아대학교 부민캠퍼스", "동아대학교 승학캠퍼스", "동아대학교 구덕캠퍼스",
                                          "동아대학교 부민캠퍼스 법학전문대학원", "동아대학교 부동산대학원")]
        self.assertEqual(names(self.m.institution_locations("동아대학교", places)),
                         ["동아대학교 부민캠퍼스", "동아대학교 승학캠퍼스", "동아대학교 구덕캠퍼스"])
        busan = [Place(name=n) for n in ("부산대학교 부산캠퍼스", "부산대학교 부산캠퍼스 정문", "부산대학교 사범대학 부설고등학교",
                                         "부산대학교 새벽벌도서관")]
        self.assertEqual(names(self.m.institution_locations("부산대학교", busan)), ["부산대학교 부산캠퍼스"])

    def test_city_hall_alias(self):
        places = [Place(name=n) for n in ("미남스시 부산시청 본점", "부산시청 주차장", "부산광역시청", "BNK부산은행 부산시청지점")]
        self.assertEqual(self.m.match("부산시청", places).name, "부산광역시청")
        # 본점은 이름이 검색어로 시작해야 한다
        self.assertIsNone(self.m.representative("부산시청", [Place(name="미남스시 부산시청 본점")]))

    def test_branch_with_busan_in_name(self):
        self.assertEqual(self.m.match("스타벅스 서면점", [Place(name="스타벅스 부산서면점")]).name, "스타벅스 부산서면점")

    def test_branches_must_start_with_query(self):
        places = [Place(name=n) for n in ("이재모피자 서면 중앙점", "BNK부산은행 이재모피자앞", "이재모피자 광안점")]
        self.assertEqual(names(self.m.related_branches("이재모피자", places)), ["이재모피자 서면 중앙점", "이재모피자 광안점"])


    def test_branches_exclude_amenities(self):
        places = [Place(name="광안리 해변", source_categories={"kakao": "여행 > 관광,명소 > 해수욕장,해변"}),
                  Place(name="광안리 공영주차장", source_categories={"kakao": "교통,수송 > 교통시설 > 주차장 > 공영주차장"}),
                  Place(name="광안리해수욕장 공중화장실", source_categories={"kakao": "가정,생활 > 화장실"})]
        self.assertEqual(names(self.m.related_branches("광안리", places)), ["광안리 해변"])
        # 검색어가 부대시설이면 남긴다
        self.assertEqual(names(self.m.related_branches("광안리 공영주차장", places)), ["광안리 공영주차장"])

if __name__ == "__main__":
    unittest.main()
