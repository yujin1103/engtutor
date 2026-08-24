"""세션·턴·교정 읽고 쓰기. SQLAlchemy 의존은 이 파일 밖으로 새지 않게 한다."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import selectinload

from ..content.schemas import WordEntry, WordTip
from ..tutor.schemas import Correction, TurnResponse
from .models import CorrectionRow, SessionRow, TurnRow, WordRow


def create_session(db: DbSession, *, session_id: str, scenario_id: str, level: str) -> SessionRow:
    row = SessionRow(id=session_id, scenario_id=scenario_id, level=level)
    db.add(row)
    db.flush()
    return row


def get_session(db: DbSession, session_id: str) -> SessionRow | None:
    stmt = (
        select(SessionRow)
        .where(SessionRow.id == session_id)
        .options(selectinload(SessionRow.turns).selectinload(TurnRow.corrections))
    )
    return db.execute(stmt).scalar_one_or_none()


def list_sessions(db: DbSession, *, limit: int = 50) -> list[SessionRow]:
    stmt = select(SessionRow).order_by(SessionRow.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def _next_index(db: DbSession, session_id: str) -> int:
    stmt = select(func.coalesce(func.max(TurnRow.turn_index), -1)).where(
        TurnRow.session_id == session_id
    )
    return int(db.execute(stmt).scalar_one()) + 1


def record_turn(
    db: DbSession, *, session_id: str, user_text: str, turn: TurnResponse
) -> None:
    """사용자 발화와 그에 대한 assistant 응답(+교정)을 한 번에 기록한다."""
    index = _next_index(db, session_id)

    db.add(
        TurnRow(session_id=session_id, turn_index=index, role="user", content=user_text)
    )
    assistant = TurnRow(
        session_id=session_id,
        turn_index=index + 1,
        role="assistant",
        content=turn.reply,
        hint_ko=turn.hint_ko,
    )
    db.add(assistant)
    db.flush()

    for correction in turn.corrections:
        db.add(
            CorrectionRow(
                turn_id=assistant.id,
                original=correction.original,
                kind=correction.kind,
                better=correction.better,
                note=correction.note,
            )
        )


def messages_of(row: SessionRow) -> list[dict[str, str]]:
    """LLM 에 넘길 대화 히스토리 형태로 변환."""
    return [{"role": t.role, "content": t.content} for t in row.turns]


def corrections_of(db: DbSession, session_id: str) -> list[Correction]:
    """세션 전체의 교정을 발생 순서대로."""
    stmt = (
        select(CorrectionRow)
        .join(TurnRow, CorrectionRow.turn_id == TurnRow.id)
        .where(TurnRow.session_id == session_id)
        .order_by(TurnRow.turn_index, CorrectionRow.id)
    )
    rows = db.execute(stmt).scalars()
    return [
        Correction(original=r.original, kind=r.kind, better=r.better, note=r.note)
        for r in rows
    ]


def end_session(db: DbSession, session_id: str) -> None:
    row = db.get(SessionRow, session_id)
    if row is not None and row.ended_at is None:
        row.ended_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------- 단어 콘텐츠

_TOKEN = re.compile(r"[a-z][a-z']*")


def tokenize(text: str) -> set[str]:
    """교정 문장에서 단어를 뽑는다. 매칭용이라 단순 소문자 토큰이면 충분하다."""
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 2}


def existing_words(db: DbSession) -> set[str]:
    """이미 생성된 단어. 배치를 다시 돌려도 중복 생성하지 않기 위해."""
    return set(db.execute(select(WordRow.word)).scalars())


def upsert_word(db: DbSession, entry: WordEntry) -> WordRow:
    """배치 생성 결과 저장. 항상 reviewed=False 로 들어간다.

    이미 있는 단어를 다시 생성하면 내용만 갱신하되, 사람이 이미 승인한 항목은
    건드리지 않는다(검수 결과를 배치가 덮어쓰면 안 된다).
    """
    row = db.execute(select(WordRow).where(WordRow.word == entry.word)).scalar_one_or_none()
    if row is None:
        row = WordRow(word=entry.word, reviewed=False)
        db.add(row)
    elif row.reviewed:
        return row

    row.level = entry.level
    row.meaning_ko = entry.meaning_ko
    row.example = entry.example
    row.usage_note = entry.usage_note
    row.confused_with = entry.confused_with
    db.flush()
    return row


def list_words(
    db: DbSession,
    *,
    reviewed: bool | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[WordRow]:
    stmt = select(WordRow)
    if reviewed is not None:
        stmt = stmt.where(WordRow.reviewed.is_(reviewed))
    if query:
        like = f"%{query.strip().lower()}%"
        stmt = stmt.where(or_(WordRow.word.like(like), WordRow.meaning_ko.like(like)))
    stmt = stmt.order_by(WordRow.reviewed, WordRow.word).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())


def assign_ranks(db: DbSession, words: list[str]) -> int:
    """단어 목록의 순서를 빈도 순위로 기록한다. 1이 가장 자주 쓰이는 단어.

    NGSL 은 파일에 등장하는 순서가 곧 빈도 순서인데, 그 정보가 저장 시점에
    사라지고 있었다. 검수를 중간에 멈춰도 **가장 많이 쓰는 단어부터** 승인돼
    있어야 리포트에 실제로 도움이 된다.
    """
    ranks = {}
    for i, word in enumerate(words, start=1):
        ranks.setdefault(word.strip().lower(), i)

    changed = 0
    for row in db.execute(select(WordRow)).scalars():
        rank = ranks.get(row.word)
        if rank is not None and row.rank != rank:
            row.rank = rank
            changed += 1
    return changed


def count_words(db: DbSession, *, reviewed: bool | None = None) -> int:
    stmt = select(func.count(WordRow.id))
    if reviewed is not None:
        stmt = stmt.where(WordRow.reviewed.is_(reviewed))
    return int(db.execute(stmt).scalar_one())


def save_word_edits(db: DbSession, word_id: int, **fields: object) -> WordRow | None:
    row = db.get(WordRow, word_id)
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    db.flush()
    return row


def word_tips_for(
    db: DbSession, corrections: list[Correction], *, limit: int = 5
) -> list[WordTip]:
    """교정에 등장한 단어 중 검수된 항목만 리포트에 붙인다.

    LLM 을 부르지 않는다 — 대화 경로에서 단어 콘텐츠를 생성하지 않는다는 원칙 때문이다.
    """
    if not corrections:
        return []

    tokens: set[str] = set()
    for c in corrections:
        tokens |= tokenize(c.original)
        tokens |= tokenize(c.better)
    if not tokens:
        return []

    stmt = (
        select(WordRow)
        .where(WordRow.word.in_(tokens), WordRow.reviewed.is_(True))
        .order_by(WordRow.level, WordRow.word)
        .limit(limit)
    )
    return [
        WordTip(
            word=r.word,
            meaning_ko=r.meaning_ko,
            example=r.example,
            usage_note=r.usage_note,
            confused_with=list(r.confused_with or []),
        )
        for r in db.execute(stmt).scalars()
    ]
