"""2026-11-23 외부 검토 2차: 제외 조건, 날짜 있는 날씨 질문, 지역만 바꾸기, 이동 시간 상한."""

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.chat.conversation import weather_day, weather_reply
from app.providers.weather import forecast_base, summarize_forecast
from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.followup import LastSearch, plan_followup
from app.services.travel_limit import travel_limit, within_limit

MONDAY = date(2026, 11, 23)
LAST = LastSearch(intent=UserIntent(intent="recommend_place", location="부산 서면",
                                    requests=[PlaceRequest(query="카페", category="cafe")]), limit=1)


def test_exclusion_is_a_filter_not_a_brand_search():
    plan = plan_followup("스타벅스는 빼고 추천해줘", LAST)
    assert [r["query"] for r in plan.requests] == ["카페"] and plan.exclude_words == ["스타벅스"]
    assert plan_followup("이디야 제외하고 보여줘", LAST).exclude_words == ["이디야"]
    # '여기 말고 다른 영화관'은 예전처럼 종류를 바꾸는 말이다.
    plan = plan_followup("여기 말고 다른 영화관", LAST)
    assert [r["query"] for r in plan.requests] == ["영화관"] and plan.exclude_words == []


def test_area_swap_keeps_kind():
    for message, area in [("해운대로 바꿔줘", "부산 해운대"), ("전포로 바꿔줘", "부산 전포"),
                          ("서면 대신 전포에서 찾아줘", "부산 전포")]:
        plan = plan_followup(message, LAST)
        assert plan.location == area and [r["query"] for r in plan.requests] == ["카페"], message
    assert plan_followup("해운대 맛집", LAST) is None  # 종류를 새로 말하면 새 검색


def test_swap_keeps_count_through_api():
    from fastapi.testclient import TestClient

    from app import main

    with patch.object(main.settings, "use_mock_places", True), patch.object(main.settings, "supabase_url", None):
        with TestClient(main.app) as client:
            send = lambda m: client.post("/api/v1/recommend",
                                         json={"message": m, "session_id": "t-swap", "mode": "recommend"}).json()
            send("서면 카페 한 곳만 추천해줘")
            moved = send("전포로 바꿔줘")
    assert moved["intent"]["location"] == "부산 전포"
    assert moved["intent"]["requests"][0]["query"] == "카페"
    assert sum(len(r["places"]) for r in moved["results"]) <= 1


def test_weather_day_reading():
    assert weather_day("내일 해운대에 비 와?", MONDAY) == ("내일", date(2026, 11, 24))
    assert weather_day("내일모레 비 와?", MONDAY) == ("모레", date(2026, 11, 25))
    assert weather_day("다음주 화요일 날씨", MONDAY)[1] == date(2026, 12, 1)
    assert weather_day("다음 주 날씨", MONDAY)[1] is None
    assert weather_day("오늘 해운대 날씨 어때?", MONDAY) is None  # 지금 관측값


def test_forecast_summary_and_reply():
    items = [
        {"fcstDate": "20261124", "fcstTime": "1500", "category": "POP", "fcstValue": "70"},
        {"fcstDate": "20261124", "fcstTime": "1500", "category": "PTY", "fcstValue": "1"},
        {"fcstDate": "20261124", "fcstTime": "1500", "category": "TMP", "fcstValue": "12"},
        {"fcstDate": "20261124", "fcstTime": "0600", "category": "TMP", "fcstValue": "4"},
    ]
    days = summarize_forecast(items)
    assert days["20261124"]["pop"] == 70 and days["20261124"]["wet"] == ["1500"]

    async def forecast(point):
        return {"days": days, "issued": "2026-11-23 11:00", "source": "kma_forecast"}

    async def weather(point):
        raise AssertionError("내일 날씨에 지금 관측값을 쓰면 안 된다")

    ctx = SimpleNamespace(pipeline=SimpleNamespace(context=SimpleNamespace(forecast=forecast, weather=weather)))
    reply = asyncio.run(weather_reply(ctx, "내일 해운대에 비 와?", today=MONDAY))
    assert reply.startswith("내일(11월 24일 화) 해운대") and "비가 올 것" in reply and "70%" in reply
    later = asyncio.run(weather_reply(ctx, "글피 날씨 어때?", today=MONDAY))
    assert "예보가 없어 확인할 수 없어요" in later


def test_forecast_base_time():
    from datetime import datetime
    assert forecast_base(datetime(2026, 11, 23, 12, 5)).hour == 11
    assert forecast_base(datetime(2026, 11, 23, 11, 5)).hour == 8   # 발표 10분 전에는 직전 발표
    early = forecast_base(datetime(2026, 11, 23, 1, 0))
    assert (early.day, early.hour) == (22, 23)


