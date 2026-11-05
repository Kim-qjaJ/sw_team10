"""Supabase 질의 로그 테스트. 실제 Supabase에는 연결하지 않는다 (httpx MockTransport).

python -m unittest tests.test_query_log
"""

import json
import unittest

import httpx

from app.db import QueryLogger, SupabaseClient
from app.schemas.api import ChatRequest, RecommendResponse, RequestResult
from app.schemas.intent import PlaceRequest, UserIntent
from app.schemas.place import Place


def recorder(status=201):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="" if status < 400 else '{"message":"denied"}')

    return calls, httpx.AsyncClient(transport=httpx.MockTransport(handler))


def sample_response():
    intent = UserIntent(intent="recommend_place", location="부산 서면",
                        requests=[PlaceRequest(query="마라탕", category="restaurant")])
    place = Place(name="서면 마라탕", latitude=35.1577, longitude=129.0596)
    return RecommendResponse(intent=intent, route="llm",
                             results=[RequestResult(request=intent.requests[0], places=[place], providers={})],
                             timing_ms={"intent_llm_ms": 812.5, "total_ms": 1290.0})


class QueryLoggerTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_without_url_or_key(self):
        calls, client = recorder()
        logger = QueryLogger(SupabaseClient(client, None, None))
        self.assertFalse(await logger.log(ChatRequest(message="서면 카페"), sample_response()))
        self.assertEqual(calls, [])
        await client.aclose()

    async def test_record_has_message_and_route_without_session_or_coordinates(self):
        request = ChatRequest(message="서면 감성 마라탕", session_id="secret-session", latitude=35.15, longitude=129.05)
        record = QueryLogger.build(request, sample_response())
        self.assertEqual(record["message"], "서면 감성 마라탕")
        self.assertEqual(record["route"], "llm")
        self.assertEqual(record["result_count"], 1)
        self.assertIn("감성", record["unknown_terms"])
        # NFR-003: 세션 ID와 좌표는 어떤 형태로도 저장하지 않는다.
        dumped = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("secret-session", dumped)
        self.assertNotIn("35.15", dumped)
        self.assertFalse({"session_id", "session_hash", "latitude", "longitude"} & record.keys())

    async def test_insert_sends_secret_key_header(self):
        calls, client = recorder()
        db = SupabaseClient(client, "https://example.supabase.co/", "sb_secret_test")
        self.assertTrue(await QueryLogger(db).log(ChatRequest(message="서면 카페"), sample_response()))
        self.assertEqual(len(calls), 1)
        self.assertEqual(str(calls[0].url), "https://example.supabase.co/rest/v1/query_logs")
        self.assertEqual(calls[0].headers["apikey"], "sb_secret_test")
        self.assertNotIn("authorization", calls[0].headers)
        self.assertEqual(db.status()["inserted"], 1)
        await client.aclose()

    async def test_failure_is_recorded_not_raised(self):
        calls, client = recorder(status=401)
        db = SupabaseClient(client, "https://example.supabase.co", "sb_secret_test")
        self.assertFalse(await QueryLogger(db).log(ChatRequest(message="서면 카페"), sample_response()))
        status = db.status()
        self.assertEqual(status["failed"], 1)
        self.assertIn("401", status["last_error"])
        # 키 값은 상태 정보에 드러나지 않는다.
        self.assertNotIn("sb_secret_test", json.dumps(status))
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
