"""단어 연습장 — 자리 읽기, 넓어진 판정, 설명 카드.

여기 시험의 초점은 "잘 가르치는가"가 아니라 **"아는 것보다 더 주장하지 않는가"**다.
WordNet 은 품사를 알지 그 자리에서 뜻이 통하는지는 모른다. 그 경계를 넘는 순간
학습자가 거짓을 외우므로, 경계를 넘지 않는지를 하나씩 못박는다.
"""

from __future__ import annotations

import pytest

from app.content import lexicon
from app.tutor import practice
from app.tutor.cloze import grade, make_item, pos_hint, pos_of
from app.tutor.slot import BLANK, head_word, narrow, slot_pos

from .conftest import temporary_database

needs_lexicon = pytest.mark.skipif(not lexicon.available(), reason="WordNet 코퍼스가 없습니다")


def _row(**over):
    base = {
        "word": "cookie",
        "level": "A1",
        "meaning_ko": "쿠키 (과자)",
        "pattern": "a/the + cookie",
        "example": "Can I have a cookie with my coffee?",
        "example_ko": "커피에 곁들여 쿠키 하나 주세요.",
        "usage_note": "작은 과자를 말해요. 케이크와는 달라요.",
        "confused_with": ["cake"],
        "rank": None,
        "topic": "cafe",
        "reviewed": False,
    }
    base.update(over)
    return type("W", (), base)()


def _item(**over):
    item = make_item(_row(**over))
    assert item is not None
    return item


# --- 자리 읽기 -----------------------------------------------------------------
#
# 규칙은 닫힌 부류(관사·조동사)만 본다. 통계 태거를 쓰지 않은 이유는 slot.py 참고.


@pytest.mark.parametrize(
    "sentence,expected",
    [
        (f"Can I have a {BLANK} with my coffee?", "n"),  # 관사 + 뒤가 전치사
        (f"I need a {BLANK}.", "n"),  # 관사 + 문장 끝
        (f"There is a {BLANK} of food.", "n"),  # 관사 + of
        (f"I will {BLANK} the letter tomorrow.", "v"),  # 조동사 바로 뒤
        (f"Can you {BLANK} me some water?", "v"),  # 조동사 + 주어 + 빈칸
        (f"Please {BLANK} the table for dinner.", "v"),  # 문장 첫머리 please
    ],
)
def test_closed_class_words_read_the_slot(sentence, expected):
    assert slot_pos(sentence) == frozenset(expected)


@pytest.mark.parametrize(
    "sentence",
    [
        f"I want a {BLANK} pen.",  # 관사 뒤지만 명사구가 안 끝났다 — 형용사 자리일 수 있다
        f"I didn't know that, {BLANK}.",  # 쉼표가 끼어 있다. that 이 빈칸 앞이 아니다
        f"I am {BLANK} to work.",  # 규칙 밖
        f"She {BLANK} the door.",  # 규칙 밖
    ],
)
def test_the_slot_stays_unknown_when_no_rule_fits(sentence):
    """규칙이 안 걸리면 판정하지 않는다. lexicon.parts_of_speech 의 None 과 같은 규약."""
    assert slot_pos(sentence) is None


def test_a_rule_that_contradicts_the_dictionary_is_dropped():
    """`I can ____ see the road.` 의 정답은 부사 barely 다. 조동사 뒤라고 동사가 아니다.

    실측 844건 중 4건이 이렇게 어긋났다. 어느 쪽이 틀렸는지 결정론적으로 가릴 수
    없어서 **덜 주장하는 쪽**으로 넘어진다 — 좁히기를 포기하고 낱말 품사를 그대로 쓴다.
    """
    sentence = f"I can {BLANK} see the road."
    assert slot_pos(sentence) == frozenset("v")
    assert narrow(sentence, frozenset("r")) == (frozenset("r"), "word")


def test_narrowing_reports_what_it_may_claim():
    slot = narrow(f"I take the {BLANK} to work.", frozenset("nv"))
    assert slot == (frozenset("n"), "slot")
    word = narrow(f"I drink {BLANK} every day.", frozenset("nv"))
    assert word == (frozenset("nv"), "word")


