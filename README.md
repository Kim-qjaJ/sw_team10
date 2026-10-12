# sw_team10

AI 기반 부산 맞춤형 장소 추천 및 안내 서비스 팀 프로젝트 저장소입니다.

## 현재 상태

전체 구조(채팅 처리 흐름, LLM 해석, Local Router, 부산 지역 사전, 파이프라인, 데이터 형식)는 구현되어 있고,
**GUI · API · 추천 알고리즘** 파일은 인터페이스(클래스·함수 이름과 입출력 형식)만 정의된 뼈대입니다.
뼈대 상태에서도 서버는 실행되며, `USE_MOCK_PLACES=true`(기본값)이면 샘플 장소 데이터로 전체 흐름을 확인할 수 있습니다.

개인 선행 실험과 전체 동작 검증은 별도 `new_sw_prototype` 저장소에서 진행합니다.

## 역할별 파일

| 담당 | 파일 | 상태 |
|---|---|---|
| GUI | `app/static/index.html`, `app.js`, `style.css` | 빈 파일 |
| API | `app/providers/kakao.py`, `naver.py`, `busan.py`, `tour.py`, `weather.py` | 뼈대 |
| API | `app/services/api_router.py`, `place_merger.py`, `context_service.py` | 뼈대 |
| 추천 알고리즘 | `app/services/recommendation.py`, `scoring.py`, `hard_filter.py`, `exact_place_matcher.py` | 뼈대 |
| 구조·AI·통합 | 그 밖의 모든 파일 | 구현됨 |

뼈대 파일에는 `[API 담당]`, `[추천 알고리즘 담당]` 표시와 `TODO`가 있습니다.
**함수 이름과 입출력 형식은 바꾸지 말고 내부만 구현해 주세요.** 형식을 바꿔야 하면 먼저 구조 담당과 상의합니다.

공용 파일(수정 시 상의):
- `app/providers/base.py`: Provider 공통 인터페이스 (`PlaceProvider.search`)
- `app/providers/mock.py`: 샘플 데이터 (API 구현 전 GUI·추천 개발용)
- `app/schemas/`: 모든 모듈이 주고받는 데이터 형식 (`Place`, `PlaceRequest`, `UserIntent`, 응답 형식)

아직 쓰지 않는 파일: `place_normalizer.py`, `recommendation_service.py`, `accessibility_service.py`
(정규화는 각 Provider가, 점수는 `recommendation.py`가 맡습니다. 접근성 점수를 추가하게 되면 `scoring.py`에 항목으로 넣습니다.)

## 요청 처리 흐름

```text
GUI (app/static)
  ↓ POST /api/v1/recommend  {message, mode, session_id, latitude, longitude}
main.py → chat/service.py            모드 분기: 장소 검색 / 장소 추천 / 주변 시설 찾기
  ↓
Local Router (services/local_router.py)   사전으로 바로 해석되면 LLM 생략
  ↓ (애매한 문장만)
LLM Intent 해석 (llm/, prompts/intent.md)  Groq 또는 Ollama
  ↓ UserIntent
pipeline.py
  ├─ LocationResolver        지역 사전·생활권·시설 좌표 → 기준점/검색 범위
  ├─ APIRouterService        [API] Provider 병렬 검색
  ├─ PlaceMerger             [API] 같은 장소 통합
  ├─ 지역 경계 확인           (구조) 행정동 경계 밖 후보 제외
  ├─ HardFilter              [추천] 명시적 조건 위반 제거
  ├─ ContextService          [API] 날씨·지하철역
  └─ RecommendationService   [추천] 점수·순위
  ↓
response_writer.py → RecommendResponse → GUI
```

## 인터페이스 (pipeline.py가 호출하는 형식)

