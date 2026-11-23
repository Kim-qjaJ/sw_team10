"""FastAPI 앱과 엔드포인트.

요청 처리 흐름은 app/chat/ 아래에 있다.
    main.py (엔드포인트) → chat/service.py (공통 처리·방식 분기)
    → chat/search.py | chat/nearby.py | chat/followup_handler.py | chat/conversation.py
    → services/pipeline.py (검색 → 통합 → 점수)
"""

import asyncio
import hmac
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.chat.intents import normalize_search_modes, parse_intent
from app.chat.service import handle_chat
from app.chat.responses import simple_response
from app.chat.session import SessionStore
from app.chat.timing import REQUEST_TIMEOUT_NOTICE
from app.config import BASE_DIR, settings
from app.db import QueryLogger, SupabaseClient
from app.llm.factory import create_intent_parser
from app.providers.busan import prefetch_exhibits
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
    # 질의 로그(Supabase). 꺼져 있거나 URL·키가 없으면 아무것도 저장하지 않는다.
    app.state.db = SupabaseClient(
        client,
        settings.supabase_url if settings.query_log_enabled else None,
        settings.supabase_secret_key if settings.query_log_enabled else None,
        timeout=settings.supabase_timeout_seconds,
    )
    app.state.query_logger = QueryLogger(app.state.db)
    # LLM 연결 상태는 서버를 켤 때와 운영 진단 조회 때만 실제로 확인하고, 공개 상태 조회는 저장한 값을 쓴다.
    app.state.llm_status = None
    app.state.llm_status_at = None
    # 로컬 Ollama는 워밍업하고, 클라우드 Provider는 기본적으로 no-op이다.
    warm_up_task = asyncio.create_task(app.state.intent_parser.warm_up())
    # 전시 상세는 공공데이터포털 호출 제한 때문에 천천히 받아야 하므로 서버를 켤 때 미리 받아 둔다.
    exhibit_task = asyncio.create_task(prefetch_exhibits())
    llm_check_task = asyncio.create_task(_refresh_llm_status())
    yield
    llm_check_task.cancel()
    warm_up_task.cancel()
    exhibit_task.cancel()
    await client.aclose()


app = FastAPI(title="SW_team10", version="0.4.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# 기본 화면은 메신저형(chat.html). 예전 검색 폼 화면은 /classic에 남겨 둔다.
# 캐시 때문에 예전 화면이 계속 보이지 않도록 HTML은 매번 새로 받게 한다.
NO_CACHE = {"Cache-Control": "no-cache"}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "chat.html", headers=NO_CACHE)


@app.get("/chat", include_in_schema=False)
async def chat_page():
    return FileResponse(STATIC_DIR / "chat.html", headers=NO_CACHE)


@app.get("/classic", include_in_schema=False)
async def classic_page():
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _status_detail_allowed(request: Request) -> bool:
    """운영 진단 정보는 STATUS_TOKEN을 아는 요청에만 준다 (배포 사이트에서 오류 내용이 보이지 않게)."""
    token = settings.status_token
    given = request.headers.get("x-status-token") or ""
    return bool(token) and hmac.compare_digest(given.encode(), token.encode())


async def _refresh_llm_status() -> dict:
    """LLM 연결을 실제로 확인하고 결과를 저장한다 (외부 요청이 생기므로 서버 시작·운영 진단 때만 부른다)."""
    try:
        llm = await app.state.intent_parser.status()
    except Exception as exc:  # noqa: BLE001 - 상태 확인 실패도 상태로 남긴다
        llm = {"provider": settings.llm_provider, "reachable": False, "error": type(exc).__name__}
    app.state.llm_status, app.state.llm_status_at = llm, time.monotonic()
    return llm


def _cached_llm_status() -> dict:
    """공개 상태용: 마지막으로 확인한 LLM 상태. 아직 확인 전이면 설정값만."""
    llm = getattr(app.state, "llm_status", None)
    checked = getattr(app.state, "llm_status_at", None)
    if llm is None:
        groq = settings.llm_provider == "groq"
        return {"provider": settings.llm_provider, "reachable": None,
                "model": settings.groq_model if groq else settings.ollama_model,
                "configured": bool(settings.groq_api_key) if groq else True, "checked_seconds_ago": None}
    return {**{key: llm.get(key) for key in ("provider", "reachable", "model", "configured")},
            "checked_seconds_ago": round(time.monotonic() - checked)}


@app.get("/api/v1/status")
async def status(request: Request):
    public_key_ready = bool(settings.public_data_service_key)
    app.state.sessions.cleanup()
    basic = {
        # 공개 응답: 설정·연결 여부만. 오류 문장·캐시·세션 수 같은 운영 정보는 넣지 않는다 (2026-11-23 검토).
        # LLM 연결은 매번 확인하지 않고 마지막 확인 결과를 쓴다 (공개 조회가 외부 요청을 만들지 않게).
        "llm": _cached_llm_status(),
        "mock_data": settings.use_mock_places,
        "providers": {
            "kakao": bool(settings.kakao_rest_api_key),
            "naver": bool(settings.naver_client_id and settings.naver_client_secret),
            "busan": public_key_ready,
            "tour": bool(public_key_ready and settings.tour_api_url),
            "weather": bool(public_key_ready and settings.kma_api_url),
        },
        "request_timeout_seconds": settings.request_timeout_seconds,
        "detail": False,
    }
    if not _status_detail_allowed(request):
        return basic
    llm = await _refresh_llm_status()
    return {
        "detail": True,
        "llm": llm,
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
        "query_log": app.state.db.status(),
        "weather": app.state.pipeline.context.weather_provider.status(),
        "tmap": app.state.pipeline.route_distance.provider.status(),
        "request_timeout_seconds": settings.request_timeout_seconds,
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
async def recommend(request: ChatRequest, background_tasks: BackgroundTasks):
    """GUI용 진입점. 처리 흐름은 app/chat/service.py를 참고한다.

    요청 전체를 request_timeout_seconds(기본 30초) 안에 끝낸다 (SRS NFR-001).
    질의 로그는 응답을 보낸 뒤 백그라운드에서 저장해 응답 시간에 영향을 주지 않는다.
    """
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(handle_chat(app.state, request), timeout=settings.request_timeout_seconds)
    except asyncio.TimeoutError:
        response = simple_response(None, UserIntent(intent="unsupported"), started, "api", notice=REQUEST_TIMEOUT_NOTICE)
    background_tasks.add_task(app.state.query_logger.log, request, response)
    return response


@app.post("/api/v1/recommend/intent", response_model=RecommendResponse)
async def recommend_by_intent(request: IntentRecommendRequest):
    """디버깅용: LLM 없이 완성된 Intent로 추천 로직만 실행한다."""
    started = time.perf_counter()
    response = await app.state.pipeline.run(request.intent, request.limit)
    response.timing_ms["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return response