def test_narrowing_needs_a_dictionary_answer():
    assert narrow(f"I need a {BLANK}.", None) is None


# --- 품사 힌트 -----------------------------------------------------------------


@needs_lexicon
def test_a_multi_pos_word_in_a_read_slot_is_narrowed():
    """`water` 는 명사이자 동사다. `Please ____ the plants.` 에서는 동사 하나다."""
    hint = pos_hint(_item(word="water", example="Please water the plants.", topic=None))
    assert hint is not None
    assert hint.pos == ("v",) and hint.source == "slot"
    assert hint.text_ko == "여기엔 동사가 들어가요."


@needs_lexicon
def test_a_multi_pos_word_in_an_unread_slot_shows_every_part_of_speech():
    """좁히지 못하면 자리를 말하지 않는다 — 그게 사용자가 원한 학습 지점이기도 하다."""
    hint = pos_hint(_item(word="water", example="I drink water every day.", topic=None))
    assert hint is not None
    assert hint.pos == ("n", "v") and hint.source == "word"
    assert hint.text_ko == "이 낱말은 명사로도 동사로도 써요."
    assert "여기엔" not in hint.text_ko  # 자리를 모르면서 자리를 말하면 안 된다


@needs_lexicon
@pytest.mark.parametrize(
    "word,example",
    [("and", "I like coffee and tea."), ("the", "I take the bus.")],
)
def test_no_hint_is_invented_when_the_dictionary_says_nothing(word, example):
    """`parts_of_speech` 가 None 이면 힌트를 빼고 낸다. 힌트 없는 빈칸도 빈칸이다."""
    assert pos_hint(_item(word=word, example=example, topic=None)) is None


@needs_lexicon
def test_a_function_word_is_never_given_a_part_of_speech():
    """WordNet 에 a(비타민 A)·in(인치)·he(헬륨) 가 있다. 사전이 맞고 화면이 거짓말한다."""
    assert lexicon.parts_of_speech("a") is not None
    assert pos_of("a") is None
    assert pos_of("in") is None


@needs_lexicon
def test_an_inflected_answer_is_read_through_its_lemma():
    """`said` 를 그대로 물으면 형용사(aforesaid)로 잡힌다. 원형으로 되돌려야 동사다."""
    assert "v" in (pos_of("said") or set())


# --- 구·절로 답하기 -------------------------------------------------------------
#
# 사용자가 명시적으로 원한 것이다. `펜 좀 빌려도 될까요?` 를 알면 pen · a pen ·
# your pen 이 다 답이다. 오답으로 처리하지 않고 머리 낱말로 줄여 같은 사다리에 태운다.


@pytest.mark.parametrize(
    "said,head",
    [
        ("a pen", "pen"),
        ("your pen", "pen"),
        ("the red pen", "pen"),
        ("to the store", "store"),
        ("a cup of coffee", "cup"),  # 머리는 coffee 가 아니라 cup 이다
        ("pick up", "pick"),  # 불변화사는 머리가 아니다
    ],
)
def test_the_head_of_a_phrase_is_what_gets_judged(said, head):
    assert head_word(said) == (head, True)


def test_a_single_word_is_not_treated_as_a_phrase():
    assert head_word("Pen!") == ("pen", False)


@needs_lexicon
def test_an_article_in_front_of_the_answer_is_still_correct():
    result = grade(_item(), "a cookie")
    assert result.verdict == "correct" and result.ok
    assert result.head == "cookie"
    # 무엇을 보고 판정했는지 반드시 드러낸다. 머리 낱말 뽑기는 틀릴 수 있다.
    assert "cookie" in result.message_ko and "a cookie" in result.message_ko


@needs_lexicon
def test_a_phrase_with_a_different_head_is_judged_on_that_head():
    result = grade(_item(), "a scone")
    assert result.verdict == "right_pos" and result.head == "scone"


# --- 넓어진 판정 ---------------------------------------------------------------


@needs_lexicon
def test_the_same_part_of_speech_is_no_longer_lumped_with_everything_else():
    """예전에는 `scone` 도 `quickly` 도 똑같이 wrong_word 였다."""
    assert grade(_item(), "scone").verdict == "right_pos"


