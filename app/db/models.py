"""SQLAlchemy 모델: sessions / turns / corrections.

corrections 는 그 교정을 만들어낸 assistant 턴에 붙는다.
original 에 학습자 원문이 그대로 들어 있어 리포트에서 단독으로 읽을 수 있다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    turns: Mapped[list["TurnRow"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="TurnRow.turn_index",
    )


class TurnRow(Base):
    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    hint_ko: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[SessionRow] = relationship(back_populates="turns")
    corrections: Mapped[list["CorrectionRow"]] = relationship(
        back_populates="turn", cascade="all, delete-orphan"
    )


class CorrectionRow(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    turn_id: Mapped[int] = mapped_column(
        ForeignKey("turns.id", ondelete="CASCADE"), index=True
    )
    original: Mapped[str] = mapped_column(Text)
    # mistake = 실제 오류 / polish = 통하지만 더 자연스러운 표현
    kind: Mapped[str] = mapped_column(String(16), default="mistake", server_default="mistake")
    better: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text)

    turn: Mapped[TurnRow] = relationship(back_populates="corrections")


class WordRow(Base):
    """사전 생성 + 사람 검수된 단어 콘텐츠.

    실시간 대화 경로에서는 절대 LLM 으로 만들지 않는다. 여기서 조회만 한다.
    """

    __tablename__ = "words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    level: Mapped[str] = mapped_column(String(8), index=True)
    meaning_ko: Mapped[str] = mapped_column(Text)
    example: Mapped[str] = mapped_column(Text)
    usage_note: Mapped[str] = mapped_column(Text)
    confused_with: Mapped[list[str]] = mapped_column(JSON, default=list)
    # NGSL 빈도 순위(1이 가장 자주 쓰임). 검수 우선순위로 쓴다 — 300개만 검수해도
    # 가장 많이 쓰는 300개를 얻어야 하기 때문이다. 목록 밖 단어는 NULL.
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 사람이 승인해야 True. 리포트에는 True 인 것만 나간다.
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
