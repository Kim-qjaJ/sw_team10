"""11/21 시나리오 점검에서 고친 대화 흐름 테스트 (외부 API·LLM 없이)."""

import asyncio
from types import SimpleNamespace

from app.chat.conversation import weather_reply
from app.chat.intents import merge_with_session
from app.schemas.intent import UserIntent
from app.services.local_router import format_place_fields, place_links, route_place_query

RECENT = [
    {"rank": 1, "name": "블랙업커피", "address": "부산 부산진구 서전로 1", "phone": "051-000-0000",
     "url": "https://place.map.kakao.com/1"},
    {"rank": 2, "name": "넉아웃", "address": "부산 부산진구 동천로 91-4", "phone": None,
     "url": "http://place.map.kakao.com/2"},
]


def test_spaced_beach_near_is_one_anchor_not_two_searches():
    for message, point in (("송도 해수욕장 근처 횟집", "송도해수욕장"), ("일광 해수욕장 근처 카페", "일광해수욕장"),
                           ("서면 역 근처 약국", "서면역")):
        route = route_place_query(message)
        assert route.kind == "direct"
        assert route.intent.location == point
        assert len(route.intent.requests) == 1
    # '근처'가 없으면 해수욕장 자체를 찾는 요청이다.
    beach = route_place_query("송도 해수욕장 추천")
    assert [r.category for r in beach.intent.requests] == ["tourism"]


def test_location_only_asks_kind_and_answer_searches_there():
    route = route_place_query("광안리")
    assert route.kind == "clarify" and "어떤 곳" in route.message
    assert route.intent.location == "부산 광안리" and not route.intent.requests
    answer = route_place_query("카페", pending_intent=route.intent)
    assert answer.kind == "direct"
    assert answer.intent.location == "부산 광안리"
    assert [r.query for r in answer.intent.requests] == ["카페"]


def test_lodging_is_out_of_scope_but_hotel_as_anchor_is_not():
    route = route_place_query("사상 터미널 근처 숙소")
    assert route.kind == "unsupported" and "숙소" in route.message
    assert route_place_query("해운대 호텔 근처 맛집").kind != "unsupported"


def test_detail_answer_is_multiline_and_link_is_a_button():
    text = format_place_fields("2번 상세 정보 알려줘", RECENT)
    assert "URL" not in text and "http" not in text
    assert "주소: 부산 부산진구 동천로 91-4" in text and "전화: 정보 없음" in text
    links = place_links("2번 상세 정보 알려줘", RECENT)
    assert links == [{"label": "2번 넉아웃 정보", "url": "http://place.map.kakao.com/2"}]
    assert place_links("1번 주소 알려줘", RECENT) == []


def test_refine_followup_keeps_previous_search():
    previous = UserIntent.model_validate({"intent": "recommend_place", "location": "부산 서면",
                                          "requests": [{"query": "카페", "category": "cafe"}]})
    for current in (UserIntent(intent="clarify", question="어느 지역?"),
                    UserIntent.model_validate({"intent": "recommend_place", "requests": [{"query": "조용한 곳"}]}),
                    UserIntent.model_validate({"intent": "recommend_place",
                                               "requests": [{"query": "조용한 곳", "category": "activity"}]}),
                    UserIntent.model_validate({"intent": "recommend_place",
                                               "requests": [{"query": "카페", "category": "cafe"}]})):
        merged = merge_with_session(previous, current, "더 조용한 곳으로")
        assert merged.intent == "recommend_place"
        assert merged.location == "부산 서면"
        assert [(r.query, r.category) for r in merged.requests] == [("조용한 카페", "cafe")]
    # 새 지역·종류를 말한 질문은 그대로 둔다.
    fresh = UserIntent.model_validate({"intent": "recommend_place", "location": "해운대",
                                       "requests": [{"query": "맛집", "category": "restaurant"}]})
    assert merge_with_session(previous, fresh, "해운대 맛집") == fresh


