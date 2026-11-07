"""Ida y vuelta hablada: audio de la pregunta → whisper → Tama (nube) → voz de la respuesta.

Mide cada parte en frases cortas. La pregunta se genera con Piper (no hace falta micrófono). Sin
Claude y con la voz que esté configurada (Piper si no hay key de ElevenLabs).

    uv run python bench/voz_ida_vuelta.py
"""

import io
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tama  # noqa: E402,F401  (quita el SSLKEYLOGFILE de Avast)
from tama import agent, chat  # noqa: E402
from tama import brain as brain_module  # noqa: E402
from tama import voice as voice_module  # noqa: E402
from tama.config import get_settings  # noqa: E402
from tama.memory import session_prompt  # noqa: E402

PHRASES = [
    "Hola Tama, ¿cómo estás?",
    "¿Qué hora es?",
    "Dime un dato curioso corto.",
    "¿Me recomiendas algo para cenar?",
    "Gracias, eso es todo.",
]


def to_16k(speech: bytes) -> bytes:
    return speech  # whisper recibe el WAV y lo remuestrea (voice.wav_to_pcm)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    base = Path(tempfile.mkdtemp(prefix="tama-voz-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
            "local_preload": False,
        }
    )
    brain = brain_module.build(settings, only=["nube"])
    voice = voice_module.build(settings)
    voice.warm()
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    totals = []
    for phrase in PHRASES:
        question = voice.piper.speak(phrase).audio
        t0 = time.perf_counter()
        heard = voice.listener.transcribe(to_16k(question))
        t1 = time.perf_counter()
        answer = ""
        for event in chat.run_turn(
            brain, messages, heard, settings.history_max_tokens, settings.tools_max, lambda *_: True
        ):
            if isinstance(event, agent.FinalAnswer):
                answer = event.text
        t2 = time.perf_counter()
        speech = voice.speak(answer)
        t3 = time.perf_counter()
        with wave.open(io.BytesIO(question)) as w:
            said_s = w.getnframes() / w.getframerate()
        totals.append(t3 - t0)
        print(
            f"{phrase!r} ({said_s:.1f} s de audio): escuchar {t1 - t0:.2f} s, Tama {t2 - t1:.2f} s,"
            f" hablar ({speech.provider}) {t3 - t2:.2f} s = {t3 - t0:.2f} s | oyó: {heard!r}"
        )
    brain.close()
    print(
        f"Total: mediana {statistics.median(totals):.2f} s, máximo {max(totals):.2f} s (meta < 6 s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
