"""요청 단위 응답 시간 관리 (SRS NFR-001).

요청 전체 제한 시간(request_timeout_seconds, 기본 30초) 안에서 남은 시간을 계산해
LLM 결과 요약처럼 생략할 수 있는 단계는 시간이 부족하면 건너뛴다.
"""

import time

from app.config import settings

# 마지막 응답 조립에 남겨 두는 여유 시간
SAFETY_MARGIN_SECONDS = 1.0
# 이보다 시간이 적게 남으면 LLM 요약 대신 템플릿 문장을 쓴다.
MIN_SUMMARY_SECONDS = 3.0

REQUEST_TIMEOUT_NOTICE = (
    "응답이 늦어져 이번 요청을 마치지 못했어요. 잠시 후 다시 시도하거나 "
    "'서면 카페 추천해줘'처럼 지역과 장소 종류를 함께 적어 주세요."
)


def remaining_budget(started: float) -> float:
    """요청을 시작한 뒤 제한 시간까지 남은 초. 음수가 되지 않는다."""
    elapsed = time.perf_counter() - started
    return max(0.0, settings.request_timeout_seconds - elapsed - SAFETY_MARGIN_SECONDS)
