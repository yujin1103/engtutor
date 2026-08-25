"""음성 전사와 학습자가 확정한 문장을 나란히 다룬다. LLM 도 네트워크도 쓰지 않는다.

왜 둘을 따로 두는가
-------------------
음성 입력에서 문자열은 실은 두 개다.

    들은 것    "I want an iced americano"     <- Whisper 가 전사한 것
    확정한 것  "I want ice americano"          <- 학습자가 고쳐서 보낸 것

한 칸에만 저장하면 전사는 버려지고, **둘의 차이**도 함께 사라진다. 그 차이가
이 앱에서 가장 알고 싶은 것이다 — 이 STT 를 믿어도 되는가.

가장 위험한 실패
----------------
Whisper 는 학습자의 틀린 영어를 **매끄럽게 고쳐서** 적는다. 언어모델 prior 가
그렇게 하도록 만들어져 있다. `I want ice americano` 가 `an iced americano` 로
전사되면 교정할 것이 사라지고, 앱의 존재 이유가 조용히 없어진다.

이건 잘못 들은 것보다 잡기 어렵다. 학습자 눈에는 맞는 문장이 떠 있어서
되돌릴 이유를 못 느끼기 때문이다. 그래서 **되돌렸다는 사실 자체가 귀한 신호**다.

확신과 수정을 대조한다
----------------------
Whisper 는 단어마다 확률을 준다. 여기에 학습자의 수정을 겹치면 두 가지를 얻는다.

  1. 확률이 낮은 단어를 화면에 표시해 **학습자가 볼 곳**을 알려준다.
  2. **확신했는데 학습자가 고친 단어**를 센다. 그게 매끄럽게 고쳐진 자리다.
     확률이 낮은 곳을 고친 것은 그냥 잘못 들은 것이라 덜 위험하다.

2번이 없으면 1번을 믿을 근거가 없다. 확률이 낮은 곳과 실제로 고친 곳이 겹치지
않는다면 그 표시는 도움이 아니라 소음이다 — 그것도 재고 나서 알 일이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

InputMode = Literal["text", "voice"]

# 이 값 아래면 "자신 없이 들었다"로 본다. 화면에 흐리게 표시할 기준이다.
# 임계값은 짐작이다 — 실제 사용 기록으로 수정과 겹치는지 보고 조정해야 한다.
LOW_CONFIDENCE = 0.6

_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")


@dataclass(frozen=True)
class TranscriptWord:
    """전사된 낱말 하나와 STT 가 그것에 준 확률."""

    word: str
    probability: float | None = None

    @property
    def uncertain(self) -> bool:
        """확률을 모르면 '자신 없음'으로 치지 않는다 — 모르는 것과 낮은 것은 다르다."""
        return self.probability is not None and self.probability < LOW_CONFIDENCE


@dataclass(frozen=True)
class Edit:
    """전사와 확정본 사이의 낱말 단위 차이."""

    kind: Literal["replaced", "inserted", "removed"]
    heard: str
    confirmed: str
    index: int


def tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def parse_words(raw: Any) -> list[TranscriptWord]:
    """STT 가 준 단어 목록을 받아들인다. 모양이 조금씩 달라도 깨지지 않게.

    faster-whisper 는 `{"word": " iced", "probability": 0.83}` 를 준다. 다른
    엔진으로 바꿀 여지가 있으므로 키 이름을 몇 가지 받아 주고, 못 알아보면
    조용히 건너뛴다. 여기서 예외를 던지면 전사 하나 때문에 턴이 통째로 죽는다.
    """
    if not raw:
        return []
    out: list[TranscriptWord] = []
    for entry in raw:
        if isinstance(entry, str):
            out.append(TranscriptWord(entry.strip()))
            continue
        if not isinstance(entry, dict):
            continue
        word = str(entry.get("word") or entry.get("text") or "").strip()
        if not word:
            continue
        prob = entry.get("probability", entry.get("confidence", entry.get("score")))
        try:
            probability = float(prob) if prob is not None else None
        except (TypeError, ValueError):
            probability = None
        out.append(TranscriptWord(word, probability))
    return out


def uncertain_words(words: list[TranscriptWord]) -> list[TranscriptWord]:
    """화면에 흐리게 표시할 낱말들."""
    return [w for w in words if w.uncertain]


def edits(heard: str, confirmed: str) -> list[Edit]:
    """전사와 확정본의 낱말 단위 차이. 같으면 빈 목록."""
    a, b = tokens(heard), tokens(confirmed)
    if a == b:
        return []

    out: list[Edit] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            # 길이가 다르면 짝이 안 맞는다. 짝지어지는 만큼만 replaced 로 보고
            # 남는 쪽은 삽입/삭제로 돌린다 — 억지로 짝지으면 차이가 왜곡된다.
            pairs = min(i2 - i1, j2 - j1)
            for k in range(pairs):
                out.append(Edit("replaced", a[i1 + k], b[j1 + k], i1 + k))
            for k in range(pairs, i2 - i1):
                out.append(Edit("removed", a[i1 + k], "", i1 + k))
            for k in range(pairs, j2 - j1):
                out.append(Edit("inserted", "", b[j1 + k], i2))
        elif tag == "delete":
            for k in range(i1, i2):
                out.append(Edit("removed", a[k], "", k))
        elif tag == "insert":
            for k in range(j1, j2):
                out.append(Edit("inserted", "", b[k], i1))
    return out


def confident_edits(words: list[TranscriptWord], changes: list[Edit]) -> list[Edit]:
    """**확신했는데 학습자가 고친** 자리. 매끄럽게 고쳐진 흔적이 여기 있다.

    확률이 낮았던 곳을 고친 것은 그냥 잘못 들은 것이라 덜 위험하다. 위험한 건
    STT 가 자신 있게 다른 말을 적었고 학습자가 그걸 되돌린 경우다.

    확률을 모르는 전사(엔진이 안 주거나 타자 입력)는 세지 않는다 — 모르는 것을
    '확신했다'로 치면 숫자가 통째로 거짓이 된다.
    """
    out: list[Edit] = []
    for change in changes:
        if change.kind == "inserted":
            continue  # 없던 자리라 대응하는 확률이 없다
        if 0 <= change.index < len(words):
            word = words[change.index]
            if word.probability is not None and not word.uncertain:
                out.append(change)
    return out
