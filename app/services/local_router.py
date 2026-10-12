"""Local Router: LLM을 부르기 전에 코드로 처리할 수 있는 질문을 먼저 걸러낸다.

처리 순서
1. 인사/감사/도움말 같은 짧은 대화 → route_local_reply
2. 직전 결과의 주소/전화/URL 질문 → format_place_fields
3. 장소 질의 → route_place_query
   - direct  : 위치 + 장소 종류가 사전에서 모두 확인된 명확한 질의. LLM 없이 Intent 생성
   - clarify : 위치나 장소 종류가 빠져 있어 되물어야 하는 질의
   - compare : "그중 ~" 처럼 직전 검색 결과를 비교하는 후속 질문 (LLM이 결과를 해석)
   - unsupported : 부산 밖 지역을 명시한 질의
   - llm     : 사전에 없는 조건/표현이 섞인 애매한 질의 → LLM Intent 해석
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from app.schemas.intent import UserIntent
from app.services.region_registry import get_region_registry
from app.services.router_lexicon import (
    BUSAN_AREAS,
    BUSAN_DISTRICTS,
    BUSAN_POINTS,
    CATEGORIES,
    COMPANIONS,
    FILLER_WORDS,
    FOLLOWUP_WORDS,
    INDOOR_WORDS,
    KNOWN_BRANDS,
    NON_BUSAN_AREAS,
    NOT_DONG,
    PARTICLES,
    POINT_SUFFIXES,
    RECOMMEND_WORDS,
    REFERENCE_WORDS,
    TRANSPORT_MODES,
    WHERE_WORDS,
)


@dataclass(frozen=True)
class LocalReply:
    kind: str
    message: str


def _normalize(message: str) -> str:
    text = message.strip().lower()
    text = re.sub(r"[!?~.ㅋㅎ]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


HELP_REPLY = (
    "저는 부산의 맛집, 카페, 관광지, 놀거리를 찾아주는 장소 추천 봇이에요. "
    "'서면 카페 추천해줘'처럼 지역과 장소 종류를 말해주면 바로 찾아드리고, 추천한 곳의 주소나 전화번호도 이어서 물어볼 수 있어요."
)
SERVICE_AREA_REPLY = (
    "아쉽지만 지금은 부산 지역 장소만 찾을 수 있어요. "
    "부산 안에서 지역과 장소 종류를 알려주시면 바로 찾아드릴게요. (예: 해운대 맛집, 덕천 카페)"
)

# 봇 자신이나 서비스에 대해 묻는 표현
_BOT_WORDS = ("너는", "너 ", "넌 ", "니가", "네가", "너가", "봇", "챗봇", "이 서비스", "여기는", "여기서")
_HELP_WORDS = (
    "무슨 일", "무슨일", "뭐 하", "뭐하", "뭘 하", "뭘하", "누구", "뭐야", "뭐니", "뭐임", "정체",
    "할 수 있", "할수있", "무엇을", "어떤 기능", "기능", "사용법", "어떻게 써", "어떻게 사용",
)
_HELP_ALONE = ("사용법", "어떤 기능", "뭐 할 수 있어", "뭘 할 수 있어", "무엇을 할 수 있어")
# 서비스 지역을 묻는 표현
_AREA_WORDS = ("다른 지역", "다른지역", "다른 도시", "부산 말고", "부산말고", "부산 외", "부산 밖", "전국", "타지역")
_AVAILABILITY_WORDS = ("돼", "되나", "되니", "될까", "되요", "돼요", "가능", "지원", "할 수", "쓸 수")


def _mentions_place_request(message: str) -> bool:
    """장소 종류나 부산 안 위치가 들어 있으면 대화가 아니라 장소 질의다."""
    parsed = _parse(message)
    return bool(parsed.categories or parsed.brands or [loc for loc in parsed.locations if loc != "부산"])


# 대화를 마무리하거나 더 필요 없다는 답 ('이제 없어', '됐어') — 검색어로 쓰면 안 된다.
_CLOSINGS = {
    "없어", "없어요", "없습니다", "이제 없어", "이제 없어요", "이제 됐어", "이제 됐어요", "됐어", "됐어요",
    "다 됐어", "괜찮아", "괜찮아요", "괜찮습니다", "그만", "그만할래", "그만할게", "끝", "아니", "아니야",
    "아니요", "아뇨", "필요없어", "필요 없어", "필요 없어요", "더 없어", "없음", "충분해", "충분해요",
}
# 짧은 맞장구
_ACKS = {"응", "어", "네", "넵", "ㅇㅇ", "좋아", "좋아요", "오케이", "ok", "okay", "알겠어", "알겠어요", "그래", "그래요"}


def route_local_reply(message: str) -> LocalReply | None:
    """확실한 짧은 대화만 LLM 호출 없이 처리한다."""
    text = _normalize(message)

    greetings = {"안녕", "안녕하세요", "안뇽", "하이", "ㅎㅇ", "반가워", "반갑습니다"}
    thanks = {"고마워", "고마워요", "감사해", "감사해요", "감사합니다", "땡큐", "ㄱㅅ"}
    goodbyes = {"잘가", "잘가요", "다음에 봐", "바이", "안녕히 계세요", "안녕히가세요"}

    if text in greetings:
        return LocalReply("greeting", "안녕하세요! 부산에서 맛집, 카페, 관광지나 놀 곳을 찾는 걸 도와드릴게요.")
    if text in thanks:
        return LocalReply("thanks", "천만에요! 다른 장소가 궁금하면 이어서 물어보세요.")
    if text in goodbyes:
        return LocalReply("goodbye", "좋은 하루 보내세요! 다음에 부산에서 갈 곳이 필요할 때 다시 찾아주세요.")
    if text in _CLOSINGS:
        return LocalReply("closing", "알겠어요! 또 찾고 싶은 곳이 생기면 언제든 말해 주세요.")
    if text in _ACKS:
        return LocalReply("ack", "좋아요! 이어서 찾고 싶은 곳이 있으면 말해 주세요.")

    if len(text) > 40 or _mentions_place_request(message):
        return None

    # '부산 말고 다른 지역도 될까?', '서울도 돼?'
    parsed = _parse(message)
    asks_area = any(word in text for word in _AREA_WORDS) or bool(parsed.non_busan)
    if asks_area and any(word in text for word in _AVAILABILITY_WORDS):
        return LocalReply("service_area", SERVICE_AREA_REPLY)

    # '너는 무슨 일을 하는 봇이니', '넌 누구야', '뭐 할 수 있어?'
    padded = f"{text} "
    about_bot = any(word in padded for word in _BOT_WORDS)
    if (about_bot and any(word in text for word in _HELP_WORDS)) or any(word in text for word in _HELP_ALONE):
        return LocalReply("help", HELP_REPLY)

    return None


# ---------------------------------------------------------------------------
# 직전 결과 상세 질문 (주소/전화/URL)
# ---------------------------------------------------------------------------

def requested_place_fields(message: str) -> set[str]:
    """후속 상세질문에서 API가 이미 제공한 기본 필드를 찾는다."""
    text = _normalize(message)
    fields: set[str] = set()
    if "주소" in text or "위치" in text:
        fields.add("address")
    if "전화" in text or "전화번호" in text or "연락처" in text:
        fields.add("phone")
    if any(word in text for word in ("url", "링크", "홈페이지", "사이트", "상세페이지")):
        fields.add("url")
    if any(word in text for word in ("정보", "상세", "알려줘", "보여줘")) and not fields:
        fields.update({"address", "phone", "url"})
    return fields


def select_recent_places(message: str, places: list[dict]) -> list[dict]:
    """'1번/첫 번째/장소명/전부' 표현으로 직전 추천 결과를 선택한다."""
    if not places:
        return []

    text = _normalize(message)
    if any(word in text for word in ("전부", "모두", "전체", "추천한 곳들", "장소들")):
        return places

    ordinal_patterns = {
        1: ("1번", "첫번째", "첫 번째"),
        2: ("2번", "두번째", "두 번째"),
        3: ("3번", "세번째", "세 번째"),
        4: ("4번", "네번째", "네 번째"),
        5: ("5번", "다섯번째", "다섯 번째"),
    }
    for rank, patterns in ordinal_patterns.items():
        if any(pattern in text for pattern in patterns):
            return [place for place in places if place.get("rank") == rank][:1]

    named = [place for place in places if place.get("name") and place["name"].lower() in text]
    if named:
        return named

    if any(word in text for word in ("거기", "그곳", "그 가게", "그 카페", "그 식당")):
        return places[:1]
    return []


def _asks_new_search(message: str) -> bool:
    """'거기 근처 카페 알려줘'처럼 새 장소 종류를 찾는 문장은 상세질문이 아니다."""
    text = _normalize(message)
    if re.search(r"\d번|번째", text):
        return False
    if any(word in text for word in ("근처", "주변", "부근")):
        return True
    text = re.sub(r"그\s?(가게|카페|식당)", "", text)
    return any(_strip_particles(token, CATEGORIES) for token in re.findall(r"[0-9a-z가-힣]+", text))


def format_place_fields(message: str, places: list[dict]) -> str | None:
    """주소/전화/URL 요청은 LLM 없이 직전 결과에서 바로 답한다."""
    fields = requested_place_fields(message)
    selected = select_recent_places(message, places)
    if not fields or not selected:
        return None
    if _asks_new_search(message):
        return None

    labels = {"address": "주소", "phone": "전화번호", "url": "URL"}
    blocks: list[str] = []
    for place in selected:
        values = []
        for field_name in ("address", "phone", "url"):
            if field_name not in fields:
                continue
            value = place.get(field_name)
            values.append(f"{labels[field_name]}: {value}" if value else f"{labels[field_name]}: 확인되지 않음")
        blocks.append(f"{place.get('rank', '?')}번 {place.get('name', '장소')} - " + ", ".join(values))
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# 장소 질의 라우팅
# ---------------------------------------------------------------------------

RouteKind = Literal["direct", "clarify", "compare", "unsupported", "llm"]

COMPARE_PATTERN = re.compile(r"(그|이|저|셋|넷|다섯|위|두 곳|세 곳)\s?중|중에서|중에|비교|어디가 더|어느 곳이|어느 게")
LOCATION_EXAMPLES = "예: 서면, 해운대, 덕천, 부산역 근처"


@dataclass
class PlaceRoute:
    kind: RouteKind
    intent: UserIntent | None = None
    message: str | None = None
    reason: str = ""
    # LLM으로 넘긴 질의라도 위치·장소 종류는 코드로 확인된 경우,
    # LLM 실패 시 이 Intent로 기본 검색을 이어간다.
    fallback: UserIntent | None = None


@dataclass
class _Parsed:
    areas: list[str] = field(default_factory=list)
    districts: list[str] = field(default_factory=list)
    points: list[str] = field(default_factory=list)
    non_busan: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    companion: str | None = None
    transport_mode: str | None = None
    indoor: bool | None = None
    recommend: bool = False
    where: bool = False
    reference: bool = False
    followup: bool = False
    unknown: list[str] = field(default_factory=list)
    prefer_region_words: set[str] = field(default_factory=set)

    @property
    def locations(self) -> list[str]:
        return self.points + self.districts + self.areas


REQUEST_VERB = re.compile(r"^(알려|찾아|보여|추천|부탁|골라|소개|말해|가르쳐)[가-힣]*$")
REQUEST_ENDINGS = {"주세요", "줄래", "줄래요", "줘요", "주라", "주실래요", "주실래", "주시겠어요", "해줄래", "해주세요", "해주라", "해줄래요"}


def _strip_particles(token: str, vocab) -> str | None:
    """조사를 최대 두 번 떼어내며 vocab에 있는 형태를 찾는다."""
    if token in vocab:
        return token
    candidates = [token]
    for _ in range(2):
        next_candidates = []
        for candidate in candidates:
            for particle in PARTICLES:
                if candidate.endswith(particle) and len(candidate) > len(particle):
                    stripped = candidate[: -len(particle)]
                    if stripped in vocab:
                        return stripped
                    next_candidates.append(stripped)
        candidates = next_candidates
    return None


# 지점 접미사(역 등)로 끝나지만 장소 이름이 아닌 일반 단어.
NOT_POINT = {"지역", "전역", "구역", "영역", "지역도", "지역은", "지역에"}


def _is_point_name(token: str) -> bool:
    if token in NOT_POINT:
        return False
    return any(token.endswith(suffix) and len(token) > len(suffix) for suffix in POINT_SUFFIXES)


def _is_dong(token: str) -> bool:
    return 2 <= len(token) <= 4 and token.endswith("동") and token not in NOT_DONG


@lru_cache(maxsize=1)
def _busan_areas() -> frozenset[str]:
    """라우터 생활권 사전 + 생활권 데이터 파일(busan_lifestyle.csv)의 이름과 별칭."""
    return frozenset(BUSAN_AREAS) | frozenset(get_region_registry().lifestyle_areas)


@lru_cache(maxsize=1)
def _food_region_overlaps() -> frozenset[str]:
    """음식 종류이면서 지역 이름인 단어 (예: 해운대구 '우동')."""
    return frozenset(word for word in CATEGORIES if word in get_region_registry().names)


def _region_words_over_food(tokens: list[str]) -> set[str]:
    """음식·지역 겸용 단어를 지역으로 볼지 정한다.

    - 그 지역이 속한 구를 함께 적었으면 지역 ('해운대구 우동 카페')
    - 다른 위치 표현이 없고, 음식이 아닌 장소 종류가 있으면 지역 ('우동 카페')
    - 그 밖에는 음식 ('서면 우동 맛집', '우동 맛집')
    """
    registry = get_region_registry()
    districts = {_strip_particles(token, BUSAN_DISTRICTS) for token in tokens}
    chosen: set[str] = set()
    for word in _food_region_overlaps():
        if not any(_strip_particles(token, {word}) for token in tokens):
            continue
        rest = [token for token in tokens if not token.startswith(word)]
        own_districts = {region.sgg for region in registry.names[word]}
        other_locations = [_strip_particles(token, _busan_areas()) or _strip_particles(token, registry.names) for token in rest]
        non_food = any((found := _strip_particles(token, CATEGORIES)) and CATEGORIES[found][1] != "restaurant" for token in rest)
        if districts & own_districts or (non_food and not any(other_locations)):
            chosen.add(word)
    return chosen


def _classify(token: str, parsed: _Parsed) -> None:
    registry = get_region_registry()
    region_word = _strip_particles(token, registry.names)
    if region_word in parsed.prefer_region_words:
        parsed.areas.append(region_word)
        return
    lookup = [
        (CATEGORIES, parsed.categories),
        (KNOWN_BRANDS, parsed.brands),
        (NON_BUSAN_AREAS, parsed.non_busan),
        (BUSAN_POINTS, parsed.points),
        (BUSAN_DISTRICTS, parsed.districts),
        (_busan_areas(), parsed.areas),
    ]
    for vocab, bucket in lookup:
        word = _strip_particles(token, vocab)
        if word:
            if word not in bucket:
                bucket.append(word)
            # '음식점도 알려줘'처럼 '도'가 붙은 장소 종류는 직전 조건을 잇는 후속 요청이다.
            if vocab is CATEGORIES and token != word and token.endswith("도"):
                parsed.followup = True
            return

    for vocab, attr in ((COMPANIONS, "companion"), (TRANSPORT_MODES, "transport_mode"), (INDOOR_WORDS, "indoor")):
        word = _strip_particles(token, vocab)
        if word:
            setattr(parsed, attr, vocab[word])
            return

    for vocab, attr in (
        (RECOMMEND_WORDS, "recommend"),
        (WHERE_WORDS, "where"),
        (REFERENCE_WORDS, "reference"),
    ):
        if _strip_particles(token, vocab):
            setattr(parsed, attr, True)
            return

    if _strip_particles(token, FILLER_WORDS):
        if _strip_particles(token, FOLLOWUP_WORDS):
            parsed.followup = True
        return

    # '알려줄래', '찾아주실래요', '추천해주세요', '부탁해' 같은 요청 어미는 뜻이 같으므로 사전 없이 처리한다.
    if REQUEST_VERB.match(token) or token in REQUEST_ENDINGS:
        if token.startswith(("추천", "골라")):
            parsed.recommend = True
        return

    if region_word and region_word not in CATEGORIES:
        if region_word not in parsed.areas:
            parsed.areas.append(region_word)
        return

    nested = registry.resolve(token)
    if nested.region:
        parsed.districts.append(nested.region.sgg)
        if nested.region.level != "sgg":
            parsed.areas.append(nested.region.name)
        return

    # 사전에 없는 고유 지명/시설명: 접미사로 지점 여부를 판단한다.
    for candidate in [token] + [token[: -len(p)] for p in PARTICLES if token.endswith(p) and len(token) > len(p) + 1]:
        if _is_point_name(candidate):
            parsed.points.append(candidate)
            return
        if _is_dong(candidate):
            parsed.areas.append(candidate)
            return

    parsed.unknown.append(token)


# '3개만', '세 곳', '다섯 군데 정도' 같은 개수 표현
COUNT_PATTERN = re.compile(r"(\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(개|곳|군데)(만|정도|쯤|씩)?")
_COUNT_WORDS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}


def requested_count(message: str) -> int | None:
    """'관광지 3개만 추천해줘' → 3. 개수를 말하지 않으면 None."""
    match = COUNT_PATTERN.search(message)
    if not match:
        return None
    value = match.group(1)
    count = int(value) if value.isdigit() else _COUNT_WORDS[value]
    return count if 1 <= count <= 20 else None


def _strip_count(text: str) -> str:
    return COUNT_PATTERN.sub(" ", text)


def _parse(message: str) -> _Parsed:
    text = _strip_count(message.strip().lower())
    # 띄어 쓴 복합 종류가 각각의 시설 요청으로 분리되지 않게 한다.
    text = re.sub(r"북\s+카페", "북카페", text)
    text = re.sub(r"스터디\s+카페", "스터디카페", text)
    text = re.sub(r"(그|이)\s+근처", r"\1근처", text)
    parsed = _Parsed()
    tokens = re.findall(r"[0-9a-z가-힣]+", text)
    parsed.prefer_region_words |= _region_words_over_food(tokens)
    for token in tokens:
        _classify(token, parsed)
    if parsed.reference or any(word in text for word in ("아까", "나머지")):
        parsed.followup = True
    return parsed


def _eul(word: str) -> str:
    """받침 유무에 맞춰 '을/를'을 붙인다."""
    last = word[-1] if word else ""
    if "가" <= last <= "힣":
        return word + ("을" if (ord(last) - 0xAC00) % 28 else "를")
    return word + "을(를)"


def _format_location(name: str, kind: str) -> str:
    """LLM Intent와 같은 형식으로 맞춘다. 생활권/행정구역은 '부산 ' 접두어를 붙인다."""
    if name == "부산":
        return "부산"
    if kind in {"area", "district"}:
        return f"부산 {name}"
    return name


def _previous_location(previous: UserIntent | None, recent_places: list[dict] | None) -> str | None:
    """'그 근처/거기' 기준 위치: 직전이 특정 장소 검색이면 그 장소, 아니면 직전 위치."""
    if previous is not None:
        exact = any(req.search_mode == "exact_place" for req in previous.requests)
        if exact and recent_places:
            return recent_places[0].get("name")
        if previous.location:
            return previous.location
    return None


def _build_requests(parsed: _Parsed) -> list[dict]:
    requests = []
    seen: set[str] = set()
    for keyword in parsed.categories:
        query, category, subcategory = CATEGORIES[keyword]
        if query in seen:
            continue
        seen.add(query)
        requests.append({
            "query": query,
            "category": category,
            "subcategory": subcategory,
            "indoor": parsed.indoor,
            "result_scope": "default",
            "search_mode": "category",
        })
    return requests


def _intent(intent_type: str, location: str | None, requests: list[dict], parsed: _Parsed) -> UserIntent:
    return UserIntent.model_validate({
        "intent": intent_type,
        "location": location,
        "requests": requests,
        "companion": parsed.companion,
        "transport_mode": parsed.transport_mode,
    })


def needs_session_context(message: str) -> bool:
    """LLM에 직전 Intent를 넘길지 결정한다. 참조 표현이 있거나 생략이 많은 짧은 문장만 넘긴다."""
    text = message.strip()
    markers = ("거기", "그곳", "그쪽", "그 근처", "그중", "그 중", "아까", "그럼", "대신", "다른", "또", "나머지", "번")
    return any(marker in text for marker in markers) or len(text) <= 15


def route_place_query(
    message: str,
    previous_intent: UserIntent | None = None,
    recent_places: list[dict] | None = None,
    pending_intent: UserIntent | None = None,
) -> PlaceRoute:
    """명확한 장소 질의는 코드로 Intent를 만들고, 애매하면 LLM으로 넘긴다."""
    text = message.strip()
    if not text:
        return PlaceRoute("llm", reason="빈 문장")

    # 1) 직전 검색 결과를 비교/선택하는 후속 질문
    if recent_places and COMPARE_PATTERN.search(text):
        return PlaceRoute("compare", reason="직전 결과 비교 질문")

    parsed = _parse(text)

    # 2) 부산 밖 지역을 명시한 경우
    if parsed.non_busan and not parsed.locations:
        area = parsed.non_busan[0]
        return PlaceRoute(
            "unsupported",
            intent=UserIntent(intent="unsupported", location=area),
            message=f"'{area}'은(는) 서비스 지역이 아니에요. 이 서비스는 부산 지역 장소만 찾을 수 있어요.",
            reason="부산 외 지역",
        )

    # 위치 결정
    location: str | None = None
    non_busan_locations = [loc for loc in parsed.locations if loc != "부산"]
    distinct = list(dict.fromkeys(non_busan_locations))
    registry = get_region_registry()
    requests = _build_requests(parsed)
    region_names = distinct
    # 위치 재질문에서 '송정'의 구나 '기장'의 군/읍 선택을 유지한다.
    if pending_intent and pending_intent.location and distinct:
        waiting = registry.resolve(pending_intent.location)
        if waiting.candidates and not any(r.name in distinct for r in waiting.candidates):
            region_names = [pending_intent.location, *distinct]
    region_match = registry.match_names(region_names, previous_intent.location if previous_intent else None)
    if region_match.candidates:
        waiting_requests = build_category_requests(parsed) or ([r.model_dump() for r in pending_intent.requests] if pending_intent else [])
        waiting_location = " ".join(distinct) or (pending_intent.location if pending_intent else None)
        return PlaceRoute(
            "clarify", message=region_match.question,
            intent=_intent("recommend_place", waiting_location, waiting_requests, parsed) if waiting_requests else None,
            reason="지역 이름 또는 범위 중복",
        )
    if region_match.region and (len(region_names) > 1 or registry.match_names(distinct).candidates):
        location = region_match.region.qualified_name
        distinct = []
    if len(distinct) > 1:
        return PlaceRoute("llm", reason=f"위치 표현 여러 개: {distinct}")
    if distinct:
        name = distinct[0]
        kind = "point" if name in parsed.points else "district" if name in parsed.districts else "area"
        location = _format_location(name, kind)
    elif location is None and "부산" in parsed.areas:
        location = "부산"

    if location is None and (parsed.reference or parsed.followup):
        location = _previous_location(previous_intent, recent_places)

    requests = _build_requests(parsed)
    fallback = _intent("recommend_place", location, requests, parsed) if (location and requests) else None

    # 일반 시설명은 특정 기준점이 아니다. '도서관 근처 카페'를 두 종류 검색으로 처리하지 않는다.
    if re.search(r"(?<![0-9a-z가-힣])도서관\s*(?:근처|주변|부근)", text):
        if any(req["category"] == "cafe" for req in requests):
            return PlaceRoute(
                "clarify",
                intent=_intent("recommend_place", None, [req for req in requests if req["category"] == "cafe"], parsed),
                message="어느 도서관 근처의 카페를 찾을까요? 도서관 이름을 알려주세요. (예: 구포도서관 근처 카페)",
                reason="도서관 기준점 불명확",
            )

    # 재질문에 대한 답: '카페 추천해줘' → (어느 지역?) → '서면'
    if pending_intent is not None and location and not requests and not parsed.brands and not parsed.unknown:
        data = pending_intent.model_dump()
        data["location"] = location
        data["companion"] = parsed.companion or data.get("companion")
        data["transport_mode"] = parsed.transport_mode or data.get("transport_mode")
        return PlaceRoute("direct", intent=UserIntent.model_validate(data), reason="재질문 답변(위치)")

    # 3) 사전에 없는 표현이 섞이면 LLM이 해석한다.
    if parsed.unknown:
        return PlaceRoute("llm", reason=f"사전에 없는 표현: {parsed.unknown}", fallback=fallback)

    # 4) 브랜드 검색: '스타벅스 어디 있어', '서면 스타벅스'
    if parsed.brands and not requests:
        brand_requests = [{
            "query": brand, "category": None, "subcategory": None, "indoor": None,
            "result_scope": "default", "search_mode": "brand",
        } for brand in parsed.brands]
        return PlaceRoute("direct", intent=_intent("search_place", location, brand_requests, parsed), reason="브랜드 검색")

    # 5) 특정 장소 검색: '동아대학교 어디야', '부산역 위치'
    if not requests and not parsed.brands and len(parsed.points) == 1 and (parsed.where or not parsed.recommend):
        name = parsed.points[0]
        category = "education" if any(name.endswith(s) for s in ("대학교", "대학", "캠퍼스")) else None
        exact_request = [{
            "query": name, "category": category, "subcategory": None, "indoor": None,
            "result_scope": "default", "search_mode": "exact_place",
        }]
        return PlaceRoute("direct", intent=_intent("search_place", None, exact_request, parsed), reason="특정 장소 검색")

    # 6) 위치 + 장소 종류 → 명확한 질의
    if requests and location:
        intent_type = "search_place" if parsed.where and not parsed.recommend else "recommend_place"
        return PlaceRoute("direct", intent=_intent(intent_type, location, requests, parsed), reason="위치+장소 종류 확인")

    # 7) 장소 종류만 있고 위치가 없음 → 재질문
    if requests and not location:
        labels = _eul(", ".join(req["query"] for req in requests))
        return PlaceRoute(
            "clarify",
            # 사용자가 위치만 답하면 이 Intent에 위치를 채워 바로 검색한다.
            intent=_intent("recommend_place", None, requests, parsed),
            message=f"어느 지역에서 {labels} 찾을까요? 지역이나 기준 장소를 알려주세요. ({LOCATION_EXAMPLES})",
            reason="위치 없음",
        )

    # 8) 의미 있는 단어가 하나도 없음 ('추천해줘', '어디 있어?') → 재질문
    if not parsed.locations and not requests and not parsed.brands and not parsed.companion:
        return PlaceRoute(
            "clarify",
            message=f"어느 지역에서 어떤 장소를 찾을지 알려주세요. 예: '서면 카페 추천해줘', '해운대 맛집 알려줘'",
            reason="위치·장소 종류 없음",
        )

    # 9) 위치만 있고 장소 종류가 없는 경우('서면 갈만한 곳') 등은 LLM이 판단한다.
    return PlaceRoute("llm", reason="장소 종류 불명확", fallback=fallback)


# ---------------------------------------------------------------------------
# 선택지 모드(검색 / 주변 시설)용 키워드 정리
# ---------------------------------------------------------------------------

# 사전에 없는 단어(가게 이름 등)에서만 떼어낼 조사. True/False는 앞 글자 받침 필요 여부.
# '고메이'처럼 이름의 일부일 수 있는 경우를 줄이려고 받침 규칙이 맞을 때만 뗀다.
_KEYWORD_PARTICLES: tuple[tuple[str, bool | None], ...] = (
    ("에서는", None), ("에서", None), ("으로", True), ("이랑", True), ("랑", False),
    ("을", True), ("를", False), ("은", True), ("는", False), ("이", True), ("가", False), ("에", None),
)


def _has_batchim(char: str) -> bool | None:
    if not ("가" <= char <= "힣"):
        return None
    return (ord(char) - 0xAC00) % 28 != 0


def _strip_keyword_particle(token: str) -> str:
    for particle, needs_batchim in _KEYWORD_PARTICLES:
        if token.endswith(particle) and len(token) >= len(particle) + 2:
            stem = token[: -len(particle)]
            if needs_batchim is None or _has_batchim(stem[-1]) is needs_batchim:
                return stem
    return token


@dataclass
class KeywordQuery:
    """검색/주변 시설 모드에서 문장을 '기준 위치'와 'API에 넘길 키워드'로 나눈 결과."""

    location: str | None
    keyword: str
    # 키워드가 사전의 장소 종류 하나뿐일 때의 (검색어, category, subcategory)
    category: tuple[str, str, str | None] | None = None
    brand: str | None = None
    non_busan: list[str] = field(default_factory=list)
    question: str | None = None


def split_query(message: str, previous_location: str | None = None, pending_location: str | None = None) -> KeywordQuery:
    """'해운대 근처 돼지국밥집 알려줘' → location='부산 해운대', keyword='돼지국밥집'.

    요청 어미·조사·'근처' 같은 군더더기는 버리고, 사전에 없는 단어(가게 이름)는 그대로 키워드에 남긴다.
    """
    locations: list[tuple[str, str]] = []
    keyword_parts: list[str] = []
    categories: list[str] = []
    brands: list[str] = []
    non_busan: list[str] = []

    text = re.sub(r"(그|이)\s+근처", r"\1근처", _strip_count(message.strip()))
    text = re.sub(r"북\s+카페", "북카페", text)
    text = re.sub(r"스터디\s+카페", "스터디카페", text)
    preferred = _parse(text).prefer_region_words
    for raw in re.findall(r"[0-9A-Za-z가-힣]+", text):
        token = raw.lower()
        parsed = _Parsed()
        parsed.prefer_region_words = preferred
        _classify(token, parsed)
        if parsed.non_busan:
            non_busan.extend(parsed.non_busan)
        elif parsed.categories:
            categories.extend(parsed.categories)
            keyword_parts.append(parsed.categories[0])
        elif parsed.brands:
            brands.extend(parsed.brands)
            keyword_parts.append(parsed.brands[0])
        elif parsed.points and parsed.points[0].endswith("본점"):
            # '삼진어묵 영도본점'의 '영도본점'은 기준 위치가 아니라 상호의 일부다.
            keyword_parts.append(parsed.points[0])
        elif parsed.points:
            locations.append((parsed.points[0], "point"))
        elif parsed.districts or parsed.areas:
            locations.extend((name, "district") for name in parsed.districts)
            locations.extend((name, "area") for name in parsed.areas if name != "부산")
        elif parsed.unknown:
            keyword_parts.append(_strip_keyword_particle(raw))
        # 그 밖의 요청 어미, '근처', 동행/교통 표현 등은 검색어에서 뺀다.

    location = None
    registry = get_region_registry()
    names = [name for name, _ in locations]
    if pending_location and names:
        waiting = registry.resolve(pending_location)
        if waiting.candidates and not any(region.name in names for region in waiting.candidates):
            names = [pending_location, *names]
    match = registry.match_names(names, previous_location)
    if match.candidates:
        keyword = " ".join(dict.fromkeys(keyword_parts))
        category = CATEGORIES[categories[0]] if len(categories) == 1 and len(keyword_parts) == 1 else None
        return KeywordQuery(location=" ".join(name for name, _ in locations), keyword=keyword, category=category, question=match.question)
    if match.region and (len(names) > 1 or registry.match_names([name for name, _ in locations]).candidates):
        location = match.region.qualified_name
        locations = []
    if locations:
        # 기준 위치는 하나만 쓰고, 나머지 지명은 검색어에 남겨 둔다.
        points = [loc for loc in locations if loc[1] == "point"]
        name, kind = (points or locations)[0]
        location = _format_location(name, kind)
        keyword_parts.extend(loc[0] for loc in locations if loc[0] != name)

    keyword = " ".join(dict.fromkeys(part for part in keyword_parts if part))
    category = CATEGORIES[categories[0]] if len(categories) == 1 and len(keyword_parts) == 1 else None
    brand = brands[0] if len(brands) == 1 and len(keyword_parts) == 1 else None
    return KeywordQuery(location=location, keyword=keyword, category=category, brand=brand, non_busan=non_busan)


def mentions_category(message: str) -> bool:
    """문장에 사전의 장소 종류가 들어 있는지 확인한다."""
    return bool(_parse(message).categories)


# ---------------------------------------------------------------------------
# 길찾기: 직전 결과 장소로 가는 카카오맵 링크
# ---------------------------------------------------------------------------

ROUTE_WORDS = ("길찾기", "길 찾기", "가는 길", "가는길", "어떻게 가", "길 안내", "길안내", "경로", "가는 방법", "가는법")


def requested_route(message: str) -> bool:
    text = message.strip()
    return any(word in text for word in ROUTE_WORDS)


def kakao_route_url(place: dict, origin: tuple[float, float] | None = None) -> str:
    """좌표가 있으면 카카오맵 길찾기, 없으면 장소 검색 링크를 만든다."""
    from urllib.parse import quote

    name = quote(str(place.get("name") or "목적지").replace(",", " "))
    lat, lng = place.get("latitude"), place.get("longitude")
    if lat is None or lng is None:
        return f"https://map.kakao.com/link/search/{name}"
    if origin is not None:
        return f"https://map.kakao.com/link/from/{quote('현재 위치')},{origin[0]},{origin[1]}/to/{name},{lat},{lng}"
    return f"https://map.kakao.com/link/to/{name},{lat},{lng}"


def format_route(
    message: str,
    places: list[dict],
    origin: tuple[float, float] | None = None,
    force: bool = False,
) -> tuple[str, list[dict]] | None:
    """'1번 길찾기', '거기 어떻게 가' → (안내 문장, 링크 목록). 대상이 없으면 None.

    force=True면 길찾기 단어가 없어도(LLM이 get_route로 해석한 경우) 처리한다.
    """
    if not places or not (force or requested_route(message)):
        return None
    selected = select_recent_places(message, places)
    if len(selected) > 3:
        selected = selected[:3]
    if not selected:
        # 번호를 말하지 않으면 1번 장소로 안내한다.
        selected = places[:1]
    links = [
        {"label": f"{place.get('rank', '?')}번 {place.get('name', '장소')} 길찾기", "url": kakao_route_url(place, origin)}
        for place in selected
    ]
    names = ", ".join(f"{place.get('rank', '?')}번 {place.get('name', '장소')}" for place in selected)
    start = "현재 위치에서 " if origin is not None else ""
    return f"{start}{names}까지 가는 길을 카카오맵에서 확인할 수 있어요.", links


def parse_message(message: str) -> "_Parsed":
    """후속 질문 판단 등 다른 모듈에서 쓰는 공개 파서."""
    return _parse(message)


def build_category_requests(parsed: "_Parsed") -> list[dict]:
    requests = _build_requests(parsed)
    for brand in parsed.brands:
        requests.append({
            "query": brand, "category": None, "subcategory": None, "indoor": None,
            "result_scope": "default", "search_mode": "brand",
        })
    return requests
