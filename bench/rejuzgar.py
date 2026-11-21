"""Juzga con Claude las respuestas guardadas del banco local (nunca el modelo local).

    uv run python bench/rejuzgar.py --con-claude bench/local/<modelo>/conversacion-r13-*.json

Puntúa cada escenario de conversación que quedó sin juicio y reescribe el archivo con los puntajes.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tama  # noqa: E402, F401  (quita el SSLKEYLOGFILE de Avast)
from evals import runner  # noqa: E402
from evals.__main__ import claude_judge  # noqa: E402
from tama.config import get_settings  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    files = [Path(a) for a in sys.argv[1:] if not a.startswith("--")]
    settings = get_settings()
    if not settings.claude_enabled or settings.anthropic_api_key is None:
        print("Falta la key de Claude o la capa está apagada (usa --con-claude).")
        return 1
    scenarios = {s.id: s for s in runner.load_scenarios()}
    judge, used, close = claude_judge(settings)
    try:
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            outcomes = [runner.Outcome(**o) for o in data["resultados"]]
            for outcome in outcomes:
                if outcome.scenario not in scenarios:
                    continue
                if outcome.error and not outcome.error.startswith("juez:"):
                    continue  # Tama no respondió: no hay nada que puntuar
                outcome.error, outcome.puntajes = None, []
                runner.apply_judgement(scenarios[outcome.scenario], outcome, judge)
            data["juez"] = ", ".join(used)
            data["resumen"] = runner.summarize(outcomes)
            data["resultados"] = [asdict(o) for o in outcomes]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            summary = data["resumen"]
            print(
                f"{path.parent.name}: media {summary.get('puntaje_medio')}, "
                f"aprobados {summary.get('aprobados')}/{summary.get('escenarios')}",
                flush=True,
            )
    finally:
        close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
