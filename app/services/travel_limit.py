"""'걸어서 5분 이내', '차로 10분 안에' 같은 이동 시간 상한 (2026-11-23 검토 반영).

예전에는 상한을 결과에 적용하지 않아 '5분 이내 카페 5곳'이라고 답하면서 도보 6분 거리를 보여 줬다.
경로 시간(TMAP)이 있으면 그 시간을, 없으면 직선거리로 넉넉하게 추정한 시간을 쓴다.
"""

import re

_MODE_WORDS = (
    ("walk", r"걸어서|걸어|도보로?|걸으면"),
    ("car", r"차로|자동차로|운전해서|택시로|차 타고"),
    ("transit", r"대중교통으로?|버스로|지하철로|버스 타고|지하철 타고"),
)
_LIMIT = re.compile(r"(\d{1,3})\s*분\s*(이내|안에|안쪽|내로|내에|내|거리|이하|정도|컷)")
LABELS = {"walk": "걸어서", "car": "차로", "transit": "대중교통으로"}
# 직선거리 → 분 추정 (실제 길은 직선보다 약 1.3배 길다). 걷기 4km/h, 시내 차량 18km/h, 대중교통 12km/h + 대기 5분.
_METERS_PER_MIN = {"walk": 67 / 1.3, "car": 300 / 1.3, "transit": 200 / 1.3}
_WAIT = {"walk": 0, "car": 0, "transit": 5}
_FIELDS = {"walk": "walking_minutes", "car": "driving_minutes", "transit": "transit_minutes"}


def travel_limit(message: str | None, transport_mode: str | None = None) -> tuple[str, int] | None:
    """문장에서 (이동수단, 분)을 읽는다. 수단을 말하지 않았으면 해석된 이동수단, 그것도 없으면 걷기."""
    if not message:
        return None
    match = _LIMIT.search(message)
    if not match:
        return None
    mode = next((m for m, words in _MODE_WORDS if re.search(words, message)), None) or transport_mode or "walk"
    return mode, int(match.group(1))


def apply_travel_limit(intent, message: str | None):
    """문장의 이동 시간 상한을 Intent에 담는다. 이미 값이 있으면(후속 질문) 새로 말한 경우에만 바꾼다."""
    rule = travel_limit(message, intent.transport_mode)
    if rule is None:
        return intent
    mode, minutes = rule
    return intent.model_copy(update={"transport_mode": intent.transport_mode or mode, "max_travel_minutes": minutes})


def estimated_minutes(place, mode: str) -> tuple[float | None, bool]:
    """(분, 추정 여부). 경로 시간이 있으면 그 값, 없으면 직선거리로 추정한다."""
    minutes = getattr(place, _FIELDS[mode], None)
    if minutes is not None:
        return float(minutes), False
    distance = place.straight_distance_m if place.straight_distance_m is not None else place.distance_m
    if distance is None:
        return None, True
    return distance / _METERS_PER_MIN[mode] + _WAIT[mode], True


def within_limit(places: list, mode: str, minutes: int) -> list:
    """상한 안에 드는 장소만 남긴다. 시간을 알 수 없는 장소는 뺀다."""
    kept = []
    for place in places:
        value, _ = estimated_minutes(place, mode)
        if value is not None and value <= minutes:
            kept.append(place)
    return kept
