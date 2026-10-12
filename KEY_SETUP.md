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
