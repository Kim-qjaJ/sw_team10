"""직전 결과 번호로 장소를 가리키는 후속 질문 ('카페 2번 길찾기', '3번 근처 맛집')."""

from app.schemas.intent import PlaceRequest, UserIntent
from app.services.followup import LastSearch, plan_followup
from app.services.local_router import select_recent_places

PLACES = [
    {"rank": 1, "group_query": "맛집", "group_rank": 1, "name": "가게A", "latitude": 35.1, "longitude": 129.0},
    {"rank": 2, "group_query": "맛집", "group_rank": 2, "name": "가게B", "latitude": 35.1, "longitude": 129.0},
    {"rank": 3, "group_query": "카페", "group_rank": 1, "name": "카페C", "latitude": 35.2, "longitude": 129.1},
    {"rank": 4, "group_query": "카페", "group_rank": 2, "name": "카페D", "latitude": 35.2, "longitude": 129.1},
]


def names(message):
    return [p["name"] for p in select_recent_places(message, PLACES)]


def test_number_counts_across_groups_like_the_screen():
    assert names("3번 길찾기") == ["카페C"]
    assert names("4번 전화번호") == ["카페D"]


def test_group_name_before_number_picks_inside_that_group():
    assert names("카페 2번 길찾기") == ["카페D"]
    assert names("맛집 1번 주소") == ["가게A"]
    # 묶음 이름이 두 개면 번호 바로 앞에 쓴 묶음을 고른다.
    assert names("카페 2번 근처 맛집") == ["카페D"]


def test_korean_ordinal_and_out_of_range():
    assert names("두 번째 가게 주소") == ["가게B"]
    assert names("30번 주소") == []


def last_search():
    return LastSearch(intent=UserIntent(
        intent="recommend_place", location="부산 서면",
        requests=[PlaceRequest(query="맛집", category="restaurant"), PlaceRequest(query="카페", category="cafe")],
    ))


def queries(message):
    plan = plan_followup(message, last_search())
    return plan and [r["query"] for r in plan.requests]


def test_numbered_near_request_is_a_followup():
    assert queries("3번 근처 맛집") == ["맛집"]
    assert queries("두 번째 주변 카페") == ["카페"]


def test_category_before_number_points_to_the_place_not_the_search():
    assert queries("카페 2번 근처 맛집 알려줘") == ["맛집"]
    assert queries("맛집 1번 주변 카페") == ["카페"]


def test_detail_question_is_not_a_followup_search():
    assert plan_followup("1번 전화번호", last_search()) is None


def test_other_places_for_one_group_skips_already_shown():
    plan = plan_followup("카페만 다른 곳 보여줘", last_search())
    assert [r["query"] for r in plan.requests] == ["카페"] and plan.same_query
    # '다른'이 없으면 같은 카페를 다시 보여줘도 된다.
    assert not plan_followup("카페도 보여줘", last_search()).same_query
