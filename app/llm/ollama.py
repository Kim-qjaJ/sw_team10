import httpx

from app.config import settings
from app.llm.base import BaseIntentParser, LLMUnavailableError


class OllamaIntentParser(BaseIntentParser):
    provider_name = "ollama"

    async def _chat(self, message: str) -> str:
        payload = {
            "model": settings.ollama_model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": message},
            ],
            "format": self.schema if settings.ollama_structured_output else "json",
            "stream": False,
            "keep_alive": settings.ollama_keep_alive,
            "options": {"temperature": 0},
        }
        try:
            response = await self.client.post(
                f"{settings.ollama_url.rstrip('/')}/api/chat",
                json=payload,
                timeout=settings.ollama_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError(
                f"Ollama가 {settings.ollama_timeout_seconds:g}초 안에 응답하지 않았습니다."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailableError(
                f"Ollama 오류 {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Ollama 서버({settings.ollama_url})에 연결할 수 없습니다. `ollama serve` 실행 여부를 확인하세요."
            ) from exc

        return response.json().get("message", {}).get("content", "")

    async def status(self) -> dict:
        try:
            response = await self.client.get(
                f"{settings.ollama_url.rstrip('/')}/api/tags",
                timeout=3,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return {
                "provider": self.provider_name,
                "reachable": False,
                "model": settings.ollama_model,
                "model_installed": False,
            }

        names = {model.get("name") for model in response.json().get("models", [])}
        wanted = {settings.ollama_model, f"{settings.ollama_model}:latest"}
        return {
            "provider": self.provider_name,
            "reachable": True,
            "model": settings.ollama_model,
            "model_installed": bool(names & wanted),
        }

    async def warm_up(self) -> None:
        """서버 시작 시 모델을 미리 메모리에 올려 첫 요청 지연을 줄인다."""
        try:
            await self.client.post(
                f"{settings.ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "messages": [],
                    "keep_alive": settings.ollama_keep_alive,
                },
                timeout=settings.ollama_timeout_seconds,
            )
        except httpx.HTTPError:
            pass
