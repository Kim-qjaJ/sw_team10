import httpx

from app.config import settings
from app.llm.base import BaseIntentParser
from app.llm.groq import GroqIntentParser
from app.llm.ollama import OllamaIntentParser


def create_intent_parser(client: httpx.AsyncClient) -> BaseIntentParser:
    provider = settings.llm_provider.strip().lower()

    if provider == "ollama":
        return OllamaIntentParser(client)

    if provider == "groq":
        return GroqIntentParser(client)

    raise ValueError(
        f"지원하지 않는 LLM_PROVIDER={settings.llm_provider!r}. ollama 또는 groq를 사용하세요."
    )
