"""NGSL(또는 임의 단어 목록) -> Ollama 배치 생성 -> words 테이블(reviewed=false).

실시간성이 필요 없는 사전 생성 경로다. 결과는 반드시 사람 검수를 거쳐야
리포트에 노출된다(content/review_app.py).

실행:
    docker compose exec api python content/batch_generate.py
    docker compose exec api python content/batch_generate.py --wordlist content/data/ngsl.csv
    docker compose exec api python content/batch_generate.py --limit 20 --concurrency 4
    docker compose exec api python content/batch_generate.py --dry-run --limit 3
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# 컨테이너에서 `python content/batch_generate.py` 로 직접 실행할 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.content.generator import WordGenerator, load_wordlist  # noqa: E402
from app.db import crud  # noqa: E402
from app.db.database import db_session, init_db  # noqa: E402
from app.llm.factory import get_client  # noqa: E402

DEFAULT_WORDLIST = Path(__file__).parent / "data" / "starter_words.txt"

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
        "--rank-only",
        action="store_true",
        help="LLM 호출 없이 목록 순서를 빈도 순위로만 기록한다",
    )
    args = parser.parse_args()

    if args.normalize_existing:
        return _normalize_existing()

    if not args.wordlist.exists():
        logger.error("단어 목록이 없습니다: %s", args.wordlist)
        return 1

    init_db()
    words = load_wordlist(args.wordlist, limit=args.limit)

    # 목록에 등장하는 순서가 곧 빈도 순서다(NGSL). 검수 우선순위로 쓰려면
    # 생성 성공 여부와 무관하게 매번 기록해 둔다.
    if not args.dry_run:
        with db_session() as db:
            changed = crud.assign_ranks(db, load_wordlist(args.wordlist))
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
        results = generator.generate_many(chunk, concurrency=args.concurrency)

        if args.dry_run:
            for r in results:
                if r.ok:
                    e = r.entry
                    print(f"\n[{e.level}] {e.word} — {e.meaning_ko}")
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
                        crud.upsert_word(db, r.entry)

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
