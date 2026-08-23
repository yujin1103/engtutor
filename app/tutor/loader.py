"""시나리오와 프롬프트 로딩.

시나리오는 코드에 하드코딩하지 않고 YAML 로만 정의한다(추가가 쉬워야 하므로).
프롬프트도 코드와 분리해서 파일로 둔다(튜닝·버전 관리 편의).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
PROMPTS_DIR = Path(__file__).parent / "prompts"

Level = Literal["A1", "A2"]


class Scenario(BaseModel):
    """YAML 한 파일 = 시나리오 하나."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    level: Level = "A1"
    ai_role: str
    situation: str
    goal: str
    opening_line: str
    opening_hint_ko: str


def load_scenarios(directory: Path | None = None) -> dict[str, Scenario]:
    target = directory or SCENARIOS_DIR
    scenarios: dict[str, Scenario] = {}
    for path in sorted(target.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        scenario = Scenario.model_validate(raw)
        if scenario.id != path.stem:
            raise ValueError(f"{path.name}: id({scenario.id})가 파일명과 다릅니다.")
        if scenario.id in scenarios:
            raise ValueError(f"시나리오 id 가 중복됩니다: {scenario.id}")
        scenarios[scenario.id] = scenario
    if not scenarios:
        raise ValueError(f"시나리오 파일을 찾지 못했습니다: {target}")
    return scenarios


@lru_cache
def get_scenarios() -> dict[str, Scenario]:
    return load_scenarios()


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")
