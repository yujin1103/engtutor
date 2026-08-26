"""빈칸 채우기.

가장 중요한 성질은 **새로 만들어지는 정보가 없다**는 것이다. 문제는 검수를 거친
예문에서 표제어를 지워서 얻고, 정답은 지운 그것이다. 그래서 여기 시험의 초점은
"좋은 문제를 만드는가"가 아니라 "**없는 것을 지어내지 않는가**"에 있다.
"""

from __future__ import annotations

import pytest

from app.content import lexicon
from app.tutor.cloze import (
    BLANK,
    ClozeItem,
    grade,
    is_answerable,
    is_safe_to_serve,
    is_speakable,
    make_item,
    normalize,
)

needs_lexicon = pytest.mark.skipif(not lexicon.available(), reason="WordNet 코퍼스가 없습니다")


def _row(**over):
    base = {
        "word": "borrow",
        "level": "A1",
        "meaning_ko": "빌리다 (내가 빌려 오는 쪽)",
        "pattern": "borrow + 목적어",
        "example": "Can I borrow your pen?",
        "usage_note": "빌려주는 쪽은 lend 예요. 방향이 반대예요.",
        "confused_with": ["lend"],
        "rank": 1,
        "reviewed": False,
    }
    base.update(over)
    return type("W", (), base)()


def _item(**over):
    item = make_item(_row(**over))
    assert item is not None
    return item


# --- 빈칸 만들기 --------------------------------------------------------------


def test_the_headword_becomes_the_blank():
    item = _item()
    assert item.sentence == f"Can I {BLANK} your pen?"
    assert item.answer == "borrow"


def test_the_blank_keeps_the_inflected_surface_form():
    """예문이 굴절형을 쓰면 정답도 굴절형이다. 원형을 정답으로 두면 문장이 안 맞는다."""
    item = _item(word="say", example='He said, "I\'m tired."')
    assert item.answer == "said"
    assert BLANK in item.sentence and "said" not in item.sentence


def test_an_example_without_the_headword_makes_no_item():
    """선별기가 이미 지적하는 항목이다. 여기서 억지로 만들 이유가 없다."""
    assert make_item(_row(word="age", example="How old are you?")) is None


def test_an_empty_example_makes_no_item():
    assert make_item(_row(example="")) is None


def test_only_the_first_occurrence_is_blanked():
    item = _item(word="see", example="I see what you see.")
    assert item.sentence.count(BLANK) == 1


# --- 채점: 이 앱이 가르치려는 구분 ---------------------------------------------


def test_the_exact_answer_is_correct():
    assert grade(_item(), "borrow").verdict == "correct"


@pytest.mark.parametrize("said", ["Borrow", "  borrow  ", "borrow."])
def test_case_and_punctuation_do_not_matter(said):
    """음성 전사는 대문자와 마침표를 마음대로 붙인다. 그걸로 틀렸다고 하면 안 된다."""
    assert grade(_item(), said).verdict == "correct"


@needs_lexicon
def test_the_right_word_in_the_wrong_form_is_its_own_verdict():
    """이 프로젝트의 전제 — 왕초보는 뜻이 아니라 형태에서 틀린다.

    `borrowing` 과 `lend` 를 같은 오답으로 묶으면 정작 가르쳐야 할 것을 못 가르친다.
    """
    result = grade(_item(), "borrowing")
    assert result.verdict == "wrong_form"
    assert "borrow" in result.message_ko


@needs_lexicon
def test_a_plural_answer_still_recognizes_the_singular():
    """복수형이 그 자체로 사전 표제어인 단어들(years, minutes, days)에서 놓치던 자리.

    `year` 라고 답한 학습자는 단어를 아는 것이다. 그걸 "다른 단어예요"로 돌려주면
    이 앱이 가르치겠다고 한 것 — 뜻이 아니라 형태 — 을 정작 못 가르친다.
    """
    item = _item(word="year", example="I lived here for two years.")
    assert item.answer == "years"
    result = grade(item, "year")
    assert result.verdict == "wrong_form"


@needs_lexicon
def test_a_different_word_is_a_different_verdict():
    """`lend` 는 예전에 wrong_word 였다. 지금은 품사가 겹치는 것을 따로 본다.

    가른 이유는 tests/test_practice.py 에 있다. 여기서는 **맞음이 아니라는 것**만
    지킨다 — 판정 이름이 늘어도 정답이 아닌 답이 통과하면 안 된다.
    """
    result = grade(_item(), "lend")
    assert not result.ok and result.verdict == "right_pos"


@needs_lexicon
def test_a_word_that_does_not_exist_asks_again_instead_of_failing():
    """음성 입력에서 흔하다. 학습자 잘못인지 마이크 잘못인지 구분해야 다시 말할 기회를 준다."""
    result = grade(_item(), "borrowd")
    assert result.verdict == "not_a_word"


def test_silence_is_not_a_wrong_answer():
    assert grade(_item(), "   ").verdict == "empty"


def test_the_answer_is_always_reported_back():
    for said in ("borrow", "lend", "", "borrowing"):
        assert grade(_item(), said).answer == "borrow"


# --- 내보내도 되는가 ----------------------------------------------------------


