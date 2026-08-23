"""3단계 콘텐츠 파이프라인: 생성 스키마, 저장, 검수 게이트, 리포트 연동."""

from __future__ import annotations

import pytest

from app.content.generator import WordGenerator, load_wordlist
from app.content.schemas import WordEntry
from app.tutor.schemas import Correction, json_schema_for


# ---------------------------------------------------------------- 스키마
def test_word_entry_schema_is_flat_and_strict():
    schema = json_schema_for(WordEntry)
    import json

    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert set(schema["required"]) == {
        "word", "level", "meaning_ko", "example", "usage_note", "confused_with",
    }


def test_word_is_lowercased_and_korean_normalized():
    entry = WordEntry(
        word="  Borrow ",
        level="A1",
        meaning_ko="빌리다",
        example="Can I borrow your pen?",
        usage_note="가격을 묻을 때가 아니라 물건을 빌릴 때 써요.",
        confused_with=["lend"],
    )
    assert entry.word == "borrow"
    assert "물을 때" in entry.usage_note  # ㄷ불규칙 정규화가 여기도 걸린다


def test_reviewed_is_not_in_the_llm_schema():
    """검수 여부는 사람이 정한다. 모델이 스스로 승인할 수 있으면 안 된다."""
    assert "reviewed" not in json_schema_for(WordEntry)["properties"]


# ---------------------------------------------------------------- 목록 로딩
def test_load_wordlist_skips_comments_and_dupes(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text(
        "# 주석\n\nborrow\nlend\nborrow\n  rent  \n한글\nword2\n", encoding="utf-8"
    )
    assert load_wordlist(path) == ["borrow", "lend", "rent"]


def test_load_wordlist_reads_csv_first_column(tmp_path):
    path = tmp_path / "ngsl.csv"
    path.write_text('"borrow",1234\n"lend",567\n', encoding="utf-8")
    assert load_wordlist(path, limit=1) == ["borrow"]


# ---------------------------------------------------------------- 생성
class _FakeClient:
    name = "fake"

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def describe(self):
        return "fake"

    def ping(self):
        return True

    def chat_json(self, **kwargs):
        self.calls += 1
        return dict(self._payload)


_GOOD = {
    "word": "borrow",
    "level": "A1",
    "meaning_ko": "빌리다 (내가 빌려 오는 쪽)",
    "example": "Can I borrow your pen?",
    "usage_note": "빌려주는 쪽은 lend 예요.",
    "confused_with": ["lend"],
}


def test_generate_one_returns_entry():
    result = WordGenerator(_FakeClient(_GOOD)).generate_one("borrow")
    assert result.ok
    assert result.entry.word == "borrow"


def test_generate_rejects_a_different_headword():
    """모델이 다른 단어로 바꿔치기하면 실패로 처리해야 한다."""
    client = _FakeClient({**_GOOD, "word": "lend"})
    result = WordGenerator(client).generate_one("borrow")
    assert not result.ok
    assert "lend" in result.error
    assert client.calls == 2  # 온도를 낮춰 1회 재시도


def test_generate_many_preserves_order():
    results = WordGenerator(_FakeClient(_GOOD)).generate_many(["borrow"], concurrency=2)
    assert [r.word for r in results] == ["borrow"]


# ---------------------------------------------------------------- 저장 + 검수 게이트
@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "content.db"))
    from app import config

    config.get_settings.cache_clear()
    import importlib

    from app.db import database

    importlib.reload(database)
    database.init_db()
    return database


def _entry(**over) -> WordEntry:
    return WordEntry.model_validate({**_GOOD, **over})


def test_upsert_stores_unreviewed(db):
    from app.db import crud

    with db.db_session() as s:
        row = crud.upsert_word(s, _entry())
        assert row.reviewed is False
        assert row.confused_with == ["lend"]
        assert crud.count_words(s, reviewed=False) == 1


def test_batch_never_overwrites_an_approved_entry(db):
    """사람이 승인한 항목을 배치가 덮어쓰면 검수가 무의미해진다."""
    from app.db import crud

    with db.db_session() as s:
        row = crud.upsert_word(s, _entry())
        crud.save_word_edits(s, row.id, meaning_ko="사람이 고친 뜻", reviewed=True)

    with db.db_session() as s:
        crud.upsert_word(s, _entry(meaning_ko="배치가 덮어쓰려는 뜻"))

    with db.db_session() as s:
        assert crud.list_words(s)[0].meaning_ko == "사람이 고친 뜻"


def test_existing_words_lets_batch_skip(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
    with db.db_session() as s:
        assert crud.existing_words(s) == {"borrow"}


def test_search_and_filter(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
        crud.upsert_word(s, _entry(word="lend", meaning_ko="빌려주다"))
    with db.db_session() as s:
        assert [r.word for r in crud.list_words(s, query="lend")] == ["lend"]
        assert [r.word for r in crud.list_words(s, query="빌려주다")] == ["lend"]
        assert crud.list_words(s, reviewed=True) == []


# ---------------------------------------------------------------- 리포트 연동
def test_word_tips_only_include_reviewed(db):
    """미검수 항목이 리포트로 새어 나가면 안 된다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())

    corrections = [
        Correction(kind="mistake", original="I borrow you pen", better="Can I borrow your pen?", note="~")
    ]

    with db.db_session() as s:
        assert crud.word_tips_for(s, corrections) == []  # 아직 미검수

    with db.db_session() as s:
        crud.save_word_edits(s, crud.list_words(s)[0].id, reviewed=True)

    with db.db_session() as s:
        tips = crud.word_tips_for(s, corrections)
        assert [t.word for t in tips] == ["borrow"]
        assert tips[0].confused_with == ["lend"]


def test_word_tips_empty_without_corrections(db):
    from app.db import crud

    with db.db_session() as s:
        assert crud.word_tips_for(s, []) == []


def test_tokenize_lowercases_and_drops_punctuation():
    from app.db.crud import tokenize

    assert tokenize("Can I borrow your pen?") == {"can", "borrow", "your", "pen"}
