"""학습 리포트 스키마.

'틀린 문장 모음'은 DB 에서 그대로 가져온다(LLM 불필요).
LLM 은 '오늘 배운 표현'과 '반복 실수 패턴'만 1회 호출로 생성한다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..tutor.schemas import Correction


class LearnedExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    english: str = Field(description="An English expression the learner practiced today.")
    note_ko: str = Field(description="When to use it, in one short friendly Korean sentence.")


class ReportInsight(BaseModel):
    """LLM 이 생성하는 부분."""

    model_config = ConfigDict(extra="forbid")

    summary_ko: str = Field(
        description="Two or three warm Korean sentences summarizing how the session went."
    )
    patterns_ko: list[str] = Field(
        description="Repeated mistake patterns, each one short Korean sentence. Empty list if none."
    )
    learned: list[LearnedExpression] = Field(
        description="Up to 5 expressions worth remembering from this session."
    )


class SessionReport(BaseModel):
    """API 응답 전체."""

    session_id: str
    scenario_title: str
    level: str
    turn_count: int
    mistake_count: int  # kind == "mistake" — 실제 오류
    polish_count: int  # kind == "polish"  — 통하지만 더 자연스러운 표현
    mistakes: list[Correction]  # 두 등급 모두, kind 를 달고 그대로
    insight: ReportInsight
