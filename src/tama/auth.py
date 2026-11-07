"""Passkeys (WebAuthn) y sesiones para entrar a Tama desde fuera del PC (Sprint 10).

Todo es local: las claves públicas, las sesiones y los códigos de alta viven en `data/acceso.db` y
la verificación la hace `webauthn` en el mismo proceso, sin servicios externos ni internet.

- Una passkey se da de alta solo con un código de un solo uso que muestra el PC
  (`tama passkey nueva` o la pestaña Estado en el PC), con caducidad corta.
- Una sesión es una cookie con un token aleatorio; en la base solo queda su hash, así que se puede
  revocar (sola, o todas las de una passkey al revocarla).
"""

import hashlib
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS passkeys (
    id INTEGER PRIMARY KEY,
    credential_id TEXT NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used TEXT,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    passkey_id INTEGER NOT NULL REFERENCES passkeys(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS enroll_codes (
    code_hash TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
"""
CODE_MINUTES = 10
CHALLENGE_S = 300
USER_ID = b"tama-renzo"
RP_NAME = "Tama"


class AuthError(Exception):
    """Algo no cuadra en un alta o un inicio de sesión; el mensaje es para Renzo."""


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Passkey:
    id: int
    name: str
    created_at: str
    last_used: str | None


class Access:
    """Passkeys, códigos de alta y sesiones de Tama."""

    def __init__(self, path: Path, clock: Callable[[], datetime] = _now) -> None:
        self.path = path
        self.clock = clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)
        self._challenges: dict[str, tuple[bytes, float, str]] = {}
        self._lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def _stamp(self, delta: timedelta = timedelta(0)) -> str:
        return (self.clock() + delta).isoformat(timespec="seconds")

    # --- códigos de alta -------------------------------------------------------------------

    def new_code(self, minutes: int = CODE_MINUTES) -> str:
        """Código de un solo uso para dar de alta una passkey desde otro dispositivo."""
        code = f"{secrets.randbelow(10**8):08d}"
        with self._db() as db:
            db.execute(
                "INSERT INTO enroll_codes (code_hash, expires_at) VALUES (?, ?)",
                (_hash(code), self._stamp(timedelta(minutes=minutes))),
            )
        return code

    def _check_code(self, code: str, spend: bool) -> None:
        code = "".join(ch for ch in code if ch.isdigit())
        with self._db() as db:
            row = db.execute(
                "SELECT expires_at, used_at FROM enroll_codes WHERE code_hash = ?", (_hash(code),)
            ).fetchone()
            if row is None or row["used_at"]:
                raise AuthError("El código no es válido. Pide uno nuevo en el PC.")
            if row["expires_at"] < self._stamp():
                raise AuthError("El código caducó. Pide uno nuevo en el PC.")
            if spend:
                db.execute(
                    "UPDATE enroll_codes SET used_at = ? WHERE code_hash = ?",
                    (self._stamp(), _hash(code)),
                )

    # --- desafíos ----------------------------------------------------------------------------

    def _remember(self, challenge: bytes, purpose: str) -> str:
        flow = secrets.token_urlsafe(16)
        now = time.monotonic()
        with self._lock:
            self._challenges = {
                k: v for k, v in self._challenges.items() if now - v[1] < CHALLENGE_S
            }
            self._challenges[flow] = (challenge, now, purpose)
        return flow

    def _take(self, flow: str, purpose: str) -> bytes:
        with self._lock:
            found = self._challenges.pop(flow, None)
        if found is None or found[2] != purpose or time.monotonic() - found[1] >= CHALLENGE_S:
            raise AuthError("La solicitud caducó. Vuelve a intentarlo.")
        return found[0]

    # --- alta ----------------------------------------------------------------------------------

    def registration_options(self, rp_id: str, code: str | None) -> tuple[str, str]:
        """Opciones para crear una passkey. Fuera del PC hace falta un código vigente."""
        if code is not None:
            self._check_code(code, spend=False)
        existing = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p)) for p in self._credential_ids()
        ]
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=RP_NAME,
            user_id=USER_ID,
            user_name="Renzo",
            exclude_credentials=existing,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        return self._remember(options.challenge, "alta"), options_to_json(options)

    def register(
        self,
        flow: str,
        credential: dict[str, Any],
        rp_id: str,
        origin: str,
        name: str,
        code: str | None,
    ) -> Passkey:
        challenge = self._take(flow, "alta")
        if code is not None:
            self._check_code(code, spend=True)
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
            )
        except Exception as exc:  # la librería usa varias excepciones propias
            raise AuthError(f"No pude verificar la passkey: {exc}") from exc
        name = (name or "").strip()[:60] or "Passkey"
        with self._db() as db:
            number = db.execute(
                "INSERT INTO passkeys (credential_id, public_key, sign_count, name, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    bytes_to_base64url(verified.credential_id),
                    verified.credential_public_key,
                    verified.sign_count,
                    name,
                    self._stamp(),
                ),
            ).lastrowid
        return Passkey(number, name, self._stamp(), None)

    # --- inicio de sesión ---------------------------------------------------------------------

    def login_options(self, rp_id: str) -> tuple[str, str]:
        allowed = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p)) for p in self._credential_ids()
        ]
        if not allowed:
            raise AuthError("Todavía no hay passkeys. Da de alta una con un código del PC.")
        options = generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=allowed,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return self._remember(options.challenge, "entrar"), options_to_json(options)

    def login(
        self, flow: str, credential: dict[str, Any], rp_id: str, origin: str, days: float
    ) -> str:
        """Verifica la passkey y abre una sesión; devuelve el token de la cookie."""
        challenge = self._take(flow, "entrar")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM passkeys WHERE credential_id = ? AND revoked_at IS NULL",
                (str(credential.get("id", "")),),
            ).fetchone()
        if row is None:
            raise AuthError("Esa passkey no está registrada o fue revocada.")
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=row["public_key"],
                credential_current_sign_count=row["sign_count"],
            )
        except Exception as exc:
            raise AuthError(f"No pude verificar la passkey: {exc}") from exc
        token = secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute(
                "UPDATE passkeys SET sign_count = ?, last_used = ? WHERE id = ?",
                (verified.new_sign_count, self._stamp(), row["id"]),
            )
            db.execute(
                "INSERT INTO sessions (token_hash, passkey_id, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (_hash(token), row["id"], self._stamp(), self._stamp(timedelta(days=days))),
            )
        return token

    def valid_session(self, token: str) -> bool:
        if not token:
            return False
        with self._db() as db:
            row = db.execute(
                "SELECT s.expires_at FROM sessions s JOIN passkeys p ON p.id = s.passkey_id"
                " WHERE s.token_hash = ? AND s.revoked_at IS NULL AND p.revoked_at IS NULL",
                (_hash(token),),
            ).fetchone()
        return row is not None and row["expires_at"] > self._stamp()

    def logout(self, token: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (self._stamp(), _hash(token)),
            )

    # --- administración (solo desde el PC) ---------------------------------------------------

    def _credential_ids(self) -> list[str]:
        with self._db() as db:
            rows = db.execute(
                "SELECT credential_id FROM passkeys WHERE revoked_at IS NULL"
            ).fetchall()
        return [r["credential_id"] for r in rows]

    def passkeys(self) -> list[Passkey]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id, name, created_at, last_used FROM passkeys"
                " WHERE revoked_at IS NULL ORDER BY id"
            ).fetchall()
        return [Passkey(r["id"], r["name"], r["created_at"], r["last_used"]) for r in rows]

    def revoke(self, passkey_id: int) -> bool:
        """Revoca la passkey y cierra todas sus sesiones. Queda registrada, no se borra."""
        with self._db() as db:
            changed = db.execute(
                "UPDATE passkeys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (self._stamp(), passkey_id),
            ).rowcount
            db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE passkey_id = ? AND revoked_at IS NULL",
                (self._stamp(), passkey_id),
            )
        return bool(changed)
