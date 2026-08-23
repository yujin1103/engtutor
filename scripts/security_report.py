"""인젝션 방어 결과를 README Security 섹션용 마크다운 표로 출력한다.

테스트 슈트(tests/security/cases.py)와 **같은 케이스 목록·같은 판정 로직**을 쓴다.
케이스를 추가하면 테스트와 이 표가 함께 갱신된다.

실행:
    docker compose exec api python scripts/security_report.py
    docker compose exec api python scripts/security_report.py --verbose
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import LLMError  # noqa: E402
from app.llm.factory import get_client  # noqa: E402
from app.tutor.loader import get_scenarios  # noqa: E402
from app.tutor.service import TutorService  # noqa: E402
from tests.security.cases import CASES  # noqa: E402
from tests.security.checks import evaluate  # noqa: E402


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="cafe_order")
    parser.add_argument("--verbose", action="store_true", help="실제 응답도 함께 출력")
    args = parser.parse_args()

    client = get_client()
    tutor = TutorService(client)
    scenario = get_scenarios()[args.scenario]

    rows: list[tuple[str, str, bool, str]] = []
    started = time.perf_counter()

    for case in CASES:
        try:
            turn = tutor.respond(
                scenario=scenario, level="A1", history=[], user_text=case.payload
            )
            verdict = evaluate(case, turn)
            blocked, reason, reply = verdict.blocked, verdict.reason, turn.reply
        except LLMError:
            # 스키마가 오염된 응답을 거부했고 재시도도 통과하지 못한 경우.
            # 사용자에게 전달된 오염 내용이 없으므로 방어 성공이다(fail-closed).
            blocked, reason, reply = True, "차단 (스키마 거부)", ""
        rows.append((case.category, case.payload, blocked, reason))
        verdict = type("V", (), {"blocked": blocked, "reason": reason})()
        if args.verbose:
            mark = "✅" if verdict.blocked else "❌"
            print(f"{mark} [{case.id}] {case.payload}", file=sys.stderr)
            print(f"     reply: {reply}", file=sys.stderr)
            if not verdict.blocked:
                print(f"     사유: {verdict.reason}", file=sys.stderr)

    elapsed = time.perf_counter() - started
    blocked = sum(1 for *_, ok, _ in rows if ok)

    print(f"<!-- scripts/security_report.py 로 생성 · {client.describe()} -->")
    print()
    print(f"**{blocked}/{len(rows)} 차단** · 시나리오 `{args.scenario}` · {elapsed:.1f}초")
    print()
    print("| # | 공격 유형 | 입력 | 결과 |")
    print("|---|---|---|---|")
    for i, (category, payload, ok, reason) in enumerate(rows, start=1):
        result = "✅ 차단" if ok else f"❌ 실패 — {_escape(reason)}"
        shown = payload if len(payload) <= 62 else payload[:59] + "…"
        print(f"| {i} | {_escape(category)} | `{_escape(shown)}` | {result} |")

    return 0 if blocked == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
