"""LLM이 만든 설명형 검색어('책 읽을 곳', '조용한 카페')를 장소 검색 API가 알아듣는 업종 단어로 바꾼다.

카카오·네이버 키워드 검색은 가게 이름·업종과 맞춰 찾기 때문에 '동래 책 읽을 곳'처럼 문장을 넣으면
결과가 거의 없거나 엉뚱하다. 그래서 검색할 때만 업종 단어를 쓰고, 화면 표시·점수 계산에는 원래 검색어를 그대로 쓴다.
"""

import re

from app.schemas.intent import PlaceRequest
from app.services.router_lexicon import CATEGORIES

# '~할 곳', '~장소', '~할 데'로 끝나는 설명형 검색어
DESCRIPTIVE = re.compile(r"(곳|장소|데)$")

# 세부 종류(LLM이 만든 영문 subcategory) → 검색 단어. 사전에 있는 세부 종류는 그대로 쓰고, 비슷한 이름은 아래 단어로 맞춘다.
_SUB_WORDS = {sub: word for word, (_, _, sub) in CATEGORIES.items() if sub}
_SUB_WORDS.update({sub: canonical for _, (canonical, _, sub) in CATEGORIES.items() if sub})
_SUB_HINTS = (
    ("book", "북카페"), ("study", "스터디카페"), ("library", "도서관"), ("dessert", "디저트카페"),
    ("brunch", "브런치"), ("bakery", "베이커리"), ("gallery", "갤러리"), ("museum", "박물관"),
    ("exhibit", "전시"), ("park", "공원"), ("beach", "해수욕장"), ("bar", "술집"), ("pub", "술집"),
)

# 세부 종류가 없을 때 쓰는 대분류 단어. other·공공시설처럼 대표 단어가 없으면 원래 검색어를 쓴다.
_CATEGORY_WORDS = {
    "restaurant": "맛집",
    "cafe": "카페",
    "tourism": "관광지",
    "culture": "문화시설",
    "activity": "놀거리",
}


def search_keyword(request: PlaceRequest) -> str:
    query = request.query.strip()
    if request.search_mode != "category" or not DESCRIPTIVE.search(query.replace(" ", "")):
        return query
    sub = (request.subcategory or "").strip().lower()
    if sub:
        if sub in _SUB_WORDS:
            return _SUB_WORDS[sub]
        for hint, word in _SUB_HINTS:
            if hint in sub:
                return word
    return _CATEGORY_WORDS.get(request.category or "", query)


# '갈 만한 곳'처럼 뜻이 넓은 요청은 업종 단어 하나로는 결과가 적어 몇 개로 나눠 찾는다.
_BROAD_WORDS = {
    "activity": ("놀거리", "체험"),
    "tourism": ("관광지", "관광명소"),
    "culture": ("문화시설", "전시"),
}
# 아이·가족과 함께라면 아이가 즐길 만한 업종을 먼저 찾는다.
_FAMILY_ACTIVITY_WORDS = ("키즈카페", "체험", "놀거리")
MAX_SEARCH_WORDS = 3


# 서비스가 실제로 거르는 조건. 이런 말이 있으면 업종 단어로 따로 찾지 않는다 ('무료 전시' → '전시'로 찾으면 유료가 섞인다).
HANDLED_CONDITIONS = ("무료",)
# 업종 단어로 따로 찾으면 Provider의 이름 필터가 풀리는 종류 (전시는 제목·전시장 이름으로 거른다)
NO_SPLIT_CORES = {"전시", "전시회"}


def _core_word(query: str) -> tuple[str | None, str]:
    """'공부하기 좋은 카페' → ('카페', '공부하기 좋은'). 마지막 단어가 업종 단어가 아니면 (None, '').

    앞말 중 지역 이름·업종 단어('해운대', '디저트')는 조건이 아니므로 뺀다.
    """
    words = query.split()
    if len(words) < 2:
        return None, ""
    last = words[-1]
    core = last if last in CATEGORIES else next((w for w in CATEGORIES if len(w) >= 2 and last.endswith(w)), None)
    if core is None or core in NO_SPLIT_CORES or any(word in query for word in HANDLED_CONDITIONS):
        return None, ""
    from app.services.region_registry import get_region_registry

    registry = get_region_registry()
    modifiers = [
        w for w in words[:-1]
        if w not in CATEGORIES and w not in registry.names and w not in registry.lifestyle_names
    ]
    return core, " ".join(modifiers).strip()


def search_keywords(request: PlaceRequest, companion: str | None = None) -> list[str]:
    """장소 검색 API에 넣을 검색어 목록 (앞의 것이 더 구체적). 결과는 합친 뒤 같은 장소를 하나로 정리한다.

    - '조용한 카페'처럼 수식어 + 업종이면 문장 그대로와 업종 단어('카페')로 함께 찾는다.
      문장 그대로는 가게 이름에 그 말이 있는 곳만 나와 결과가 1~2곳뿐일 때가 많다.
    - '갈 만한 곳'처럼 뜻이 넓으면 업종 단어 몇 개로 나눠 찾는다.
    """
    query = request.query.strip()
    if request.search_mode != "category" or not query:
        return [query]
    words: list[str] = []
    if DESCRIPTIVE.search(query.replace(" ", "")):
        first = search_keyword(request)
        words.append(first)
        if first == _CATEGORY_WORDS.get(request.category or "") or first == query:
            if request.category == "activity" and companion == "family":
                words = list(_FAMILY_ACTIVITY_WORDS)
            else:
                words.extend(_BROAD_WORDS.get(request.category or "", ()))
    else:
        words.append(query)
        core, _ = _core_word(query)
        if core:
            words.append(core)
    return list(dict.fromkeys(w for w in words if w))[:MAX_SEARCH_WORDS]


def unverifiable_condition(request: PlaceRequest) -> str | None:
    """'조용한 카페'의 '조용한'처럼 장소 데이터로 확인할 수 없는 조건. 화면에 그 사실을 알린다."""
    if request.search_mode != "category":
        return None
    core, modifier = _core_word(request.query.strip())
    return modifier or None if core else None