@needs_lexicon
def test_a_different_part_of_speech_is_the_lesson_this_app_wants_to_teach():
    result = grade(_item(), "quickly")
    assert result.verdict == "wrong_pos"
    assert result.said_pos == ("r",)
    assert "부사" in result.message_ko and "명사" in result.message_ko


@needs_lexicon
def test_the_message_never_claims_the_answer_makes_sense_in_the_sentence():
    """WordNet 은 banana 가 명사인 건 알아도 이 문장에 어울리는지는 모른다."""
    message = grade(_item(), "banana").message_ko
    assert "품사는 맞아요" in message
    assert "뜻이 통" not in message and "어울" not in message


@needs_lexicon
def test_a_function_word_answer_falls_back_instead_of_claiming_a_part_of_speech():
    """`the` 의 품사를 WordNet 에 물으면 안 된다. 비교할 수 없으면 wrong_word 로 둔다."""
    result = grade(_item(), "the")
    assert result.verdict == "wrong_word"
    assert result.said_pos == ()


@needs_lexicon
def test_the_old_verdicts_still_come_out_of_the_same_places():
    """이름을 바꾸지 않았다. 웹 UI 와 기존 시험이 문자열을 그대로 쓴다."""
    assert grade(_item(), "cookie").verdict == "correct"
    assert grade(_item(), "cookies").verdict == "wrong_form"
    assert grade(_item(), "cookee").verdict == "not_a_word"
    assert grade(_item(), "").verdict == "empty"


# --- 설명 카드 -----------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    with temporary_database(tmp_path / "practice.db", monkeypatch) as database:
        yield database


def _seed(database, rows):
    from app.db.models import WordRow

    with database.db_session() as session:
        for values in rows:
            # 선별기를 통과할 만한 최소 행. 설명이 비면 `usage_note_too_short` 로
            # 걸려서 후보에서 통째로 빠진다 — 후보 고르기가 아니라 선별기를 시험하게 된다.
            base = {
                "level": "A1",
                "meaning_ko": "뜻",
                "example": "",
                "usage_note": "이 낱말은 이렇게 써요. 예문을 그대로 따라 말해 보세요.",
                "confused_with": [],
                "reviewed": False,
            }
            base.update(values)
            session.add(WordRow(**base))


def test_an_item_without_a_gloss_still_makes_a_card():
    """해석은 3,245개 중 792개만 채워져 있다. 없으면 안 보여줄 뿐이어야 한다."""
    item = _item(example_ko=None)
    assert item.example_ko is None
    card = practice.explain(None, _row(example_ko=None), item)
    assert card.example_ko is None
    assert card.example == "Can I have a cookie with my coffee?"


def test_an_unreviewed_note_never_lands_in_the_verified_slot():
    """확인된 환각 13건이 전부 usage_note 와 confused_with 에 있었다.

    플래그 하나로 두면 언젠가 화면이 그 플래그를 안 본다. 그래서 자리를 갈랐다.
    """
    card = practice.explain(None, _row(reviewed=False), _item())
    assert card.usage_note is None and card.confused_with == ()
    assert card.unverified is not None
    assert card.unverified.usage_note.startswith("작은 과자")
    assert "확인하지 않은" in card.unverified.note_ko


def test_a_reviewed_note_moves_into_the_verified_slot():
    card = practice.explain(None, _row(reviewed=True), _item(reviewed=True))
    assert card.usage_note is not None and card.confused_with == ("cake",)
    assert card.unverified is None


def test_a_note_the_learner_cannot_read_never_reaches_the_card():
    """설명에도 못 읽는 글자가 섞인 것이 있다 — 출제 가능 2,950개 중 22개.

    뜻과 달리 항목을 빼지 않고 그 칸만 뗀다. 설명은 답을 본 뒤에만 보이는 곁가지라,
    이것 때문에 항목을 빼면 `she`·`need`·`sure` 가 연습장에서 통째로 사라진다.
    """
    note = "한국어 '아' 또는 '叹气'로 표현하지만 영어에서는 동사로 써요."
    card = practice.explain(None, _row(usage_note=note), _item())
    assert card.unverified is not None
    assert card.unverified.usage_note is None
    assert card.unverified.confused_with == ("cake",)  # 나머지는 그대로 나간다
    assert practice.explain(None, _row(usage_note=note, reviewed=True), _item()).usage_note is None


