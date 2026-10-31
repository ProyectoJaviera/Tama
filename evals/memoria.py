"""Evals de memoria: un "día 1" cuenta 10 hechos, se reinicia Tama y un "día 2" pregunta por ellos.

Los datos son ficticios: nada personal de Renzo. Mide cuántos hechos recuerda cada capa, cuántas
entidades duplicadas o mal nombradas crea, si inventa recuerdos y el tamaño real del prompt.

    uv run python -m evals.memoria --capas nube claude local
    uv run python -m evals.memoria --capas local --offline
"""

import argparse
import json
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from tama import agent, history
from tama import brain as brain_module
from tama.cascade import Answered
from tama.config import get_settings
from tama.mcp_servers.memoria import normalize
from tama.memory import session_prompt

EVALS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Fact:
    told: str
    question: str
    expected: str


FACTS = [
    Fact(
        "Mi hermana Lucía se muda a Temuco en marzo.", "¿A qué ciudad se muda mi hermana?", "temuco"
    ),
    Fact(
        "Estoy armando una huerta en el balcón con tomates cherry.",
        "¿Qué estoy plantando en el balcón?",
        "tomate",
    ),
    Fact("Mi perro se llama Bruno y tiene 4 años.", "¿Cómo se llama mi perro?", "bruno"),
    Fact("Odio el cilantro, no lo soporto.", "¿Qué hierba no me gusta?", "cilantro"),
    Fact("Estoy leyendo Cien años de soledad.", "¿Qué libro estoy leyendo?", "soledad"),
    Fact(
        "Mi proyecto de este trimestre es un reloj con Arduino.",
        "¿Cuál es mi proyecto de este trimestre?",
        "reloj",
    ),
    Fact(
        "El cumpleaños de mi mamá es el 14 de diciembre.",
        "¿Cuándo es el cumpleaños de mi mamá?",
        "14",
    ),
    Fact(
        "Mi colega Tomás trabaja conmigo en el proyecto Faro.",
        "¿Cómo se llama el proyecto en que trabajo con Tomás?",
        "faro",
    ),
    Fact("Empecé a aprender portugués en las tardes.", "¿Qué idioma estoy aprendiendo?", "portugu"),
    Fact(
        "En las tardes prefiero té verde antes que café.",
        "¿Qué prefiero tomar en las tardes?",
        "verde",
    ),
]
FILLER = ["Hola Tama, ¿cómo va todo?", "Qué bueno, gracias.", "Bueno, eso por ahora."]
NEGATIVE = ["¿Cómo se llama mi dentista?", "¿En qué año me casé?", "¿Cuál es mi color favorito?"]
DOES_NOT_KNOW = (
    "no lo tengo",
    "no tengo",
    "no sé",
    "no lo sé",
    "no encuentro",
    "no encontré",
    "no aparece",
    "no hay",
    "no me has",
    "no me lo has",
    "no recuerdo",
)


def day_one_script() -> list[str]:
    """Los 10 hechos repartidos en una conversación natural, con turnos de relleno."""
    told = [f.told for f in FACTS]
    return [FILLER[0], *told[:5], FILLER[1], *told[5:], FILLER[2]]


def said_it_does_not_know(answer: str) -> bool:
    text = answer.lower()
    return any(phrase in text for phrase in DOES_NOT_KNOW)


def recalled(fact: Fact, answer: str) -> bool:
    return fact.expected in normalize(answer)


def entity_quality(db_path: Path) -> dict[str, Any]:
    """Entidades creadas, duplicadas (un nombre contenido en otro) y mal nombradas (frases)."""
    if not db_path.exists():
        return {"entidades": 0, "duplicadas": [], "mal_nombradas": []}
    db = sqlite3.connect(db_path)
    names = [r[0] for r in db.execute("SELECT norm FROM entities ORDER BY norm")]
    db.close()
    duplicated = [
        f"{a} / {b}"
        for i, a in enumerate(names)
        for b in names[i + 1 :]
        if set(a.split()) <= set(b.split()) or set(b.split()) <= set(a.split())
    ]
    return {
        "entidades": len(names),
        "duplicadas": duplicated,
        "mal_nombradas": [n for n in names if len(n.split()) > 4],
    }


