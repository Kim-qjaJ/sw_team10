"""LLM 장애 처리 표 (SRS NFR-002·AI-REQ-02, SDD 7.4)를 경우별로 확인한다. 실제 LLM 호출 없음.

| 경우                                  | 다음 동작                         | 사전으로 지역·종류 확인됨 | 확인 안 됨          |
| Groq 429                              | Ollama로 해석 (60초간 Groq 생략) | -                          | -                   |
| Groq 8초 무응답·연결 실패·5xx         | Ollama로 해석                    | -                          | -                   |
| Groq·Ollama 모두 실패                 | -                                 | 기본 검색 200(llm_fallback) | 503 llm_unavailable / llm_rate_limited |
| 해석 전체 20초 초과                    | -                                 | 기본 검색 200(llm_fallback) | 504 llm_timeout     |
| 스키마 불일치(2회 시도 후)             | Ollama로 넘기지 않음             | 기본 검색 200(llm_fallback) | 422 intent_parse_failed |
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.chat import intents
from app.config import settings
from app.llm.base import IntentParseError, LLMRateLimitedError, LLMUnavailableError
from app.llm.fallback import FallbackIntentParser
from app.schemas.intent import PlaceRequest, UserIntent

INTENT = UserIntent(intent="recommend_place", location="부산 서면", requests=[PlaceRequest(query="카페", category="cafe")])


class Parser:
    def __init__(self, name, error=None, delay=0.0):
        self.provider_name, self.error, self.delay, self.calls = name, error, delay, 0

    async def parse(self, message, previous_intent=None, max_attempts=2):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return INTENT, self.delay * 1000

    async def status(self):
        return {"provider": self.provider_name}

    async def warm_up(self):
        return None


def ctx_with(parser):
    return SimpleNamespace(sessions=SimpleNamespace(get=lambda _sid: None, save_search_intent=lambda *a: None),
                           intent_parser=parser)


def test_groq_no_answer_switches_to_ollama_inside_intent_budget(monkeypatch):
    """Groq가 응답하지 않으면 llm_primary_timeout 뒤 Ollama로 넘기고, 해석 전체 제한 안에 끝난다."""
    monkeypatch.setattr(settings, "llm_primary_timeout_seconds", 0.1)
    monkeypatch.setattr(settings, "llm_intent_timeout_seconds", 1.0)
    groq, ollama = Parser("groq", delay=30), Parser("ollama", delay=0.05)
    parser = FallbackIntentParser(groq, ollama)
    started = time.perf_counter()
    intent, _ = asyncio.run(intents.parse_intent(ctx_with(parser), "서면 감성 넘치는 카페"))
    assert time.perf_counter() - started < 0.5
    assert intent.requests[0].category == "cafe" and parser.last_provider == "ollama"
    assert "응답하지 않았습니다" in parser.last_fallback_reason


@pytest.mark.parametrize("error, status, code", [
    (LLMUnavailableError("연결 실패"), 503, "llm_unavailable"),
    (LLMRateLimitedError("429"), 503, "llm_rate_limited"),
])
def test_both_llms_fail_gives_503_with_reason(error, status, code):
    parser = FallbackIntentParser(Parser("groq", error), Parser("ollama", LLMUnavailableError("꺼짐")))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(intents.parse_intent(ctx_with(parser), "조용하고 감성적인 곳"))
    assert (caught.value.status_code, caught.value.detail["code"]) == (status, code)


def test_whole_intent_budget_exceeded_gives_504(monkeypatch):
    monkeypatch.setattr(settings, "llm_primary_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "llm_intent_timeout_seconds", 0.2)
    parser = FallbackIntentParser(Parser("groq", delay=30), Parser("ollama", delay=30))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(intents.parse_intent(ctx_with(parser), "조용하고 감성적인 곳"))
    assert (caught.value.status_code, caught.value.detail["code"]) == (504, "llm_timeout")


def test_schema_error_gives_422_without_ollama():
    ollama = Parser("ollama")
    parser = FallbackIntentParser(Parser("groq", IntentParseError("bad json")), ollama)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(intents.parse_intent(ctx_with(parser), "조용하고 감성적인 곳"))
    assert caught.value.status_code == 422 and ollama.calls == 0


@pytest.mark.parametrize("message, expected", [
    ("서면 감성 넘치는 카페", 200),   # 사전으로 서면·카페 확인 → 기본 검색
    ("조용하고 감성적인 곳", 503),    # 확인된 조건 없음 → 원인 안내
])
def test_chat_api_falls_back_to_basic_search_or_reason(message, expected):
    from fastapi.testclient import TestClient

    from app import main

    failing = FallbackIntentParser(Parser("groq", LLMUnavailableError("연결 실패")),
                                   Parser("ollama", LLMUnavailableError("꺼짐")))
    with patch.object(main.settings, "use_mock_places", True), \
            patch.object(main.settings, "supabase_url", None):
        with TestClient(main.app) as client:
            main.app.state.intent_parser = failing
            response = client.post("/api/v1/recommend", json={"message": message, "session_id": "t-llm"})
    assert response.status_code == expected
    if expected == 200:
        assert response.json()["route"] == "llm_fallback"
    else:
        assert response.json()["detail"]["code"] == "llm_unavailable"
