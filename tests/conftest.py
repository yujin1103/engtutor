"""라이브(실제 LLM 호출) 테스트 게이트.

기본 `pytest` 는 오프라인 테스트만 돌려 빠르게 유지하고,
실제 모델 호출이 필요한 보안 슈트는 `--live` 로만 실행한다.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest


@contextmanager
def temporary_database(path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    """임시 SQLite 로 갈아끼웠다가 **원래대로 되돌린다.**

    `app.db.database` 는 임포트 시점에 엔진을 만들므로 DB_PATH 를 바꾸려면
    모듈을 리로드해야 한다. 그런데 monkeypatch 는 모듈 리로드를 되돌리지 못한다 —
    되돌리지 않으면 이후 테스트가 조용히 임시 DB 를 쓴다. 실제로 검수 UI
    렌더링 테스트가 단독 실행에서는 통과하고 전체 실행에서는 실패했다.

    monkeypatch 의 teardown 보다 이 함수의 teardown 이 먼저 돌기 때문에
    환경변수도 직접 원복한 뒤 리로드한다.
    """
    from app import config
    from app.db import database

    original = os.environ.get("DB_PATH")
    monkeypatch.setenv("DB_PATH", str(path))
    config.get_settings.cache_clear()
    importlib.reload(database)
    database.init_db()
    try:
        yield database
    finally:
        if original is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = original
        config.get_settings.cache_clear()
        importlib.reload(database)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="실제 LLM 백엔드를 호출하는 테스트도 실행한다 (느리고 비결정적)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="LLM 호출 테스트 — `--live` 로 실행하세요")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