def ask(brain, messages: list[dict], text: str, max_tokens: int, max_tools: int) -> dict[str, Any]:
    """Un turno como en la CLI: registra en events.db y usa las herramientas."""
    messages.append({"role": "user", "content": text})
    brain.tools.current_event = brain.store.log("user_msg", text)
    tools_used: list[str] = []
    first: Answered | None = None
    answer = ""
    for event in agent.run_turn(
        brain.cascade,
        brain.tools,
        history.trim(messages, max_tokens),
        lambda call, info: False,
        max_tools,
    ):
        if isinstance(event, agent.ToolUse):
            tools_used.append(event.name)
        elif isinstance(event, Answered) and first is None:
            first = event
        elif isinstance(event, agent.FinalAnswer):
            answer = event.text
            messages.extend(event.trace)
    messages.append({"role": "assistant", "content": answer})
    brain.store.log("assistant_msg", answer, first.provider if first else None)
    return {
        "respuesta": answer,
        "herramientas": tools_used,
        "capa": first.provider if first else None,
        "tokens_prompt": first.input_tokens if first else None,
    }


def run_layer(layer: str, offline: bool) -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix=f"tama-memoria-{layer}-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
        }
    )
    label = f"[{layer}{' sin red' if offline else ''}]"
    limits = (settings.history_max_tokens, settings.tools_max)

    # Día 1: conversación natural con los 10 hechos.
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    saved = 0
    for text in day_one_script():
        turn = ask(brain, messages, text, *limits)
        saved += sum(t in ("create_entities", "add_observations") for t in turn["herramientas"])
        print(f"{label} día 1: {text[:40]} -> {turn['herramientas']}", flush=True)
    brain.close()

    # Día 2: Tama se reinició; cada pregunta parte de una sesión nueva.
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    system = session_prompt(settings.memory_dir, brain.store)
    facts, negatives = [], []
    prompt_tokens = []
    for fact in FACTS:
        turn = ask(brain, [{"role": "system", "content": system}], fact.question, *limits)
        ok = recalled(fact, turn["respuesta"])
        facts.append({"pregunta": fact.question, "ok": ok, **turn})
        if turn["tokens_prompt"]:
            prompt_tokens.append(turn["tokens_prompt"])
        print(f"{label} día 2: {fact.question[:40]} -> {'ok' if ok else 'FALLA'}", flush=True)
    for question in NEGATIVE:
        turn = ask(brain, [{"role": "system", "content": system}], question, *limits)
        ok = said_it_does_not_know(turn["respuesta"])
        negatives.append({"pregunta": question, "ok": ok, **turn})
        print(f"{label} negativo: {question[:40]} -> {'ok' if ok else 'INVENTÓ'}", flush=True)
    brain.close()

    return {
        "capa": layer,
        "sin_red": offline,
        "fecha": date.today().isoformat(),
        "hechos": sum(f["ok"] for f in facts),
        "total_hechos": len(facts),
        "guardados": saved,
        "negativos": sum(n["ok"] for n in negatives),
        "total_negativos": len(negatives),
        "tokens_prompt_max": max(prompt_tokens, default=None),
        "calidad": entity_quality(settings.data_dir / "memoria.db"),
        "detalle": facts + negatives,
    }


CORRECTION = [
    "Para Halloween me voy a disfrazar de Shrek.",
    "Mi esposa se va a disfrazar de Fiona para Halloween.",
    "Cambio de planes: con mi esposa iremos de la Bella y la Bestia.",
    "Al final no, mi esposa y yo iremos de piratas.",
]
CORRECTION_QUESTION = "¿De qué nos vamos a disfrazar en Halloween?"
OUTDATED = ("shrek", "fiona", "bella", "bestia")


