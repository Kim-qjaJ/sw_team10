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

# 요청 category → 원본 업종 경로에 나타나는 단어
CATEGORY_WORDS = {
    "restaurant": ("음식점", "식당", "한식", "중식", "일식", "양식", "분식", "국밥", "고기", "술집", "주점"),
    "cafe": ("카페", "커피", "디저트", "베이커리", "찻집"),
    "tourism": ("관광", "명소", "해수욕장", "공원", "전망", "여행"),
    "culture": ("문화", "전시", "미술관", "박물관", "도서관", "공연", "영화"),
    "activity": ("체험", "스포츠", "레저", "놀이", "방탈출", "볼링"),
    "public_facility": ("공공기관", "행정", "구청", "주민센터", "지하철역", "기차역", "병원", "약국", "우체국"),
    "education": ("학교", "대학", "학원", "교육"),
}

# 동행별로 어울리는 / 피하는 업종 단어 (Kakao·Naver 업종 경로 기준)
COMPANION_WORDS = {
    "alone": ("도서관", "미술관", "갤러리", "서점", "책방", "전시", "카페", "혼밥", "1인"),
    "friend": ("술집", "주점", "호프", "포차", "이자카야", "방탈출", "볼링", "노래방", "보드게임", "당구", "오락"),
    "family": ("키즈", "어린이", "가족", "놀이", "체험", "공원", "박물관", "과학관", "아쿠아리움", "수족관",
               "동물원", "테마파크", "해수욕장"),
}
COMPANION_AVOID = {
    "family": ("술집", "주점", "호프", "포차", "이자카야", "클럽", "바"),
}
# 업종 정보가 없을 때 쓰는 종류 단위 적합도 (후보 간 차이는 없다)
COMPANION_FIT = {
    "alone": {"cafe", "culture", "tourism"},
    "friend": {"cafe", "restaurant", "activity", "tourism"},
    "family": {"restaurant", "tourism", "culture", "activity"},
}
COMPANION_LABEL = {"alone": "혼자", "friend": "친구와", "family": "가족과"}

# 업종으로 실내/야외를 추정하는 단어. 1글자 단어(산)는 업종 단계가 정확히 같을 때만 인정한다.
OUTDOOR_WORDS = ("해수욕장", "해변", "공원", "산", "등산", "둘레길", "산책로", "캠핑", "수목원", "해안", "계곡", "유원지")
INDOOR_WORDS = ("카페", "음식점", "식당", "미술관", "박물관", "도서관", "영화관", "백화점", "쇼핑몰", "아쿠아리움",
                "방탈출", "볼링", "노래방", "서점", "전시관", "과학관", "학원", "PC방")

# 이동수단별 접근성 거리 감쇠 배율 (자동차는 먼 거리도 덜 불리하다)
MODE_SCALE = {"walk": 1.0, "transit": 1.5, "car": 3.0}


def _decay(value: float, scale: float) -> float:
    """value가 0이면 1, scale에서 0.25로 감쇠."""
    return 1.0 / (1.0 + (value / max(scale, 1.0)) * 3.0)


def category_score(category: str | None, subcategory: str | None, query: str,
                   source_categories: dict[str, str], place_category: str | None) -> tuple[float, str] | None:
    """요청 종류·검색어와 장소 업종이 맞는지."""
    path = " ".join(source_categories.values()) + " " + (place_category or "")
    if not path.strip():
        return None
    if subcategory and subcategory in path:
        return 1.0, f"세부 종류 '{subcategory}' 일치"
    if query and query in path:
        return 0.9, f"'{query}' 업종 일치"
    if category and (place_category == category or any(w in path for w in CATEGORY_WORDS.get(category, ()))):
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
    base = _decay(distance_m, scale)
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
    """Kakao / Naver 등 여러 Provider에서 교차 확인되었는지. 인기 신호가 있으면 함께 반영한다."""
    real = set(sources)
    if not real and popularity is None:
        return None
    value = 1.0 if len(real) >= 2 else 0.5
    reason = f"{len(real)}개 출처에서 확인" if real else "단일 출처"
    if popularity is not None:
        value = 0.5 * value + 0.5 * min(max(popularity, 0.0), 1.0)
        reason += ", 인기 신호 반영"
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
