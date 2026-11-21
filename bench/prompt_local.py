"""Cuánto prompt procesa el modelo local en la precarga, y cuánto tarda en frío, según cuántas
herramientas ve (S13: arranque en frío).

    uv run python bench/prompt_local.py 18 10 6 0

Para cada valor: descarga el modelo, arma el prompt del primer turno sin red como lo hace Tama y
manda 1 token de respuesta; mide tokens del prompt, carga y procesamiento.
"""

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tama import agent  # noqa: E402
from tama import brain as brain_module  # noqa: E402
from tama.config import get_settings  # noqa: E402
from tama.memory import session_prompt  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    base = Path(tempfile.mkdtemp(prefix="tama-prompt-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
            "local_preload": False,
            "claude_enabled": False,
        }
    )
    brain = brain_module.build(settings, force_offline=True, only=["local"])
    model = settings.local_model
    options = brain_module.local_options(settings)
    print("| Herramientas a la vista | Tokens del prompt | Carga | Procesar prompt | Total |")
    print("|---:|---:|---:|---:|---:|")
    try:
        system = session_prompt(settings.memory_dir, brain.store)
        for limit in [int(a) for a in sys.argv[1:]]:
            messages = [{"role": "system", "content": system}, {"role": "user", "content": "hola"}]
            if limit:
                messages, tools = agent.prepare(brain.tools, messages, limit, brain.focus)
            else:
                tools = None
            brain.local._request("POST", "/api/generate", {"model": model, "keep_alive": 0})
            time.sleep(2)
            start = time.perf_counter()
            reply = brain.local.chat(
                model, messages, tools=tools, think=False, options={**options, "num_predict": 1}
            )
            total = time.perf_counter() - start
            load = reply.get("load_duration", 0) / 1e9
            prompt_s = reply.get("prompt_eval_duration", 0) / 1e9
            print(
                f"| {limit} | {reply.get('prompt_eval_count')} | {load:.1f} s | "
                f"{prompt_s:.1f} s | {total:.1f} s |",
                flush=True,
            )
    finally:
        brain.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