def test_travel_time_limit_is_applied():
    assert travel_limit("부산역에서 걸어서 5분 이내 카페 추천해줘") == ("walk", 5)
    assert travel_limit("서면역 차로 10분 안에 맛집") == ("car", 10)
    assert travel_limit("서면 카페 5곳") is None
    six = Place(name="코이시", source="kakao", walking_minutes=6.0, straight_distance_m=390)
    three = Place(name="딥커피스트릿", source="kakao", walking_minutes=3.0, straight_distance_m=225)
    near = Place(name="역 안 빵집", source="kakao", straight_distance_m=23)  # 경로 시간 없음 → 직선거리 추정
    assert [p.name for p in within_limit([six, three, near], "walk", 5)] == ["딥커피스트릿", "역 안 빵집"]


def test_exhibit_rest_comes_from_the_exhibit_text():
    from app.providers.busan import exhibit_rest
    from app.providers.rest_days import closed_on

    detail = {"seq": "9", "price": "무료", "placeUrl": "https://example.com/휴관",
              "contents1": "<p>관람시간 10:00~18:00<br>매주 월요일 휴관</p>"}
    rest = exhibit_rest(detail)
    assert rest and "월요일 휴관" in rest and "example" not in rest
    assert closed_on(rest, MONDAY) is True
    assert exhibit_rest({"contents1": "누구나 볼 수 있는 전시"}) is None


def test_exclusion_survives_the_next_followup():
    last = LastSearch(intent=LAST.intent, exclude_words=["스타벅스"])
    plan = plan_followup("다른 곳 더 보여줘", last)
    assert plan is not None and plan.same_query  # 핸들러가 last.exclude_words를 이어 쓴다


def test_travel_limit_lives_on_the_intent():
    from app.services.travel_limit import apply_travel_limit

    intent = UserIntent(intent="recommend_place", location="부산역", requests=[PlaceRequest(query="카페")])
    timed = apply_travel_limit(intent, "부산역에서 걸어서 5분 이내 카페 추천해줘")
    assert (timed.transport_mode, timed.max_travel_minutes) == ("walk", 5)
    assert apply_travel_limit(timed, "다른 곳 더") is timed  # 후속 질문에서도 그대로


def test_exclusion_is_saved_with_the_search_and_cleared_by_a_new_one():
    from fastapi.testclient import TestClient

    from app import main

    with patch.object(main.settings, "use_mock_places", True), patch.object(main.settings, "supabase_url", None):
        with TestClient(main.app) as client:
            send = lambda m: client.post("/api/v1/recommend",
                                         json={"message": m, "session_id": "t-excl", "mode": "recommend"}).json()
            send("서면 카페 추천해줘")
            reply = send("광장점 빼고 추천해줘")
            names = [p["name"] for r in reply["results"] for p in r["places"]]
            assert names and not any("광장점" in n for n in names)
            assert main.app.state.sessions.get("t-excl").last_search.exclude_words == ["광장점"]
            send("해운대로 바꿔줘")  # 같은 종류를 이어 찾는 동안은 유지
            assert main.app.state.sessions.get("t-excl").last_search.exclude_words == ["광장점"]
            send("남포동 맛집 추천해줘")  # 새 검색이면 비운다
            assert main.app.state.sessions.get("t-excl").last_search.exclude_words == []


def test_weather_explicit_date_and_unsupported_region():
    from app.chat.conversation import resolve_weather_place

    assert weather_day("11월 27일 해운대 날씨", MONDAY) == ("11월 27일", date(2026, 11, 27))
    assert weather_day("3일 날씨", MONDAY) == ("12월 3일", date(2026, 12, 3))  # 지난 날이면 다음 달
    assert "없는 날짜" in weather_day("2월 30일 날씨", MONDAY)

    async def resolve_text(text):
        found = text.startswith("대항항")
        return SimpleNamespace(latitude=35.01 if found else None, longitude=128.83 if found else None)

    ctx = SimpleNamespace(pipeline=SimpleNamespace(location_resolver=SimpleNamespace(resolve_text=resolve_text)))
    run = lambda m: asyncio.run(resolve_weather_place(ctx, m))
    assert "부산 안의 지역 날씨만" in run("내일 서울에 비 와?")
    assert run("내일 해운대에 비 와?")[0] == "해운대"
    assert run("대항항 날씨 어때?") == ("대항항", (35.01, 128.83))
    assert "찾지 못해" in run("없는섬 날씨 어때?")
    assert run("오늘 날씨 어때?")[0].startswith("부산(")  # 지역을 말하지 않았을 때만 시청 부근


