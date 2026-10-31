"""Red: contexto TLS del sistema y detección barata de conexión a internet."""

import socket
import ssl
import threading
import time
from collections.abc import Callable
from functools import cache

import truststore


@cache
def tls_context() -> ssl.SSLContext:
    """Contexto TLS que confía en el almacén de certificados del sistema operativo.

    Así funcionan los antivirus que inspeccionan HTTPS (Avast instala su propia CA en Windows).
    """
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def tcp_probe(
    host: str = "1.1.1.1", port: int = 443, timeout: float = 2.0, attempts: int = 2
) -> bool:
    """True si se puede abrir una conexión TCP: no envía datos ni resuelve DNS.

    Reintenta: con la CPU al 100% un intento puede fallar aunque haya red, y eso saltaría la nube.
    """
    for _ in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


class Connectivity:
    """Recuerda por `ttl` segundos si hay internet, para no probar en cada turno."""

    def __init__(
        self,
        probe: Callable[[], bool] = tcp_probe,
        ttl: float = 30.0,
        offline_ttl: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        on_offline: Callable[[], None] | None = None,
    ) -> None:
        self._probe = probe
        self._ttl = ttl
        self._offline_ttl = offline_ttl
        self._clock = clock
        self._checked_at: float | None = None
        self._online = False
        self._stale = False
        self._lock = threading.Lock()
        self.on_offline = on_offline
        """Se llama cuando se detecta un corte (de con red a sin red)."""

    def online(self) -> bool:
        with self._lock:
            now = self._clock()
            # Sin red se revisa más seguido, para volver a la nube apenas regrese la conexión.
            ttl = self._ttl if self._online else min(self._ttl, self._offline_ttl)
            fresh = self._checked_at is not None and now - self._checked_at < ttl
            if fresh and not self._stale:
                return self._online
            was_online = self._checked_at is not None and self._online
            self._online = self._probe()
            self._checked_at = now
            self._stale = False
            went_offline = was_online and not self._online
        if went_offline and self.on_offline:
            self.on_offline()
        return self._online

    def invalidate(self) -> None:
        """Fuerza un nuevo chequeo (p. ej., tras un error de red en una capa).

        Conserva el último estado conocido para poder detectar el paso de con red a sin red.
        """
        self._stale = True


def watch(
    connectivity: Connectivity,
    interval: float,
    stop: threading.Event,
    on_tick: Callable[[], None] | None = None,
) -> threading.Thread:
    """Revisa la conexión cada `interval` segundos en segundo plano hasta que se active `stop`.

    Así un corte se detecta aunque Renzo no esté escribiendo, y se puede preparar la capa local.
    """

    def loop() -> None:
        while not stop.wait(interval):
            connectivity.invalidate()
            connectivity.online()
            if on_tick:
                on_tick()

    thread = threading.Thread(target=loop, name="tama-red", daemon=True)
    thread.start()
    return thread
