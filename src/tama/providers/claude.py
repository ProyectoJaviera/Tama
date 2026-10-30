"""Proveedor de Claude con el SDK oficial `anthropic`."""

from collections.abc import Iterator
from typing import Any

import anthropic
from anthropic import DefaultHttpxClient

from tama.net import tls_context
from tama.providers import Done, Error, Event, Message, Text, Tool, ToolCall


class ClaudeProvider:
    name = "claude"
    needs_network = True

    def __init__(
        self,
        api_key: str | None,
        model: str,
        max_tokens: int = 4096,
        client: Any | None = None,
        effort: str = "low",
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._client = client
        if self._client is None and api_key:
            self._client = anthropic.Anthropic(
                api_key=api_key,
                # La cascada ya cambia de capa ante un fallo: sin reintentos internos que demoren.
                max_retries=0,
                timeout=anthropic.Timeout(60.0, connect=5.0),
                http_client=DefaultHttpxClient(verify=tls_context()),
            )

    def unavailable_reason(self) -> str | None:
        return None if self._client else "falta ANTHROPIC_API_KEY"

    def stream_chat(
        self, messages: list[Message], tools: list[Tool] | None = None
    ) -> Iterator[Event]:
        system, turns = to_claude_messages(messages)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "messages": turns,
            # Haiku 5.5 razona por defecto con effort "medium"; para charlar basta "low" y responde
            # antes. Sin temperature/top_p/top_k: la API los rechaza desde el 21-10-2026.
            "output_config": {"effort": self._effort},
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = [to_claude_tool(t) for t in tools]
        try:
            with self._client.messages.stream(**params) as stream:
                for event in stream:
                    if event.type == "text":
                        yield Text(event.text)
                final = stream.get_final_message()
            if final.stop_reason == "refusal":
                yield Error("Claude se negó a responder")
                return
            for block in final.content:
                if block.type == "tool_use" and final.stop_reason != "max_tokens":
                    yield ToolCall(block.name, dict(block.input), block.id)
            yield Done(final.usage.input_tokens, final.usage.output_tokens)
        except anthropic.APIError as exc:
            yield Error(f"Claude: {exc}")
        except ValueError as exc:
            # JSON de herramienta imposible de interpretar (ver S2, bucle de herramientas).
            yield Error(f"Claude: respuesta inválida ({exc})")


def to_claude_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Del formato común al de Claude.

    Los mensajes de sistema van aparte; las llamadas a herramientas pasan a bloques `tool_use` y
    sus resultados (rol "tool") a bloques `tool_result` dentro de un turno del usuario.
    """
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "content": m["content"],
            }
            last = turns[-1] if turns else None
            if last and last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append(block)  # todos los resultados en un solo turno
            else:
                turns.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for call in m["tool_calls"]:
                fn = call["function"]
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id", ""),
                        "name": fn["name"],
                        "input": fn.get("arguments") or {},
                    }
                )
            turns.append({"role": "assistant", "content": blocks})
        elif m["role"] in ("user", "assistant"):
            turns.append({"role": m["role"], "content": m["content"]})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)  # la API exige que el primer turno sea del usuario
    return system, turns


def to_claude_tool(tool: Tool) -> dict[str, Any]:
    """Del formato común (estilo Ollama/OpenAI) al formato de herramientas de Claude."""
    fn = tool["function"]
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        "eager_input_streaming": True,
    }
