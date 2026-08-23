"""세션 저장소.

1단계는 인메모리다. 2단계에서 SQLite 구현체로 갈아끼우되
Protocol 만 지키면 /chat 코드는 건드리지 않는다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from .llm.base import Message


@dataclass
class Session:
    id: str
    scenario_id: str
    level: str
    messages: list[Message] = field(default_factory=list)


class SessionStore(Protocol):
    def create(self, *, scenario_id: str, level: str) -> Session: ...

    def get(self, session_id: str) -> Session | None: ...

    def append(self, session_id: str, message: Message) -> None: ...


class InMemorySessionStore:
    """프로세스가 죽으면 사라진다. 1단계에서만 쓴다."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, *, scenario_id: str, level: str) -> Session:
        session = Session(id=uuid.uuid4().hex, scenario_id=scenario_id, level=level)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def append(self, session_id: str, message: Message) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.messages.append(message)
