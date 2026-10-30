"""Arma el cerebro en cascada a partir de la configuración."""

from dataclasses import dataclass

from tama.cascade import Cascade, CircuitBreaker, Layer, claude_budget_gate
from tama.config import Settings
from tama.events import EventStore
from tama.net import Connectivity, tcp_probe
from tama.ollama import OllamaClient
from tama.providers.claude import ClaudeProvider
from tama.providers.ollama import OllamaCloudProvider, OllamaLocalProvider


@dataclass
class Brain:
    cascade: Cascade
    store: EventStore
    local: OllamaClient

    def close(self) -> None:
        self.local.close()
        self.store.close()


def build(settings: Settings, force_offline: bool = False, only: list[str] | None = None) -> Brain:
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
    connectivity = Connectivity(probe, ttl=settings.connectivity_ttl_s)
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
        Layer(
            OllamaLocalProvider(local, settings.local_model, settings.num_ctx, settings.num_gpu),
            None,
            breaker(),
        ),
    ]
    if only:
        layers = [layer for layer in layers if layer.provider.name in only]
    return Brain(Cascade(layers, connectivity), store, local)
