"""Prueba rápida sin red: Tama responde, no se cae y llama herramientas. No mide calidad.

uv run python -m evals.smoke
"""

import sys
import tempfile
from pathlib import Path

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from evals.memoria import ask
from tama import brain as brain_module
from tama.config import get_settings
from tama.memory import session_prompt

# Mensaje y herramienta que debe usar; None si basta con que responda.
CASES = [
    ("Hola Tama, ¿cómo estás?", None),
    ("¿Qué hora es?", "get_current_time"),
    ("Anota en una nota que debo comprar pan.", "create_note"),
    ("Mi perro se llama Bruno.", "create_entities"),
    ("Dime algo breve sobre el mar.", None),
]


def run() -> list[str]:
    base = Path(tempfile.mkdtemp(prefix="tama-smoke-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
        }
    )
    brain = brain_module.build(settings, force_offline=True, only=["local"])
    system = session_prompt(settings.memory_dir, brain.store)
    limits = (settings.history_max_tokens, settings.tools_max)
    failures = []
    try:
        for text, tool in CASES:
            turn = ask(brain, [{"role": "system", "content": system}], text, *limits)
            ok = bool(turn["respuesta"].strip()) and (tool is None or tool in turn["herramientas"])
            print(f"{'ok' if ok else 'FALLA'}: {text} -> {turn['herramientas']}", flush=True)
            if not ok:
                failures.append(text)
    finally:
        brain.close()
    return failures


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    failures = run()
    print(f"{len(CASES) - len(failures)}/{len(CASES)} casos")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
