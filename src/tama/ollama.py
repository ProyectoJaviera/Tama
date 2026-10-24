"""Cliente mínimo de la API HTTP de Ollama (sin dependencias extra)."""

import json
from collections.abc import Iterator
from typing import Any

import httpx

Message = dict[str, Any]


class OllamaError(RuntimeError):
    """Error al hablar con Ollama (servidor caído, modelo ausente, etc.)."""


class OllamaClient:
    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # trust_env=False: Ollama local no debe pasar por proxies del sistema.
        self._http = httpx.Client(
            base_url=host,
            timeout=httpx.Timeout(timeout, connect=5.0),
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def stream_chat(
        self,
        model: str,
        messages: list[Message],
        think: bool | None = None,
        options: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        """Envía un chat y devuelve los fragmentos de texto a medida que llegan."""
        payload = _chat_payload(model, messages, stream=True, think=think)
        if options:
            payload["options"] = options
        try:
            with self._http.stream("POST", "/api/chat", json=payload) as response:
                if response.is_error:
                    response.read()
                    raise OllamaError(_error_text(response))
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise OllamaError(chunk["error"])
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        return
        except httpx.ConnectError as exc:
            raise OllamaError(f"no se pudo conectar con Ollama: {exc}") from exc

    def chat(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        think: bool | None = None,
        options: dict[str, Any] | None = None,
        keep_alive: str | int | None = None,
    ) -> dict[str, Any]:
        """Chat sin streaming; devuelve la respuesta completa con las métricas de Ollama."""
        payload = _chat_payload(model, messages, stream=False, think=think)
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

    def unload(self, model: str) -> None:
        self._request("POST", "/api/generate", {"model": model, "keep_alive": 0})

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, json=payload)
        except httpx.ConnectError as exc:
            raise OllamaError(f"no se pudo conectar con Ollama: {exc}") from exc
        if response.is_error:
            raise OllamaError(_error_text(response))
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
