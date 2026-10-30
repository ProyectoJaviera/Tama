"""Arma el cerebro en cascada a partir de la configuración."""

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from tama.cascade import Cascade, CircuitBreaker, Layer, claude_budget_gate
from tama.config import Settings
from tama.events import EventStore
from tama.mcp_hub import McpHub, load_config, server_env
from tama.memory import recall_context, session_prompt
from tama.net import Connectivity, tcp_probe, watch
from tama.ollama import OllamaClient
from tama.providers.claude import ClaudeProvider
from tama.providers.ollama import OllamaCloudProvider, OllamaLocalProvider
from tama.reflection import Reflector, Thinker, close_days, enough_ram, reflect
from tama.selector import ToolFocus
from tama.warmup import LocalWarmer


@dataclass
class Brain:
    cascade: Cascade
    store: EventStore
    local: OllamaClient
    connectivity: Connectivity | None = None
    warmer: LocalWarmer | None = None
    tools: McpHub | None = None
    focus: ToolFocus = field(default_factory=ToolFocus)
    reflector: Reflector | None = None
    thinker: Thinker | None = None
    memory_dir: Path = Path("memoria")
    _stop: threading.Event = field(default_factory=threading.Event)

    def watch_network(self, interval: float, keep_warm: bool = False) -> None:
        """Vigila la conexión en segundo plano: al detectar un corte, precarga el modelo local.

        Con `keep_warm`, además mantiene el modelo local siempre en RAM (si sobra memoria).
        """
        if self.connectivity is None:
            return
        tick = self.warmer.warm if keep_warm and self.warmer else None
        if tick:
            tick()
        watch(self.connectivity, interval, self._stop, on_tick=tick)

    def close(self) -> None:
        self._stop.set()
        if self.tools is not None:
            self.tools.close()
        self.local.close()
        self.store.close()


def build(
    settings: Settings,
    force_offline: bool = False,
    only: list[str] | None = None,
    with_tools: bool = True,
) -> Brain:
    """Arma la cascada.

    `force_offline` simula no tener internet y `only` deja solo las capas nombradas (evals).
    """
    store = EventStore(settings.events_db)
    local = OllamaClient(settings.ollama_host, timeout=settings.timeout)
    # La nube de Ollama pasa por el daemon local (sesión de `ollama signin`), salvo que haya
    # OLLAMA_API_KEY: entonces se habla directo con ollama.com.
    cloud_key = settings.ollama_api_key
    if cloud_key:
        cloud_client = OllamaClient(
            settings.cloud_host, timeout=60.0, api_key=cloud_key.get_secret_value()
        )
        cloud = OllamaCloudProvider(cloud_client, settings.cloud_model, direct=True)
    else:
        cloud = OllamaCloudProvider(local, settings.cloud_model)
    claude_key = settings.anthropic_api_key

    def breaker() -> CircuitBreaker:
        return CircuitBreaker(settings.breaker_failures, settings.breaker_cooldown_s)

    host, _, port = settings.connectivity_probe.rpartition(":")
    probe = (lambda: False) if force_offline else (lambda: tcp_probe(host, int(port)))
    # Al detectar un corte se precarga el modelo local con las mismas opciones del chat, para que
    # la primera respuesta sin internet no espere la carga del modelo en la RAM.
    warmer = LocalWarmer(
        local,
        settings.local_model,
        {"num_ctx": settings.num_ctx, "num_gpu": settings.num_gpu},
        system_prompt=session_prompt(settings.memory_dir, store),
    )
    connectivity = Connectivity(probe, ttl=settings.connectivity_ttl_s, on_offline=warmer.warm)
    local_provider = OllamaLocalProvider(
        local, settings.local_model, settings.num_ctx, settings.num_gpu
    )
    layers = [
        Layer(
            cloud,
            settings.cloud_first_token_s,
            breaker(),
        ),
        Layer(
            ClaudeProvider(
                claude_key.get_secret_value() if claude_key else None,
                settings.claude_model,
                effort=settings.claude_effort,
            ),
            settings.claude_first_token_s,
            breaker(),
            claude_budget_gate(
                lambda: store.month_tokens("claude"),
                settings.claude_usd_per_mtok_in,
                settings.claude_usd_per_mtok_out,
                settings.claude_monthly_usd,
            ),
        ),
        # Sin tiempo máximo: es la última capa y cargar el modelo en CPU puede tardar.
        Layer(local_provider, None, breaker()),
    ]
    if only:
        layers = [layer for layer in layers if layer.provider.name in only]
    tools = build_tools(settings, connectivity, breaker) if with_tools else None
    if tools is not None and settings.local_auto_recall:
        local_provider.recall = lambda text: recall_context(tools, text)
    brain = Brain(Cascade(layers, connectivity), store, local, connectivity, warmer, tools)
    brain.memory_dir = settings.memory_dir
    if tools is not None:
        brain.thinker = thinker(settings, local, threading.Event())
    if tools is not None and settings.reflection:
        brain.reflector = build_reflector(settings, local)
    return brain


def thinker(settings: Settings, client: OllamaClient, cancel: threading.Event) -> Thinker:
    # El mismo modelo del chat: así la reflexión nunca obliga a cargar un segundo modelo.
    options = {"num_ctx": settings.num_ctx, "num_gpu": settings.num_gpu}
    return Thinker(client, settings.local_model, options, cancel)


def build_reflector(settings: Settings, client: OllamaClient) -> Reflector:
    """Prepara la reflexión; usa la memoria directamente, desde su propia conexión."""
    os.environ["TAMA_DATA_DIR"] = str(settings.data_dir.resolve())
    os.environ["TAMA_MEMORY_DIR"] = str(settings.memory_dir.resolve())

    def run(task, cancel: threading.Event) -> None:
        store = EventStore(settings.events_db)
        try:
            task(thinker(settings, client, cancel), store, settings.memory_dir)
        finally:
            store.close()

    reflector = Reflector(
        lambda cancel: run(reflect, cancel),
        settings.reflection_idle_min * 60,
        lambda: enough_ram(settings.reflection_min_free_gb),
    )
    reflector.close_days = lambda: reflector.run_now(lambda cancel: run(close_days, cancel))
    return reflector


def build_tools(settings: Settings, connectivity: Connectivity, breaker) -> McpHub | None:
    """Lanza los servidores MCP de `config/mcp.toml` y espera su chequeo de salud."""
    if not settings.mcp_enabled or not settings.mcp_config.exists():
        return None
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    hub = McpHub(
        load_config(settings.mcp_config),
        server_env(settings.data_dir, settings.workdir, settings.memory_dir),
        online=connectivity.online,
        start_timeout_s=settings.tool_start_timeout_s,
        call_timeout_s=settings.tool_timeout_s,
        breaker_factory=breaker,
        max_result_chars=settings.tool_result_max_chars,
        errlog=open(settings.data_dir / "mcp.log", "a", encoding="utf-8"),  # noqa: SIM115
    )
    hub.start()
    return hub
