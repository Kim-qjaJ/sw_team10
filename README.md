# sw_team10 — Busan Mate

AI 기반 부산 맞춤형 장소 추천 및 안내 서비스 팀 프로젝트 저장소입니다.

## 현재 구현 범위

현재는 팀 협업 전에 **로컬 LLM 자연어 해석 환경**까지만 구현되어 있습니다. 이 부분은 담당 구현으로 남겨두고, 외부 API·위치 해석·장소 통합·추천 알고리즘 등 나머지 모듈은 팀원들이 역할을 나누어 구현할 수 있도록 파일 구조만 유지합니다.

현재 동작 흐름은 다음과 같습니다.

```text
User
  ↓
FastAPI
  ↓
POST /api/v1/chat
  ↓
LLMService
  ↓
Ollama / Gemma
  ↓
intent.md
  ↓
UserIntent JSON
  ↓
Pydantic validation
  ↓
FastAPI response
```

## 현재 구현된 파일

```text
app/
├── main.py                  # FastAPI 앱 / 라우터 등록 / health
├── prompts/
│   └── intent.md            # Gemma Intent 추출 규칙
├── routers/
│   └── chat.py              # POST /api/v1/chat
├── schemas/
│   ├── chat.py              # ChatRequest / ChatResponse
│   └── intent.py            # UserIntent / PlaceRequest
└── services/
    └── llm_service.py       # Ollama 호출 + JSON 검증
```

다음 파일들은 협업용 구조만 남겨두고 구현 내용은 비워 둡니다.

```text
app/providers/
├── base.py
├── kakao.py
├── naver.py
├── busan.py
├── tour.py
└── weather.py

app/schemas/
├── location.py
└── place.py

app/services/
├── location_resolver.py
├── api_router.py
├── place_normalizer.py
├── place_merger.py
├── hard_filter.py
├── accessibility_service.py
└── recommendation_service.py
```

## 실행 방법

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Ollama는 별도로 실행되어 있어야 합니다.

```text
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e4b
```

실행 후 `http://127.0.0.1:8000/docs`에서 API를 테스트할 수 있습니다.

현재 `POST /api/v1/chat`은 **자연어 → Intent JSON 추출까지만** 수행하며, 장소 데이터는 아직 연결하지 않기 때문에 `places=[]`를 반환합니다.

## 협업 단계

이후 팀 구현에서는 빈 파일들을 역할별로 채워 다음 구조로 확장합니다.

```text
Intent
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

개인 선행 실험과 전체 동작 검증은 별도 `new_sw_prototype` 저장소에서 진행합니다.
