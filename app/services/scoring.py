"""추천 점수 계산 (100점 구조).

항목별 함수는 (0~1 점수, 근거 문장)을 반환하고, 데이터가 없으면 None을 반환해 전체 점수에서 제외한다.
접근성(accessibility)은 별도 서비스 대신 이 파일의 `access_score`로 계산한다.
"""

import math
import re


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리(m)."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# 평가 항목별 배점 (합계 100)
WEIGHTS = {
    "category": 30,    # 검색어 / 카테고리 일치
    "access": 25,      # 이동수단별 접근성
    "condition": 15,   # 사용자 조건 (동행, 실내/야외)
    "weather": 10,     # 날씨 / 실내외
    "cross": 10,       # Kakao / Naver 교차정보
    "busan": 10,       # 부산 특화 데이터
}
# 비·눈이 오면 날씨 배점을 올린다. 10점으로는 기준점 바로 앞 해수욕장이 아쿠아리움보다 위에 남았다 (2026-11 실제 데이터).
# 점수는 사용한 배점 합으로 100점 환산하므로 합계가 100을 넘어도 된다.
RAIN_WEATHER_WEIGHT = 25


def weights_for(weather: dict | None) -> dict[str, int]:
    """현재 날씨에 맞는 항목별 배점."""
    if weather and weather.get("is_raining"):
        return {**WEIGHTS, "weather": RAIN_WEATHER_WEIGHT}
    return dict(WEIGHTS)

# 요청 category → 원본 업종 경로에 나타나는 단어 (Kakao·Naver 실제 업종 경로 기준)
CATEGORY_WORDS = {
    "restaurant": ("음식점", "식당", "한식", "중식", "일식", "양식", "분식", "국밥", "고기", "술집", "주점", "뷔페"),
    "cafe": ("카페", "커피", "디저트", "베이커리", "찻집"),
    "tourism": ("관광", "명소", "해수욕장", "공원", "전망", "여행", "시장", "사찰"),
    "culture": ("문화", "전시", "미술관", "박물관", "도서관", "공연", "영화"),
    # Kakao는 방탈출 카페 상당수를 '음식점 > 카페 > 테마카페'로 분류해서 테마카페는 카페이자 체험으로 본다.
    "activity": ("체험", "스포츠", "레저", "놀이", "방탈출", "볼링", "노래방", "오락", "여가시설", "찜질방", "수상", "요트",
                 "테마카페", "레포츠"),
    "public_facility": ("공공기관", "행정", "구청", "주민센터", "지하철", "기차역", "터미널", "병원", "약국", "우체국"),
    "education": ("학교", "대학", "학원", "교육"),
}

# 관광·문화·체험은 실제 업종 분류의 경계가 흐리다 (같은 아쿠아리움이 Kakao는 문화시설, Naver는 관람,체험).
# 서로 다른 종류로 보지 않고 '비슷한 종류'로 본다.
RELATED_CATEGORIES = {"tourism", "culture", "activity"}

# 목적지가 아닌 부대시설 (주차장, 정문, ATM 등). 공공시설 요청이 아니면 후보에서 뺀다.
# '도로시설' 전체가 아니라 '교차로'만 본다 (광안대교 같은 다리가 도로시설로 분류될 수 있다).
AMENITY_WORDS = ("주차장", "입출구", "ATM", "편의점", "충전소", "안경", "자동차", "숙박", "관리,운영", "화장실", "교차로")

# 기간 행사 (축제, 이벤트). 상시 장소가 아니라서 검색어가 행사를 찾을 때만 정상 점수를 준다.
# 후보에서 빼지는 않는다 (행사 장소를 찾는 사람도 있다).
EVENT_WORDS = ("축제", "페스티벌", "이벤트", "행사")

