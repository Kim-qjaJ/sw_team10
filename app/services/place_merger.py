"""여러 Provider의 같은 장소를 하나로 합친다.

같은 장소 판정 (모두 만족):
1) 정규화한 이름이 같거나 한쪽이 다른 쪽을 포함한다 (공백·특수문자·대소문자 무시)
2) 좌표가 둘 다 있으면 SAME_PLACE_M 이내, 아니면 정규화한 주소가 같다
3) 서로 다른 Provider의 결과다 (같은 Provider 안의 결과는 별개 장소로 본다)
먼저 들어온 결과(Provider 순서)를 기준으로 두고 비어 있는 값만 다른 결과로 채운다.
"""

import re

from app.schemas.place import Place
from app.services.scoring import haversine_m

# 같은 장소로 볼 최대 좌표 거리(m)
SAME_PLACE_M = 200

# 출처 정보는 따로 합치고, 점수 필드는 병합 뒤 추천 단계에서 채운다.
_SKIP_FILL = {
    "id", "name", "latitude", "longitude", "sources", "source_ids", "source_categories", "source_category_codes",
    "popularity_signal", "busan_verified", "score", "rank_score", "data_confidence", "score_breakdown",
}


def _norm_name(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


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
        for place in places:
            target = next((m for m in merged if self._is_same(m, place)), None)
            if target is None:
                merged.append(place.model_copy(deep=True))
            else:
                self._absorb(target, place)
        return merged

    @staticmethod
    def _is_same(base: Place, other: Place) -> bool:
        if set(base.sources) & set(other.sources):
            return False
        return _same_name(_norm_name(base.name), _norm_name(other.name)) and _same_location(base, other)

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
