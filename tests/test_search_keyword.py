"""설명형 검색어 변환과 '기타' 종류 처리."""

from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.hard_filter import HardFilter
from app.services.scoring import category_score
from app.services.search_keyword import search_keyword, search_keywords, unverifiable_condition


def kw(query, category, sub=None, mode="category"):
    return search_keyword(PlaceRequest(query=query, category=category, subcategory=sub, search_mode=mode))


def test_descriptive_query_becomes_place_word():
    assert kw("책 읽을 곳", "cafe", "bookstore_cafe") == "북카페"
    assert kw("먹을 곳", "restaurant") == "맛집"
    assert kw("조용히 공부할 곳", "cafe", "study_cafe") == "스터디카페"


def test_normal_or_unknown_query_is_kept():
    assert kw("바다 보이는 카페", "cafe") == "바다 보이는 카페"
    assert kw("데이트 장소", "other") == "데이트 장소"  # 대표 단어가 없으면 그대로 검색
    assert kw("이재모피자", "restaurant", mode="brand") == "이재모피자"


def test_other_category_is_not_used_as_filter_or_penalty():
    cafe = Place(name="광안리 카페", source_categories={"kakao": "음식점 > 카페"})
    request = PlaceRequest(query="데이트 장소", category="other")
    assert HardFilter().apply(request, [cafe]) == [cafe]
    score, _ = category_score("other", None, "데이트 장소", cafe.source_categories, None)
    assert score > 0.1


def test_other_category_still_drops_lodging_and_amenities():
    motel = Place(name="호텔1", source_categories={"kakao": "숙박 > 모텔"})
    request = PlaceRequest(query="데이트 장소", category="other")
    assert HardFilter().apply(request, [motel]) == []


def test_library_counts_as_public_facility():
    library = Place(name="동래읍성도서관", source_categories={"kakao": "문화,예술 > 도서관"})
    request = PlaceRequest(query="책 읽을 곳", category="public_facility", subcategory="library")
    assert HardFilter().apply(request, [library]) == [library]


def _req(query, category=None, sub=None, mode="category"):
    return PlaceRequest(query=query, category=category, subcategory=sub, search_mode=mode)


def test_modifier_query_also_searches_core_word():
    assert search_keywords(_req("조용한 카페", "cafe")) == ["조용한 카페", "카페"]
    assert search_keywords(_req("공부하기 좋은 카페", "cafe")) == ["공부하기 좋은 카페", "카페"]
    assert unverifiable_condition(_req("조용한 카페", "cafe")) == "조용한"
    # 지역 이름·업종 단어는 조건이 아니다.
    assert unverifiable_condition(_req("해운대 맛집", "restaurant")) is None
    assert unverifiable_condition(_req("디저트 카페", "cafe")) is None
    # 서비스가 직접 거르는 '무료'·전시는 나누지 않는다 (유료 전시가 섞이지 않게).
    assert search_keywords(_req("무료 전시", "culture", "exhibition")) == ["무료 전시"]
    assert unverifiable_condition(_req("무료 전시", "culture")) is None
    # 한 단어·장소 이름 검색은 그대로
    assert search_keywords(_req("카페", "cafe")) == ["카페"]
    assert search_keywords(_req("부산역", None, mode="exact_place")) == ["부산역"]


def test_broad_request_is_split_into_place_words():
    assert search_keywords(_req("갈 만한 곳", "activity")) == ["놀거리", "체험"]
    assert search_keywords(_req("갈 만한 곳", "activity"), companion="family") == ["키즈카페", "체험", "놀거리"]
    assert search_keywords(_req("가볼 만한 곳", "tourism")) == ["관광지", "관광명소"]
    # 세부 종류로 단어가 정해지면 그 단어만 쓴다.
    assert search_keywords(_req("책 읽을 곳", "cafe", "book_cafe")) == ["북카페"]
