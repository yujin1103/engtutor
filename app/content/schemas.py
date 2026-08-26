"""단어 콘텐츠 스키마.

`reviewed` 는 사람이 정하는 값이라 LLM 출력 스키마에 넣지 않는다.
배치 생성은 항상 reviewed=false 로 저장되고, 검수 UI 에서만 켜진다.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..tutor.korean import (
    normalize,
    reject_english_run,
    reject_foreign_script,
    reject_hangul,
    reject_untranslated_latin,
    require_korean,
)

WordLevel = Literal["A1", "A2", "B1"]

# words.pattern 컬럼 폭. 이걸 넘기면 저장 시점에 잘려 문형이 깨진다.
MAX_PATTERN_CHARS = 120

# 모델이 문형을 `enjoy + -ing` 처럼 코드 표기로 감싸 내놓는 일이 잦다.
# 학습자에게는 그대로 보이는 값이라 껍데기만 벗긴다.
_PATTERN_WRAPPER = re.compile(r"^[`'\"*\s]+|[`'\"*\s]+$")


def clean_pattern(value: str) -> str:
    """문형 표기를 한 줄로 정리한다. 뜻이 아니라 형태를 적는 칸이다."""
    text = _PATTERN_WRAPPER.sub("", " ".join(value.split()))
    if not text:
        raise ValueError("pattern 이 비어 있습니다. 이 단어가 문장에서 취하는 형태를 적어야 합니다.")
    if len(text) > MAX_PATTERN_CHARS:
        raise ValueError(
            f"pattern 이 {len(text)}자입니다. {MAX_PATTERN_CHARS}자 이내의 형태 표기여야 합니다"
            f"(설명은 usage_note 에 씁니다): {text[:60]!r}"
        )
    return text


# 예문 해석 칸의 상한. 8단어 이내 영어 문장의 해석은 보통 40자 안쪽이라 두 배로 잡았다.
# 목표와 하드캡을 같은 값으로 두면 멀쩡한 출력이 검증에 걸려 재시도를 부른다(korean.py 참고).
MAX_EXAMPLE_KO_CHARS = 80

# 괄호 안 보충 설명과 문장부호. 해석과 낱말 뜻을 견줄 때 이것들 때문에 다르게 보이면 안 된다.
_PARENTHETICAL = re.compile(r"\([^)]*\)")
# 활용이 끝난 한국어 문장의 종결. 사전형('빌리다')이나 맨 명사('음료')와 구분하려고 쓴다.
_ENDS_LIKE_A_SENTENCE = re.compile(r"(?:요|죠|까|네|군|니다)[.!?…~]*$")

_TRIVIA = re.compile(r"[\s.,!?~…·'\"]+")


def clean_gloss(value: str) -> str:
    """예문 해석을 한 줄로 정리하고 한국어인지 확인한다."""
    text = " ".join(value.split())
    if not text:
        raise ValueError("example_ko 가 비어 있습니다. 예문 그 문장의 한국어 해석을 적어야 합니다.")
    if len(text) > MAX_EXAMPLE_KO_CHARS:
        raise ValueError(
            f"example_ko 가 {len(text)}자입니다. {MAX_EXAMPLE_KO_CHARS}자 이내의 한 문장이어야 "
            f"합니다(설명이 아니라 해석입니다): {text[:60]!r}"
        )
    text = normalize(text)
    text = reject_english_run(text, "example_ko")
    text = reject_untranslated_latin(text, "example_ko")
    text = reject_foreign_script(text, "example_ko")
    return require_korean(text, "example_ko")


class ExampleGloss(BaseModel):
    """예문 한 문장의 한국어 해석. 칸이 하나뿐인 스키마다.

    왜 WordEntry 에 넣지 않고 따로 두는가
    ------------------------------------
    1. 해석은 **이미 저장된 예문**을 옮긴 것이어야 한다. WordEntry 에 넣으면 채우는
       길이 항목 전체 재생성뿐인데, 그러면 예문 자체가 바뀐다 — 학습자가 풀던 빈칸
       문장이 해석을 붙이는 김에 다른 문장이 돼 버린다.
    2. 이 저장소에는 프롬프트를 넓게 고쳤다가 스키마 실패가 0% -> 62% 가 된 기록이
       있다. 이미 도는 6칸짜리 생성에 한국어 칸을 하나 더 얹는 것은 그 방향이다.
       칸 하나짜리 번역 요청은 실패할 자리가 그만큼 적다.

    새로 만드는 단어는 해석 없이 저장되고, 이 백필 모드가 나중에 주워 간다.
    어차피 3,245개 중 일부만 채워진 채로 오래 갈 칸이라 앱이 빈 칸을 견디게 돼 있다.
    """

    model_config = ConfigDict(extra="forbid")

    example_ko: str = Field(
        description=(
            "The Korean translation of the EXAMPLE SENTENCE — what a Korean person would "
            "actually say in that situation. Not the meaning of the headword. One line, "
            "natural spoken Korean (해요체), no English."
        )
    )

    _clean = field_validator("example_ko")(clean_gloss)


def _bare(text: str) -> str:
    """괄호와 문장부호를 걷어낸 알맹이. 두 한국어 문자열이 사실상 같은지 보려고 쓴다."""
    return _TRIVIA.sub("", _PARENTHETICAL.sub(" ", text))


def reject_word_meaning(gloss: str, *, example: str, meaning_ko: str) -> str:
    """해석이 **문장 뜻**인지 확인한다. 낱말 뜻이면 거부한다.

    모델에게 표제어·낱말 뜻·예문을 함께 주면 meaning_ko 를 그대로 베껴 오기 쉽다.
    'Can I borrow your pen?' 의 해석 자리에 '빌리다' 가 들어오면 연습장이 성립하지
    않는다 — 학습자는 그 문장이 무슨 말인지 알아야 구나 절로도 답할 수 있다.

    pydantic 검증이 아니라 함수인 이유: 판단에 example 과 meaning_ko 가 필요한데
    둘 다 모델이 생성하는 값이 아니라 DB 에 이미 있는 값이다. 실패 문구는 생성기의
    재시도 경로가 그대로 모델에게 돌려준다.
    """
    if _bare(gloss) and _bare(gloss) == _bare(meaning_ko):
        raise ValueError(
            f"낱말 뜻(meaning_ko)을 그대로 옮겼습니다: {gloss!r}. "
            f"필요한 것은 문장 {example!r} 의 해석입니다."
        )
    # 한 어절짜리 해석은 대개 낱말 뜻이다 — '빌리다', '음료'. 다만 한 어절이어도
    # 활용이 끝난 문장이면 멀쩡한 해석이다: "It's the tenth floor." 의 해석은
    # '10층입니다' 한 어절이고, 'Thank you.' 는 '고마워요' 한 어절이다.
    #
    # 처음에는 어절 수만 봤다가 444개 백필에서 tenth·eleventh·twelfth 세 개를
    # 헛되이 떨어뜨렸다. 그래서 종결어미까지 본다 — 낱말 뜻은 사전형('-다')이나
    # 맨 명사로 끝나고, 문장은 '-요/-니다/-까' 로 끝난다.
    if len(gloss.split()) == 1 and len(example.split()) >= 3 and not _ENDS_LIKE_A_SENTENCE.search(
        gloss
    ):
        raise ValueError(
            f"해석이 낱말 뜻처럼 끝나는 한 어절입니다: {gloss!r}. "
            f"{len(example.split())}단어짜리 문장 {example!r} 을 통째로 옮겨야 합니다."
        )
    return gloss


# ---------------------------------------------------------------- 해석이 그 문장의 해석인가
#
# 아래 두 검사는 `reject_word_meaning` 과 같은 자리에 선다 — 스키마 검증이 아니라
# 함수인 이유도 같다. 판정에 `example`·`meaning_ko` 처럼 **DB 에 이미 있는 값**이
# 필요한데, 그건 모델이 이번에 생성한 값이 아니다.
#
# 왜 필요한가
# -----------
# 연습장에서 해석은 장식이 아니라 **과제 지시문**이다. 빈칸이 된 낱말의 뜻이 해석에서
# 통째로 사라지면 학습자는 해석대로 답하고 오답 처리된다. 빈도 상위 35개 중 3개가
# 그랬다: `Remember to call your mom.` -> '엄마께 전화하세요'(기억이 없다),
# `I have forty books.` -> '서른다섯 권의 책'(수가 틀렸다).

# 수사·서수·달 이름. 값이 정해져 있어 **해석에 그 수가 있는지**를 따질 수 있다.
# 해석 검사 중 여기서만은 참·거짓이 갈린다 — 40 을 '서른다섯'으로 옮긴 것은 문체의
# 문제가 아니라 틀린 사실이다.
_CARDINALS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}
_ORDINALS: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30,
}
# 달 이름은 **예문 안쪽에 대문자로 쓰였을 때만** 달로 본다. `march`(행진)·`may`(조동사)·
# `august`(위엄 있는)는 같은 철자의 다른 낱말이고, 그 예문의 해석에 '3월'이 없는 건
# 결함이 아니다. 문장 첫 글자는 무엇이든 대문자라 근거가 못 된다 — 실제로
# `May I use your phone?` 이 5월로 잡혀 멀쩡한 해석이 걸렸다.
_MONTHS: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

_SINO_DIGIT = ("", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
_NATIVE_UNIT = ("", "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉")
# 뒤에 단위가 붙으면 모양이 바뀐다 — 하나 -> 한 개, 스물 -> 스무 살.
_NATIVE_ATTR = {"하나": "한", "둘": "두", "셋": "세", "넷": "네"}
_NATIVE_TENS = {
    10: "열", 20: "스물", 30: "서른", 40: "마흔", 50: "쉰",
    60: "예순", 70: "일흔", 80: "여든", 90: "아흔",
}


def _sino(n: int) -> str:
    """한자어 수사. 40 -> '사십', 15 -> '십오'."""
    if n >= 1000:
        return "천"
    if n >= 100:
        return "백"
    tens, unit = divmod(n, 10)
    head = "" if tens == 0 else ("십" if tens == 1 else _SINO_DIGIT[tens] + "십")
    return head + _SINO_DIGIT[unit]


def _native(n: int) -> set[str]:
    """고유어 수사와 그 관형형. 40 -> {'마흔'}, 14 -> {'열넷', '열네'}."""
    if n <= 0 or n >= 100:
        return set()
    tens, unit = divmod(n, 10)
    head = _NATIVE_TENS.get(tens * 10, "")
    if tens and not head:
        return set()
    forms = {head + _NATIVE_UNIT[unit]}
    if unit:
        forms.add(head + _NATIVE_ATTR.get(_NATIVE_UNIT[unit], _NATIVE_UNIT[unit]))
    elif tens == 2:
        forms.add("스무")
    return {f for f in forms if f}


def _number_forms(n: int) -> set[str]:
    """이 수가 한국어 해석에 나타날 수 있는 모양 전부."""
    return {str(n), _sino(n)} | _native(n)


def _number_value(word: str, example: str) -> int | None:
    """이 표제어가 가리키는 수. 수사가 아니면 None."""
    w = word.strip().lower()
    if w in _CARDINALS:
        return _CARDINALS[w]
    if w in _ORDINALS:
        return _ORDINALS[w]
    if w in _MONTHS and re.search(rf"(?<!^)(?<![.!?] )\b{w.capitalize()}\b", example):
        return _MONTHS[w]
    return None


def reject_wrong_number(gloss: str, *, word: str, example: str) -> str:
    """표제어가 수사면 해석에 그 수가 있는지 본다. 없으면 거부한다.

    수는 해석에서 유일하게 **참·거짓이 갈리는** 부분이다. 'I have forty books.' 를
    '서른다섯 권의 책'으로 옮기면 학습자는 forty 가 35 라고 배운다. 저장된 792개
    중 실제로 seventeen 이 '일곱 살', forty 가 '서른다섯'이었다.

    아라비아 숫자·한자어·고유어를 다 받는다 — '40'·'사십'·'마흔'이 다 맞는 해석이다.
    100·1000 은 배수로 나타나므로('two hundred' -> '200명') 자릿수로도 본다.
    """
    n = _number_value(word, example)
    if n is None or n == 0:
        return gloss
    forms = _number_forms(n)
    if any(f and f in gloss for f in forms):
        return gloss
    if n in (100, 1000) and re.search(r"\d+" + "0" * (2 if n == 100 else 3), gloss):
        return gloss
    raise ValueError(
        f"해석에 {word!r} 의 수({n})가 없습니다: {gloss!r}. "
        f"{example!r} 의 해석에는 {' 또는 '.join(sorted(forms))} 가 나와야 합니다."
    )


# 한국어 낱말에서 잘라 내는 꼬리. 어미와 조사를 떼어 어간만 남긴다.
_KO_TAIL = (
    "하다", "되다", "이다", "시다", "다", "의", "적", "히", "게", "한", "인", "들",
    "을", "를", "이", "가", "은", "는", "에", "와", "과", "로",
)
_KO_PIECE = re.compile(r"[,;/·\n]|또는|혹은")

_HANGUL_BASE = 0xAC00


def _hangul_only(text: str) -> str:
    return "".join(c for c in text if _HANGUL_BASE <= ord(c) <= 0xD7A3)


def _initial(ch: str) -> int:
    return (ord(ch) - _HANGUL_BASE) // 588


def _medial(ch: str) -> int:
    return ((ord(ch) - _HANGUL_BASE) % 588) // 28


def _stems(korean: str) -> set[str]:
    """한국어 설명에서 뜯어낸 어간 조각들. 해석에서 찾아볼 **흔적**의 목록이다."""
    out: set[str] = set()
    inner = _PARENTHETICAL.findall(korean)
    outer = _PARENTHETICAL.sub(" ", korean)
    for chunk in _KO_PIECE.split(outer) + [p for i in inner for p in _KO_PIECE.split(i)]:
        for token in chunk.split():
            token = _hangul_only(token)
            if not token:
                continue
            forms = {token}
            for tail in _KO_TAIL:
                if token.endswith(tail) and len(token) > len(tail):
                    forms.add(token[: -len(tail)])
            for form in list(forms):
                if len(form) >= 3:
                    forms.add(form[:2])  # '가져가'의 흔적은 '가져다'에도 남는다
            out |= forms
    return out


def _leaves_a_trace(stem: str, condensed: str) -> bool:
    """어간이 해석 안에 (활용된 채로라도) 나타나는가.

    마지막 음절은 초성만 맞아도 같은 낱말로 본다 — 한국어는 어간의 끝이 활용에서
    바뀐다('빌리' -> '빌려', '기억하' -> '기억해'). 한 음절짜리 어간에는 그 완화가
    너무 헐거워서 초성·중성을 다 맞춰 본다('주'는 '줄'과 같고 '자'와는 다르다).

    해석은 공백을 지운 채로 본다. '관심이'의 흔적은 '요리에 관심 있어요'에 있는데,
    공백을 그대로 두면 '관심 있'이 이어진 조각으로 보이지 않는다.
    """
    length = len(stem)
    if not length:
        return False
    for i in range(len(condensed) - length + 1):
        window = condensed[i : i + length]
        if window[:-1] != stem[:-1]:
            continue
        seen, want = window[-1], stem[-1]
        if length == 1:
            if _initial(seen) == _initial(want) and _medial(seen) == _medial(want):
                return True
        elif _initial(seen) == _initial(want):
            return True
    return False


def reject_unrelated_gloss(
    gloss: str, *, word: str, example: str, meaning_ko: str, usage_note: str
) -> str:
    """해석이 표제어와 **완전히 무관한지** 본다. 무관하면 거부한다.

    무엇을 재는가
    -------------
    빈칸이 된 낱말의 뜻이 해석에 어떤 형태로든 남았는지만 본다. 잣대는 그 낱말에
    대해 저장해 둔 한국어 전부 — `meaning_ko` 와 `usage_note` 에서 뜯은 어간 조각이다.

    왜 '맞는 뜻인가'가 아니라 '무관한가'인가
    ----------------------------------------
    `meaning_ko` 는 못 믿는다. 미검수 값이고 실제로 틀린 것이 있다(`ankle` 의 뜻이
    '종아리'로 저장돼 있었다). 그래서 해석이 그 뜻과 **일치하는지**는 물을 수 없다.
    물을 수 있는 건 훨씬 약한 질문이다 — 저장된 한국어 **어느 조각과도** 안 겹치는가.
    'Remember to call your mom.' -> '엄마께 전화하세요' 는 '기억'·'떠올리' 어느 쪽과도
    안 겹친다. 틀린 뜻이 섞여 있어도 이 질문의 답은 잘 변하지 않는다 — 잣대가 하나
    틀렸을 뿐 여러 개이기 때문이다.

    무엇을 판정하지 않는가
    ----------------------
    - 기능어(`let`, `have`, `will`): 뜻이 해석에 낱말로 남지 않는 부류다.
      `lexicon.FUNCTION_WORDS` 는 이미 같은 이유로 품사 힌트를 끄는 데 쓰는 목록이다.
    - 수사: 위의 `reject_wrong_number` 가 더 정확하게 본다.
    이 둘까지 판정하면 재시도가 영원히 성공할 수 없는 요청이 되고, 멀쩡한 해석이
    빈칸으로 남는다.

    한계 — 통과했다고 맞는 해석은 아니다
    ------------------------------------
    뜻이 옆으로 미끄러진 경우는 못 잡는다. 'He controls the company.' 를 '운영해요'로
    옮긴 것은 usage_note 의 어느 조각엔가 걸려 통과한다. 잡는 것은 **흔적이 아예
    없는** 경우뿐이고, 그게 학습자를 다른 답으로 데려가는 경우다.
    """
    from .lexicon import FUNCTION_WORDS  # 순환 참조를 피해 함수 안에서 가져온다

    key = word.strip().lower()
    if key in FUNCTION_WORDS or _number_value(key, example) is not None:
        return gloss
    condensed = _hangul_only(gloss)
    stems = _stems(meaning_ko) | _stems(usage_note)
    if any(_leaves_a_trace(stem, condensed) for stem in stems):
        return gloss
    raise ValueError(
        f"해석에 {word!r} 의 뜻이 남아 있지 않습니다: {gloss!r}. "
        f"{example!r} 은 {word!r} 를 배우는 문장이라, 그 낱말의 뜻이 빠지면 "
        f"학습자가 해석대로 답해도 오답이 됩니다."
    )


class WordEntry(BaseModel):
    """LLM 이 단어 하나에 대해 생성하는 항목."""

    model_config = ConfigDict(extra="forbid")

    word: str = Field(description="The headword, lowercase.")
    level: WordLevel = Field(description="CEFR level where a Korean learner first needs this word.")
    meaning_ko: str = Field(
        description=(
            "Korean meaning. If the word is easy to confuse with another, disambiguate in "
            "parentheses — e.g. '빌리다 (내가 빌려 오는 쪽)'."
        )
    )
    # example 앞에 둔다. 스키마 순서가 곧 생성 순서라, 형태를 먼저 정하고 나면
    # 예문이 그 형태를 따라간다. 반대로 두면 예문을 쓴 뒤 형태를 갖다 붙인다.
    pattern: str = Field(
        description=(
            "The grammatical shape this word takes in a sentence — the FORM, not the meaning. "
            "Write the headword together with what must follow it, in 40 characters or fewer: "
            "'listen to + 목적어', 'enjoy + -ing', 'advice: 불가산명사 (an advice X)'. "
            "Put optional parts in parentheses. This is what Korean beginners get wrong most."
        )
    )
    example: str = Field(
        description="One short example sentence a beginner could actually say. 8 words or fewer."
    )
    usage_note: str = Field(
        description=(
            "One or two Korean sentences on how Koreans typically get this word wrong, "
            "or when to use it instead of a similar word."
        )
    )
    confused_with: list[str] = Field(
        description="English words a Korean learner confuses with this one. Empty list if none."
    )

    # 학습자에게 보이는 한국어 필드는 한글이 있어야 하고, 예문에는 한글이 없어야 한다.
    # 프롬프트로 "한국어로 써라"라고 해도 확률적으로 새어 나간다 — 실제로 NGSL 2,801개
    # 중 calm 의 설명이 통째로 영어로 생성됐다. 스키마에서 거부하면 재시도로 넘어간다.
    _fix_pattern = field_validator("pattern")(clean_pattern)
    _fix_meaning = field_validator("meaning_ko")(lambda v: require_korean(normalize(v), "meaning_ko"))
    _fix_usage = field_validator("usage_note")(lambda v: require_korean(normalize(v), "usage_note"))
    _chk_example = field_validator("example")(lambda v: reject_hangul(v, "example"))

    @field_validator("word")
    @classmethod
    def _lowercase(cls, v: str) -> str:
        return v.strip().lower()

    @model_validator(mode="after")
    def _example_must_use_the_headword(self) -> "WordEntry":
        """예문이 표제어를 실제로 쓰는지 확인한다.

        프롬프트에 "Use the headword in it" 이 있는데도 NGSL 2,801개 중 39개가
        어겼다 — age 의 예문이 'How old are you?', hand 의 예문이
        'Pass me the book, please.' 였다. 표제어를 안 쓰는 예문은 예문이 하는
        유일한 일을 안 하는 것이라, 프롬프트가 아니라 여기서 막는다.

        굴절형은 허용한다(bought, went, arose). 거부되면 생성기의 재시도 경로가
        무엇이 틀렸는지 알려주며 다시 요청한다.
        """
        from .screening import mentions  # 순환 참조를 피해 함수 안에서 가져온다

        if not mentions(self.example, self.word):
            raise ValueError(
                f"예문이 표제어 {self.word!r} 를 쓰지 않습니다: {self.example!r}"
            )
        return self

    @model_validator(mode="after")
    def _drop_self_reference(self) -> "WordEntry":
        """모델이 confused_with 에 표제어 자신을 넣는 일이 있어 코드에서 걸러낸다."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for w in self.confused_with:
            key = w.strip().lower()
            if not key or key == self.word or key in seen:
                continue
            seen.add(key)
            cleaned.append(key)
        self.confused_with = cleaned
        return self


class WordTip(BaseModel):
    """리포트에 붙는 단어 팁. 검수된 항목만 나간다."""

    word: str
    meaning_ko: str
    # 문형은 pattern 이 생기기 전에 만들어진 항목에는 없다. 없으면 안 보여줄 뿐이라
    # 옵셔널로 둔다 — 이것 때문에 리포트가 실패하면 안 된다.
    pattern: str | None = None
    example: str
    usage_note: str
    confused_with: list[str]
