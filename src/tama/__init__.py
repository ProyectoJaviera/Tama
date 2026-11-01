"""Tama: asistente de IA personal."""

import logging
import os

__version__ = "0.1.0"

# Avast pone SSLKEYLOGFILE apuntando a su driver (\\.\aswMonFltProxy\...). El módulo ssl de
# Python intenta abrirlo y OpenSSL aborta el proceso en Windows. Solo se quita en este proceso.
if os.environ.get("SSLKEYLOGFILE", "").startswith("\\\\.\\"):
    del os.environ["SSLKEYLOGFILE"]

# Los registros HTTP no deben aparecer en la consola mientras Renzo escribe.
for _name in ("httpx", "httpcore", "mcp"):
    logging.getLogger(_name).setLevel(logging.WARNING)
