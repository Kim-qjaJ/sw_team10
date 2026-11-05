"""반경 단계, 날씨 언급, 날씨 캐시, 응답 시간 상한 테스트. 외부 API·LLM 없이 돈다.

python -m unittest tests.test_weather_timing
"""

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.chat import intents
from app.chat.timing import remaining_budget
from app.config import settings
from app.providers import weather as weather_module
from app.schemas.api import RequestResult
from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place
from app.services.followup import EXPAND_STEPS_M, MAX_EXPAND_M, next_radius
from app.services.mode_router import NEARBY_RADII_M
from app.services.pipeline import apply_rain_hint
from app.services.response_writer import ResponseWriter, weather_sentence, with_weather


def result(category="cafe", subcategory=None, result_type="recommend", places=1):
    request = PlaceRequest(query="카페", category=category, subcategory=subcategory)
    return RequestResult(request=request, places=[Place(name=f"장소{i}") for i in range(places)],
                         providers={}, result_type=result_type)


KMA_RAIN = {"is_raining": True, "precipitation": "rain", "temperature": 12.0, "source": "kma"}
KMA_CLEAR = {"is_raining": False, "precipitation": None, "temperature": 18.5, "source": "kma"}


class RadiusTest(unittest.TestCase):
    def test_nearby_radii_follow_srs(self):
        self.assertEqual(NEARBY_RADII_M, (1500, 3000, 5000))

    def test_expand_steps_stop_at_5km(self):
        self.assertEqual(EXPAND_STEPS_M, (1500, 3000, 5000))
        self.assertEqual(MAX_EXPAND_M, 5000)
        self.assertEqual(next_radius(None), 3000)  # 기본 1.5km에서 넓히면 3km
        self.assertEqual(next_radius(1500), 3000)
        self.assertEqual(next_radius(3000), 5000)
        self.assertIsNone(next_radius(5000))


class WeatherSentenceTest(unittest.TestCase):
    def test_rain_is_always_mentioned_for_scored_results(self):
        sentence = weather_sentence(KMA_RAIN, [result()], "부산 서면")
        self.assertEqual(sentence, "지금 부산 서면에 비가 와서 실내 장소를 먼저 보여드려요.")

    def test_snow_uses_snow_word(self):
        sentence = weather_sentence({**KMA_RAIN, "precipitation": "snow"}, [result()])
        self.assertIn("눈이 와서", sentence)

    def test_user_said_rain_is_not_reported_as_observed(self):
        weather = apply_rain_hint(KMA_CLEAR, True)
        self.assertTrue(weather["is_raining"])
        self.assertEqual(weather_sentence(weather, [result()]), "비가 온다고 하셔서 실내 장소를 먼저 보여드려요.")

    def test_observed_rain_is_kept_when_user_also_says_rain(self):
        self.assertEqual(apply_rain_hint(KMA_RAIN, True), KMA_RAIN)
        self.assertIsNone(apply_rain_hint(None, False))
        self.assertTrue(apply_rain_hint(None, True)["user_hint"])

    def test_clear_weather_only_for_outdoor_requests(self):
        self.assertIsNone(weather_sentence(KMA_CLEAR, [result("cafe")]))
        self.assertEqual(weather_sentence(KMA_CLEAR, [result("tourism")]),
                         "지금 기온은 18.5°C이고 비가 오지 않아 야외 장소도 함께 골랐어요.")
        self.assertIsNotNone(weather_sentence(KMA_CLEAR, [result("activity", subcategory="park")]))

    def test_no_weather_or_unscored_results_means_no_mention(self):
        self.assertIsNone(weather_sentence(None, [result()]))
        self.assertIsNone(weather_sentence(KMA_RAIN, [result(result_type="search")]))
        self.assertIsNone(weather_sentence(KMA_RAIN, [result(places=0)]))

    def test_template_summary_appends_weather(self):
        intent = UserIntent(intent="recommend_place", location="부산 서면",
                            requests=[PlaceRequest(query="카페", category="cafe")])
        summary = ResponseWriter.template_summary(intent, [result()], KMA_RAIN)
        self.assertTrue(summary.startswith("부산 서면에서 카페 1곳을 찾아 점수순으로 정리했어요."))
        self.assertTrue(summary.endswith("실내 장소를 먼저 보여드려요."))
        self.assertNotIn("비가", ResponseWriter.template_summary(intent, [result()], None))

    def test_with_weather_does_not_duplicate(self):
        once = with_weather("카페 3곳을 찾았어요.", KMA_RAIN, [result()])
        self.assertEqual(with_weather(once, KMA_RAIN, [result()]), once)
        self.assertIsNone(with_weather(None, KMA_RAIN, [result()]))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        FakeClient.calls += 1
        items = [{"category": "PTY", "obsrValue": "1"}, {"category": "T1H", "obsrValue": "11.2"}]
        return FakeResponse({"response": {"body": {"items": {"item": items}}}})


class WeatherProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_grid_and_hour_is_cached(self):
        FakeClient.calls = 0
        provider = weather_module.WeatherProvider()
        with patch.object(weather_module.httpx, "AsyncClient", FakeClient), \
                patch.object(weather_module.settings, "public_data_api_key", "test-key"), \
                patch.object(weather_module.settings, "kma_api_url", "https://example.test/kma"):
            first = await provider.get_weather(35.1796, 129.0756)
            second = await provider.get_weather(35.1797, 129.0757)  # 같은 격자
        self.assertEqual(FakeClient.calls, 1)
        self.assertEqual(first, second)
        self.assertTrue(first["is_raining"])
        self.assertEqual(first["precipitation"], "rain")
        self.assertEqual(first["temperature"], 11.2)

    async def test_missing_key_returns_none(self):
        provider = weather_module.WeatherProvider()
        with patch.object(weather_module.settings, "public_data_api_key", None):
            self.assertIsNone(await provider.get_weather(35.18, 129.07))


class TimingTest(unittest.IsolatedAsyncioTestCase):
    def test_remaining_budget_never_negative(self):
        self.assertGreater(remaining_budget(time.perf_counter()), settings.request_timeout_seconds - 2)
        self.assertEqual(remaining_budget(time.perf_counter() - 10_000), 0.0)

    async def test_slow_llm_intent_becomes_504(self):
        async def slow_parse(message, previous_intent=None):
            await asyncio.sleep(1)

        ctx = SimpleNamespace(
            sessions=SimpleNamespace(get=lambda _session_id: None),
            intent_parser=SimpleNamespace(parse=slow_parse),
        )
        with patch.object(intents.settings, "llm_intent_timeout_seconds", 0.05):
            with self.assertRaises(HTTPException) as caught:
                await intents.parse_intent(ctx, "조용하고 감성적인 곳", None)
        self.assertEqual(caught.exception.status_code, 504)
        self.assertEqual(caught.exception.detail["code"], "llm_timeout")


class RequestTimeoutTest(unittest.TestCase):
    def test_recommend_returns_notice_when_request_takes_too_long(self):
        from fastapi.testclient import TestClient

        from app import main

        async def slow_chat(state, request):
            await asyncio.sleep(1)

        with patch.object(main, "handle_chat", slow_chat), \
                patch.object(main.settings, "request_timeout_seconds", 0.05):
            with TestClient(main.app) as client:
                response = client.post("/api/v1/recommend", json={"message": "서면 카페 추천해줘"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["results"], [])
        self.assertIn("응답이 늦어져", body["notice"])


if __name__ == "__main__":
    unittest.main()
