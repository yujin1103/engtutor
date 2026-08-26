"""단어 콘텐츠 배치 생성.

원칙(CLAUDE.md 3.5): 생성은 AI(로컬 배치), 검수는 사람, 서빙은 DB.
실시간 대화 경로에서는 절대 호출하지 않는다.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ..llm.base import LLMClient, LLMError
from ..tutor.schemas import json_schema_for
from .schemas import WordEntry

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# 장면 묶음 이름 -> 모델에게 줄 한 줄 설명. 예문을 그 장면의 문장으로 만들기 위한
# 것이라 시나리오 제목처럼 구체적이어야 한다. 없는 이름은 그대로 넘긴다.
TOPIC_SCENES: dict[str, str] = {
    "cafe": "ordering a drink at a coffee shop counter",
    "fastfood": "ordering at a fast food counter",
    "food": "eating out and talking about food",
    "grocery": "shopping at a supermarket",
    "transport": "taking the subway, a bus or a taxi",
    "airport": "checking in and going through an airport",
    "hotel": "checking into a hotel and asking for things",
    "shopping": "buying clothes in a shop",
    "money": "paying, getting change, asking for a refund",
    "health": "describing symptoms at a clinic or pharmacy",
    "daily": "talking about the weather, dates and daily plans",
    "home": "talking about home, devices and deliveries",
    "talk": "small talk and keeping a conversation going",
    "number": "saying numbers out loud — prices, times, quantities",
    "weekday": "making plans on a certain day of the week",
    "month": "saying dates — months and days of the month",
    "ordinal": "saying which one in order — a date, a floor, a turn",
}


@dataclass
class GenerationResult:
    word: str
    entry: WordEntry | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.entry is not None


def _repair_note(target: str, exc: Exception) -> str:
    """직전 실패를 그대로 알려 주는 재시도 지시문."""
    return (
        f'SYSTEM NOTE: your previous answer was rejected. Reason: {exc}. '
        f'The "word" field must be exactly "{target}" — never a different, similar-looking, '
        f'or more common word. Describe "{target}" itself. '
        "Reply again with ONLY the JSON object required by the schema."
    )


class WordGenerator:
    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._system = (PROMPTS_DIR / "word_system.md").read_text(encoding="utf-8")
        self._schema = json_schema_for(WordEntry)

    def generate_one(self, word: str, topic: str | None = None) -> GenerationResult:
        target = word.strip().lower()
        prompt = f"Headword: {word}"
        if topic:
            # 장면을 알려 주면 예문이 그 장면의 문장이 된다. 회화 앱들이 어휘를
            # 유닛(장면)에 매어 두는 이유가 이것이다 — 학습자는 같은 말을 롤플레이에서
            # 한 번, 단어 카드에서 한 번 만나야 붙는다.
            scene = TOPIC_SCENES.get(topic, topic)
            prompt += (
                f"\nScene: {scene}\n"
                "Write the example as a sentence someone actually says in that scene. "
                "Keep it inside the 8-word limit."
            )
        ask: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        last: Exception | None = None

        # 온도를 낮추며 재시도하되, 두 번째부터는 **무엇이 틀렸는지** 알려준다.
        # 같은 요청을 그대로 반복하면 대개 똑같이 실패한다 (TutorService 와 같은 이유).
        for temperature in (0.5, 0.1, 0.0):
            try:
                raw = self._client.chat_json(
                    system=self._system,
                    messages=ask,
                    schema=self._schema,
                    temperature=temperature,
                    max_tokens=768,
                )
                entry = WordEntry.model_validate(raw)
                # 모델이 다른 단어로 바꿔치기하는 경우가 있어 확인한다.
                # arrange -> arrive 처럼 비슷하게 생긴 고빈도 단어로 끌려간다.
                if entry.word != target:
                    raise ValueError(f"다른 단어를 생성했습니다: {entry.word!r}")
                return GenerationResult(word=word, entry=entry)
            except (LLMError, ValidationError, ValueError) as exc:
                last = exc
                logger.debug("[%s] 생성 실패(temperature=%s): %s", word, temperature, exc)
                ask = [
                    {"role": "user", "content": prompt},
                    {"role": "user", "content": _repair_note(target, exc)},
                ]

        return GenerationResult(word=word, entry=None, error=str(last))

    def generate_many(
        self,
        words: list[str],
        *,
        concurrency: int = 4,
        topics: dict[str, str] | None = None,
    ) -> list[GenerationResult]:
        """동시 호출. 로컬 GPU 한 장이라 과하게 올리면 오히려 느려진다.

        캡스톤(E:/Capstone_dub)에서 LLM 배치에 max_workers=10 이 검증됐지만,
        그건 여러 서비스가 나눠 쓰던 구성이었다. 여기서는 4 부터 재보는 걸 권한다.
        """
        if not words:
            return []
        packs = topics or {}
        workers = max(1, min(concurrency, len(words)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda w: self.generate_one(w, packs.get(w)), words))


def declares_no_rank(path: Path) -> bool:
    """목록이 스스로 "내 순서는 빈도가 아니다" 라고 밝혔는가.

    NGSL 은 파일 순서가 곧 빈도 순서지만, 장면별로 묶은 목록(content/data/app_words.txt)은
    순서에 그런 뜻이 없다. 그걸 빈도로 읽으면 `americano` 가 `the` 보다 자주 쓰는 말이
    된다. 목록 맨 위에 `# rank: none` 한 줄을 두면 순위를 건드리지 않는다.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            return False  # 주석 구역이 끝났다
        if stripped.lower().replace(" ", "").startswith("#rank:none"):
            return True
    return False


_TOPIC_DIRECTIVE = re.compile(r"^#\s*topic\s*:\s*([a-z][a-z0-9_-]{0,31})\s*$", re.I)


def load_topics(path: Path) -> dict[str, str]:
    """표제어 -> 장면 묶음. `# topic: cafe` 아래 나오는 단어들이 그 묶음이다.

    다른 회화 앱들이 '유닛'이라 부르는 것을 파일에서 그대로 표현한다. 선언이 없는
    구간의 단어는 묶음이 없다(NGSL 같은 일반 어휘). 순서를 바꿔도 뜻이 안 변하도록
    **선언 뒤에 오는 줄에만** 적용한다.
    """
    topics: dict[str, str] = {}
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            found = _TOPIC_DIRECTIVE.match(stripped)
            if found:
                current = found.group(1).lower()
            continue
        word = stripped.split(",")[0].strip().strip('"').lower()
        if word and current and word not in topics:
            topics[word] = current
    return topics


def load_wordlist(path: Path, *, limit: int | None = None) -> list[str]:
    """단어 목록 파일을 읽는다. 한 줄에 한 단어, `#` 로 시작하면 주석.

    CSV 도 받는다(첫 컬럼을 단어로 본다) — NGSL 배포본이 CSV 라서.
    """
    words: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word = line.split(",")[0].strip().strip('"').lower()
        # 하이픈은 표제어의 일부다 — `check-in`, `take-out`, `thirty-first`.
        # 예전에는 여기서 조용히 걸러져서, 목록에 적어도 생성되지 않았다.
        if not word or not word.isascii() or not word.replace("'", "").replace("-", "").isalpha():
            continue
        if word in seen:
            continue
        seen.add(word)
        words.append(word)
        if limit is not None and len(words) >= limit:
            break
    return words
