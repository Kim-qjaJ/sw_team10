"""기존 import 경로 호환용 모듈.

새 코드는 app.llm.factory.create_intent_parser를 사용한다.
"""

from app.llm.base import BaseIntentParser, IntentParseError, LLMUnavailableError
from app.llm.factory import create_intent_parser
from app.llm.groq import GroqIntentParser
from app.llm.ollama import OllamaIntentParser

__all__ = [
    "BaseIntentParser",
    "IntentParseError",
    "LLMUnavailableError",
    "OllamaIntentParser",
    "GroqIntentParser",
    "create_intent_parser",
]