# 동행별로 어울리는 / 피하는 업종 단어 (Kakao·Naver 업종 경로 기준)
COMPANION_WORDS = {
    "alone": ("도서관", "미술관", "갤러리", "서점", "책방", "전시", "카페", "영화관", "코인노래", "혼밥", "1인"),
    "friend": ("술집", "주점", "호프", "포차", "이자카야", "칵테일", "방탈출", "볼링", "노래방", "보드게임", "당구",
               "오락", "요트", "워터파크", "테마파크"),
    "family": ("키즈", "어린이", "유아", "가족", "패밀리", "뷔페", "놀이", "체험", "공원", "박물관", "과학관",
               "아쿠아리움", "수족관", "동물", "테마파크", "해수욕장", "수목원"),
}
COMPANION_AVOID = {
    "alone": ("키즈", "유아", "어린이", "패밀리"),
    "family": ("술집", "주점", "호프", "포차", "이자카야", "칵테일", "클럽", "바"),
}
# 업종 정보가 없을 때 쓰는 종류 단위 적합도 (후보 간 차이는 없다)
COMPANION_FIT = {
    "alone": {"cafe", "culture", "tourism"},
    "friend": {"cafe", "restaurant", "activity", "tourism"},
    "family": {"restaurant", "tourism", "culture", "activity"},
}
COMPANION_LABEL = {"alone": "혼자", "friend": "친구와", "family": "가족과"}

# 업종으로 실내/야외를 추정하는 단어. 1글자 단어(산, 섬, 숲)는 업종 단계가 정확히 같을 때만 인정한다.
# 실내·야외 단서가 함께 있으면(예: '테마거리 > 카페거리') 판단하지 않는다.
OUTDOOR_WORDS = ("해수욕장", "해변", "공원", "산", "섬", "숲", "등산", "둘레길", "산책로", "도보여행", "갈맷길", "해파랑길",
                 "캠핑", "수목원", "자연휴양림", "해안", "계곡", "저수지", "유원지", "광장", "거리", "골목", "축제",
                 "요트", "수상")
INDOOR_WORDS = ("카페", "음식점", "식당", "한식", "중식", "일식", "양식", "분식", "뷔페", "술집", "주점", "이자카야",
                "미술관", "박물관", "도서관", "영화관", "공연장", "극장", "회관", "백화점", "쇼핑몰", "아쿠아리움",
                "방탈출", "볼링", "노래방", "찜질방", "실내", "서점", "전시관", "과학관", "학원", "PC방")

# 이동수단별 접근성 거리 감쇠 배율 (자동차는 먼 거리도 덜 불리하다)
MODE_SCALE = {"walk": 1.0, "transit": 1.5, "car": 3.0}
# 이 거리 안은 모두 '바로 근처'로 보고 접근성을 같게 준다 (걸어서 5분 안팎).
# 200m와 400m 차이로 카카오·네이버 상위 장소가 밀려나지 않게 한다 (2026-11 검색 대비 비교).
NEAR_M = 400

# '맛집'처럼 식사 장소를 찾는 검색어. 이 검색에서 술집은 음식점이어도 종류 점수를 낮춘다.
MEAL_QUERIES = ("맛집", "밥집", "식당")
BAR_WORDS = ("술집", "주점", "호프", "포차", "이자카야", "칵테일", "와인바")


def _decay(value: float, scale: float) -> float:
    """value가 0이면 1, scale에서 0.25로 감쇠."""
    return 1.0 / (1.0 + (value / max(scale, 1.0)) * 3.0)


def _deepest_category(path: str) -> tuple[str, set[str]]:
    """업종 경로에서 종류 단어가 들어 있는 가장 세부 단계와 그 단계가 가리키는 종류.

    예: '음식점 > 카페 > 커피전문점' → ('커피전문점', {'cafe'})  (상위 '음식점'보다 세부 단계를 따른다)
    """
    for segment in reversed([s.strip() for s in path.split(">") if s.strip()]):
        kinds = {kind for kind, words in CATEGORY_WORDS.items() if any(w in segment for w in words)}
        if kinds:
            return segment, kinds
    return "", set()


def infer_categories(source_categories: dict[str, str]) -> set[str]:
    """Provider 원본 업종으로 장소의 실제 종류를 추정한다. 단서가 없으면 빈 집합."""
    kinds: set[str] = set()
    for path in source_categories.values():
        kinds |= _deepest_category(path)[1]
    return kinds


