"""사람이 읽고 고친 교정을 DB 에 넣는 길(content/apply_fixes.py).

해석 교정(--glosses)에만 있는 두 가지를 고정한다.

1. **승인된 항목도 고친다.** 배치(백필)는 reviewed 를 건드리지 않는 것이 맞다 —
   사람의 검수를 기계가 덮으면 안 되니까. 그런데 이 파일이 바로 사람이 다시 본
   결과다. 여기서까지 승인을 이유로 거르면, 승인하며 놓친 오역(`pork` → '소고기
   버거')을 영영 못 고친다.
2. **배치 검사에 걸리는 교정본은 경고로 올린다.** --missing-example-ko 가 도는
   자리에서 _clear_stale_glosses 는 검사에 걸리는 미승인 해석을 **비우고 다시
   채운다.** 검사에 걸리는 것을 말없이 넣으면 다음 배치가 사람의 판단을 조용히
   지운다. 조용히 지워지느니 종료 코드로 시끄러운 편이 낫다.

그리고 실제 gloss_fixes.yaml 자체도 시험한다. 이 파일은 DB 를 고치는 데이터라
오타 하나가 33건 중 하나를 말없이 건너뛰게 만든다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content"))

import apply_fixes  # noqa: E402

from app.content.schemas import WordEntry, clean_gloss  # noqa: E402
from app.db import crud  # noqa: E402

from .conftest import temporary_database  # noqa: E402

GLOSS_FIXES = Path(__file__).resolve().parent.parent / "content" / "data" / "gloss_fixes.yaml"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    with temporary_database(tmp_path / "fixes.db", monkeypatch) as database:
        yield database


def _seed(database, **over):
    base = {
        "word": "pork",
        "level": "A1",
        "meaning_ko": "돼지고기",
        "pattern": "불가산명사",
        "example": "I want a pork burger.",
        "usage_note": "돼지고기는 pork 예요. 소고기는 beef 라고 해요.",
        "confused_with": ["beef"],
    }
    gloss = over.pop("example_ko", "소고기 버거 하나 주세요.")
    reviewed = over.pop("reviewed", False)
    base.update(over)
    with database.db_session() as db:
        row = crud.upsert_word(db, WordEntry(**base), topic="fastfood")
        row.example_ko = gloss
        row.reviewed = reviewed
    return base["word"]


def _run(database, fixes, *, dry_run=False):
    with database.db_session() as db:
        return apply_fixes.apply_gloss_fixes(db, fixes, dry_run=dry_run)



# ------------------------------------------------------------------ 고치는 칸
def test_only_the_gloss_column_changes(db):
    """해석 한 칸만 쓴다. 예문까지 새로 쓰면 학습자가 풀던 빈칸 문장이 바뀐다."""
    _seed(db)
    applied, _, _ = _run(db, [{"word": "pork", "example_ko": "돼지고기 버거 하나 주세요."}])

    assert applied == 1
    with db.db_session() as session:
        row = apply_fixes._find(session, "pork")
        assert row.example_ko == "돼지고기 버거 하나 주세요."
        assert row.example == "I want a pork burger."
        assert row.meaning_ko == "돼지고기"
        assert row.usage_note.startswith("돼지고기는 pork")


def test_an_approved_row_is_fixed_too(db):
    """승인은 '사람이 봤다'는 표시인데, 이 교정이 바로 사람이 다시 본 결과다."""
    _seed(db, reviewed=True)
    applied, skipped, _ = _run(db, [{"word": "pork", "example_ko": "돼지고기 버거 하나 주세요."}])

    assert (applied, skipped) == (1, 0)
    with db.db_session() as session:
        row = apply_fixes._find(session, "pork")
        assert row.example_ko == "돼지고기 버거 하나 주세요."
        assert row.reviewed is True  # 승인 표시는 이 스크립트가 건드리지 않는다


def test_a_dry_run_writes_nothing(db):
    _seed(db)
    applied, _, _ = _run(db, [{"word": "pork", "example_ko": "돼지고기 버거 하나 주세요."}], dry_run=True)

    assert applied == 1  # 무엇을 고칠지는 세어 보여주되
    with db.db_session() as session:
        assert apply_fixes._find(session, "pork").example_ko == "소고기 버거 하나 주세요."


def test_an_unchanged_gloss_is_not_counted_as_a_fix(db):
    _seed(db, example_ko="돼지고기 버거 하나 주세요.")
    applied, skipped, _ = _run(db, [{"word": "pork", "example_ko": "돼지고기 버거 하나 주세요."}])
    assert (applied, skipped) == (0, 1)


# ------------------------------------------------------------------ 건너뛰는 것
def test_a_word_that_is_not_in_the_db_is_skipped(db):
    """DB 는 다시 만들어지고 목록은 바뀐다. 없는 낱말에 걸려 33건이 멈추면 안 된다."""
    _seed(db)
    applied, skipped, _ = _run(db, [{"word": "nonexistent", "example_ko": "없는 낱말이에요."}])
    assert (applied, skipped) == (0, 1)


def test_a_row_without_an_example_is_skipped(db):
    """예문이 없으면 해석이 무엇의 해석인지 말할 수 없다."""
    _seed(db)
    with db.db_session() as session:
        apply_fixes._find(session, "pork").example = ""
    applied, skipped, _ = _run(db, [{"word": "pork", "example_ko": "돼지고기 버거 하나 주세요."}])
    assert (applied, skipped) == (0, 1)


# ------------------------------------------------------------------ 시끄럽게 실패
def test_a_gloss_that_breaks_the_schema_is_not_written(db):
    """저장할 수 없는 값(영어가 섞인 해석)은 넣지 않는다."""
    _seed(db)
    applied, skipped, flagged = _run(db, [{"word": "pork", "example_ko": "I want a pork burger."}])

    assert (applied, skipped) == (0, 1)
    assert flagged == 1
    with db.db_session() as session:
        assert apply_fixes._find(session, "pork").example_ko == "소고기 버거 하나 주세요."


def test_a_gloss_the_batch_would_wipe_is_flagged(db):
    """낱말 뜻을 그대로 베낀 해석은 다음 백필이 비운다. 조용히 넣으면 안 된다."""
    _seed(db)
    applied, _, flagged = _run(db, [{"word": "pork", "example_ko": "돼지고기"}])

    # 사람의 판단이니 넣기는 넣는다. 다만 세어서 종료 코드로 올린다.
    assert applied == 1
    assert flagged >= 1


# ------------------------------------------------------------------ 데이터 파일
def test_the_batch_does_not_wipe_a_gloss_a_human_wrote():
    """이 빗장이 없으면 배치가 사람의 판단을 조용히 지운다.

    실제로 그럴 뻔했다. 손으로 쓴 해석 17개가 `reject_unrelated_gloss` 에 걸렸는데
    전부 **맞는 해석인데 저장된 뜻과 글자가 안 겹치는** 경우였다 — `journalist` 의
    해석 '언론인' 과 저장된 뜻 '기자', `classroom` 의 '교실' 과 '수업을 듣는 공간'.
    그 검사는 뜻을 다른 말로 옮길 수 있는 낱말에서 헛짚는데, 걸린 해석은 배치가
    비우고 LLM 으로 다시 채운다.
    """
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "content"))
    import batch_generate

    kept = batch_generate.human_glossed()
    assert "pork" in kept  # 첫 검수에서 '소고기 버거' 를 고친 낱말
    assert len(kept) > 100

    # 파일이 없어도 배치가 멈추면 안 된다.
    assert batch_generate.human_glossed(_Path("없는파일.yaml")) == set()


def test_every_recorded_gloss_fix_is_shaped_right():
    """33건 중 하나가 오타로 말없이 건너뛰어지는 것을 막는다."""
    fixes = yaml.safe_load(GLOSS_FIXES.read_text(encoding="utf-8"))

    assert fixes, "gloss_fixes.yaml 이 비어 있습니다"
    words = [f["word"] for f in fixes]
    assert len(words) == len(set(words)), "같은 낱말이 두 번 나옵니다"
    for fix in fixes:
        assert fix["word"] == fix["word"].strip().lower()
        assert fix.get("reason"), f"{fix['word']}: 왜 고쳤는지가 없습니다"
        # 교정본도 생성물과 같은 스키마를 통과해야 한다.
        assert clean_gloss(fix["example_ko"])
