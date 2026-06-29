from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def normalize_db_url(url: str) -> str:
    u = url.strip()
    if not u:
        return u
    for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
        if u.startswith(prefix):
            return "postgresql+psycopg://" + u[len(prefix):]
    return u


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dataset_dir: str = Field(default="../goodlane-interview-dataset")
    database_url: str = Field(default="")
    sqlite_path: str = Field(default="data/freight.db")

    openai_api_key: str = Field(default="")
    agent_model: str = Field(default="gpt-5.5")
    extraction_model: str = Field(default="gpt-5.4-mini")
    transcribe_model: str = Field(default="gpt-4o-transcribe-diarize")
    embed_model: str = Field(default="text-embedding-3-small")
    embed_dim: int = Field(default=1536)

    @property
    def dataset_path(self) -> Path:
        return _resolve(self.dataset_dir)

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{_resolve(self.sqlite_path)}"

    @property
    def primary_url(self) -> str:
        return normalize_db_url(self.database_url) or self.sqlite_url

    @property
    def uses_postgres(self) -> bool:
        return bool(self.database_url.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
