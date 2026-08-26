"""SQLAlchemy 모델: sessions / turns / corrections.

corrections 는 그 교정을 만들어낸 assistant 턴에 붙는다.
original 에 학습자 원문이 그대로 들어 있어 리포트에서 단독으로 읽을 수 있다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# 트랙 이름. 문자열을 여기저기 흩어 놓으면 오타 하나가 조용히 빈 목록이 된다.
# 'general' = 생활 회화(NGSL + 장면 팩), 'toeic' = TOEIC/비즈니스(TSL + BSL).
TRACK_GENERAL = "general"
TRACK_TOEIC = "toeic"
TRACKS = (TRACK_GENERAL, TRACK_TOEIC)


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
    # 예문 **그 문장**의 한국어 해석. meaning_ko 와 다른 칸이다 — 저쪽은 낱말 뜻이고
    # 이쪽은 문장 뜻이다. "Can I borrow your pen?" 에 대해 meaning_ko 는 '빌리다',
    # example_ko 는 '펜 좀 빌려도 될까요?' 다.
    #
    # 단어 연습장이 빈칸 문장과 함께 이걸 가리지 않고 보여준다. 답이 일부 드러나지만
    # 이건 시험이 아니라 연습장이고, **뜻을 알아야 구나 절로도 답할 수 있다** —
    # '펜 좀 빌려도 될까요?' 를 알면 pen · a pen · your pen 이 다 답이 된다.
    # 뜻을 안 주면 왕초보에게는 과제 자체가 성립하지 않는다.
    #
    # 나중에 생긴 칸이라 3,245개 중 일부만 채워져 있다. 없으면 안 보여줄 뿐이고
    # 이것 때문에 출제가 실패하면 안 된다. 그래서 NULL 을 허용한다.
    example_ko: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_note: Mapped[str] = mapped_column(Text)
    confused_with: Mapped[list[str]] = mapped_column(JSON, default=list)
    # 이 단어의 문형·연어 사실. 예: "V + -ing", "listen to + 목적어", "불가산명사".
    # 뜻이 아니라 **어떻게 쓰이는가**다. 왕초보가 틀리는 건 대부분 여기다 —
    # 생성된 2,801개 중 이걸 짚은 설명이 10개(0.4%)뿐이었다.
    # 구조화해 두면 재생성 때 제약으로 넣고, 선별기가 설명이 이걸 반영했는지 검사할 수 있다.
    pattern: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 빈도 순위(1이 가장 자주 쓰임). 검수 우선순위로 쓴다 — 300개만 검수해도
    # 가장 많이 쓰는 300개를 얻어야 하기 때문이다. 목록 밖 단어는 NULL.
    #
    # **트랙 안에서만 뜻이 있는 값이다.** NGSL 의 1위(`be`)와 TSL 의 1위(`mister`)는
    # 서로 다른 코퍼스에서 나온 순위라 한 줄로 세우면 뜻이 없다. 그래서 순위로
    # 정렬하는 곳은 전부 트랙을 먼저 좁힌다(crud.cloze_candidates).
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 장면 묶음. 'cafe', 'hotel', 'health' … NGSL 일반 어휘는 NULL 이다.
    #
    # 빈도 목록만으로는 카페에서 쓸 말을 모을 수 없다 — NGSL 2,801개에 `americano`
    # 도 `towel` 도 없었다. 그래서 장면별로 묶은 어휘를 따로 넣고, 그 묶음 이름을
    # 여기 남긴다. 검수를 장면 단위로 끊어서 할 수 있고(카페 팩만 승인하고 시연),
    # 빈칸도 그 장면 것만 낼 수 있다. 다른 회화 앱들이 '유닛'이라 부르는 것과 같다.
    topic: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # 어느 트랙의 낱말인가. 'general'(생활 회화) | 'toeic'(TOEIC/비즈니스).
    #
    # 한 통에 섞으면 **카페 주문을 연습하는 왕초보에게 `reimbursement` 가 나온다.**
    # 두 트랙은 어휘도 예문 상황도 다른 물건이라 — 이쪽은 카페·길 묻기, 저쪽은
    # 회의·송장 — 조회하는 쪽이 매번 빼먹지 않고 걸러야 한다. 그래서 기본값을
    # 'general' 로 두고 조회 함수도 기본이 'general' 이다. 새 트랙을 넣는 사람이
    # 실수해도 왕초보 화면은 그대로다.
    #
    # 트랙은 **행을 만들 때 정해지고 배치 재실행이 바꾸지 않는다**(crud.upsert_word).
    # TSL·BSL 에는 이미 생활 회화 트랙에 있는 낱말이 159개 있는데(`airport`,
    # `refund` …), 그것들이 토익 목록에 있다는 이유로 트랙을 옮겨 버리면 카페 팩에
    # 구멍이 난다. 먼저 들어간 트랙이 그 낱말의 트랙이다.
    track: Mapped[str] = mapped_column(
        String(16), default=TRACK_GENERAL, server_default=TRACK_GENERAL, index=True
    )
    # 승인해야 True. 리포트에는 True 인 것만 나간다.
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # 누가 승인했는지. 검수 UI 에서 사람이 누르면 "human", 그 밖에는 모델 이름을
    # 남긴다. 출처를 안 남기면 "검수됨"이 무슨 뜻인지 나중에 알 수 없다.
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