def test_room_names_fall_back_to_the_building():
    import time as _time

    from app.providers import rest_days

    assert rest_days.lookup_names("동래문화회관 전시실") == ["동래문화회관 전시실", "동래문화회관"]
    assert rest_days.lookup_names("을숙도문화회관 제1전시실")[-1] == "을숙도문화회관"
    # 예전 방식에서 못 찾은 이름은 다시 찾는다. 이번 방식으로 못 찾은 이름은 7일 동안 다시 찾지 않는다.
    rest_days._cache = {"동래문화회관전시실": {"rest": None, "at": _time.time()},
                        "갤러리조이": {"rest": None, "at": _time.time(), "v": rest_days.CACHE_VERSION}}
    assert rest_days.cached_rest("동래문화회관 전시실") == (False, None)
    assert rest_days.cached_rest("갤러리 조이") == (True, None)


def test_weather_errors_never_carry_the_service_key():
    import httpx

    from app.config import settings
    from app.providers.weather import WeatherProvider, get_forecast
    from app.services.safe_error import public_error, redact

    secret = "FAKE+KEY/abc123=="
    url = f"https://apis.data.go.kr/x/getVilageFcst?serviceKey={httpx.QueryParams({'k': secret})['k']}&nx=1"
    assert redact(f"ConnectError: {url}").find("FAKE") == -1
    request = httpx.Request("GET", url)
    error = httpx.HTTPStatusError("bad", request=request, response=httpx.Response(401, request=request))
    assert public_error(error, "단기예보") == "단기예보 HTTP 401"

    class Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            raise httpx.ConnectError(f"failed {url}?serviceKey={params['serviceKey']}")

    provider = WeatherProvider()
    with patch.object(settings, "public_data_api_key", secret), patch.object(settings, "kma_api_url", "https://k/getUltraSrtNcst"), \
            patch("app.providers.weather.httpx.AsyncClient", Boom):
        assert asyncio.run(get_forecast(provider, 35.1, 129.0)) is None
        assert asyncio.run(provider.get_weather(35.1, 129.0)) is None
    assert "FAKE" not in str(provider.status()) and "serviceKey" not in str(provider.status())


def test_public_status_hides_diagnostics():
    from fastapi.testclient import TestClient

    from app import main

    with patch.object(main.settings, "use_mock_places", True), patch.object(main.settings, "supabase_url", None), \
            patch.object(main.settings, "status_token", "letmein"):
        with TestClient(main.app) as client:
            public = client.get("/api/v1/status").json()
            wrong = client.get("/api/v1/status", headers={"X-Status-Token": "nope"}).json()
            full = client.get("/api/v1/status", headers={"X-Status-Token": "letmein"}).json()
    for body in (public, wrong):
        assert body["detail"] is False and "weather" not in body and "tmap" not in body and "query_log" not in body
    assert full["detail"] is True and "weather" in full and "tmap" in full


def test_explicit_date_wins_over_weekday():
    assert weather_day("12월 4일 금요일 해운대 날씨", MONDAY) == ("12월 4일", date(2026, 12, 4))
    assert weather_day("다음 달 27일 날씨", MONDAY) == ("12월 27일", date(2026, 12, 27))
    assert weather_day("2일 뒤 날씨", MONDAY) == ("2일 뒤", date(2026, 11, 25))
    question = weather_day("12월 4일 목요일 날씨", MONDAY)
    assert isinstance(question, str) and "금요일이에요" in question and "어느 날" in question
    assert "지난 날짜" in weather_day("이번 달 20일 날씨", MONDAY)


def test_mismatch_question_is_the_reply():
    async def never(point):
        raise AssertionError("되물을 때는 예보를 부르지 않는다")

    ctx = SimpleNamespace(pipeline=SimpleNamespace(context=SimpleNamespace(forecast=never, weather=never)))
    reply = asyncio.run(weather_reply(ctx, "12월 4일 목요일 해운대 날씨", today=MONDAY))
    assert reply.startswith("12월 4일은 금요일이에요")


