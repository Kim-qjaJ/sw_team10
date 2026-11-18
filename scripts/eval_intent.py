"""AC-002: 평가 질문 세트로 질의 해석 정확도와 응답 시간을 잰다.

실행 중인 서버에 질문을 하나씩 보내고(질문마다 새 세션), 돌아온 Intent를 기대값과 비교한다.
실행: python scripts/eval_intent.py [서버 주소, 기본 http://127.0.0.1:8000]
결과: reports/intent_eval_<날짜>.md
"""

import datetime as dt
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests" / "eval" / "intent_eval.json"
PASS_RATE = 0.85
PAUSE_SECONDS = 2.0  # LLM 호출 한도(분당)를 넘지 않게 질문 사이에 쉰다.


def check(item: dict, intent: dict) -> list[str]:
    """틀린 항목 이름 목록. 비어 있으면 정답."""
    wrong = []
    if intent.get("intent") not in item["intent"]:
        wrong.append("intent")
    if "location" in item and not any(word in (intent.get("location") or "") for word in item["location"]):
        wrong.append("location")
    if "categories" in item:
        got = sorted(r.get("category") or "" for r in intent.get("requests") or [])
        if got not in [sorted(c) for c in item["categories"]]:
            wrong.append("category")
    if "companion" in item and intent.get("companion") != item["companion"]:
        wrong.append("companion")
    if "transport" in item and intent.get("transport_mode") != item["transport"]:
        wrong.append("transport")
    if "indoor" in item and not any(r.get("indoor") == item["indoor"] for r in intent.get("requests") or []):
        wrong.append("indoor")
    return wrong


def main(base_url: str) -> None:
    items = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    rows = []
    with httpx.Client(timeout=60) as client:
        for item in items:
            body = {"message": item["q"], "session_id": f"eval-{uuid.uuid4()}", "limit": 5, "mode": "recommend"}
            started = time.perf_counter()
            response = client.post(f"{base_url.rstrip('/')}/api/v1/recommend", json=body)
            elapsed = (time.perf_counter() - started) * 1000
            data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            intent = data.get("intent") or {}
            wrong = check(item, intent) if response.status_code == 200 else [f"HTTP {response.status_code}"]
            rows.append({"q": item["q"], "route": data.get("route"), "ms": round(elapsed), "wrong": wrong,
                         "got": {k: intent.get(k) for k in ("intent", "location", "companion", "transport_mode")} |
                                {"categories": [r.get("category") for r in intent.get("requests") or []]}})
            print(("O " if not wrong else "X ") + item["q"], wrong or "")
            if data.get("route") == "llm":
                time.sleep(PAUSE_SECONDS)

    correct = sum(1 for r in rows if not r["wrong"])
    rate = correct / len(rows)
    today = dt.date.today().isoformat()
    lines = [
        f"# 질의 해석 평가 결과 ({today})", "",
        f"- 질문 {len(rows)}개 중 {correct}개 정답, 정확도 {rate:.1%} (기준 {PASS_RATE:.0%}) → {'통과' if rate >= PASS_RATE else '미달'}",
        f"- LLM을 거친 질문 {sum(1 for r in rows if r['route'] in ('llm', 'llm_fallback'))}개", "",
        "| 질문 | 처리 | 응답(ms) | 결과 | 틀린 항목 |", "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['q']} | {r['route']} | {r['ms']} | {'정답' if not r['wrong'] else '오답'} | {', '.join(r['wrong'])} |")
    out = ROOT / "reports" / f"intent_eval_{today}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n정확도 {rate:.1%} ({correct}/{len(rows)}) → {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
