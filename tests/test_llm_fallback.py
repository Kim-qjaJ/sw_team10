"""Groq 한도 초과·장애 시 Ollama 자동 전환."""

import asyncio

import pytest

from app.llm.base import IntentParseError, LLMRateLimitedError, LLMUnavailableError
from app.llm.fallback import FallbackIntentParser
from app.schemas.intent import UserIntent

INTENT = UserIntent(intent="smalltalk")


class Fake:
    def __init__(self, name, error=None):
        self.provider_name = name
        self.error = error
        self.calls = 0

    async def parse(self, message, previous_intent=None, max_attempts=2):
        self.calls += 1
        if self.error:
            raise self.error
        return INTENT, 1.0

    async def status(self):
        return {"provider": self.provider_name}

    async def warm_up(self):
        return None


def run(parser):
    return asyncio.run(parser.parse("남포동 먹을 곳"))


def test_uses_primary_when_ok():
    groq, ollama = Fake("groq"), Fake("ollama")
    parser = FallbackIntentParser(groq, ollama)
    assert run(parser)[0] == INTENT
    assert (groq.calls, ollama.calls, parser.last_provider) == (1, 0, "groq")


def test_rate_limit_switches_and_cools_down():
    groq, ollama = Fake("groq", LLMRateLimitedError("429")), Fake("ollama")
    parser = FallbackIntentParser(groq, ollama, cooldown_seconds=60)
    run(parser)
    run(parser)  # 대기 시간 동안은 Groq를 다시 부르지 않는다
    assert (groq.calls, ollama.calls, parser.last_provider) == (1, 2, "ollama")


def test_unavailable_switches_without_cooldown():
    groq, ollama = Fake("groq", LLMUnavailableError("timeout")), Fake("ollama")
    parser = FallbackIntentParser(groq, ollama)
    run(parser)
    run(parser)
    assert (groq.calls, ollama.calls) == (2, 2)


def test_both_down_raises_primary_error():
    parser = FallbackIntentParser(Fake("groq", LLMRateLimitedError("429")), Fake("ollama", LLMUnavailableError("off")))
    with pytest.raises(LLMRateLimitedError):
        run(parser)


def test_schema_error_is_not_retried_on_fallback():
    groq, ollama = Fake("groq", IntentParseError("bad json")), Fake("ollama")
    with pytest.raises(IntentParseError):
        run(FallbackIntentParser(groq, ollama))
    assert ollama.calls == 0
