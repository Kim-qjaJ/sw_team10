# sw_team10 — Busan Mate

AI 기반 부산 맞춤형 장소 추천 및 안내 서비스 팀 프로젝트 저장소입니다.

## 현재 저장소 운영 방식

이 저장소는 **협업용 설계/구조 스켈레톤**으로 유지합니다.
현재 `app/` 아래 Python 파일은 팀원들이 역할을 나누어 직접 구현할 수 있도록 **파일명과 디렉터리 구조만 남기고 내용은 비워 둔 상태**입니다.

프로젝트 설계 방향, Intent 규칙, 추천 알고리즘 구조는 README 및 `app/prompts/intent.md`, Notion 문서를 참고합니다.
개인 실험 및 선행 구현은 별도 `new_sw_prototype` 저장소에서 진행하며, 검증이 끝난 기능만 팀 협업 과정에서 다시 구현/통합합니다.

## 예정 구조

```text
app/
├── main.py
├── prompts/
│   └── intent.md
├── providers/
│   ├── __init__.py
│   ├── base.py
│   ├── kakao.py
│   ├── naver.py
│   ├── busan.py
│   ├── tour.py
│   └── weather.py
├── routers/
│   └── chat.py
├── schemas/
│   ├── chat.py
│   ├── intent.py
│   ├── location.py
│   └── place.py
└── services/
    ├── llm_service.py
    ├── location_resolver.py
    ├── api_router.py
    ├── place_normalizer.py
    ├── place_merger.py
    ├── hard_filter.py
    ├── accessibility_service.py
    └── recommendation_service.py
```

## 설계 흐름

```text
사용자 자연어
  ↓
Local LLM / Intent
  ↓
LocationResolver
  ↓
API Router
  ↓
PlaceNormalizer / PlaceMerger
  ↓
Hard Filter
  ↓
AccessibilityService
  ↓
RecommendationService
  ↓
최종 결과
```

위 모듈들은 현재 팀 협업을 위해 구현을 비워 둔 상태입니다.
외부 API Provider 역시 파일만 유지하며 실제 API 호출 코드는 아직 작성하지 않습니다.
