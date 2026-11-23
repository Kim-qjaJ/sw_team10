"""외부 API 오류를 상태 응답·로그에 남길 때 인증키가 섞이지 않게 정리한다 (2026-11-23 검토).

예외 문자열에는 요청 주소가 들어갈 수 있고, 공공데이터 API는 주소에 serviceKey를 넣는다.
그래서 오류는 '종류 + 상태 코드'만 남기고, 혹시 남는 글자에서도 키 값과 주소를 지운다.
"""

import re

import httpx

from app.config import settings

_SECRET_NAMES = (
    "public_data_api_key", "groq_api_key", "kakao_rest_api_key", "naver_client_id", "naver_client_secret",
    "tmap_api_key", "tmap_transit_api_key", "supabase_secret_key",
)
_KEY_PARAM = re.compile(r"(?i)(service_?key|api_?key|appkey|key|token|secret)=[^&\s'\"]+")
_URL = re.compile(r"https?://\S+")


def redact(text: str | None) -> str | None:
    """키 값·주소를 지운 문자열."""
    if not text:
        return text
    secrets = [getattr(settings, name, None) for name in _SECRET_NAMES]
    secrets.append(settings.public_data_service_key)
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
            # 주소 안에서는 키가 퍼센트 인코딩되어 있을 수 있다.
            text = text.replace(httpx.QueryParams({"k": secret}).get("k"), "***")
            text = text.replace(str(httpx.QueryParams({"k": secret}))[2:], "***")
    text = _KEY_PARAM.sub(lambda m: f"{m.group(1)}=***", text)
    return _URL.sub("[주소 생략]", text)


def public_error(exc: BaseException, label: str = "") -> str:
    """예외를 '종류 + 상태 코드'로만 요약한다. 예외 메시지(주소 포함 가능)는 쓰지 않는다."""
    prefix = f"{label} " if label else ""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{prefix}HTTP {exc.response.status_code}"
    return f"{prefix}{type(exc).__name__}"
