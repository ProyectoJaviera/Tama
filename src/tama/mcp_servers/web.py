"""Lanza el servidor oficial mcp-server-fetch confiando en los certificados del sistema.

Con antivirus que inspeccionan HTTPS (Avast), los certificados de `certifi` no bastan.
"""

import os

import truststore

if os.environ.get("SSLKEYLOGFILE", "").startswith("\\\\.\\"):
    del os.environ["SSLKEYLOGFILE"]  # ver tama/__init__.py
truststore.inject_into_ssl()

from mcp_server_fetch import main  # noqa: E402  (después de preparar SSL)

if __name__ == "__main__":
    main()
