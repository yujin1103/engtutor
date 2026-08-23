"""세션 종료 리포트 생성.

LLM 은 1회만 부른다. 틀린 문장 모음은 DB 값을 그대로 쓰므로 LLM 이 지어낼 여지가 없다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from ..llm.base import LLMClient, LLMError, Message
from ..tutor.loader import Scenario
from ..tutor.schemas import Correction, json_schema_for
from .schemas import ReportInsight, SessionReport

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_TRANSCRIPT_MESSAGES = 40


class ReportService:
    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._system_template = (PROMPTS_DIR / "report_system.md").read_text(encoding="utf-8")
        self._schema = json_schema_for(ReportInsight)

    def build(
        self,
        *,
        session_id: str,
        scenario: Scenario,
        level: str,
        messages: list[Message],
        corrections: list[Correction],
    ) -> SessionReport:
        user_turns = [m for m in messages if m["role"] == "user"]
        insight = self._insight(scenario, level, messages, corrections)
        return SessionReport(
            session_id=session_id,
            scenario_title=scenario.title,
            level=level,
            turn_count=len(user_turns),
            mistake_count=len(corrections),
            mistakes=corrections,
            insight=insight,
        )

    def _insight(
        self,
        scenario: Scenario,
        level: str,
        messages: list[Message],
        corrections: list[Correction],
    ) -> ReportInsight:
        system = self._system_template.format(
            level=level, scenario_title=scenario.title, goal=scenario.goal
        )
        payload: list[Message] = [{"role": "user", "content": _transcript(messages, corrections)}]

        try:
            raw = self._client.chat_json(
                system=system, messages=payload, schema=self._schema, temperature=0.4, max_tokens=1536
            )
            return ReportInsight.model_validate(raw)
        except (LLMError, ValidationError) as first:
            logger.warning("리포트 1차 실패, 재시도합니다: %s", first)

        try:
            raw = self._client.chat_json(
                system=system, messages=payload, schema=self._schema, temperature=0.1, max_tokens=1536
            )
            return ReportInsight.model_validate(raw)
        except (LLMError, ValidationError) as second:
            raise LLMError(f"리포트를 생성하지 못했습니다: {second}") from second


def _transcript(messages: list[Message], corrections: list[Correction]) -> str:
    lines = ["=== CONVERSATION ==="]
    for m in messages[-MAX_TRANSCRIPT_MESSAGES:]:
        who = "Learner" if m["role"] == "user" else "Partner"
        lines.append(f"{who}: {m['content']}")

    lines.append("")
    lines.append("=== CORRECTIONS THAT CAME UP ===")
    if corrections:
        for c in corrections:
            lines.append(f'- "{c.original}" -> "{c.better}" ({c.note})')
    else:
        lines.append("(none - the learner made no mistakes worth correcting)")
    return "\n".join(lines)
