"""Cliente mínimo de la API HTTP de Ollama (local o nube de Ollama)."""

import json
from collections.abc import Iterator
from typing import Any

import httpx

from tama.net import tls_context

Message = dict[str, Any]


class OllamaError(RuntimeError):
    """Error al hablar con Ollama (servidor caído, modelo ausente, etc.)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OllamaClient:
    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
        api_key: str | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        # trust_env=False: Ollama no debe pasar por proxies del sistema.
        self._http = httpx.Client(
            base_url=host,
            timeout=httpx.Timeout(timeout, connect=5.0),
            transport=transport,
            trust_env=False,
            headers=headers,
            verify=tls_context(),
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def stream_raw(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        think: bool | None = None,
        options: dict[str, Any] | None = None,
        format: dict[str, Any] | str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Chat en streaming: devuelve cada fragmento JSON tal como lo envía Ollama."""
        payload = _chat_payload(model, messages, stream=True, think=think)
        if format is not None:
            payload["format"] = format
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        try:
            with self._http.stream("POST", "/api/chat", json=payload) as response:
                if response.is_error:
                    response.read()
                    raise OllamaError(_error_text(response), response.status_code)
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise OllamaError(chunk["error"])
                    yield chunk
                    if chunk.get("done"):
                        return
        except httpx.HTTPError as exc:
            raise OllamaError(f"no se pudo hablar con Ollama: {exc}") from exc

    def stream_chat(
        self,
        model: str,
        messages: list[Message],
        think: bool | None = None,
        options: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        """Envía un chat y devuelve solo los fragmentos de texto a medida que llegan."""
        for chunk in self.stream_raw(model, messages, think=think, options=options):
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content

    def chat(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        think: bool | None = None,
        options: dict[str, Any] | None = None,
        keep_alive: str | int | None = None,
        format: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Chat sin streaming; devuelve la respuesta completa con las métricas de Ollama.

        `format` acepta "json" o un esquema JSON para forzar salida estructurada.
        """
        payload = _chat_payload(model, messages, stream=False, think=think)
        if format is not None:
            payload["format"] = format
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        return self._request("POST", "/api/chat", payload)

    def version(self) -> str:
        return self._request("GET", "/api/version").get("version", "?")

    def list_models(self) -> list[str]:
        return [m["name"] for m in self._request("GET", "/api/tags").get("models", [])]

    def running(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/ps").get("models", [])

    def load(
        self, model: str, options: dict[str, Any] | None = None, keep_alive: str = "30m"
    ) -> None:
        """Carga el modelo en RAM sin generar nada. `options` debe coincidir con las del chat
        (p. ej. `num_ctx`); si no, Ollama lo recarga en la primera petición."""
        payload: dict[str, Any] = {"model": model, "keep_alive": keep_alive}
        if options:
            payload["options"] = options
        self._request("POST", "/api/generate", payload)

    def unload(self, model: str) -> None:
        self._request("POST", "/api/generate", {"model": model, "keep_alive": 0})

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise OllamaError(f"no se pudo hablar con Ollama: {exc}") from exc
        if response.is_error:
            raise OllamaError(_error_text(response), response.status_code)
        return response.json()


def _chat_payload(
    model: str, messages: list[Message], stream: bool, think: bool | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
    if think is not None:
        payload["think"] = think
    return payload


def _error_text(response: httpx.Response) -> str:
    try:
        return response.json().get("error", response.text)
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
