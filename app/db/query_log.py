"""질의 로그: 어떤 질문이 들어왔고 어떤 경로로 처리됐는지 한 줄로 저장한다.

저장 목적 (SRS DATA-09, 잠정안)
- Local Router 사전에 없던 단어(unknown_terms)를 모아 사전을 보강한다.
- 처리 경로(local / llm / llm_fallback / api)와 결과 수를 보고 해석 품질을 점검한다.

개인정보 (SRS NFR-003)
- 질의 문장과 처리 경로 중심으로만 저장한다. 세션 ID와 위도·경도는 저장하지 않는다.
- 하루 1회 일괄 처리한 뒤 삭제해 1일을 넘겨 보관하지 않는다. (db/supabase_schema.sql의 삭제 작업 참고)
"""

from __future__ import annotations

from app.db.supabase_client import SupabaseClient
from app.schemas.api import ChatRequest, RecommendResponse
from app.services.local_router import unknown_terms

TABLE = "query_logs"


class QueryLogger:
    def __init__(self, db: SupabaseClient) -> None:
        self.db = db

    @staticmethod
    def build(request: ChatRequest, response: RecommendResponse) -> dict:
        return {
            "message": request.message,
            "route": response.route,
            "mode": request.mode,
            "intent": response.intent.intent if response.intent else None,
            "unknown_terms": unknown_terms(request.message),
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
