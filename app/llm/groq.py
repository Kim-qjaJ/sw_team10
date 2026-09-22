import httpx

from app.config import settings
from app.llm.base import BaseIntentParser, LLMUnavailableError


class GroqIntentParser(BaseIntentParser):
    provider_name = "groq"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.groq_api_key or ''}",
            "Content-Type": "application/json",
        }

    async def _chat(self, message: str) -> str:
        if not settings.groq_api_key:
            raise LLMUnavailableError(
                "GROQ_API_KEY가 없습니다. .env에 키를 넣거나 LLM_PROVIDER=ollama로 변경하세요."
            )

        response_format: dict = {"type": "json_object"}
        if settings.groq_structured_output:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "user_intent",
                    "strict": False,
                    "schema": self.schema,
                },
            }

        payload = {
            "model": settings.groq_model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": message},
            ],
            "temperature": 0,
            "response_format": response_format,
        }

        try:
            response = await self.client.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers=self._headers,
                json=payload,
                timeout=settings.groq_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError(
                f"Groq가 {settings.groq_timeout_seconds:g}초 안에 응답하지 않았습니다."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailableError(
                f"Groq 오류 {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError("Groq API에 연결할 수 없습니다.") from exc

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailableError("Groq 응답에 choices가 없습니다.")

        return choices[0].get("message", {}).get("content", "")

    async def status(self) -> dict:
        if not settings.groq_api_key:
            return {
                "provider": self.provider_name,
                "reachable": False,
                "model": settings.groq_model,
                "model_installed": False,
                "configured": False,
            }

        try:
            response = await self.client.get(
                f"{settings.groq_base_url.rstrip('/')}/models",
                headers=self._headers,
                timeout=3,
            )
            response.raise_for_status()
            model_ids = {item.get("id") for item in response.json().get("data", [])}
        except httpx.HTTPError:
            return {
                "provider": self.provider_name,
                "reachable": False,
                "model": settings.groq_model,
                "model_installed": False,
                "configured": True,
            }

        return {
            "provider": self.provider_name,
            "reachable": True,
            "model": settings.groq_model,
            "model_installed": settings.groq_model in model_ids,
            "configured": True,
        }
