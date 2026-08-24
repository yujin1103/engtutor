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

from .categories import BY_ID, sort_key

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
PROMPTS_DIR = Path(__file__).parent / "prompts"

# B1 은 A1~A2 를 끝낸 학습자가 갈 곳이다. 앱의 중심은 여전히 왕초보지만,
# 목표가 없으면 조금 익숙해진 사람이 앱을 떠난다.
Level = Literal["A1", "A2", "B1"]


class Scenario(BaseModel):
    """YAML 한 파일 = 시나리오 하나."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    # 어느 상황 묶음에 속하는가(app/tutor/categories.py). 시나리오가 30개를 넘으면
    # 평평한 목록으로는 고를 수 없다.
    category: str
    level: Level = "A1"
    ai_role: str
    situation: str
    goal: str
    opening_line: str
    # 첫 화면은 LLM 호출이 없다. 왕초보가 가장 크게 얼어붙는 지점이므로
    # 여기에도 '그대로 말할 영어'가 있어야 한다.
    # 첫 발화의 해석. 왕초보는 AI 의 영어 자체를 못 읽으므로 화면에서 열어 볼 수 있어야 한다.
    opening_line_ko: str
    opening_say_en: str
    opening_say_more: str
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
        if scenario.category not in BY_ID:
            raise ValueError(
                f"{path.name}: 모르는 분류입니다: {scenario.category!r} "
                f"(가능한 값: {', '.join(BY_ID)})"
            )
        scenarios[scenario.id] = scenario
    if not scenarios:
        raise ValueError(f"시나리오 파일을 찾지 못했습니다: {target}")
    # 분류 순서 -> 레벨 -> 제목. 화면에 나오는 순서가 곧 난이도 순이 되게 한다.
    return dict(
        sorted(
            scenarios.items(),
            key=lambda kv: (sort_key(kv[1].category), kv[1].level, kv[1].title),
        )
    )


@lru_cache
def get_scenarios() -> dict[str, Scenario]:
    return load_scenarios()


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")
