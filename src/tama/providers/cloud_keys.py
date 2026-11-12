"""Capa nube con varias API keys de Ollama Cloud, en orden (Sprint 11).

Llama directo a https://ollama.com/api/chat con `Authorization: Bearer <key>`, sin pasar por el
Ollama del PC. Usa la key 1; si Ollama responde que la cuota o el límite se agotaron, la marca
como agotada y prueba la 2 en la MISMA petición, y luego la 3. Si ninguna sirve, la cascada baja
a la capa siguiente (Claude y luego local).

- Agotada: 429 (límite o cuota) o 402 (sin saldo). Rechazada: 401 o 403 (key inválida).
  Ambas se dejan de lado por un enfriamiento (por defecto 1 h, o lo que diga Retry-After) y luego
  se vuelven a preferir en orden (1 > 2 > 3).
- Un error de red o un 5xx no marca la key: es pasajero y lo maneja el cortacircuitos de la capa.
- El estado vive en `data/nube_keys.json` (sin las keys), así sobrevive a un reinicio.
- Las keys nunca van a logs, errores ni Estado: se nombran por su número.
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from tama.ollama import OllamaClient, OllamaError
from tama.providers import Error, Event, Message, Tool
from tama.providers.ollama import chat_events

QUOTA = {429: "agotada", 402: "agotada", 401: "rechazada", 403: "rechazada"}


class KeyRing:
    """Qué keys están disponibles. Persiste hasta cuándo queda de lado cada una."""

    def __init__(
        self,
        count: int,
        path: Path | None,
        cooldown_s: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.count = count
        self.path = path
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = self._read()

    def _read(self) -> dict[str, dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._state), encoding="utf-8")

    def usable(self) -> list[int]:
        """Números de key (desde 1) que se pueden probar ahora, en orden."""
        now = self.clock()
        with self._lock:
            return [
                n
                for n in range(1, self.count + 1)
                if self._state.get(str(n), {}).get("until", 0) <= now
            ]

    def set_aside(self, number: int, reason: str, retry_after_s: float | None = None) -> None:
        wait = retry_after_s if retry_after_s and retry_after_s > 0 else self.cooldown_s
        with self._lock:
            self._state[str(number)] = {"until": self.clock() + wait, "reason": reason}
            self._write()

    def recovered(self, number: int) -> None:
        with self._lock:
            if self._state.pop(str(number), None) is not None:
                self._write()

    def status(self) -> list[str]:
        """Una línea por key: "key 1: activa", "key 2: agotada hasta 14:30"."""
        now = self.clock()
        lines = []
        with self._lock:
            for n in range(1, self.count + 1):
                entry = self._state.get(str(n), {})
                if entry.get("until", 0) > now:
                    hour = datetime.fromtimestamp(entry["until"]).strftime("%H:%M")
                    lines.append(f"key {n}: {entry.get('reason', 'agotada')} hasta {hour}")
                else:
                    lines.append(f"key {n}: activa")
        return lines


class OllamaCloudKeys:
    """La capa "nube": Ollama Cloud con varias keys en orden."""

    name = "nube"
    needs_network = True

    def __init__(
        self,
        clients: list[OllamaClient],
        model: str,
        ring: KeyRing,
        only: int = 0,
    ) -> None:
        self._clients = clients
        # En ollama.com el modelo va sin el sufijo "-cloud" (ese es para el Ollama del PC).
        self.model = model
        self._api_model = model.removesuffix("-cloud")
        self.ring = ring
        self.only = only
        """Con un número, usa solo esa key (p. ej. para las evals)."""

    def _candidates(self) -> list[int]:
        usable = self.ring.usable()
        return [n for n in usable if n == self.only] if self.only else usable

    def unavailable_reason(self) -> str | None:
        if not self._clients:
            return "sin API keys de Ollama Cloud (ver README, capa nube)"
        if self.only and self.only > len(self._clients):
            return f"no hay key {self.only} de Ollama Cloud"
        if not self._candidates():
            return "todas las API keys de Ollama Cloud están agotadas o rechazadas por ahora"
        return None

    def stream_chat(
        self, messages: list[Message], tools: list[Tool] | None = None
    ) -> Iterator[Event]:
        tried = []
        for number in self._candidates():
            client = self._clients[number - 1]
            started = False
            try:
                for event in chat_events(client, self._api_model, messages, tools, None, None):
                    started = True
                    yield event
                self.ring.recovered(number)
                return
            except OllamaError as exc:
                kind = QUOTA.get(exc.status or 0)
                if kind is None or started:
                    # Red, 5xx u otro error: no es de la key; lo maneja el cortacircuitos.
                    yield Error(f"Ollama Cloud (key {number}): {safe(exc)}")
                    return
                self.ring.set_aside(number, kind, getattr(exc, "retry_after", None))
                tried.append(f"key {number} {kind}")
            except ValueError as exc:
                yield Error(str(exc))
                return
        yield Error("Ollama Cloud: " + (", ".join(tried) or "ninguna key disponible"))

    def keys_status(self) -> list[str]:
        if not self._clients:
            return ["sin API keys configuradas"]
        return self.ring.status()


def safe(exc: OllamaError) -> str:
    """El mensaje de error sin nada que se parezca a una key."""
    text = str(exc)
    return f"error {exc.status}" if exc.status else text.split(":", 1)[0]
