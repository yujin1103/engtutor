"""단어 콘텐츠 스키마.

`reviewed` 는 사람이 정하는 값이라 LLM 출력 스키마에 넣지 않는다.
배치 생성은 항상 reviewed=false 로 저장되고, 검수 UI 에서만 켜진다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..tutor.korean import normalize

WordLevel = Literal["A1", "A2", "B1"]


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

    _fix_meaning = field_validator("meaning_ko")(lambda v: normalize(v))
    _fix_usage = field_validator("usage_note")(lambda v: normalize(v))

    @field_validator("word")
    @classmethod
    def _lowercase(cls, v: str) -> str:
        return v.strip().lower()

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
    example: str
    usage_note: str
    confused_with: list[str]
