"""생성된 단어 항목을 선별해 검수 순서를 매긴다. LLM 을 부르지 않는다.

승인은 하지 않는다 — 순서만 매긴다. 사람이 나쁜 것부터 보게 하는 게 목적이다.

실행:
    docker compose exec api python content/screen_words.py
    docker compose exec api python content/screen_words.py --show 15
    docker compose exec api python content/screen_words.py --code headword_absent --show 30
    docker compose exec api python content/screen_words.py --include-reviewed
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.content.screening import (  # noqa: E402
    Finding,
    risk_score,
    screen_all,
    worst_severity,
)
from app.db import crud  # noqa: E402
from app.db.database import db_session, init_db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("screen_words")

_ICON = {"high": "🔴", "medium": "🟡", "low": "⚪"}


def _bar(count: int, total: int, width: int = 28) -> str:
    filled = round(width * count / total) if total else 0
    return "█" * filled + "·" * (width - filled)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", type=int, default=8, help="의심 순 상위 N개를 자세히 출력")
    parser.add_argument("--code", default=None, help="이 코드가 붙은 항목만 본다")
    parser.add_argument(
        "--include-reviewed", action="store_true", help="이미 승인된 항목도 포함"
    )
    args = parser.parse_args()

    init_db()
    with db_session() as db:
        rows = crud.list_words(db, limit=100_000)
        if not args.include_reviewed:
            rows = [r for r in rows if not r.reviewed]
        # 세션 밖에서도 읽을 수 있게 필요한 값만 떼어 낸다.
        items = [
            type(
                "W",
                (),
                {
                    "word": r.word,
                    "level": r.level,
                    "meaning_ko": r.meaning_ko,
                    "example": r.example,
                    "usage_note": r.usage_note,
                    "confused_with": list(r.confused_with or []),
                },
            )()
            for r in rows
        ]

    if not items:
        print("검사할 항목이 없습니다.")
        return 0

    findings = screen_all(items)
    total = len(items)
    clean = [w for w, f in findings.items() if not f]

    print(f"\n검사 대상 {total}개 (미검수{'' if not args.include_reviewed else ' + 검수완료'})")
    print("=" * 66)

    by_severity = Counter(worst_severity(f) for f in findings.values() if f)
    print(f"  ✅ 지적사항 없음        {len(clean):>5}  {_bar(len(clean), total)}")
    for level in ("high", "medium", "low"):
        n = by_severity.get(level, 0)
        print(f"  {_ICON[level]} 최고 심각도 {level:<6} {n:>5}  {_bar(n, total)}")

    print("\n항목별 지적 내역")
    print("-" * 66)
    codes = Counter(f.code for flist in findings.values() for f in flist)
    severity_of: dict[str, str] = {
        f.code: f.severity for flist in findings.values() for f in flist
    }
    for code, n in codes.most_common():
        print(f"  {_ICON[severity_of[code]]} {code:<28} {n:>5}")

    ranked = sorted(
        ((w, f) for w, f in findings.items() if f),
        key=lambda pair: risk_score(pair[1]),
        reverse=True,
    )
    if args.code:
        ranked = [(w, f) for w, f in ranked if any(x.code == args.code for x in f)]
        print(f"\n'{args.code}' 로 걸린 항목: {len(ranked)}개")

    by_word = {i.word: i for i in items}
    if args.show and ranked:
        print(f"\n의심 순 상위 {min(args.show, len(ranked))}개")
        print("=" * 66)
        for word, flist in ranked[: args.show]:
            item = by_word[word]
            print(f"\n▸ {word}  ({item.level})  위험도 {risk_score(flist)}")
            for f in flist:
                print(f"    {_ICON[f.severity]} {f.message}  [{f.code}]")
            print(f"    뜻   {item.meaning_ko}")
            print(f"    예문 {item.example}")
            print(f"    설명 {item.usage_note[:120]}")

    flagged = len(ranked) if not args.code else len(ranked)
    print(
        f"\n{'=' * 66}\n"
        f"검수 큐: 지적 {flagged}개 먼저, 나머지 {total - len([1 for f in findings.values() if f])}개는 빠르게.\n"
        f"content/review_app.py 에서 '의심 순' 정렬로 같은 순서를 볼 수 있습니다."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