def run_correction(layer: str, offline: bool) -> dict[str, Any]:
    """Corregir un dato: sin confirmación, sin borrar nada, y se recuerda el nuevo."""
    base = Path(tempfile.mkdtemp(prefix=f"tama-correccion-{layer}-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
        }
    )
    limits = (settings.history_max_tokens, settings.tools_max)
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    used: list[str] = []
    for text in CORRECTION:
        turn = ask(brain, messages, text, *limits)
        used += turn["herramientas"]
        print(f"[{layer}] {text[:40]} -> {turn['herramientas']}", flush=True)
    brain.close()
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    system = session_prompt(settings.memory_dir, brain.store)
    turn = ask(brain, [{"role": "system", "content": system}], CORRECTION_QUESTION, *limits)
    brain.close()
    db = sqlite3.connect(settings.data_dir / "memoria.db")
    rows = db.execute("SELECT content, valid FROM observations").fetchall()
    db.close()
    answer = normalize(turn["respuesta"])

    def mentions_old(text: str) -> bool:
        return any(word in normalize(text) for word in OUTDATED)

    return {
        "capa": layer,
        "sin_red": offline,
        "sin_confirmacion": "delete_observations" not in used,
        "conserva_antiguo": any(mentions_old(r[0]) and not r[1] for r in rows),
        "antiguo_no_vigente": not any(mentions_old(r[0]) and r[1] for r in rows),
        "recuerda_nuevo": "pirata" in answer and not any(w in answer for w in OUTDATED),
        "respuesta": turn["respuesta"],
        "herramientas": used + turn["herramientas"],
    }


def render(data: dict[str, Any]) -> str:
    lines = [
        "# Evals de memoria",
        "",
        "Generado por `python -m evals.memoria` con datos ficticios. Día 1: 10 hechos en una",
        "conversación natural. Se reinicia Tama. Día 2: una pregunta por hecho, cada una en una",
        "sesión nueva. Casos negativos: datos nunca contados. Meta: 9 de 10 y ninguna invención.",
        "",
        "| Capa | Hechos recordados | Negativos | Entidades | Duplicadas | Mal nombradas |"
        " Tokens de prompt (máx) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, run in data.items():
        if key.startswith("corrección"):
            continue
        q = run["calidad"]
        lines.append(
            f"| {key} | {run['hechos']}/{run['total_hechos']} | "
            f"{run['negativos']}/{run['total_negativos']} | {q['entidades']} | "
            f"{len(q['duplicadas'])} | {len(q['mal_nombradas'])} | "
            f"{run['tokens_prompt_max'] or '-'} |"
        )
    corrections = {k: v for k, v in data.items() if k.startswith("corrección")}
    data = {k: v for k, v in data.items() if not k.startswith("corrección")}
    if corrections:
        lines += ["", "## Corrección de un dato", ""]
        lines += ["| Capa | Sin confirmación | Antiguo no vigente | Recuerda el nuevo |"]
        lines += ["|---|---|---|---|"]
        for key, run in corrections.items():
            marks = [run["sin_confirmacion"], run["antiguo_no_vigente"], run["recuerda_nuevo"]]
            cells = " | ".join("ok" if m else "FALLA" for m in marks)
            lines.append(f"| {key.removeprefix('corrección ')} | {cells} |")
    lines += ["", "## Fallos", ""]
    for key, run in data.items():
        for item in run["detalle"]:
            if not item["ok"]:
                answer = item["respuesta"][:120].replace("\n", " ").replace("|", "/")
                lines.append(f"- `{key}` · {item['pregunta']}: {answer}")
        for pair in run["calidad"]["duplicadas"]:
            lines.append(f"- `{key}` · duplicada: {pair}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capas", nargs="+", default=["nube", "claude", "local"])
    parser.add_argument("--offline", action="store_true", help="simula no tener internet")
    parser.add_argument("--correccion", action="store_true", help="solo corrección de un dato")
    args = parser.parse_args()

    out = EVALS_DIR / "memoria-results.json"
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    for layer in args.capas:
        key = layer + (" (sin red)" if args.offline else "")
        if args.correccion:
            data[f"corrección {key}"] = run_correction(layer, args.offline)
        else:
            data[key] = run_layer(layer, args.offline)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (EVALS_DIR / "RESULTS_MEMORIA.md").write_text(render(data), encoding="utf-8")
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
