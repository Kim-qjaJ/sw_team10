# API Key setup

실제 API 키는 GitHub에 커밋하지 않고 프로젝트 루트의 `.env` 파일에만 저장합니다.

## LLM Provider

기본값은 기존과 동일하게 로컬 Ollama입니다.

```env
LLM_PROVIDER=ollama
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e4b
```

GroqCloud를 사용할 때는 아래처럼 변경합니다.

```env
LLM_PROVIDER=groq
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
```

`GROQ_API_KEY`는 각 개발자가 자신의 키를 로컬 `.env`에 넣습니다. 키 자체는 GitHub에 올리지 않습니다.
`LLM_PROVIDER`만 바꾸면 나머지 Intent → 장소 검색 → 통합 → 추천 파이프라인은 동일하게 동작합니다.

## 장소/공공데이터 API 키

### TMAP 경로 거리 (선택)

TMAP 보행자·자동차 경로를 사용할 수 있는 SK Open API 앱 키를 `.env`에 넣습니다.

```env
TMAP_ENABLED=true
TMAP_API_KEY=
TMAP_TRANSIT_API_KEY=
TMAP_TIMEOUT_SECONDS=2
TMAP_TOTAL_TIMEOUT_SECONDS=4
TMAP_MAX_ROUTES=40
TMAP_CONCURRENCY=4
TMAP_CACHE_TTL_SECONDS=300
```

`TMAP_API_KEY`는 보행자·자동차 경로에 사용합니다. 대중교통은 별도 상품 권한이 필요하며,
권한이 있는 키를 `TMAP_TRANSIT_API_KEY`에 넣습니다. 두 상품을 같은 앱 키로 이용할 수 있으면 같은 값을 넣어도 됩니다.
키를 추가·변경한 뒤 서버를 재시작합니다. 키 값을 보내거나 반환하는 별도 API는 없습니다.
설정 상태는 `/api/v1/status`의 `tmap.road_configured`와 `transit_configured`에서 확인할 수 있습니다.
이 값은 키 입력 여부이며 실제 상품 권한을 검증한 결과는 아닙니다. 호출 권한·한도 문제는 `last_error`에 HTTP 상태로 표시합니다.

기준 장소·현재 위치가 있는 추천과 주변 시설 찾기에서 경로 거리를 사용합니다.
API 키가 없거나 호출이 실패하면 직선거리 추정으로 계속 동작합니다.

### Kakao/Naver/공공데이터

필수 키:

```env
KAKAO_REST_API_KEY=
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
PUBLIC_DATA_API_KEY=
```

`PUBLIC_DATA_API_KEY`는 공공데이터포털의 일반 인증키 1개를 공통으로 사용하도록 구성했습니다. 현재 부산광역시 전시공간 목록 API도 이 키를 사용하며, Encoding/Decoding 키 어느 쪽을 넣어도 내부에서 URL decoding을 한 번 적용합니다.

공공데이터포털 API는 인증키만으로 어떤 데이터셋을 호출할지 알 수 없으므로, 실제로 사용할 부산/관광/날씨 API의 요청 URL은 별도로 연결해야 합니다. 현재는 아래 항목을 선택적으로 둘 수 있습니다.

```env
TOUR_API_URL=
KMA_API_URL=
```

부산광역시 전시공간 목록 API는 공식 요청 주소를 코드에 연결해 두었으므로 `BUSAN_API_URL`은 따로 설정하지 않습니다.

Kakao/Naver 장소 검색은 키를 넣고 `USE_MOCK_PLACES=false`로 설정하면 바로 실제 검색 Provider가 호출됩니다. `전시공간`, `전시장`, `갤러리`, `미술관`, `문화회관`, `공연장` 등 전시공간 계열 요청에는 부산광역시 공공데이터 Provider도 함께 호출됩니다.
