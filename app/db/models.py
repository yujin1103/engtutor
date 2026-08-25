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
    # 타자면 "text", 음성이면 "voice". 나중에 둘을 갈라서 보려면 지금 남겨야 한다.
    input_mode: Mapped[str] = mapped_column(String(8), default="text")
    # 음성 입력에서 **STT 가 들은 것**. content 는 학습자가 확정한 것이라 다를 수 있고,
    # 그 차이가 이 앱에서 가장 알고 싶은 것이다 — 이 STT 를 믿어도 되는가.
    # 한 칸에만 저장하면 전사도 차이도 함께 사라진다. app/tutor/transcript.py 참고.
    transcript: Mapped[str | None] = mapped_column(Text, default=None)
    # 낱말별 확률: [{"word": "iced", "probability": 0.88}, ...]
    # 화면에 자신 없는 단어를 표시하는 데 쓰고, **확신했는데 학습자가 고친 자리**를
    # 세는 데 쓴다. 후자가 Whisper 가 틀린 영어를 매끄럽게 고친 흔적이다.
    transcript_words: Mapped[list[dict] | None] = mapped_column(JSON, default=None)
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
    # 이 단어의 문형·연어 사실. 예: "V + -ing", "listen to + 목적어", "불가산명사".
    # 뜻이 아니라 **어떻게 쓰이는가**다. 왕초보가 틀리는 건 대부분 여기다 —
    # 생성된 2,801개 중 이걸 짚은 설명이 10개(0.4%)뿐이었다.
    # 구조화해 두면 재생성 때 제약으로 넣고, 선별기가 설명이 이걸 반영했는지 검사할 수 있다.
    pattern: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # NGSL 빈도 순위(1이 가장 자주 쓰임). 검수 우선순위로 쓴다 — 300개만 검수해도
    # 가장 많이 쓰는 300개를 얻어야 하기 때문이다. 목록 밖 단어는 NULL.
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 승인해야 True. 리포트에는 True 인 것만 나간다.
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 누가 승인했는지. 검수 UI 에서 사람이 누르면 "human", 그 밖에는 모델 이름을
    # 남긴다. 출처를 안 남기면 "검수됨"이 무슨 뜻인지 나중에 알 수 없다.
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