def related_categories(category: str | None) -> set[str]:
    """요청 종류와 '비슷한 종류'로 볼 종류들 (자기 자신 제외)."""
    return RELATED_CATEGORIES - {category} if category in RELATED_CATEGORIES else set()


def is_amenity(source_categories: dict[str, str]) -> bool:
    """주차장·정문·ATM 같은 부대시설인지 (원본 업종 기준)."""
    return any(word in path for path in source_categories.values() for word in AMENITY_WORDS)


def category_score(category: str | None, subcategory: str | None, query: str,
                   source_categories: dict[str, str], place_category: str | None) -> tuple[float, str] | None:
    """요청 종류·검색어와 장소 업종이 맞는지.

    Provider는 Place.category에 요청 종류를 그대로 넣는 경우가 있어서, 원본 업종(source_categories)이
    있으면 그것만으로 판단하고 place_category는 원본 업종이 없을 때만 쓴다.
    """
    # '식당'·'맛집' 같은 일반 검색어는 업종 글자 일치로 가산하지 않는다.
    # 카카오 업종 경로 끝에 가게 이름이 붙어서('음식점 > 일식 > 후라토식당') 이름에 '식당'이 든 가게만 올라갔다.
    generic = query.strip() in MEAL_QUERIES
    if source_categories:
        path = " > ".join(source_categories.values())
        actual = infer_categories(source_categories)
        segment = next((s for s in (_deepest_category(p)[0] for p in source_categories.values()) if s), "")
        if category and category != "public_facility" and is_amenity(source_categories):
            return 0.1, "목적지가 아닌 부대시설"
        if any(w in path for w in EVENT_WORDS) and not any(w in f"{query} {subcategory or ''}" for w in EVENT_WORDS):
            return 0.2, "기간 행사 (상시 장소 아님)"
        if category and actual and category not in actual and not actual & related_categories(category):
            return 0.1, f"요청과 다른 업종 ({segment})"
        if generic and _has_word(path, BAR_WORDS):
            return 0.4, "식사보다 술자리에 가까운 곳"
        if subcategory and subcategory in path:
            return 1.0, f"세부 종류 '{subcategory}' 일치"
        if query and query in path and not generic:
            return 0.9, f"'{query}' 업종 일치"
        if category and category in actual:
            return 0.75, "요청 종류와 일치"
        if category and actual & related_categories(category):
            return 0.6, f"비슷한 종류 ({segment})"
        return 0.5, "업종으로 종류를 확인하지 못함"

    if not place_category:
        return None
    if subcategory and subcategory in place_category:
        return 1.0, f"세부 종류 '{subcategory}' 일치"
    if query and query in place_category and not generic:
        return 0.9, f"'{query}' 업종 일치"
    if category and place_category == category:
        return 0.75, "요청 종류와 일치"
    return 0.4, "업종 정보가 요청과 직접 일치하지 않음"


def access_score(
    mode: str | None,
    distance_m: float | None,
    scale_m: float,
    lat: float | None,
    lng: float | None,
    stations: list[tuple[str, float, float]] | None,
    place_minutes: dict[str, float | None] | None = None,
) -> tuple[float, str] | None:
    """이동수단(walk/transit/car)별 접근성. 미지정이면 기준점 거리로 계산한다."""
    minutes = (place_minutes or {}).get(mode or "")
    if mode and minutes is not None:
        limit = {"walk": 20.0, "transit": 40.0, "car": 25.0}[mode]
        label = {"walk": "도보", "transit": "대중교통", "car": "자동차"}[mode]
        return _decay(minutes, limit), f"{label} {round(minutes)}분"

    if distance_m is None:
        return None
    scale = scale_m * MODE_SCALE.get(mode or "", 1.0)
    base = _decay(max(0.0, distance_m - NEAR_M), scale)
    dist_text = f"기준점에서 {round(distance_m):,}m"

    if mode == "walk":
        return base, f"도보 기준 {dist_text}"
    if mode == "car":
        return base, f"자동차 기준 {dist_text}"
    if mode == "transit" and lat is not None and lng is not None and stations:
        name, d = min(((s[0], haversine_m(lat, lng, s[1], s[2])) for s in stations), key=lambda x: x[1])
        station = _decay(d, 500.0)
        return 0.6 * station + 0.4 * base, f"{name}까지 {round(d):,}m, {dist_text}"
    return base, dist_text


