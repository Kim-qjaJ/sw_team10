"""AI-REQ-03: LLM에 보내는 내용에 좌표가 들어가지 않는다."""

import asyncio
import json

from app.config import settings
from app.services.response_writer import ResponseWriter

RECENT = [
    {"rank": 1, "group_query": "약국", "group_rank": 1, "name": "서면약국", "category": "public_facility",
     "address": "부산 부산진구 중앙대로 1", "phone": "051-000-0000", "distance_m": 120.0,
     "latitude": 35.1578, "longitude": 129.0597, "sources": ["kakao"]},
]


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "답변"}}]}


class RecordingClient:
    def __init__(self):
        self.bodies = []

    async def post(self, url, headers=None, json=None, **kwargs):
        self.bodies.append(json)
        return FakeResponse()


def sent_text(client) -> str:
    return json.dumps(client.bodies, ensure_ascii=False)


def test_place_detail_and_compare_send_no_coordinates(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    client = RecordingClient()
    writer = ResponseWriter(client)
    asyncio.run(writer.write_place_detail("1번 어때?", RECENT))
    asyncio.run(writer.compare("그중 어디가 가까워?", RECENT))
    text = sent_text(client)
    assert len(client.bodies) == 2
    assert "서면약국" in text and "120" in text  # 공개 정보는 보낸다
    for hidden in ("latitude", "longitude", "35.1578", "129.0597", "group_rank"):
        assert hidden not in text, hidden
