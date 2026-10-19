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


if __name__ == "__main__":
    unittest.main()
