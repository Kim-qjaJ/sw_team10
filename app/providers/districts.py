"""부산 구·군 이름과 법정동 시군구 코드. 공공데이터 Provider(TourAPI·부산명소정보)가 함께 쓴다."""

from __future__ import annotations

import re

# 부산 구·군 → 법정동 시군구 코드 (앞 두 자리 26 제외, TourAPI lDongSignguCd 형식)
BUSAN_DISTRICTS = {
    "중구": "110", "서구": "140", "동구": "170", "영도구": "200", "부산진구": "230", "동래구": "260",
    "남구": "290", "북구": "320", "해운대구": "350", "사하구": "380", "금정구": "410", "강서구": "440",
    "연제구": "470", "수영구": "500", "사상구": "530", "기장군": "710",
}
# '구'·'군' 없이 불러도 구·군으로 보는 이름. 한 글자 이름('중', '서')은 다른 말과 헷갈려 뺀다.
DISTRICT_ALIASES = {name[:-1]: name for name in BUSAN_DISTRICTS if len(name) >= 3}


def district_name(location: str | None) -> str | None:
    """'부산 해운대구', '해운대', '부산광역시 기장군' → '해운대구'·'기장군'. 구·군을 말하지 않았으면 None."""
    if not location:
        return None
    for token in location.split():
        token = re.sub(r"^(부산광역시|부산시|부산)", "", token)
        if token in BUSAN_DISTRICTS:
            return token
        if token in DISTRICT_ALIASES:
            return DISTRICT_ALIASES[token]
    return None


def district_code(location: str | None) -> str | None:
    """구·군의 법정동 시군구 코드. 구·군을 말하지 않았으면 None."""
    name = district_name(location)
    return BUSAN_DISTRICTS[name] if name else None
