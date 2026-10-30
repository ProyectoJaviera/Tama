"""Interfaz única para los proveedores de modelos (nube de Ollama, Claude y Ollama local).

Cada proveedor convierte su API a los mismos eventos, así la cascada y la CLI no saben con quién
hablan. Los errores no se lanzan: llegan como un evento `Error` para que la cascada decida.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

Message = dict[str, Any]
Tool = dict[str, Any]


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Done:
    input_tokens: int = 0
    output_tokens: int = 0
    generation_s: float | None = None
    """Segundos generando la salida, si el proveedor lo informa (Ollama: eval_duration)."""


@dataclass(frozen=True)
class Error:
    message: str


Event = Text | ToolCall | Done | Error


class Provider(Protocol):
    name: str
    """Nombre corto de la capa, visible para Renzo ("nube", "claude", "local")."""
    model: str
    needs_network: bool

    def unavailable_reason(self) -> str | None:
        """Por qué no se puede usar (p. ej., falta la API key), o None si está lista."""
        ...

    def stream_chat(
        self, messages: list[Message], tools: list[Tool] | None = None
    ) -> Iterator[Event]:
        """Envía la conversación y devuelve eventos; termina con `Done` o `Error`."""
        ...
