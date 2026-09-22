import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.llm.base import IntentParseError, LLMUnavailableError
from app.llm.factory import create_intent_parser
from app.schemas.api import ChatRequest, IntentRecommendRequest, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.pipeline import RecommendationPipeline

STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient()
    app.state.http = client
    app.state.intent_parser = create_intent_parser(client)
    app.state.pipeline = RecommendationPipeline()
    # 로컬 Ollama는 워밍업하고, 클라우드 Provider는 기본적으로 no-op이다.
    warm_up_task = asyncio.create_task(app.state.intent_parser.warm_up())
    yield
    warm_up_task.cancel()
    await client.aclose()


app = FastAPI(title="SW_team10", version="0.3.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


async def parse_intent(message: str) -> tuple[UserIntent, float]:
    try:
        return await app.state.intent_parser.parse(message)
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail={"code": "llm_unavailable", "message": str(exc)})
    except IntentParseError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "intent_parse_failed",
                "message": "요청을 해석하지 못했습니다. 지역과 장소를 넣어 다시 표현해 주세요.",
                "debug": str(exc)[:500],
            },
        )


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/v1/status")
async def status():
    public_key_ready = bool(settings.public_data_service_key)
    return {
        "llm": await app.state.intent_parser.status(),
        "mock_data": settings.use_mock_places,
        "providers": {
            "kakao": bool(settings.kakao_rest_api_key),
            "naver": bool(settings.naver_client_id and settings.naver_client_secret),
            "busan": public_key_ready,
            "tour": bool(public_key_ready and settings.tour_api_url),
            "weather": bool(public_key_ready and settings.kma_api_url),
        },
        "public_data_key_loaded": public_key_ready,
    }


@app.post("/api/v1/intent")
async def intent_only(request: ChatRequest):
    """프롬프트 테스트용: 자연어를 Intent JSON으로만 변환한다."""
    intent, llm_ms = await parse_intent(request.message)
    return {"intent": intent, "timing_ms": {"intent_llm_ms": llm_ms}}


@app.post("/api/v1/recommend", response_model=RecommendResponse)
async def recommend(request: ChatRequest):
    """GUI용: 자연어 → Intent → 검색 → 통합 → 추천."""
    started = time.perf_counter()
    intent, llm_ms = await parse_intent(request.message)
    response = await app.state.pipeline.run(intent, request.limit, message=request.message)
    response.timing_ms = {
        "intent_llm_ms": llm_ms,
        **response.timing_ms,
        "total_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return response


@app.post("/api/v1/recommend/intent", response_model=RecommendResponse)
async def recommend_by_intent(request: IntentRecommendRequest):
    """디버깅용: LLM 없이 완성된 Intent로 추천 로직만 실행한다."""
    started = time.perf_counter()
    response = await app.state.pipeline.run(request.intent, request.limit)
    response.timing_ms["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return response
