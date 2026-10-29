"""Local Router 단위 테스트: python -m unittest discover -s tests"""
import unittest

from app.schemas.intent import UserIntent
from app.services.local_router import format_place_fields, needs_session_context, route_place_query

PREVIOUS = UserIntent.model_validate({
    "intent": "recommend_place",
    "location": "부산 서면",
    "requests": [{"query": "카페", "category": "cafe"}],
})
RECENT = [{"rank": 1, "name": "A카페", "address": "부산 A로 1"}, {"rank": 2, "name": "B카페", "address": "부산 B로 2"}]


class DirectRouteTest(unittest.TestCase):
    """위치 + 장소 종류가 분명한 질의는 LLM 없이 Intent를 만든다."""

    def assertDirect(self, message, location, queries, **kwargs):
        route = route_place_query(message, **kwargs)
        self.assertEqual(route.kind, "direct", f"{message}: {route.reason}")
        self.assertEqual(route.intent.location, location)
        self.assertEqual([r.query for r in route.intent.requests], queries)
        return route.intent

    def test_area_and_category(self):
        self.assertDirect("서면 카페 추천해줘", "부산 서면", ["카페"])
        self.assertDirect("덕천에서 맛집 알려줘", "부산 덕천", ["맛집"])
        self.assertDirect("해운대구 중국집", "부산 해운대구", ["중국집"])
        self.assertDirect("전포동 카페", "부산 전포동", ["카페"])

    def test_point_anchor(self):
        self.assertDirect("동아대학교 근처 카페", "동아대학교", ["카페"])
        self.assertDirect("부산역 주변 국밥 추천", "부산역", ["국밥"])

    def test_multiple_requests(self):
        self.assertDirect("북구청 근처 도서관과 중국집을 가고 싶어", "북구청", ["도서관", "중국집"])
        self.assertDirect("서면 카페랑 음식점", "부산 서면", ["카페", "음식점"])

    def test_companion_and_transport(self):
        intent = self.assertDirect("친구랑 지하철로 광안리 술집", "부산 광안리", ["술집"])
        self.assertEqual(intent.companion, "friend")
        self.assertEqual(intent.transport_mode, "transit")

    def test_exact_and_brand(self):
        route = route_place_query("동아대학교 어디야")
        self.assertEqual(route.kind, "direct")
        self.assertEqual(route.intent.requests[0].search_mode, "exact_place")
        route = route_place_query("부산시립미술관 위치")
        self.assertEqual(route.intent.requests[0].query, "부산시립미술관")
        route = route_place_query("스타벅스 어디 있어?")
        self.assertEqual(route.intent.requests[0].search_mode, "brand")

    def test_reference_uses_previous_location(self):
        self.assertDirect("그 근처 음식점", "부산 서면", ["음식점"], previous_intent=PREVIOUS, recent_places=RECENT)
        self.assertDirect("다른 카페는?", "부산 서면", ["카페"], previous_intent=PREVIOUS, recent_places=RECENT)
        self.assertDirect("음식점도 알려줘", "부산 서면", ["음식점"], previous_intent=PREVIOUS, recent_places=RECENT)

    def test_reference_after_exact_search_uses_place_name(self):
        previous = UserIntent.model_validate({
            "intent": "search_place",
            "requests": [{"query": "동아대학교", "search_mode": "exact_place"}],
        })
        self.assertDirect("그 근처 카페", "동아대학교 승학캠퍼스", ["카페"],
                          previous_intent=previous, recent_places=[{"rank": 1, "name": "동아대학교 승학캠퍼스"}])

    def test_answer_to_clarify(self):
        pending = route_place_query("카페 추천해줘").intent
        self.assertDirect("서면", "부산 서면", ["카페"], pending_intent=pending)


class OtherRouteTest(unittest.TestCase):
    def test_ambiguous_goes_to_llm_with_fallback(self):
        route = route_place_query("서면 조용한 카페")
        self.assertEqual(route.kind, "llm")
        self.assertEqual(route.fallback.location, "부산 서면")

        route = route_place_query("친구랑 얘기하면서 오래 있기 괜찮은 곳 없을까? 서면 쪽으로")
        self.assertEqual(route.kind, "llm")
        self.assertIsNone(route.fallback)

        self.assertEqual(route_place_query("서면 갈만한 곳").kind, "llm")
        self.assertEqual(route_place_query("서면이랑 해운대 카페").kind, "llm")

    def test_clarify(self):
        route = route_place_query("카페 추천해줘")
        self.assertEqual(route.kind, "clarify")
        self.assertIn("카페를", route.message)
        self.assertEqual(route_place_query("추천해줘").kind, "clarify")

    def test_non_busan(self):
        for message in ("서울 강남 카페", "대구 동성로 맛집"):
            self.assertEqual(route_place_query(message).kind, "unsupported")

    def test_compare_followup(self):
        route = route_place_query("그중에서 친구 4명이 가기 괜찮아 보이는 곳은?", PREVIOUS, RECENT)
        self.assertEqual(route.kind, "compare")
        # 직전 결과가 없으면 비교가 아니다.
        self.assertNotEqual(route_place_query("그중에서 조용한 곳은?").kind, "compare")

    def test_detail_question_vs_new_search(self):
        self.assertIn("A카페", format_place_fields("1번 주소 알려줘", RECENT))
        self.assertIsNone(format_place_fields("거기 근처 술집 알려줘", RECENT))

    def test_session_context_only_when_needed(self):
        self.assertTrue(needs_session_context("그 근처는?"))
        self.assertFalse(needs_session_context("친구랑 얘기하면서 오래 있기 괜찮은 곳 없을까? 서면 쪽으로"))


if __name__ == "__main__":
    unittest.main()