def test_a_reviewed_item_is_always_servable():
    """사람이 승인했으면 선별기 지적이 있어도 사람의 판단이 이긴다."""
    assert is_safe_to_serve(_row(reviewed=True, confused_with=["chip (as in 'a piece')"]))


def test_an_unreviewed_item_with_findings_is_held_back():
    assert not is_safe_to_serve(_row(example="How old are you?"))


def test_a_clean_unreviewed_item_is_servable():
    assert is_safe_to_serve(_row())


# --- 문제로 성립하는가 (승인과 무관하게 늘 본다) --------------------------------
#
# 아래 셋은 전부 한 가지를 묻는다: **화면에 그 낱말의 한국어 이름이 남는가.**
# 검사기는 원래도 있었는데 후보 목록에만 걸려 있어서, 같은 값이 문제 본문으로
# 나가는 것은 아무도 안 막고 있었다.


def test_a_meaning_the_learner_cannot_read_is_not_served():
    """출제 가능 2,950개 중 14개의 뜻에 한자·가나·키릴이 섞여 있었다."""
    assert not is_safe_to_serve(
        _row(word="bagel", example="I want a bagel.", meaning_ko="백일(백面包), 빵 종류")
    )
    assert not is_safe_to_serve(
        _row(word="spicy", example="This soup is spicy.", meaning_ko="매운, 辛い")
    )


def test_approval_does_not_make_foreign_script_readable():
    """`reviewed` 는 "내용을 믿을 수 있는가"에 답할 뿐이다.

    사람이 승인해도 왕초보가 `叹气` 를 읽게 되지는 않는다. 그래서 이 문은
    선별기와 달리 승인으로 열리지 않는다.
    """
    assert not is_safe_to_serve(
        _row(word="sigh", example="I heard him sigh.", meaning_ko="叹气하다", reviewed=True)
    )


def test_a_meaning_whose_korean_name_is_masked_away_is_not_served():
    """`turnstile (지하철, 버스 등에 있는 출입문)` 은 가리면 괄호만 남는다.

    괄호 안은 부연이지 그 낱말의 한국어 이름이 아니다. 이름이 사라지면 학습자가
    화면 어디에서도 답을 고를 근거를 볼 수 없다.
    """
    assert not is_safe_to_serve(
        _row(
            word="turnstile",
            example="Where is the turnstile?",
            meaning_ko="turnstile (지하철, 버스 등에 있는 출입문)",
        )
    )


def test_a_meaning_that_keeps_its_korean_name_outside_the_parentheses_still_serves():
    """가려도 되는 것과 안 되는 것을 가른다 — 이름이 괄호 밖에 살아 있으면 문제가 된다."""
    row = _row(
        word="concentration",
        example="I need more concentration on my work.",
        meaning_ko="집중, 중점 (집중력: concentration level)",
        pattern=None,
    )
    assert is_safe_to_serve(row)


def test_a_sentence_that_still_shows_the_answer_is_not_served():
    """`make_item` 은 첫 등장만 지운다. 두 번째 `as` 가 답을 그대로 알려 준다."""
    item = make_item(_row(word="as", example="He is as tall as his father."))
    assert item is not None and item.sentence == f"He is {BLANK} tall as his father."
    assert not is_answerable(item)


def test_a_sentence_that_only_shows_a_related_form_still_serves():
    """정답 표면형만 본다. `mentions` 로 넓게 잡으면 이런 것까지 버린다.

    `The ____ did a magic trick.` 의 정답은 `magician` 이라 `magic` 이 남아 있어도
    답을 알려 주지 않는다. 전수에서 `mentions` 기준 5개, 정확일치 기준 1개였다.
    """
    row = _row(word="magic", example="The magician did a magic trick.", pattern=None)
    item = make_item(row)
    assert item is not None and item.answer == "magician"
    assert is_answerable(item)


def test_an_unreadable_gloss_drops_the_gloss_and_keeps_the_item():
    """해석은 없어도 문제가 성립한다. 뜻과 달리 항목까지 버릴 이유가 없다."""
    row = _row(example_ko="스ープ 한 그릇 주세요.")
    item = make_item(row)
    assert item is not None and item.example_ko is None
    assert is_safe_to_serve(row)


# --- 음성으로 답하기 적당한가 --------------------------------------------------


@pytest.mark.parametrize("word,example", [("borrow", "Can I borrow your pen?"),
                                          ("people", "There are many people here.")])
def test_content_words_are_speakable(word, example):
    assert is_speakable(_item(word=word, example=example))


@pytest.mark.parametrize("word,example", [("and", "I like coffee and tea."),
                                          ("be", "I am a student."),
                                          ("it", "It is a book.")])
def test_function_words_are_not_speakable(word, example):
    """WordNet 에 he(헬륨)·a(비타민 A)·in(인치) 가 있어서 사전 조회로는 못 거른다."""
    assert not is_speakable(_item(word=word, example=example))


def test_normalize_strips_everything_that_is_not_a_word():
    assert normalize("  Borrow, please! ") == "borrow please"


# --- API ----------------------------------------------------------------------


def test_the_answer_never_reaches_the_client():
    """정답이 payload 에 실리면 빈칸 문제가 아니다."""
    assert set(ClozeItem.__dataclass_fields__) >= {"answer", "sentence"}
    from app.main import ClozeOut

    assert "answer" not in ClozeOut.model_fields
