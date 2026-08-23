"""SQLite 엔진과 세션 팩토리.

DB 파일은 bind mount 위(호스트의 E:\\engtutor\\data)에 남는다. 컨테이너를 지워도
검수 결과와 학습 기록이 보존돼야 하기 때문이다.

주의: NTFS -> WSL2 경계(virtiofs)에서는 WAL 모드의 파일 락이 불안정하다.
단일 사용자 토이 규모이므로 기본 journal 모드(DELETE)를 그대로 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from .models import Base


def _make_engine() -> Engine:
    settings = get_settings()
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        # FastAPI 는 요청을 스레드풀에서 처리하므로 스레드 검사를 끈다.
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@contextmanager
def db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
