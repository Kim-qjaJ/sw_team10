"""추천 점수 계산.

[추천 알고리즘 담당] 인터페이스만 정의된 뼈대입니다.
haversine_m은 파이프라인의 거리 계산에도 쓰는 공용 함수라 동작하는 상태로 둡니다.
"""

import math


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리(m)."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# TODO: 항목별 점수 함수를 구현한다. 각 항목은 0~1 점수와 사람이 읽을 근거 문장을 함께 반환하고,
# 데이터가 없는 항목은 None으로 두어 전체 점수에서 제외한다. 예시 항목:
#   - 거리: 기준점(또는 지역 대표점)까지의 거리
#   - 종류 일치: 요청 종류 vs Place.source_categories(원본 업종 경로, 예: '음식점 > 중식 > 중국요리')
#   - 인기, 동행, 날씨(실내/야외), 교통(가까운 지하철역)
