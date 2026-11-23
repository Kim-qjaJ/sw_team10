"""사전에 없는 기준 장소('대항항')로 주변 시설을 찾을 때의 동작 (2026-11-23 외부 검토 반영)."""

import asyncio
from unittest.mock import patch

from app.schemas.location import LocationReference
from app.services.local_router import split_query
from app.services.location_resolver import LocationResolver

DAEHANG = LocationReference(mode="specific_place", resolved_name="대항항", latitude=35.0, longitude=128.83, source="kakao")


def fake_lookup(table):
    async def lookup(self, text, required_district=None):
        return table.get(text, LocationReference(mode="not_found", original_text=text))
    return lookup


def resolve(text, table):
    with patch.object(LocationResolver, "_kakao_lookup", fake_lookup(table)):
        return asyncio.run(LocationResolver().resolve_text(text))


def test_unknown_word_before_near_is_the_anchor_not_the_keyword():
    q = split_query("대항항 근처 약국")
    assert (q.location, q.keyword) == ("대항항 근처", "약국")
    q = split_query("가덕도 대항항 근처 도서관")
    assert (q.location, q.keyword) == ("가덕도 대항항 근처", "도서관")
    # 사전에 있는 지명은 예전과 같다.
    assert split_query("서면 근처 카페").location == "부산 서면"


def test_unknown_anchor_is_looked_up_on_the_map():
    ref = resolve("대항항 근처", {"대항항": DAEHANG})
    assert ref.mode == "specific_place" and ref.latitude == 35.0


def test_anchor_not_found_says_so_and_offers_a_similar_name():
    ref = resolve("대항항 근처", {})
    assert ref.mode == "not_found" and ref.resolved_name == "대항항"
    other = LocationReference(mode="specific_place", resolved_name="천성선착장", latitude=35.0, longitude=128.8)
    ref = resolve("대항항 근처", {"대항항": other})
    assert ref.mode == "not_found" and ref.similar_name == "천성선착장"


def test_area_word_with_missing_anchor_widens_and_records_it():
    ref = resolve("가덕도 대항항 근처", {})
    assert ref.mode == "area_anchor" and ref.widened_from == "대항항"
    assert "가덕도동" in ref.resolved_name


def test_generic_words_are_not_looked_up():
    ref = resolve("부산 전체", {"전체": DAEHANG})
    assert ref.mode == "area_anchor" and ref.latitude is None


def test_reply_to_location_question_is_used_as_the_anchor():
    from fastapi.testclient import TestClient

    from app import main

    asked = []

    async def lookup(self, text, required_district=None):
        asked.append(text)
        return DAEHANG if text == "대항항" else LocationReference(mode="not_found", original_text=text)

    with patch.object(main.settings, "use_mock_places", True), \
            patch.object(main.settings, "supabase_url", None), \
            patch.object(LocationResolver, "_kakao_lookup", lookup):
        with TestClient(main.app) as client:
            send = lambda m: client.post("/api/v1/recommend", json={"message": m, "session_id": "t-anchor", "mode": "nearby"}).json()
            first = send("약국")
            assert "현재 위치를 확인하지 못했어요" in first["message"]
            second = send("대항항")
            # 같은 질문을 되풀이하지 않고 '대항항'을 기준 장소로 찾는다.
            assert "현재 위치를 확인하지 못했어요" not in (second.get("message") or "")
            assert second["intent"]["location"] == "대항항 근처"
            assert second["intent"]["requests"][0]["query"] == "약국"

            missing = send("없는항구 근처 약국")
            assert "'없는항구' 위치를 부산에서 찾지 못했어요" in missing["notice"]
            assert "다시 알려 주세요" in missing["notice"]
            # 못 찾았으면 다음 답도 기준 장소로 받는다.
            again = send("대항항")
            assert again["intent"]["location"] == "대항항 근처"
    assert "대항항" in asked


def test_amenity_search_keeps_the_amenity_itself():
    from app.schemas.intent import PlaceRequest
    from app.schemas.place import Place
    from app.services.hard_filter import HardFilter

    store = Place(name="GS25 해운대점", source="kakao", latitude=35.16, longitude=129.16,
                  source_categories={"kakao": "가정,생활 > 편의점 > GS25"})
    parking = Place(name="해운대 공영주차장", source="kakao", latitude=35.16, longitude=129.16,
                    source_categories={"kakao": "교통,수송 > 교통시설 > 주차장"})
    want_store = PlaceRequest(query="편의점", category="other", subcategory="convenience_store", search_mode="category")
    assert [p.name for p in HardFilter().apply(want_store, [store, parking])] == ["GS25 해운대점"]
    want_food = PlaceRequest(query="맛집", category="other", search_mode="category")
    assert HardFilter().apply(want_food, [store, parking]) == []


def test_missing_number_never_gives_another_places_link():
    from app.services.local_router import format_place_fields, format_route

    places = [{"rank": 1, "name": "블랙업커피 서면본점", "latitude": 35.15, "longitude": 129.06, "phone": "051-000-0000"}]
    text, links = format_route("2번 길찾기", places)
    assert links == [] and "2번 장소가 없어요" in text and "1번만" in text
    assert "2번 장소가 없어요" in format_place_fields("2번 전화번호", places)
    # 있는 번호와 번호 없는 길찾기는 예전처럼 1번으로 안내한다.
    assert format_route("1번 길찾기", places)[1][0]["label"].startswith("1번")
    assert format_route("거기 어떻게 가", places)[1]


def test_expand_request_while_waiting_for_anchor_keeps_the_facility():
    from fastapi.testclient import TestClient

    from app import main

    with patch.object(main.settings, "use_mock_places", True), \
            patch.object(main.settings, "supabase_url", None), \
            patch.object(LocationResolver, "_kakao_lookup", fake_lookup({"대항항": DAEHANG})):
        with TestClient(main.app) as client:
            send = lambda m: client.post("/api/v1/recommend", json={"message": m, "session_id": "t-expand", "mode": "nearby"}).json()
            send("약국")
            reply = send("범위 넓혀줘")
            assert "어떤 시설을 찾을까요" not in (reply.get("message") or "")
            assert "최대 5km" in reply["notice"] and "'약국'" in reply["notice"]
            after = send("대항항")
            assert after["intent"]["requests"][0]["query"] == "약국"
