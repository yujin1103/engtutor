"""단어 콘텐츠 배치 생성.

원칙(CLAUDE.md 3.5): 생성은 AI(로컬 배치), 검수는 사람, 서빙은 DB.
실시간 대화 경로에서는 절대 호출하지 않는다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ..llm.base import LLMClient, LLMError
from ..tutor.schemas import json_schema_for
from .schemas import WordEntry

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass
class GenerationResult:
    word: str
    entry: WordEntry | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.entry is not None


class WordGenerator:
    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._system = (PROMPTS_DIR / "word_system.md").read_text(encoding="utf-8")
        self._schema = json_schema_for(WordEntry)

    def generate_one(self, word: str) -> GenerationResult:
        for temperature in (0.5, 0.1):  # 실패하면 온도를 낮춰 1회 재시도
            try:
                raw = self._client.chat_json(
                    system=self._system,
                    messages=[{"role": "user", "content": f"Headword: {word}"}],
                    schema=self._schema,
                    temperature=temperature,
                    max_tokens=768,
                )
                entry = WordEntry.model_validate(raw)
                # 모델이 다른 단어로 바꿔치기하는 경우가 있어 확인한다.
                if entry.word != word.strip().lower():
                    raise ValueError(f"다른 단어를 생성했습니다: {entry.word!r}")
                return GenerationResult(word=word, entry=entry)
            except (LLMError, ValidationError, ValueError) as exc:
                last = exc
                logger.debug("[%s] 생성 실패(temperature=%s): %s", word, temperature, exc)
        return GenerationResult(word=word, entry=None, error=str(last))

    def generate_many(self, words: list[str], *, concurrency: int = 4) -> list[GenerationResult]:
        """동시 호출. 로컬 GPU 한 장이라 과하게 올리면 오히려 느려진다.

        캡스톤(E:/Capstone_dub)에서 LLM 배치에 max_workers=10 이 검증됐지만,
        그건 여러 서비스가 나눠 쓰던 구성이었다. 여기서는 4 부터 재보는 걸 권한다.
        """
        if not words:
            return []
        workers = max(1, min(concurrency, len(words)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.generate_one, words))


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
        if not word or not word.isascii() or not word.replace("'", "").isalpha():
            continue
        if word in seen:
            continue
        seen.add(word)
        words.append(word)
        if limit is not None and len(words) >= limit:
            break
    return words
