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


# 이미 만들어진 테이블에 나중에 추가된 컬럼. create_all 은 기존 테이블을 바꾸지 않으므로
# 여기서 직접 채워 넣는다. 토이 규모라 Alembic 을 들이는 대신 이 정도로 충분하다.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "corrections": {"kind": "VARCHAR(16) NOT NULL DEFAULT 'mistake'"},
    "words": {
        "rank": "INTEGER",
        "reviewed_by": "VARCHAR(32)",
        "pattern": "VARCHAR(120)",
        "topic": "VARCHAR(32)",
        "example_ko": "TEXT",
        # 기존 3,245개는 전부 생활 회화 트랙이다. NOT NULL DEFAULT 라 ALTER 한 번으로
        # 그렇게 채워진다 — 따로 UPDATE 를 돌릴 필요가 없다.
        "track": "VARCHAR(16) NOT NULL DEFAULT 'general'",
    },
    "turns": {
        "input_mode": "VARCHAR(8) NOT NULL DEFAULT 'text'",
        "transcript": "TEXT",
        "transcript_words": "JSON",
    },
}


def _apply_added_columns() -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if not existing:  # 테이블 자체가 없으면 create_all 이 만든다
                continue
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _apply_added_columns()


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
