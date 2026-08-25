"""사람이 확인해 손으로 고친 교정을 DB 에 적용한다. LLM 을 부르지 않는다.

왜 이 스크립트가 따로 있는가
----------------------------
프롬프트를 고쳐도 환각은 확률적으로 다시 나온다. 이미 생성된 유한한 집합에서
**확인된 거짓**을 지우는 일은 재생성이 아니라 교정으로 해야 한다. 재생성은
같은 자리에 다른 거짓을 넣을 수 있지만 교정은 그렇지 않다.

교정 내용은 코드가 아니라 content/data/manual_fixes.yaml 에 있다. 판정 근거는
docs/hallucinations.md 에 남긴다.

적용한 뒤에도 reviewed 는 건드리지 않는다 — 승인은 사람이 검수 UI 에서 하는 일이고,
이 스크립트는 내용만 고친다.

실행:
    docker compose exec api python content/apply_fixes.py
    docker compose exec api python content/apply_fixes.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.content.schemas import WordEntry  # noqa: E402
from app.content.screening import screen  # noqa: E402
from app.db import crud  # noqa: E402
from app.db.database import db_session, init_db  # noqa: E402

logger = logging.getLogger("apply_fixes")
FIXES = Path(__file__).parent / "data" / "manual_fixes.yaml"
FIELDS = ("meaning_ko", "pattern", "example", "usage_note", "confused_with")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixes", type=Path, default=FIXES)
    parser.add_argument("--dry-run", action="store_true", help="DB 에 쓰지 않고 결과만 출력")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    fixes = yaml.safe_load(args.fixes.read_text(encoding="utf-8"))
    logger.info("교정 %d건을 읽었습니다: %s", len(fixes), args.fixes)

    init_db()
    applied = skipped = 0
    with db_session() as db:
        for fix in fixes:
            word = fix["word"]
            row = crud.get_word(db, word) if hasattr(crud, "get_word") else None
            if row is None:
                from sqlalchemy import select

                from app.db.models import WordRow

                row = db.execute(select(WordRow).where(WordRow.word == word)).scalar_one_or_none()
            if row is None:
                logger.warning("건너뜀: %s — DB 에 없습니다", word)
                skipped += 1
                continue

            # 교정본도 생성물과 같은 스키마를 통과해야 한다. 손으로 쓴 글이라고
            # 검증을 건너뛰면, 고치면서 새 결함을 넣어도 아무도 모른다.
            entry = WordEntry(
                word=word,
                level=fix.get("level", row.level),
                meaning_ko=fix.get("meaning_ko", row.meaning_ko),
                pattern=fix.get("pattern", row.pattern or ""),
                example=fix.get("example", row.example),
                usage_note=fix.get("usage_note", row.usage_note),
                confused_with=fix.get("confused_with", row.confused_with or []),
            )
            findings = screen(entry)
            if findings:
                for f in findings:
                    logger.warning("교정본이 선별기에 걸립니다: %s — %s", word, f.message)

            before = {k: getattr(row, k) for k in FIELDS}
            after = {k: getattr(entry, k) for k in FIELDS}
            changed = [k for k in FIELDS if before[k] != after[k]]
            if not changed:
                logger.info("변화 없음: %s", word)
                skipped += 1
                continue

            logger.info("%s — %s  (지운 단어: %s)", word, ", ".join(changed),
                        ", ".join(fix.get("removed", [])) or "없음")
            if not args.dry_run:
                for k in FIELDS:
                    setattr(row, k, after[k])
            applied += 1

        if args.dry_run:
            db.rollback()

    logger.info("%s: 적용 %d · 건너뜀 %d", "예행" if args.dry_run else "완료", applied, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
