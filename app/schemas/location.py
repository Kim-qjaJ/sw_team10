from typing import Literal

from pydantic import BaseModel

LocationMode = Literal[
    "current_location",
    "specific_place",
    "area_anchor",
    "unresolved",
    # Kakao 검색은 성공했지만 부산에서 해당 위치를 찾지 못한 경우
    "not_found",
    "ambiguous",
    # 서면·센텀 같은 생활권. 기준 시설 좌표와 반경으로 검색한다.
    "lifestyle_area",
]


class LocationReference(BaseModel):
    """Resolved reference point used for nearby search and accessibility scoring."""

    mode: LocationMode
    original_text: str | None = None
    resolved_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source: str | None = None
    region_code: str | None = None
    region_level: str | None = None
    coordinate_accuracy: str | None = None
    boundary_scope: str | None = None
    question: str | None = None
    # 생활권 반경(m) 또는 행정구역 크기(같은 면적의 원 반지름). 거리 점수 스케일에 쓴다.
    radius_m: int | None = None
