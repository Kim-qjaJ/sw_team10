# 성능 측정 결과 (2026-11-16)

## NFR-006 추천 순위 계산 (후보 50개, 기준 100ms)

- 실행: `python scripts/benchmark_recommendation.py 300` (외부 API·LLM 없이 Mock 후보 50개, 비 오는 날·지하철역 10개 포함)
- 결과: 중앙값 2.28ms, 95% 2.36ms, 최대 4.65ms → 통과
- 회귀 테스트: tests/test_recommendation_performance.py

## NFR-001 응답 시간 (맥미니 서버, 실제 외부 API)

측정: 브라우저에서 /api/v1/recommend 호출, 질문마다 새 세션으로 3회. 서버 기록(timing_ms.total_ms) 기준.

| 구분 | 횟수 | 결과 | 기준 |
|---|---|---|---|
| 사전으로 해석되는 질문, 첫 호출 | 12 | 230~566ms | 3초 이내 → 통과 |
| 같은 질문 두·세 번째 | 24 | 107~244ms | 3초 이내 → 통과 |
| 외부 API 하나가 응답하지 않을 때 (부산 전시 목록 무응답) | 2 | 5.5초 | 6초 이내 → 통과 |
| LLM이 해석하는 질문 | 9 | 1.3~3.9초 | 30초 이내 → 통과 |

- 질문: 서면 카페, 해운대 맛집, 광안리 밥집, 기장 관광지, 전포동 맛집, 덕천 카페, 부산역 근처 약국, 남포동 카페, 센텀 맛집, 송정 카페, 동래 맛집, 영도 관광지
- Provider별 대기 상한 provider_wait_seconds = 5.5초 (app/config.py). 넘기면 그 Provider 없이 응답하고 하던 다운로드는 뒤에서 마저 받는다.
- 회귀 테스트: tests/test_recommendation_performance.py::test_slow_provider_does_not_hold_the_response
