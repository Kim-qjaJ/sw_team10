# sw_team10 — Busan Mate

AI 기반 부산 맞춤형 장소 추천 및 안내 서비스의 백엔드 구조입니다.

현재 단계에서는 **GUI를 추가하거나 실제 외부 API를 구현하지 않고**, 자연어 질의 구조화, 위치 기준 결정, 장소 통합, 접근성 기반 추천 알고리즘의 전체 구조를 먼저 정의합니다.

## 전체 흐름

```text
User
  ↓
FastAPI
  ↓
Ollama / Gemma
  ↓
intent.md
  ↓
UserIntent
  ├─ location: 사용자가 말한 위치 표현 그대로
  ├─ requests[]: 실제 검색용 query 중심
  ├─ companion
  └─ transport_mode
  ↓
LocationResolver
  ├─ current_location : 사용자가 허용한 현재 GPS
  ├─ specific_place   : 해운대 해수욕장, 광안대교, 부산시청 등
  └─ area_anchor      : 덕천, 구포, 서면 등 생활권 → 대표 역/랜드마크
  ↓
API Router
  ├─ KakaoProvider      (구조만 정의, API 미연결)
  ├─ NaverProvider      (구조만 정의, API 미연결)
  ├─ BusanProvider      (구조만 정의, API 미연결)
  ├─ TourProvider       (구조만 정의, API 미연결)
  └─ WeatherProvider    (구조만 정의, API 미연결)
  ↓
PlaceNormalizer
  ↓
PlaceMerger
  ↓
HardFilter
  ↓
AccessibilityService
  ├─ walk
  ├─ transit
  └─ car
  ↓
RecommendationService
  ↓
TOP N
```

## Query 중심 Intent

세부 장소 종류를 제한된 enum에 억지로 맞추지 않고 실제 검색에 사용할 표현을 `query`에 보존합니다.

예:

```json
{
  "intent": "recommend_place",
  "location": "북구청",
  "requests": [
    {
      "query": "도서관",
      "category": "public_facility",
      "subcategory": "library",
      "indoor": null
    },
    {
      "query": "중국집",
      "category": "restaurant",
      "subcategory": "chinese",
      "indoor": null
    }
  ],
  "companion": null,
  "transport_mode": null
}
```

`해운대 해수욕장`처럼 구체적인 장소를 사용자가 말하면 `location`을 `해운대`로 축약하지 않습니다. 실제 기준점 선택은 LLM이 아니라 `LocationResolver`가 담당합니다.

## 위치 기준 결정

위치는 세 가지 방식으로 처리하도록 설계합니다.

1. **current_location** — 사용자가 브라우저/앱에서 현재 위치 사용을 허용한 경우
2. **specific_place** — 해운대 해수욕장, 광안대교, 부산시청 등 구체적인 장소
3. **area_anchor** — 덕천, 구포, 서면처럼 생활권 표현을 대표 역/랜드마크로 변환

구체적인 장소가 입력되면 생활권 Anchor로 임의 변환하지 않습니다.

## 추천 점수 v2

기존 단순 거리 점수 대신 이동수단별 **접근성 점수**를 사용합니다.

```text
검색어/카테고리 일치     30
접근성                   25
사용자 조건              15
날씨/실내외              10
Kakao/Naver 교차정보      10
부산 특화 데이터          10
----------------------------
합계                    100
```

접근성 25점은 선택한 이동수단에 따라 다르게 계산합니다.

- `walk`: 도보 시간
- `transit`: 대중교통 총 소요시간 + 환승 패널티
- `car`: 자동차 예상 소요시간
- 이동수단 미지정 시 Backend 정책에서 기본값을 결정

실제 이동시간과 경로는 추후 지도/경로 API가 연결된 뒤 채웁니다.

## 프로젝트 구조

```text
app/
├── main.py
├── routers/
│   └── chat.py
├── schemas/
│   ├── chat.py
│   ├── intent.py
│   ├── location.py
│   └── place.py
├── services/
│   ├── llm_service.py
│   ├── location_resolver.py
│   ├── api_router.py
│   ├── place_normalizer.py
│   ├── place_merger.py
│   ├── hard_filter.py
│   ├── accessibility_service.py
│   └── recommendation_service.py
├── providers/
│   ├── base.py
│   ├── kakao.py
│   ├── naver.py
│   ├── busan.py
│   ├── tour.py
│   └── weather.py
└── prompts/
    └── intent.md
```

Provider 파일은 현재 **인터페이스/빈 구현만 유지**하며 API 키나 실제 외부 API 호출 코드는 넣지 않습니다.

## 실행 방법

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

실행 후 `http://127.0.0.1:8000/docs`에서 현재 Intent 추출 API를 테스트할 수 있습니다.

현재 `POST /api/v1/chat`은 LLM Intent 추출까지만 수행하며 `places=[]`를 반환합니다. 외부 API와 추천 파이프라인은 팀 구현 단계에서 연결합니다.
