"""`tama celular`: los pasos para usar Tama desde el celular, con los valores de este PC.

No escribe nada ni cambia la configuración: muestra los comandos de Tailscale y las líneas para el
`.env`, y propone un tópico de ntfy aleatorio. Si Tailscale está instalado, toma de ahí el nombre
del PC en la red privada.
"""

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

from tama.config import Settings

NTFY_HTTPS_PORT = 8443


WINDOWS_TAILSCALE = Path("C:/Program Files/Tailscale/tailscale.exe")


def tailscale_info() -> tuple[str, str]:
    """Nombre del PC en Tailscale (mi-pc.tail1234.ts.net) y la cuenta con que entró.

    Vacíos si Tailscale no está instalado o no responde.
    """
    exe = shutil.which("tailscale")
    if not exe and WINDOWS_TAILSCALE.exists():
        exe = str(WINDOWS_TAILSCALE)
    if not exe:
        return "", ""
    try:
        out = subprocess.run(
            [exe, "status", "--json"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
        data = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return "", ""
    me = data.get("Self", {})
    user = (data.get("User") or {}).get(str(me.get("UserID", "")), {})
    return me.get("DNSName", "").rstrip("."), user.get("LoginName", "")


def check_env(settings: Settings, name: str, login: str) -> list[str]:
    """Diferencias entre el .env y lo que dice Tailscale: la causa típica de un rechazo."""
    problems = []
    host = settings.web_tailscale_host.strip().rstrip(".").lower()
    if name and host != name.lower():
        problems.append(
            f"TAMA_WEB_TAILSCALE_HOST dice {host or '(vacío)'}, pero el PC en Tailscale es "
            f"{name}. Pon: TAMA_WEB_TAILSCALE_HOST={name}"
        )
    allowed = settings.web_tailscale_user.strip().lower()
    if login and allowed and allowed != login.lower():
        problems.append(
            f"TAMA_WEB_TAILSCALE_USER dice {allowed}, pero la cuenta de Tailscale es {login}. "
            f"Pon: TAMA_WEB_TAILSCALE_USER={login}"
        )
    public = settings_public_url()
    if name and public and name.lower() not in public.lower():
        problems.append(
            f"TAMA_NTFY_PUBLIC_URL no usa {name}. Pon: "
            f"TAMA_NTFY_PUBLIC_URL=https://{name}:{NTFY_HTTPS_PORT}"
        )
    return problems


def settings_public_url() -> str:
    """TAMA_NTFY_PUBLIC_URL: la lee docker compose, no Tama; aquí solo se revisa."""
    from dotenv import dotenv_values

    return (
        os.environ.get("TAMA_NTFY_PUBLIC_URL")
        or dotenv_values(".env").get("TAMA_NTFY_PUBLIC_URL", "")
        or ""
    )


def new_topic() -> str:
    """Tópico de ntfy difícil de adivinar: quien lo sepa puede leer y mandar avisos."""
    return f"tama-{secrets.token_urlsafe(24)}"


def guide(settings: Settings, name: str = "", topic: str = "") -> str:
    name = name or settings.web_tailscale_host or "<nombre-del-pc>.<tu-red>.ts.net"
    topic = settings.ntfy_topic or topic or new_topic()
    port = settings.web_port
    found = "" if "<" in name else " (detectado)"
    return f"""Tama en el celular

1. Tailscale (red privada; no se abre ningún puerto del router)
   - Instala Tailscale en el PC y en el celular, y entra con la misma cuenta en ambos.
   - En la consola de administración de Tailscale (DNS), activa MagicDNS y HTTPS Certificates.
     Si `tailscale serve` pide activar HTTPS, deja marcado HTTPS certificates y DESMARCA
     Tailscale Funnel: Funnel publicaría Tama a todo internet.
   - Nombre de este PC en Tailscale: {name}{found}

2. Publicar Tama solo dentro de Tailscale (en PowerShell, en el PC):
   tailscale serve --bg --https=443 http://127.0.0.1:{port}
   tailscale serve --bg --https={NTFY_HTTPS_PORT} http://127.0.0.1:{settings_ntfy_port()}
   tailscale serve status
   (Para dejar de publicar: tailscale serve reset)

3. Agrega al .env de Tama:
   TAMA_WEB_TAILSCALE_HOST={name}
   TAMA_WEB_TAILSCALE_USER=<tu correo de Tailscale>
   TAMA_NTFY_URL=http://127.0.0.1:{settings_ntfy_port()}
   TAMA_NTFY_TOPIC={topic}
   TAMA_NTFY_PUBLIC_URL=https://{name}:{NTFY_HTTPS_PORT}
   (Con Tama en Docker: TAMA_NTFY_URL=http://ntfy:80)

4. Levanta ntfy (en Docker) y Tama:
   docker compose --profile ntfy up -d ntfy     (solo ntfy; Tama con: uv run tama web)
   docker compose --profile ntfy up -d          (o ntfy y Tama, ambos en Docker)

5. En el celular:
   - Abre https://{name} con Tailscale conectado, también con datos móviles.
   - Instálala: en Chrome, menú > "Agregar a la pantalla principal"; en Safari, Compartir >
     "Agregar a inicio".
   - App ntfy: agrega el servidor https://{name}:{NTFY_HTTPS_PORT} y suscríbete al tópico de arriba.
   - En Tama, pestaña Estado > "Enviar aviso de prueba" (o /aviso-prueba en el chat).

Mientras no haya login (Sprint 10), entra solo quien esté en tu cuenta de Tailscale. Tama sigue
escuchando solo en 127.0.0.1: ningún puerto queda abierto a internet.
"""


def settings_ntfy_port() -> int:
    """Puerto de ntfy en el host, el mismo de docker-compose.yml (TAMA_NTFY_PORT)."""
    value = os.environ.get("TAMA_NTFY_PORT", "8090")
    return int(value) if value.isdigit() else 8090


def main(settings: Settings) -> int:
    name, login = tailscale_info()
    problems = check_env(settings, name, login)
    if problems:
        print("Revisa tu .env (y reinicia Tama después de cambiarlo):")
        print("\n".join(f"- {p}" for p in problems))
        print()
    elif name and settings.web_tailscale_host:
        print("Tu .env coincide con Tailscale.\n")
    print(guide(settings, name))
    return 0
