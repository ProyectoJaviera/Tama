"""Cliente de los servidores MCP locales: los lanza, vigila su salud y ejecuta sus herramientas.

El SDK de MCP es asíncrono; Tama no. El hub corre su propio bucle asyncio en un hilo aparte y
expone métodos síncronos. Cada servidor tiene su cortacircuitos: si falla, sus herramientas
desaparecen del prompt hasta que vuelva.
"""

import asyncio
import os
import sys
import threading
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tama.cascade import CircuitBreaker


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: list[str]
    requires_network: bool = False
    tools: list[str] | None = None
    """Si se indica, solo estas herramientas del servidor quedan a la vista del modelo."""


@dataclass(frozen=True)
class ToolInfo:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    destructive: bool = False

    def as_provider_tool(self) -> dict[str, Any]:
        """Formato común de herramientas (estilo Ollama/OpenAI); Claude lo convierte aparte."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    text: str


def load_config(path: Path) -> list[ServerConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    servers = []
    for name, entry in data.get("servidores", {}).items():
        command = [sys.executable if part == "python" else part for part in entry["comando"]]
        servers.append(
            ServerConfig(
                name,
                command,
                requires_network=entry.get("requiere_red", False),
                tools=entry.get("herramientas"),
            )
        )
    return servers


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n… (recortado: {len(text)} caracteres en total)"


@dataclass
class _Server:
    config: ServerConfig
    breaker: CircuitBreaker
    session: ClientSession | None = None
    tools: list[ToolInfo] = field(default_factory=list)
    error: str | None = None
    stop: asyncio.Event | None = None
    retry_at: float = 0.0


class McpHub:
    def __init__(
        self,
        configs: list[ServerConfig],
        env: dict[str, str],
        online: Callable[[], bool] = lambda: True,
        start_timeout_s: float = 15.0,
        call_timeout_s: float = 30.0,
        breaker_factory: Callable[[], CircuitBreaker] = CircuitBreaker,
        max_result_chars: int = 4000,
        errlog: TextIO | None = None,
        retry_after_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._retry_after_s = retry_after_s
        self._clock = clock
        self._servers = {c.name: _Server(c, breaker_factory()) for c in configs}
        self._env = env
        self._online = online
        self._start_timeout_s = start_timeout_s
        self._call_timeout_s = call_timeout_s
        self._max_result_chars = max_result_chars
        self._errlog = errlog or open(os.devnull, "w")  # noqa: SIM115  (vive lo que vive el hub)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="tama-mcp", daemon=True)
        self._thread.start()

    # --- ciclo de vida ---------------------------------------------------------------------

    def start(self) -> None:
        """Lanza los servidores y espera su chequeo de salud (list_tools con tiempo máximo)."""
        futures = [self._submit(self._connect(s)) for s in self._servers.values()]
        for future in futures:
            future.result()

    def close(self) -> None:
        for server in self._servers.values():
            if server.stop is not None:
                self._loop.call_soon_threadsafe(server.stop.set)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _connect(self, server: _Server) -> None:
        """Arranca el subproceso y deja la sesión abierta hasta que se pida detenerlo."""
        ready = asyncio.get_running_loop().create_future()
        server.stop = asyncio.Event()
        asyncio.get_running_loop().create_task(self._serve(server, ready))
        try:
            await asyncio.wait_for(asyncio.shield(ready), self._start_timeout_s)
        except Exception as exc:
            self._mark_failed(server, f"no arrancó: {exc or type(exc).__name__}")
            server.stop.set()

    async def _serve(self, server: _Server, ready: asyncio.Future) -> None:
        params = StdioServerParameters(
            command=server.config.command[0], args=server.config.command[1:], env=self._env
        )
        try:
            async with (
                stdio_client(params, errlog=self._errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                listed = await session.list_tools()
                server.tools = [_tool_info(server.config, t) for t in listed.tools]
                server.session, server.error = session, None
                server.breaker.success()
                if not ready.done():
                    ready.set_result(True)
                await server.stop.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                self._mark_failed(server, f"se cayó: {exc}")
        finally:
            server.session = None

    def _mark_failed(self, server: _Server, reason: str) -> None:
        server.session, server.tools, server.error = None, [], reason
        server.retry_at = self._clock() + self._retry_after_s
        server.breaker.failure()

    def _maybe_restart(self, server: _Server) -> None:
        """Si el cortacircuitos ya dejó pasar un intento, vuelve a lanzar el servidor caído."""
        ready = server.breaker.allow() and self._clock() >= server.retry_at
        if server.session is None and ready:
            if server.stop is not None:
                self._loop.call_soon_threadsafe(server.stop.set)
            self._submit(self._connect(server)).result()

    # --- consulta --------------------------------------------------------------------------

    def unavailable_reason(self, name: str) -> str | None:
        server = self._servers[name]
        if server.config.requires_network and not self._online():
            return "sin internet"
        if not server.breaker.allow():
            return f"fuera de servicio ({server.error or 'fallos repetidos'})"
        self._maybe_restart(server)
        if server.session is None:
            return server.error or "no conectado"
        return None

    def status(self) -> list[tuple[str, str, list[str]]]:
        """(servidor, estado, herramientas) para `/herramientas`."""
        result = []
        for name, server in self._servers.items():
            reason = self.unavailable_reason(name)
            result.append((name, reason or "listo", [t.name for t in self._visible(server)]))
        return result

    def available_tools(self, max_tools: int = 10) -> tuple[list[ToolInfo], list[str]]:
        """Herramientas a la vista en este turno y servidores no disponibles.

        Selector simple: servidores sanos en el orden de la configuración, hasta `max_tools`.
        """
        tools: list[ToolInfo] = []
        down: list[str] = []
        for name, server in self._servers.items():
            if self.unavailable_reason(name):
                down.append(name)
                continue
            for tool in self._visible(server):
                if len(tools) < max_tools:
                    tools.append(tool)
        return tools, down

    def find(self, tool_name: str) -> ToolInfo | None:
        for server in self._servers.values():
            for tool in server.tools:
                if tool.name == tool_name:
                    return tool
        return None

    @staticmethod
    def _visible(server: _Server) -> list[ToolInfo]:
        allowed = server.config.tools
        return [t for t in server.tools if allowed is None or t.name in allowed]

    # --- ejecución -------------------------------------------------------------------------

    def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolOutcome:
        tool = self.find(tool_name)
        if tool is None:
            return ToolOutcome(False, f"La herramienta {tool_name} no existe o no está disponible.")
        server = self._servers[tool.server]
        reason = self.unavailable_reason(tool.server)
        if reason:
            return ToolOutcome(False, f"El servidor {tool.server} no está disponible: {reason}.")
        try:
            result = self._submit(
                server.session.call_tool(
                    tool_name, arguments, read_timeout_seconds=self._call_timeout_s
                )
            ).result(self._call_timeout_s + 5)
        except Exception as exc:
            self._mark_failed(server, f"falló una llamada: {exc or type(exc).__name__}")
            return ToolOutcome(False, f"Falló {tool_name}: {exc or type(exc).__name__}")
        server.breaker.success()
        text = "\n".join(
            getattr(block, "text", "") for block in result.content if hasattr(block, "text")
        )
        is_error = bool(getattr(result, "is_error", False))
        return ToolOutcome(
            not is_error, truncate(text or "(sin contenido)", self._max_result_chars)
        )


def _tool_info(config: ServerConfig, tool: Any) -> ToolInfo:
    annotations = getattr(tool, "annotations", None)
    return ToolInfo(
        server=config.name,
        name=tool.name,
        description=tool.description or "",
        input_schema=tool.input_schema or {},
        destructive=bool(annotations and annotations.destructive_hint),
    )


def server_env(data_dir: Path, workdir: Path) -> dict[str, str]:
    """Entorno mínimo para los subprocesos (sin la variable SSLKEYLOGFILE de Avast)."""
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(
        TAMA_DATA_DIR=str(data_dir.resolve()),
        TAMA_WORKDIR=str(workdir.resolve()),
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    return env
