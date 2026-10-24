"""Proveedores simulados para probar la cascada y la CLI sin modelos ni red."""

import time
from collections.abc import Iterator

from tama.providers import Done, Error, Event, Text


class FakeProvider:
    """Responde con un guion fijo de eventos y cuenta cuántas veces lo llamaron."""

    def __init__(
        self,
        name: str,
        script: list[Event] | None = None,
        needs_network: bool = False,
        missing: str | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.needs_network = needs_network
        self.script = script if script is not None else [Text(f"hola desde {name}"), Done(10, 5)]
        self.missing = missing
        self.delay = delay
        self.calls: list[list[dict]] = []

    def unavailable_reason(self) -> str | None:
        return self.missing

    def stream_chat(self, messages, tools=None) -> Iterator[Event]:
        self.calls.append(list(messages))
        if self.delay:
            time.sleep(self.delay)
        yield from self.script


def failing(name: str, needs_network: bool = True) -> FakeProvider:
    return FakeProvider(name, [Error(f"{name} caído")], needs_network=needs_network)


def mid_stream_failure(name: str) -> FakeProvider:
    return FakeProvider(name, [Text("empiezo a res"), Error("se cortó")], needs_network=True)


class FakeConnectivity:
    def __init__(self, online: bool = True) -> None:
        self._online = online
        self.invalidated = 0

    def online(self) -> bool:
        return self._online

    def invalidate(self) -> None:
        self.invalidated += 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now
