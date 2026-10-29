"""Evals de conversación: Tama responde los escenarios y un juez LLM los puntúa de 1 a 5."""

import json
import math
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from tama import prompts
from tama.cascade import Answered, Cascade, Switch
from tama.providers import Error, Message, Text

EVALS_DIR = Path(__file__).parent
SCENARIOS_DIR = EVALS_DIR / "escenarios"
# Criterio que se aplica a todos los escenarios, además de los propios.
GENERIC_CRITERION = "Responde en español natural y cercano (de Chile, neutro)"

JUDGE_SYSTEM = (
    "Eres un evaluador estricto y justo de las respuestas de Tama, un asistente personal en "
    "español. Recibes la conversación, la última respuesta de Tama y una lista de criterios. "
    "Puntúa cada criterio de 1 (no lo cumple) a 5 (lo cumple por completo), en el mismo orden. "
    "Responde solo con JSON."
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "puntajes": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 5}},
        "comentario": {"type": "string"},
    },
    "required": ["puntajes", "comentario"],
}


@dataclass
class Scenario:
    id: str
    descripcion: str
    mensaje: str
    criterios: list[str]
    previos: list[str] = field(default_factory=list)

    @property
    def all_criteria(self) -> list[str]:
        return [GENERIC_CRITERION, *self.criterios]


@dataclass
class Outcome:
    scenario: str
    respuesta: str = ""
    capa: str | None = None
    modelo: str | None = None
    ttft_s: float | None = None
    total_s: float | None = None
    cambios: list[str] = field(default_factory=list)
    puntajes: list[int] = field(default_factory=list)
    comentario: str = ""
    error: str | None = None

    @property
    def promedio(self) -> float | None:
        return statistics.mean(self.puntajes) if self.puntajes else None


def load_scenarios(directory: Path = SCENARIOS_DIR) -> list[Scenario]:
    scenarios = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        scenarios.append(Scenario(**data))
    return scenarios


def answer(cascade: Cascade, messages: list[Message], outcome: Outcome | None = None) -> str | None:
    """Corre un turno en la cascada; devuelve el texto final (o None si ninguna capa respondió)."""
    parts: list[str] = []
    for event in cascade.turn(messages):
        if isinstance(event, Text):
            parts.append(event.text)
        elif isinstance(event, Switch):
            parts.clear()
            if outcome:
                outcome.cambios.append(f"{event.frm} → {event.to}: {event.reason}")
        elif isinstance(event, Answered):
            if outcome:
                outcome.capa, outcome.modelo = event.provider, event.model
                outcome.ttft_s, outcome.total_s = event.ttft_s, event.total_s
            return "".join(parts)
        elif isinstance(event, Error):
            if outcome:
                outcome.error = event.message
            return None
    return None


def run_scenario(cascade: Cascade, scenario: Scenario) -> Outcome:
    outcome = Outcome(scenario.id)
    messages: list[Message] = [{"role": "system", "content": prompts.identity()}]
    for previous in scenario.previos:
        messages.append({"role": "user", "content": previous})
        reply = answer(cascade, messages)
        if reply is None:
            outcome.error = "falló un turno previo"
            return outcome
        messages.append({"role": "assistant", "content": reply})
    messages.append({"role": "user", "content": scenario.mensaje})
    outcome.respuesta = answer(cascade, messages, outcome) or ""
    return outcome


def judge_prompt(scenario: Scenario, outcome: Outcome) -> str:
    previous = "\n".join(f"Renzo: {p}\nTama: (respondió)" for p in scenario.previos)
    criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(scenario.all_criteria, 1))
    return (
        f"Conversación previa:\n{previous or '(ninguna)'}\n\n"
        f"Último mensaje de Renzo:\n{scenario.mensaje}\n\n"
        f"Respuesta de Tama:\n{outcome.respuesta}\n\n"
        f"Criterios ({len(scenario.all_criteria)}):\n{criteria}\n\n"
        'Devuelve {"puntajes": [un entero por criterio], "comentario": "una frase"}.'
    )


Judge = Callable[[str], dict[str, Any]]


def apply_judgement(scenario: Scenario, outcome: Outcome, judge: Judge) -> None:
    if outcome.error:
        return
    try:
        verdict = judge(judge_prompt(scenario, outcome))
        scores = [int(s) for s in verdict["puntajes"]]
    except (KeyError, TypeError, ValueError) as exc:
        outcome.error = f"juez: {exc}"
        return
    if len(scores) != len(scenario.all_criteria):
        outcome.error = f"juez: {len(scores)} puntajes para {len(scenario.all_criteria)} criterios"
        return
    outcome.puntajes = [min(5, max(1, s)) for s in scores]
    outcome.comentario = str(verdict.get("comentario", ""))


