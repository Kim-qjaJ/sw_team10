"""기존 자연어 흐름 (방식 선택 없음 / 장소 추천): 자연어 → (Local Router | LLM) → 검색 → 통합 → 추천."""

import re
import time
from datetime import date, datetime, timedelta

from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.chat.followup_handler import handle_followup
from app.chat.intents import (
    GENERIC_FOLLOWUP_QUERIES,
    SEARCHABLE_INTENTS,
    needs_location,
    keep_free_condition,
    normalize_search_modes,
    parse_intent,
)
from app.chat.messages import LLM_FALLBACK_NOTICE, MISSING_LOCATION_QUESTION, ROUTE_NEEDS_PLACE
from app.chat.responses import simple_response
from app.schemas.api import ChatRequest, Link, RecommendResponse
from app.schemas.intent import UserIntent
from app.services.context_service import mentions_rain
from app.services.followup import FollowupPlan
from app.services.local_router import format_route, route_place_query
from app.chat.timing import MIN_SUMMARY_SECONDS, remaining_budget
from app.config import settings
from app.services.region_registry import get_region_registry
from app.services.travel_limit import apply_travel_limit
from app.services.response_writer import ResponseWriter, with_weather


WEATHER_QUESTION = re.compile(
    r"날씨|기온|몇\s*도|추워|추운|더워|더운|우산|(비|눈)\s*(와|오나|오니|올까|오는지|내려|내리나)\s*[?요]*\s*$"
)
# 부산시청 부근. 지역을 말하지 않은 날씨 질문의 기준점.
BUSAN_CENTER = (35.1798, 129.0750)


def weather_place(message: str) -> tuple[str, tuple[float, float]]:
    """'해운대 날씨'처럼 지역을 말했으면 그 지역 대표점, 아니면 부산시청 부근."""
    registry = get_region_registry()
    for token in re.findall(r"[가-힣]+", message):
        token = re.sub(r"(에서|에는|에|은|는|이|가|의)$", "", token)
        # 생활권('해운대', '서면')을 행정구역보다 먼저 쓴다. 생활권은 대표 지역의 좌표를 쓴다.
        region = None
        if token in registry.lifestyle_names:
            region = getattr(registry.lifestyle(token), "fallback", None)
        elif registry.names.get(token):
            region = registry.names[token][0]
        if region is not None and getattr(region, "latitude", None) is not None:
            return token, (region.latitude, region.longitude)
    return "부산(시청 부근)", BUSAN_CENTER


# 날씨 질문에서 장소처럼 보이는 낱말 ('대항항', '태종대'): 사전에 없으면 지도에서 찾아본다.
_PLACE_LIKE = re.compile(r"(항|포구|섬|도|산|역|해수욕장|해변|공원|시장|마을|대|사|천|강|교|터미널|공항|대학교)$")
_WEATHER_WORDS = {"날씨", "기온", "우산", "주말", "오늘", "내일", "모레", "글피", "지금", "이번", "다음", "몇", "정도", "온도"}


async def resolve_weather_place(ctx, message: str) -> tuple[str, tuple[float, float]] | str:
    """날씨를 볼 위치. 말한 지역을 쓸 수 없으면 이유 문장(str)을 돌려준다. 지역을 말하지 않았을 때만 부산시청 부근."""
    from app.services.local_router import parse_message
    parsed = parse_message(message)
    if parsed.non_busan:
        return f"'{parsed.non_busan[0]}' 날씨는 알려 드릴 수 없어요. 부산 안의 지역 날씨만 확인할 수 있어요."
    place, point = weather_place(message)
    if not place.startswith("부산("):
        return place, point
    words = [re.sub(r"(에서|에는|에|은|는|의)$", "", w) for w in parsed.unknown]
    candidates = [w for w in words if w not in _WEATHER_WORDS and len(w) >= 2 and _PLACE_LIKE.search(w)]
    if not candidates:
        return place, point
    ref = await ctx.pipeline.location_resolver.resolve_text(f"{candidates[0]} 근처")
    if ref.latitude is not None and ref.longitude is not None:
        return candidates[0], (ref.latitude, ref.longitude)
    return (f"'{candidates[0]}' 위치를 부산에서 찾지 못해 날씨를 알려 드릴 수 없어요. "
            "동이나 구 이름으로 다시 물어봐 주세요. (예: 가덕도동 날씨)")


