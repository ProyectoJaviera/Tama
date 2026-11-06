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
    local_model: str = "qwen3:4b-instruct-2507-q8_0"
    background_model: str = "qwen3:4b-instruct-2507-q8_0"
    think: bool = False
    # Siempre explícito: si no, Ollama usa su default (puede ser enorme y llenar la RAM).
    num_ctx: int = 8192
    # 0 = solo CPU y RAM. La GPU de este PC (GTX 660, 2 GB) no sirve para modelos.
    num_gpu: int = 0
    # Hilos de CPU para el modelo local. Ollama usa solo los 6 núcleos físicos del i5-10400F;
    # con 10 de sus 12 hilos lee el prompt ~35% más rápido y escribe ~20% más rápido (ADR 0004).
    # 0 deja que Ollama decida.
    num_thread: int = 10
    timeout: float = 300.0
    user_name: str = "Renzo"
    data_dir: Path = Path("data")
    # Perfil (`nucleo.md`) y export del grafo; datos personales, nunca en el repo público.
    memory_dir: Path = Path("memoria")

    # Capa 1: nube de Ollama.
    ollama_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("OLLAMA_API_KEY", "TAMA_OLLAMA_API_KEY")
    )
    cloud_host: str = "https://ollama.com"
    cloud_model: str = "gemma4:31b-cloud"
    cloud_first_token_s: float = 8.0

    # Capa 2: Claude. Las evals la apagan (ver evals/__init__.py) para cuidar el cupo de la API.
    claude_enabled: bool = True
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
    # Al abrir Tama se precarga el modelo local en segundo plano, haya red o no: si se corta, la
    # primera respuesta no espera la carga. Sin red, la web dice "preparando" mientras tanto.
    local_preload: bool = True
    # Cuánto queda el modelo local en RAM tras cada petición (chat, precarga y reflexión). -1 lo
    # deja cargado hasta que se cierre Ollama, así al volver a abrir Tama ya está listo (usa
    # ~5 GB de RAM). Acepta duraciones de Ollama: "30m", "2h"; 0 lo descarga al tiro.
    local_keep_alive: str = "-1"
    # La capa local busca en la memoria con las palabras del mensaje (ADR 0003).
    local_auto_recall: bool = True

    # Motor proactivo: avisa solo, con Silencio Operativo. ntfy es opcional y autoalojado.
    proactive: bool = True
    timezone: str = "America/Santiago"
    quiet_start: int = 22
    quiet_end: int = 8
    daily_cap: int = 3
    morning_hour: int = 8
    summary_until_hour: int = 12
    stale_goal_days: int = 14
    ntfy_url: str = ""
    ntfy_topic: str = ""
    # Dirección de ntfy que usa el celular (https://<pc>.ts.net:8443): para el enlace de Estado
    # que suscribe la app ntfy. docker compose la usa también como base-url de ntfy.
    ntfy_public_url: str = ""

    # Interfaz web: solo localhost, salvo que el contenedor necesite escuchar hacia su puerto.
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    # Acceso desde el celular por Tailscale (`tailscale serve`): nombre del PC en Tailscale, p. ej.
    # "mi-pc.tail1234.ts.net". Solo se acepta si la petición trae la cabecera que agrega
    # `tailscale serve` (Tailscale-User-Login) y, con web_tailscale_user, solo de esa cuenta.
    web_tailscale_host: str = ""
    web_tailscale_user: str = ""
    web_confirm_timeout_s: float = 120.0
    # Un turno que pasa de esto se da por perdido y el chat acepta un mensaje nuevo.
    web_turn_limit_s: float = 600.0

    # Herramientas (servidores MCP locales).
    mcp_enabled: bool = True
    mcp_config: Path = Path("config/mcp.toml")
    workdir: Path = Path("data/trabajo")
    # Máximo a la vista por turno; el resto se pide con abrir_herramientas (ver selector.py).
    tools_max: int = 18
    tool_start_timeout_s: float = 15.0
    tool_timeout_s: float = 30.0
    tool_result_max_chars: int = 4000

    # Reflexión en inactividad (siempre con el modelo local). Se puede apagar.
    reflection: bool = True
    reflection_idle_min: float = 20.0
    reflection_min_free_gb: float = 6.0

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