def test_an_item_with_no_notes_gets_no_empty_warning_box():
    card = practice.explain(None, _row(usage_note="", confused_with=[]), _item())
    assert card.unverified is None


# --- 같은 자리에 올 수 있는 다른 낱말들 -----------------------------------------


@needs_lexicon
def test_alternatives_come_only_from_the_words_table(db):
    from app.db import crud

    _seed(
        db,
        [
            {"word": "cookie", "example": "Can I have a cookie?", "topic": "cafe"},
            {"word": "scone", "example": "Can I have a scone?", "topic": "cafe"},
            {"word": "mug", "example": "I need a mug.", "topic": "cafe"},
            {"word": "drink", "example": "I drink water.", "topic": "hotel"},
        ],
    )
    with db.db_session() as session:
        alts = practice.alternatives_for(session, _item(topic="cafe"))
        assert alts is not None
        found = {a.word for a in alts.words}
        assert found <= {"scone", "mug"}  # 다른 장면(hotel)도, 정답 자신도 안 나온다
        stored = {w.word for w in crud.list_words(session)}
        assert found <= stored


@needs_lexicon
def test_the_label_says_what_the_list_is_grouped_by(db):
    """'이 자리에 올 수 있어요' 는 거짓이다. 우리가 아는 건 같은 장면이라는 것뿐이다."""
    _seed(
        db,
        [
            {"word": "cookie", "example": "Can I have a cookie?", "topic": "cafe"},
            {"word": "scone", "example": "Can I have a scone?", "topic": "cafe"},
        ],
    )
    with db.db_session() as session:
        alts = practice.alternatives_for(session, _item(topic="cafe"))
        assert alts is not None and alts.basis == "topic"
        assert alts.label_ko == "같은 장면(카페)에서 쓰는 명사예요."
        assert "올 수 있" not in alts.label_ko


@needs_lexicon
def test_words_without_a_topic_fall_back_to_frequency(db):
    """3,245개 중 장면이 붙은 것은 444개뿐이다. 대부분이 이 길로 온다."""
    _seed(
        db,
        [
            {"word": "cup", "example": "I need a cup.", "rank": 100},
            {"word": "plate", "example": "I need a plate.", "rank": 101},
            {"word": "spoon", "example": "I need a spoon.", "rank": 2000},
        ],
    )
    with db.db_session() as session:
        alts = practice.alternatives_for(
            session, _item(word="cup", example="I need a cup.", topic=None, rank=100)
        )
        assert alts is not None and alts.basis == "rank"
        assert alts.label_ko == "비슷하게 자주 쓰는 명사예요."
        assert "plate" in {a.word for a in alts.words}


@needs_lexicon
def test_an_empty_candidate_pool_gives_no_list_instead_of_an_invented_one(db):
    _seed(db, [{"word": "cookie", "example": "Can I have a cookie?", "topic": "cafe"}])
    with db.db_session() as session:
        assert practice.alternatives_for(session, _item(topic="cafe")) is None


@needs_lexicon
def test_alternatives_match_the_narrowed_part_of_speech(db):
    """`Please ____ the plants.` 는 동사 자리다. 명사만 되는 낱말이 끼면 안 된다."""
    _seed(
        db,
        [
            {"word": "water", "example": "Please water the plants.", "rank": 300},
            {"word": "cook", "example": "I cook dinner.", "rank": 301},
            {"word": "americano", "example": "I want an americano.", "rank": 302},
        ],
    )
    with db.db_session() as session:
        alts = practice.alternatives_for(
            session, _item(word="water", example="Please water the plants.", topic=None, rank=300)
        )
        assert alts is not None
        found = {a.word for a in alts.words}
        assert "cook" in found and "americano" not in found
        assert alts.label_ko == "비슷하게 자주 쓰는 동사예요."


