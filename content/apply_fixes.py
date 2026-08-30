"""사람이 확인해 손으로 고친 교정을 DB 에 적용한다. LLM 을 부르지 않는다.

왜 이 스크립트가 따로 있는가
----------------------------
프롬프트를 고쳐도 환각은 확률적으로 다시 나온다. 이미 생성된 유한한 집합에서
**확인된 거짓**을 지우는 일은 재생성이 아니라 교정으로 해야 한다. 재생성은
같은 자리에 다른 거짓을 넣을 수 있지만 교정은 그렇지 않다.

교정 내용은 코드가 아니라 content/data/ 아래 YAML 두 개에 있다.

- manual_fixes.yaml — **실재하지 않는 영어 낱말**을 지운다. 사전 조회로 판정을 좁혔고
  근거는 docs/hallucinations.md 에 있다. WordEntry 여섯 칸을 고친다.
- gloss_fixes.yaml — **예문 해석(example_ko)의 번역 오류**를 고친다(--glosses).
  example_ko 한 칸만 고친다.

둘을 나눠 둔 이유는 판정 방법이 다르기 때문이다. 낱말의 실재는 사전이 가려 주지만,
"이 한국어가 저 영어와 같은 뜻인가"에는 결정론적 정답기가 없다 — 사람이 영어와
한국어를 나란히 읽는 수밖에 없다. 실측으로 검사기 4종이 0건이라고 한 129개에서
사람이 읽으니 22개(17%)가 틀렸다.

적용한 뒤에도 reviewed 는 건드리지 않는다 — 승인은 사람이 검수 UI 에서 하는 일이고,
이 스크립트는 내용만 고친다.

실행:
    docker compose exec api python content/apply_fixes.py
    docker compose exec api python content/apply_fixes.py --dry-run
    docker compose exec api python content/apply_fixes.py --glosses
    docker compose exec api python content/apply_fixes.py --glosses --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.content.schemas import (  # noqa: E402
    WordEntry,
    clean_gloss,
    reject_unrelated_gloss,
    reject_word_meaning,
    reject_wrong_number,
)
from app.content.screening import screen  # noqa: E402
from app.db.database import db_session, init_db  # noqa: E402
from app.db.models import WordRow  # noqa: E402

logger = logging.getLogger("apply_fixes")
FIXES = Path(__file__).parent / "data" / "manual_fixes.yaml"
GLOSS_FIXES = Path(__file__).parent / "data" / "gloss_fixes.yaml"
# level 도 여기 있어야 한다. 없던 동안 apply 는 fix 의 level 을 받아 WordEntry 로
# 검증까지 하고는 row 에 쓰지 않고 버렸다 — `sake` 의 뜻을 술에서 for the sake of 로
# 옮기며 B1 을 준 것이 조용히 A2 로 남아서 드러났다.
FIELDS = ("level", "meaning_ko", "pattern", "example", "usage_note", "confused_with")


def _find(db, word: str) -> WordRow | None:
    """표제어로 행 하나를 찾는다. DB 의 표제어는 소문자로 정규화돼 있다."""
    return db.execute(
        select(WordRow).where(WordRow.word == word.strip().lower())
    ).scalar_one_or_none()


def apply_word_fixes(db, fixes: list[dict], *, dry_run: bool) -> tuple[int, int, int]:
    """WordEntry 여섯 칸을 고친다. 돌려주는 값은 (적용, 건너뜀, 경고)."""
    applied = skipped = flagged = 0
    for fix in fixes:
        word = fix["word"]
        row = _find(db, word)
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
            flagged += 1
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
        if not dry_run:
            for k in FIELDS:
                setattr(row, k, after[k])
        applied += 1
    return applied, skipped, flagged


def apply_gloss_fixes(db, fixes: list[dict], *, dry_run: bool) -> tuple[int, int, int]:
    """예문 해석(example_ko) 한 칸만 고친다. 돌려주는 값은 (적용, 건너뜀, 경고).

    승인된 항목도 고친다. 승인은 "사람이 봤다"는 표시인데 이 파일이 바로 사람이
    다시 본 결과다 — 여기서 거르면 앞서 승인하며 놓친 오역을 영영 못 고친다.

    배치의 자동 검사 4종을 여기서도 돌린다. 통과 여부가 곧 이 해석의 수명이기
    때문이다: --missing-example-ko 가 도는 자리에서 _clear_stale_glosses 는
    검사에 걸리는 미승인 해석을 비우고 LLM 으로 다시 채운다. 검사에 걸리는 것을
    말없이 넣어 두면 다음 배치가 사람의 판단을 조용히 지운다. 그래서 걸린 것은
    경고로 세고 종료 코드로 올린다 — 사람이 YAML 을 고치든 검사를 고치든 한다.
    """
    applied = skipped = flagged = 0
    for fix in fixes:
        word = fix["word"]
        row = _find(db, word)
        if row is None:
            logger.warning("건너뜀: %s — DB 에 없습니다", word)
            skipped += 1
            continue
        if not row.example:
            logger.warning("건너뜀: %s — 예문이 없어 해석을 붙일 자리가 없습니다", word)
            skipped += 1
            continue

        try:
            gloss = clean_gloss(fix["example_ko"])
        except ValueError as exc:
            # 저장할 수 없는 값이다(빈 칸·길이 초과·외국 문자). 넣지 않는다.
            logger.error("건너뜀: %s — 교정본이 해석 스키마에 어긋납니다: %s", word, exc)
            skipped += 1
            flagged += 1
            continue

        for check in (
            lambda: reject_word_meaning(gloss, example=row.example, meaning_ko=row.meaning_ko),
            lambda: reject_wrong_number(gloss, word=row.word, example=row.example),
            lambda: reject_unrelated_gloss(
                gloss,
                word=row.word,
                example=row.example,
                meaning_ko=row.meaning_ko,
                usage_note=row.usage_note,
            ),
        ):
            try:
                check()
            except ValueError as exc:
                flagged += 1
                logger.warning(
                    "%s — 교정본이 배치 검사에 걸립니다(다음 백필이 이 해석을 비웁니다): %s",
                    word, exc,
                )

        if row.example_ko == gloss:
            logger.info("변화 없음: %s", word)
            skipped += 1
            continue

        logger.info("%s — %r → %r  (%s)", word, row.example_ko, gloss,
                    fix.get("reason", "이유 없음"))
        if not dry_run:
            row.example_ko = gloss
        applied += 1
    return applied, skipped, flagged


def yaml_booleans(fixes: list[dict]) -> list[str]:
    """따옴표가 없어 불리언으로 읽힌 낱말을 찾는다. 자리와 함께 돌려준다.

    PyYAML 은 `on`·`off`·`yes`·`no`·`true`·`false` 를 따옴표 없이 쓰면 불리언으로
    읽는다. 영어 표제어에는 하필 그 여섯이 다 있다.

    두 번 물렸다. 처음에는 `- word: on` 이 True 가 되어 적용이 멈췄고, 다음에는
    `confused_with: [no, never]` 의 no 가 False 가 되어 pydantic 이 스무 줄짜리
    타입 오류를 뱉었다. 둘 다 원인이 화면에 안 나온다 — 오류 어디에도 'no' 라는
    글자가 없다. 그래서 YAML 을 고치라고 **낱말을 짚어** 말해 준다.
    """
    bad: list[str] = []
    for i, fix in enumerate(fixes):
        if not isinstance(fix, dict):
            continue
        if isinstance(fix.get("word"), bool):
            bad.append(f"{i + 1}번째 항목의 word: {fix['word']!r}")
        for j, c in enumerate(fix.get("confused_with") or []):
            if isinstance(c, bool):
                bad.append(f"{fix.get('word', '?')} 의 confused_with[{j}]: {c!r}")
    return bad


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--glosses",
        action="store_true",
        help="예문 해석(example_ko) 교정을 적용한다. 기본은 gloss_fixes.yaml",
    )
    parser.add_argument("--fixes", type=Path, default=None, help="교정 YAML 경로")
    parser.add_argument("--dry-run", action="store_true", help="DB 에 쓰지 않고 결과만 출력")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    path = args.fixes or (GLOSS_FIXES if args.glosses else FIXES)
    fixes = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    logger.info("교정 %d건을 읽었습니다: %s", len(fixes), path)

    booleans = yaml_booleans(fixes)
    if booleans:
        for where in booleans:
            logger.error("따옴표가 없어 불리언으로 읽혔습니다 — %s", where)
        logger.error(
            "YAML 에서 그 낱말을 따옴표로 묶으세요. on/off/yes/no/true/false 가 이렇게 됩니다."
        )
        return 1

    init_db()
    with db_session() as db:
        run = apply_gloss_fixes if args.glosses else apply_word_fixes
        applied, skipped, flagged = run(db, fixes, dry_run=args.dry_run)
        if args.dry_run:
            db.rollback()

    logger.info("%s: 적용 %d · 건너뜀 %d · 경고 %d",
                "예행" if args.dry_run else "완료", applied, skipped, flagged)
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
