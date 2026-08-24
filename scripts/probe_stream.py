"""Ollama 원시 스트림을 시간축으로 찍어 보는 진단 도구.

'첫 글자까지 6초'가 나왔을 때 원인이 어디인지 가르는 데 쓴다. 후보는 셋이다.
  1. 프리필(프롬프트 처리)이 오래 걸린다        -> 첫 토큰 자체가 늦게 나온다
  2. <think> 블록을 먼저 뱉는다                 -> 첫 토큰은 빠른데 reply 가 늦다
  3. 다른 요청(배치 등) 뒤에 큐잉돼 있다        -> 요청 시작~첫 토큰이 통째로 길다

1과 3은 첫 토큰 시각으로, 2는 첫 토큰과 reply 시작 사이의 간격으로 구분된다.

실행:
    docker compose exec api python scripts/probe_stream.py
    docker compose exec api python scripts/probe_stream.py --raw   # 앞부분 원문까지
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

# 스크립트로 직접 실행하면 sys.path[0] 이 scripts/ 라 app 을 못 찾는다.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm.partial_json import extract_string  # noqa: E402
from app.tutor.loader import get_scenarios  # noqa: E402
from app.tutor.schemas import turn_response_schema  # noqa: E402
from app.tutor.service import TutorService  # noqa: E402

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
MODEL = os.getenv("OLLAMA_MODEL", "qwen3:14b")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", default="I want ice americano")
    parser.add_argument("--scenario", default="cafe_order")
    parser.add_argument("--raw", action="store_true", help="생성 앞부분 원문을 그대로 출력")
    parser.add_argument("--think", action="store_true", help="일부러 think 를 켜고 비교")
    args = parser.parse_args()

    scenario = get_scenarios()[args.scenario]
    service = TutorService.__new__(TutorService)
    from app.tutor.loader import load_prompt

    service._system_template = load_prompt("tutor_system.md")
    service._guardrails = load_prompt("guardrails.md")
    system = service.build_system(scenario, "A1")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": args.message},
        ],
        "stream": True,
        "format": turn_response_schema(),
        "keep_alive": "30m",
        "options": {"temperature": 0.7, "num_ctx": 4096, "num_predict": 1024},
    }
    if not args.think:
        payload["think"] = False

    print(f"모델 {MODEL} · think={args.think} · 시스템 프롬프트 {len(system)}자")
    print("-" * 66)

    started = time.perf_counter()
    parts: list[str] = []
    first_token: float | None = None
    reply_at: float | None = None
    reply_done_at: float | None = None
    final: dict | None = None

    with httpx.Client(timeout=300) as client:
        with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as res:
            res.raise_for_status()
            for line in res.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                chunk = (event.get("message") or {}).get("content", "")
                if chunk:
                    if first_token is None:
                        first_token = time.perf_counter() - started
                    parts.append(chunk)
                    value, done = extract_string("".join(parts), "reply")
                    if value and reply_at is None:
                        reply_at = time.perf_counter() - started
                    if done and reply_done_at is None:
                        reply_done_at = time.perf_counter() - started
                if event.get("done"):
                    final = event
                    break

    total = time.perf_counter() - started
    text = "".join(parts)

    def show(label: str, value: float | None) -> None:
        print(f"  {label:<28} {'—' if value is None else f'{value:6.2f}s'}")

    show("첫 토큰 (프리필 끝)", first_token)
    show("reply 첫 글자", reply_at)
    show("reply 완성", reply_done_at)
    show("전체 완료", total)

    print("-" * 66)
    if final:
        # ns 단위. load = 모델 로딩, prompt_eval = 프리필, eval = 생성
        for key, label in (
            ("load_duration", "모델 로딩"),
            ("prompt_eval_duration", "프롬프트 처리(프리필)"),
            ("eval_duration", "토큰 생성"),
        ):
            if key in final:
                print(f"  {label:<28} {final[key] / 1e9:6.2f}s")
        if "prompt_eval_count" in final:
            print(f"  {'프롬프트 토큰':<28} {final['prompt_eval_count']:>6}")
        if "eval_count" in final:
            print(f"  {'생성 토큰':<28} {final['eval_count']:>6}")

    think_leaked = "<think>" in text.lower()
    print("-" * 66)
    print(f"  <think> 블록 유출: {'예 ⚠️' if think_leaked else '아니오 ✅'}")
    print(f"  총 생성 문자수: {len(text)}")

    if args.raw:
        print("-" * 66)
        print(text[:600])

    return 0


if __name__ == "__main__":
    sys.exit(main())
