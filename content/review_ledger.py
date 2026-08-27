"""사람이 읽은 낱말의 대장. LLM 을 부르지 않는다.

왜 이 파일이 있는가
-------------------
지금까지 검수는 **고친 것만** 흔적을 남겼다 — manual_fixes.yaml 과 gloss_fixes.yaml 에
이름이 오르는 낱말은 고쳐진 낱말뿐이다. "읽었는데 고칠 데가 없었다" 는 판정은 어디에도
남지 않았고, 그래서 빈도 상위 150개를 읽다 중간에 끊었을 때 **어디까지 읽었는지가 통째로
사라졌다**. 다시 처음부터 읽는 수밖에 없었다.

읽는 데 드는 것은 사람의 시간이므로, 그 시간이 만든 결과 중 절반(무변경 판정)을
버리면 안 된다. 이 대장은 그 절반을 남긴다.

대장은 코드가 아니라 content/data/review_log.yaml 에 있다. 형식:

    passes:
      - id: scene-packs
        date: 2026-08-26
        what: "장면 팩 16개 전부"
        words: [coffee, latte, ...]

한 낱말이 여러 pass 에 나올 수 있다 — 다른 이유로 다시 읽은 것이고, 그건 사실이다.

실행:
    docker compose exec api python content/review_ledger.py --status
    docker compose exec api python content/review_ledger.py --remaining --limit 150
    docker compose exec api python content/review_ledger.py --show 40
    docker compose exec api python content/review_ledger.py --record top150-a --what "빈도 1~40위" --words-from /tmp/w.txt
    docker compose exec api python content/review_ledger.py --seed
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.database import db_session, init_db  # noqa: E402
from app.db.models import WordRow  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("review_ledger")

DATA = Path(__file__).parent / "data"
LEDGER = DATA / "review_log.yaml"
FIXES = DATA / "manual_fixes.yaml"
GLOSS_FIXES = DATA / "gloss_fixes.yaml"

HEADER = """\
# 사람이 읽은 낱말의 대장 — 고친 것과 "읽었는데 고칠 데 없었다" 를 함께 남긴다.
#
# 교정 YAML 두 개는 **고친 것만** 담는다. 그것만으로는 검수를 재개할 수 없다.
# 어디까지 읽었는지 모르면 처음부터 다시 읽어야 하고, 실제로 한 번 그렇게 됐다.
#
# 한 낱말이 여러 pass 에 나오는 것은 정상이다 — 다른 이유로 다시 읽었다는 뜻이다.
#
# 손으로 고쳐도 되지만, 보통은 스크립트가 붙인다:
#   docker compose exec api python content/review_ledger.py --record <id> --what "..." --words-from <파일>
"""


def headword(value: object) -> str | None:
    """YAML 에서 읽은 표제어를 글자로 만든다. 못 만들면 None 이다.

    `- word: on` 은 따옴표가 없으면 PyYAML 이 불리언 True 로 읽는다(on/off/yes/no).
    그대로 str() 하면 'true' 가 되어 **있지도 않은 낱말이 읽은 것으로 기록되고,
    정작 `on` 은 영영 안 읽은 채로 남는다.** True 가 on 이었는지 yes 였는지는
    되살릴 수 없으므로 되살리려 하지 말고 시끄럽게 멈춘다.
    """
    if isinstance(value, bool):
        logger.error(
            "표제어가 불리언 %r 로 읽혔습니다. YAML 에서 따옴표로 묶으세요 — "
            "on/off/yes/no/true/false 가 이렇게 됩니다.",
            value,
        )
        return None
    text = str(value).strip().lower()
    return text or None


def _load_words_of(path: Path) -> list[str]:
    """교정 YAML 에 이름이 오른 표제어. 여기 있으면 사람이 읽은 것은 확실하다."""
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    found = (headword(e["word"]) for e in doc if isinstance(e, dict) and "word" in e)
    return [w for w in found if w]


def load_ledger() -> dict:
    if not LEDGER.exists():
        return {"passes": []}
    return yaml.safe_load(LEDGER.read_text(encoding="utf-8")) or {"passes": []}


def save_ledger(doc: dict) -> None:
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100)
    LEDGER.write_text(HEADER + "\n" + body, encoding="utf-8")


def read_words(doc: dict) -> set[str]:
    """대장에 이름이 오른 낱말 전부. 손으로 고친 대장도 같은 함정을 지난다."""
    seen: set[str] = set()
    for p in doc.get("passes") or []:
        for raw in p.get("words") or []:
            word = headword(raw)
            if word:
                seen.add(word)
    return seen


def _rows(db, track: str | None) -> list[WordRow]:
    stmt = select(WordRow)
    if track:
        stmt = stmt.where(WordRow.track == track)
    rows = list(db.execute(stmt).scalars())
    # rank 가 없는 행은 뒤로 — 빈도를 모르는 낱말이다.
    rows.sort(key=lambda r: (r.rank is None, r.rank or 0, r.word))
    return rows


def cmd_seed() -> int:
    """되살릴 수 있는 과거 검수를 대장에 넣는다. 한 번만 돌리면 된다.

    되살릴 수 있는 것은 둘뿐이다 — 팩 단위로 통째로 읽은 장면 팩과, 교정 YAML 에
    이름이 오른 낱말. 검사기가 걸어 준 것만 골라 읽은 pass 의 '무변경' 판정은
    이미 사라졌다. 이 대장은 그것이 다시 사라지지 않게 하려고 만든다.
    """
    doc = load_ledger()
    have = {p["id"] for p in doc.get("passes") or []}
    added = 0

    with db_session() as db:
        packs = sorted(
            {r.word for r in db.execute(select(WordRow)).scalars() if (r.topic or "").strip()}
        )

    seeds = [
        (
            "scene-packs",
            "2026-08-26",
            "장면 팩 전부 — 팩 단위로 처음부터 끝까지 읽었다",
            packs,
        ),
        (
            "manual-fixes",
            "2026-08-27",
            "manual_fixes.yaml 에 이름이 오른 낱말 — 읽고 고친 것이다",
            sorted(set(_load_words_of(FIXES))),
        ),
        (
            "gloss-fixes",
            "2026-08-27",
            "gloss_fixes.yaml 에 이름이 오른 낱말 — 해석을 읽고 고친 것이다",
            sorted(set(_load_words_of(GLOSS_FIXES))),
        ),
    ]
    with db_session() as db:
        known = {r.word for r in db.execute(select(WordRow)).scalars()}

    for pid, date, what, words in seeds:
        if pid in have:
            logger.info("건너뜀: %s — 이미 대장에 있습니다", pid)
            continue
        # DB 에 없는 표제어는 교정 YAML 의 오타이거나 위의 불리언 함정이다.
        # 대장이 "읽었다" 고 주장하는 낱말은 실재해야 한다.
        stray = [w for w in words if w not in known]
        if stray:
            logger.warning(
                "%s: DB 에 없는 표제어 %d개 — %s", pid, len(stray), ", ".join(stray[:10])
            )
        doc.setdefault("passes", []).append(
            {"id": pid, "date": date, "what": what, "words": words}
        )
        logger.info("넣음: %s — %d개", pid, len(words))
        added += 1

    if added:
        save_ledger(doc)
        print(f"\n{LEDGER} 에 pass {added}개를 넣었습니다.")
    return 0


def cmd_record(args) -> int:
    words = list(args.words or [])
    if args.words_from:
        text = Path(args.words_from).read_text(encoding="utf-8")
        words += [w.strip().lower() for w in text.replace(",", "\n").split() if w.strip()]
    words = sorted({w.strip().lower() for w in words if w.strip()})
    if not words:
        logger.error("넣을 낱말이 없습니다.")
        return 1

    with db_session() as db:
        known = {r.word for r in db.execute(select(WordRow)).scalars()}
    unknown = [w for w in words if w not in known]
    if unknown:
        logger.warning("DB 에 없는 표제어 %d개: %s", len(unknown), ", ".join(unknown[:10]))

    doc = load_ledger()
    for p in doc.get("passes") or []:
        if p["id"] == args.record:
            before = len(p.get("words") or [])
            p["words"] = sorted(set((p.get("words") or []) + words))
            save_ledger(doc)
            grew = len(p["words"]) - before
            print(f"[{args.record}] 에 {grew}개를 더했습니다 (총 {len(p['words'])}개).")
            return 0

    doc.setdefault("passes", []).append(
        {
            "id": args.record,
            "date": args.date or _dt.date.today().isoformat(),
            "what": args.what or "",
            "words": words,
        }
    )
    save_ledger(doc)
    print(f"pass [{args.record}] 를 만들고 {len(words)}개를 넣었습니다.")
    return 0


def cmd_status(args) -> int:
    doc = load_ledger()
    seen = read_words(doc)
    with db_session() as db:
        rows = _rows(db, None)

    print("=" * 66)
    print("검수 대장")
    print("=" * 66)
    for p in doc.get("passes") or []:
        n = len(p.get("words") or [])
        print(f"  {p['date']}  {p['id']:<16} {n:>5}개  {p.get('what', '')}")
    if not doc.get("passes"):
        print("  (비어 있습니다 — --seed 를 먼저 돌리세요)")

    print()
    for track in ("general", "toeic"):
        sub = [r for r in rows if r.track == track]
        done = [r for r in sub if r.word in seen]
        pct = 100 * len(done) / len(sub) if sub else 0
        filled = round(28 * pct / 100)
        bar = "█" * filled + "·" * (28 - filled)
        print(
            f"  {track:<8} {bar} {len(done):>5}/{len(sub):<5} ({pct:4.1f}%)"
            f"  남은 {len(sub) - len(done)}개"
        )
    total_done = len([r for r in rows if r.word in seen])
    print(f"\n  전체 {total_done}/{len(rows)} — 남은 {len(rows) - total_done}개")
    return 0


def cmd_remaining(args) -> int:
    doc = load_ledger()
    seen = read_words(doc)
    with db_session() as db:
        rows = [r for r in _rows(db, args.track) if r.word not in seen]
        if args.show:
            for i, r in enumerate(rows[: args.show], 1):
                print(f"\n▸ {i}. {r.word}  ({r.level}, 빈도 {r.rank}, {r.track})")
                print(f"    뜻   {r.meaning_ko}")
                print(f"    문형 {r.pattern}")
                print(f"    예문 {r.example}")
                print(f"    해석 {r.example_ko}")
                print(f"    설명 {r.usage_note}")
                print(f"    혼동 {r.confused_with}")
        else:
            for r in rows[: args.limit]:
                print(r.word)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="구간별 검수 진도")
    parser.add_argument("--remaining", action="store_true", help="아직 안 읽은 낱말 목록")
    parser.add_argument("--show", type=int, default=0, help="안 읽은 낱말 N개를 항목 전체로 출력")
    parser.add_argument("--limit", type=int, default=150, help="--remaining 이 출력할 개수")
    parser.add_argument("--track", default=None, help="general | toeic")
    parser.add_argument("--seed", action="store_true", help="되살릴 수 있는 과거 검수를 대장에 넣는다")
    parser.add_argument("--record", default=None, help="이 id 의 pass 에 판정을 기록한다")
    parser.add_argument("--what", default=None, help="--record 와 함께: 무엇을 읽었는지")
    parser.add_argument("--date", default=None, help="--record 와 함께: 날짜 (기본 오늘)")
    parser.add_argument("--words", nargs="*", help="--record 와 함께: 표제어들")
    parser.add_argument("--words-from", default=None, help="--record 와 함께: 표제어가 든 파일")
    args = parser.parse_args()

    init_db()
    if args.seed:
        return cmd_seed()
    if args.record:
        return cmd_record(args)
    if args.remaining or args.show:
        return cmd_remaining(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
