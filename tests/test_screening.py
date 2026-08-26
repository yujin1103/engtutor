"""단어 항목 선별기.

승인을 대신하지 않는다 — 검수 순서를 매길 뿐이다. 그래서 오탐은 비용이 낮고
(사람이 한 번 더 볼 뿐) 미탐은 비용이 높다(나쁜 항목이 큐 뒤로 밀린다).
검사는 그 비대칭에 맞춰 느슨한 쪽이 아니라 민감한 쪽으로 맞춘다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.content import lexicon
from app.content.schemas import WordEntry
from app.content.screening import (
    mentions,
    pattern_forms,
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
        "pattern": "borrow + 목적어 (+ from + 사람)",
        "example": "Can I borrow your pen?",
        "usage_note": "빌려주는 쪽은 lend 예요. 방향이 반대예요.",
        "confused_with": ["lend"],
    }
    base.update(over)
    # 표제어만 바꿔 다른 검사를 보려는 시험이 많다. 문형은 표제어를 따라가야
    # (borrow 의 문형이 comfort 항목에 남지 않게) 그 시험들이 문형 지적에 오염되지 않는다.
    if "word" in over and "pattern" not in over:
        base["pattern"] = f"{over['word']} + 목적어"
    return type("W", (), base)()


def _codes(findings):
    return {f.code for f in findings}


def test_a_meaning_that_mixes_foreign_script_is_flagged_even_though_it_has_hangul():
    """한글이 하나라도 있으면 통과였다 — `bagel` 의 뜻이 `백일(백面包)` 로 저장돼 있다.

    검수 큐가 이것들을 맨 앞으로 올려야 하고, 미검수 항목은 이 지적 때문에
    출제 문(`cloze.is_safe_to_serve`)을 통과하지 못한다.
    """
    codes = _codes(screen(_row(word="bagel", meaning_ko="백일(백面包), 빵 종류")))
    assert "meaning_foreign_script" in codes
    assert "meaning_not_korean" not in codes  # 한글은 있다. 그게 예전에 통과한 이유다
    assert "meaning_foreign_script" not in _codes(screen(_row()))


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
        ("freeze", "The water froze in the fridge."),
        ("hide", "She hid the key under the mat."),
        ("bite", "The dog bit my hand."),
    ],
)
def test_an_irregular_the_table_never_learned_still_counts(word, sentence):
    """손으로 적은 불규칙 표는 언제나 모자란다.

    `freeze` 가 빠져 있어서 멀쩡한 예문이 "표제어를 안 쓴다"고 **거부됐다** —
    문형 백필에서 세 번 연속 같은 이유로 떨어졌다. 어간이 바뀌는 형태는
    앞부분 대조로도 못 잡으므로, 사전이 아는 것은 사전에게 묻는다.
    """
    if not lexicon.available():
        pytest.skip("WordNet 코퍼스가 없습니다")
    assert mentions(sentence, word)


@pytest.mark.parametrize(
    ("word", "sentence"),
    [
        ("dine-in", "I want to order a coffee for dine-in."),
        ("check-in", "What time is check in?"),
        ("take-out", "Is this takeout or for here?"),
        ("carry-on", "I have one carry-on bag."),
    ],
)
def test_a_hyphenated_headword_is_found_in_the_example(word, sentence):
    """토큰으로 쪼개면 `dine-in` 이 dine 과 in 이 돼 영원히 안 잡힌다.

    실제로 예문에 그대로 들어 있는데 "표제어를 안 쓴다"고 거부됐다. 붙여 쓰거나
    띄어 쓴 형태도 같은 말로 본다 — 학습자가 보는 것은 같은 말이다.
    """
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


def test_a_made_up_headword_is_caught():
    """NGSL 전수 조사에서 `restaurate`·`habor`·`oranje` 13건이 나왔다.

    그때는 던져 쓰는 스크립트로 잡았는데, 그러면 다음 배치에서 또 들어온다.
    """
    if not lexicon.available():
        pytest.skip("WordNet 코퍼스가 없습니다")
    found = _codes(
        screen(_row(word="habor", example="I saw a habor.", usage_note="항구를 뜻해요."))
    )
    assert "headword_not_in_dictionary" in found


def test_a_word_the_dictionary_verified_is_not_called_made_up():
    """`americano` 는 WordNet 에 없지만 확인해서 등록한 말이다. 여기서 걸리면 안 된다."""
    if "americano" not in lexicon.extra_lexicon():
        pytest.skip("추가 사전에 아직 없습니다")
    found = _codes(
        screen(
            _row(
                word="americano",
                meaning_ko="아메리카노 (물을 넣은 커피)",
                pattern="an/the + americano",
                example="I'll have an americano, please.",
                usage_note="에스프레소에 물을 넣은 커피예요. 그냥 coffee 라고 하면 다른 걸 줄 수 있어요.",
                confused_with=["espresso"],
            )
        )
    )
    assert "headword_not_in_dictionary" not in found


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


# ------------------------------------------------------------------ 문형(pattern)
def test_example_must_actually_show_the_pattern():
    """문형이 `listen to` 인데 예문에 to 가 없으면, 예문이 하려던 일을 안 한 것이다.

    왕초보가 틀리는 지점이 정확히 이 전치사다. 문형만 맞고 예문이 다르면
    학습자는 예문을 따라 말하면서 틀린 형태를 익힌다.
    """
    found = _codes(screen(_row(
        word="listen",
        pattern="listen to + 목적어",
        example="I listen music every day.",
        usage_note="음악을 들을 때 써요.",
    )))
    assert "example_ignores_pattern" in found


def test_optional_parts_of_a_pattern_are_not_required():
    """괄호 안은 선택 사항이다. `borrow (+ from + 사람)` 의 from 을 요구하면 오탐이다."""
    assert "example_ignores_pattern" not in _codes(screen(_row()))


def test_alternatives_need_only_one():
    """`arrive at/in + 장소` 는 at 이나 in 중 하나면 된다."""
    found = _codes(screen(_row(
        word="arrive",
        pattern="arrive at/in + 장소",
        example="I arrive at the airport.",
        usage_note="도착지 앞에 at 이나 in 을 붙여요.",
    )))
    assert "example_ignores_pattern" not in found


def test_pattern_check_understands_inflection():
    """be + interested in 의 be 는 예문에서 am 으로 나타난다."""
    found = _codes(screen(_row(
        word="interested",
        pattern="be interested in + 명사",
        example="I am interested in music.",
        usage_note="관심 있는 대상 앞에 in 을 써요. 사람이 주어예요.",
    )))
    assert "example_ignores_pattern" not in found


def test_missing_pattern_is_not_flagged():
    """문형 이전에 생성된 항목이 전부 걸리면 큐 순서가 무의미해진다.

    빈 문형은 사람이 한 줄씩 고칠 일이 아니라 배치가 채울 일이다
    (batch_generate.py --missing-pattern). 그래서 선별기는 지적하지 않는다.
    """
    found = _codes(screen(_row(pattern=None)))
    assert not any(code.startswith("pattern") or code == "example_ignores_pattern" for code in found)


def test_a_definition_in_the_pattern_field_is_flagged():
    """형태를 적는 칸에 설명이 들어오면 문형이 아니다."""
    long_note = (
        "빌리다라는 뜻으로 쓰이고 돌려주는 것을 전제로 하는 동사예요. "
        "반대로 빌려주는 쪽은 lend 를 쓰고, 돈을 내고 빌리는 건 rent 예요."
    )
    assert len(long_note) > 60, "이 시험이 검사하려는 건 길이다"
    assert "pattern_too_long" in _codes(screen(_row(pattern=long_note)))


def test_alternative_forms_need_only_one_to_match():
    """`hope + that + 문장 / hope + to + 동사` 는 둘 중 아무 쪽이나 맞는 예문이다.

    처음엔 슬래시 양쪽을 모두 요구했다가 실제 데이터에서 오탐 10건을 냈다.
    문형은 한 단어가 취할 수 있는 형태들의 목록이지, 전부 동시에 만족할 조건이 아니다.
    """
    found = _codes(screen(_row(
        word="hope",
        pattern="hope + that + 문장 / hope + to + 동사",
        example="I hope to see you again.",
        usage_note="바라는 일을 말할 때 to 나 that 뒤에 붙여요.",
    )))
    assert "example_ignores_pattern" not in found


def test_contractions_count_as_the_word():
    """`be against` 의 be 는 예문에서 `I'm` 으로 나타난다. 이걸 놓쳐 오탐 2건이 났다."""
    found = _codes(screen(_row(
        word="against",
        pattern="be against + 목적어",
        example="I'm against smoking.",
        usage_note="반대하는 대상 앞에 붙여요. 찬성은 for 예요.",
    )))
    assert "example_ignores_pattern" not in found
    assert mentions("I'm against smoking.", "be")
    assert mentions("I can't go there.", "can"), "축약형을 풀면서 원본을 잃으면 안 된다"


def test_placeholder_words_are_not_required_in_the_example():
    """`put + 목적어 + somewhere` 의 somewhere 는 자리 표시어지 찾아야 할 단어가 아니다."""
    found = _codes(screen(_row(
        word="put",
        pattern="put + 목적어 + somewhere",
        example="Put the book on the table.",
        usage_note="놓을 자리를 함께 말해야 자연스러워요.",
    )))
    assert "example_ignores_pattern" not in found


def test_grammar_terms_are_not_required_in_the_example():
    """`that-clause` 의 clause 는 형태를 설명하는 말이다. 예문에서 찾으면 영원히 못 찾는다."""
    assert pattern_forms("to an extent + that-clause", "extent") == [("to", "that")]


def test_a_pattern_from_another_sense_is_still_caught():
    """오탐을 없애면서 진짜까지 놓치면 안 된다.

    `kind` 항목은 문형이 '종류'(kind of), 예문이 '친절한'(a kind person)이라
    두 뜻이 섞여 있다. 이런 건 사람이 봐야 한다.
    """
    found = _codes(screen(_row(
        word="kind",
        pattern="kind + of + 명사",
        example="She is a kind person.",
        usage_note="종류를 말할 때는 kind of 를 써요.",
    )))
    assert "example_ignores_pattern" in found


@pytest.mark.parametrize(
    "pattern,word,expected",
    [
        ("enjoy + -ing", "enjoy", [()]),          # -ing 는 자리 표시지 단어가 아니다
        ("listen to + 목적어", "listen", [("to",)]),
        ("arrive at/in + 장소", "arrive", [("at",), ("in",)]),
        ("borrow + 목적어 (+ from + 사람)", "borrow", [()]),
        ("advice: 불가산명사", "advice", [()]),    # 표제어만 남으면 검사할 게 없다
        ("feel + 형용사 / feel like + 명사", "feel", [(), ("like",)]),
        # 슬래시가 형태를 나누는 게 아니라 자리 표시어 안에 있는 경우.
        # '주제' 를 형태로 세면 요구 없는 형태가 생겨 검사가 무력해진다.
        ("area + of + 장소/주제", "area", [("of",)]),
    ],
)
def test_pattern_forms(pattern, word, expected):
    assert pattern_forms(pattern, word) == expected


# ------------------------------------------------------------------ 생성 시점 차단
def test_schema_rejects_an_example_without_the_headword():
    """선별은 사후 진단이다. 생성 시점에 막으면 재시도가 알아서 고친다."""
    with pytest.raises(ValidationError, match="예문"):
        WordEntry(
            word="age",
            level="A1",
            meaning_ko="나이",
            pattern="age: 셀 수 있는 명사",
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
            pattern="stay/keep calm",
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
            pattern="borrow + 목적어",
            example="Can I borrow your 펜?",
            usage_note="빌려주는 쪽은 lend 예요.",
            confused_with=[],
        )


def test_schema_still_accepts_an_inflected_example():
    entry = WordEntry(
        word="buy",
        level="A1",
        meaning_ko="사다",
        pattern="buy + 목적어 (+ for + 사람)",
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
        pattern="say + 말한 내용",
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