def percentile(values: list[float], pct: float) -> float | None:
    """Percentil por rango más cercano."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def summarize(outcomes: list[Outcome]) -> dict[str, Any]:
    ttfts = [o.ttft_s for o in outcomes if o.ttft_s is not None]
    totals = [o.total_s for o in outcomes if o.total_s is not None]
    scored = [o.promedio for o in outcomes if o.promedio is not None]
    layers: dict[str, int] = {}
    for o in outcomes:
        if o.capa:
            layers[o.capa] = layers.get(o.capa, 0) + 1
    return {
        "escenarios": len(outcomes),
        "errores": sum(1 for o in outcomes if o.error),
        "puntaje_medio": statistics.mean(scored) if scored else None,
        "aprobados": sum(1 for s in scored if s >= 4),
        "ttft_p50": percentile(ttfts, 50),
        "ttft_p95": percentile(ttfts, 95),
        "total_p50": percentile(totals, 50),
        "total_p95": percentile(totals, 95),
        "capas": layers,
        "ttft_por_capa": {
            capa: {
                "p50": percentile([o.ttft_s for o in outcomes if o.capa == capa and o.ttft_s], 50),
                "p95": percentile([o.ttft_s for o in outcomes if o.capa == capa and o.ttft_s], 95),
            }
            for capa in layers
        },
    }


def save_mode(mode: str, judge_name: str, outcomes: list[Outcome]) -> None:
    data = {
        "modo": mode,
        "fecha": date.today().isoformat(),
        "juez": judge_name,
        "resumen": summarize(outcomes),
        "resultados": [asdict(o) for o in outcomes],
    }
    (EVALS_DIR / f"results-{mode}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_mode(mode: str) -> tuple[str, list[Outcome]]:
    data = json.loads((EVALS_DIR / f"results-{mode}.json").read_text(encoding="utf-8"))
    return data["juez"], [Outcome(**o) for o in data["resultados"]]


def fmt(value: float | None, unit: str = " s") -> str:
    return "—" if value is None else f"{value:.1f}{unit}"


def mode_title(mode: str) -> str:
    if mode == "offline":
        return "Sin red (solo capa local)"
    if mode == "online":
        return "Con red (cascada completa)"
    return f"Con red, solo {mode.removeprefix('online-').replace('-', ' y ')}"


def _mode_order(path: Path) -> tuple[int, str]:
    mode = path.stem.removeprefix("results-")
    return ({"online": 0, "offline": 2}.get(mode, 1), mode)


def render_report() -> str:
    lines = [
        "# Resultados de las evals de conversación",
        "",
        "Generado por `uv run python -m evals`. Cada escenario se puntúa de 1 a 5 por criterio con",
        f'un juez LLM; a todos se suma el criterio "{GENERIC_CRITERION}". Aprobado = media ≥ 4.',
    ]
    for path in sorted(EVALS_DIR.glob("results-*.json"), key=_mode_order):
        mode = path.stem.removeprefix("results-")
        data = json.loads(path.read_text(encoding="utf-8"))
        r = data["resumen"]
        capas = ", ".join(f"{k}: {v}" for k, v in r["capas"].items()) or "—"
        lines += [
            "",
            f"## {mode_title(mode)}",
            "",
            f"- Fecha: {data['fecha']} · juez: `{data['juez']}` · capas: {capas}",
            f"- Puntaje medio: {fmt(r['puntaje_medio'], '')} · aprobados: {r['aprobados']}/"
            f"{r['escenarios']} · errores: {r['errores']}",
            f"- Primer token: p50 {fmt(r['ttft_p50'])}, p95 {fmt(r['ttft_p95'])} · "
            f"respuesta completa: p50 {fmt(r['total_p50'])}, p95 {fmt(r['total_p95'])}",
            *[
                f"- Primer token en {capa}: p50 {fmt(v['p50'])}, p95 {fmt(v['p95'])}"
                for capa, v in r.get("ttft_por_capa", {}).items()
            ],
            "",
            "| Escenario | Capa | Primer token | Total | Puntajes | Media | Comentario del juez |",
            "|---|---|---:|---:|---|---:|---|",
        ]
        for o in data["resultados"]:
            scores = " ".join(map(str, o["puntajes"])) or "—"
            mean = statistics.mean(o["puntajes"]) if o["puntajes"] else None
            note = (o["error"] or o["comentario"]).replace("|", "/").replace("\n", " ")[:140]
            lines.append(
                f"| {o['scenario']} | {o['capa'] or '—'} | {fmt(o['ttft_s'])} | "
                f"{fmt(o['total_s'])} | {scores} | {fmt(mean, '')} | {note} |"
            )
    return "\n".join(lines) + "\n"
