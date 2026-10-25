"""Proveedores sobre la API de Ollama: el Ollama local del PC y la nube de Ollama."""

import json
import time
from collections.abc import Callable, Iterator
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
        except OllamaError as exc:
            self._on_error(exc)
            yield Error(str(exc))
        except ValueError as exc:
            yield Error(str(exc))

    def _on_error(self, exc: OllamaError) -> None:
        pass

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
    """Modelos de la nube de Ollama.

    Por defecto pasa por el Ollama del PC, que usa la sesión iniciada con `ollama signin` (sin
    API key; el modelo lleva el sufijo "-cloud"). Con `direct=True` habla con ollama.com usando
    OLLAMA_API_KEY, y ahí el modelo va sin el sufijo.
    """

    name = "nube"
    needs_network = True

    def __init__(
        self,
        client: OllamaClient,
        model: str,
        direct: bool = False,
        retry_after_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(client, model)
        self._direct = direct
        self._retry_after_s = retry_after_s
        self._clock = clock
        self._unauthorized_at: float | None = None

    def unavailable_reason(self) -> str | None:
        if self._unauthorized_at is None:
            return None
        if self._clock() - self._unauthorized_at >= self._retry_after_s:
            self._unauthorized_at = None  # se vuelve a intentar: quizá ya inició sesión
            return None
        if self._direct:
            return "OLLAMA_API_KEY rechazada"
        return "sesión de Ollama no iniciada (ejecuta `ollama signin`)"

    def _on_error(self, exc: OllamaError) -> None:
        if exc.status == 401:
            self._unauthorized_at = self._clock()

    @property
    def _api_model(self) -> str:
        return self.model.removesuffix("-cloud") if self._direct else self.model
