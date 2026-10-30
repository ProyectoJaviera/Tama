"""Precarga del modelo local para que la primera respuesta sin internet no espere la carga."""

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

from tama.ollama import OllamaClient, OllamaError


class LocalWarmer:
    """Carga el modelo local en RAM en un hilo aparte, como mucho una vez cada `min_interval`."""

    def __init__(
        self,
        client: OllamaClient,
        model: str,
        options: dict[str, Any],
        min_interval: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        system_prompt: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._options = options
        self._system_prompt = system_prompt
        self._min_interval = min_interval
        self._clock = clock
        self._last: float | None = None
        self._lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.context: Callable[[], tuple[list[dict], list[dict] | None]] | None = None
        """Da los mensajes y herramientas del próximo turno; lo define la CLI durante el chat."""

    def warm(self) -> bool:
        """Lanza la precarga; devuelve False si se hizo hace poco o ya está en curso."""
        with self._lock:
            now = self._clock()
            busy = self.thread is not None and self.thread.is_alive()
            if busy or (self._last is not None and now - self._last < self._min_interval):
                return False
            self._last = now
            self.thread = threading.Thread(target=self._load, name="tama-precarga", daemon=True)
            self.thread.start()
            return True

    def _load(self) -> None:
        # Si Ollama no responde, la cascada lo avisará en el siguiente turno.
        with contextlib.suppress(OllamaError):
            if self.context is not None:
                messages, tools = self.context()
            elif self._system_prompt:
                messages, tools = [{"role": "system", "content": self._system_prompt}], None
            else:
                self._client.load(self._model, self._options)
                return
            # En CPU, procesar la identidad, las herramientas y la conversación toma varios
            # segundos. Una petición de 1 token con el mismo prefijo que tendrá el próximo turno
            # lo deja en la caché de Ollama, y la primera respuesta real lo reusa.
            self._client.chat(
                self._model,
                [*messages, {"role": "user", "content": "."}],
                tools=tools,
                think=False,
                options={**self._options, "num_predict": 1},
                keep_alive="30m",
            )
