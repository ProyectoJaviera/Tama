"""Autenticador WebAuthn de software para los tests: firma como lo haría una passkey real.

Crea una clave P-256, arma authenticatorData y attestationObject ("none") y firma las aserciones,
así el servidor verifica de verdad con `webauthn`, sin simular la librería.
"""

import hashlib
import json
import secrets
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url


class SoftAuthenticator:
    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.count = 0

    def _client_data(self, kind: str, challenge: str, origin: str) -> bytes:
        data = {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}
        return json.dumps(data).encode()

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def create(self, options: dict[str, Any], origin: str) -> dict[str, Any]:
        rp_hash = hashlib.sha256(options["rp"]["id"].encode()).digest()
        attested = (
            bytes(16)
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self._cose_key()
        )
        auth_data = rp_hash + bytes([0x45]) + struct.pack(">I", self.count) + attested
        client = self._client_data("webauthn.create", options["challenge"], origin)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cid = bytes_to_base64url(self.credential_id)
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client),
                "attestationObject": bytes_to_base64url(attestation),
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict[str, Any], origin: str, rp_id: str) -> dict[str, Any]:
        self.count += 1
        rp_hash = hashlib.sha256(rp_id.encode()).digest()
        auth_data = rp_hash + bytes([0x05]) + struct.pack(">I", self.count)
        client = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256())
        )
        cid = bytes_to_base64url(self.credential_id)
        allowed = [c["id"] for c in options.get("allowCredentials", [])]
        assert not allowed or cid in allowed, "esta passkey no está entre las permitidas"
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": bytes_to_base64url(base64url_to_bytes("dGFtYS1yZW56bw")),
            },
            "clientExtensionResults": {},
        }
