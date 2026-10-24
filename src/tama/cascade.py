"""Cerebro en cascada: nube de Ollama → Claude → Ollama local.

Cada capa tiene un tiempo máximo hasta el primer token y un cortacircuitos. Si una capa falla
(antes o a mitad de la respuesta) se repite el turno completo en la siguiente. Sin internet, las
capas de red se saltan sin esperar timeouts.
"""

import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from tama.net import Connectivity
from tama.providers import Done, Error, Event, Message, Provider, Text, Tool, ToolCall


class CircuitBreaker:
    """Cerrado → (N fallos seguidos) abierto → (tras `cooldown`) medio abierto → 1 intento."""

    def __init__(
        self,
        threshold: int = 3,
        cooldown: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._cooldown = cooldown
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "cerrado"
        if self._clock() - self._opened_at >= self._cooldown:
            return "medio abierto"
        return "abierto"

    def allow(self) -> bool:
        return self.state != "abierto"

    def success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        if self.state == "medio abierto":
            self._opened_at = self._clock()  # el intento de prueba falló: vuelve a abrirse
            return
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()


class FirstTokenTimeout(Exception):
    pass


_END = object()


def first_token_timeout(events: Iterator[Event], timeout: float | None) -> Iterator[Event]:
    """Reenvía los eventos, pero falla si el primer evento no llega en `timeout` segundos.

    El proveedor corre en un hilo aparte para poder abandonarlo si se queda colgado.
    """
    if timeout is None:
        yield from events
        return
    channel: queue.Queue = queue.Queue()
    stop = threading.Event()

    def pump() -> None:
        try:
            for event in events:
                if stop.is_set():
                    break
                channel.put(event)
        except Exception as exc:  # un proveedor no debería lanzar, pero por si acaso
            channel.put(Error(str(exc)))
        finally:
            channel.put(_END)

    threading.Thread(target=pump, daemon=True).start()
    try:
        try:
            item = channel.get(timeout=timeout)
        except queue.Empty:
            raise FirstTokenTimeout(f"sin respuesta en {timeout:g} s") from None
        while item is not _END:
            yield item
            item = channel.get()
    finally:
        stop.set()


@dataclass
class Layer:
    provider: Provider
    first_token_timeout: float | None = None
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    gate: Callable[[], str | None] = lambda: None
    """Condición extra para usar la capa (p. ej., el tope de gasto); devuelve el motivo si no."""


@dataclass(frozen=True)
class Switch:
    """La capa `frm` no respondió y el turno sigue en `to`."""

    frm: str
    to: str
    reason: str
    partial: bool
    """True si la capa alcanzó a escribir parte de la respuesta antes de fallar."""


@dataclass(frozen=True)
class Answered:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    ttft_s: float | None
    total_s: float


TurnEvent = Text | ToolCall | Switch | Answered | Error


class Cascade:
    def __init__(
        self,
        layers: list[Layer],
        connectivity: Connectivity,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.layers = layers
        self._connectivity = connectivity
        self._clock = clock

    def skip_reason(self, layer: Layer) -> str | None:
        provider = layer.provider
        reason = provider.unavailable_reason() or layer.gate()
        if reason:
            return reason
        if not layer.breaker.allow():
            return "cortacircuitos abierto"
        if provider.needs_network and not self._connectivity.online():
            return "sin internet"
        return None

    def status(self) -> list[tuple[Layer, str]]:
        """Estado de cada capa, para `/proveedor`."""
        result = []
        for layer in self.layers:
            reason = self.skip_reason(layer)
            state = layer.breaker.state
            result.append((layer, reason or ("lista" if state == "cerrado" else state)))
        return result

    def turn(self, messages: list[Message], tools: list[Tool] | None = None) -> Iterator[TurnEvent]:
        failed: tuple[str, str, bool] | None = None  # (capa, motivo, parcial) pendiente de avisar
        for layer in self.layers:
            if self.skip_reason(layer):
                continue
            name = layer.provider.name
            if failed:
                yield Switch(failed[0], name, failed[1], failed[2])
                failed = None
            start = self._clock()
            ttft: float | None = None
            reason: str | None = None
            try:
                for event in first_token_timeout(
                    layer.provider.stream_chat(messages, tools), layer.first_token_timeout
                ):
                    if isinstance(event, Error):
                        reason = event.message
                        break
                    if isinstance(event, Done):
                        layer.breaker.success()
                        yield Answered(
                            name,
                            layer.provider.model,
                            event.input_tokens,
                            event.output_tokens,
                            ttft,
                            self._clock() - start,
                        )
                        return
                    if ttft is None:
                        ttft = self._clock() - start
                    yield event
                else:
                    reason = "la respuesta terminó sin cerrarse"
            except FirstTokenTimeout as exc:
                reason = str(exc)
            layer.breaker.failure()
            if layer.provider.needs_network:
                self._connectivity.invalidate()
            failed = (name, reason or "error", ttft is not None)
        detail = f" (último error en {failed[0]}: {failed[1]})" if failed else ""
        yield Error(f"ninguna capa pudo responder{detail}")


def claude_budget_gate(
    month_tokens: Callable[[], tuple[int, int]],
    price_in_per_mtok: float,
    price_out_per_mtok: float,
    monthly_usd: float,
) -> Callable[[], str | None]:
    """Desactiva la capa de Claude cuando el gasto del mes supera el tope."""

    def gate() -> str | None:
        tokens_in, tokens_out = month_tokens()
        spent = (tokens_in * price_in_per_mtok + tokens_out * price_out_per_mtok) / 1_000_000
        if spent >= monthly_usd:
            return f"tope mensual alcanzado (US$ {spent:.2f} de {monthly_usd:.2f})"
        return None

    return gate
