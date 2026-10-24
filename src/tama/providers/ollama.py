"""Proveedores sobre la API de Ollama: el Ollama local del PC y la nube de Ollama."""

import json
from collections.abc import Iterator
from typing import Any

from tama.ollama import OllamaClient, OllamaError
from tama.providers import Done, Error, Event, Message, Text, Tool, ToolCall


class _OllamaProvider:
    name = "ollama"
    needs_network = False

    def __init__(
        self,
        client: OllamaClient,
        model: str,
        options: dict[str, Any] | None = None,
        think: bool | None = None,
    ) -> None:
        self._client = client
        self.model = model
        self._options = options
        self._think = think

    def unavailable_reason(self) -> str | None:
        return None

    def stream_chat(
        self, messages: list[Message], tools: list[Tool] | None = None
    ) -> Iterator[Event]:
        try:
            for chunk in self._client.stream_raw(
                self._api_model, messages, tools=tools, think=self._think, options=self._options
            ):
                message = chunk.get("message", {})
                if message.get("content"):
                    yield Text(message["content"])
                for call in message.get("tool_calls") or []:
                    fn = call.get("function", {})
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                    yield ToolCall(fn.get("name", ""), args)
                if chunk.get("done"):
                    yield Done(chunk.get("prompt_eval_count", 0), chunk.get("eval_count", 0))
                    return
            yield Error("Ollama cortó la respuesta sin terminar")
        except (OllamaError, ValueError) as exc:
            yield Error(str(exc))

    @property
    def _api_model(self) -> str:
        return self.model


class OllamaLocalProvider(_OllamaProvider):
    """Ollama nativo del PC: solo CPU (`num_gpu` 0) y contexto acotado."""

    name = "local"
    needs_network = False

    def __init__(self, client: OllamaClient, model: str, num_ctx: int, num_gpu: int = 0) -> None:
        super().__init__(
            client, model, options={"num_ctx": num_ctx, "num_gpu": num_gpu}, think=False
        )


class OllamaCloudProvider(_OllamaProvider):
    """API de la nube de Ollama (ollama.com) con OLLAMA_API_KEY."""

    name = "nube"
    needs_network = True

    def __init__(self, client: OllamaClient | None, model: str) -> None:
        super().__init__(client, model)  # type: ignore[arg-type]

    def unavailable_reason(self) -> str | None:
        return None if self._client else "falta OLLAMA_API_KEY"

    @property
    def _api_model(self) -> str:
        # El sufijo "-cloud" solo existe al usar la nube a través del Ollama local; la API de
        # ollama.com usa el nombre base (gemma4:31b-cloud → gemma4:31b).
        return self.model.removesuffix("-cloud")
