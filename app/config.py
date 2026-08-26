"""환경변수 로딩. 값은 .env 하나에서만 온다 (코드에 키를 두지 않는다)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- 백엔드 선택 ---
    llm_backend: Literal["ollama", "anthropic"] = "ollama"

    # --- Ollama ---
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:14b"
    ollama_num_ctx: int = 4096
    ollama_keep_alive: str = "30m"

    # --- Anthropic ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"

    # --- 음성 입력 (STT) ---
    # 값의 근거는 app/stt/service.py 에 측정 수치와 함께 적어 뒀다.
    stt_enabled: bool = True
    stt_model: str = "small"          # base 는 말을 지어내고 large-v3 는 오류를 지운다
    stt_device: str = "cpu"           # 정확도는 장치와 무관하다. GPU 는 ollama 몫
    stt_compute_type: str = "int8"
    # 모델 파일이 놓이는 곳. 컨테이너를 다시 만들어도 500MB 를 다시 받지 않도록
    # docker-compose.yml 에서 볼륨을 붙여 둔다.
    stt_model_dir: Path = Path("/models/whisper")
    # 업로드 상한. 실수로 긴 파일이 올라오면 CPU 가 오래 잡힌다 — small 은 CPU 에서
    # 오디오 1초당 약 0.3초를 쓴다.
    # 10MB 가 실제로 몇 분인지는 브라우저가 무엇을 주느냐에 달렸다(실측):
    #   webm/opus  약 9KB/s  -> 18분   (MediaRecorder 기본)
    #   wav 48k 스테레오 192KB/s -> 1분  (Streamlit st.audio_input)
    # 한 턴에 한 문장을 말하는 앱이라 어느 쪽이든 넉넉하다.
    stt_max_upload_mb: float = 10.0

    # --- 앱 ---
    db_path: Path = Path("./data/engtutor.db")
    api_base_url: str = "http://api:8000"
    request_timeout: float = 120.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