def weather_location(message: str) -> str | None:
    """날씨 질문에서 사용자가 말한 지역 (말하지 않았으면 None). 답 아래 '해운대 카페' 같은 버튼에 쓴다."""
    place, _ = weather_place(message)
    return None if place.startswith("부산(") else place


_WEEKDAYS = "월화수목금토일"
_PAST_DATE = re.compile(
    r"(?<![가-힣])(?:그저께|어제|지난\s*(?:주말|주|달|[월화수목금토일]요일)|[0-9]+\s*일\s*전)"
)
_PAST_WEATHER_NOTICE = "지난 날짜의 날씨는 알려 드릴 수 없어요. 오늘 관측값과 앞으로의 단기예보를 확인할 수 있어요."
_LATER_TODAY = re.compile(r"오늘\s*(오후|저녁|밤|낮|이따|나중|퇴근)|이따|오늘\s*밤")


_WEEKDAY_WORD = re.compile(r"(다음\s?주\s?)?([월화수목금토일])요일")
_EXPLICIT_DATE = re.compile(r"(?:(다음|이번)\s?달\s*)?(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일(?!\s*(?:뒤|후|동안|간))")


def _weekday_date(match: re.Match, today: date) -> date:
    """'금요일' → 오늘 이후 가장 가까운 금요일, '다음 주 금요일' → 다음 주의 금요일."""
    target = _WEEKDAYS.index(match.group(2))
    ahead = (target - today.weekday()) % 7
    if match.group(1):
        ahead += 7 if ahead == 0 or target >= today.weekday() else 0
    return today + timedelta(days=ahead)


def _explicit_date(match: re.Match, today: date) -> date | None:
    """월을 명시한 날짜와 가까운 미래에 해당하는 일자만 적은 날짜를 해석한다."""
    relative, month_text, day_text = match.groups()
    try:
        if month_text:
            month = int(month_text)
            year = today.year + (today.month == 12 and month == 1)
            return date(year, month, int(day_text))
        month, year = today.month, today.year
        if relative == "다음":
            month, year = today.month % 12 + 1, today.year + (today.month == 12)
        day = date(year, month, int(day_text))
        if day < today and relative is None:
            day = date(today.year + (today.month == 12), today.month % 12 + 1, day.day)
        return day
    except ValueError:
        return None


def weather_day(message: str, today: date) -> tuple[str, date | None] | str | None:
    """날씨를 물은 날. None이면 지금(관측값), (이름, None)이면 예보로 알 수 없는 날(다음 주 등),
    문자열이면 날짜와 요일이 맞지 않아 되물을 질문.

    날짜를 적었으면 날짜를 먼저 본다 (예전에는 '12월 4일 금요일'이 요일에 덮여 11월 27일 금요일이 됐다)."""
    text = re.sub(r"\s+", " ", message)
    if _PAST_DATE.search(text):
        return _PAST_WEATHER_NOTICE
    weekday = _WEEKDAY_WORD.search(text)
    explicit = _EXPLICIT_DATE.search(text)
    if explicit:
        label = explicit.group(0).strip()
        day = _explicit_date(explicit, today)
        if day is None:
            return f"{label}은 없는 날짜예요. 날짜를 다시 알려 주세요."
        if day < today:
            return (f"지난 날짜({day.month}월 {day.day}일)의 날씨는 알려 드릴 수 없어요. "
                    "오늘부터 3일 뒤까지의 예보만 확인할 수 있어요.")
        if weekday and _WEEKDAYS[day.weekday()] != weekday.group(2):
            other = _weekday_date(weekday, today)
            actual = _WEEKDAYS[day.weekday()]
            return (f"{day.month}월 {day.day}일은 {actual}요일이에요. {day.month}월 {day.day}일({actual})과 "
                    f"{weekday.group(0)}({other.month}월 {other.day}일) 중 어느 날 날씨를 알려 드릴까요?")
        if day == today:
            return None
        return f"{day.month}월 {day.day}일", day
    later = re.search(r"(\d{1,2})\s*일\s*(뒤|후)", text)
    if later:
        day = today + timedelta(days=int(later.group(1)))
        return f"{later.group(1)}일 뒤", day
    if re.search(r"내일\s?모레|모레", text):
        return "모레", today + timedelta(days=2)
    if "글피" in text:
        return "글피", today + timedelta(days=3)
    if "내일" in text:
        return "내일", today + timedelta(days=1)
    if weekday:
        return weekday.group(0), _weekday_date(weekday, today)
    if "주말" in text:
        if "다음" in text:
            return "다음 주말", None
        ahead = max(0, 5 - today.weekday())
        return "이번 주말", today + timedelta(days=ahead)
    if re.search(r"다음\s?주|다음\s?달", text):
        return re.search(r"다음\s?(주|달)", text).group(0), None
    if _LATER_TODAY.search(text):
        return "오늘", today
    return None


