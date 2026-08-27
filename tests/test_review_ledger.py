"""사람이 읽은 낱말의 대장(content/review_ledger.py).

이 대장이 없어서 실제로 잃은 것이 있다. 검수는 지금까지 **고친 것만** 흔적을
남겼고 — 교정 YAML 두 개에 이름이 오르는 건 고쳐진 낱말뿐이다 — "읽었는데 고칠 데가
없었다" 는 판정은 어디에도 안 남았다. 빈도 상위 150개를 읽다 중간에 끊었을 때
어디까지 읽었는지가 통째로 사라져 처음부터 다시 읽어야 했다.

그래서 여기서 고정하는 것은 셋이다.

1. **읽은 낱말은 다시 안 나온다.** 대장의 쓸모가 전부 여기 달려 있다.
2. **표제어가 불리언으로 읽히면 시끄럽게 버린다.** `- word: on` 은 따옴표가 없으면
   PyYAML 이 True 로 읽는다. str() 하면 'true' 가 되는데, 그러면 있지도 않은 낱말이
   읽은 것으로 기록되고 정작 `on` 은 영영 안 읽은 채 남는다. 대장에서는 이 조용한
   거짓이 특히 나쁘다 — 검수를 건너뛰게 만드는 방향으로 틀리기 때문이다.
3. **같은 pass 에 두 번 기록해도 늘지 않는다.** 30~40개씩 끊어 붙이는 방식이라
   같은 구간을 두 번 넣는 일이 실제로 생긴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content"))

import review_ledger  # noqa: E402

from app.content.schemas import WordEntry  # noqa: E402
from app.db import crud  # noqa: E402

from .conftest import temporary_database  # noqa: E402

LEDGER = Path(__file__).resolve().parent.parent / "content" / "data" / "review_log.yaml"


def _entry(word: str, **over) -> WordEntry:
    base = {
        "word": word,
        "level": "A1",
        "meaning_ko": "뜻",
        "pattern": "명사",
        "example": f"I like {word}.",
        "usage_note": f"{word} 는 이렇게 써요.",
        "confused_with": [],
    }
    base.update(over)
    return WordEntry(**base)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """임시 DB 와 임시 대장. 진짜 대장 파일을 건드리면 안 된다."""
    monkeypatch.setattr(review_ledger, "LEDGER", tmp_path / "review_log.yaml")
    monkeypatch.setattr(review_ledger, "FIXES", tmp_path / "manual_fixes.yaml")
    monkeypatch.setattr(review_ledger, "GLOSS_FIXES", tmp_path / "gloss_fixes.yaml")
    with temporary_database(tmp_path / "ledger.db", monkeypatch) as database:
        monkeypatch.setattr(review_ledger, "db_session", database.db_session)
        yield database


def _seed_words(database, words: list[str], *, topic: str | None = None) -> None:
    with database.db_session() as session:
        for i, word in enumerate(words, 1):
            row = crud.upsert_word(session, _entry(word), topic=topic)
            row.rank = i


def _remaining(track: str | None = None) -> list[str]:
    doc = review_ledger.load_ledger()
    seen = review_ledger.read_words(doc)
    with review_ledger.db_session() as session:
        return [r.word for r in review_ledger._rows(session, track) if r.word not in seen]


def test_read_words_drop_out_of_the_queue(db):
    """대장에 오른 낱말은 '아직 안 읽은' 목록에서 빠진다."""
    _seed_words(db, ["coffee", "latte", "invoice"])
    assert _remaining() == ["coffee", "latte", "invoice"]

    review_ledger.save_ledger(
        {"passes": [{"id": "top", "date": "2026-08-27", "what": "빈도 1~2위", "words": ["coffee", "latte"]}]}
    )
    assert _remaining() == ["invoice"]


def test_unread_words_come_back_in_frequency_order(db):
    """빈도 순으로 준다. 검수는 자주 나오는 낱말부터 하는 게 이득이다."""
    _seed_words(db, ["one", "two", "three", "four"])
    assert _remaining()[:2] == ["one", "two"]


def test_boolean_headword_is_refused_not_stringified(db, caplog):
    """`- word: on` 은 'true' 로 기록되면 안 된다 — 버리고 알린다.

    str(True) 는 'true' 다. 그대로 넣으면 대장이 있지도 않은 'true' 를 읽었다고
    주장하고, 진짜 `on` 은 안 읽은 줄도 모르게 남는다.
    """
    assert review_ledger.headword(True) is None
    assert review_ledger.headword("On") == "on"

    review_ledger.FIXES.write_text("- word: on\n- word: 'off'\n", encoding="utf-8")
    words = review_ledger._load_words_of(review_ledger.FIXES)
    assert words == ["off"], "불리언으로 읽힌 표제어는 버린다"
    assert "true" not in words


def test_hand_written_ledger_hits_the_same_trap(db):
    """대장을 손으로 고쳐도 같은 함정이 있다. 읽는 쪽에서도 막는다."""
    review_ledger.LEDGER.write_text(
        "passes:\n- id: hand\n  date: '2026-08-27'\n  what: 손\n  words: [on, coffee]\n",
        encoding="utf-8",
    )
    seen = review_ledger.read_words(review_ledger.load_ledger())
    assert seen == {"coffee"}, "True 로 읽힌 것은 세지 않는다"


def test_recording_twice_does_not_grow_the_pass(db, tmp_path):
    """같은 구간을 두 번 넣어도 대장은 늘지 않는다."""
    _seed_words(db, ["coffee", "latte"])
    listing = tmp_path / "w.txt"
    listing.write_text("coffee\nlatte\n", encoding="utf-8")

    args = type(
        "A",
        (),
        {"record": "top", "what": "빈도 1~2위", "date": "2026-08-27", "words": None, "words_from": str(listing)},
    )()
    assert review_ledger.cmd_record(args) == 0
    assert review_ledger.cmd_record(args) == 0

    doc = review_ledger.load_ledger()
    assert len(doc["passes"]) == 1
    assert doc["passes"][0]["words"] == ["coffee", "latte"]


def test_seed_recovers_the_packs_it_can(db):
    """되살릴 수 있는 것만 되살린다 — 팩 전체와 교정 YAML 에 오른 이름."""
    _seed_words(db, ["coffee", "latte"], topic="cafe")
    _seed_words(db, ["invoice"])
    review_ledger.FIXES.write_text("- word: invoice\n  reason: 뭐라도\n", encoding="utf-8")

    assert review_ledger.cmd_seed() == 0
    seen = review_ledger.read_words(review_ledger.load_ledger())
    assert seen == {"coffee", "latte", "invoice"}
    assert _remaining() == []


def test_seed_is_idempotent(db):
    """두 번 돌려도 pass 가 겹쳐 쌓이지 않는다."""
    _seed_words(db, ["coffee"], topic="cafe")
    review_ledger.cmd_seed()
    review_ledger.cmd_seed()
    ids = [p["id"] for p in review_ledger.load_ledger()["passes"]]
    assert len(ids) == len(set(ids))


def test_the_real_ledger_parses_and_names_real_words():
    """실제 대장 파일 자체를 시험한다. 이건 검수 진도를 세는 데이터다.

    오타 하나가 낱말 하나를 조용히 두 번 읽게 하거나 영영 안 읽게 한다.
    """
    doc = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    assert doc["passes"], "대장이 비어 있습니다"
    for p in doc["passes"]:
        assert p["id"] and p["date"] and p["what"], f"pass 에 빠진 칸이 있습니다: {p}"
        assert p["words"], f"{p['id']} 에 낱말이 없습니다"
        for w in p["words"]:
            assert isinstance(w, str), f"{p['id']} 의 표제어가 글자가 아닙니다: {w!r}"
