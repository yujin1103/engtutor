"""오디오 -> 글자. faster-whisper 를 한 번만 올려 두고 재사용한다.

설정은 전부 측정으로 정했다
---------------------------
같은 화자의 녹음 20개(윈도우 10 · 아이폰 10)로 15가지 설정을 재고 남은 것이
아래 값들이다. 각 값 옆에 왜 그 값인지 근거를 적어 뒀다 — 나중에 "더 큰 모델이
낫지 않나" 로 되돌리는 일을 막기 위해서다. 대부분은 **더 좋은 전사가 이 앱에는
더 나쁘다**는, 직관과 반대인 결과다.

이 앱이 STT 에 바라는 것은 정확한 받아쓰기가 아니라 **학습자가 말한 대로**
적는 것이다. `I want ice americano` 를 `an iced americano` 로 고쳐 적으면
교정할 것이 사라지고 앱의 존재 이유가 조용히 없어진다(app/tutor/transcript.py).

첫 요청 때 올린다
-----------------
모델은 500MB 다. 프로세스 시작 때 올리면 STT 를 쓰지 않는 사람도 그만큼을
기다린다. 대신 첫 요청 한 번만 느리다(로드 약 3초). 미리 데워 두려면:

    docker compose exec -T api python -c "from app.stt import get_stt_service; get_stt_service().load()"
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

# 학습자 문체 표본.
#
# initial_prompt 는 Whisper 를 어느 쪽 영어로 미는 손잡이다. 표준 영어 표본을
# 주면 표준 쪽으로 밀려 학습자의 오류를 지운다. 그래서 **일부러 틀린 문장**을
# 준다. 두 기기 모두 WER 이 내려갔고(윈도우 0.114 -> 0.085, 아이폰 0.193 ->
# 0.151) 오류 생존율은 깎이지 않았다.
#
# 여기에 **목표 문장이나 시나리오 대사를 넣으면 안 된다.** 무엇을 말해야 하는지
# 알려 주면 Whisper 가 오디오가 아니라 프롬프트를 받아 적는다 — 틀리게 말한
# 10문장 중 9개가 맞는 문장으로 전사됐다. 이 상수를 시나리오별로 바꾸고
# 싶어지면 그 측정을 먼저 다시 하라.
LEARNER_STYLE_PROMPT = (
    "Yesterday I go to market. I have 20 years old. Please explain me this. "
    "I very like this song. She is more taller than me."
)


class SttUnavailable(RuntimeError):
    """음성 인식을 쓸 수 없다 — 꺼져 있거나 faster-whisper 가 없다.

    앱의 다른 기능은 이것과 무관하게 계속 돌아야 하므로, 임포트 시점이 아니라
    호출 시점에만 터진다.
    """


@dataclass(frozen=True)
class Transcription:
    """전사 한 번의 결과."""

    text: str
    # {"word": " iced", "probability": 0.83} 모양. app/tutor/transcript.py 의
    # parse_words() 가 그대로 읽는 형식이다.
    words: list[dict[str, Any]]
    duration_ms: int          # 서버가 실제로 쓴 시간
    audio_seconds: float      # 오디오 길이. 지연이 길이 탓인지 구분하려고 같이 준다
    model: str

    @property
    def empty(self) -> bool:
        """말이 안 들렸다. 오류가 아니라 정상적인 결과다 — 무음이면 여기로 온다."""
        return not self.text


class SttService:
    """WhisperModel 하나를 감싼다. 지연 로드 + 요청 직렬화."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._model: Any = None
        # 로드도 전사도 이 자물쇠 뒤에서 한다.
        # 로드: 두 요청이 동시에 들어오면 500MB 를 두 번 올린다.
        # 전사: CPU 를 다 쓰는 작업이라 동시에 돌리면 둘 다 느려질 뿐이다.
        #       단일 사용자용 앱이므로 줄을 세우는 편이 낫다.
        self._lock = threading.Lock()

    # ------------------------------------------------------------- 상태
    @property
    def enabled(self) -> bool:
        return self._s.stt_enabled

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @staticmethod
    def installed() -> bool:
        """faster-whisper 가 설치돼 있는가. 임포트만 해 보고 모델은 건드리지 않는다."""
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def describe(self) -> dict[str, Any]:
        """/healthz 에 넣을 한 줄. '왜 안 되지' 를 빨리 좁히기 위한 것."""
        return {
            "enabled": self.enabled,
            "installed": self.installed(),
            "model": self._s.stt_model,
            "device": self._s.stt_device,
            "compute_type": self._s.stt_compute_type,
            "model_dir": str(self._s.stt_model_dir),
            "loaded": self.loaded,
            "max_upload_mb": self._s.stt_max_upload_mb,
        }

    # ------------------------------------------------------------- 로드
    def load(self) -> None:
        """모델을 올린다. 이미 올라와 있으면 아무 일도 하지 않는다."""
        self._ensure_model()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:  # 자물쇠를 기다리는 동안 남이 올렸을 수 있다
                return self._model
            if not self.enabled:
                raise SttUnavailable(
                    "음성 인식이 꺼져 있습니다. .env 에서 STT_ENABLED=true 로 켜세요."
                )
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SttUnavailable(
                    "faster-whisper 가 설치돼 있지 않습니다. "
                    "`docker compose build api` 로 이미지를 다시 만드세요."
                ) from exc

            directory = Path(self._s.stt_model_dir)
            directory.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            logger.info("Whisper 모델을 올립니다: %s (%s)", self._s.stt_model, directory)
            try:
                self._model = WhisperModel(
                    # 모델은 small 이다. base 는 프롬프트를 주면 "Thank you very
                    # much." 를 지어내 붙였고, large-v3 는 학습자 오류를 가장 많이
                    # 지웠다(생존 44%). small 만 두 기기에서 버텼다(67% / 56%).
                    self._s.stt_model,
                    # 전사 정확도는 장치와 무관하다 — GPU 는 속도만 바꾼다. 그래서
                    # api 컨테이너에 CUDA 를 넣지 않는다. GPU 는 ollama 몫이다.
                    device=self._s.stt_device,
                    compute_type=self._s.stt_compute_type,
                    # 컨테이너를 다시 만들어도 500MB 를 다시 받지 않도록 볼륨 위에 둔다.
                    download_root=str(directory),
                )
            except Exception as exc:  # 네트워크 없음 · 디스크 없음 · 모델명 오타
                raise SttUnavailable(
                    f"Whisper 모델을 올리지 못했습니다 ({self._s.stt_model}): {exc}"
                ) from exc
            logger.info("Whisper 모델을 올렸습니다: %.2fs", time.perf_counter() - started)
            return self._model

    # ------------------------------------------------------------- 전사
    def transcribe(self, audio: str | Path | BinaryIO) -> Transcription:
        """오디오를 전사한다. 디코딩은 faster-whisper 가 PyAV 로 직접 한다.

        그래서 webm · wav · m4a · mp3 · ogg 를 그대로 받는다. 브라우저가 주는
        것은 보통 webm 아니면 wav 다.

        무음이면 예외가 아니라 **빈 전사**가 나온다. 마이크만 누르고 말을 안 한
        경우라서 오류로 다룰 일이 아니다 — 화면이 "안 들렸어요" 를 그리면 된다.
        """
        model = self._ensure_model()
        started = time.perf_counter()
        with self._lock:
            segments, info = model.transcribe(
                audio,
                # 한국인 학습자가 **영어를** 연습하는 앱이다. 언어 자동 감지를 켜 두면
                # 짧은 발화에서 한국어로 잘못 잡아 통째로 엉뚱한 글자가 나온다.
                language="en",
                # 무음이 들어오면 Whisper 는 말을 **지어낸다**. 실제로 무음 파일로
                # 배관을 확인하다 "I'm sorry" 를 56번 반복해 지어낸 것을 봤다.
                # 이 옵션으로 지어낸 낱말 1,090건이 0이 됐다. 끄면 안 된다.
                vad_filter=True,
                # 샘플링을 끈다. 같은 오디오에서 같은 글자가 나와야 측정이 의미가 있다.
                temperature=0.0,
                # 앞 문장을 다음 전사의 조건으로 넘기지 않는다. 넘기면 한 번 잘못
                # 들은 것이 뒤 발화까지 끌고 간다.
                condition_on_previous_text=False,
                # 낱말별 확률. **화면에는 쓰지 않는다** — 확신도로 "여기를 보라"고
                # 찍어 주는 방법은 재 봤더니 정확도 20%, 잡는 비율 29~40% 라
                # 소음이었다. 그래도 저장은 한다. 실사용 기록이 쌓이면 녹음 20개보다
                # 나은 답을 준다(app/tutor/transcript.py 의 confident_edits).
                word_timestamps=True,
                initial_prompt=LEARNER_STYLE_PROMPT,
            )

            parts: list[str] = []
            words: list[dict[str, Any]] = []
            # segments 는 제너레이터다. 여기서 소비해야 실제 계산이 일어난다.
            for segment in segments:
                parts.append(segment.text)
                for word in getattr(segment, "words", None) or []:
                    words.append(
                        {
                            "word": word.word,
                            "probability": getattr(word, "probability", None),
                        }
                    )

        return Transcription(
            text=" ".join(p.strip() for p in parts).strip(),
            words=words,
            duration_ms=int((time.perf_counter() - started) * 1000),
            audio_seconds=round(float(getattr(info, "duration", 0.0) or 0.0), 2),
            model=self._s.stt_model,
        )


@lru_cache
def get_stt_service() -> SttService:
    """프로세스에 하나만 둔다. 요청마다 올리면 매번 3초씩 날아간다."""
    return SttService()
