"""Configuración de Tama, leída de variables de entorno y de `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TAMA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # En Docker apunta al Ollama nativo del host (http://host.docker.internal:11434).
    ollama_host: str = Field(
        "http://127.0.0.1:11434", validation_alias=AliasChoices("TAMA_OLLAMA_HOST", "OLLAMA_HOST")
    )
    local_model: str = "qwen3:8b"
    think: bool = False
    # Siempre explícito: si no, Ollama usa su default (puede ser enorme y llenar la RAM).
    num_ctx: int = 8192
    # 0 = solo CPU y RAM. La GPU de este PC (GTX 660, 2 GB) no sirve para modelos.
    num_gpu: int = 0
    timeout: float = 300.0
    data_dir: Path = Path("data")

    @field_validator("ollama_host")
    @classmethod
    def add_scheme(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        return value if "://" in value else f"http://{value}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
