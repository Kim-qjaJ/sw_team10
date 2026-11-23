"""질의 로그: 어떤 질문이 들어왔고 어떤 경로로 처리됐는지 한 줄로 저장한다.

저장 목적 (SRS DATA-09, 잠정안)
- Local Router 사전에 없던 단어(unknown_terms)를 모아 사전을 보강한다.
- 처리 경로(local / llm / llm_fallback / api)와 결과 수를 보고 해석 품질을 점검한다.

개인정보 (SRS NFR-003)
- 저장 항목: 질의 문장, 처리 경로(route), 화면에서 고른 방식(mode), 해석된 의도(intent), 사전에 없는 단어, 결과 수.
- 세션 ID와 위도·경도는 저장하지 않는다. 사용자가 문장에 좌표('35.1578, 129.0597')를 직접 적으면 '[좌표]'로 바꿔 저장한다.
- 하루 1회 일괄 처리한 뒤 삭제해 1일을 넘겨 보관하지 않는다. (db/supabase_schema.sql의 삭제 작업 참고)
"""

from __future__ import annotations

import re

from app.db.supabase_client import SupabaseClient
from app.schemas.api import ChatRequest, RecommendResponse
from app.services.local_router import unknown_terms

TABLE = "query_logs"
# 소수점 아래 3자리 이상인 위도·경도 모양의 숫자 (예: 35.1578, 129.05970)
COORDINATE = re.compile(r"(?<![\d.])-?\d{1,3}\.\d{3,}(?![\d.])")


def mask_coordinates(message: str) -> str:
    return COORDINATE.sub("[좌표]", message)


class QueryLogger:
    def __init__(self, db: SupabaseClient) -> None:
        self.db = db

    @staticmethod
    def build(request: ChatRequest, response: RecommendResponse) -> dict:
        message = mask_coordinates(request.message)
        return {
            "message": message,
            "route": response.route,
            "mode": request.mode,
            "intent": response.intent.intent if response.intent else None,
            "unknown_terms": unknown_terms(message),
            "result_count": sum(len(result.places) for result in response.results),
        }

    async def log(self, request: ChatRequest, response: RecommendResponse) -> bool:
        """응답을 보낸 뒤 백그라운드에서 호출한다. 실패해도 예외를 내보내지 않는다."""
        if not self.db.enabled:
            return False
        try:
            record = self.build(request, response)
        except Exception as exc:  # noqa: BLE001 - 로그 실패가 서비스에 영향을 주지 않게 한다.
            self.db._fail(f"record build: {type(exc).__name__}: {exc}")
            return False
        return await self.db.insert(TABLE, [record])
