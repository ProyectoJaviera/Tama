"""Primer token del modelo local en una conversación como la demo, sin red.

Mide si Ollama reutiliza el prefijo del prompt entre turnos: con el prefijo estable, solo los
turnos que cargan el modelo deberían tardar más de unos segundos.

Uso:
    uv run python bench/first_token.py qwen3:4b-instruct-2507-q8_0
"""

import argparse
import os
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
from tama.providers import Text  # noqa: E402

TURNS = [
    "mi hermana se muda a Temuco en marzo",
    "¿dónde se muda mi hermana?",
    "en verdad se muda a Valdivia",
    "¿a qué ciudad se muda?",
    "me operaron de la rodilla",
    "me equivoqué, nunca me operaron de la rodilla",
    "recuérdame en 2 minutos regar las plantas",
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model")
    args = parser.parse_args()
    os.environ["TAMA_LOCAL_MODEL"] = args.model
    base = Path(tempfile.mkdtemp(prefix="tama-primer-token-"))
    settings = get_settings().model_copy(
        update={
            "local_model": args.model,
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
        }
    )
    brain = brain_module.build(settings, force_offline=True, only=["local"])
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    try:
        for text in TURNS:
            messages.append({"role": "user", "content": text})
            start = time.perf_counter()
            first = None
            tools = []
            for event in agent.run_turn(
                brain.cascade,
                brain.tools,
                history.trim(messages, settings.history_max_tokens),
                lambda call, info: True,
                settings.tools_max,
            ):
                if isinstance(event, agent.ToolUse):
                    tools.append(event.name)
                elif isinstance(event, Text) and first is None:
                    first = time.perf_counter() - start
                elif isinstance(event, agent.FinalAnswer):
                    messages.extend(event.trace)
            total = time.perf_counter() - start
            print(f"{first or 0:6.1f} s primer texto · {total:6.1f} s total · {text} -> {tools}")
    finally:
        brain.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