@needs_lexicon
def test_a_candidate_whose_meaning_is_unreadable_is_dropped(db):
    """선별기가 못 잡는 것이 남아 있다 — bagel 의 뜻이 `백일(백面包)` 로 저장돼 있다."""
    _seed(
        db,
        [
            {"word": "cookie", "example": "Can I have a cookie?", "topic": "cafe"},
            {
                "word": "bagel",
                "example": "I want a bagel.",
                "topic": "cafe",
                "meaning_ko": "백일(백面包)",
            },
            {"word": "scone", "example": "Can I have a scone?", "topic": "cafe"},
        ],
    )
    with db.db_session() as session:
        alts = practice.alternatives_for(session, _item(topic="cafe"))
        assert alts is not None
        assert {a.word for a in alts.words} == {"scone"}


@needs_lexicon
def test_the_label_is_true_of_every_word_in_the_list(db):
    """이름표가 정답 낱말의 품사를 그대로 옮겨 적던 시절의 결함.

    `bitter` 는 명사·동사·형용사·부사가 다 되는데 목록에 실제로 남는 것은
    `americano`(명사) 하나였다. 그래서 이름표가 "명사·동사·형용사·부사예요"라고
    말하면서 유일한 구성원에 대해 거짓이었다. 후보가 붙는 2,813개 중 927개(33%)가
    이랬다.

    이제는 품사를 **먼저 고르고** 그 품사를 가진 후보만 모은다. 그래서 `quickly`
    (부사)는 목록에서 빠지고 이름표는 남은 것들에 대해 참이 된다.
    """
    _seed(
        db,
        [
            {"word": "americano", "example": "I want an americano.", "topic": "cafe"},
            {"word": "quickly", "example": "Please come quickly.", "topic": "cafe"},
        ],
    )
    with db.db_session() as session:
        alts = practice.alternatives_for(
            session, _item(word="bitter", example="Can I get a bitter coffee?", topic="cafe")
        )
    assert alts is not None
    assert alts.label_ko == "같은 장면(카페)에서 쓰는 명사예요."
    assert {a.word for a in alts.words} == {"americano"}
    claimed = {label for label in lexicon.POS_KO.values() if label in alts.label_ko}
    for word in alts.words:
        assert claimed <= set(word.pos_ko), f"{word.word} 에 대해 이름표가 거짓이에요"


@needs_lexicon
def test_the_label_names_every_part_of_speech_the_whole_list_really_shares(db):
    """구성원 전부가 명사이자 동사면 둘 다 말해도 된다. 그건 과장이 아니다."""
    _seed(
        db,
        [
            {"word": "drink", "example": "I drink water.", "rank": 101},
            {"word": "cook", "example": "I cook dinner.", "rank": 102},
        ],
    )
    with db.db_session() as session:
        alts = practice.alternatives_for(
            session, _item(word="water", example="I want water.", topic=None, rank=100)
        )
    assert alts is not None
    assert {a.word for a in alts.words} == {"drink", "cook"}
    assert alts.label_ko == "비슷하게 자주 쓰는 명사·동사예요."


@needs_lexicon
def test_the_same_item_always_gets_the_same_list(db):
    """목록은 낱말로 섞는다 — 문제마다 달라야 하지만 시험은 흔들리면 안 된다."""
    _seed(
        db,
        [{"word": "cookie", "example": "Can I have a cookie?", "topic": "cafe"}]
        + [
            {"word": w, "example": f"I want a {w}.", "topic": "cafe"}
            for w in ("scone", "mug", "muffin", "cake", "spoon", "cup", "plate", "napkin")
        ],
    )
    with db.db_session() as session:
        first = practice.alternatives_for(session, _item(topic="cafe"))
        second = practice.alternatives_for(session, _item(topic="cafe"))
    assert first is not None and second is not None
    assert [a.word for a in first.words] == [a.word for a in second.words]


# --- API ------------------------------------------------------------------------


@pytest.fixture()
def client(db):
    """임시 DB 를 붙인 API. `db` 픽스처가 먼저 모듈을 리로드해야 한다."""
    from fastapi.testclient import TestClient

    import app.main as main

    return TestClient(main.app)


