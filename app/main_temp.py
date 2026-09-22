# 각 기능별 코드 구현 전 실행 파일입니다.
# LLM 호출 테스트 해보실분은 이거 실행해주세요.

import httpx
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.llm.base import IntentParseError, LLMUnavailableError
from app.llm.factory import create_intent_parser
from app.schemas.api import ChatRequest
from app.schemas.intent import UserIntent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # LLM API 호출에 사용할 HTTP 클라이언트
    client = httpx.AsyncClient()

    # .env의 LLM_PROVIDER에 따라 Groq 또는 Ollama 선택
    app.state.intent_parser = create_intent_parser(client)

    # Ollama면 워밍업, Groq면 아무 작업도 하지 않음
    await app.state.intent_parser.warm_up()

    yield

    await client.aclose()


app = FastAPI(
    title="SW_team10 - LLM Test",
    version="0.1.0",
    lifespan=lifespan,
)


async def parse_intent(message: str) -> tuple[UserIntent, float]:
    try:
        return await app.state.intent_parser.parse(message)

    except LLMUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "llm_unavailable",
                "message": str(exc),
            },
        ) from exc

    except IntentParseError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "intent_parse_failed",
                "message": "요청을 해석하지 못했습니다.",
                "debug": str(exc)[:500],
            },
        ) from exc


@app.get("/")
async def root():
    return {
        "service": "Busan Mate LLM Test",
        "status": "running",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/v1/status")
async def status():
    return {
        "llm": await app.state.intent_parser.status(),
    }


@app.post("/api/v1/intent")
async def intent_only(request: ChatRequest):
    intent, llm_ms = await parse_intent(request.message)

    return {
        "intent": intent,
        "timing_ms": {
            "intent_llm_ms": llm_ms,
        },
    }