def _forecast_text(label: str, day: date, place: str, summary: dict | None, issued: str) -> str:
    if label == "오늘":
        when = "오늘 남은 시간"
    elif label == f"{day.month}월 {day.day}일":
        when = f"{label}({_WEEKDAYS[day.weekday()]})"
    else:
        when = f"{label}({day.month}월 {day.day}일 {_WEEKDAYS[day.weekday()]})"
    if summary is None:
        return (f"{when} {place} 날씨는 아직 예보가 없어 확인할 수 없어요. 기상청 단기예보는 3일 뒤까지만 나와요. "
                "더 먼 날은 기상청 날씨누리의 중기예보를 확인해 주세요.")
    pop = summary.get("pop")
    temps = ""
    if summary.get("tmn") is not None and summary.get("tmx") is not None:
        temps = f" 기온은 {summary['tmn']:g}~{summary['tmx']:g}°C예요."
    source = f" (기상청 단기예보, {issued[11:13]}시 발표)"
    if summary.get("wet"):
        word = "눈이" if summary.get("kinds") == ["snow"] else "비나 눈이" if "sleet" in summary.get("kinds", []) else "비가"
        hours = summary["wet"]
        span = f"{int(hours[0][:2])}시" if len(hours) == 1 else f"{int(hours[0][:2])}시~{int(hours[-1][:2])}시 사이"
        return (f"{when} {place}에는 {word} 올 것으로 예보됐어요. 강수확률은 최대 {pop}%이고 {span}에 예보가 있어요."
                f"{temps}{source} 실내 장소를 찾아 드릴까요?")
    chance = f" 강수확률은 최대 {pop}%예요." if pop is not None else ""
    return f"{when} {place}에는 비 예보가 없어요.{chance}{temps}{source}"


async def weather_reply(ctx, message: str = "", today: date | None = None) -> str:
    where = await resolve_weather_place(ctx, message)
    if isinstance(where, str):
        return where
    place, point = where
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    asked = weather_day(message, today)
    if asked is not None:
        # '내일 해운대에 비 와?'에 지금 관측값으로 답하면 틀린 답이 된다. 그 날의 예보로 답한다 (2026-11-23).
        if isinstance(asked, str):
            return asked  # 날짜와 요일이 맞지 않으면 어느 날인지 되묻는다
        label, day = asked
        if day is None:
            return (f"{label} {place} 날씨는 아직 예보가 없어 확인할 수 없어요. 기상청 단기예보는 3일 뒤까지만 나와요. "
                    "기상청 날씨누리의 중기예보를 확인해 주세요.")
        forecast = await ctx.pipeline.context.forecast(point)
        if not forecast:
            return f"{label} {place} 날씨 예보를 지금 불러오지 못했어요. 잠시 뒤 다시 물어봐 주세요."
        return _forecast_text(label, day, place, forecast["days"].get(day.strftime("%Y%m%d")), forecast["issued"])
    weather = await ctx.pipeline.context.weather(point)
    if not weather or weather.get("source") != "kma":
        return "지금은 날씨 정보를 확인하지 못했어요. 장소 추천은 계속 도와드릴 수 있어요."
    temperature = weather.get("temperature")
    temp_text = f"기온은 {temperature:g}°C" if isinstance(temperature, (int, float)) else "기온은 확인하지 못했고"
    if weather.get("is_raining"):
        word = {"snow": "눈이", "sleet": "비나 눈이"}.get(weather.get("precipitation") or "", "비가")
        return f"지금 {place}은 {word} 오고 {temp_text}예요. 실내 장소를 찾아 드릴까요?"
    return f"지금 {place} {temp_text}이고 비는 오지 않아요. (기상청 초단기실황 기준)"