def _cafe(db):
    _seed(
        db,
        [
            {
                "word": "cookie",
                "example": "Can I have a cookie with my coffee?",
                "example_ko": "커피에 곁들여 쿠키 하나 주세요.",
                "meaning_ko": "쿠키 (과자)",
                "topic": "cafe",
                "pattern": "a/the + cookie",
            },
            {
                "word": "scone",
                "example": "Can I have a scone with my tea?",
                "meaning_ko": "스콘",
                "topic": "cafe",
            },
            {
                "word": "and",
                "example": "I like coffee and tea.",
                "meaning_ko": "그리고",
                "topic": "cafe",
            },
        ],
    )


@needs_lexicon
def test_the_answer_never_leaves_the_server_with_the_question(client, db):
    """빈칸 문제의 성질 그 자체다. 정답이 payload 에 실리면 빈칸 문제가 아니다."""
    _cafe(db)
    items = client.get("/cloze", params={"topic": "cafe", "count": 10}).json()
    cookie = next(i for i in items if i["word"] == "cookie")
    assert "answer" not in cookie
    assert "cookie" not in cookie["sentence"]
    # 해석은 가리지 않고 그대로 준다 — 그게 이 화면의 결정이다.
    assert cookie["example_ko"] == "커피에 곁들여 쿠키 하나 주세요."
    assert cookie["pos_hint"]["text_ko"] == "여기엔 명사가 들어가요."


@needs_lexicon
def test_a_function_word_blank_goes_out_without_a_hint(client, db):
    """`and` 의 품사를 모른다. 힌트를 지어내지 않고 빼고 낸다."""
    _cafe(db)
    items = client.get("/cloze", params={"topic": "cafe", "count": 10}).json()
    conj = next(i for i in items if i["word"] == "and")
    assert conj["pos_hint"] is None


@needs_lexicon
def test_answering_returns_the_lesson_not_just_a_score(client, db):
    _cafe(db)
    body = client.post("/cloze/answer", json={"word": "cookie", "said": "quickly"}).json()
    assert body["verdict"] == "wrong_pos" and body["ok"] is False
    assert body["said_pos"] == ["r"]
    card = body["explain"]
    assert card["example"] == "Can I have a cookie with my coffee?"
    assert card["pos_text_ko"] == "이 낱말은 명사예요."
    # 승인 전 설명은 별도 상자에만 있다.
    assert card["usage_note"] is None
    assert card["unverified"]["note_ko"]
    assert {a["word"] for a in card["alternatives"]["words"]} == {"scone"}


@needs_lexicon
def test_a_phrase_answer_is_graded_on_its_head(client, db):
    _cafe(db)
    body = client.post("/cloze/answer", json={"word": "cookie", "said": "a cookie"}).json()
    assert body["verdict"] == "correct" and body["head"] == "cookie"


@needs_lexicon
def test_the_old_shape_of_the_answer_response_is_still_there(client, db):
    """예전 화면은 이 다섯 칸만 읽는다. 늘어난 칸은 전부 기본값이 있어야 한다."""
    _cafe(db)
    body = client.post(
        "/cloze/answer", json={"word": "cookie", "said": "cookie", "explain": False}
    ).json()
    assert set(body) >= {"verdict", "ok", "said", "answer", "message_ko"}
    assert body["explain"] is None


@needs_lexicon
def test_an_item_with_no_gloss_still_goes_out(client, db):
    """해석은 792/3,245 만 채워져 있다. 없다고 출제가 막히면 앱이 3/4 를 잃는다."""
    _cafe(db)
    items = client.get("/cloze", params={"topic": "cafe", "count": 10}).json()
    scone = next(i for i in items if i["word"] == "scone")
    assert scone["example_ko"] is None
    body = client.post("/cloze/answer", json={"word": "scone", "said": "cookie"}).json()
    assert body["explain"]["example_ko"] is None
    assert body["verdict"] == "right_pos"


# --- 정답이 새지 않는가 ----------------------------------------------------------
#
# 빈칸을 뚫어 놓고 옆 칸으로 답을 흘리면 빈칸 문제가 아니다. 실제로 흘리고 있었다 —
# 서빙 가능한 2,950개 중 2,882개(97.7%)의 pattern 이 표제어를 그대로 담고 있었다.


