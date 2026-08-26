"""음성 입력의 첫 단계 — 오디오를 글자로 옮긴다.

여기서 나온 전사는 **학습자에게 확인만 받고** 그대로 `/chat` 의 `transcript` 로
넘어간다. 전사를 대신 고쳐 주는 일은 하지 않는다 — 그건 튜터의 일이다.
"""

from .service import (
    LEARNER_STYLE_PROMPT,
    SttService,
    SttUnavailable,
    Transcription,
    get_stt_service,
)

__all__ = [
    "LEARNER_STYLE_PROMPT",
    "SttService",
    "SttUnavailable",
    "Transcription",
    "get_stt_service",
]
