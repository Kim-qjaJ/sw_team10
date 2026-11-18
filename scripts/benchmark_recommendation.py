"""NFR-006: 후보 50개 추천 순위 계산이 100ms 안에 끝나는지 잰다.

외부 API·LLM 없이 Mock 후보만 쓴다.
실행: python scripts/benchmark_recommendation.py [반복 횟수]
"""

import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.intent import PlaceRequest, UserIntent  # noqa: E402
from app.schemas.place import Place  # noqa: E402
from app.services.recommendation import RecommendationService  # noqa: E402

LIMIT_MS = 100.0
CANDIDATES = 50
ANCHOR = (35.1578, 129.0597)  # 서면역
KINDS = [
    ("cafe", "음식점 > 카페 > 커피전문점"),
    ("restaurant", "음식점 > 한식 > 국밥"),
    ("restaurant", "음식점 > 술집 > 호프,요리주점"),
    ("tourism", "여행 > 관광,명소"),
]


def make_places(seed: int = 0) -> list[Place]:
    rnd = random.Random(seed)
    places = []
    for i in range(CANDIDATES):
        category, path = KINDS[i % len(KINDS)]
        sources = rnd.choice([["kakao"], ["naver"], ["kakao", "naver"], ["kakao", "naver", "tour"]])
        places.append(Place(
            name=f"후보{i}",
            category=category,
            latitude=ANCHOR[0] + rnd.uniform(-0.012, 0.012),
            longitude=ANCHOR[1] + rnd.uniform(-0.012, 0.012),
            sources=sources,
            source_categories={sources[0]: path},
            popularity_signal=rnd.random() if "kakao" in sources else None,
            busan_verified=rnd.random() < 0.2,
            region_match="inside",
        ))
    return places


def run(repeat: int = 200) -> dict:
    service = RecommendationService()
    intent = UserIntent(intent="recommend_place", location="서면역", companion="friend", transport_mode="walk",
                        requests=[PlaceRequest(query="카페", category="cafe")])
    stations = [(f"역{i}", ANCHOR[0] + i * 0.002, ANCHOR[1] - i * 0.002) for i in range(10)]
    weather = {"precipitation": "rain", "temperature": 14.0}
    places = make_places()
    service.rank(intent, intent.requests[0], places, anchor=ANCHOR, stations=stations, weather=weather)  # 준비 실행
    times = []
    for _ in range(repeat):
        started = time.perf_counter()
        service.rank(intent, intent.requests[0], places, limit=10, anchor=ANCHOR, stations=stations, weather=weather)
        times.append((time.perf_counter() - started) * 1000)
    times.sort()
    return {
        "candidates": CANDIDATES,
        "repeat": repeat,
        "median_ms": round(statistics.median(times), 2),
        "p95_ms": round(times[int(len(times) * 0.95) - 1], 2),
        "max_ms": round(times[-1], 2),
        "limit_ms": LIMIT_MS,
    }


if __name__ == "__main__":
    result = run(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
    print(result)
    print("통과" if result["p95_ms"] <= LIMIT_MS else "기준 초과")
