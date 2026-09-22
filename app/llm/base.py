import time
from abc import ABC, abstractmethod

import httpx
from pydantic import ValidationError

from app.config import BASE_DIR
from app.schemas.intent import UserIntent

PROMPT_PATH = BASE_DIR / "prompts" / "intent.md"


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    return text.strip()


class LLMUnavailableError(Exception):
    """선택한 LLM Provider를 사용할 수 없거나 호출이 실패한 경우."""


class IntentParseError(Exception):
    """LLM 응답이 UserIntent 스키마를 만족하지 못한 경우."""


class BaseIntentParser(ABC):
    provider_name = "base"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        self.schema = UserIntent.model_json_schema()

    async def parse(self, message: str, max_attempts: int = 2) -> tuple[UserIntent, float]:
        started = time.perf_counter()
        last_error: Exception | None = None

        for _ in range(max_attempts):
            content = _strip_code_fence(await self._chat(message))
            try:
                intent = UserIntent.model_validate_json(content)
                return intent, round((time.perf_counter() - started) * 1000, 2)
            except ValidationError as exc:
                last_error = exc

        raise IntentParseError(str(last_error))

    @abstractmethod
    async def _chat(self, message: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def status(self) -> dict:
        raise NotImplementedError

    async def warm_up(self) -> None:
        """Provider별로 필요할 때만 오버라이드한다."""
        return None
