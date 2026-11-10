from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KBASE_", env_file=str(ROOT / ".env"), extra="ignore"
    )

    app_name: str = "KBase"
    version: str = "1.0.0"
    host: str = "127.0.0.1"
    port: int = 8010
    data_dir: Path = ROOT / "data"

    embed_dim: int = 1024
    retrieve_top_k: int = 50
    rerank_top_k: int = 6
    rrf_k: int = 60
    refusal_threshold: float = 0.03
    refusal_weak: float = 0.34
    refusal_coverage: float = 0.18

    chunk_max_tokens: int = 420
    chunk_overlap: float = 0.15
    parent_max_tokens: int = 1800

    llm_provider: str = "local"
    embed_provider: str = "hash"
    openai_base_url: str = "https://api.deepseek.com"
    openai_api_key: str = ""
    openai_model: str = "deepseek-flash"
    openai_model_deep: str = "deepseek-v4-pro"
    max_tokens_simple: int = 400
    max_tokens_complex: int = 900
    llm_timeout: float = 60.0

    max_tool_calls: int = 3
    cors_origins: list[str] = ["*"]

    # 语义缓存与成本核算
    cache_enabled: bool = True
    cache_threshold: float = 0.95
    cache_ttl_seconds: int = 1800
    price_input_per_1k: float = 0.001
    price_output_per_1k: float = 0.002

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kbase.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
