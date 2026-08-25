"""교정 정확도를 잰다. 오탐·미탐·의도 교체를 숫자로 뽑는다.

왜 재는가
---------
"교정이 가끔 이상하다"는 인상이고, 인상으로는 고쳤는지 알 수 없다. 프롬프트를
바꾸기 전에 자를 먼저 만들어야 바꾼 효과가 보인다 — 단어 콘텐츠 때와 같은 순서다.

무엇을 재는가
-------------
1. **오탐률** 맞는 문장(`expect: clean`)에 교정이 나온 비율. 이 앱에서 가장
   비싼 실패다. 왕초보는 맞게 말하고도 빨간 줄을 받으면 그만둔다.
2. **미탐률** 오류 문장(`expect: mistake`)에서 mistake 를 못 잡은 비율.
3. **규칙 위반** `app/tutor/verify.py` 의 결정론적 검사에 걸린 교정.
   의도 교체(`How much is it?` -> `Can I get a coffee, please?`)가 여기 잡힌다.

왜 여러 번 도는가
-----------------
실제 대화 온도(0.7)로 잰다. 온도를 낮추면 숫자는 예뻐지지만 사용자가 겪는 것과
달라진다. 대신 같은 발화를 여러 번 돌려 흔들림을 평균한다.

실행:
    docker compose exec api python scripts/eval_corrections.py
    docker compose exec api python scripts/eval_corrections.py --repeat 5 --strictness strict
    docker compose exec api python scripts/eval_corrections.py --out .review/eval.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tutor.loader import get_scenarios, load_prompt  # noqa: E402
from app.tutor.schemas import TurnResponse, turn_response_schema  # noqa: E402
from app.tutor.service import TutorService  # noqa: E402
from app.tutor.strictness import ORDER as STRICTNESS_ORDER  # noqa: E402
from app.tutor.strictness import show_polish  # noqa: E402
from app.tutor.verify import check_turn, sound_corrections  # noqa: E402

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
PROBES = Path(__file__).parent / "data" / "correction_probes.yaml"


def build_system(scenario_id: str, level: str, strictness: str) -> str:
    scenario = get_scenarios()[scenario_id]
    service = TutorService.__new__(TutorService)
    service._system_template = load_prompt("tutor_system.md")
    service._guardrails = load_prompt("guardrails.md")
    return service.build_system(scenario, level, strictness)


def ask(model: str, system: str, text: str, *, num_ctx: int, temperature: float):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "format": turn_response_schema(),
        "keep_alive": "30m",
        "think": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": 1024},
    }
    with httpx.Client(timeout=600) as client:
        res = client.post(f"{OLLAMA}/api/chat", json=payload)
        res.raise_for_status()
        content = (res.json().get("message") or {}).get("content", "")
    return TurnResponse.model_validate(json.loads(content))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    parser.add_argument("--probes", type=Path, default=PROBES)
    parser.add_argument("--repeat", type=int, default=3, help="발화당 반복 횟수")
    parser.add_argument("--level", default="A1")
    parser.add_argument("--strictness", default="balanced", choices=list(STRICTNESS_ORDER))
    parser.add_argument("--temperature", type=float, default=0.7, help="실제 대화 온도")
    parser.add_argument("--num-ctx", type=int, default=int(os.getenv("OLLAMA_NUM_CTX", "4096")))
    parser.add_argument("--workers", type=int, default=4, help="OLLAMA_NUM_PARALLEL 과 맞춘다")
    parser.add_argument("--out", default="")
    parser.add_argument("--show", type=int, default=12, help="예시로 보여줄 실패 개수")
    args = parser.parse_args()

    probes = yaml.safe_load(args.probes.read_text(encoding="utf-8"))
    jobs = [(p, r) for p in probes for r in range(args.repeat)]
    print(
        f"발화 {len(probes)}개 x {args.repeat}회 = {len(jobs)}회 · {args.model} · "
        f"{args.level}/{args.strictness} · temp {args.temperature} · 동시 {args.workers}"
    )

    systems = {
        (p["scenario"]): build_system(p["scenario"], args.level, args.strictness)
        for p in probes
    }

    def run(job):
        # 예외가 하나라도 새면 ThreadPoolExecutor.map 이 통째로 죽어 84회가 날아간다.
        # 실제로 두 번 겪었다 — 검사 단계(check_turn)가 try 밖에 있었다.
        try:
            return _run(job)
        except Exception as exc:
            return {"probe": job[0], "error": f"{type(exc).__name__}: {exc}"[:200]}

    def _run(job):
        probe, _ = job
        try:
            turn = ask(
                args.model,
                systems[probe["scenario"]],
                probe["say"],
                num_ctx=args.num_ctx,
                temperature=args.temperature,
            )
        except Exception as exc:  # 스키마 실패도 결과다 — 숨기지 않는다
            return {"probe": probe, "error": str(exc)[:200]}
        issues = check_turn(turn, probe["say"])
        # 학습자가 실제로 보는 것만 남긴다. 강도가 polish 를 숨기고(show_polish),
        # verify 가 규칙 위반을 떨어뜨린 뒤에 남는 것이 화면에 뜬다.
        # 모델 원출력을 세면 숨겨지는 교정까지 오탐으로 잡혀 숫자가 부풀려진다.
        kept = sound_corrections(turn, probe["say"])
        if not show_polish(args.strictness):
            kept = [c for c in kept if c.kind == "mistake"]
        return {
            "probe": probe,
            "corrections": [c.model_dump() for c in turn.corrections],
            "visible": [c.model_dump() for c in kept],
            "issues": [{"index": i, "code": s.code, "detail": s.detail} for i, s in issues],
        }

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, jobs))
    elapsed = time.perf_counter() - started

    clean_runs = [r for r in results if r["probe"]["expect"] == "clean" and "error" not in r]
    mistake_runs = [r for r in results if r["probe"]["expect"] == "mistake" and "error" not in r]
    errors = [r for r in results if "error" in r]

    # 오탐: 맞는 문장에 교정이 붙었다. mistake 라벨이 붙은 쪽이 더 나쁘다.
    fp_any = [r for r in clean_runs if r["corrections"]]
    fp_hard = [r for r in clean_runs if any(c["kind"] == "mistake" for c in r["corrections"])]
    fp_visible = [r for r in clean_runs if r["visible"]]
    # 미탐: 오류 문장인데 mistake 가 없다.
    fn = [r for r in mistake_runs if not any(c["kind"] == "mistake" for c in r["corrections"])]

    codes = Counter(i["code"] for r in results if "error" not in r for i in r["issues"])
    total_corrections = sum(len(r["corrections"]) for r in results if "error" not in r)

    def pct(n, d):
        return f"{n / d * 100:5.1f}%" if d else "    -"

    print(f"\n{'=' * 66}\n{elapsed:.0f}초 · 교정 {total_corrections}건 생성 · 스키마 실패 {len(errors)}건")
    print(f"{'=' * 66}")
    print(f"  오탐 · 학습자가 본 것        {pct(len(fp_visible), len(clean_runs))}   {len(fp_visible):3}/{len(clean_runs)}  <- 이게 실제다")
    print(f"  오탐 · 모델 원출력           {pct(len(fp_any), len(clean_runs))}   {len(fp_any):3}/{len(clean_runs)}")
    print(f"    그중 mistake 라벨          {pct(len(fp_hard), len(clean_runs))}   {len(fp_hard):3}/{len(clean_runs)}")
    print(f"  미탐 (오류를 못 잡음)        {pct(len(fn), len(mistake_runs))}   {len(fn):3}/{len(mistake_runs)}")
    print(f"  규칙 위반 (verify.py)        {pct(sum(codes.values()), total_corrections)}   {sum(codes.values()):3}/{total_corrections}")
    for code, n in codes.most_common():
        print(f"      {code:<26} {n}")

    if args.show:
        print(f"\n오탐 예시 (맞는 문장인데 고침)\n{'-' * 66}")
        seen = set()
        for r in fp_any:
            key = r["probe"]["say"]
            if key in seen:
                continue
            seen.add(key)
            for c in r["corrections"]:
                print(f"  [{c['kind']:<7}] {key!r}\n            -> {c['better']!r}")
            if len(seen) >= args.show:
                break

        if fn:
            print(f"\n미탐 예시 (오류를 놓침)\n{'-' * 66}")
            for r in list({r['probe']['say']: r for r in fn}.values())[: args.show]:
                got = [c["better"] for c in r["corrections"]] or ["(교정 없음)"]
                print(f"  {r['probe']['say']!r}  ->  {got}")

    if args.out:
        Path(args.out).write_text(
            json.dumps({"args": vars(args) | {"probes": str(args.probes), "out": args.out},
                        "results": results}, ensure_ascii=False, default=str, indent=1),
            encoding="utf-8",
        )
        print(f"\n원자료: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
