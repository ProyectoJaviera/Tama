"""Motor proactivo: recordatorios, resumen matutino y seguimiento, con Silencio Operativo.

Todo es determinista y no usa el modelo: lee lo guardado y decide cuándo avisar. Corre en un hilo
aparte; los canales reciben el aviso ya aprobado (web, CLI y ntfy).
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from tama.events import EventStore
from tama.mcp_servers import memoria

log = logging.getLogger(__name__)

MAX_SUMMARY_LINES = 5
BUSY_S = 60
IGNORED_AFTER = timedelta(hours=24)
FOLLOW_UP_EVERY = timedelta(days=7)
SNOOZE = timedelta(hours=1)

Listener = Callable[[dict], None]


@dataclass
class Rules:
    quiet_start: int = 22
    quiet_end: int = 8
    daily_cap: int = 3
    morning_hour: int = 8
    stale_days: int = 14
    # Si Tama estuvo apagada en la mañana, el resumen sale al iniciar solo antes de esta hora.
    summary_until: int = 12


def is_quiet(now: datetime, rules: Rules) -> bool:
    return memoria.in_quiet_window(now, rules.quiet_start, rules.quiet_end)


def morning_summary(now: datetime, stale_days: int = 14) -> str:
    """Agenda del día, objetivos y una sugerencia, solo con lo guardado. Máximo 5 líneas."""
    lines = [f"Hoy: {text}" for text in memoria.reminders_on(now.date())]
    lines += memoria.goal_notes(now.date(), stale_days)
    pending = memoria.pending_list()
    if pending:
        lines.append(f"Sugerencia: {pending[0]}")
    return "\n".join(lines[:MAX_SUMMARY_LINES]) or "Hoy no tienes nada agendado."


class Engine:
    def __init__(
        self,
        store: EventStore,
        rules: Rules,
        clock: Callable[[], datetime] = memoria.local_now,
        ntfy_url: str = "",
        ntfy_topic: str = "",
        post: Callable[..., object] = httpx.post,
    ) -> None:
        self.store = store
        self.rules = rules
        self.clock = clock
        self.ntfy_url = ntfy_url.rstrip("/")
        self.ntfy_topic = ntfy_topic
        self.ntfy_error = ""
        self.post = post
        self.listeners: list[Listener] = []
        self.busy: Callable[[], bool] = lambda: False
        self._last_activity: datetime | None = None
        self._stop = threading.Event()

    def activity(self) -> None:
        self._last_activity = self.clock()

    def _occupied(self, now: datetime) -> bool:
        if self.busy():
            return True
        last = self._last_activity
        return last is not None and (now - last).total_seconds() < BUSY_S

    def tick(self) -> None:
        now = self.clock()
        if self._occupied(now):
            # Mientras Renzo conversa solo pasan los urgentes.
            self._reminders(now, hold=True)
            return
        muted = memoria.muted_until(now)
        quiet = is_quiet(now, self.rules) or muted
        self._reminders(now, quiet)
        memoria.age_notices(now - IGNORED_AFTER)
        if quiet:
            return
        for notice in memoria.snoozed_notices(now):
            self.deliver(notice["kind"], notice["key"], notice["text"], "pospuesto", notice["id"])
        self._repeat_ignored(now)
        self._unrequested(now)

    def _reminders(self, now: datetime, hold: bool) -> None:
        for reminder in memoria.due_reminders(now):
            if hold and not reminder["urgent"]:
                continue
            reason = "urgente" if reminder["urgent"] else "pedido por Renzo"
            self.deliver("recordatorio", f"recordatorio:{reminder['id']}", reminder["text"], reason)
            memoria.finish_reminder(reminder["id"])

    def _repeat_ignored(self, now: datetime) -> None:
        """Un aviso ignorado se repite una sola vez, y solo si Renzo lo pidió."""
        for notice in memoria.ignored_once():
            self.deliver(notice["kind"], notice["key"], notice["text"], "repetición", notice["id"])

    def _room(self, now: datetime) -> bool:
        return memoria.unrequested_today(now.date()) < self.rules.daily_cap

    def _unrequested(self, now: datetime) -> None:
        if (
            self._room(now)
            and self.rules.morning_hour <= now.hour < self.rules.summary_until
            and not memoria.notice_today("resumen", now.date())
        ):
            text = morning_summary(now, self.rules.stale_days)
            self.deliver("resumen", f"resumen:{now.date()}", text, "resumen matutino")
        for goal in memoria.stale_goals(now.date(), self.rules.stale_days):
            key = f"objetivo:{goal['id']}"
            if self._room(now) and not memoria.asked_since(key, now - FOLLOW_UP_EVERY):
                text = f"¿Cómo vas con «{goal['title']}»? Lleva {goal['days']} días sin avance."
                self.deliver("seguimiento", key, text, "objetivo sin avance")
        while self._room(now):
            text = memoria.take_suggestion()
            if text is None:
                return
            self.deliver(
                "sugerencia", f"sugerencia:{text[:60]}", text, "sugerencia de la reflexión"
            )

    def deliver(
        self, kind: str, key: str, text: str, reason: str, notice_id: int | None = None
    ) -> dict | None:
        """Registra el aviso y lo entrega; lo que Renzo silenció no se muestra."""
        if memoria.is_silenced(key):
            return None
        notice = memoria.save_notice(self.clock(), kind, key, text, reason, notice_id)
        self.store.log("proactive", f"{kind} ({reason}): {text}")
        for listener in self.listeners:
            listener(notice)
        self._push(text)
        return notice

    def _push(self, text: str) -> None:
        if not self.ntfy_url or not self.ntfy_topic:
            return
        self.ntfy_error = self._send_ntfy(text)
        if self.ntfy_error:
            log.debug("ntfy no respondió: %s", self.ntfy_error)

    def _send_ntfy(self, text: str) -> str:
        """Publica en el tópico de ntfy; devuelve el error, o "" si salió bien."""
        try:
            response = self.post(
                f"{self.ntfy_url}/{self.ntfy_topic}",
                content=text.encode("utf-8"),
                headers={"Title": "Tama"},
                timeout=5,
            )
        except httpx.HTTPError as exc:
            return str(exc) or type(exc).__name__
        status = getattr(response, "status_code", 200)
        return f"ntfy respondió {status}" if status >= 400 else ""

    def test_push(self) -> str:
        """Aviso de prueba por ntfy, pedido por Renzo: no cuenta en el tope ni queda en Avisos."""
        if not self.ntfy_url or not self.ntfy_topic:
            return (
                "ntfy no está configurado: define TAMA_NTFY_URL y TAMA_NTFY_TOPIC en .env "
                "(`uv run tama celular` muestra cómo)."
            )
        self.ntfy_error = self._send_ntfy(
            "Aviso de prueba: si lo ves en el celular, ntfy funciona."
        )
        if self.ntfy_error:
            return f"No pude enviar el aviso de prueba: {self.ntfy_error}"
        return "Aviso de prueba enviado. Debería llegarte al celular en unos segundos."

    def start(self, every_s: float = 20.0) -> None:
        def loop() -> None:
            while not self._stop.wait(every_s):
                try:
                    self.tick()
                except Exception:
                    # Un error en una pasada no puede apagar los avisos para siempre.
                    log.exception("falló una pasada del motor proactivo")

        threading.Thread(target=loop, name="tama-proactivo", daemon=True).start()

    def close(self) -> None:
        self._stop.set()


def act_on(notice_id: int, action: str, now: datetime) -> str:
    """Las respuestas de Renzo a un aviso."""
    if action == "posponer":
        memoria.snooze_notice(notice_id, now + SNOOZE)
        return "Te aviso de nuevo en una hora."
    if action == "silenciar":
        memoria.silence_notice(notice_id)
        return "No te lo recordaré más."
    if action in ("util", "molesto"):
        memoria.rate_notice(notice_id, action)
        return "Anotado."
    memoria.close_notice(notice_id)
    return "Listo."
