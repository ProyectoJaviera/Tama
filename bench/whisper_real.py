"""Whisper local con la voz real de Renzo (S13): base, small y small con audio_ctx.

1. Renzo graba frases con Tama y `TAMA_VOICE_DEBUG_DIR` puesto (con internet: el .txt de cada
   audio lo escribe Scribe, que sirve de referencia).
2. `uv run python bench/whisper_real.py <carpeta de voz-debug>`

Mide el error por palabra (WER) contra la referencia y el tiempo de cada modelo, en CPU.
"""

import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tama import voice  # noqa: E402

MODELS = [
    ("base", "ggml-base-q5_1.bin", False),
    ("small", "ggml-small-q5_1.bin", False),
    ("small+audio_ctx", "ggml-small-q5_1.bin", True),
]


def words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9ñ]+", plain)


def wer(reference: str, hypothesis: str) -> float:
    """Distancia de edición por palabras, dividida por las palabras de la referencia."""
    ref, hyp = words(reference), words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1] / max(1, len(ref))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    folder = Path(sys.argv[1])
    clips = [
        (w, w.with_suffix(".txt").read_text(encoding="utf-8"))
        for w in sorted(folder.glob("*.wav"))
        if w.with_suffix(".txt").exists()
    ]
    if not clips:
        print(f"No hay audios con su .txt en {folder}.")
        return 1
    print(f"{len(clips)} audios. Referencia: el .txt de cada uno (Scribe, con internet).\n")
    print("| Modelo | WER medio | Frases exactas | Tiempo medio |")
    print("|---|---:|---:|---:|")
    for name, file, fit in MODELS:
        listener = voice.Listener(ROOT / "models" / "voz" / file, threads=6, fit_audio=fit)
        listener.transcribe(clips[0][0].read_bytes())  # carga
        errors, exact, seconds = [], 0, []
        for wav, reference in clips:
            start = time.perf_counter()
            text = listener.transcribe(wav.read_bytes())
            seconds.append(time.perf_counter() - start)
            errors.append(wer(reference, text))
            exact += words(reference) == words(text)
        print(
            f"| {name} | {sum(errors) / len(errors):.0%} | {exact}/{len(clips)} | "
            f"{sum(seconds) / len(seconds):.1f} s |",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
