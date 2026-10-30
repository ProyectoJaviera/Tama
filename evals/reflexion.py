"""Evals del Sprint 4: reflexión sobre un grafo sembrado, objetivos por conversación y diario.

Los datos son ficticios: nada personal de Renzo. La reflexión y el diario corren en el modelo local;
los objetivos, en la capa que se indique. El juez verifica que el diario no invente nada.

    uv run python -m evals.reflexion reflexion
    uv run python -m evals.reflexion diario
    uv run python -m evals.reflexion objetivos --capas nube claude local
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from evals.__main__ import make_judge
from evals.memoria import ask
from tama import brain as brain_module
from tama import reflection
from tama.config import get_settings
from tama.events import EventStore
from tama.mcp_servers import memoria
from tama.memory import session_prompt
from tama.ollama import OllamaClient

EVALS_DIR = Path(__file__).parent
MAX_PASSES = 4

# name, tipo, [(fecha, dato)]
Seed = tuple[str, str, list[tuple[str, str]]]
OLD, NEW = "2026-02-10T12:00:00+00:00", "2026-09-20T12:00:00+00:00"

DUPLICATES: list[tuple[Seed, Seed]] = [
    (
        ("Mónica Rojas", "persona", [(OLD, "Es mi prima"), (OLD, "Vive en Valdivia")]),
        ("Moni Rojas", "persona", [(NEW, "Mi prima de Valdivia tiene una pastelería")]),
    ),
    (
        ("Proyecto Faro", "proyecto", [(OLD, "App para mapear faros de Chile")]),
        ("Faro", "proyecto", [(NEW, "La app de los faros ya tiene 40 faros cargados")]),
    ),
    (
        ("Café Altura", "lugar", [(OLD, "Cafetería de Providencia donde trabajo los viernes")]),
        ("Cafe Altura Providencia", "lugar", [(NEW, "Los viernes voy a trabajar allá")]),
    ),
    (
        ("Bruno", "mascota", [(OLD, "Es mi perro quiltro")]),
        ("Brunito", "mascota", [(NEW, "Mi perro quiltro tiene 4 años")]),
    ),
    (
        ("Escuela Allegro", "lugar", [(OLD, "Donde tomo clases de guitarra")]),
        ("Escuela de Música Allegro", "lugar", [(NEW, "Mis clases de guitarra son los martes")]),
    ),
]
# Se parecen, pero son distintas: fusionarlas es un error.
TRAPS: list[tuple[Seed, Seed]] = [
    (
        ("Laura Vidal", "persona", [(OLD, "Es mi tía, tiene 62 años y vive en La Serena")]),
        ("Laura Vial", "persona", [(NEW, "Compañera de trabajo, 28 años, vive en Concepción")]),
    ),
    (
        ("Clara", "persona", [(OLD, "Es mi hermana, estudia medicina")]),
        ("Clarita", "mascota", [(NEW, "Es la gata de la vecina")]),
    ),
    (
        ("Tomás", "persona", [(OLD, "Es mi sobrino de 7 años")]),
        ("Tomás Soto", "persona", [(NEW, "Gerente de mi empresa, tiene 50 años")]),
    ),
]
CONTRADICTIONS: list[tuple[Seed, str]] = [
    (
        (
            "Andrea",
            "persona",
            [
                (OLD, "Trabaja en el Banco Sur"),
                (OLD, "Le gusta el ciclismo"),
                (NEW, "Dejó el banco y ahora trabaja en la Clínica Norte"),
            ],
        ),
        "Trabaja en el Banco Sur",
    ),
    (
        ("Luis", "persona", [(OLD, "Vive en Talca"), (NEW, "Se mudó a Temuco en agosto")]),
        "Vive en Talca",
    ),
    (
        ("Camioneta", "cosa", [(OLD, "Es de color rojo"), (NEW, "La pintaron de azul")]),
        "Es de color rojo",
    ),
]
COMPATIBLE: list[Seed] = [
    ("Paula", "persona", [(OLD, "Toma té en las tardes"), (NEW, "Toma café en las mañanas")]),
    ("Diego", "persona", [(OLD, "Vive en Puerto Varas"), (NEW, "Trabaja en Puerto Montt")]),
]


def isolate() -> tuple[Any, Path]:
    base = Path(tempfile.mkdtemp(prefix="tama-reflexion-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
        }
    )
    os.environ["TAMA_DATA_DIR"] = str(settings.data_dir)
    os.environ["TAMA_MEMORY_DIR"] = str(settings.memory_dir)
    return settings, base


def seed(items: list[Seed]) -> None:
    """Inserta directo: la deduplicación de create_entities impediría sembrar duplicados."""
    with memoria.connect() as db:
        for name, type_, observations in items:
            cursor = db.execute(
                "INSERT INTO entities (name, norm, type, created_at) VALUES (?, ?, ?, ?)",
                (name, memoria.normalize(name), type_, OLD),
            )
            for stamp, text in observations:
                db.execute(
                    "INSERT INTO observations (entity_id, content, created_at) VALUES (?, ?, ?)",
                    (cursor.lastrowid, text, stamp),
                )
            memoria.reindex(db, cursor.lastrowid)


def thinker(settings, client: OllamaClient) -> reflection.Thinker:
    return brain_module.thinker(settings, client, threading.Event())


def same_entity(a: str, b: str) -> bool:
    with memoria.connect() as db:
        first, second = memoria.find_entity(db, a), memoria.find_entity(db, b)
    return first is not None and second is not None and first["id"] == second["id"]


def valid(text: str) -> bool:
    with memoria.connect() as db:
        row = db.execute("SELECT valid FROM observations WHERE content = ?", (text,)).fetchone()
    return bool(row and row[0])


def run_reflection() -> dict[str, Any]:
    settings, base = isolate()
    seed([s for pair in DUPLICATES + TRAPS for s in pair])
    seed([s for s, _ in CONTRADICTIONS] + COMPATIBLE)
    store = EventStore(settings.events_db)
    started = time.monotonic()
    with OllamaClient(settings.ollama_host, timeout=settings.timeout) as client:
        think = thinker(settings, client)
        for _ in range(MAX_PASSES):
            reflection.reflect(think, store, settings.memory_dir)
            print(f"[reflexión] pasada lista: {len(store.rows('reflection'))} eventos", flush=True)
    merged = [f"{a[0]} = {b[0]}" for a, b in DUPLICATES if same_entity(a[0], b[0])]
    wrong = [f"{a[0]} = {b[0]}" for a, b in TRAPS if same_entity(a[0], b[0])]
    marked = [text for _, text in CONTRADICTIONS if not valid(text)]
    kept = [text for seeded in COMPATIBLE for _, text in seeded[2]]
    kept += [text for seeded, _ in CONTRADICTIONS for _, text in seeded[2]]
    outdated = {text for _, text in CONTRADICTIONS}
    retired = [text for text in kept if text not in outdated and not valid(text)]
    with memoria.connect() as db:
        questions = [r[0] for r in db.execute("SELECT text FROM suggestions")]
    return {
        "fecha": date.today().isoformat(),
        "segundos": round(time.monotonic() - started),
        "fusionados": merged,
        "total_duplicados": len(DUPLICATES),
        "fusiones_incorrectas": wrong,
        "contradicciones": marked,
        "total_contradicciones": len(CONTRADICTIONS),
        "datos_vigentes_perdidos": retired,
        "preguntas": questions,
        "eventos": [r["content"] for r in store.rows("reflection")],
        "base": str(base),
    }


GOAL_TURNS = [
    "Hola Tama. Me propuse un objetivo: correr una media maratón antes de junio.",
    "Esta semana corrí 12 kilómetros, voy bien con lo de la media maratón.",
    "¿Qué objetivos tengo ahora y cómo voy?",
    "Pausemos lo de la media maratón, me lesioné la rodilla.",
]


def run_goals(layer: str, offline: bool) -> dict[str, Any]:
    settings, _ = isolate()
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    turns = []
    for text in GOAL_TURNS:
        limits = (settings.history_max_tokens, settings.tools_max)
        turn = ask(brain, messages, text, *limits)
        turns.append({"mensaje": text, **turn})
        print(f"[{layer}] {text[:40]} -> {turn['herramientas']}", flush=True)
    brain.close()
    db = sqlite3.connect(settings.data_dir / "memoria.db")
    goals = db.execute("SELECT title, status FROM goals").fetchall()
    progress = db.execute("SELECT content FROM goal_progress").fetchall()
    db.close()
    answer = turns[2]["respuesta"].lower()
    checks = {
        "crear": any("marat" in t.lower() for t, _ in goals),
        "avance": any("12" in p[0] for p in progress),
        "consultar": "marat" in answer and "list_goals" in turns[2]["herramientas"],
        "actualizar": any(status == "pausado" for _, status in goals),
    }
    return {"capa": layer, "sin_red": offline, "checks": checks, "turnos": turns}


DIARY_DAY = [
    ("user_msg", "Hola Tama, hoy fui al taller a arreglar la camioneta."),
    ("assistant_msg", "¡Hola! ¿Qué le pasaba a la camioneta?"),
    ("user_msg", "Era el alternador. Me cobraron 85 mil pesos."),
    ("assistant_msg", "Qué bueno que quedó resuelto."),
    ("user_msg", "En la tarde hablé con Lucía: se muda a Temuco en marzo."),
    ("assistant_msg", "Lo anoto. Debe estar contenta."),
    ("user_msg", "Y avancé con el reloj Arduino, ya muestra la hora en la pantalla."),
    ("assistant_msg", "Buen paso para tu proyecto."),
]
DIARY_CRITERIA = (
    "1. Todo lo que afirma el diario aparece en el registro: no inventa hechos, personas, cifras "
    "ni sentimientos.\n"
    "2. Menciona lo principal: la camioneta y el alternador, Lucía y Temuco, y el reloj Arduino.\n"
    "3. Es breve y está en español natural."
)


def run_diary() -> dict[str, Any]:
    settings, _ = isolate()
    store = EventStore(settings.events_db)
    for type_, text in DIARY_DAY:
        store.log(type_, text)
    memoria.create_entities(
        [
            {
                "name": "Lucía",
                "entityType": "persona",
                "observations": ["Se muda a Temuco en marzo"],
            }
        ]
    )
    memoria.create_goal("Terminar el reloj Arduino")
    memoria.add_goal_progress("reloj", "Ya muestra la hora en la pantalla")
    day = date.today()
    with OllamaClient(settings.ollama_host, timeout=settings.timeout) as client:
        path = reflection.write_diary(thinker(settings, client), store, settings.memory_dir, day)
    text = path.read_text(encoding="utf-8")
    source = reflection.diary_source(store, day)
    print(text, flush=True)
    judge, used, close = make_judge(settings)
    try:
        verdict = judge(
            f"Registro del día:\n{source}\n\nDiario escrito por Tama:\n{text}\n\n"
            f"Criterios (3):\n{DIARY_CRITERIA}\n\n"
            'Devuelve {"puntajes": [un entero por criterio], "comentario": "una frase"}.'
        )
    finally:
        close()
    return {"fecha": day.isoformat(), "juez": used, "diario": text, **verdict}


def cancel_latency() -> dict[str, Any]:
    """Tiempo entre que Renzo escribe y la reflexión suelta el modelo."""
    settings, _ = isolate()
    seed([s for pair in DUPLICATES for s in pair])
    store = EventStore(settings.events_db)
    with OllamaClient(settings.ollama_host, timeout=settings.timeout) as client:
        think = thinker(settings, client)
        job = threading.Thread(
            target=lambda: _quiet(lambda: reflection.reflect(think, store, settings.memory_dir))
        )
        job.start()
        time.sleep(float(os.environ.get("TAMA_EVAL_CANCEL_AFTER_S", "20")))
        started = time.monotonic()
        think.cancel.set()
        job.join()
    return {"segundos_hasta_soltar": round(time.monotonic() - started, 1)}


def _quiet(work) -> None:
    try:
        work()
    except reflection.Cancelled:
        pass


def render(data: dict[str, Any]) -> str:
    lines = [
        "# Evals de reflexión, objetivos y diario",
        "",
        "Generado por `python -m evals.reflexion` con datos ficticios.",
        "",
    ]
    if run := data.get("reflexion"):
        lines += [
            "## Reflexión (modelo local)",
            "",
            f"- Duplicados fusionados: {len(run['fusionados'])}/{run['total_duplicados']}"
            " (meta: 4)",
            f"- Fusiones incorrectas: {len(run['fusiones_incorrectas'])} (meta: 0)"
            f" {run['fusiones_incorrectas'] or ''}",
            f"- Contradicciones marcadas: {len(run['contradicciones'])}/"
            f"{run['total_contradicciones']} (meta: 3)",
            f"- Datos vigentes perdidos: {len(run['datos_vigentes_perdidos'])} (meta: 0)"
            f" {run['datos_vigentes_perdidos'] or ''}",
            f"- Tiempo total: {run['segundos']} s",
            "",
        ]
    if run := data.get("cancelacion"):
        lines += [f"Soltar el modelo al escribir Renzo: {run['segundos_hasta_soltar']} s.", ""]
    goals = {k: v for k, v in data.items() if k.startswith("objetivos")}
    if goals:
        lines += ["## Objetivos", "", "| Capa | Crear | Avance | Consultar | Actualizar |"]
        lines.append("|---|---|---|---|---|")
        for key, run in goals.items():
            c = run["checks"]
            marks = [("ok" if c[n] else "FALLA") for n in c]
            lines.append(f"| {key.removeprefix('objetivos ')} | " + " | ".join(marks) + " |")
        lines.append("")
    if run := data.get("diario"):
        lines += [
            "## Diario",
            "",
            f"Puntajes del juez (fidelidad, cobertura, estilo): {run.get('puntajes')}."
            f" {run.get('comentario', '')}",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("eval", choices=["reflexion", "diario", "objetivos", "cancelacion"])
    parser.add_argument("--capas", nargs="+", default=["nube", "claude", "local"])
    parser.add_argument("--offline", action="store_true", help="simula no tener internet")
    args = parser.parse_args()

    out = EVALS_DIR / "reflexion-results.json"
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}

    def save() -> None:
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (EVALS_DIR / "RESULTS_REFLEXION.md").write_text(render(data), encoding="utf-8")

    if args.eval == "reflexion":
        data["reflexion"] = run_reflection()
    elif args.eval == "diario":
        data["diario"] = run_diary()
    elif args.eval == "cancelacion":
        data["cancelacion"] = cancel_latency()
    else:
        for layer in args.capas:
            key = f"objetivos {layer}" + (" (sin red)" if args.offline else "")
            data[key] = run_goals(layer, args.offline)
            save()
    save()
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
