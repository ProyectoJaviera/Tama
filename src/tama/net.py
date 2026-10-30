"""Red: contexto TLS del sistema y detección barata de conexión a internet."""

import socket
import ssl
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


def tcp_probe(host: str = "1.1.1.1", port: int = 443, timeout: float = 1.0) -> bool:
    """True si se puede abrir una conexión TCP: no envía datos ni resuelve DNS."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Connectivity:
    """Recuerda por `ttl` segundos si hay internet, para no probar en cada turno."""

    def __init__(
        self,
        probe: Callable[[], bool] = tcp_probe,
        ttl: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe = probe
        self._ttl = ttl
        self._clock = clock
        self._checked_at: float | None = None
        self._online = False

    def online(self) -> bool:
        now = self._clock()
        if self._checked_at is None or now - self._checked_at >= self._ttl:
            self._online = self._probe()
            self._checked_at = now
        return self._online

    def invalidate(self) -> None:
        """Fuerza un nuevo chequeo (p. ej., tras un error de red en una capa)."""
        self._checked_at = None
