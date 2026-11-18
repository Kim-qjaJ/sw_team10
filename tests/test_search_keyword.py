"""설명형 검색어 변환과 '기타' 종류 처리."""

from app.schemas.intent import PlaceRequest
from app.schemas.place import Place
from app.services.hard_filter import HardFilter
from app.services.scoring import category_score
from app.services.search_keyword import search_keyword


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
