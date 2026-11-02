"""Evals del motor proactivo: recordatorios en lenguaje natural y un resumen que no inventa.

uv run python -m evals.avisos
"""

import os
import sqlite3
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import tama  # noqa: F401  (quita el SSLKEYLOGFILE de Avast)
from evals.__main__ import make_judge
from evals.memoria import ask
from tama import brain as brain_module
from tama.config import get_settings
from tama.mcp_servers import memoria
from tama.memory import session_prompt
from tama.proactive import morning_summary

# Mensaje y minutos aproximados hasta el aviso; None si solo importa que quede agendado.
REMINDERS = [
    ("Recuérdame en 2 minutos sacar la ropa de la lavadora.", 2),
    ("Avísame en media hora que debo llamar al banco.", 30),
    ("En una hora recuérdame tomar agua.", 60),
    ("Recuérdame mañana a las 9 pagar la luz.", None),
    ("Mañana a las 3 de la tarde tengo dentista, recuérdamelo.", None),
    ("Recuérdame el viernes a las 10 revisar el auto.", None),
    ("Avísame el 5 de diciembre a las 8 que es el cumpleaños de Ana.", None),
    ("Recuérdame hoy en la noche, a las 21:30, regar las plantas.", None),
    ("En tres días recuérdame comprar pan.", None),
    ("Agenda un recordatorio urgente en 10 minutos para apagar el horno.", 10),
    ("Recuérdame el lunes a las 8:15 llevar el informe.", None),
    ("La próxima semana recuérdame renovar la licencia.", None),
]
JUDGE_CRITERIA = (
    "1. Todo lo que dice el resumen aparece en los datos: no inventa nada.\n"
    "2. Tiene 5 líneas o menos.\n"
    "3. No habla del clima ni de noticias."
)


def isolated():
    base = Path(tempfile.mkdtemp(prefix="tama-avisos-"))
    settings = get_settings().model_copy(
        update={
            "data_dir": base / "data",
            "workdir": base / "trabajo",
            "memory_dir": base / "memoria",
            "connectivity_watch_s": 0,
        }
    )
    os.environ["TAMA_DATA_DIR"] = str(settings.data_dir)
    return settings


def run_reminders() -> int:
    good = 0
    for text, minutes in REMINDERS:
        settings = isolated()
        brain = brain_module.build(settings, only=["nube"])
        system = session_prompt(settings.memory_dir, brain.store)
        limits = (settings.history_max_tokens, settings.tools_max)
        started = memoria.local_now()
        turn = ask(brain, [{"role": "system", "content": system}], text, *limits)
        brain.close()
        path = settings.data_dir / "memoria.db"
        rows = []
        if path.exists():
            db = sqlite3.connect(path)
            rows = db.execute("SELECT due_at, text FROM reminders").fetchall()
            db.close()
        ok = len(rows) == 1
        if ok and minutes is not None:
            due = memoria.datetime.fromisoformat(rows[0][0])
            ok = abs(due - started - timedelta(minutes=minutes)) < timedelta(minutes=2)
        good += ok
        print(f"{'ok' if ok else 'FALLA'}: {text} -> {rows} {turn['herramientas']}", flush=True)
    print(f"Recordatorios: {good}/{len(REMINDERS)}")
    return good


def run_summary() -> None:
    settings = isolated()
    now = memoria.local_now()
    memoria.crear_recordatorio("Reunión con Tomás", (now + timedelta(minutes=30)).isoformat())
    memoria.create_goal(
        "Terminar el reloj Arduino", fecha=(now + timedelta(days=3)).date().isoformat()
    )
    memoria.add_suggestion("Retomar el libro que dejaste")
    data = "\n".join(
        [
            *memoria.reminders_on(now.date()),
            *memoria.goal_notes(now.date(), 14),
            *memoria.pending_list(),
        ]
    )
    text = morning_summary(now)
    print(text, flush=True)
    judge, _, close = make_judge(settings)
    try:
        verdict = judge(
            f"Datos guardados:\n{data}\n\nResumen matutino:\n{text}\n\nCriterios (3):\n"
            f"{JUDGE_CRITERIA}

"
            'Devuelve {"puntajes": [un entero por criterio], "comentario": "una frase"}.'
        )
    finally:
        close()
    print(f"Resumen: {verdict.get('puntajes')} {verdict.get('comentario', '')}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run_reminders()
    run_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
