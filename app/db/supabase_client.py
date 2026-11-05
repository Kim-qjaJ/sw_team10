"""Supabase REST(PostgREST) 최소 클라이언트.

supabase-py 대신 이미 쓰는 httpx로 /rest/v1 에 직접 요청해 의존성을 늘리지 않는다.
DB 장애가 채팅 응답을 막지 않도록 모든 오류는 기록만 하고 False/None을 반환한다.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class SupabaseClient:
    def __init__(self, client: httpx.AsyncClient, url: str | None, key: str | None, timeout: float = 3.0) -> None:
        self.client = client
        self.url = (url or "").strip().rstrip("/")
        self.key = (key or "").strip()
        self.timeout = timeout
        self.inserted = 0
        self.failed = 0
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        # 새 secret 키(sb_secret_...)는 apikey 헤더로만 보낸다.
        # 예전 service_role 키(JWT, 'eyJ'로 시작)는 Authorization 헤더도 함께 보낸다.
        headers = {"apikey": self.key, "Content-Type": "application/json"}
        if self.key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self.key}"
        if prefer:
            headers["Prefer"] = prefer
        return headers

    async def insert(self, table: str, rows: list[dict]) -> bool:
        """rows를 table에 추가한다. 성공하면 True."""
        if not self.enabled or not rows:
            return False
        try:
            response = await self.client.post(
                f"{self.url}/rest/v1/{table}", headers=self._headers("return=minimal"),
                json=rows, timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._fail(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
            return False
        except httpx.HTTPError as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            return False
        self.inserted += len(rows)
        return True

    async def select(self, table: str, params: dict[str, str]) -> list[dict] | None:
        """PostgREST 조회 (예: {"select": "term,occurrences", "order": "occurrences.desc"})."""
        if not self.enabled:
            return None
        try:
            response = await self.client.get(f"{self.url}/rest/v1/{table}", headers=self._headers(), params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            return None

    def _fail(self, message: str) -> None:
        self.failed += 1
        self.last_error = message
        logger.warning("Supabase 요청 실패: %s", message)

    def status(self) -> dict:
        # 키 값은 노출하지 않고 설정 여부만 보여준다.
        return {"enabled": self.enabled, "inserted": self.inserted, "failed": self.failed, "last_error": self.last_error}
