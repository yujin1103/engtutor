"""예문의 한국어 해석(example_ko) — 낱말 뜻이 아니라 **문장 뜻**.

단어 연습장은 빈칸 문장 옆에 해석을 가리지 않고 보여준다. 답이 일부 드러나지만
이건 시험이 아니라 연습장이고, 뜻을 알아야 구나 절로도 답할 수 있다 —
'펜 좀 빌려도 될까요?' 를 알면 pen · a pen · your pen 이 다 답이 된다.

그래서 이 칸의 시험은 세 가지에 집중한다.
1. 들어온 것이 **문장 해석인가** (낱말 뜻을 베껴 온 것이 아닌가).
2. 학습자가 **읽을 수 있는 한국어인가** (영어 원문·한자·키릴 문자가 안 섞였는가).
3. 해석이 **그 빈칸의 답을 가리키는가** — 빈칸이 된 낱말의 뜻이 해석에서 사라지면
   학습자는 해석대로 답하고 오답 처리된다. 해석은 장식이 아니라 과제 지시문이다.
그리고 하나 더 — 3,245개 중 일부만 채워질 칸이라 **비어 있어도 앱이 죽지 않아야 한다.**
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.content.generator import GlossGenerator, GlossTask
from app.content.schemas import (
    ExampleGloss,
    WordEntry,
    reject_unrelated_gloss,
    reject_word_meaning,
    reject_wrong_number,
)
from app.tutor.schemas import json_schema_for

from .conftest import temporary_database

PEN = "Can I borrow your pen?"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    with temporary_database(tmp_path / "gloss.db", monkeypatch) as database:
        yield database


def _entry(word: str = "borrow", **over) -> WordEntry:
    base = {
        "word": word,
        "level": "A1",
        "meaning_ko": "빌리다 (내가 빌려 오는 쪽)",
        "pattern": "borrow + 목적어 (+ from + 사람)",
        "example": PEN,
        "usage_note": "빌려주는 쪽은 lend 예요.",
        "confused_with": ["lend"],
    }
    base.update(over)
    return WordEntry(**base)


# ---------------------------------------------------------------- 문장 뜻인가
def test_the_word_meaning_copied_over_is_rejected():
    """모델에게 낱말 뜻이 보이면 그대로 베껴 온다. 그러면 연습장이 성립하지 않는다."""
    with pytest.raises(ValueError, match="낱말 뜻"):
        reject_word_meaning(
            "빌리다 (내가 빌려 오는 쪽)", example=PEN, meaning_ko="빌리다 (내가 빌려 오는 쪽)"
        )


def test_the_meaning_stripped_of_its_parenthetical_is_still_a_copy():
    """'빌리다 (내가 빌려 오는 쪽)' 에서 괄호만 떼어 낸 답도 낱말 뜻이다."""
    with pytest.raises(ValueError, match="낱말 뜻"):
        reject_word_meaning("빌리다", example=PEN, meaning_ko="빌리다 (내가 빌려 오는 쪽)")


def test_a_bare_dictionary_form_is_rejected():
    """사전형 한 어절은 문장 해석일 수 없다. 뜻과 글자가 달라도 마찬가지다."""
    with pytest.raises(ValueError, match="한 어절"):
        reject_word_meaning("대여하다", example=PEN, meaning_ko="빌리다")


def test_a_one_word_gloss_passes_when_the_sentence_is_short():
    """'Thank you.' 의 해석은 '고마워요' 한 어절이 정답이다."""
    assert reject_word_meaning("고마워요", example="Thank you.", meaning_ko="고맙다")


def test_a_one_word_gloss_that_ends_like_a_sentence_passes():
    """어절 수만 보다가 tenth·eleventh·twelfth 를 헛되이 떨어뜨린 적이 있다.

    "It's the tenth floor." 의 해석 '10층입니다' 는 한 어절이지만 멀쩡한 문장이다.
    낱말 뜻은 사전형이나 맨 명사로 끝나고, 문장은 '-요/-니다/-까' 로 끝난다.
    """
    assert reject_word_meaning("10층입니다.", example="It's the tenth floor.", meaning_ko="열 번째")


def test_the_real_translation_passes():
    assert reject_word_meaning(
        "펜 좀 빌려도 될까요?", example=PEN, meaning_ko="빌리다 (내가 빌려 오는 쪽)"
    )


# ---------------------------------------------------------------- 읽을 수 있는가
def test_a_gloss_must_be_korean():
    with pytest.raises(ValidationError):
        ExampleGloss(example_ko="Can I borrow your pen?")


def test_the_english_original_pasted_beside_the_gloss_is_rejected():
    """원문을 덧붙이는 실패가 잦다. 해석 칸은 원문을 가린 채로도 쓸 수 있어야 한다."""
    with pytest.raises(ValidationError):
        ExampleGloss(example_ko="펜 좀 빌려도 될까요? (Can I borrow your pen?)")


def test_a_single_english_token_is_allowed():
    """'Wi-Fi 비밀번호가 뭐예요?' 는 정상이다. 한국 사람이 실제로 로마자로 쓰는 말이다.

    무엇이 남아도 되는지는 목록이 정한다 — 아래 '옮기다 만 영어가 남았는가' 를 보라.
    """
    assert ExampleGloss(example_ko="Wi-Fi 비밀번호가 뭐예요?").example_ko


@pytest.mark.parametrize(
    "gloss",
    [
        "저는 견과류에 알레르기体质입니다.",  # 한자
        "스ープ 한 그릇 주세요.",  # 가나
        "약사가 мне 약을 주었어요.",  # 키릴
    ],
)
def test_scripts_the_learner_cannot_read_are_rejected(gloss: str):
    """전부 실제로 생성된 값이다. 왕초보는 이 글자들을 못 읽는다."""
    with pytest.raises(ValidationError):
        ExampleGloss(example_ko=gloss)


def test_a_gloss_is_collapsed_to_one_line():
    assert ExampleGloss(example_ko="  펜 좀\n  빌려도 될까요?  ").example_ko == "펜 좀 빌려도 될까요?"


def test_a_gloss_that_turned_into_an_explanation_is_rejected():
    """해석 칸이지 설명 칸이 아니다. 길면 화면에서도 한 문장으로 안 읽힌다."""
    with pytest.raises(ValidationError):
        ExampleGloss(example_ko="펜을 빌릴 때 쓰는 표현인데요 " * 6)


def test_the_gloss_schema_has_exactly_one_field():
    """칸이 하나뿐이라 실패할 자리도 그만큼 적다 — 이 스키마를 따로 둔 이유다."""
    assert list(json_schema_for(ExampleGloss)["properties"]) == ["example_ko"]


# ------------------------------------------------- 옮기다 만 영어가 남았는가
@pytest.mark.parametrize(
    "gloss",
    [
        "크로issant와 커피 하나 주세요.",  # croissant
        "나oodles를 먹을 때 저는 젓가락을 사용해요.",  # noodles
        "turnstile은 어디에 있어요?",
    ],
)
def test_a_half_translated_word_is_rejected(gloss: str):
    """전부 실제로 저장돼 있던 값이다.

    로마자 두 낱말 이상만 보던 검사는 이걸 다 통과시켰다 — 한글에 붙어 버려서
    공백이 없기 때문이다. 학습자는 `크로issant` 를 읽을 수 없다.
    """
    with pytest.raises(ValidationError):
        ExampleGloss(example_ko=gloss)


@pytest.mark.parametrize(
    "gloss",
    [
        "Wi-Fi 비밀번호가 뭐예요?",
        "ATM에서 동전을 바꿔 주세요.",
        "이 삼각형의 밑변은 5cm입니다.",
    ],
)
def test_latin_that_koreans_actually_write_survives(gloss: str):
    """한국어 문장에 로마자로 적히는 말은 닫힌 부류다 — 단위, 두문자어, Wi-Fi.

    반쯤 옮긴 낱말을 막겠다고 이것까지 죽이면 멀쩡한 해석이 재시도로 사라진다.
    """
    assert ExampleGloss(example_ko=gloss).example_ko


# ------------------------------------------------------------ 수가 맞는가
def test_a_gloss_with_the_wrong_number_is_rejected():
    """'I have forty books.' 가 '서른다섯 권'이면 학습자는 forty 를 35 로 배운다."""
    with pytest.raises(ValueError, match="수\\(40\\)"):
        reject_wrong_number(
            "저는 서른다섯 권의 책을 가지고 있어요.", word="forty", example="I have forty books."
        )


@pytest.mark.parametrize(
    "gloss",
    ["저는 책이 40권 있어요.", "저는 사십 권의 책이 있어요.", "저는 책 마흔 권이 있어요."],
)
def test_arabic_sino_and_native_numerals_all_count(gloss: str):
    """'40'·'사십'·'마흔'은 다 맞는 해석이다. 하나만 받으면 멀쩡한 해석이 걸린다."""
    assert reject_wrong_number(gloss, word="forty", example="I have forty books.")


def test_a_numeral_that_changes_shape_before_a_unit_counts():
    """고유어 수사는 단위 앞에서 모양이 바뀐다 — 열넷 -> 열네 권, 스물 -> 스무 살."""
    assert reject_wrong_number(
        "저는 열네 권의 책이 있어요.", word="fourteen", example="I have fourteen books."
    )
    assert reject_wrong_number("저는 스무 살이에요.", word="twenty", example="I am twenty years old.")


def test_hundred_and_thousand_are_read_as_place_values():
    """'two hundred' 의 해석은 '200명'이다. 100 이라는 글자는 나오지 않는다."""
    assert reject_wrong_number(
        "학생이 200명 있어요.", word="hundred", example="There are two hundred students."
    )


def test_a_month_name_at_the_start_of_a_sentence_is_not_a_month():
    """`May I use your phone?` 의 May 는 5월이 아니다. 문장 첫 글자는 무엇이든 대문자다.

    이걸 안 가리면 멀쩡한 해석('전화기 좀 써도 될까요?')이 5월이 없다고 걸린다.
    """
    assert reject_wrong_number(
        "전화기 좀 써도 될까요?", word="may", example="May I use your phone?"
    )
    with pytest.raises(ValueError, match="수\\(5\\)"):
        reject_wrong_number("제 생일은 유월이에요.", word="may", example="My birthday is in May.")


# ------------------------------------------------- 해석이 빈칸의 답을 가리키는가
def _unrelated(gloss: str, **over) -> str:
    base = {
        "word": "remember",
        "example": "Remember to call your mom.",
        "meaning_ko": "기억하다, 떠올리다",
        "usage_note": "잊지 않고 무엇을 한다는 뜻이에요.",
    }
    base.update(over)
    return reject_unrelated_gloss(gloss, **base)


def test_a_gloss_that_drops_the_headword_is_rejected():
    """빈도 상위 35개 중 3개가 이랬다. 해석대로 답한 학습자가 오답 처리된다 —
    '엄마께 전화하세요' 를 읽고 `please` 를 넣으면 wrong_word 다."""
    with pytest.raises(ValueError, match="뜻이 남아 있지 않습니다"):
        _unrelated("엄마께 전화하세요.")


def test_the_headword_left_in_a_conjugated_form_is_still_a_trace():
    """'기억하다'의 흔적은 '기억하세요'에도 '기억해요'에도 있다. 어간 끝은 활용에서 바뀐다."""
    assert _unrelated("엄마한테 전화하는 거 기억하세요.")
    assert _unrelated("엄마한테 전화하는 거 기억해요.")


def test_a_trace_split_by_a_particle_and_a_space_still_counts():
    """'관심이 되다'의 흔적은 '요리에 관심 있어요'에 있다. 공백을 지운 채로 본다."""
    assert reject_unrelated_gloss(
        "요리에 관심 있어요.",
        word="interest",
        example="I'm interested in cooking.",
        meaning_ko="관심이 되다, 흥미를 느끼다",
        usage_note="관심을 가진다는 뜻이에요.",
    )


def test_the_usage_note_is_a_yardstick_too():
    """뜻은 대개 한두 개만 적혀 있다. 노트까지 잣대로 삼지 않으면 멀쩡한 해석이 걸린다 —
    저장된 792개 기준으로 걸리는 것이 200개에서 59개로 줄었다.

    아래는 실제로 저장돼 있던 값이다. 뜻은 '보고 있다'라고만 적혀 있어서 '봅니다'와
    글자가 안 맞지만, 노트에 '보다'가 있다.
    """
    assert reject_unrelated_gloss(
        "저는 저녁에 항상 텔레비전을 봅니다.",
        word="watch",
        example="I watch TV every evening.",
        meaning_ko="보고 있다, 시청하다",
        usage_note="영화나 드라마를 보는 경우는 'see'보다 'watch'가 자연스러워요.",
    )


def test_a_function_word_is_not_judged():
    """`let`·`have` 의 뜻은 해석에 낱말로 남지 않는다. 판정하면 재시도가 영원히
    성공할 수 없는 요청이 되고, 멀쩡한 해석이 빈칸으로 남는다."""
    assert reject_unrelated_gloss(
        "도와드릴게요.",
        word="let",
        example="Let me help you.",
        meaning_ko="허락하다",
        usage_note="상대에게 무엇을 하게 해 준다는 뜻이에요.",
    )


def test_a_numeral_is_left_to_the_number_check():
    """수사는 `reject_wrong_number` 가 더 정확하게 본다. 두 검사가 같은 것을
    두 번 판정하면, 뜻 흔적 쪽의 헐거운 판정이 먼저 걸려 이유가 흐려진다."""
    assert reject_unrelated_gloss(
        "저는 열일곱 살이에요.",
        word="seventeen",
        example="I am seventeen years old.",
        meaning_ko="일곱 번째 열 (17)",
        usage_note="나이를 말할 때 써요.",
    )


# ---------------------------------------------------------------- 생성
class _FakeClient:
    name = "fake"

    def __init__(self, *payloads):
        self._queue = list(payloads)
        self.sent: list[list[dict]] = []
        self.calls = 0

    def describe(self):
        return "fake"

    def ping(self):
        return True

    def chat_json(self, **kwargs):
        self.calls += 1
        self.sent.append(kwargs.get("messages"))
        # 다 떨어지면 마지막 응답을 반복한다 (재시도 횟수를 시험이 강제하지 않도록)
        payload = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        return dict(payload)


def _task() -> GlossTask:
    return GlossTask(word="borrow", meaning_ko="빌리다 (내가 빌려 오는 쪽)", example=PEN)


def test_gloss_one_returns_the_translation():
    result = GlossGenerator(_FakeClient({"example_ko": "펜 좀 빌려도 될까요?"})).gloss_one(_task())
    assert result.ok and result.example_ko == "펜 좀 빌려도 될까요?"


def test_the_stored_word_meaning_is_never_sent_to_the_model():
    """뜻을 주면 **틀린 뜻이 해석으로 번진다.**

    `ankle` 의 뜻이 '종아리'로 저장돼 있으니 'My ankle hurts a lot.' 이
    '종아리가 많이 아파요'가 됐다(2회 중 2회). 뜻을 빼자 '발목'이 됐다.
    뜻은 판정에만 쓰고 프롬프트에는 넣지 않는다.
    """
    client = _FakeClient({"example_ko": "펜 좀 빌려도 될까요?"})
    GlossGenerator(client).gloss_one(_task())

    prompt = client.sent[0][0]["content"]
    assert "borrow" in prompt and PEN in prompt
    assert "빌리다" not in prompt


def test_a_gloss_that_is_only_the_word_meaning_fails():
    client = _FakeClient({"example_ko": "빌리다"})
    result = GlossGenerator(client).gloss_one(_task())
    assert not result.ok
    assert client.calls == 3  # 온도를 낮추며 두 번 재시도


def test_the_gloss_retry_tells_the_model_what_went_wrong():
    """같은 요청을 그대로 반복하면 대개 똑같이 실패한다. 기존 재시도 규약 그대로다."""
    client = _FakeClient({"example_ko": "빌리다"})
    GlossGenerator(client).gloss_one(_task())

    assert len(client.sent[0]) == 1, "1차 요청에는 수리 지시문이 붙지 않는다"
    note = client.sent[1][-1]["content"]
    assert "낱말 뜻" in note, "무엇을 잘못했는지가 안 들어갔다"
    assert PEN in note, "옮겨야 할 문장을 다시 못 박아야 한다"


def test_the_gloss_recovers_on_retry():
    client = _FakeClient({"example_ko": "빌리다"}, {"example_ko": "펜 좀 빌려도 될까요?"})
    result = GlossGenerator(client).gloss_one(_task())
    assert result.ok and client.calls == 2


def test_a_gloss_with_the_wrong_number_goes_back_for_a_retry():
    """수가 틀린 해석은 저장하지 않고 다시 묻는다. 40 을 '서른다섯'으로 옮긴 판이 있었다."""
    task = GlossTask("forty", "사십 (40)", "I have forty books.", "수를 셀 때 써요.")
    client = _FakeClient({"example_ko": "저는 서른다섯 권의 책이 있어요."},
                         {"example_ko": "저는 책이 40권 있어요."})
    result = GlossGenerator(client).gloss_one(task)
    assert result.ok and result.example_ko == "저는 책이 40권 있어요."
    assert "수(40)" in client.sent[1][-1]["content"], "무엇이 틀렸는지가 재시도에 들어가야 한다"


def test_a_gloss_that_drops_the_word_goes_back_for_a_retry():
    """'Remember to call your mom.' 이 '엄마께 전화하세요' 면 학습자가 답할 낱말이 사라진다."""
    task = GlossTask("remember", "기억하다, 떠올리다", "Remember to call your mom.",
                     "잊지 않는다는 뜻이에요.")
    client = _FakeClient({"example_ko": "엄마께 전화하세요."},
                         {"example_ko": "엄마한테 전화하는 거 기억하세요."})
    result = GlossGenerator(client).gloss_one(task)
    assert result.ok and "기억" in result.example_ko


def test_the_usage_note_is_never_sent_to_the_model_either():
    """노트도 판정용이다. 프롬프트에 넣으면 틀린 노트가 해석으로 번진다 —
    뜻을 빼는 것과 같은 이유다(`ankle` -> '종아리')."""
    client = _FakeClient({"example_ko": "펜 좀 빌려도 될까요?"})
    GlossGenerator(client).gloss_one(
        GlossTask("borrow", "빌리다", PEN, "빌려주는 쪽은 lend 예요.")
    )
    prompt = client.sent[0][0]["content"]
    assert "lend" not in prompt and "빌려주는" not in prompt


def test_gloss_many_preserves_order():
    client = _FakeClient({"example_ko": "펜 좀 빌려도 될까요?"})
    tasks = [_task(), GlossTask("lend", "빌려주다", "Can you lend me a pen?")]
    assert [r.word for r in GlossGenerator(client).gloss_many(tasks, concurrency=2)] == [
        "borrow",
        "lend",
    ]


# ---------------------------------------------------------------- 저장
def test_the_column_is_added_to_an_existing_database(db):
    """이미 만들어진 DB 에 ALTER 가 돌아야 한다. create_all 은 기존 테이블을 안 바꾼다."""
    from sqlalchemy import text

    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE words DROP COLUMN example_ko"))
        assert "example_ko" not in {r[1] for r in conn.execute(text("PRAGMA table_info(words)"))}

    db._apply_added_columns()

    with db.engine.begin() as conn:
        assert "example_ko" in {r[1] for r in conn.execute(text("PRAGMA table_info(words)"))}


def test_a_freshly_generated_entry_has_no_gloss_yet(db):
    """해석은 나중에 백필로 붙는다. 생성 스키마에는 없다 — 없다고 죽으면 안 된다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
    with db.db_session() as s:
        assert crud.list_words(s)[0].example_ko is None


