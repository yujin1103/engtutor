"""단어 콘텐츠 스키마.

`reviewed` 는 사람이 정하는 값이라 LLM 출력 스키마에 넣지 않는다.
배치 생성은 항상 reviewed=false 로 저장되고, 검수 UI 에서만 켜진다.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..tutor.korean import normalize, reject_hangul, require_korean

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
