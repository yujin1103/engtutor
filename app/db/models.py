"""SQLAlchemy 모델: sessions / turns / corrections.

corrections 는 그 교정을 만들어낸 assistant 턴에 붙는다.
original 에 학습자 원문이 그대로 들어 있어 리포트에서 단독으로 읽을 수 있다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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
    better: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text)

    turn: Mapped[TurnRow] = relationship(back_populates="corrections")
