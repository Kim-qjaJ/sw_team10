# sw_team10 — Busan Mate

AI 기반 부산 맞춤형 장소 추천 및 안내 서비스의 프로토타입 백엔드입니다.

현재 단계에서는 외부 장소/날씨/관광 API와 DB를 제외하고 핵심 구조부터 구현합니다.

```text
User
  ↓
FastAPI
  ↓
Ollama / Gemma
  ↓
intent.md
  ↓
Intent JSON
  ↓
Pydantic validation
  ↓
FastAPI response
```

## 프로젝트 구조

```text
app/
├── main.py
├── routers/
│   └── chat.py
├── schemas/
│   ├── chat.py
│   └── intent.py
├── services/
│   └── llm_service.py
└── prompts/
    └── intent.md
```

## 실행 방법

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

실행 후 브라우저에서 `http://127.0.0.1:8000/docs`를 열어 API를 테스트할 수 있습니다.

## Ollama

기본 설정은 같은 컴퓨터의 Ollama를 사용합니다.

```text
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e4b
```

환경에 맞게 값을 변경할 수 있습니다. 실제 `.env` 파일은 Git에 커밋하지 않습니다.

## 현재 API

`POST /api/v1/chat`

예시 요청:

```json
{
  "message": "서면에서 친구랑 저렴하게 놀 곳 추천해줘"
}
```

현재는 LLM을 이용한 Intent 추출까지만 수행하며, 이후 외부 API, DB, 추천 엔진을 연결할 예정입니다.
