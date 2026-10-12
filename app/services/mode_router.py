"""화면 선택지(장소 검색 / 장소 추천 / 주변 시설 찾기)에 쓰는 보조 규칙."""

from app.services.local_router import mentions_category

# 장소 추천 2단계에서 고른 종류를 문장에 붙일 때 쓰는 단어.
# '놀거리'는 사전에 없으므로 LLM이 문화/활동 장소로 해석한다.
CATEGORY_HINTS = {"restaurant": "맛집", "cafe": "카페", "culture": "놀거리"}

# 주변 시설 찾기 반경. 결과가 없으면 다음 반경으로 넓혀 다시 호출한다.
NEARBY_RADII_M = (1000, 3000, 5000)

# 부산광역시를 넉넉히 감싸는 좌표 범위 (기장군·강서구·가덕도 포함)
_BUSAN_LAT = (34.85, 35.45)
_BUSAN_LNG = (128.75, 129.35)


def apply_category_hint(message: str, category: str | None) -> str:
    """'서면'만 입력해도 2단계에서 고른 종류(예: 카페)로 추천하도록 문장에 덧붙인다.

    사용자가 문장에 장소 종류를 직접 적었다면 그 표현을 우선한다.
    """
    hint = CATEGORY_HINTS.get(category or "")
    if not hint or hint in message or mentions_category(message):
        return message
    return f"{message.strip()} {hint}"


def is_in_busan(latitude: float, longitude: float) -> bool:
    return _BUSAN_LAT[0] <= latitude <= _BUSAN_LAT[1] and _BUSAN_LNG[0] <= longitude <= _BUSAN_LNG[1]


def format_radius(radius_m: int) -> str:
    return f"{radius_m / 1000:g}km" if radius_m >= 1000 else f"{radius_m}m"
