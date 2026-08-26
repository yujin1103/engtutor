"""3단계 콘텐츠 파이프라인: 생성 스키마, 저장, 검수 게이트, 리포트 연동."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.content.generator import WordGenerator, declares_no_rank, load_wordlist
from app.content.schemas import WordEntry
from app.tutor.schemas import Correction, json_schema_for


# ---------------------------------------------------------------- 스키마
def test_word_entry_schema_is_flat_and_strict():
    schema = json_schema_for(WordEntry)
    import json

    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert set(schema["required"]) == {
        "word", "level", "meaning_ko", "pattern", "example", "usage_note", "confused_with",
    }


def test_pattern_is_generated_before_the_example():
    """스키마 순서가 곧 생성 순서다. 형태를 먼저 정해야 예문이 그 형태를 따라간다.

    반대로 두면 모델이 예문을 쓴 뒤 거기에 맞는 문형을 갖다 붙인다 — 그러면
    문형은 예문의 요약일 뿐이고, 왕초보가 틀리는 지점을 짚지 못한다.
    """
    keys = list(json_schema_for(WordEntry)["properties"])
    assert keys.index("pattern") < keys.index("example")


def test_word_is_lowercased_and_korean_normalized():
    entry = WordEntry(
        word="  Borrow ",
        level="A1",
        meaning_ko="빌리다",
        pattern="borrow + 목적어",
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


def test_a_list_can_declare_that_its_order_is_not_frequency(tmp_path):
    """장면별로 묶은 목록의 순서를 빈도로 읽으면 americano 가 the 보다 앞에 온다."""
    path = tmp_path / "app_words.txt"
    path.write_text("# rank: none\n# 장면별 묶음\nlatte\nsubway\n", encoding="utf-8")
    assert declares_no_rank(path)
    assert load_wordlist(path) == ["latte", "subway"]


def test_a_plain_list_still_carries_its_order(tmp_path):
    """NGSL 은 파일 순서가 곧 빈도 순서다. 선언이 없으면 예전 그대로 동작해야 한다."""
    path = tmp_path / "ngsl.csv"
    path.write_text("# NGSL headwords\nbe,1\nand,2\n", encoding="utf-8")
    assert not declares_no_rank(path)


def test_the_declaration_must_be_in_the_comment_block(tmp_path):
    """단어가 시작된 뒤에 나오는 `# rank: none` 은 목록의 선언이 아니다."""
    path = tmp_path / "words.txt"
    path.write_text("borrow\n# rank: none\nlend\n", encoding="utf-8")
    assert not declares_no_rank(path)


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
    "pattern": "borrow + 목적어 (+ from + 사람)",
    "example": "Can I borrow your pen?",
    "usage_note": "빌려주는 쪽은 lend 예요.",
    "confused_with": ["lend"],
}


# ---------------------------------------------------------------- 문형(pattern)
def test_pattern_strips_code_formatting():
    """모델이 문형을 백틱으로 감싸 내놓는 일이 잦다. 학습자에게 그대로 보이는 값이다."""
    entry = WordEntry(**{**_GOOD, "pattern": "  `borrow + 목적어`  "})
    assert entry.pattern == "borrow + 목적어"


def test_pattern_rejects_a_definition_pasted_into_the_form_field():
    """형태를 적는 칸이다. 설명이 들어오면 저장 시점에 잘려 문형이 깨진다."""
    with pytest.raises(ValidationError):
        WordEntry(**{**_GOOD, "pattern": "빌리다라는 뜻으로 " + "아주 길게 설명하는 문장 " * 8})


def test_pattern_is_required():
    """빠뜨릴 수 있게 두면 모델이 대부분 빠뜨린다 — 2,801개 중 10개만 짚었던 그 필드다."""
    payload = {k: v for k, v in _GOOD.items() if k != "pattern"}
    with pytest.raises(ValidationError):
        WordEntry(**payload)


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


def test_word_tips_find_the_word_through_its_inflected_form(db):
    """교정 문장은 굴절형을 쓴다 — `borrowed`. 표제어는 원형이라 글자가 다르다.

    글자 그대로만 맞추면 학습자가 방금 틀린 그 단어의 팁이 리포트에서 빠진다.
    실제 교정 70건에서 이렇게 놓치는 토큰이 66개였다.
    """
    from app.content import lexicon
    from app.db import crud

    if not lexicon.available():
        pytest.skip("WordNet 코퍼스가 없습니다")

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
        crud.save_word_edits(s, crud.list_words(s)[0].id, reviewed=True)

    corrections = [
        Correction(
            kind="mistake",
            original="I borrowed you pen",
            better="I borrowed your pen.",
            note="your 가 맞아요.",
        )
    ]
    with db.db_session() as s:
        assert [t.word for t in crud.word_tips_for(s, corrections)] == ["borrow"]


def test_word_tips_show_the_learners_own_word_first(db):
    """자리가 다섯뿐이라 순서가 곧 무엇을 버리느냐다. 그대로 쓴 단어가 먼저다."""
    from app.content import lexicon
    from app.db import crud
    from app.content.schemas import WordEntry

    if not lexicon.available():
        pytest.skip("WordNet 코퍼스가 없습니다")

    lend = WordEntry(
        word="lend",
        level="A1",
        meaning_ko="빌려주다 (내가 주는 쪽)",
        pattern="lend + 사람 + 물건",
        example="Can you lend me a pen?",
        usage_note="빌려 오는 쪽은 borrow 예요.",
        confused_with=["borrow"],
    )
    with db.db_session() as s:
        crud.upsert_word(s, _entry())
        crud.upsert_word(s, lend)
        for row in crud.list_words(s):
            crud.save_word_edits(s, row.id, reviewed=True)

    # lend 는 글자 그대로, borrow 는 굴절형(borrowed)으로만 등장한다.
    corrections = [
        Correction(
            kind="mistake",
            original="I borrowed a pen",
            better="Can you lend me a pen?",
            note="빌려주는 쪽이라 lend 예요.",
        )
    ]
    with db.db_session() as s:
        assert [t.word for t in crud.word_tips_for(s, corrections)][0] == "lend"


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


# ---------------------------------------------------------------- 문형 백필
def test_pattern_survives_the_round_trip(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
    with db.db_session() as s:
        assert crud.list_words(s)[0].pattern == "borrow + 목적어 (+ from + 사람)"


def test_missing_pattern_is_listed_by_frequency_and_skips_approved(db):
    """2,801개를 통째로 다시 돌릴 이유가 없다 — 빠진 것만, 자주 쓰는 것부터 채운다.

    승인된 항목은 빠진다. 배치가 검수 결과를 덮어쓰지 않는다는 규칙이 여기에도 걸린다.
    """
    from app.db import crud

    with db.db_session() as s:
        for word in ("borrow", "lend", "rent"):
            crud.upsert_word(s, _entry(word=word, example=f"Can I {word} it?"))
        crud.assign_ranks(s, ["rent", "lend", "borrow"])

    with db.db_session() as s:
        rows = {r.word: r for r in crud.list_words(s)}
        # pattern 이 생기기 전에 저장된 항목을 흉내 낸다.
        crud.save_word_edits(s, rows["borrow"].id, pattern=None)
        crud.save_word_edits(s, rows["lend"].id, pattern="")
        crud.save_word_edits(s, rows["rent"].id, pattern=None, reviewed=True)

    with db.db_session() as s:
        # rent 는 승인돼서 빠지고, 남은 둘은 빈도 순(lend #2 -> borrow #3)이다.
        assert crud.words_missing_pattern(s) == ["lend", "borrow"]
        assert "rent" in crud.words_missing_pattern(s, include_reviewed=True)


def test_word_tips_carry_the_pattern(db):
    """리포트에서 학습자가 보는 건 뜻보다 형태다. 여기서 끊기면 문형이 갈 곳이 없다."""
    from app.db import crud

    with db.db_session() as s:
        row = crud.upsert_word(s, _entry())
        crud.save_word_edits(s, row.id, reviewed=True)

    corrections = [
        Correction(kind="mistake", original="I borrow you pen", better="Can I borrow your pen?", note="빌리다는 borrow 예요.")
    ]
    with db.db_session() as s:
        assert crud.word_tips_for(s, corrections)[0].pattern == "borrow + 목적어 (+ from + 사람)"


def test_word_tips_tolerate_entries_made_before_pattern_existed(db):
    """문형이 없다고 리포트가 실패하면 안 된다. 안 보여줄 뿐이다."""
    from app.db import crud

    with db.db_session() as s:
        row = crud.upsert_word(s, _entry())
        crud.save_word_edits(s, row.id, pattern=None, reviewed=True)

    corrections = [
        Correction(kind="mistake", original="I borrow you pen", better="Can I borrow your pen?", note="빌리다는 borrow 예요.")
    ]
    with db.db_session() as s:
        assert crud.word_tips_for(s, corrections)[0].pattern is None


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
