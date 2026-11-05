"""외부 DB 연동 계층. 앱의 다른 코드는 이 패키지만 통해 DB에 접근한다.

- supabase_client.SupabaseClient: Supabase REST(PostgREST) 호출. 실패해도 예외를 밖으로 내보내지 않는다.
- query_log.QueryLogger: /api/v1/recommend 요청 한 건을 질의 로그 한 줄로 만들어 저장한다.
"""

from app.db.query_log import QueryLogger
from app.db.supabase_client import SupabaseClient

__all__ = ["QueryLogger", "SupabaseClient"]
