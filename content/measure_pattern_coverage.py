"""저장된 설명(`usage_note`)이 단어의 **형태**를 짚고 있는지 센다. LLM 을 부르지 않는다.

왜 재는가
---------
"왕초보는 뜻이 아니라 형태에서 틀린다"는 건 주장이고, 주장은 재면 된다.
NGSL 2,801개를 처음 생성했을 때 형태를 짚은 설명은 **10개(0.4%)**였다.
`pattern` 필드를 별도로 둔 근거가 이 숫자다.

무엇을 세는가
-------------
설명에 형태 표지(전치사·불가산·`+ -ing`·목적어 …)가 들어 있는지 본다. 대리 지표라
정확하지 않다 — 표지 없이 형태를 설명한 문장은 놓치고, 지나가는 말로 '목적어'를
언급한 문장은 잡는다. 그래도 0.4% 와 80% 는 구분한다. 그 정도면 결정을 내리기에 충분하다.

실행:
    docker compose exec api python content/measure_pattern_coverage.py
    docker compose exec api python content/measure_pattern_coverage.py --show 20
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import crud  # noqa: E402
from app.db.database import db_session, init_db  # noqa: E402

# 한국어로 형태를 설명할 때 실제로 쓰는 말들. 뜻풀이에는 나올 이유가 없는 것들이다.
FORM_MARKERS = (
    r"\+\s*-?ing", r"to\s*부정사", r"전치사", r"불가산", r"가산", r"복수형",
    r"관사", r"목적어", r"자동사", r"타동사", r"동명사", r"원형", r"어순",
)
_FORM = re.compile("|".join(FORM_MARKERS))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", type=int, default=8, help="형태를 짚은 설명 N개를 보여준다")
    args = parser.parse_args()

    init_db()
    with db_session() as db:
        rows = [
            (r.word, r.usage_note or "", (r.pattern or "").strip())
            for r in crud.list_words(db, limit=100_000)
        ]

    if not rows:
        print("저장된 단어가 없습니다. 먼저 배치를 돌리세요.")
        return 0

    total = len(rows)
    with_form = [(w, note) for w, note, _ in rows if _FORM.search(note)]
    patterned = [(w, note) for w, note, pattern in rows if pattern]
    plain = [(w, note) for w, note, pattern in rows if not pattern]
    with_pattern = len(patterned)

    print(f"\n단어 {total}개")
    print("=" * 66)
    print(f"  설명이 형태를 짚음   {len(with_form):>5}  ({len(with_form) / total * 100:.1f}%)")
    print(f"  문형 칸이 채워짐     {with_pattern:>5}  ({with_pattern / total * 100:.1f}%)")

    # 문형 칸을 만든 것이 설명까지 바꿨는가. 같은 모델·같은 프롬프트에서
    # 칸 하나가 늘었을 때의 차이라 비교가 성립한다.
    if patterned and plain:
        a = sum(1 for _, note in patterned if _FORM.search(note)) / len(patterned) * 100
        b = sum(1 for _, note in plain if _FORM.search(note)) / len(plain) * 100
        print("\n  문형 칸 유무로 나눠 본 '설명이 형태를 짚은 비율'")
        print(f"    문형 있음 ({len(patterned):>4}개)   {a:5.1f}%")
        print(f"    문형 없음 ({len(plain):>4}개)   {b:5.1f}%")

    if with_form and args.show:
        print(f"\n형태를 짚은 설명 {min(args.show, len(with_form))}개")
        print("-" * 66)
        for word, note in with_form[: args.show]:
            print(f"  ▸ {word}: {note[:80]}")

    missing = total - with_pattern
    if missing:
        print(
            f"\n문형이 빈 항목 {missing}개 — "
            "content/batch_generate.py --missing-pattern 으로 채웁니다."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
