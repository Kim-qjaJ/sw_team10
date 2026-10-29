import json
import time

import httpx

from app.config import settings
from app.schemas.api import RequestResult
from app.schemas.intent import UserIntent
from app.services.local_router import HELP_REPLY, route_local_reply

SYSTEM_PROMPT = """너는 부산 장소 추천 서비스 SW_team10의 짧은 응답 작성기다.
백엔드가 이미 검색하고 순위를 계산한 결과만 사용한다.
장소 정보, 분위기, 가격, 리뷰, 영업시간 등 제공되지 않은 사실을 추측하지 않는다.
사용자의 요청과 검색 결과를 자연스러운 한국어로 요약한다. 반드시 1~2문장 안에서 답변을 끝낸다.
순위, 주소, 전화번호, URL, 점수는 화면 카드에 따로 표시되므로 답변에서 장소 목록이나 상세정보를 반복하지 않는다.\n결과가 여러 개여도 장소명을 줄줄이 나열하지 말고, 검색 조건과 찾은 결과 개수 정도만 짧게 요약한다.
과장하거나 특정 장소를 임의로 더 추천하지 않는다.
설명만 일반 텍스트로 반환한다."""


class ResponseWriter:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def chat(self, user_message: str) -> tuple[str, float]:
        """인사/감사/서비스 사용법 같은 짧은 대화에 답한다."""
        # Groq를 쓰지 않거나 실패하면 문장에 맞는 템플릿으로 답한다(항상 같은 인사말이 나오지 않게).
        local = route_local_reply(user_message)
        fallback = local.message if local else HELP_REPLY
        if settings.llm_provider.strip().lower() != "groq" or not settings.groq_api_key:
            return fallback, 0.0

        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.groq_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "너는 부산 장소 추천 서비스 SW_team10의 챗봇이다. "
                                "인사, 감사, 작별, 서비스 사용법 같은 짧은 대화에 친근한 한국어로 답한다. 반드시 1~2문장 안에서 답변을 끝낸다. "
                                "부산의 맛집, 카페, 관광지, 놀거리 등을 찾을 수 있다는 서비스 범위 안에서 답하고 "
                                "확인되지 않은 장소 정보는 만들지 않는다."
                            ),
                        },
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.5,
                    "max_tokens": 500,
                },
                timeout=settings.groq_timeout_seconds,
            )
            response.raise_for_status()
            choices = response.json().get("choices") or []
            if not choices:
                return fallback, round((time.perf_counter() - started) * 1000, 2)
            reply = choices[0].get("message", {}).get("content", "").strip()
            return reply or fallback, round((time.perf_counter() - started) * 1000, 2)
        except (httpx.HTTPError, ValueError):
            return fallback, round((time.perf_counter() - started) * 1000, 2)

    async def write_place_detail(
        self,
        user_message: str,
        places: list[dict],
    ) -> tuple[str, float]:
        """직전 추천 결과에 대한 후속 질문을 새 순위 계산 없이 답한다."""
        if not places:
            return "직전 추천 결과에서 해당 장소를 찾지 못했어요. 장소 이름이나 번호를 다시 말해 주세요.", 0.0

        # 현재 Place 모델에 들어있는 사실만 전달한다. 메뉴/영업시간 등 없는 정보는 추측하지 않는다.
        fallback_place = places[0]
        fallback = (
            f"{fallback_place.get('name', '해당 장소')}에 대해 현재 확보된 정보만 확인할 수 있어요. "
            "메뉴나 영업시간처럼 아직 연결되지 않은 정보는 추가 데이터 소스가 필요해요."
        )
        if settings.llm_provider.strip().lower() != "groq" or not settings.groq_api_key:
            return fallback, 0.0

        started = time.perf_counter()
        context = {"question": user_message, "recent_places": places}
        try:
            response = await self.client.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.groq_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "너는 SW_team10의 장소 상세질문 응답기다. 직전 추천 결과의 제공된 데이터만 사용한다. "
                                "질문의 대상 장소를 이름, 번호(1번/첫 번째 등), 문맥으로 찾아 답한다. "
                                "메뉴, 영업시간, 리뷰 등 데이터에 없는 사실은 절대 추측하지 말고 현재 데이터로는 확인할 수 없다고 말한다. "
                                "반드시 한국어 1~2문장으로 답한다."
                            ),
                        },
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 500,
                },
                timeout=settings.groq_timeout_seconds,
            )
            response.raise_for_status()
            choices = response.json().get("choices") or []
            if not choices:
                return fallback, round((time.perf_counter() - started) * 1000, 2)
            reply = choices[0].get("message", {}).get("content", "").strip()
            return reply or fallback, round((time.perf_counter() - started) * 1000, 2)
        except (httpx.HTTPError, ValueError):
            return fallback, round((time.perf_counter() - started) * 1000, 2)

    async def write(
        self,
        user_message: str,
        intent: UserIntent,
        results: list[RequestResult],
    ) -> tuple[str | None, float]:
        if settings.llm_provider.strip().lower() != "groq" or not settings.groq_api_key:
            return None, 0.0

        compact_results = [
            {
                "request": result.request.model_dump(),
                "places": [
                    {
                        "name": place.name,
                        "category": place.category,
                        "distance_m": place.distance_m,
                        "indoor": place.indoor,
                        "score": place.score,
                        "sources": place.sources,
                    }
                    for place in result.places[:5]
                ],
            }
            for result in results
        ]
        context = {
            "user_message": user_message,
            "intent": intent.model_dump(),
            "results": compact_results,
        }

        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.groq_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(context, ensure_ascii=False),
                        },
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
                timeout=settings.groq_timeout_seconds,
            )
            response.raise_for_status()
            choices = response.json().get("choices") or []
            if not choices:
                return None, round((time.perf_counter() - started) * 1000, 2)
            text = choices[0].get("message", {}).get("content", "").strip()
            return text or None, round((time.perf_counter() - started) * 1000, 2)
        except (httpx.HTTPError, ValueError):
            # 자연어 설명 실패가 추천 전체를 실패시키지는 않는다.
            return None, round((time.perf_counter() - started) * 1000, 2)

    # ------------------------------------------------------------------
    # LLM 없이 만드는 요약 (Local Router가 처리한 명확한 질의용)
    # ------------------------------------------------------------------
    @staticmethod
    def template_summary(intent: UserIntent, results: list[RequestResult]) -> str | None:
        """명확한 질의는 LLM 대신 규칙 기반 문장으로 결과를 요약한다."""
        found = [(result, len(result.places)) for result in results if result.places]
        if not found:
            return None

        first = found[0][0]
        if len(found) == 1 and first.result_type == "exact":
            count = len(first.places)
            if count == 1:
                return f"'{first.request.query}' 위치를 찾았어요."
            return f"'{first.request.query}' 관련 장소 {count}곳을 찾았어요. 대표 장소를 맨 위에 두었어요."
        if len(found) == 1 and first.result_type == "brand":
            where = f"{intent.location} " if intent.location else "부산 "
            return f"{where}{first.request.query} 지점 {len(first.places)}곳을 찾았어요."

        where = f"{intent.location}에서 " if intent.location else ""
        parts = ", ".join(f"{result.request.query} {count}곳" for result, count in found)
        if all(result.result_type == "search" for result, _ in found):
            return f"{where}{parts}을 찾았어요. 검색된 순서대로 보여드려요."
        return f"{where}{parts}을 찾아 점수순으로 정리했어요."

    # ------------------------------------------------------------------
    # 직전 검색 결과 비교 (복잡한 후속 질문)
    # ------------------------------------------------------------------
    async def compare(self, user_message: str, places: list[dict]) -> tuple[str, float]:
        """세션에 저장된 실제 검색 결과와 새 조건을 LLM에 넘겨 비교·설명한다."""
        if not places:
            return "비교할 직전 검색 결과가 없어요. 먼저 장소를 검색해 주세요.", 0.0

        names = ", ".join(f"{p.get('rank')}번 {p.get('name')}" for p in places[:5])
        fallback = (
            f"직전 결과({names}) 중에서 골라야 하는데, 지금은 비교를 도와줄 LLM을 사용할 수 없어요. "
            "카드의 거리·카테고리 정보를 참고해 주세요."
        )
        if settings.llm_provider.strip().lower() != "groq" or not settings.groq_api_key:
            return fallback, 0.0

        facts = [
            {key: place.get(key) for key in ("rank", "name", "category", "address", "distance_m", "indoor", "score")}
            for place in places[:10]
        ]
        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.groq_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "너는 SW_team10의 검색 결과 비교 응답기다. 사용자가 직전 검색 결과 중에서 "
                                "새 조건에 맞는 곳을 묻는다. 제공된 장소 목록 안에서만 고르고, 새 장소를 만들지 않는다. "
                                "좌석 수, 분위기, 가격, 리뷰처럼 데이터에 없는 사실은 추측하지 말고 "
                                "'현재 데이터로는 확인할 수 없다'고 밝힌 뒤, 거리·카테고리·실내 여부 등 있는 정보로만 판단한다. "
                                "답변은 한국어 2~3문장이며 장소는 '번호 + 이름'으로 언급한다."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps({"question": user_message, "recent_places": facts}, ensure_ascii=False),
                        },
                    ],
                    "temperature": 0.2,
                    "max_tokens": 500,
                },
                timeout=settings.groq_timeout_seconds,
            )
            response.raise_for_status()
            choices = response.json().get("choices") or []
            reply = choices[0].get("message", {}).get("content", "").strip() if choices else ""
            return reply or fallback, round((time.perf_counter() - started) * 1000, 2)
        except (httpx.HTTPError, ValueError):
            return fallback, round((time.perf_counter() - started) * 1000, 2)