def _ctx(weather):
    async def get(point):
        return weather
    return SimpleNamespace(pipeline=SimpleNamespace(context=SimpleNamespace(weather=get)))


def test_weather_question_uses_observation_not_llm():
    clear = asyncio.run(weather_reply(_ctx({"source": "kma", "is_raining": False, "temperature": -0.8})))
    assert "-0.8°C" in clear and "비는 오지 않아요" in clear
    rain = asyncio.run(weather_reply(_ctx({"source": "kma", "is_raining": True, "precipitation": "rain",
                                           "temperature": 12})))
    assert "비가" in rain and "12°C" in rain
    missing = asyncio.run(weather_reply(_ctx(None)))
    assert "확인하지 못했어요" in missing


def test_weather_question_detection_and_place():
    from app.chat.conversation import WEATHER_QUESTION, weather_place
    for text in ("오늘 날씨 어때", "지금 몇 도야", "밖에 추워?", "내일 비 와?", "우산 챙겨야 해?"):
        assert WEATHER_QUESTION.search(text), text
    for text in ("비 오는 날 해운대 관광지", "서면 카페 추천해줘"):
        assert not WEATHER_QUESTION.search(text), text
    assert weather_place("해운대 날씨 어때")[0] == "해운대"
    assert weather_place("오늘 날씨 어때")[0].startswith("부산")


def test_adjective_ending_is_not_a_count():
    from app.services.local_router import requested_count
    assert requested_count("더 조용한 곳으로") is None
    assert requested_count("해운대에서 아이랑 갈 만한 곳") is None
    assert requested_count("카페 한 곳만 추천해줘") == 1
    assert requested_count("서면 맛집 두 곳") == 2
    assert requested_count("관광지 3개만") == 3


def test_weather_answer_offers_buttons_for_that_area():
    from app.chat.conversation import weather_location
    from app.chat.suggestions import build_suggestions
    from app.schemas.api import ChatRequest, RecommendResponse
    from app.schemas.intent import UserIntent

    assert weather_location("해운대 날씨 어때") == "해운대"
    assert weather_location("오늘 날씨 어때") is None
    def labels(message, location):
        response = RecommendResponse(message=message, intent=UserIntent(intent="smalltalk", location=location),
                                     results=[], timing_ms={})
        return [s.label for s in build_suggestions(response, None, ChatRequest(message="날씨"))]
    assert labels("지금 해운대 기온은 2°C이고 비는 오지 않아요. (기상청 초단기실황 기준)", "해운대") == \
        ["해운대 관광지 추천해줘", "해운대 카페", "해운대 맛집"]
    assert labels("지금 서면은 비가 오고 기온은 9°C예요. 실내 장소를 찾아 드릴까요?", "서면")[0] == "비 오는 날 서면 갈 만한 곳"
    assert labels("지금 부산(시청 부근)은 비가 오고 기온은 9°C예요. 실내 장소를 찾아 드릴까요?", None)[0].startswith("비 오는 날")


def test_festival_question_after_results_is_not_a_detail_answer():
    """'부산 전체 진행중인 축제를 알려줘'를 직전 결과 전체의 상세 정보로 답하던 문제 (2026-11-23)."""
    from app.services.local_router import format_place_fields

    recent = [{"rank": 1, "name": "해운대해수욕장", "address": "부산 해운대구 우동", "phone": "051-749-7612"},
              {"rank": 2, "name": "해운대 이벤트광장", "address": "부산 해운대구"}]
    assert format_place_fields("부산 전체 진행중인 축제를 알려줘", recent) is None
    assert format_place_fields("전체 알려줘", recent) is None            # 무엇을 알려 달라는지 없음 → LLM이 해석
    assert format_place_fields("전체 전화번호 알려줘", recent) is not None
    assert format_place_fields("1번 알려줘", recent).startswith("1번 해운대해수욕장")
    assert format_place_fields("해운대해수욕장 주소", recent) is not None