| 파일 | 호출 형식 | 반환 |
|---|---|---|
| `api_router.py` | `APIRouterService().search(request, location, anchor=None, radius_m=None)` | `(list[Place], dict[str, ProviderStatus])` |
| `providers/*.py` | `provider.search(request, location=None, anchor=None[, radius_m])` | `list[Place]`, 설정 없으면 `ProviderNotConfigured` |
| `place_merger.py` | `PlaceMerger().merge(places)` | `list[Place]` |
| `context_service.py` | `ContextService().weather(point)`, `.stations(point)` | `dict` 또는 `None`, `[(역 이름, 위도, 경도)]` |
| `hard_filter.py` | `HardFilter().apply(request, places)` | `list[Place]` |
| `recommendation.py` | `RecommendationService().rank(intent, request, places, limit, anchor, radius_m, sort_by_distance, stations, weather, score_anchor, distance_scale_m)` | 점수를 채운 `list[Place]` |
| `exact_place_matcher.py` | `institution_locations / match / representative / related_branches(query, places)` | `list[Place]` 또는 `Place`/`None` |

각 파일의 docstring에 인자의 의미와 채워야 할 필드를 적어 두었습니다.

## 폴더 구조

```text
app/
├── main.py                 FastAPI 앱, 엔드포인트
├── config.py               .env 설정
├── chat/                   요청 처리 흐름 (모드 분기, 세션, 후속 질문, 응답 조립)
├── llm/                    LLM Intent 해석 (Groq / Ollama)
├── prompts/intent.md       Intent 추출 규칙
├── schemas/                데이터 형식 (공용)
├── data/regions/           부산 지역 사전 (행정구역, 경계, 생활권)
├── providers/              [API] 외부 장소·날씨 API
├── services/
│   ├── pipeline.py              검색 → 통합 → 필터 → 점수
│   ├── local_router.py          LLM 없이 처리하는 질의 해석
│   ├── router_lexicon.py        Local Router 사전 (장소 종류, 생활권, 브랜드)
│   ├── region_registry.py       지역 사전 로더, 경계 판정
│   ├── location_resolver.py     위치 해석 (지역/생활권/시설)
│   ├── mode_router.py, followup.py, response_writer.py
│   ├── api_router.py, place_merger.py, context_service.py                      [API]
│   └── recommendation.py, scoring.py, hard_filter.py, exact_place_matcher.py   [추천]
└── static/                 [GUI]
tests/                      외부 API·LLM 없이 도는 테스트
```

## 실행 방법

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # 필요한 키를 입력
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 기본 LLM은 Groq입니다(`GROQ_API_KEY` 필요). 로컬 LLM을 쓰려면 `LLM_PROVIDER=ollama`로 바꾸고 Ollama를 실행해 둡니다.
- 지역·장소 종류가 분명한 문장("서면 카페 추천해줘")은 Local Router가 처리하므로 LLM 키 없이도 동작합니다.
- API 키 발급 방법은 `KEY_SETUP.md`를 참고합니다.
- 실행 후 `http://127.0.0.1:8000/docs`에서 API를 테스트할 수 있습니다.

## 엔드포인트

| 경로 | 설명 |
|---|---|
| `POST /api/v1/recommend` | GUI용 진입점. 자연어 → 해석 → 검색 → 추천 |
| `POST /api/v1/intent` | 해석 결과(Intent JSON)만 확인 |
| `POST /api/v1/recommend/intent` | 완성된 Intent로 검색·추천만 실행 (디버깅용) |
| `GET /api/v1/status` | LLM·Provider 설정, 지역 사전 상태 |
| `GET /health` | 상태 확인 |

## 테스트

```bash
python -m pytest
```

## 데이터 출처

- 행정동 경계: 통계청 SGIS(공공누리 제1유형)를 가공한 [vuski/admdongkor](https://github.com/vuski/admdongkor)(CC BY 4.0), 2026-07-01 버전
- 법정동·리 코드, 행정동-법정동 매핑: 행정안전부 「행정기관(행정동) 및 관할구역(법정동) 변경내역」(2024-08-01 시행)

자세한 내용은 `app/data/regions/manifest.json`에 있습니다.
