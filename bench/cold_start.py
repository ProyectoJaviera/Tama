"""Arranque sin red: cuánto tarda Tama en responder la primera pregunta tras abrirse.

Descarga el modelo de la RAM (arranque en frío), arma Tama sin red como lo hace `tama web` y
mide: la precarga en segundo plano y la primera respuesta, cada una desde que se abrió Tama.
Con `--escribir-al-tiro` la pregunta se manda sin esperar la precarga (Renzo apurado).

Uso:
    uv run python bench/cold_start.py
    uv run python bench/cold_start.py --tibio    # el modelo ya estaba en RAM (keep_alive)
"""

import argparse
import contextlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tama  # noqa: E402,F401  (quita el SSLKEYLOGFILE de Avast)
from tama import agent, history  # noqa: E402
from tama import brain as brain_module  # noqa: E402
from tama.config import get_settings  # noqa: E402
from tama.memory import session_prompt  # noqa: E402
from tama.ollama import OllamaError  # noqa: E402
from tama.providers import Text  # noqa: E402

QUESTION = "hola Tama, ¿cómo estás?"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tibio", action="store_true", help="no descarga el modelo antes")
    parser.add_argument("--escribir-al-tiro", action="store_true", help="no espera la precarga")
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="tama-arranque-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
            "reflection": False,
            "proactive": False,
        }
    )
    if not args.tibio:
        brain = brain_module.build(settings, force_offline=True, only=["local"], with_tools=False)
        with contextlib.suppress(OllamaError):
            brain.local.unload(settings.local_model)
        brain.close()
    opened = time.perf_counter()
    brain = brain_module.build(settings, force_offline=True, only=["local"])
    built = time.perf_counter() - opened
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    limits = (settings.history_max_tokens, settings.tools_max)
    brain.warmer.context = lambda: agent.prepare(
        brain.tools, history.trim(messages, limits[0]), limits[1], brain.focus
    )
    brain.warmer.warm()
    if not args.escribir_al_tiro:
        brain.warmer.thread.join()
    warmed = time.perf_counter() - opened
    messages.append({"role": "user", "content": QUESTION})
    asked = time.perf_counter()
    first = None
    for event in agent.run_turn(
        brain.cascade, brain.tools, messages, lambda *_: False, limits[1], focus=brain.focus
    ):
        if first is None and isinstance(event, Text):
            first = time.perf_counter() - opened
    done = time.perf_counter() - opened
    brain.close()
    print(f"modelo: {settings.local_model} ({'tibio' if args.tibio else 'frío'})")
    print(f"Tama armada: {built:.1f} s")
    print(f"precarga lista: {warmed:.1f} s")
    print(f"primer token: {first or 0:.1f} s (pregunta enviada a los {asked - opened:.1f} s)")
    print(f"respuesta completa: {done:.1f} s desde que se abrió Tama")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
