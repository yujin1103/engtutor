"""장면 묶음(topic) — 다른 회화 앱들이 '유닛'이라 부르는 것.

빈도 목록만으로는 카페에서 쓸 말을 모을 수 없다. NGSL 2,801개에 `americano` 도
`towel` 도 없었다. 그래서 장면별로 묶은 어휘를 따로 넣고, 검수도 빈칸도 장면 단위로
끊을 수 있게 한다. 여기 시험은 **묶음이 사라지지 않는가**에 초점을 둔다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.content.generator import load_topics, load_wordlist
from app.content.schemas import WordEntry

from .conftest import temporary_database


@pytest.fixture()
def db(tmp_path, monkeypatch):
    with temporary_database(tmp_path / "topics.db", monkeypatch) as database:
        yield database


def _entry(word: str = "latte", **over) -> WordEntry:
    base = {
        "word": word,
        "level": "A2",
        "meaning_ko": "라떼 (우유를 넣은 커피)",
        "pattern": "a/the + latte",
        "example": "I'll have a latte, please.",
        "usage_note": "우유가 들어간 커피예요. 아메리카노와 다릅니다.",
        "confused_with": ["americano"],
    }
    base.update(over)
    return WordEntry(**base)


# ------------------------------------------------------------------ 목록 파일
def test_words_take_the_topic_declared_above_them(tmp_path: Path):
    path = tmp_path / "words.txt"
    path.write_text(
        "# rank: none\n# topic: cafe\nlatte\nespresso\n\n# topic: hotel\ntowel\n",
        encoding="utf-8",
    )
    assert load_topics(path) == {"latte": "cafe", "espresso": "cafe", "towel": "hotel"}


def test_words_before_any_declaration_have_no_topic(tmp_path: Path):
    """NGSL 같은 일반 어휘 목록은 묶음이 없다. 없는 것을 지어내면 안 된다."""
    path = tmp_path / "words.txt"
    path.write_text("borrow\nlend\n# topic: cafe\nlatte\n", encoding="utf-8")
    assert load_topics(path) == {"latte": "cafe"}


def test_a_plain_list_declares_nothing(tmp_path: Path):
    path = tmp_path / "ngsl.csv"
    path.write_text("# NGSL headwords\nbe,1\nand,2\n", encoding="utf-8")
    assert load_topics(path) == {}


def test_a_hyphenated_headword_survives_the_list(tmp_path: Path):
    """`check-in`·`sugar-free` 가 조용히 걸러지고 있었다 — 목록에 적어도 생성되지 않았다."""
    path = tmp_path / "words.txt"
    path.write_text("# topic: airport\ncheck-in\ncarry-on\n", encoding="utf-8")
    assert load_wordlist(path) == ["check-in", "carry-on"]
    assert load_topics(path) == {"check-in": "airport", "carry-on": "airport"}


# ------------------------------------------------------------------ 저장
def test_the_topic_is_stored_with_the_entry(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry(), topic="cafe")

    with db.db_session() as s:
        assert crud.list_words(s)[0].topic == "cafe"
        assert crud.topics(s) == [("cafe", 1, 0)]


def test_regenerating_without_a_topic_does_not_erase_it(db):
    """장면 없는 목록으로 한 번만 다시 돌려도 묶음이 통째로 날아가면 안 된다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry(), topic="cafe")
    with db.db_session() as s:
        crud.upsert_word(s, _entry(meaning_ko="라떼 (커피의 한 종류)"))

    with db.db_session() as s:
        row = crud.list_words(s)[0]
        assert row.topic == "cafe"
        assert row.meaning_ko == "라떼 (커피의 한 종류)"


def test_an_approved_entry_keeps_its_content_but_can_gain_a_topic(db):
    """묶음은 내용이 아니라 분류다. 승인된 항목의 뜻은 지키되 분류는 붙일 수 있어야 한다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
        crud.save_word_edits(s, crud.list_words(s)[0].id, reviewed=True, meaning_ko="사람이 고친 뜻")

    with db.db_session() as s:
        crud.upsert_word(s, _entry(meaning_ko="배치가 다시 쓴 뜻"), topic="cafe")

    with db.db_session() as s:
        row = crud.list_words(s)[0]
        assert row.meaning_ko == "사람이 고친 뜻"
        assert row.topic == "cafe"


def test_assign_topics_backfills_without_calling_the_model(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())

    with db.db_session() as s:
        assert crud.assign_topics(s, {"latte": "cafe"}) == 1
        assert crud.assign_topics(s, {"latte": "cafe"}) == 0  # 두 번째는 바뀔 게 없다


# ------------------------------------------------------------------ 빈칸 연동
def test_cloze_can_be_limited_to_one_scene(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry(), topic="cafe")
        crud.upsert_word(
            s,
            _entry(
                word="towel",
                meaning_ko="수건",
                pattern="a/the + towel",
                example="Can I get a towel, please?",
                usage_note="호텔에서 수건을 더 달라고 할 때 써요.",
                confused_with=[],
            ),
            topic="hotel",
        )

    with db.db_session() as s:
        assert [r.word for r in crud.cloze_candidates(s, topic="cafe")] == ["latte"]
        assert [r.word for r in crud.cloze_candidates(s, topic="hotel")] == ["towel"]
        assert len(crud.cloze_candidates(s)) == 2


# ------------------------------------------------------------------ 등급 대조
def test_the_reference_level_table_answers_or_says_it_does_not_know():
    """모르는 것을 아는 척하면 레벨 조정이 통째로 거짓이 된다."""
    from app.content import lexicon

    if lexicon.reference_level("water") is None:
        pytest.skip("등급표가 없습니다 (content/prepare_cefrj.py)")
    assert lexicon.reference_level("water") in lexicon.LEVEL_ORDER
    assert lexicon.reference_level("zzzznotaword") is None


def test_level_distance_points_the_right_way():
    from app.content import lexicon

    assert lexicon.level_distance("B1", "A1") > 0  # 우리가 더 어렵게 봤다
    assert lexicon.level_distance("A1", "B1") < 0
    assert lexicon.level_distance("A2", "A2") == 0
    assert lexicon.level_distance("A2", "Z9") is None