def test_empty_followup_keeps_previous_search():
    from fastapi.testclient import TestClient

    from app import main

    with patch.object(main.settings, "use_mock_places", True), patch.object(main.settings, "supabase_url", None):
        with TestClient(main.app) as client:
            send = lambda m: client.post("/api/v1/recommend",
                                         json={"message": m, "session_id": "t-empty", "mode": "recommend"}).json()
            send("서면 카페 추천해줘")
            state = main.app.state.sessions.get("t-empty")
            before = (state.last_search.intent, state.last_search.anchor, state.last_places, state.intent)
            original = main.app.state.pipeline.run

            async def empty(intent, *args, **kwargs):
                response = await original(intent, *args, **kwargs)
                return response.model_copy(update={
                    "results": [r.model_copy(update={"places": []}) for r in response.results]})

            with patch.object(main.app.state.pipeline, "run", empty):
                send("1번 근처 약국")  # 0건
            after = (state.last_search.intent, state.last_search.anchor, state.last_places, state.intent)
            assert after == before  # 직전 검색 조건·기준 좌표·카드 번호를 바꾸지 않는다


def test_public_status_does_not_call_llm():
    from fastapi.testclient import TestClient

    from app import main

    with patch.object(main.settings, "use_mock_places", True), patch.object(main.settings, "supabase_url", None), \
            patch.object(main.settings, "status_token", "letmein"):
        with TestClient(main.app) as client:
            calls = []

            async def status():
                calls.append(1)
                return {"provider": "groq", "reachable": True, "model": "m", "configured": True}

            main.app.state.intent_parser.status = status
            for _ in range(3):
                public = client.get("/api/v1/status").json()
            assert calls == [] and "checked_seconds_ago" in public["llm"]
            client.get("/api/v1/status", headers={"X-Status-Token": "letmein"})
            assert calls == [1]
            assert client.get("/api/v1/status").json()["llm"]["reachable"] is True  # 마지막 확인 결과를 쓴다


def test_llm_summary_count_must_match_results():
    from app.schemas.api import RequestResult
    from app.services.response_writer import counts_match

    places = [Place(name=f"카페{i}", source="kakao") for i in range(19)]
    results = [RequestResult(request=PlaceRequest(query="카페"), places=places, providers={})]
    assert not counts_match("부산에서 카페 5곳을 찾았어요.", results)
    assert counts_match("부산에서 카페 19곳을 찾았어요.", results)
    assert counts_match("부산 곳곳의 카페를 모았어요.", results)
    two = [RequestResult(request=PlaceRequest(query="맛집"), places=places[:5], providers={}),
           RequestResult(request=PlaceRequest(query="카페"), places=places[:3], providers={})]
    assert counts_match("맛집 다섯 곳과 카페 세 곳, 모두 8곳이에요.", two)
    assert not counts_match("맛집 두 곳을 찾았어요.", two)


def test_tags_are_removed_and_category_smalltalk_asks_for_area():
    from fastapi.testclient import TestClient

    from app import main

    with patch.object(main.settings, "use_mock_places", True), patch.object(main.settings, "supabase_url", None):
        with TestClient(main.app) as client:
            class Smalltalk:  # LLM이 짧은 대화로 해석한 경우를 흉내 낸다
                provider_name = "fake"

                async def parse(self, message, previous_intent=None, max_attempts=2):
                    return UserIntent(intent="smalltalk"), 0.0

                async def status(self):
                    return {"provider": "fake"}

            main.app.state.intent_parser = Smalltalk()
            reply = client.post("/api/v1/recommend", json={"message": "<script>alert(1)</script> 카페",
                                                           "session_id": "t-tag", "mode": "recommend"}).json()
            assert "script" not in str(reply.get("message"))
            assert reply["intent"]["intent"] == "clarify"
            after = client.post("/api/v1/recommend", json={"message": "서면", "session_id": "t-tag",
                                                           "mode": "recommend"}).json()
            assert after["intent"]["requests"][0]["query"] == "카페"


def test_blank_message_is_answered_without_llm():
    from fastapi.testclient import TestClient

    from app import main

    with patch.object(main.settings, "use_mock_places", True), patch.object(main.settings, "supabase_url", None):
        with TestClient(main.app) as client:
            reply = client.post("/api/v1/recommend", json={"message": "   ", "session_id": "t-blank"})
    assert reply.status_code == 200 and reply.json()["route"] == "local"



def test_past_weather_never_calls_current_weather_or_forecast():
    async def unexpected(point):
        raise AssertionError("과거 날씨에 관측값이나 예보를 조회하면 안 된다")

    ctx = SimpleNamespace(pipeline=SimpleNamespace(context=SimpleNamespace(forecast=unexpected, weather=unexpected)))
    for query in ("어제 해운대 날씨", "지난 금요일 해운대 날씨", "2일 전 해운대 날씨", "11월 20일 해운대 날씨"):
        reply = asyncio.run(weather_reply(ctx, query, today=MONDAY))
        assert "지난 날짜" in reply
    assert weather_day("1월 2일 해운대 날씨", date(2026, 12, 31)) == ("1월 2일", date(2027, 1, 2))
