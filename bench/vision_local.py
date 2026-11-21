"""Banco de visión local (S13): modelos de Ollama con visión, solo CPU, sin red.

    uv run python bench/vision_local.py gemma4:e4b ministral-3:8b nemotron-3.5-bolt:e4b

Tres pruebas con imágenes ficticias (dibujos con rótulos y una página escaneada): una foto, cuatro
fotos juntas y leer el texto de un escaneo. Mide el tiempo y cuenta palabras clave (sin juez).
Las respuestas quedan en bench/vision/<modelo>.json para juzgarlas después con Claude.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.sentidos import drawing, manual_pdf, workshop_photo  # noqa: E402
from tama.attachments import Senses, for_vision, pdf_page_image  # noqa: E402
from tama.config import get_settings  # noqa: E402
from tama.mcp_servers.memoria import normalize  # noqa: E402
from tama.ollama import OllamaClient  # noqa: E402

OUT = ROOT / "bench" / "vision"
GB = 1024**3


def tests() -> list[dict]:
    four = [workshop_photo(), drawing("cielo"), drawing("semaforo"), drawing("casa")]
    return [
        {
            "id": "una-foto",
            "images": [workshop_photo()],
            "prompt": "Describe la foto: qué se ve, el lugar y los detalles útiles.",
            "words": ["martillo", "destornillador", "llave", "caja", "taller"],
        },
        {
            "id": "cuatro-juntas",
            "images": four,
            "prompt": "Fotos, en orden: tablero-taller.jpg, cielo-azul.png, semaforo.jpg, "
            "casa-roja.png.\nDescribe cada foto en orden, nombrándola, en 2 a 4 frases.",
            "words": ["martillo", "cielo", "sol", "semaforo", "casa", "roj"],
        },
        {
            "id": "escaneo",
            "images": [pdf_page_image(manual_pdf(), 7)],
            "prompt": "Transcribe el texto de esta página, tal cual.",
            "words": ["neumaticos", "32 psi", "30 psi", "pagina 7"],
        },
    ]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    settings = get_settings().model_copy(update={"vision_cloud": False, "claude_enabled": False})
    client = OllamaClient(settings.ollama_host, timeout=900)
    cases = tests()
    for model in sys.argv[1:]:
        senses = Senses(
            settings.model_copy(update={"vision_local_model": model}), None, lambda: False
        )
        rows = []
        for case in cases:
            images = [for_vision(i) for i in case["images"]]
            start = time.perf_counter()
            try:
                text = senses._ask_local(model, case["prompt"], images)
                error = None
            except Exception as exc:  # un modelo que falla no corta el banco
                text, error = "", f"{type(exc).__name__}: {exc}"
            seconds = round(time.perf_counter() - start, 1)
            vram = sum(m.get("size_vram", 0) for m in client.running())
            if vram:
                raise SystemExit(f"{model} usa {vram / GB:.2f} GB de GPU: se detiene.")
            ram = next((m.get("size", 0) for m in client.running() if m["name"] == model), 0)
            plain = normalize(text)
            hits = [w for w in case["words"] if normalize(w) in plain]
            rows.append(
                {
                    "caso": case["id"],
                    "segundos": seconds,
                    "ram_modelo_gb": round(ram / GB, 1),
                    "palabras": f"{len(hits)}/{len(case['words'])}",
                    "error": error,
                    "respuesta": text,
                }
            )
            print(
                f"[{model}] {case['id']}: {seconds} s, {len(hits)}/{len(case['words'])}", flush=True
            )
        name = model.replace(":", "_").replace("/", "_")
        (OUT / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        client._request("POST", "/api/generate", {"model": model, "keep_alive": 0})
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
