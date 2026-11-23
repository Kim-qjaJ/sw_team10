"""후속 질문 처리: 직전 검색 조건(검색어·기준 위치·반경)을 이어서 다시 검색한다.

후속 질문인지 판단하는 규칙은 app/services/followup.py(plan_followup)에 있다.
"""

from app.chat.messages import EXPAND_NOT_POSSIBLE, FOLLOWUP_EXHAUSTED, josa, max_radius_reached
from app.chat.intents import SEARCHABLE_INTENTS
from app.chat.responses import finish_search, simple_response
from app.chat.session import SessionState, has_places
from app.schemas.api import ChatRequest, RecommendResponse
from app.schemas.intent import PlaceRequest
from app.services.context_service import mentions_rain
from app.services.followup import NEAR_PATTERN, PLACE_NUMBER_PATTERN, FollowupPlan, next_radius
from app.services.search_radius import MAX_EXPAND_M, POINT_RADIUS_M
from app.services.local_router import missing_rank_reply, requested_count, select_recent_places
from app.services.mode_router import format_radius
from app.services.pipeline import provider_failure_notice
from app.services.response_writer import with_weather


async def handle_followup(
    ctx,
    request: ChatRequest,
    plan: FollowupPlan,
    state: SessionState,
    started: float,
) -> RecommendResponse:
    last = state.last_search
    if last.limit and requested_count(request.message) is None:
        # 개수를 새로 말하지 않았으면 직전에 보여 준 개수를 유지한다 ('한 곳만' → '해운대로 바꿔줘').
        request = request.model_copy(update={"limit": last.limit})
    requests = [PlaceRequest.model_validate(item) for item in plan.requests]
    intent_type = last.intent.intent if last.intent.intent in SEARCHABLE_INTENTS else "search_place"
    if not plan.same_query and intent_type == "search_place" and all(r.search_mode == "category" for r in requests):
        intent_type = "recommend_place"
    intent = last.intent.model_copy(update={"intent": intent_type, "requests": requests})
    # 직전에 빼기로 한 이름은 같은 종류를 다시 찾는 동안 계속 뺀다. 종류가 바뀌면 새 검색으로 보고 비운다.
    same_kind = {r.query for r in requests} == {r.query for r in last.intent.requests}
    carried = [w for w in last.exclude_words if w not in plan.exclude_words] if same_kind else []
    exclude_words = carried + plan.exclude_words

    anchor, radius = last.anchor, last.radius_m
    if plan.location:
        # 지역만 바꾸면 이전 기준점·반경은 버리고 새 지역을 다시 해석한다.
        intent = intent.model_copy(update={"location": plan.location})
        anchor, radius = None, None
    display_location = intent.location
    # '부산역' 같은 특정 장소 검색 직후 '이 근처 카페'면 그 장소 좌표를 기준점으로 쓴다.
    if anchor is None and not plan.same_query and any(r.search_mode == "exact_place" for r in last.intent.requests):
        first = next((p for p in (state.last_places or []) if p.get("latitude") is not None), None)
        if first is not None:
            anchor, radius = (first["latitude"], first["longitude"]), POINT_RADIUS_M
            display_location = f"{first['name']} 근처"
            intent = intent.model_copy(update={"location": first["name"]})

    # '3번 근처 맛집', '거기 주변 카페': 직전 결과 중 그 장소 좌표를 기준점으로 쓴다.
    near_place = None
    if NEAR_PATTERN.search(request.message) and not plan.expand:
        recent = state.last_places or []
        picked = select_recent_places(request.message, recent)
        if not picked and PLACE_NUMBER_PATTERN.search(request.message):
            notice = missing_rank_reply(request.message, recent) or "직전 결과에서 그 번호의 장소를 찾지 못했어요."
            return simple_response(None, intent, started, "local", notice=notice)
        if picked and (picked[0].get("latitude") is None or picked[0].get("longitude") is None):
            return simple_response(
                None, intent, started, "local",
                notice="선택한 장소의 위치를 확인하지 못했어요. 다른 장소를 골라 주세요.",
            )
        if picked:
            near_place = picked[0]
            anchor, radius = (near_place["latitude"], near_place["longitude"]), POINT_RADIUS_M
            display_location = f"{near_place['name']} 근처"
            intent = intent.model_copy(update={"location": near_place["name"]})

    if plan.expand:
        if anchor is None:
            return simple_response(None, intent, started, "local", notice=EXPAND_NOT_POSSIBLE)
        radius = next_radius(radius)
        if radius is None:
            notice = f"이미 최대 범위({format_radius(MAX_EXPAND_M)})까지 찾았어요."
            if not last.shown and intent.requests:
                # 최대 범위에서도 0건이었으면 같은 시설을 다른 기준 장소에서 찾도록 이어 준다.
                notice = max_radius_reached(intent.requests[0].query, None, format_radius(MAX_EXPAND_M))
                if last.mode == "nearby":
                    ctx.sessions.save_pending(request.session_id, intent.model_copy(update={"location": None}))
            return simple_response(None, intent, started, "local", notice=notice)

    exclude = set(last.shown) if plan.same_query and near_place is None else set()
    if near_place is not None:
        exclude.add(str(near_place["name"]).strip())  # 기준이 된 장소 자신은 빼고 보여준다.
    nearby_like = last.from_user_location or last.mode == "nearby"
    response: RecommendResponse | None = None
    # 결과가 없으면(기준점이 있을 때) 반경을 단계적으로 넓혀 다시 찾는다.
    while True:
        response = await ctx.pipeline.run(
            intent, request.limit, exclude_names=exclude, exclude_words=exclude_words,
            anchor_override=anchor, radius_m=radius if anchor else None,
            rain_hint=mentions_rain(request.message), sort_by_distance=nearby_like,
            raw_order=last.mode == "search",
        )
        if near_place is not None or has_places(response) or provider_failure_notice(response.results) or anchor is None:
            break
        bigger = next_radius(radius)
        if bigger is None or bigger > MAX_EXPAND_M:
            break
        radius = bigger

    response.radius_m = radius if anchor else None
    if last.from_user_location and anchor is not None:
        display_location = f"현재 위치 {format_radius(radius or POINT_RADIUS_M)} 이내"
    count = sum(len(result.places) for result in response.results)
    query = ", ".join(r.query for r in requests)
    if count:
        widened = (
            f"범위를 {format_radius(radius)}까지 넓혀 "
            if radius and anchor and radius != (POINT_RADIUS_M if near_place is not None else last.radius_m)
            else ""
        )
        other = "다른 " if exclude and near_place is None else ""
        where = f"{near_place['name']} 근처 " if near_place is not None else ""
        response.message = f"{widened}{where}{other}{query} {count}곳을 찾았어요."
        if plan.location:
            response.message = f"지역을 {josa(plan.location.removeprefix('부산 '), '으로', '로')} 바꿔 {query} {count}곳을 찾았어요."
        if plan.exclude_words:
            names = ", ".join(plan.exclude_words)
            response.message = f"{josa(names, '은', '는')} 빼고 {where}{query} {count}곳을 찾았어요."
        elif carried:
            response.message = f"{response.message} ({', '.join(carried)} 제외)"
        # 거리순(주변 시설)이나 검색 순서 그대로 보여주는 경우가 아니면 순위에 반영된 날씨를 알려준다.
        if not nearby_like and last.mode != "search":
            response.message = with_weather(response.message, (response.context or {}).get("weather"),
                                            response.results, intent.location)
    elif not provider_failure_notice(response.results):
        if near_place is not None:
            response.notice = f"{near_place['name']}에서 {format_radius(POINT_RADIUS_M)} 안에 '{query}' 결과가 없어요."
        else:
            response.notice = FOLLOWUP_EXHAUSTED if exclude else f"'{query}' 검색 결과가 없어요."

    finished = finish_search(
        ctx, request, intent, response, started, "local",
        radius_m=radius if anchor else None,
        from_user_location=last.from_user_location,
        keep_shown=plan.same_query and near_place is None,
        exclude_words=exclude_words,
        # 0건이면 직전 검색(조건·좌표·카드)을 그대로 둔다. 예전에는 '1번 근처 약국'이 0건이어도 기준 좌표가
        # 1번 카페로 바뀌어, 이어서 '다른 곳 더'가 서면 카페를 1번 카페 좌표로 찾았다 (2026-11-23 검토).
        remember=has_places(response),
    )
    if display_location:
        finished.intent = intent.model_copy(update={"location": display_location})
    # finish_search가 저장한 기준점은 화면용 좌표 대신 실제 검색 기준점으로 맞춘다 (결과를 저장했을 때만).
    if state.last_search is not None and anchor is not None and has_places(response):
        state.last_search.anchor = anchor
    return finished
