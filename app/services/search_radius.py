"""검색 반경 기준 (SRS FR-008·FR-036·FR-037, NFR-010).

반경을 바꿀 때는 이 파일만 고친다. 다른 모듈은 여기 값을 가져다 쓴다.
(생활권별 반경은 지역 사전 data/regions의 radius_m 열에서 정한다.)
"""

# 현재 위치·특정 장소가 기준점일 때 일반 추천에 쓰는 반경
POINT_RADIUS_M = 1500

# 주변 시설 찾기와 '범위 넓혀줘'에서 차례로 넓히는 단계 (1.5km → 3km → 5km)
EXPAND_STEPS_M = (1500, 3000, 5000)
MAX_EXPAND_M = EXPAND_STEPS_M[-1]

# TourAPI 위치기반 목록: 반경을 따로 정하지 않았을 때의 기본값과 API 상한
TOUR_DEFAULT_RADIUS_M = 5000
TOUR_MAX_RADIUS_M = 20000
