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
        self.messages = kwargs.get("messages")
        return dict(self._payload)


class _SequenceClient(_FakeClient):
    """호출마다 다른 응답을 준다. 재시도 동작을 보려는 것."""

    def __init__(self, *payloads):
        super().__init__(payloads[0])
        self._queue = list(payloads)
        self.sent: list[list[dict]] = []

    def chat_json(self, **kwargs):
        self.calls += 1
        self.sent.append(kwargs.get("messages"))
        # 다 떨어지면 마지막 응답을 반복한다 (재시도 횟수를 시험이 강제하지 않도록)
        payload = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        return dict(payload)


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
    """모델이 다른 단어로 바꿔치기하면 실패로 처리해야 한다.

    실제로 arrange -> arrive 로 바뀌어 NGSL 배치에서 딱 한 단어가 빠졌다.
    비슷하게 생긴 고빈도 단어로 끌려가는 실패라 검사를 느슨하게 하면 안 된다.
    """
    client = _FakeClient({**_GOOD, "word": "lend"})
    result = WordGenerator(client).generate_one("borrow")
    assert not result.ok
    assert "lend" in result.error
    assert client.calls == 3  # 온도를 낮추며 두 번 재시도


def test_retry_tells_the_model_what_went_wrong():
    """같은 요청을 그대로 반복하면 대개 똑같이 실패한다. 무엇이 틀렸는지 알려줘야 한다."""
    client = _SequenceClient({**_GOOD, "word": "lend"})
    WordGenerator(client).generate_one("borrow")

    first, second = client.sent[0], client.sent[1]
    assert len(first) == 1, "1차 요청에는 수리 지시문이 붙지 않는다"
    note = second[-1]["content"]
    assert "lend" in note, "무엇을 잘못했는지가 안 들어갔다"
    assert '"borrow"' in note, "올바른 표제어를 명시해야 한다"


def test_retry_recovers_the_headword():
    """1차에 다른 단어를 뱉어도 재시도로 복구되면 성공이다 (arrange 사례)."""
    client = _SequenceClient({**_GOOD, "word": "lend"}, _GOOD)
    result = WordGenerator(client).generate_one("borrow")
    assert result.ok and result.entry.word == "borrow"
    assert client.calls == 2


def test_generate_many_preserves_order():
    results = WordGenerator(_FakeClient(_GOOD)).generate_many(["borrow"], concurrency=2)
    assert [r.word for r in results] == ["borrow"]


# ---------------------------------------------------------------- 저장 + 검수 게이트
@pytest.fixture()
def db(tmp_path, monkeypatch):
    from .conftest import temporary_database

    with temporary_database(tmp_path / "content.db", monkeypatch) as database:
        yield database


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
        crud.upsert_word(
            s,
            _entry(word="lend", meaning_ko="빌려주다", example="Can you lend me a pen?"),
        )
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
        Correction(kind="mistake", original="I borrow you pen", better="Can I borrow your pen?", note="빌리다는 borrow 예요.")
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


# ---------------------------------------------------------------- 검수 우선순위
def test_ranks_follow_the_wordlist_order(db):
    """NGSL 은 목록 순서가 곧 빈도 순서다. 저장하며 그 정보를 잃고 있었다.

    검수를 중간에 멈춰도 자주 쓰는 단어부터 승인돼 있어야 리포트에 도움이 된다.
    """
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
        crud.upsert_word(
            s, _entry(word="lend", meaning_ko="빌려주다", example="Can you lend me a pen?")
        )

    with db.db_session() as s:
        assert crud.assign_ranks(s, ["lend", "borrow"]) == 2

    with db.db_session() as s:
        ranks = {r.word: r.rank for r in crud.list_words(s)}
    assert ranks == {"lend": 1, "borrow": 2}


def test_ranks_ignore_words_not_in_the_database(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
    with db.db_session() as s:
        assert crud.assign_ranks(s, ["nonexistent", "borrow"]) == 1
    with db.db_session() as s:
        assert crud.list_words(s)[0].rank == 2


def test_reassigning_the_same_ranks_changes_nothing(db):
    """배치를 다시 돌려도 순위가 흔들리면 검수 순서가 뒤집힌다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
    with db.db_session() as s:
        crud.assign_ranks(s, ["borrow"])
    with db.db_session() as s:
        assert crud.assign_ranks(s, ["borrow"]) == 0
