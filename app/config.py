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

    # --- 앱 ---
    db_path: Path = Path("./data/engtutor.db")
    api_base_url: str = "http://api:8000"
    request_timeout: float = 120.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
