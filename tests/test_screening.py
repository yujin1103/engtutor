"""단어 항목 선별기.

승인을 대신하지 않는다 — 검수 순서를 매길 뿐이다. 그래서 오탐은 비용이 낮고
(사람이 한 번 더 볼 뿐) 미탐은 비용이 높다(나쁜 항목이 큐 뒤로 밀린다).
검사는 그 비대칭에 맞춰 느슨한 쪽이 아니라 민감한 쪽으로 맞춘다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.content.schemas import WordEntry
from app.content.screening import (
    mentions,
    risk_score,
    screen,
    screen_all,
    worst_severity,
)


def _row(**over):
    base = {
        "word": "borrow",
        "level": "A1",
        "meaning_ko": "빌리다 (내가 빌려 오는 쪽)",
        "example": "Can I borrow your pen?",
        "usage_note": "빌려주는 쪽은 lend 예요. 방향이 반대예요.",
        "confused_with": ["lend"],
    }
    base.update(over)
    return type("W", (), base)()


def _codes(findings):
    return {f.code for f in findings}


# ------------------------------------------------------------------ 굴절 인식
@pytest.mark.parametrize(
    ("word", "sentence"),
    [
        ("borrow", "Can I borrow your pen?"),
        ("borrow", "I borrowed a book."),
        ("arrange", "I arranged a table for two."),
        ("arrange", "She is arranging the flowers."),
        ("study", "He studied all night."),
        ("buy", "I bought a coffee."),
        ("go", "I went there yesterday."),
        ("child", "The children are playing."),
        ("arise", "A problem arose during the meeting."),
        ("sink", "The boat sank in the storm."),
        ("strike", "The man struck the table."),
    ],
)
def test_inflections_count_as_a_mention(word, sentence):
    assert mentions(sentence, word)


@pytest.mark.parametrize(
    ("word", "sentence"),
    [
        ("age", "How old are you?"),
        ("hand", "Pass me the book, please."),
        ("country", "Where are you from?"),
        ("vegetable", "I like carrots and broccoli."),
        ("arrange", "I arrived at the station."),
    ],
)
def test_a_related_word_is_not_a_mention(word, sentence):
    """실제 NGSL 배치에서 나온 결함들. 여기가 느슨해지면 전부 통과한다."""
    assert not mentions(sentence, word)


# ------------------------------------------------------------------ 단일 항목
def test_a_good_entry_has_no_findings():
    assert screen(_row()) == []


def test_headword_absent_is_high():
    """예문에도 설명에도 표제어가 없으면 다른 단어를 설명한 것이다 (arrange -> arrive)."""
    findings = screen(_row(word="clause", example="Read the contract carefully.", usage_note="계약서에서 자주 나와요."))
    assert "headword_absent" in _codes(findings)
    assert worst_severity(findings) == "high"


def test_example_missing_headword_is_medium_when_the_note_has_it():
    findings = screen(
        _row(word="comfort", example="This chair is very comfortable.", usage_note="comfort 는 명사예요.")
    )
    assert _codes(findings) == {"example_missing_headword"}
    assert worst_severity(findings) == "medium"


def test_non_korean_fields_are_caught():
    findings = screen(_row(usage_note="Koreans often confuse this with lend."))
    assert "usage_not_korean" in _codes(findings)


def test_hangul_in_the_example_is_caught():
    findings = screen(_row(example="Can I borrow your 펜?"))
    assert "example_has_hangul" in _codes(findings)


def test_self_reference_is_caught():
    findings = screen(_row(confused_with=["borrow", "lend"]))
    assert "self_reference" in _codes(findings)


def test_length_outliers_are_low_severity():
    long_example = "I would really like to borrow your pen for just a moment if that is fine"
    findings = screen(_row(example=long_example, usage_note="짧아요"))
    assert {"example_too_long", "usage_note_too_short"} <= _codes(findings)
    assert worst_severity(findings) == "low"


def test_bad_level_is_flagged():
    assert "bad_level" in _codes(screen(_row(level="Z9")))


# ------------------------------------------------------------------ 항목 간 비교
def test_duplicate_notes_are_only_visible_across_entries():
    """few-shot 이나 앞 항목을 베끼는 실패는 한 항목만 봐서는 안 보인다."""
    shared = "빌려주는 쪽은 lend 예요. 방향이 반대예요."
    rows = [
        _row(word="borrow", usage_note=shared),
        _row(word="lend", example="Can you lend me a pen?", usage_note=shared),
    ]
    assert screen(rows[0]) == []  # 따로 보면 멀쩡하다

    findings = screen_all(rows)
    assert "duplicate_usage_note" in _codes(findings["borrow"])
    assert "duplicate_usage_note" in _codes(findings["lend"])


def test_duplicate_examples_are_caught():
    rows = [
        _row(word="borrow", example="Can I borrow your pen?"),
        _row(word="pen", example="Can I borrow your pen?", usage_note="필기구를 말해요."),
    ]
    findings = screen_all(rows)
    assert "duplicate_example" in _codes(findings["pen"])


def test_risk_score_puts_high_first():
    """high 하나가 low 여러 개보다 항상 앞서야 큐 정렬이 뒤집히지 않는다."""
    high = screen(_row(word="clause", example="Read the contract.", usage_note="계약서요."))
    low = screen(_row(usage_note="짧아요"))
    assert risk_score(high) > risk_score(low)


# ------------------------------------------------------------------ 생성 시점 차단
def test_schema_rejects_an_example_without_the_headword():
    """선별은 사후 진단이다. 생성 시점에 막으면 재시도가 알아서 고친다."""
    with pytest.raises(ValidationError, match="예문"):
        WordEntry(
            word="age",
            level="A1",
            meaning_ko="나이",
            example="How old are you?",
            usage_note="나이를 물을 때 써요.",
            confused_with=[],
        )


def test_schema_rejects_an_english_usage_note():
    """NGSL 배치에서 calm 의 설명이 통째로 영어로 생성됐다."""
    with pytest.raises(ValidationError, match="한국어"):
        WordEntry(
            word="calm",
            level="A2",
            meaning_ko="차분한",
            example="Stay calm and think carefully.",
            usage_note="Korean learners confuse 'calm' with 'quiet'.",
            confused_with=["quiet"],
        )


def test_schema_rejects_hangul_in_the_example():
    with pytest.raises(ValidationError, match="영어"):
        WordEntry(
            word="borrow",
            level="A1",
            meaning_ko="빌리다",
            example="Can I borrow your 펜?",
            usage_note="빌려주는 쪽은 lend 예요.",
            confused_with=[],
        )


def test_schema_still_accepts_an_inflected_example():
    entry = WordEntry(
        word="buy",
        level="A1",
        meaning_ko="사다",
        example="I bought a coffee.",
        usage_note="값을 치르고 얻는 걸 말해요. 빌리는 건 borrow 예요.",
        confused_with=["borrow"],
    )
    assert entry.example == "I bought a coffee."


def test_schema_allows_punctuation_the_english_whitelist_would_reject():
    """예문에 따옴표나 콜론이 정당하게 들어갈 수 있어 화이트리스트를 걸지 않는다."""
    entry = WordEntry(
        word="say",
        level="A1",
        meaning_ko="말하다",
        example='He said, "Hello!"',
        usage_note="상대를 밝힐 때는 tell 을 써요.",
        confused_with=["tell"],
    )
    assert '"' in entry.example


@pytest.mark.parametrize("value", ["lend", "he'll", "can't", "driver's license", "in fact", "well-known"])
def test_plain_english_words_are_accepted_in_confused_with(value):
    """아포스트로피와 하이픈은 정당한 영어다. 여기서 막으면 오탐만 늘어난다."""
    assert "confused_with_malformed" not in _codes(screen(_row(confused_with=[value])))


@pytest.mark.parametrize("value", ["chip (as in 'a piece')", "dear (money)", "+", "miss (name)"])
def test_glosses_and_symbols_are_flagged_in_confused_with(value):
    """헷갈리는 '단어' 자리에 해설이 들어간 경우. 실제 NGSL 배치에서 14건 나왔다."""
    assert "confused_with_malformed" in _codes(screen(_row(confused_with=[value])))


def test_a_shared_example_is_only_a_low_priority_glance():
    """"I am a student." 는 be·i·student 세 표제어 모두에 정당한 예문이다.

    여기를 high 로 두면 멀쩡한 항목 25개가 검수 큐 맨 앞을 차지한다 —
    실제로 그렇게 만들었다가 되돌렸다.
    """
    rows = [
        _row(word="student", example="I am a student.", usage_note="학생을 말해요."),
        _row(word="be", example="I am a student.", usage_note="영어는 '이다'를 동사로 써요."),
    ]
    findings = screen_all(rows)
    assert worst_severity(findings["be"]) == "low"


def test_a_shared_usage_note_stays_high():
    """설명은 단어마다 경고 지점이 달라 겹칠 이유가 없다. 겹치면 베낀 것이다."""
    shared = "빌려주는 쪽은 lend 예요. 방향이 반대예요."
    rows = [
        _row(word="borrow", usage_note=shared),
        _row(word="lend", example="Can you lend me a pen?", usage_note=shared),
    ]
    assert worst_severity(screen_all(rows)["lend"]) == "high"