@pytest.mark.parametrize(
    "pattern,masked",
    [
        ("a/the + cookie", f"a/the + {BLANK}"),
        ("borrow + 목적어 (+ from + 사람)", f"{BLANK} + 목적어 (+ from + 사람)"),
        ("V + -ing", "V + -ing"),  # 표제어가 없으면 그대로 둔다
    ],
)
def test_the_pattern_no_longer_carries_the_answer(pattern, masked):
    from app.tutor.cloze import mask_answer

    word = "borrow" if "borrow" in pattern else "cookie"
    assert mask_answer(pattern, word) == masked


def test_masking_an_inflected_mention_too():
    """빈칸을 뚫을 때와 **같은 함수**로 가린다. 어긋나면 한쪽이 답을 흘린다."""
    from app.tutor.cloze import mask_answer

    assert mask_answer("복수형은 cookies 예요", "cookie") == f"복수형은 {BLANK} 예요"


def test_masking_leaves_text_without_the_answer_alone():
    from app.tutor.cloze import mask_answer

    assert mask_answer("한국어만 있는 설명", "cookie") == "한국어만 있는 설명"
    assert mask_answer(None, "cookie") is None


@needs_lexicon
def test_the_question_payload_hides_the_answer_everywhere_it_can(client, db):
    _cafe(db)
    cookie = next(
        i for i in client.get("/cloze", params={"topic": "cafe", "count": 10}).json()
        if i["word"] == "cookie"
    )
    for field in ("sentence", "pattern", "meaning_ko", "example_ko"):
        assert "cookie" not in (cookie[field] or "")
    assert cookie["pattern"] == f"a/the + {BLANK}"


@needs_lexicon
def test_the_explanation_shows_the_pattern_unmasked(client, db):
    """답을 본 뒤라 가릴 이유가 없다. 오히려 여기서 문형을 온전히 봐야 배운다."""
    _cafe(db)
    card = client.post("/cloze/answer", json={"word": "cookie", "said": "cookie"}).json()["explain"]
    assert card["pattern"] == "a/the + cookie"
    assert card["example"] == "Can I have a cookie with my coffee?"


# --- 화면이 기대는 성질 두 가지 ------------------------------------------------
#
# 아래 둘은 단어 연습장 **화면**(ui_web) 이 기대는 것이라 여기서 못박는다.
# 서버 쪽 리팩터링이 조용히 깨뜨리면 증상이 화면에서만 나타나고, 그때는
# "장면 이름이 영어로 나온다"·"카페 팩에 문제가 여덟 개뿐이다" 로 보여서
# 원인을 서버에서 찾기 어렵다.


def test_topics_carry_the_korean_name(client, db):
    """화면이 {"cafe": "카페"} 를 따로 들고 있지 않게 서버가 이름을 준다."""
    _cafe(db)
    topics = client.get("/cloze/topics").json()
    cafe = next(t for t in topics if t["topic"] == "cafe")
    assert cafe["label_ko"] == "카페"


def test_an_unknown_topic_falls_back_to_its_own_name(db):
    """새 팩이 들어와도 화면이 안 깨진다 — 빈 이름을 내보내지 않는다."""
    assert practice.topic_ko("brandnewpack") == "brandnewpack"


@needs_lexicon
def test_an_empty_level_serves_every_level(client, db):
    """`level=""` 은 "레벨로 안 가른다" 다. 장면 팩을 통째로 낼 때 쓴다.

    팩은 그 자리에서 쓰는 말을 모은 것이지 난이도로 묶은 것이 아니다. A1 으로
    자르면 카페 60개가 8개로 줄어 연습이 성립하지 않는다.
    """
    _seed(
        db,
        [
            {"word": "cookie", "level": "A1", "example": "Can I have a cookie?", "topic": "cafe"},
            {"word": "scone", "level": "B1", "example": "Can I have a scone?", "topic": "cafe"},
        ],
    )
    words = {i["word"] for i in client.get("/cloze", params={"topic": "cafe", "level": ""}).json()}
    assert words == {"cookie", "scone"}

    only_a1 = {
        i["word"] for i in client.get("/cloze", params={"topic": "cafe", "level": "A1"}).json()
    }
    assert only_a1 == {"cookie"}
