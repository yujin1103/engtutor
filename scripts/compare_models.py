"""같은 입력을 여러 모델에 통과시켜 출력을 나란히 저장한다. 반드시 직렬로 돈다.

속도는 bench_latency.py 가 잰다. 여기서 보는 건 **품질**이다.
이 앱에서 모델 크기를 요구하는 곳은 영어 생성이 아니다 — 8단어짜리 A1 문장은
작은 모델도 쓴다. 어려운 건 셋이다.

  1. 오류 진단: 학습자 문장의 무엇이 왜 틀렸는지 정확히 짚는가
  2. 한국어 설명: 번역투가 아닌 자연스러운 해요체로, 사실이 맞게
  3. 구조 준수: 스키마를 지키고 reply 에 한국어를 섞지 않는가

실행:
    docker compose exec api python scripts/compare_models.py --models qwen3:14b qwen3:8b
    docker compose exec api python scripts/compare_models.py --out .review/models.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tutor.loader import get_scenarios, load_prompt  # noqa: E402
from app.tutor.schemas import TurnResponse, turn_response_schema  # noqa: E402
from app.tutor.service import TutorService  # noqa: E402
from app.tutor.strictness import ORDER as STRICTNESS_ORDER  # noqa: E402

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

# 왕초보가 실제로 저지르는 실수를 골고루. 마지막은 오류 없는 문장(빈 배열이 나와야 한다).
PROBES: list[tuple[str, str, str]] = [
    ("cafe_order", "I want ice americano", "ice/iced + 주문 표현"),
    ("cafe_order", "Large", "통하지만 please 가 자연스러움"),
    ("cafe_order", "I go to here yesterday with my friend", "시제 + 불필요한 to"),
    ("cafe_order", "Can I get a hot latte, please?", "오류 없음 -> 빈 배열 기대"),
    ("self_intro", "I am live in Seoul", "am + live 중복"),
    ("self_intro", "I working at hospital since 3 year", "다중 오류"),
    ("self_intro", "My hobby is listen to music", "listen -> listening"),
    ("directions", "Excuse me, how I can go to subway station?", "어순"),
    ("directions", "How long it takes?", "어순 + 조동사"),
    ("directions", "I am lost. Can you help me?", "오류 없음 -> 빈 배열 기대"),
]


def _system(scenario_id: str, strictness: str) -> str:
    scenario = get_scenarios()[scenario_id]
    service = TutorService.__new__(TutorService)
    service._system_template = load_prompt("tutor_system.md")
    service._guardrails = load_prompt("guardrails.md")
    return service.build_system(scenario, "A1", strictness)


def ask(model: str, system: str, user_text: str, *, num_ctx: int) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
        "format": turn_response_schema(),
        "keep_alive": "30m",
        "think": False,
        # 비교니까 온도를 낮춰 흔들림을 줄인다. 품질 차이를 보려는 것이지
        # 샘플링 운을 보려는 게 아니다.
        "options": {"temperature": 0.2, "num_ctx": num_ctx, "num_predict": 1024},
    }
    started = time.perf_counter()
    with httpx.Client(timeout=300) as client:
        res = client.post(f"{OLLAMA}/api/chat", json=payload)
        res.raise_for_status()
        data = res.json()
    elapsed = time.perf_counter() - started

    content = (data.get("message") or {}).get("content", "")
    out: dict = {"elapsed": round(elapsed, 2), "eval_count": data.get("eval_count", 0)}
    try:
        turn = TurnResponse.model_validate(json.loads(content))
        out["ok"] = True
        out.update(turn.model_dump())
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)[:300]
        out["raw"] = content[:400]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=["qwen3:14b", "qwen3:8b"])
    parser.add_argument("--strictness", default="balanced", choices=list(STRICTNESS_ORDER))
    parser.add_argument("--num-ctx", type=int, default=int(os.getenv("OLLAMA_NUM_CTX", "4096")))
    parser.add_argument("--out", default="/workspace/.review/models.json")
    args = parser.parse_args()

    systems = {s: _system(s, args.strictness) for s in {p[0] for p in PROBES}}
    results: list[dict] = []

    for scenario, text, why in PROBES:
        row = {"scenario": scenario, "user": text, "expected": why, "models": {}}
        for model in args.models:
            print(f"[{model}] {scenario} · {text[:40]}", flush=True)
            row["models"][model] = ask(model, systems[scenario], text, num_ctx=args.num_ctx)
        results.append(row)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"strictness": args.strictness, "probes": results}, f, ensure_ascii=False, indent=1)

    print(f"\n{'=' * 70}")
    for model in args.models:
        rows = [r["models"][model] for r in results]
        ok = sum(1 for r in rows if r.get("ok"))
        empty = sum(1 for r in rows if r.get("ok") and not r.get("corrections"))
        hangul_in_reply = sum(
            1 for r in rows if r.get("ok") and any("가" <= c <= "힣" for c in r.get("reply", ""))
        )
        avg = sum(r["elapsed"] for r in rows) / len(rows)
        print(
            f"  {model:<14} 스키마 {ok}/{len(rows)} · 교정없음 {empty}개 · "
            f"reply에 한글 {hangul_in_reply}개 · 평균 {avg:.2f}s"
        )
    print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
