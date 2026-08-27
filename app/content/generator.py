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
from .schemas import (
    ExampleGloss,
    WordEntry,
    reject_unrelated_gloss,
    reject_word_meaning,
    reject_wrong_number,
)

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
    "date": "saying dates and times — months, weekdays, today and tomorrow, the clock",

    "ordinal": "saying which one in order — a date, a floor, a turn",
}

# 트랙 -> 예문을 쓸 상황. 장면 묶음(TOPIC_SCENES)과 같은 자리에 같은 방식으로 들어간다.
#
# 왜 프롬프트 파일을 가르지 않고 한 줄만 얹는가
# ---------------------------------------------
# 이 저장소에는 넓은 규칙표를 넣었다가 스키마 실패가 0% -> 62% 로 뛴 기록이 있고,
# 좁은 한 줄이 0/5 -> 5/5 로 먹힌 기록이 있다(README 의 같은 이야기). 토익 어휘에 필요한 것은
# 규칙이 아니라 **예문이 놓일 자리**뿐이다 — `invoice`·`deadline`·`supervisor` 를
# 카페 문장에 넣지 않는 것. 그건 이미 장면 묶음이 쓰고 있는 기계다.
#
# 시스템 프롬프트의 "Everyday situations only" 를 덮어야 해서 상황을 열거해 준다.
# 8단어 제한과 나머지 규칙은 그대로 둔다 — 회의에서 하는 말도 짧게 할 수 있다.
TRACK_SCENES: dict[str, str] = {
    "toeic": (
        "a workplace — a meeting, an email to a client, a business trip, "
        "an invoice, a delivery, a job interview"
    ),
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

    def generate_one(
        self, word: str, topic: str | None = None, track: str | None = None
    ) -> GenerationResult:
        target = word.strip().lower()
        prompt = f"Headword: {word}"
        # 장면이 먼저다. 장면은 낱말 하나에 매인 자리(카페 팩의 `refund`)라
        # 트랙보다 구체적이고, 둘 다 주면 문장이 어느 쪽도 아니게 된다.
        scene = TOPIC_SCENES.get(topic, topic) if topic else TRACK_SCENES.get(track or "")
        if scene:
            # 장면을 알려 주면 예문이 그 장면의 문장이 된다. 회화 앱들이 어휘를
            # 유닛(장면)에 매어 두는 이유가 이것이다 — 학습자는 같은 말을 롤플레이에서
            # 한 번, 단어 카드에서 한 번 만나야 붙는다.
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
        track: str | None = None,
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
            return list(pool.map(lambda w: self.generate_one(w, packs.get(w), track), words))

@dataclass(frozen=True)
class GlossTask:
    """해석을 붙일 대상 하나. DB 행을 그대로 넘기지 않는다.

    동시 호출이라 스레드마다 만지게 되는데, 세션에 매인 ORM 객체를 스레드로
    넘기면 언제 무엇이 로드되는지가 불분명해진다. 필요한 칸만 미리 떠서 넘긴다.
    """

    word: str
    # 모델에게 주는 값이 아니다. 나온 해석을 판정하는 데만 쓴다 — 프롬프트에 넣으면
    # 틀린 뜻이 해석으로 번진다(gloss_one 주석 참고).
    meaning_ko: str
    example: str
    # 판정에만 쓰는 두 번째 잣대. `meaning_ko` 하나로는 '이 해석이 표제어와 완전히
    # 무관한가'를 물을 수 없다 — 뜻은 대개 한두 개만 적혀 있어서, 멀쩡한 해석이
    # 거기 없는 낱말을 쓰면 그대로 걸린다. 노트까지 합치면 걸리는 것이 792개 기준
    # 200개에서 59개로 줄었고, 남은 쪽이 실제 결함이었다.
    #
    # 기본값이 빈 문자열인 이유: 노트 없이 만든 GlossTask 도 그대로 돌아야 한다.
    usage_note: str = ""

@dataclass
class GlossResult:
    word: str
    example_ko: str | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.example_ko is not None

def _gloss_repair_note(example: str, exc: Exception) -> str:
    """해석 재시도 지시문. 무엇이 틀렸는지 그대로 알려 준다."""
    return (
        f"SYSTEM NOTE: your previous answer was rejected. Reason: {exc}. "
        f'Translate this exact sentence into Korean: "{example}". '
        "Answer with the meaning of the SENTENCE, not the meaning of the headword, "
        "and never repeat the English. "
        "Reply again with ONLY the JSON object required by the schema."
    )

class GlossGenerator:
    """이미 저장된 예문에 한국어 해석만 붙인다.

    항목을 다시 만들지 않는다는 것이 이 클래스의 존재 이유다. 해석은 **그 예문**의
    해석이어야 하는데, WordGenerator 로 다시 돌리면 예문 자체가 새로 쓰여서
    학습자가 풀던 빈칸 문장이 통째로 바뀐다. 그래서 칸 하나만 따로 채운다.
    """

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._system = (PROMPTS_DIR / "gloss_system.md").read_text(encoding="utf-8")
        self._schema = json_schema_for(ExampleGloss)

    def gloss_one(self, task: GlossTask) -> GlossResult:
        # **낱말 뜻(meaning_ko)은 모델에게 주지 않는다.** 뜻을 알려 주면 다의어를
        # 가려낼 것 같지만, 실제로는 저장된 뜻이 틀렸을 때 해석이 그 틀린 뜻을
        # 그대로 따라갔다 — `ankle` 의 뜻이 '종아리'로 저장돼 있어서 'My ankle
        # hurts a lot.' 이 '종아리가 많이 아파요'가 됐다(2회 중 2회). 뜻을 빼자
        # '발목이 많이 아파요'로 바뀌었고(2회 중 2회), `bagel` 이 '백일'이 되던
        # 것도 '베이글'이 됐다. 다의어는 문장 자체가 이미 가려낸다 —
        # 'I want to buy a book.' 도 'Can you change the channel?' 도 뜻 없이
        # 정확했다. 뜻은 검증(reject_word_meaning)에만 쓴다.
        #
        # "뜻이 틀리면 무시하라"는 한 줄을 프롬프트에 넣어 보기도 했는데 듣지
        # 않았다(ankle 2/2 그대로). 안 듣는 지시는 남겨 두지 않는다.
        prompt = f"Headword: {task.word}\nSentence: {task.example}"
        ask: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        last: Exception | None = None

        # WordGenerator 와 같은 사다리다. 온도를 낮추며 재시도하되 두 번째부터는
        # 무엇이 틀렸는지 알려준다 — 같은 요청을 그대로 반복하면 대개 똑같이 실패한다.
        for temperature in (0.5, 0.1, 0.0):
            try:
                raw = self._client.chat_json(
                    system=self._system,
                    messages=ask,
                    schema=self._schema,
                    temperature=temperature,
                    max_tokens=256,
                )
                gloss = ExampleGloss.model_validate(raw).example_ko
                # 스키마가 못 보는 검사 셋. 전부 판정에 **DB 에 이미 있는 값**이
                # 필요해서 스키마 안에 넣을 수 없다.
                #
                # 순서는 좁은 것부터다. 수사는 참·거짓이 갈리고(40 은 '서른다섯'이
                # 아니다), 뜻의 흔적은 그보다 약한 판정이라 뒤에 둔다. 앞에서 걸리면
                # 재시도 지시문이 더 구체적인 이유를 들고 간다.
                reject_word_meaning(gloss, example=task.example, meaning_ko=task.meaning_ko)
                reject_wrong_number(gloss, word=task.word, example=task.example)
                reject_unrelated_gloss(
                    gloss,
                    word=task.word,
                    example=task.example,
                    meaning_ko=task.meaning_ko,
                    usage_note=task.usage_note,
                )
                return GlossResult(word=task.word, example_ko=gloss)
            except (LLMError, ValidationError, ValueError) as exc:
                last = exc
                logger.debug("[%s] 해석 실패(temperature=%s): %s", task.word, temperature, exc)
                ask = [
                    {"role": "user", "content": prompt},
                    {"role": "user", "content": _gloss_repair_note(task.example, exc)},
                ]

        return GlossResult(word=task.word, example_ko=None, error=str(last))

    def gloss_many(
        self, tasks: list[GlossTask], *, concurrency: int = 4
    ) -> list[GlossResult]:
        if not tasks:
            return []
        workers = max(1, min(concurrency, len(tasks)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.gloss_one, tasks))

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

_TRACK_DIRECTIVE = re.compile(r"^#\s*track\s*:\s*([a-z][a-z0-9_-]{0,15})\s*$", re.I)
_RANK_OFFSET_DIRECTIVE = re.compile(r"^#\s*rank-offset\s*:\s*(\d{1,6})\s*$", re.I)

def load_track(path: Path) -> str | None:
    """목록이 선언한 트랙. `# track: toeic` 한 줄이면 그 파일은 토익 목록이다.

    왜 CLI 플래그가 아니라 파일에 적는가
    ------------------------------------
    트랙은 그 목록의 성질이지 실행할 때 고르는 것이 아니다. 플래그로 두면 한 번
    빠뜨렸을 때 TOEIC 어휘 2,260개가 **왕초보 트랙에 통째로 쏟아지고**, 트랙은
    행을 만들 때만 정해지므로(crud.upsert_word) 되돌리려면 지우고 다시 만들어야
    한다. 파일에 적으면 누가 언제 다시 돌려도 같은 곳으로 간다.

    선언이 없으면 None — 호출부가 기본 트랙(생활 회화)으로 읽는다.
    """
    for line in _header(path):
        found = _TRACK_DIRECTIVE.match(line)
        if found:
            return found.group(1).lower()
    return None

def load_rank_offset(path: Path) -> int:
    """`# rank-offset: 1250` — 이 목록의 순위를 그만큼 뒤에서 시작한다.

    한 트랙에 목록을 **이어 붙일 때** 필요하다. TSL 과 BSL 은 둘 다 1위부터 매겨진
    다른 코퍼스의 순위라, 그냥 합치면 토익 트랙에 1위가 둘 생기고 정렬이 뒤엉킨다.
    TSL(토익 시험 어휘)을 앞에 두고 BSL(일반 비즈니스)을 1251위부터 잇는다.

    합친 순위가 "TSL 1251위" 라는 뜻은 아니다 — 두 목록의 순위를 한 축에 세울 근거는
    없다. 이 값은 **화면에 낼 순서**이고, 토익 화면에서 시험 어휘가 먼저 나오는 것이
    맞기 때문에 이 순서를 고른 것이다.
    """
    for line in _header(path):
        found = _RANK_OFFSET_DIRECTIVE.match(line)
        if found:
            return int(found.group(1))
    return 0

def _header(path: Path) -> list[str]:
    """맨 위 주석 구역의 줄들. 선언은 여기서만 읽는다(declares_no_rank 와 같은 규칙)."""
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        lines.append(stripped)
    return lines

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
    dropped: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word = line.split(",")[0].strip().strip('"').lower()
        # 하이픈은 표제어의 일부다 — `check-in`, `take-out`, `thirty-first`.
        # 예전에는 여기서 조용히 걸러져서, 목록에 적어도 생성되지 않았다.
        if not word or not word.isascii() or not word.replace("'", "").replace("-", "").isalpha():
            # 조용히 버리지 않는다. TSL·BSL 에는 `ice cream`(띄어쓰기)과
            # `résumé`·`café`·`entrée`(악센트)가 있는데, 아무 말 없이 사라지면
            # "1,250개를 돌렸다"는 보고가 1,246개를 뜻하게 된다.
            if word:
                dropped.append(word)
            continue
        if word in seen:
            continue
        seen.add(word)
        words.append(word)
        if limit is not None and len(words) >= limit:
            break
    if dropped:
        logger.warning(
            "표제어로 받아들일 수 없어 건너뜁니다 %d개 (한 낱말·ASCII 만 받습니다): %s",
            len(dropped), ", ".join(dropped[:10]),
        )
    return words
