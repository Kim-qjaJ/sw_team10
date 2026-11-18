import httpx

from app.config import settings
from app.llm.base import BaseIntentParser
from app.llm.fallback import FallbackIntentParser
from app.llm.groq import GroqIntentParser
from app.llm.ollama import OllamaIntentParser


def create_intent_parser(client: httpx.AsyncClient) -> BaseIntentParser:
    provider = settings.llm_provider.strip().lower()

    if provider == "ollama":
        return OllamaIntentParser(client)

    if provider == "groq":
        groq = GroqIntentParser(client)
        # Groq 한도 초과·장애 시 로컬 Ollama로 자동 전환 (LLM_FALLBACK_PROVIDER=none이면 끈다)
        if settings.llm_fallback_provider.strip().lower() == "ollama":
            return FallbackIntentParser(groq, OllamaIntentParser(client), settings.llm_fallback_cooldown_seconds)
        return groq

    raise ValueError(
        f"지원하지 않는 LLM_PROVIDER={settings.llm_provider!r}. ollama 또는 groq를 사용하세요."
    )
