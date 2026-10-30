"""Reflexión en inactividad y diario: Tama ordena su memoria mientras Renzo no escribe.

Corre en un hilo, siempre con el modelo local y sin red. El modelo decide (regla 3); aquí solo se
buscan candidatos y se aplican sus decisiones. Si duda, no se toca nada y queda una pregunta.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from typing import Any

import psutil

from tama.events import EventStore
from tama.mcp_servers import memoria
from tama.ollama import OllamaClient, OllamaError

log = logging.getLogger(__name__)

MAX_SUGGESTIONS = 2
MAX_DIARY_MESSAGES = 60
MAX_DIARY_CHARS = 300
DIARY_DAYS_BACK = 3

MERGE_PROMPT = (
    "Revisas la memoria de un asistente. Te dan dos fichas: decide si hablan de la misma cosa o "
    "persona. Un nombre parecido no basta: datos incompatibles indican que son distintas. Responde "
    'en JSON: {"decision": "misma" | "distintas" | "dudo", "conservar": "<nombre de la ficha cuyo '
    'nombre es más completo>"}.'
)
CONTRADICTION_PROMPT = (
    "Revisas los datos guardados sobre una entidad, con su fecha. Busca datos que ya no pueden ser "
    "ciertos porque otro más nuevo los contradice (por ejemplo, vivía en un lugar y ahora vive en "
    "otro). Dos datos que pueden ser ciertos a la vez NO se contradicen. Responde en JSON: "
    '{"obsoletos": [<números de los datos antiguos contradichos>]}. Si no hay, lista vacía.'
)
SUGGESTION_PROMPT = (
    "Eres Tama, acompañante de Renzo. Mira sus objetivos activos. Solo si alguno lleva 14 días o "
    "más sin avance, propone una sugerencia breve, amable y sin presionar, en español de Chile "
    'neutro. Responde en JSON: {"sugerencias": ["..."]}, con máximo 2. Si no hay motivo, lista '
    "vacía."
)
DIARY_PROMPT = (
    "Escribe en español el diario de Tama de un día, en primera persona y en Markdown breve, con "
    "tres secciones: ## Conversamos, ## Aprendí y ## Objetivos. Usa solo lo que aparece en el "
    "registro: no inventes ni supongas nada. Si una sección no tiene contenido, escribe "
    '"Nada en especial." Máximo 150 palabras.'
)
MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["misma", "distintas", "dudo"]},
        "conservar": {"type": "string"},
    },
    "required": ["decision", "conservar"],
}
CONTRADICTION_SCHEMA = {
    "type": "object",
    "properties": {"obsoletos": {"type": "array", "items": {"type": "integer"}}},
    "required": ["obsoletos"],
}
SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {"sugerencias": {"type": "array", "items": {"type": "string"}}},
    "required": ["sugerencias"],
}


class Cancelled(Exception):
    """Renzo volvió a escribir: se deja la tarea a medias, sin perder lo ya guardado."""


def enough_ram(min_free_gb: float) -> bool:
    return psutil.virtual_memory().available >= min_free_gb * 1024**3


class Thinker:
    """Consultas al modelo local que se pueden cancelar entre fragmentos."""

    def __init__(
        self, client: OllamaClient, model: str, options: dict[str, Any], cancel: threading.Event
    ) -> None:
        self._client = client
        self._model = model
        self._options = options
        self.cancel = cancel

    def text(self, system: str, user: str, max_tokens: int = 400, schema=None) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        options = {**self._options, "num_predict": max_tokens}
        parts: list[str] = []
        stream = self._client.stream_raw(
            self._model, messages, think=False, options=options, format=schema
        )
        try:
            for chunk in stream:
                if self.cancel.is_set():
                    raise Cancelled
                parts.append(chunk.get("message", {}).get("content", ""))
        finally:
            stream.close()  # corta la conexión: Ollama deja de generar
        return "".join(parts).strip()

    def json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(self.text(system, user, 200, schema))
        except json.JSONDecodeError:
            return {}


def unify_entities(thinker: Thinker) -> list[str]:
    """Fusiona duplicados claros; ante la duda deja una pregunta para Renzo."""
    done = []
    for pair in memoria.merge_candidates():
        a, b = pair["a"], pair["b"]
        answer = thinker.json(
            MERGE_PROMPT, f"Ficha 1:\n{pair['text_a']}\n\nFicha 2:\n{pair['text_b']}", MERGE_SCHEMA
        )
        decision = answer.get("decision")
        if decision == "misma":
            keep = answer.get("conservar") if answer.get("conservar") in (a, b) else a
            if memoria.merge_entities(keep, b if keep == a else a):
                done.append(f"fusioné {a} y {b} en {keep}")
        elif decision == "dudo":
            memoria.add_suggestion(f"¿{a} y {b} son lo mismo? Quiero ordenar mi memoria.")
            done.append(f"dudé entre {a} y {b}")
        elif decision != "distintas":
            continue  # respuesta inválida: se reintenta en la próxima reflexión
        memoria.review_pair(a, b, decision)
    return done


def retire_contradictions(thinker: Thinker) -> list[str]:
    """Marca como no vigentes los datos contradichos por otros más nuevos."""
    done = []
    for entity in memoria.entities_to_review():
        listed = entity["observations"]
        lines = "\n".join(f"{i}. ({o['date']}) {o['text']}" for i, o in enumerate(listed, 1))
        answer = thinker.json(
            CONTRADICTION_PROMPT, f"{entity['name']}:\n{lines}", CONTRADICTION_SCHEMA
        )
        chosen = [i for i in answer.get("obsoletos", []) if 1 <= i < len(listed)]
        ids = [listed[i - 1]["id"] for i in chosen]
        if memoria.retire_observations(entity["id"], ids):
            done.append(f"{entity['name']}: {len(ids)} datos ya no vigentes")
        memoria.mark_reviewed(entity["id"])
    return done


def propose_suggestions(thinker: Thinker) -> list[str]:
    room = MAX_SUGGESTIONS - memoria.pending_suggestions()
    overview = memoria.goals_overview()
    if room <= 0 or not overview:
        return []
    answer = thinker.json(SUGGESTION_PROMPT, overview, SUGGESTION_SCHEMA)
    texts = [t.strip() for t in answer.get("sugerencias", []) if t.strip()][:room]
    return [f"sugerencia: {t}" for t in texts if memoria.add_suggestion(t)]


def diary_path(memory_dir: Path, day: date) -> Path:
    return memory_dir / "diario" / f"{day.isoformat()}.md"


def diary_source(store: EventStore, day: date) -> str:
    """Registro del día: de aquí sale todo lo que el diario puede decir."""
    who = {"user_msg": "Renzo", "assistant_msg": "Tama"}
    messages = store.day_messages(day)[-MAX_DIARY_MESSAGES:]
    lines = [f"{h} {who[t]}: {' '.join(c.split())[:MAX_DIARY_CHARS]}" for h, t, c in messages]
    start = datetime.combine(day, day_time.min).astimezone(UTC)
    end = start + timedelta(days=1)
    learned = memoria.day_learned(start.isoformat(), end.isoformat())
    return "\n".join(["Conversación:", *lines, "", "Guardado en la memoria:", *learned])


def write_diary(thinker: Thinker, store: EventStore, memory_dir: Path, day: date) -> Path | None:
    """Escribe el diario de un día con conversación. Devuelve su ruta."""
    if not store.day_messages(day):
        return None
    text = thinker.text(DIARY_PROMPT, f"Día {day.isoformat()}.\n\n{diary_source(store, day)}", 400)
    if not text:
        return None
    path = diary_path(memory_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {day.isoformat()}\n\n{text}\n", encoding="utf-8")
    return path


def pending_diaries(store: EventStore, memory_dir: Path, today: date) -> list[date]:
    """Días recientes ya cerrados, con conversación y sin diario."""
    days = store.days_with_messages(today - timedelta(days=DIARY_DAYS_BACK))
    return [d for d in days if d < today and not diary_path(memory_dir, d).exists()]


def close_days(thinker: Thinker, store: EventStore, memory_dir: Path) -> None:
    for day in pending_diaries(store, memory_dir, date.today()):
        if write_diary(thinker, store, memory_dir, day):
            store.log("reflection", f"diario de {day.isoformat()}")


def reflect(thinker: Thinker, store: EventStore, memory_dir: Path) -> None:
    """Una pasada completa. Cada cambio queda guardado aunque luego se cancele."""
    close_days(thinker, store, memory_dir)
    for step in (unify_entities, retire_contradictions, propose_suggestions):
        for line in step(thinker):
            store.log("reflection", line)


class Reflector:
    """Lanza una tarea tras `idle_s` sin actividad, una vez por cada rato de inactividad."""

    def __init__(
        self,
        work: Callable[[threading.Event], None],
        idle_s: float,
        ram_ok: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.monotonic,
        tick_s: float = 5.0,
    ) -> None:
        self._work = work
        self._idle_s = idle_s
        self._ram_ok = ram_ok
        self._clock = clock
        self._tick_s = tick_s
        self._last = clock()
        self._ran = False
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self._job: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.is_alive()

    def activity(self) -> None:
        """Renzo escribió: cancela lo que esté corriendo y reinicia la espera."""
        self._last = self._clock()
        self._ran = False
        self._cancel.set()

    def run_now(self, work: Callable[[threading.Event], None]) -> None:
        """Lanza una tarea puntual (p. ej., el diario del día anterior al abrir el chat)."""
        if not self.busy:
            self._launch(work)

    def tick(self) -> None:
        if self.busy or self._ran or self._clock() - self._last < self._idle_s:
            return
        if self._ram_ok():
            self._ran = True
            self._launch(self._work)

    def _launch(self, work: Callable[[threading.Event], None]) -> None:
        cancel = self._cancel = threading.Event()

        def job() -> None:
            try:
                work(cancel)
            except Cancelled:
                log.debug("reflexión cancelada")
            except (OllamaError, OSError) as exc:
                log.warning("reflexión interrumpida: %s", exc)

        self._job = threading.Thread(target=job, name="tama-reflexion", daemon=True)
        self._job.start()

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self._tick_s):
                self.tick()

        threading.Thread(target=loop, name="tama-inactividad", daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        self._cancel.set()
