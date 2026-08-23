"""라이브(실제 LLM 호출) 테스트 게이트.

기본 `pytest` 는 오프라인 테스트만 돌려 빠르게 유지하고,
실제 모델 호출이 필요한 보안 슈트는 `--live` 로만 실행한다.
"""

from __future__ import annotations

import pytest


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
