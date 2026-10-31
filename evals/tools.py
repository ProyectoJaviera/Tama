"""Evals de herramientas: escenarios de `evals/herramientas/*.yaml` con los servidores MCP reales.

Cada capa se prueba por separado y con datos de ejemplo frescos. El resultado es objetivo: qué
herramientas llamó el modelo, con qué argumentos, si pidió confirmación antes de una acción
destructiva y si se abstuvo cuando no hacía falta ninguna.

    uv run python -m evals.tools --capas nube local   # una corrida por capa
    uv run python -m evals.tools --capas local --offline      # sin red (la web queda fuera)
"""

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from tama import agent, prompts
from tama import brain as brain_module
from tama.cascade import Answered
from tama.config import get_settings
from tama.providers import Error

EVALS_DIR = Path(__file__).parent
SCENARIOS_DIR = EVALS_DIR / "herramientas"

SEED_NOTES = [
    ("Dentista", "Hora con el dentista el martes a las 9:00"),
    ("Viaje a Chiloé", "Salir el 5 de enero y reservar cabaña en Castro"),
    ("Impresora 3D", "Cambiar la boquilla de 0.4 mm y calibrar la cama"),
]
SEED_FILES = {
    "plan.md": "# Plan del sábado\nImprimir el soporte del teléfono y ordenar el taller.\n",
    "tareas.txt": "Tareas:\n- pagar la luz (urgente, vence mañana)\n- regar las plantas\n",
    "presupuesto.md": "# Presupuesto de noviembre\nFilamento: 25.000\nLuz: 40.000\n",
}


@dataclass
class Expected:
    herramienta: str
    argumentos: dict[str, str] = field(default_factory=dict)

    def matches(self, name: str, args: dict[str, Any]) -> bool:
        if name != self.herramienta:
            return False
        for key, needle in self.argumentos.items():
            haystack = " ".join(map(str, args.values())) if key == "*" else str(args.get(key, ""))
            if needle.lower() not in haystack.lower():
                return False
        return True


@dataclass
class Scenario:
    id: str
    mensaje: str
    espera: list[Expected]
    destructiva: bool = False
    requiere_red: bool = False
    respuesta: str | None = None
    rechazo: bool = False
    """Tras decir que no, la respuesta debe dejar claro que la acción no se hizo."""


@dataclass
class Result:
    scenario: str
    ok: bool
    motivo: str
    llamadas: list[str]
    confirmaciones: list[str]
    respuesta: str
    capa: str | None
    ttft_s: float | None
    total_s: float | None


def load_scenarios() -> list[Scenario]:
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["espera"] = [Expected(**e) for e in data.get("espera") or []]
        scenarios.append(Scenario(**data))
    return scenarios


def seed(data_dir: Path, workdir: Path) -> None:
    """Notas y archivos de ejemplo, en una carpeta temporal nueva para cada capa."""
    from tama.mcp_servers import notas

    old = os.environ.get("TAMA_DATA_DIR")
    os.environ["TAMA_DATA_DIR"] = str(data_dir)
    try:
        for title, body in SEED_NOTES:
            notas.create_note(title, body)
    finally:
        if old is None:
            os.environ.pop("TAMA_DATA_DIR", None)
        else:
            os.environ["TAMA_DATA_DIR"] = old
    workdir.mkdir(parents=True, exist_ok=True)
    for name, content in SEED_FILES.items():
        (workdir / name).write_text(content, encoding="utf-8")


def reports_rejection(answer: str) -> bool:
    """Criterio de la eval: dice que no se hizo y no da a entender que sigue en trámite."""
    text = answer.lower()
    negated = any(
        w in text for w in ("no se ", "no la ", "no lo ", "no borr", "no cambi", "sigue", "cancel")
    )
    misleading = any(w in text for w in ("ya mandé", "confirma en el sistema", "solicitud"))
    return negated and not misleading


def voseo_count(answers: list[str]) -> int:
    """Formas de voseo en las respuestas (Tama debe tutear)."""
    forms = r"\b(querés|podés|tenés|sabés|hacés|decime|mirá|fijate|contame|sos|vos)\b"
    return sum(len(re.findall(forms, a.lower())) for a in answers)