def category_path(source_categories: dict[str, str], subcategory: str | None = None) -> str:
    """Provider 원본 업종 경로를 하나로 합친다. 예: '여행 > 관광,명소 > 해수욕장'"""
    return " > ".join([*source_categories.values(), *([subcategory] if subcategory else [])])


def _has_word(path: str, words: tuple[str, ...]) -> bool:
    segments = [s.strip() for s in re.split(r"[>,/]", path) if s.strip()]
    return any((w == s) if len(w) == 1 else (w in s) for w in words for s in segments)


def infer_indoor(path: str) -> bool | None:
    """업종 경로로 실내(True)/야외(False)를 추정한다. 단서가 없거나 양쪽 다 있으면 None."""
    indoor, outdoor = _has_word(path, INDOOR_WORDS), _has_word(path, OUTDOOR_WORDS)
    if indoor == outdoor:
        return None
    return indoor


def condition_score(companion: str | None, indoor_wanted: bool | None, indoor: bool | None,
                    path: str, category: str | None = None) -> tuple[float, str] | None:
    """사용자 조건(동행, 명시한 실내/야외)과의 적합도. 장소마다 업종 경로로 계산한다."""
    parts: list[tuple[float, str]] = []
    if companion in COMPANION_LABEL:
        label = COMPANION_LABEL[companion]
        if path and _has_word(path, COMPANION_AVOID.get(companion, ())):
            parts.append((0.1, f"{label} 가기엔 맞지 않는 업종"))
        elif path and _has_word(path, COMPANION_WORDS[companion]):
            parts.append((1.0, f"{label} 가기 좋은 곳"))
        elif path:
            parts.append((0.5, f"{label} 가기 무난한 곳"))
        elif category:
            fit = category in COMPANION_FIT[companion]
            parts.append((0.8 if fit else 0.5, f"{label} 가기 {'좋은' if fit else '무난한'} 종류"))
    if indoor_wanted is not None and indoor is not None:
        match = indoor_wanted == indoor
        parts.append((1.0 if match else 0.0, f"요청한 {'실내' if indoor_wanted else '야외'} 조건 {'일치' if match else '불일치'}"))
    if not parts:
        return None
    return sum(p[0] for p in parts) / len(parts), ", ".join(p[1] for p in parts)


def weather_score(indoor: bool | None, weather: dict | None, inferred: bool = False) -> tuple[float, str] | None:
    """비/눈이면 실내, 맑으면 야외를 약간 선호. inferred=True면 업종으로 추정한 실내/야외."""
    if indoor is None or not weather:
        return None
    note = " (업종으로 추정)" if inferred else ""
    if weather.get("is_raining"):
        return (1.0, f"비 오는 날 실내 장소{note}") if indoor else (0.2, f"비 오는 날 야외 장소{note}")
    return (0.7, f"실내 장소{note}") if indoor else (0.9, f"맑은 날 야외 장소{note}")


def cross_score(sources: list[str], popularity: float | None = None) -> tuple[float, str] | None:
    """Kakao / Naver 등 여러 Provider에서 교차 확인되었는지. 검색 순위·인기 신호(popularity_signal)가 있으면 함께 반영한다."""
    real = set(sources)
    if not real and popularity is None:
        return None
    value = 1.0 if len(real) >= 2 else 0.5
    reason = f"{len(real)}개 출처에서 확인" if real else "단일 출처"
    if popularity is not None:
        value = 0.5 * value + 0.5 * min(max(popularity, 0.0), 1.0)
        reason += ", 검색 순위 반영"
    return value, reason


def busan_score(busan_verified: bool, region_match: str | None) -> tuple[float, str] | None:
    """부산 특화 데이터(부산시 공공데이터 확인, 행정동 경계 내부 여부)."""
    if busan_verified:
        return 1.0, "부산시 공공데이터에서 확인"
    if region_match == "inside":
        return 0.8, "요청 지역 경계 안"
    if region_match == "unverified":
        return 0.5, "지역 경계 미확인"
    return None
