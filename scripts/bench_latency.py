"""턴 지연을 필드별로 쪼개 재는 벤치마크. 반드시 **직렬**로 돈다.

왜 필드별인가
-------------
출력 토큰이 지연의 거의 전부다(프리필 0.02s vs 생성 2.06s). 그러면 다음 질문은
"어느 필드가 그 2초를 먹는가"이고, 그건 추정할 게 아니라 재면 된다.

스트리밍 버퍼에 각 키가 처음 등장하는 시각을 찍으면 필드 경계가 그대로 나온다.
`"corrections"` 가 보이는 순간이 reply 가 끝난 순간이고, `"say_en"` 이 보이는
순간이 corrections 가 끝난 순간이다.

왜 직렬인가
-----------
OLLAMA_NUM_PARALLEL=1 이라 동시에 두 요청을 보내면 뒤엣것은 큐에서 기다린다.
그 대기 시간이 측정값에 섞이면 숫자가 통째로 무의미해진다 — 실제로 배치와
겹쳤을 때 2.39s 가 8.64s 로 보였다.

실행:
    docker compose exec api python scripts/bench_latency.py
    docker compose exec api python scripts/bench_latency.py --model qwen3:8b --runs 5
    docker compose exec api python scripts/bench_latency.py --compare qwen3:14b qwen3:8b
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm.partial_json import extract_string  # noqa: E402
from app.tutor.loader import get_scenarios, load_prompt  # noqa: E402
from app.tutor.schemas import TurnResponse, turn_response_schema  # noqa: E402
from app.tutor.service import TutorService  # noqa: E402

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

# 이 키가 버퍼에 처음 보이는 순간 = 앞 필드가 끝난 순간.
FIELD_ORDER = ("reply", "corrections", "say_en", "say_more", "hint_ko")

# 왕초보가 실제로 칠 법한 입력. 교정이 나오는 것과 안 나오는 것을 섞는다.
TURNS = [
    "I want ice americano",
    "Large",
    "I go to here yesterday with my friend",
    "Thank you so much!",
]


@dataclass
class Run:
    first_token: float = 0.0
    total: float = 0.0
    prefill: float = 0.0
    eval_time: float = 0.0
    prompt_tokens: int = 0
    eval_tokens: int = 0
    chars: int = 0
    valid: bool = False
    corrections: int = 0
    marks: dict[str, float] = field(default_factory=dict)

    @property
    def queue_wait(self) -> float:
        """Ollama 자체 계측에 안 잡히는 시간. 이게 크면 다른 요청에 밀린 것이다."""
        return max(0.0, self.total - self.prefill - self.eval_time)

    @property
    def tok_per_sec(self) -> float:
        return self.eval_tokens / self.eval_time if self.eval_time else 0.0


def _system(scenario_id: str) -> str:
    scenario = get_scenarios()[scenario_id]
    service = TutorService.__new__(TutorService)
    service._system_template = load_prompt("tutor_system.md")
    service._guardrails = load_prompt("guardrails.md")
    return service.build_system(scenario, "A1")


def one_run(model: str, system: str, user_text: str, *, num_ctx: int) -> Run:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "stream": True,
        "format": turn_response_schema(),
        "keep_alive": "30m",
        "think": False,
        "options": {"temperature": 0.7, "num_ctx": num_ctx, "num_predict": 1024},
    }

    run = Run()
    parts: list[str] = []
    started = time.perf_counter()

    with httpx.Client(timeout=300) as client:
        with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as res:
            res.raise_for_status()
            for line in res.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                chunk = (event.get("message") or {}).get("content", "")
                if chunk:
                    if not run.first_token:
                        run.first_token = time.perf_counter() - started
                    parts.append(chunk)
                    text = "".join(parts)
                    now = time.perf_counter() - started
                    for key in FIELD_ORDER:
                        if key not in run.marks and f'"{key}"' in text:
                            run.marks[key] = now
                    if "reply_done" not in run.marks:
                        _, done = extract_string(text, "reply")
                        if done:
                            run.marks["reply_done"] = now
                if event.get("done"):
                    run.total = time.perf_counter() - started
                    run.prefill = event.get("prompt_eval_duration", 0) / 1e9
                    run.eval_time = event.get("eval_duration", 0) / 1e9
                    run.prompt_tokens = event.get("prompt_eval_count", 0)
                    run.eval_tokens = event.get("eval_count", 0)
                    break

    text = "".join(parts)
    run.chars = len(text)
    try:
        turn = TurnResponse.model_validate(json.loads(text))
        run.valid = True
        run.corrections = len(turn.corrections)
    except Exception:
        run.valid = False
    return run


def bench(model: str, *, runs: int, scenario: str, num_ctx: int) -> list[Run]:
    system = _system(scenario)
    print(f"\n{'=' * 70}\n{model}  ·  시스템 프롬프트 {len(system)}자  ·  num_ctx={num_ctx}\n{'=' * 70}")

    # 워밍업: 첫 요청은 모델 로딩과 프리필 캐시 채우기가 섞여 대표성이 없다.
    print("워밍업...", end=" ", flush=True)
    warm = one_run(model, system, "hello", num_ctx=num_ctx)
    print(f"{warm.total:.1f}s (버림)")

    out: list[Run] = []
    for i in range(runs):
        text = TURNS[i % len(TURNS)]
        r = one_run(model, system, text, num_ctx=num_ctx)
        out.append(r)
        ok = "✅" if r.valid else "❌스키마실패"
        queue = f" ⚠️큐{r.queue_wait:.1f}s" if r.queue_wait > 0.3 else ""
        print(
            f"  {i + 1}. {text[:34]:<34} "
            f"첫글자 {r.marks.get('reply', 0):.2f}s · 전체 {r.total:.2f}s · "
            f"{r.eval_tokens:>3}tok {r.tok_per_sec:.0f}tok/s · 교정 {r.corrections} {ok}{queue}"
        )
    return out


def _seg(runs: list[Run], start: str | None, end: str) -> float:
    """구간 소요 시간의 중앙값."""
    vals = []
    for r in runs:
        a = 0.0 if start is None else r.marks.get(start)
        b = r.total if end == "END" else r.marks.get(end)
        if a is not None and b is not None:
            vals.append(b - a)
    return statistics.median(vals) if vals else 0.0


def report(model: str, runs: list[Run]) -> dict[str, float]:
    ok = [r for r in runs if r.valid]
    med = lambda f: statistics.median([f(r) for r in runs])  # noqa: E731

    total = med(lambda r: r.total)
    print(f"\n── {model} 중앙값 ({len(ok)}/{len(runs)} 스키마 통과) ──")
    print(f"  프리필            {med(lambda r: r.prefill):6.2f}s   (프롬프트 {int(med(lambda r: r.prompt_tokens))}토큰)")
    print(f"  생성              {med(lambda r: r.eval_time):6.2f}s   ({int(med(lambda r: r.eval_tokens))}토큰 · {med(lambda r: r.tok_per_sec):.0f} tok/s)")
    print(f"  큐 대기           {med(lambda r: r.queue_wait):6.2f}s   (0에 가까워야 정상)")
    print(f"  전체              {total:6.2f}s")

    print("\n  필드별 구간 (사용자가 보는 순서대로)")
    segments = [
        ("첫 글자까지", None, "reply"),
        ("reply 생성", "reply", "reply_done"),
        ("corrections", "corrections", "say_en"),
        ("say_en", "say_en", "say_more"),
        ("say_more", "say_more", "hint_ko"),
        ("hint_ko + 마무리", "hint_ko", "END"),
    ]
    for label, a, b in segments:
        secs = _seg(runs, a, b)
        share = secs / total * 100 if total else 0
        bar = "█" * round(share / 3)
        print(f"    {label:<18} {secs:5.2f}s  {share:4.1f}%  {bar}")

    reply_done = med(lambda r: r.marks.get("reply_done", r.total))
    print(f"\n  👤 사용자 체감: 첫 글자 {med(lambda r: r.marks.get('reply', 0)):.2f}s · "
          f"답장 완성 {reply_done:.2f}s · 교정까지 {total:.2f}s")
    return {"total": total, "reply_done": reply_done, "tok_s": med(lambda r: r.tok_per_sec)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    parser.add_argument("--compare", nargs="+", default=None, help="여러 모델을 차례로 잰다")
    parser.add_argument("--runs", type=int, default=6)
    parser.add_argument("--scenario", default="cafe_order")
    parser.add_argument("--num-ctx", type=int, default=int(os.getenv("OLLAMA_NUM_CTX", "4096")))
    args = parser.parse_args()

    models = args.compare or [args.model]
    summary: dict[str, dict[str, float]] = {}
    for model in models:
        summary[model] = report(model, bench(model, runs=args.runs, scenario=args.scenario, num_ctx=args.num_ctx))

    if len(models) > 1:
        print(f"\n{'=' * 70}\n비교\n{'=' * 70}")
        base = summary[models[0]]
        for model, s in summary.items():
            delta = f"  ({(base['total'] - s['total']):+.2f}s vs {models[0]})" if model != models[0] else ""
            print(f"  {model:<14} 전체 {s['total']:.2f}s · 답장완성 {s['reply_done']:.2f}s · {s['tok_s']:.0f} tok/s{delta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
