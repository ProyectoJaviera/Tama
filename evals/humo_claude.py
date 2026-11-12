"""Prueba de humo con Claude como capa principal: 5 mensajes y su costo real en US$.

Conversación, una herramienta (la hora) y memoria (guardar y recordar), en una memoria nueva.
Usa SOLO la capa de Claude y gasta cupo real: corre con --con-claude.

    uv run python -m evals.humo_claude --con-claude
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from evals.memoria import ask
from tama import brain as brain_module
from tama.config import get_settings
from tama.memory import session_prompt

EVALS_DIR = Path(__file__).parent
MESSAGES = [
    "Hola Tama, ¿cómo estás?",
    "¿Qué hora es?",
    "Mi perro se llama Bruno y tiene 4 años.",
    "¿Cómo se llama mi perro?",
    "Gracias, eso es todo por ahora.",
]
# Turnos aproximados de cada suite con modelo (sin contar al juez), para estimar su costo.
SUITE_TURNS = {
    "conversación (evals)": 22,
    "herramientas (evals.tools)": 25,
    "memoria (evals.memoria)": 26,
    "historial, corrección y rectificación": 16,
    "objetivos (evals.reflexion objetivos)": 12,
    "pulido (evals.pulido)": 15,
    "remota (evals.remoto)": 6,
}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--con-claude", action="store_true", help="obligatorio: gasta cupo real")
    args = parser.parse_args()
    if not args.con_claude:
        print("Esta prueba gasta cupo de Claude: córrela con --con-claude.")
        return 2
    base = Path(tempfile.mkdtemp(prefix="tama-humo-claude-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
            "local_preload": False,
            "claude_enabled": True,
        }
    )
    brain = brain_module.build(settings, only=["claude"])
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    limits = (settings.history_max_tokens, settings.tools_max)
    rows = []
    before = 0.0
    for text in MESSAGES:
        turn = ask(brain, messages, text, *limits)
        spent = brain.spend.month_usd()
        rows.append(
            {
                "mensaje": text,
                "capa": turn["capa"],
                "herramientas": turn["herramientas"],
                "costo_usd": round(spent - before, 6),
                "respuesta": turn["respuesta"],
            }
        )
        print(f"US$ {spent - before:.5f} [{turn['capa']}] {text} -> {turn['herramientas']}")
        print(f"    {turn['respuesta'][:140]}")
        before = spent
    brain.close()
    total = before
    per_turn = total / len(MESSAGES)
    suite = sum(SUITE_TURNS.values())
    estimate = per_turn * suite
    print(f"\nTotal de la prueba: US$ {total:.4f} ({per_turn:.5f} por mensaje)")
    print(f"Estimación de la suite completa con Claude (~{suite} turnos): US$ {estimate:.2f}")
    result = {
        "total_usd": round(total, 5),
        "por_mensaje_usd": round(per_turn, 6),
        "suite_turnos": suite,
        "suite_estimada_usd": round(estimate, 3),
        "detalle": rows,
    }
    out = EVALS_DIR / "humo-claude-results.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