def test_scene_words_come_first_in_the_backfill_queue(db):
    """연습장이 장면으로 문제를 고른다. 빈도 상위 60개의 해석은 그 화면에 안 나온다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry(word="the", meaning_ko="그", example="Pass me the pen."))
        crud.assign_ranks(s, ["the"])
        crud.upsert_word(
            s,
            _entry(word="latte", meaning_ko="라떼", example="I'll have a latte, please."),
            topic="cafe",
        )

    with db.db_session() as s:
        assert crud.words_missing_example_ko(s) == ["latte", "the"]


def test_the_backfill_can_be_limited_to_one_scene(db):
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(
            s, _entry(word="latte", example="I'll have a latte, please."), topic="cafe"
        )
        crud.upsert_word(s, _entry(word="towel", example="Can I get a towel?"), topic="hotel")

    with db.db_session() as s:
        assert crud.words_missing_example_ko(s, topic="cafe") == ["latte"]


def test_approved_entries_are_left_alone(db):
    """--missing-pattern 과 같은 규칙이다. 배치가 검수 결과를 덮어쓰지 않는다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
        crud.save_word_edits(s, crud.list_words(s)[0].id, reviewed=True)

    with db.db_session() as s:
        assert crud.words_missing_example_ko(s) == []
        assert crud.set_example_ko(s, "borrow", "펜 좀 빌려도 될까요?") is False
        assert crud.list_words(s)[0].example_ko is None


