"""세션·턴·교정 읽고 쓰기. SQLAlchemy 의존은 이 파일 밖으로 새지 않게 한다."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import selectinload

from ..tutor.schemas import Correction, TurnResponse
from .models import CorrectionRow, SessionRow, TurnRow


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
    return [Correction(original=r.original, better=r.better, note=r.note) for r in rows]


def end_session(db: DbSession, session_id: str) -> None:
    row = db.get(SessionRow, session_id)
    if row is not None and row.ended_at is None:
        row.ended_at = datetime.now(timezone.utc)
