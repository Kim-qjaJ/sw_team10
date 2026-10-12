"""FastAPI 앱과 엔드포인트.

요청 처리 흐름은 app/chat/ 아래에 있다.
    main.py (엔드포인트) → chat/service.py (공통 처리·방식 분기)
    → chat/search.py | chat/nearby.py | chat/followup_handler.py | chat/conversation.py
    → services/pipeline.py (검색 → 통합 → 점수)
"""

import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.chat.intents import normalize_search_modes, parse_intent
from app.chat.service import handle_chat
from app.chat.session import SessionStore
from app.config import BASE_DIR, settings
from app.llm.factory import create_intent_parser
from app.schemas.api import ChatRequest, IntentRecommendRequest, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.local_router import route_place_query
from app.services.pipeline import RecommendationPipeline
from app.services.response_writer import ResponseWriter
from app.services.region_registry import get_region_registry

STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = httpx.AsyncClient()
    app.state.http = client
    app.state.intent_parser = create_intent_parser(client)
    app.state.pipeline = RecommendationPipeline()
    app.state.response_writer = ResponseWriter(client)
    app.state.sessions = SessionStore()
    # 로컬 Ollama는 워밍업하고, 클라우드 Provider는 기본적으로 no-op이다.
    warm_up_task = asyncio.create_task(app.state.intent_parser.warm_up())
    yield
    warm_up_task.cancel()
    await client.aclose()


app = FastAPI(title="SW_team10", version="0.4.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/v1/status")
async def status():
    public_key_ready = bool(settings.public_data_service_key)
    app.state.sessions.cleanup()
    return {
        "llm": await app.state.intent_parser.status(),
        "mock_data": settings.use_mock_places,
        "ram_sessions": len(app.state.sessions),
        "region_dictionary": {"regions": len(get_region_registry().regions), "names": len(get_region_registry().names),
                              "administrative_boundaries": len(get_region_registry().polygons),
                              "lifestyle_areas": len({area.name for area in get_region_registry().lifestyle_areas.values()})},
        "location_cache": app.state.pipeline.location_resolver.cache_stats(),
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
    """프롬프트 테스트용: 자연어를 Intent JSON으로 변환한다. Local Router가 처리하면 LLM을 부르지 않는다."""
    state = app.state.sessions.get(request.session_id)
    route = route_place_query(
        request.message,
        previous_intent=state.intent if state else None,
        recent_places=state.last_places if state else None,
        pending_intent=state.pending if state else None,
    )
    if route.kind == "direct" and route.intent is not None:
        return {"intent": normalize_search_modes(route.intent), "route": "local", "reason": route.reason,
                "timing_ms": {"intent_llm_ms": 0.0}}
    if route.kind == "clarify":
        app.state.sessions.save_pending(request.session_id, route.intent)
        return {"intent": UserIntent(intent="clarify", question=route.message), "route": "local", "reason": route.reason,
                "timing_ms": {"intent_llm_ms": 0.0}}
    intent, llm_ms = await parse_intent(app.state, request.message, request.session_id)
    return {"intent": intent, "route": "llm", "reason": route.reason, "timing_ms": {"intent_llm_ms": llm_ms}}


@app.post("/api/v1/recommend", response_model=RecommendResponse)
async def recommend(request: ChatRequest):
    """GUI용 진입점. 처리 흐름은 app/chat/service.py를 참고한다."""
    return await handle_chat(app.state, request)


@app.post("/api/v1/recommend/intent", response_model=RecommendResponse)
async def recommend_by_intent(request: IntentRecommendRequest):
    """디버깅용: LLM 없이 완성된 Intent로 추천 로직만 실행한다."""
    started = time.perf_counter()
    response = await app.state.pipeline.run(request.intent, request.limit)
    response.timing_ms["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return response
