"""Bucle de herramientas de un turno: el modelo pide herramientas, Tama las ejecuta y sigue.

El modelo decide con herramientas (regla 3): aquí no se interpreta el texto de Renzo. Las
herramientas destructivas solo corren con una confirmación explícita.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from tama.cascade import Answered, Cascade, Switch
from tama.providers import Error, Message, Text, ToolCall

MAX_STEPS = 5
MAX_RESULT_IN_HISTORY = 300
REJECTED = (
    "CANCELADO: Renzo respondió que NO. La acción NO se ejecutó y todo sigue exactamente igual. "
    "Dile claramente que no se hizo y que sigue como estaba. No pidas otra confirmación."
)


class ToolBox(Protocol):
    """Lo que el bucle necesita del hub de MCP (facilita los tests)."""

    def available_tools(self, max_tools: int = 10) -> tuple[list[Any], list[str]]: ...

    def find(self, tool_name: str) -> Any | None: ...

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class ToolUse:
    name: str
    arguments: dict[str, Any]
    server: str | None


@dataclass(frozen=True)
class ToolResult:
    name: str
    ok: bool
    text: str
    skipped: bool = False
    """True si Renzo no confirmó una acción destructiva y no se ejecutó."""


@dataclass(frozen=True)
class FinalAnswer:
    text: str
    trace: tuple[Message, ...] = ()
    """Llamadas a herramientas del turno. Quedan en el historial: si el modelo solo viera texto,
    imitaría esas respuestas y dejaría de usar herramientas (p. ej., de guardar en la memoria)."""


Confirm = Callable[[ToolCall, Any], bool]
AgentEvent = Text | Switch | Answered | Error | ToolUse | ToolResult | FinalAnswer


def with_tool_status(messages: list[Message], down: list[str]) -> list[Message]:
    """Avisa al modelo, en una línea, qué servidores de herramientas no están disponibles."""
    if not down or not messages or messages[0]["role"] != "system":
        return list(messages)
    notice = (
        "Herramientas no disponibles en este momento: "
        + ", ".join(down)
        + ". Si te piden algo que depende de ellas, dilo con claridad."
    )
    first = {**messages[0], "content": messages[0]["content"] + "\n\n" + notice}
    return [first, *messages[1:]]


def compact(trace: list[Message]) -> list[Message]:
    """Acorta los resultados de herramientas para que el historial siga corto."""
    return [
        {**m, "content": m["content"][:MAX_RESULT_IN_HISTORY]} if m["role"] == "tool" else m
        for m in trace
    ]


def prepare(
    tools: ToolBox | None, messages: list[Message], max_tools: int = 10
) -> tuple[list[Message], list[dict[str, Any]] | None]:
    """Mensajes y herramientas tal como llegarán al modelo en este turno.

    La precarga usa lo mismo: Ollama solo reaprovecha lo ya procesado si el prefijo es idéntico.
    """
    visible, down = tools.available_tools(max_tools) if tools else ([], [])
    return with_tool_status(messages, down), [t.as_provider_tool() for t in visible] or None


def run_turn(
    cascade: Cascade,
    tools: ToolBox | None,
    messages: list[Message],
    confirm: Confirm,
    max_tools: int = 10,
    max_steps: int = MAX_STEPS,
) -> Iterator[AgentEvent]:
    """Corre un turno completo. `messages` no se modifica: el bucle trabaja sobre una copia."""
    work, tool_specs = prepare(tools, messages, max_tools)
    start = len(work)

    for step in range(max_steps + 1):
        # El último paso va sin herramientas: el modelo debe responder con lo que tiene.
        specs = tool_specs if step < max_steps else None
        text: list[str] = []
        calls: list[ToolCall] = []
        for event in cascade.turn(work, specs):
            if isinstance(event, Text):
                text.append(event.text)
                yield event
            elif isinstance(event, ToolCall):
                calls.append(event)
            elif isinstance(event, Switch):
                text.clear()
                calls.clear()
                yield event
            elif isinstance(event, Answered):
                yield event
            elif isinstance(event, Error):
                yield event
                return
        if not calls:
            yield FinalAnswer("".join(text), tuple(compact(work[start:])))
            return

        ids = [call.id or f"call_{step}_{i}" for i, call in enumerate(calls)]
        work.append(
            {
                "role": "assistant",
                "content": "".join(text),
                "tool_calls": [
                    {"id": call_id, "function": {"name": c.name, "arguments": c.arguments}}
                    for call_id, c in zip(ids, calls, strict=True)
                ],
            }
        )
        for call_id, call in zip(ids, calls, strict=True):
            info = tools.find(call.name) if tools else None
            yield ToolUse(call.name, call.arguments, info.server if info else None)
            if info is not None and info.destructive and not confirm(call, info):
                result = ToolResult(call.name, False, REJECTED, skipped=True)
            elif tools is None:
                result = ToolResult(call.name, False, "No hay herramientas disponibles.")
            else:
                outcome = tools.call(call.name, call.arguments)
                result = ToolResult(call.name, outcome.ok, outcome.text)
            yield result
            work.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "tool_name": call.name,
                    "content": result.text,
                }
            )