def test_set_example_ko_touches_only_that_column(db):
    """해석을 붙이려다 예문이 바뀌면 학습자가 풀던 빈칸 문장이 딴것이 된다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
    with db.db_session() as s:
        assert crud.set_example_ko(s, "borrow", "펜 좀 빌려도 될까요?") is True

    with db.db_session() as s:
        row = crud.list_words(s)[0]
        assert row.example_ko == "펜 좀 빌려도 될까요?"
        assert row.example == PEN
        assert row.meaning_ko == "빌리다 (내가 빌려 오는 쪽)"
        assert row.reviewed is False


def test_word_examples_gives_the_generator_three_plain_fields(db):
    """세션에 매인 행을 스레드로 넘기지 않는다. 필요한 칸만 미리 뜬다."""
    from app.db import crud

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
    with db.db_session() as s:
        assert crud.word_examples(s, ["borrow", "없는단어"]) == [
            ("borrow", "빌리다 (내가 빌려 오는 쪽)", PEN)
        ]


# ---------------------------------------------------------------- 비어 있어도 산다
def test_a_cloze_item_is_made_from_an_entry_without_a_gloss(db):
    """3,245개 중 일부만 채워진다. 해석이 없다고 출제가 실패하면 안 된다."""
    from app.db import crud
    from app.tutor.cloze import make_item

    with db.db_session() as s:
        crud.upsert_word(s, _entry())
        row = crud.cloze_candidates(s)[0]
        assert row.example_ko is None
        item = make_item(row)

    assert item is not None and item.answer == "borrow"