async def handle_conversation(ctx, request: ChatRequest, message: str, started: float) -> RecommendResponse:
    sessions = ctx.sessions
    session_id = request.session_id
    state = sessions.get(session_id)
    previous_intent = state.intent if state else None
    recent_places = state.last_places if state and state.last_places else []
    pending = state.pending if state else None

    # Local Router: 명확한 장소 질의는 LLM 없이 Intent를 만든다.
    route = route_place_query(
        message,
        previous_intent=previous_intent,
        recent_places=recent_places,
        pending_intent=pending,
    )

    # 장소를 찾는 문장이 아니고 날씨만 묻는다면 LLM 없이 기상청 관측값으로 답한다 (지어내지 않게, AI-REQ-01).
    if route.kind != "direct" and WEATHER_QUESTION.search(message):
        return simple_response(await weather_reply(ctx, message),
                               UserIntent(intent="smalltalk", location=weather_location(message)), started, "local")

    if route.kind == "clarify":
        sessions.save_pending(session_id, route.intent)
        return simple_response(
            route.message,
            # 위치를 실어 보내 '광안리 맛집/카페/관광지' 같은 버튼을 만들 수 있게 한다.
            UserIntent(intent="clarify", question=route.message,
                       location=route.intent.location if route.intent else None),
            started,
            "local",
        )

    if route.kind == "unsupported":
        sessions.save_pending(session_id, None)
        return simple_response(None, route.intent or UserIntent(intent="unsupported"), started, "local", notice=route.message)

    if route.kind == "compare":
        reply, response_llm_ms = await ctx.response_writer.compare(message, recent_places)
        if state:
            state.updated_at = time.monotonic()
        return simple_response(
            reply,
            UserIntent(intent="compare_results"),
            started,
            "llm" if response_llm_ms else "local",
            response_llm_ms=response_llm_ms,
        )

    route_type = "local"
    fallback_notice: str | None = None
    llm_ms = 0.0
    if route.kind == "direct" and route.intent is not None:
        intent = normalize_search_modes(route.intent)
        sessions.save_search_intent(session_id, intent)
    else:
        # 애매한 질의만 LLM으로 해석한다.
        try:
            intent, llm_ms = await parse_intent(ctx, message, session_id)
            route_type = "llm"
        except HTTPException:
            if route.fallback is None:
                raise
            # LLM 실패 시에도 코드로 확인된 지역·장소 종류가 있으면 기본 검색을 이어간다.
            intent = normalize_search_modes(route.fallback)
            sessions.save_search_intent(session_id, intent)
            route_type = "llm_fallback"
            fallback_notice = LLM_FALLBACK_NOTICE
    free_intent = keep_free_condition(message, intent)
    if free_intent is not intent:
        intent = free_intent
        sessions.save_search_intent(session_id, intent)
    timed_intent = apply_travel_limit(intent, message)
    if timed_intent is not intent:
        # '걸어서 5분 이내'를 Intent에 담아 세션에도 남긴다 (후속 질문이 같은 상한을 쓴다).
        intent = timed_intent
        sessions.save_search_intent(session_id, intent)
    sessions.save_pending(session_id, None)

    if intent.intent == "clarify":
        question = intent.question or MISSING_LOCATION_QUESTION
        return simple_response(question, intent, started, route_type, intent_llm_ms=llm_ms)

    if intent.intent == "smalltalk" and WEATHER_QUESTION.search(message):
        # LLM은 실시간 날씨를 모르므로 지어내지 않게 기상청 관측값으로 직접 답한다 (AI-REQ-01).
        return simple_response(await weather_reply(ctx, message),
                               intent.model_copy(update={"location": weather_location(message)}),
                               started, route_type, intent_llm_ms=llm_ms)

    if intent.intent == "smalltalk":
        from app.services.local_router import build_category_requests, parse_message
        parsed = parse_message(message)
        if parsed.categories:
            # 장소 종류('카페')를 말했는데 짧은 대화로 해석되면, LLM이 장소를 지어낼 수 있다.
            # 대신 지역을 묻고, 답이 오면 그 종류를 찾는다 (2026-11-23 점검).
            waiting = UserIntent.model_validate({"intent": "recommend_place", "requests": build_category_requests(parsed)})
            sessions.save_pending(session_id, waiting)
            return simple_response(MISSING_LOCATION_QUESTION,
                                   UserIntent(intent="clarify", question=MISSING_LOCATION_QUESTION),
                                   started, route_type, intent_llm_ms=llm_ms)
        natural_reply, response_llm_ms = await ctx.response_writer.chat(message)
        return simple_response(natural_reply, intent, started, route_type,
                               intent_llm_ms=llm_ms, response_llm_ms=response_llm_ms)

    if intent.intent == "place_detail":
        natural_reply, response_llm_ms = await ctx.response_writer.write_place_detail(message, recent_places)
        return simple_response(natural_reply, intent, started, route_type,
                               intent_llm_ms=llm_ms, response_llm_ms=response_llm_ms)

    # LLM이 '다른 곳'처럼 뜻 없는 검색어를 만들면 직전 검색을 이어서 다른 곳을 찾는다.
    if (
        intent.intent in SEARCHABLE_INTENTS
        and state is not None
        and state.last_search is not None
        and intent.requests
        and all(re.sub(r"\s+", "", r.query) in GENERIC_FOLLOWUP_QUERIES for r in intent.requests)
    ):
        plan = FollowupPlan(
            requests=[r.model_dump() for r in state.last_search.intent.requests], same_query=True, expand=False,
        )
        return await handle_followup(ctx, request, plan, state, started)

    if intent.intent == "get_route":
        # LLM이 길찾기로 해석한 경우: 직전 결과 장소로 카카오맵 링크를 준다.
        route_link = format_route(message, recent_places, origin=state.origin if state else None, force=True)
        if route_link is None:
            return simple_response(ROUTE_NEEDS_PLACE, intent, started, route_type, intent_llm_ms=llm_ms)
        response = simple_response(route_link[0], intent, started, route_type, intent_llm_ms=llm_ms)
        response.links = [Link(**link) for link in route_link[1]]
        return response

    # 위치를 알 수 없는 일반 카테고리 검색은 전국 결과가 섞이지 않도록 되묻는다.
    if needs_location(intent):
        sessions.save_pending(session_id, intent)
        clarify = UserIntent(intent="clarify", question=MISSING_LOCATION_QUESTION)
        return simple_response(MISSING_LOCATION_QUESTION, clarify, started, route_type, intent_llm_ms=llm_ms)

    # "다른 영화관/카페" 같은 후속 요청은 직전 카드와 같은 장소를 다시 보여주지 않는다.
    exclude_names: set[str] = set()
    if "다른" in message and recent_places:
        exclude_names = {str(place.get("name") or "").strip() for place in recent_places if place.get("name")}

    response = await ctx.pipeline.run(
        intent,
        request.limit,
        message=message,
        exclude_names=exclude_names,
        rain_hint=mentions_rain(message),
    )
    response.route = route_type
    if fallback_notice and not response.notice:
        response.notice = fallback_notice

    # 결과 설명: 명확한 질의는 템플릿 문장, LLM이 해석한 복합 조건은 LLM이 설명한다.
    # 두 경우 모두 순위에 반영된 날씨가 있으면 한 문장으로 덧붙인다.
    response_llm_ms = 0.0
    summary = None
    weather = (response.context or {}).get("weather")
    if intent.intent != "unsupported" and response.results:
        # 요청 전체 제한 시간(30초) 안에서 남은 시간만 LLM 요약에 쓴다. 부족하면 템플릿 문장으로 대신한다.
        remaining = remaining_budget(started)
        time_limit = (response.context or {}).get("time_limit")
        # 이동 시간 조건을 채우지 못했으면 LLM 요약이 '5분 이내'라고 말하지 않도록 템플릿 문장을 쓴다.
        if route_type == "llm" and remaining >= MIN_SUMMARY_SECONDS and not (time_limit and not time_limit["met"]):
            summary, response_llm_ms = await ctx.response_writer.write(
                user_message=message,
                intent=intent,
                results=response.results,
                timeout=min(settings.groq_timeout_seconds, remaining),
            )
            summary = with_weather(summary, weather, response.results, intent.location)
        summary = summary or ResponseWriter.template_summary(intent, response.results, weather)
    # 결과가 없으면 notice가 이유를 설명하므로 사용자 문장을 그대로 되돌려주지 않는다.
    response.message = summary

    sessions.save_last_places(session_id, response)
    sessions.remember_search(session_id, intent, response, request.mode, limit=request.limit)

    response.timing_ms = {
        "intent_llm_ms": llm_ms,
        **response.timing_ms,
        "response_llm_ms": response_llm_ms,
        "total_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return response
