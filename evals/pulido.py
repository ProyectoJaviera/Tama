"""Evals del Sprint 8 (pulido), con datos ficticios y una memoria nueva por caso.

    uv run python -m evals.pulido                       # todo, en la nube y sin Claude
    uv run python -m evals.pulido fechas planes         # solo esas partes
    uv run python -m evals.pulido planes-reflexion      # la reflexión busca planes (nube)

- fechas: historial médico con un evento sin fecha; la respuesta debe decir que no tiene fecha.
- planes: planes contados en una frase; deben quedar como plan, con su fecha si la tienen.
- sugerencias: con una sugerencia pendiente, "¿tienes alguna sugerencia?" parte por ella.
- avisos: Tama no promete avisos que no puede dar (lluvia, mensajes).
- planes-reflexion: la revisión de planes de la reflexión, con el modelo de la nube en vez del
  local (la calidad del local se mide en `docs/sprints/13-modelo-local-v2.md`).
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import threading
from datetime import timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from evals.memoria import ask
from tama import brain as brain_module
from tama.config import get_settings
from tama.events import EventStore
from tama.mcp_servers import memoria
from tama.memory import session_prompt
from tama.reflection import Thinker, review_plans

EVALS_DIR = Path(__file__).parent
PARTS = ("fechas", "planes", "sugerencias", "avisos", "planes-reflexion")

UNDATED_HISTORY = [
    "El 3 de marzo de 2026 me operaron de la rodilla.",
    "Una vez tuve una alergia fuerte a la penicilina.",
    "El 2 de agosto de 2026 me fracturé la muñeca.",
]
SAYS_UNDATED = ("no indicada", "sin fecha", "no me dijiste", "no dijiste", "no tengo la fecha")


def weekday_after(today, weekday: int) -> str:
    return (today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)).isoformat()


def plan_cases(today) -> list[tuple[str, str | None]]:
    """(lo que cuenta Renzo, día esperado: "" sin fecha, None si no es un plan)."""
    year = today.year + (1 if today.month >= 3 else 0)
    december = f"{today.year + (1 if today.month == 12 and today.day > 20 else 0)}-12-20"
    return [
        ("Me voy a disfrazar de Shrek para la fiesta de la oficina.", ""),
        ("El sábado voy a la playa con Ana.", weekday_after(today, 5)),
        ("El sábado voy a la playa con Frida", weekday_after(today, 5)),  # la demo del S8
        ("El 20 de diciembre voy al concierto de Los Bunkers.", december),
        ("En marzo pienso empezar un curso de cerámica.", f"{year}-03-01"),
        ("Mañana voy a almorzar con mi mamá.", (today + timedelta(days=1)).isoformat()),
        ("Ayer fui a la playa con Ana.", None),
    ]


def fresh_settings(name: str):
    base = Path(tempfile.mkdtemp(prefix=f"tama-pulido-{name}-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
            "local_preload": False,
        }
    )
    os.environ["TAMA_DATA_DIR"] = str(settings.data_dir.resolve())
    return settings


def one_turn(settings, layer: str, text: str, offline: bool) -> dict[str, Any]:
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    try:
        system = session_prompt(settings.memory_dir, brain.store)
        limits = (settings.history_max_tokens, settings.tools_max)
        return ask(brain, [{"role": "system", "content": system}], text, *limits)
    finally:
        brain.close()


def run_dates(layer: str, offline: bool) -> dict[str, Any]:
    settings = fresh_settings("fechas")
    brain = brain_module.build(settings, force_offline=offline, only=[layer])
    messages = [{"role": "system", "content": session_prompt(settings.memory_dir, brain.store)}]
    limits = (settings.history_max_tokens, settings.tools_max)
    for text in UNDATED_HISTORY:
        ask(brain, messages, text, *limits)
    brain.close()
    turn = one_turn(settings, layer, "Cuéntame mi historial médico, en orden.", offline)
    answer = memoria.normalize(turn["respuesta"])
    ok = "penicilina" in answer and any(p in answer for p in SAYS_UNDATED)
    print(f"[{layer}] fechas {'ok' if ok else 'FALLA'}: {turn['respuesta']}", flush=True)
    return {"ok": ok, "respuesta": turn["respuesta"], "herramientas": turn["herramientas"]}


def saved_facts(settings) -> list[tuple[str, str, str]]:
    path = settings.data_dir / "memoria.db"
    if not path.exists():
        return []
    db = sqlite3.connect(path)
    try:
        return db.execute(
            "SELECT content, kind, occurred_at FROM observations WHERE valid = 1"
        ).fetchall()
    finally:
        db.close()


def duplicates(facts: list[tuple[str, str, str]]) -> list[str]:
    """Datos comunes que repiten un plan (el mismo hecho guardado dos veces)."""
    plans = [memoria.fact_key(f[0])[0] for f in facts if f[1] == "plan"]
    return [
        f[0]
        for f in facts
        if f[1] != "plan"
        and any(SequenceMatcher(None, memoria.fact_key(f[0])[0], p).ratio() >= 0.85 for p in plans)
    ]


def run_plans(layer: str, offline: bool) -> dict[str, Any]:
    today = memoria.local_now().date()
    results = []
    for text, expected in plan_cases(today):
        settings = fresh_settings("planes")
        turn = one_turn(settings, layer, text, offline)
        facts = saved_facts(settings)
        plans = [f for f in facts if f[1] == "plan"]
        repeated = duplicates(facts)
        if expected is None:
            ok = not plans
        else:
            dated = [p for p in plans if memoria.resolve_dates(p[0], future=True)[1]]
            right_date = (bool(dated) and dated[0][2] == expected) if expected else not dated
            ok = len(plans) == 1 and right_date and not repeated
        mark = "ok" if ok else "FALLA"
        extra = f" duplicados: {repeated}" if repeated else ""
        print(f"[{layer}] {text} -> {turn['herramientas']} {plans}{extra} {mark}", flush=True)
        results.append(
            {"caso": text, "ok": ok, "planes": plans, "duplicados": repeated, "esperado": expected}
        )
    return {"correctos": sum(r["ok"] for r in results), "total": len(results), "detalle": results}


def run_suggestions(layer: str, offline: bool) -> dict[str, Any]:
    settings = fresh_settings("sugerencias")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    memoria.add_suggestion("Retomar las clases de portugués: llevas tres semanas sin avance.")
    turn = one_turn(settings, layer, "¿Tienes alguna sugerencia para mí?", offline)
    answer = memoria.normalize(turn["respuesta"])
    ok = "list_suggestions" in turn["herramientas"] and "portugu" in answer
    print(f"[{layer}] sugerencias {'ok' if ok else 'FALLA'}: {turn['respuesta']}", flush=True)
    return {"ok": ok, "respuesta": turn["respuesta"], "herramientas": turn["herramientas"]}


CANNOT = ("no puedo", "no tengo forma", "no tengo como", "no tengo cómo", "no soy capaz")
IMPOSSIBLE = ["Avísame cuando empiece a llover.", "Avísame si mi hermana me escribe un mensaje."]


def run_notices(layer: str, offline: bool) -> dict[str, Any]:
    results = []
    for text in IMPOSSIBLE:
        settings = fresh_settings("avisos")
        turn = one_turn(settings, layer, text, offline)
        answer = turn["respuesta"].lower()
        ok = "crear_recordatorio" not in turn["herramientas"] and any(c in answer for c in CANNOT)
        print(f"[{layer}] {text} {'ok' if ok else 'FALLA'}: {turn['respuesta']}", flush=True)
        results.append({"caso": text, "ok": ok, "respuesta": turn["respuesta"]})
    return {"correctos": sum(r["ok"] for r in results), "total": len(results), "detalle": results}


CONVERSATION = [
    ("hola Tama, ¿cómo estás?", None),
    ("oye, me voy a disfrazar de Shrek para la fiesta del 20 de diciembre", "shrek"),
    ("ayer fui al cine con Ana", None),
    ("¿qué hora es?", None),
    ("el sábado pienso ir a la playa con Ana", "playa"),
    ("me encantaría ir a Japón algún día", None),
    ("en marzo empiezo un curso de cerámica", "ceramica"),
]


def run_reflection_plans(layer: str, offline: bool) -> dict[str, Any]:
    """La revisión de planes con el modelo de la capa pedida (por defecto, la nube)."""
    settings = fresh_settings("planes-reflexion")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    memoria.ensure_owner(settings.user_name)
    model = settings.cloud_model if layer == "nube" else settings.local_model
    store = EventStore(settings.events_db)
    for text, _ in CONVERSATION:
        store.log("user_msg", text)
    client = brain_module.OllamaClient(settings.ollama_host, timeout=settings.timeout)
    thinker = Thinker(client, model, brain_module.local_options(settings), threading.Event())
    try:
        done = review_plans(thinker, store)
    finally:
        client.close()
        store.close()
    plans = [memoria.normalize(p) for p in memoria.plan_texts()]
    wanted = [word for _, word in CONVERSATION if word]
    found = [w for w in wanted if any(w in p for p in plans)]
    extra = [p for p in plans if not any(w in p for w in wanted)]
    print(f"[{layer}] planes-reflexion: {done}", flush=True)
    return {"encontrados": len(found), "total": len(wanted), "de_mas": extra, "lineas": done}


RUNNERS = {
    "fechas": run_dates,
    "planes": run_plans,
    "sugerencias": run_suggestions,
    "avisos": run_notices,
    "planes-reflexion": run_reflection_plans,
}


def render(data: dict[str, Any]) -> str:
    lines = [
        "# Evals del Sprint 8 (pulido)",
        "",
        "Generado por `python -m evals.pulido`, con datos ficticios, en la nube y sin Claude.",
        "",
        "| Capa | Fecha no indicada | Planes | Sugerencias | Avisos imposibles | Planes en la "
        "reflexión |",
        "|---|---|---:|---|---:|---:|",
    ]
    for layer, run in data.items():
        cells = []
        for part in PARTS:
            item = run.get(part)
            if item is None:
                cells.append("-")
            elif "ok" in item:
                cells.append("ok" if item["ok"] else "FALLA")
            elif "encontrados" in item:
                extra = f", {len(item['de_mas'])} de más" if item["de_mas"] else ""
                cells.append(f"{item['encontrados']}/{item['total']}{extra}")
            else:
                cells.append(f"{item['correctos']}/{item['total']}")
        lines.append(f"| {layer} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("partes", nargs="*", help=", ".join(PARTS))
    parser.add_argument("--capas", nargs="+", default=["nube"])
    parser.add_argument("--offline", action="store_true", help="simula no tener internet")
    parser.add_argument("--con-claude", action="store_true", help="incluye la capa de Claude")
    args = parser.parse_args()
    parts = args.partes or list(PARTS)
    unknown = set(parts) - set(PARTS)
    if unknown:
        parser.error(f"partes desconocidas: {', '.join(sorted(unknown))}")
    out = EVALS_DIR / "pulido-results.json"
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    for layer in args.capas:
        key = layer + (" (sin red)" if args.offline else "")
        run = data.setdefault(key, {})
        for part in parts:
            run[part] = RUNNERS[part](layer, args.offline)
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EVALS_DIR / "RESULTS_PULIDO.md").write_text(render(data), encoding="utf-8")
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
