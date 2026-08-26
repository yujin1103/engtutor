"""NGSL(또는 임의 단어 목록) -> Ollama 배치 생성 -> words 테이블(reviewed=false).

실시간성이 필요 없는 사전 생성 경로다. 결과는 반드시 사람 검수를 거쳐야
리포트에 노출된다(content/review_app.py).

실행:
    docker compose exec api python content/batch_generate.py
    docker compose exec api python content/batch_generate.py --wordlist content/data/ngsl.csv
    docker compose exec api python content/batch_generate.py --limit 20 --concurrency 4
    docker compose exec api python content/batch_generate.py --dry-run --limit 3
    docker compose exec api python content/batch_generate.py --missing-pattern
    docker compose exec api python content/batch_generate.py --flagged
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# 컨테이너에서 `python content/batch_generate.py` 로 직접 실행할 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.content.generator import (  # noqa: E402
    WordGenerator,
    declares_no_rank,
    load_topics,
    load_wordlist,
)
from app.db import crud  # noqa: E402
from app.db.database import db_session, init_db  # noqa: E402
from app.llm.factory import get_client  # noqa: E402

DEFAULT_WORDLIST = Path(__file__).parent / "data" / "starter_words.txt"

# 이보다 짧은 목록의 순서는 빈도로 보지 않는다. NGSL 은 2,801개, 시작용 목록도 60개다.
MIN_RANKED_WORDS = 50

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("batch_generate")


def _normalize_existing() -> int:
    """정규화 규칙이 늘어났을 때 기존 행에 소급 적용한다. LLM 을 부르지 않는다."""
    from app.tutor.korean import normalize

    init_db()
    changed = 0
    with db_session() as db:
        for row in crud.list_words(db, limit=100_000):
            meaning, usage = normalize(row.meaning_ko), normalize(row.usage_note)
            if (meaning, usage) != (row.meaning_ko, row.usage_note):
                crud.save_word_edits(db, row.id, meaning_ko=meaning, usage_note=usage)
                changed += 1
                logger.info("정규화: %s", row.word)
    logger.info("완료: %d건 수정", changed)
    return 0


def _relevel(dry_run: bool) -> int:
    """LLM 이 매긴 레벨을 바깥 등급표와 대조해 **내리는 방향으로만** 고친다.

    왜 내리기만 하는가
    ------------------
    2,890개를 CEFR-J 등급표(6,867 표제어)와 맞춰 보니 일치가 46% 였고, 어긋난 방향이
    한쪽으로 쏠려 있었다 — 등급표가 A1 이라 보는 단어 166개, A2 라 보는 단어 554개를
    우리는 B1 이라고 붙여 놨다. 프롬프트에 "레벨을 부풀리지 말라"고 적어 두었는데도
    2,801개 중 1,899개가 B1 이었다. **B1 이 미분류 통이 된 것이다.**

    반대 방향(등급표가 더 어렵게 보는 것)은 건드리지 않는다. `passport` 를 등급표는
    B1 으로 보지만 이 앱은 공항 시나리오를 A1 으로 가르친다 — 장면에 매인 단어의
    난이도는 그 장면이 정한다. 실측된 결함은 부풀림 한 방향뿐이므로 그것만 고친다.

    승인된 항목은 건드리지 않는다. 사람이 정한 것을 표가 덮으면 안 된다.
    """
    from app.content import lexicon

    init_db()
    changed: list[tuple[str, str, str]] = []
    with db_session() as db:
        for row in crud.list_words(db, limit=100_000):
            if row.reviewed:
                continue
            theirs = lexicon.reference_level(row.word)
            if theirs is None or theirs not in ("A1", "A2", "B1"):
                continue  # 표에 없거나, 표가 더 어렵게 보는 구간(B2+)이다
            gap = lexicon.level_distance(row.level, theirs)
            if gap is None or gap <= 0:
                continue  # 같거나, 우리가 이미 더 쉽게 본 경우
            changed.append((row.word, row.level, theirs))
            if not dry_run:
                crud.save_word_edits(db, row.id, level=theirs)

    logger.info("%s: %d개", "내릴 대상" if dry_run else "레벨 조정", len(changed))
    for word, before, after in changed[:15]:
        logger.info("  %-16s %s -> %s", word, before, after)
    if len(changed) > 15:
        logger.info("  … %d개 더", len(changed) - 15)
    if not dry_run:
        with db_session() as db:
            from sqlalchemy import func, select

            from app.db.models import WordRow

            spread = db.execute(
                select(WordRow.level, func.count()).group_by(WordRow.level).order_by(WordRow.level)
            ).all()
        logger.info("레벨 분포: %s", " · ".join(f"{lv} {n}" for lv, n in spread))
    return 0


def _flagged_words(db, *, code: str | None) -> list[str]:
    """선별기가 지적한 미검수 항목의 표제어. 빈도 순으로 돌려준다.

    선별은 사후 진단이고, 같은 규칙이 이미 `WordEntry` 검증에 들어가 있다.
    그래서 지적된 항목을 **지금 프롬프트로 다시 생성하면** 대부분 스스로 고쳐진다 —
    남는 것만 사람이 보면 된다. 그게 검수 큐를 짧게 유지하는 방법이다.
    """
    from app.content.screening import screen_all

    rows = [r for r in crud.list_words(db, limit=100_000) if not r.reviewed]
    findings = screen_all(rows)
    picked = [
        r for r in rows
        if findings.get(r.word)
        and (code is None or any(f.code == code for f in findings[r.word]))
    ]
    picked.sort(key=lambda r: (r.rank is None, r.rank or 0, r.word))
    return [r.word for r in picked]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wordlist", type=Path, default=DEFAULT_WORDLIST)
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N개만 처리")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=20, help="이 단위로 커밋한다")
    parser.add_argument("--redo", action="store_true", help="이미 생성된 단어도 다시 생성")
    parser.add_argument("--dry-run", action="store_true", help="DB 에 쓰지 않고 결과만 출력")
    parser.add_argument(
        "--normalize-existing",
        action="store_true",
        help="LLM 호출 없이 저장된 항목의 한국어 표기만 다시 정규화한다",
    )
    parser.add_argument(
        "--missing-pattern",
        action="store_true",
        help="문형(pattern)이 비어 있는 미검수 항목만 다시 생성한다. 목록 파일 대신 DB 를 본다",
    )
    parser.add_argument(
        "--flagged",
        action="store_true",
        help="선별기가 지적한 미검수 항목만 다시 생성한다. 목록 파일 대신 DB 를 본다",
    )
    parser.add_argument(
        "--code", default=None, help="--flagged 와 함께: 이 지적 코드가 붙은 항목만"
    )
    parser.add_argument(
        "--rank-only",
        action="store_true",
        help="LLM 호출 없이 목록 순서를 빈도 순위로만 기록한다",
    )
    parser.add_argument(
        "--topics-only",
        action="store_true",
        help="LLM 호출 없이 목록의 `# topic:` 만 이미 저장된 항목에 붙인다",
    )
    parser.add_argument(
        "--relevel",
        action="store_true",
        help="LLM 호출 없이 바깥 등급표와 대조해 부풀려진 레벨을 내린다",
    )
    args = parser.parse_args()

    if args.normalize_existing:
        return _normalize_existing()

    if args.relevel:
        return _relevel(args.dry_run)

    # 표제어 -> 장면 묶음. DB 를 보고 도는 경로(--missing-pattern/--flagged)에서는
    # 이미 저장된 묶음을 그대로 두면 되므로 비워 둔다.
    topics: dict[str, str] = {}

    if args.missing_pattern or args.flagged:
        # 목록 파일이 아니라 DB 가 대상이다. 2,801개를 통째로 다시 돌릴 이유가 없다 —
        # 빠진 것·지적된 것만 고친다. 승인된 항목은 어느 쪽이든 빠진다.
        init_db()
        with db_session() as db:
            if args.flagged:
                words = _flagged_words(db, code=args.code)
                what = f"지적된 항목{f' ({args.code})' if args.code else ''}"
            else:
                words = crud.words_missing_pattern(db)
                what = "문형 없는 항목"
        if args.limit:
            words = words[: args.limit]
        if not words:
            logger.info("%s이 없습니다.", what)
            return 0
        # 전부 이미 DB 에 있는 단어라, 중복 건너뛰기를 끄지 않으면 하나도 안 돈다.
        args.redo = True
        logger.info("%s %d개를 빈도 순으로 다시 생성합니다.", what, len(words))
    else:
        if not args.wordlist.exists():
            logger.error("단어 목록이 없습니다: %s", args.wordlist)
            return 1

        init_db()
        words = load_wordlist(args.wordlist, limit=args.limit)
        topics = load_topics(args.wordlist)
        if topics:
            names = sorted(set(topics.values()))
            logger.info("장면 묶음 %d개: %s", len(names), ", ".join(names))
        if args.topics_only:
            with db_session() as db:
                changed = crud.assign_topics(db, topics)
            logger.info("장면 묶음 기록: %d개", changed)
            return 0

        # 목록에 등장하는 순서가 곧 빈도 순서다(NGSL). 검수 우선순위로 쓰려면
        # 생성 성공 여부와 무관하게 매번 기록해 둔다.
        #
        # 단, **짧은 목록의 순서는 빈도가 아니다.** 실패한 단어 5개를 다시 돌리려고
        # 임시 파일로 부르면 그 파일의 2번째 단어가 rank 2 가 돼 `and` 와 같은 자리에
        # 앉는다. 실제로 그렇게 seventeen 이 2위가 됐다. 목록이 짧으면 순위를 건드리지
        # 않는다 — 빈도 목록은 원래 길다.
        if not args.dry_run:
            full = load_wordlist(args.wordlist)
            if declares_no_rank(args.wordlist):
                logger.info("목록이 `# rank: none` 을 선언해 빈도 순위는 건드리지 않습니다.")
            elif len(full) < MIN_RANKED_WORDS:
                logger.info(
                    "목록이 %d개뿐이라 빈도 순위는 건드리지 않습니다 "
                    "(순위는 %d개 이상인 목록에서만 기록합니다).",
                    len(full),
                    MIN_RANKED_WORDS,
                )
            else:
                with db_session() as db:
                    changed = crud.assign_ranks(db, full)
                if changed:
                    logger.info("빈도 순위 기록: %d개", changed)
        if args.rank_only:
            return 0

    if not args.redo:
        with db_session() as db:
            done = crud.existing_words(db)
        skipped = [w for w in words if w in done]
        words = [w for w in words if w not in done]
        if skipped:
            logger.info("이미 생성됨 %d개 건너뜀 (--redo 로 다시 생성)", len(skipped))

    if not words:
        logger.info("생성할 단어가 없습니다.")
        return 0

    client = get_client()
    logger.info("백엔드: %s · 단어 %d개 · 동시 %d", client.describe(), len(words), args.concurrency)
    generator = WordGenerator(client)

    started = time.perf_counter()
    ok = failed = 0

    for offset in range(0, len(words), args.batch_size):
        chunk = words[offset : offset + args.batch_size]
        results = generator.generate_many(chunk, concurrency=args.concurrency, topics=topics)

        if args.dry_run:
            for r in results:
                if r.ok:
                    e = r.entry
                    print(f"\n[{e.level}] {e.word} — {e.meaning_ko}")
                    print(f"  문형: {e.pattern}")
                    print(f"  예문: {e.example}")
                    print(f"  노트: {e.usage_note}")
                    if e.confused_with:
                        print(f"  혼동: {', '.join(e.confused_with)}")
                else:
                    print(f"\n[실패] {r.word}: {r.error}")
        else:
            with db_session() as db:
                for r in results:
                    if r.ok:
                        crud.upsert_word(db, r.entry, topic=topics.get(r.word))

        ok += sum(1 for r in results if r.ok)
        failed += sum(1 for r in results if not r.ok)
        for r in results:
            if not r.ok:
                logger.warning("실패: %s — %s", r.word, r.error)

        elapsed = time.perf_counter() - started
        done_n = ok + failed
        rate = done_n / elapsed if elapsed else 0
        remain = (len(words) - done_n) / rate if rate else 0
        logger.info(
            "%d/%d (성공 %d · 실패 %d) · %.1f단어/초 · 남은 시간 약 %.0f초",
            done_n, len(words), ok, failed, rate, remain,
        )

    logger.info("완료: 성공 %d · 실패 %d · %.1f초", ok, failed, time.perf_counter() - started)
    if not args.dry_run:
        with db_session() as db:
            logger.info(
                "DB 현황: 전체 %d · 미검수 %d — content/review_app.py 에서 검수하세요.",
                crud.count_words(db), crud.count_words(db, reviewed=False),
            )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
