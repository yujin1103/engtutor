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


def test_unquoted_headword_is_named_not_buried_in_a_type_error():
    """따옴표 없는 `on`·`no` 를 **낱말을 짚어** 알린다.

    PyYAML 은 on/off/yes/no/true/false 를 따옴표 없이 쓰면 불리언으로 읽는다.
    영어 표제어에는 하필 그 여섯이 다 있다. 두 번 물렸다 — `- word: on` 이 True 가
    되어 적용이 멈췄고, `confused_with: [no, never]` 의 no 가 False 가 되어
    pydantic 이 스무 줄짜리 타입 오류를 뱉었다.

    둘 다 **오류 어디에도 'on' 이나 'no' 라는 글자가 없다.** 그래서 못 찾는다.
    """
    fixes = yaml.safe_load("- word: on\n- word: not\n  confused_with: [no, never]\n")
    found = apply_fixes.yaml_booleans(fixes)

    assert len(found) == 2
    assert "word: True" in found[0]
    assert "confused_with[0]" in found[1] and "not" in found[1]


def test_quoted_headwords_pass():
    """따옴표로 묶으면 아무 말도 하지 않는다."""
    fixes = yaml.safe_load('- word: "on"\n- word: not\n  confused_with: ["no", never]\n')
    assert apply_fixes.yaml_booleans(fixes) == []


def test_the_real_fix_files_have_no_unquoted_booleans():
    """실제 교정 YAML 두 개를 시험한다. 이게 걸리면 적용이 통째로 멈춘다."""
    for path in (apply_fixes.FIXES, apply_fixes.GLOSS_FIXES):
        fixes = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        assert apply_fixes.yaml_booleans(fixes) == [], f"{path.name} 에 따옴표 없는 낱말이 있습니다"


def test_no_word_appears_twice_in_the_fix_file():
    """한 낱말이 두 번 나오면 **앞 항목이 중간 상태로 검사받는다.**

    `apply_fixes` 는 항목마다 고친 값을 DB 값과 합쳐 선별기에 건다. 같은 낱말이
    두 번 있으면 앞 항목은 "새 설명 + 옛 문형" 이라는, 실제로는 존재하지 않는
    상태로 검사받아 경고를 낸다. 실제로 101개가 그렇게 쌓여 있었다.

    `gloss_fixes.yaml` 은 이미 같은 시험이 있다(`test_every_recorded_gloss_fix_is_shaped_right`).
    두 파일의 규칙이 서로 다를 이유가 없다.
    """
    fixes = yaml.safe_load(apply_fixes.FIXES.read_text(encoding="utf-8"))
    words = [str(f["word"]) for f in fixes]
    dupes = sorted({w for w in words if words.count(w) > 1})
    assert dupes == [], f"두 번 이상 나오는 낱말: {dupes[:10]}"


def test_merge_fixes_folds_a_repeated_word_and_keeps_both_reasons():
    """겹치는 항목을 접을 때 **뒤엣값이 이기고 근거는 둘 다 남는다.**

    뒤엣값이 이기는 이유: 그것이 마지막 판단이다. 근거를 이어 붙이는 이유:
    왜 고쳤는지가 이 파일의 값어치라, 앞의 근거를 덮으면 나중에 "이건 왜
    이렇게 됐지" 를 답할 수 없다.

    묶음으로 검수를 돌리면 앞 묶음에서 고친 낱말이 다음 묶음에 다시 올라온다 —
    한 번은 101개가 겹쳐 있었다.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content"))
    import merge_fixes  # noqa: E402

    merged, folded = merge_fixes._merge(
        [
            {"word": "send", "reason": "앞 근거", "usage_note": "옛 설명", "pattern": "옛 문형"},
            {"word": "send", "reason": "뒤 근거", "usage_note": "새 설명"},
        ]
    )
    assert folded == 1
    entry = merged["send"]
    assert entry["usage_note"] == "새 설명", "뒤엣값이 이겨야 합니다"
    assert entry["pattern"] == "옛 문형", "뒤 항목에 없는 칸은 그대로 남아야 합니다"
    assert "앞 근거" in entry["reason"] and "뒤 근거" in entry["reason"]
