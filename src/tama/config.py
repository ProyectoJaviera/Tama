"""Configuración de Tama, leída de variables de entorno y de `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TAMA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Ollama local. En Docker apunta al Ollama nativo del host (http://host.docker.internal:11434).
    ollama_host: str = Field(
        "http://127.0.0.1:11434", validation_alias=AliasChoices("TAMA_OLLAMA_HOST", "OLLAMA_HOST")
    )
    local_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    background_model: str = "qwen3:4b"
    think: bool = False
    # Siempre explícito: si no, Ollama usa su default (puede ser enorme y llenar la RAM).
    num_ctx: int = 8192
    # 0 = solo CPU y RAM. La GPU de este PC (GTX 660, 2 GB) no sirve para modelos.
    num_gpu: int = 0
    timeout: float = 300.0
    data_dir: Path = Path("data")

    # Capa 1: nube de Ollama.
    ollama_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("OLLAMA_API_KEY", "TAMA_OLLAMA_API_KEY")
    )
    cloud_host: str = "https://ollama.com"
    cloud_model: str = "gemma4:31b-cloud"
    cloud_first_token_s: float = 8.0

    # Capa 2: Claude.
    anthropic_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "TAMA_ANTHROPIC_API_KEY")
    )
    claude_model: str = "claude-haiku-5-5"
    claude_first_token_s: float = 8.0
    # low | medium | high | xhigh | max. "low" baja la latencia en conversación.
    claude_effort: str = "low"
    claude_monthly_usd: float = 5.0
    # Precios en US$ por millón de tokens (Haiku 5.5, prompts de hasta 100K tokens).
    claude_usd_per_mtok_in: float = 0.10
    claude_usd_per_mtok_out: float = 0.50

    # Cortacircuitos y conexión.
    breaker_failures: int = 3
    breaker_cooldown_s: float = 300.0
    connectivity_probe: str = "1.1.1.1:443"
    connectivity_ttl_s: float = 30.0
    # En el chat, cada cuántos segundos se revisa la red en segundo plano (0 = no se revisa).
    connectivity_watch_s: float = 5.0
    # Mantener el modelo local siempre cargado en RAM (útil si sobra memoria).
    local_keep_warm: bool = False

    # Herramientas (servidores MCP locales).
    mcp_enabled: bool = True
    mcp_config: Path = Path("config/mcp.toml")
    workdir: Path = Path("data/trabajo")
    tools_max: int = 10
    tool_start_timeout_s: float = 15.0
    tool_timeout_s: float = 30.0
    tool_result_max_chars: int = 4000

    # Historial de la sesión que se envía al modelo (tokens estimados, sin contar la identidad).
    history_max_tokens: int = 2000

    @field_validator("ollama_host")
    @classmethod
    def add_scheme(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        return value if "://" in value else f"http://{value}"

    @field_validator("ollama_api_key", "anthropic_api_key", mode="before")
    @classmethod
    def empty_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def events_db(self) -> Path:
        return self.data_dir / "events.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
