import json
import os
from pathlib import Path
from urllib import request

from app.schemas.intent import UserIntent


class LLMService:
    def __init__(self):
        self.ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        self.model = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
        prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "intent.md"
        self.system_prompt = prompt_path.read_text(encoding="utf-8")

    def parse_intent(self, user_text: str) -> UserIntent:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_text},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }

        req = request.Request(
            f"{self.ollama_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))

        content = result["message"]["content"]
        return UserIntent.model_validate_json(content)
