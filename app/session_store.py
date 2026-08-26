"""세션 저장소.

Protocol 을 지키는 구현이 두 개다.
- InMemorySessionStore : 테스트·DB 없이 돌릴 때
- SqliteSessionStore   : 실제 실행 경로 (2단계부터)

/chat 코드는 어느 쪽인지 몰라도 되게 유지한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from .content.schemas import WordTip
from .llm.base import Message
from .tutor.schemas import Correction, TurnResponse


@dataclass
class Session:
    id: str
    scenario_id: str
    level: str
    messages: list[Message] = field(default_factory=list)
    ended: bool = False


class SessionStore(Protocol):
    def create(self, *, scenario_id: str, level: str) -> Session: ...

    def get(self, session_id: str) -> Session | None: ...

    def set_level(self, session_id: str, level: str) -> None: ...

    def record_turn(
        self,
        session_id: str,
        *,
        user_text: str,
        turn: TurnResponse,
        input_mode: str = "text",
        transcript: str | None = None,
        transcript_words: list[dict] | None = None,
    ) -> None: ...

    def corrections(self, session_id: str) -> list[Correction]: ...

    def word_tips(self, corrections: list[Correction]) -> list[WordTip]: ...

    def end(self, session_id: str) -> None: ...


class InMemorySessionStore:
    """프로세스가 죽으면 사라진다. 테스트용."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._corrections: dict[str, list[Correction]] = {}

    def create(self, *, scenario_id: str, level: str) -> Session:
        session = Session(id=uuid.uuid4().hex, scenario_id=scenario_id, level=level)
        self._sessions[session.id] = session
        self._corrections[session.id] = []
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def set_level(self, session_id: str, level: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.level = level

    def record_turn(
        self,
        session_id: str,
        *,
        user_text: str,
        turn: TurnResponse,
        input_mode: str = "text",
        transcript: str | None = None,
        transcript_words: list[dict] | None = None,
    ) -> None:
        # 인메모리 저장소는 전사를 보관하지 않는다 — 대화 흐름만 재현하면 되고,
        # 전사는 나중에 되돌아볼 때 값이 있는 것이라 영속 저장소의 몫이다.
        session = self._sessions[session_id]
        session.messages.append({"role": "user", "content": user_text})
        session.messages.append({"role": "assistant", "content": turn.reply})
        self._corrections[session_id].extend(turn.corrections)

    def corrections(self, session_id: str) -> list[Correction]:
        return list(self._corrections.get(session_id, []))

    def word_tips(self, corrections: list[Correction]) -> list[WordTip]:
        return []  # 인메모리 저장소에는 단어 콘텐츠가 없다

    def end(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.ended = True


class SqliteSessionStore:
    """SQLite 영속 저장. 대화 히스토리는 turns 테이블에서 복원한다."""

    def create(self, *, scenario_id: str, level: str) -> Session:
        from .db import crud
        from .db.database import db_session

        session_id = uuid.uuid4().hex
        with db_session() as db:
            crud.create_session(db, session_id=session_id, scenario_id=scenario_id, level=level)
        return Session(id=session_id, scenario_id=scenario_id, level=level)

    def get(self, session_id: str) -> Session | None:
        from .db import crud
        from .db.database import db_session

        with db_session() as db:
            row = crud.get_session(db, session_id)
            if row is None:
                return None
            return Session(
                id=row.id,
                scenario_id=row.scenario_id,
                level=row.level,
                messages=crud.messages_of(row),  # type: ignore[arg-type]
                ended=row.ended_at is not None,
            )

    def set_level(self, session_id: str, level: str) -> None:
        from .db import crud
        from .db.database import db_session

        with db_session() as db:
            crud.set_session_level(db, session_id, level)

    def record_turn(
        self,
        session_id: str,
        *,
        user_text: str,
        turn: TurnResponse,
        input_mode: str = "text",
        transcript: str | None = None,
        transcript_words: list[dict] | None = None,
    ) -> None:
        from .db import crud
        from .db.database import db_session

        with db_session() as db:
            crud.record_turn(
                db,
                session_id=session_id,
                user_text=user_text,
                turn=turn,
                input_mode=input_mode,
                transcript=transcript,
                transcript_words=transcript_words,
            )

    def corrections(self, session_id: str) -> list[Correction]:
        from .db import crud
        from .db.database import db_session

        with db_session() as db:
            return crud.corrections_of(db, session_id)

    def word_tips(self, corrections: list[Correction]) -> list[WordTip]:
        from .db import crud
        from .db.database import db_session

        with db_session() as db:
            return crud.word_tips_for(db, corrections)

    def end(self, session_id: str) -> None:
        from .db import crud
        from .db.database import db_session

        with db_session() as db:
            crud.end_session(db, session_id)
