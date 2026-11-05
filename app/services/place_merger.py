"""여러 Provider의 같은 장소를 하나로 합친다.

같은 장소 판정 (서로 다른 Provider의 결과일 때만):
1) 정규화한 이름이 같거나 한쪽이 다른 쪽을 포함하고, 좌표가 SAME_PLACE_M 이내(좌표가 없으면 주소 일치)
2) 또는 이름 유사도가 SIMILAR_NAME_RATIO 이상이고, 좌표가 SIMILAR_PLACE_M 이내 (표기 오타 보완)
이름 정규화: 공백·특수문자·대소문자 무시, 숫자는 한글로 읽는다 ('송정3대국밥' = '송정삼대국밥').
같은 Provider 안의 결과는 별개 장소로 본다.
먼저 들어온 결과(Provider 순서)를 기준으로 두고 비어 있는 값만 다른 결과로 채운다.

검색 순위 신호: Kakao(정확도순)·Naver(정확도순)가 준 순서를 popularity_signal(0~1)로 남긴다.
여러 Provider에 있으면 높은 값을 쓴다. Provider가 이미 popularity_signal을 줬으면 그 값을 쓴다.
추천 점수의 교차정보 항목이 이 값을 쓴다 (scoring.cross_score).
"""

import re
from difflib import SequenceMatcher

from app.schemas.place import Place
from app.services.scoring import haversine_m

# 같은 장소로 볼 최대 좌표 거리(m)
SAME_PLACE_M = 200
# 이름이 포함 관계가 아니어도, 이만큼 가깝고 이름이 이만큼 비슷하면 같은 장소로 본다.
# 0.85는 '이재모피자 본점'/'이재모피자 서면점'(0.8) 같은 지점 차이를 합치지 않는 값이다.
SIMILAR_PLACE_M = 50
SIMILAR_NAME_RATIO = 0.85

# 결과 순서가 관련도 순인 Provider와, 순위 신호가 0이 되는 순위 (Kakao 한 번에 15건)
RANKED_PROVIDERS = {"kakao", "naver"}
PROVIDER_RANK_SPAN = 15

_DIGITS = str.maketrans("0123456789", "영일이삼사오육칠팔구")

# 출처 정보는 따로 합치고, 점수 필드는 병합 뒤 추천 단계에서 채운다.
_SKIP_FILL = {
    "id", "name", "latitude", "longitude", "sources", "source_ids", "source_categories", "source_category_codes",
    "popularity_signal", "busan_verified", "score", "rank_score", "data_confidence", "score_breakdown",
}


def _rank_signal(source: str, rank: int) -> float | None:
    """Provider 안에서의 순위(0부터)를 0~1 신호로. 1위가 1.0이고 PROVIDER_RANK_SPAN위에서 0이 된다."""
    if source not in RANKED_PROVIDERS:
        return None
    return round(max(0.0, 1.0 - rank / PROVIDER_RANK_SPAN), 3)


def _norm_name(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum()).translate(_DIGITS)


def _norm_address(address: str | None) -> str:
    if not address:
        return ""
    text = re.sub(r"^부산광역시", "부산", address.strip())
    return re.sub(r"[\s,]", "", text)


def _same_name(a: str, b: str) -> bool:
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return short == long or (len(short) >= 2 and short in long)


def _similar_nearby(a: Place, b: Place, name_a: str, name_b: str) -> bool:
    if None in (a.latitude, a.longitude, b.latitude, b.longitude) or min(len(name_a), len(name_b)) < 4:
        return False
    if haversine_m(a.latitude, a.longitude, b.latitude, b.longitude) > SIMILAR_PLACE_M:
        return False
    return SequenceMatcher(None, name_a, name_b).ratio() >= SIMILAR_NAME_RATIO


def _same_location(a: Place, b: Place) -> bool:
    if None not in (a.latitude, a.longitude, b.latitude, b.longitude):
        return haversine_m(a.latitude, a.longitude, b.latitude, b.longitude) <= SAME_PLACE_M
    addr_a, addr_b = _norm_address(a.address), _norm_address(b.address)
    return bool(addr_a) and addr_a == addr_b


class PlaceMerger:
    def merge(self, places: list[Place]) -> list[Place]:
        """이름·주소·좌표(SAME_PLACE_M 이내)로 같은 장소를 찾아 하나로 합친다.

        합칠 때 sources, source_ids, source_categories, source_category_codes는 모두 보존한다.
        입력 순서(Provider 우선순위)를 유지하고, 원본 Place는 바꾸지 않는다.
        """
        merged: list[Place] = []
        ranks: dict[str, int] = {}
        for place in places:
            source = place.sources[0] if place.sources else ""
            rank = ranks.get(source, 0)
            ranks[source] = rank + 1
            signal = place.popularity_signal if place.popularity_signal is not None else _rank_signal(source, rank)
            target = next((m for m in merged if self._is_same(m, place)), None)
            if target is None:
                target = place.model_copy(deep=True)
                merged.append(target)
            else:
                self._absorb(target, place)
            if signal is not None:
                target.popularity_signal = max(target.popularity_signal or 0.0, signal)
        return merged

    @staticmethod
    def _is_same(base: Place, other: Place) -> bool:
        if set(base.sources) & set(other.sources):
            return False
        name_a, name_b = _norm_name(base.name), _norm_name(other.name)
        if _same_name(name_a, name_b) and _same_location(base, other):
            return True
        return _similar_nearby(base, other, name_a, name_b)

    @staticmethod
    def _absorb(base: Place, other: Place) -> None:
        """other의 정보를 base에 합친다. 기준(base)의 값이 있으면 유지한다."""
        for source in other.sources:
            if source not in base.sources:
                base.sources.append(source)
        for field in ("source_ids", "source_categories", "source_category_codes"):
            target = getattr(base, field)
            for key, value in getattr(other, field).items():
                target.setdefault(key, value)

        if base.latitude is None or base.longitude is None:
            base.latitude, base.longitude = other.latitude, other.longitude
        for field in Place.model_fields:
            if field not in _SKIP_FILL and getattr(base, field) is None:
                setattr(base, field, getattr(other, field))

        if other.popularity_signal is not None:
            base.popularity_signal = max(base.popularity_signal or 0.0, other.popularity_signal)
        base.busan_verified = base.busan_verified or other.busan_verified
