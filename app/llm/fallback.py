"""기본 LLM(Groq)이 호출 한도·장애로 실패하면 로컬 LLM(Ollama)으로 자동 전환한다.

- 한도 초과(429)·시간 초과·연결 실패일 때만 넘긴다. 시간 초과는 llm_primary_timeout_seconds(기본 8초)로 판단해
  해석 전체 제한(llm_intent_timeout_seconds, 20초) 안에 Ollama가 쓸 시간을 남긴다. 스키마 검증 실패(IntentParseError)는 그대로 올린다.
- 한도 초과가 나면 잠시(cooldown) 기본 LLM을 건너뛰고 바로 로컬 LLM을 쓴다. 한도가 풀리기 전에 계속 두드리지 않기 위해서다.
- 두 LLM 모두 실패하면 기본 LLM의 오류를 그대로 올려 기존 안내(503)를 유지한다.
"""

import asyncio
import logging
import time

from app.config import settings
from app.llm.base import BaseIntentParser, LLMRateLimitedError, LLMUnavailableError
from app.schemas.intent import UserIntent
from app.services.safe_error import redact

logger = logging.getLogger(__name__)


class FallbackIntentParser:
    provider_name = "fallback"

    def __init__(self, primary: BaseIntentParser, fallback: BaseIntentParser, cooldown_seconds: float = 60.0) -> None:
        self.primary = primary
        self.fallback = fallback
        self.cooldown_seconds = cooldown_seconds
        self._skip_primary_until = 0.0
        self.fallback_count = 0
        self.last_provider: str | None = None
        self.last_fallback_reason: str | None = None

    def _primary_cooling(self) -> bool:
        return time.monotonic() < self._skip_primary_until

    async def parse(self, message: str, previous_intent: UserIntent | None = None, max_attempts: int = 2):
        primary_error: LLMUnavailableError | None = None
        if not self._primary_cooling():
            try:
                result = await asyncio.wait_for(
                    self.primary.parse(message, previous_intent=previous_intent, max_attempts=max_attempts),
                    timeout=settings.llm_primary_timeout_seconds,
                )
                self.last_provider = self.primary.provider_name
                return result
            except LLMRateLimitedError as exc:
                primary_error = exc
                self._skip_primary_until = time.monotonic() + self.cooldown_seconds
            except LLMUnavailableError as exc:
                primary_error = exc
            except asyncio.TimeoutError:
                primary_error = LLMUnavailableError(
                    f"{self.primary.provider_name}가 {settings.llm_primary_timeout_seconds:g}초 안에 응답하지 않았습니다."
                )
        else:
            primary_error = LLMRateLimitedError(f"{self.primary.provider_name} 호출 한도 대기 중")

        self.last_fallback_reason = redact(str(primary_error)[:200])
        logger.warning("%s 사용 불가(%s) → %s로 전환", self.primary.provider_name, primary_error, self.fallback.provider_name)
        try:
            result = await self.fallback.parse(message, previous_intent=previous_intent, max_attempts=max_attempts)
        except LLMUnavailableError as fallback_error:
            logger.warning("%s도 사용 불가: %s", self.fallback.provider_name, fallback_error)
            raise primary_error from fallback_error
        self.fallback_count += 1
        self.last_provider = self.fallback.provider_name
        return result

    async def status(self) -> dict:
        primary = await self.primary.status()
        fallback = await self.fallback.status()
        return {
            **primary,
            "fallback": {
                **fallback,
                "used_count": self.fallback_count,
                "primary_paused_seconds": max(0, round(self._skip_primary_until - time.monotonic())),
                "last_reason": self.last_fallback_reason,
            },
        }

    async def warm_up(self) -> None:
        # 대체 LLM도 미리 메모리에 올려 두어 첫 전환 때 모델을 읽느라 늦어지지 않게 한다.
        await self.primary.warm_up()
        await self.fallback.warm_up()
