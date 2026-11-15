"""Descarga UNA vez los modelos de voz (al instalar, con internet): whisper.cpp y Piper.

Tama nunca descarga nada en ejecución (regla 2): si falta un modelo, lo dice y sigue sin voz.
Cada archivo tiene versión y hash fijos; si el hash no coincide, se borra y se avisa.

    uv run python scripts/descargar_voz.py            # lo que usa Tama por defecto
    uv run python scripts/descargar_voz.py --todos    # también el whisper "base", para comparar
"""

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tama.net import tls_context  # noqa: E402

WHISPER = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_MX/ald/medium"
# (archivo, url, sha256, por defecto)
FILES = [
    (
        "ggml-small-q5_1.bin",
        f"{WHISPER}/ggml-small-q5_1.bin",
        "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
        True,
    ),
    (
        "ggml-base-q5_1.bin",
        f"{WHISPER}/ggml-base-q5_1.bin",
        "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
        False,
    ),
    (
        "es_MX-ald-medium.onnx",
        f"{PIPER}/es_MX-ald-medium.onnx",
        "019b3803293c93e34a206dd2e53a3889209a514e786fd7144f7b70196c579b63",
        True,
    ),
    (
        "es_MX-ald-medium.onnx.json",
        f"{PIPER}/es_MX-ald-medium.onnx.json",
        "efab736e62e5321dd5d063d1b46e63c59ce655419816355b81d025c1a8d6b03c",
        True,
    ),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path) -> None:
    partial = target.with_suffix(target.suffix + ".parcial")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60, verify=tls_context()) as r:
        r.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in r.iter_bytes(1 << 20):
                handle.write(chunk)
    partial.replace(target)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--carpeta", default=str(ROOT / "models" / "voz"))
    parser.add_argument("--todos", action="store_true", help="también los modelos opcionales")
    args = parser.parse_args()
    folder = Path(args.carpeta)
    folder.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, url, expected, default in FILES:
        if not (default or args.todos):
            continue
        target = folder / name
        if not target.exists():
            print(f"Descargando {name}…", flush=True)
            download(url, target)
        found = sha256(target)
        if expected and found != expected:
            target.unlink()
            print(f"{name}: el hash no coincide; lo borré. Vuelve a intentarlo.")
            ok = False
        else:
            print(f"{name}: listo ({target.stat().st_size // 1_000_000} MB) sha256={found}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
