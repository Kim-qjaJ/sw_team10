"""LLM이 만든 설명형 검색어('책 읽을 곳', '먹을 곳')를 장소 검색 API가 알아듣는 업종 단어로 바꾼다.

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