def evaluate(s: Scenario, calls: list[tuple[str, dict]], confirms: list[str], answer: str) -> str:
    """Devuelve "" si el escenario se cumple, o el motivo del fallo."""
    if not s.espera:
        return f"llamó {', '.join(n for n, _ in calls)}" if calls else ""
    if s.destructiva:
        target = s.espera[-1].herramienta
        if target not in confirms:
            return f"no pidió confirmación para {target} (llamó {[n for n, _ in calls] or 'nada'})"
        if s.rechazo and not reports_rejection(answer):
            return f"tras el rechazo no dijo que no se hizo: {answer[:80]!r}"
        return ""
    position = 0
    for name, args in calls:
        if s.espera[position].matches(name, args):
            position += 1
            if position == len(s.espera):
                break
    if position < len(s.espera):
        missing = s.espera[position]
        called = [n for n, _ in calls] or "nada"
        return f"faltó {missing.herramienta} {missing.argumentos or ''} (llamó {called})"
    if s.respuesta and s.respuesta.lower() not in answer.lower():
        return f"la respuesta no menciona '{s.respuesta}'"
    return ""


def run_scenario(brain, scenario: Scenario, max_tools: int) -> Result:
    calls: list[tuple[str, dict]] = []
    confirms: list[str] = []

    def confirm(call, info) -> bool:
        confirms.append(call.name)
        return False  # en las evals nunca se confirma: nada destructivo se ejecuta

    messages = [
        {"role": "system", "content": prompts.identity()},
        {"role": "user", "content": scenario.mensaje},
    ]
    answer, answered, error = "", None, None
    for event in agent.run_turn(brain.cascade, brain.tools, messages, confirm, max_tools):
        if isinstance(event, agent.ToolUse):
            calls.append((event.name, event.arguments))
        elif isinstance(event, Answered):
            answered = event
        elif isinstance(event, agent.FinalAnswer):
            answer = event.text
        elif isinstance(event, Error):
            error = event.message
    reason = error or evaluate(scenario, calls, confirms, answer)
    return Result(
        scenario.id,
        not reason,
        reason,
        [n for n, _ in calls],
        confirms,
        answer,
        answered.provider if answered else None,
        answered.ttft_s if answered else None,
        answered.total_s if answered else None,
    )


def run_layer(layer: str, offline: bool, scenarios: list[Scenario]) -> dict:
    base = Path(tempfile.mkdtemp(prefix=f"tama-evals-{layer}-"))
    settings = get_settings().model_copy(
        update={"data_dir": base / "data", "workdir": base / "trabajo", "connectivity_watch_s": 0}
    )
    seed(settings.data_dir, settings.workdir)
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    try:
        selected = [s for s in scenarios if not (offline and s.requiere_red)]
        results = []
        for scenario in selected:
            result = run_scenario(brain, scenario, settings.tools_max)
            results.append(result)
            mark = "ok" if result.ok else f"FALLA: {result.motivo}"
            print(f"[{layer}{' sin red' if offline else ''}] {scenario.id}: {mark}", flush=True)
    finally:
        brain.close()
    ok = sum(r.ok for r in results)
    ttfts = [r.ttft_s for r in results if r.ttft_s]
    return {
        "capa": layer,
        "sin_red": offline,
        "fecha": date.today().isoformat(),
        "casos": len(results),
        "correctos": ok,
        "pct": round(100 * ok / len(results), 1) if results else 0.0,
        "ttft_p50": statistics.median(ttfts) if ttfts else None,
        "voseo": voseo_count([r.respuesta for r in results]),
        "resultados": [asdict(r) for r in results],
    }


def render(data: dict) -> str:
    lines = [
        "# Evals de herramientas",
        "",
        "Generado por `uv run python -m evals.tools`. Servidores MCP reales con datos de ejemplo;",
        "las acciones destructivas nunca se confirman (cuenta como correcto que el modelo pida la",
        "herramienta y el sistema pida confirmación). Criterio de cierre: 90% o más por capa.",
        "",
        "| Capa | Correctos | % | Primer token p50 | Voseo |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, run in data.items():
        ttft = f"{run['ttft_p50']:.1f} s" if run["ttft_p50"] else "—"
        score = f"{run['correctos']}/{run['casos']} | {run['pct']:.0f}%"
        lines.append(f"| {key} | {score} | {ttft} | {run.get('voseo', '-')} |")
    lines += ["", "## Fallos", ""]
    for key, run in data.items():
        for r in run["resultados"]:
            if not r["ok"]:
                lines.append(f"- `{key}` · {r['scenario']}: {r['motivo']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Claude queda fuera por defecto: cuida el cupo mensual de la API.
    parser.add_argument("--capas", nargs="+", default=["nube", "local"])
    parser.add_argument("--con-claude", action="store_true", help="incluye la capa de Claude")
    parser.add_argument("--offline", action="store_true", help="simula no tener internet")
    args = parser.parse_args()

    out = EVALS_DIR / "herramientas-results.json"
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    scenarios = load_scenarios()
    for layer in args.capas:
        key = layer + (" (sin red)" if args.offline else "")
        data[key] = run_layer(layer, args.offline, scenarios)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (EVALS_DIR / "RESULTS_HERRAMIENTAS.md").write_text(render(data), encoding="utf-8")
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
