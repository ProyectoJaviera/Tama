from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from tama.events import EventStore
from tama.mcp_servers import memoria
from tama.proactive import Engine, Rules, act_on, is_quiet, morning_summary

ZONE = ZoneInfo("America/Santiago")
WEDNESDAY = datetime(2026, 11, 4, 10, 0, tzinfo=ZONE)


@pytest.fixture(autouse=True)
def memory_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))


class Clock:
    def __init__(self, now=WEDNESDAY):
        self.now = now

    def __call__(self):
        return self.now


def engine(clock=None, **kwargs):
    item = Engine(EventStore(":memory:"), Rules(), clock or Clock(), **kwargs)
    item.seen = []
    item.listeners.append(lambda notice: item.seen.append(notice))
    return item


def remind(moment, urgent=False, text="Sacar la basura"):
    return memoria.crear_recordatorio(text, moment.isoformat(), urgent)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("en 2 minutos", datetime(2026, 11, 4, 10, 2, tzinfo=ZONE)),
        ("en una hora", datetime(2026, 11, 4, 11, 0, tzinfo=ZONE)),
        ("en media hora", datetime(2026, 11, 4, 10, 30, tzinfo=ZONE)),
        ("la próxima semana", datetime(2026, 11, 11, 9, 0, tzinfo=ZONE)),
        ("en tres dias", datetime(2026, 11, 7, 10, 0, tzinfo=ZONE)),
        ("mañana a las 9", datetime(2026, 11, 5, 9, 0, tzinfo=ZONE)),
        ("mañana a las 3 de la tarde", datetime(2026, 11, 5, 15, 0, tzinfo=ZONE)),
        ("hoy a las 18:30", datetime(2026, 11, 4, 18, 30, tzinfo=ZONE)),
        ("el viernes a las 10", datetime(2026, 11, 6, 10, 0, tzinfo=ZONE)),
        ("el miércoles a las 10", datetime(2026, 11, 11, 10, 0, tzinfo=ZONE)),
        ("el 5 de diciembre a las 8", datetime(2026, 12, 5, 8, 0, tzinfo=ZONE)),
    ],
)
def test_when_is_resolved(text, expected):
    assert memoria.resolve_when(text, WEDNESDAY) == expected


def test_unknown_date_is_none():
    assert memoria.resolve_when("algún día", WEDNESDAY) is None


def test_due_reminder_is_delivered_and_logged_once():
    item = engine()
    remind(WEDNESDAY - timedelta(minutes=1))
    item.tick()
    item.tick()
    reminders = [n for n in item.seen if n["kind"] == "recordatorio"]
    assert [n["text"] for n in reminders] == ["Sacar la basura"]
    assert any("Sacar la basura" in r["content"] for r in item.store.rows("proactive"))


def test_future_reminder_waits():
    item = engine()
    remind(WEDNESDAY + timedelta(minutes=5))
    item.tick()
    assert item.seen == [] or all(n["kind"] != "recordatorio" for n in item.seen)


def night():
    return datetime(2026, 11, 4, 23, 30, tzinfo=ZONE)


def test_quiet_hours_cross_midnight():
    rules = Rules()
    assert is_quiet(night(), rules)
    assert is_quiet(datetime(2026, 11, 4, 7, 59, tzinfo=ZONE), rules)
    assert not is_quiet(datetime(2026, 11, 4, 8, 0, tzinfo=ZONE), rules)


def test_night_holds_normal_reminders_until_morning():
    clock = Clock(night())
    item = engine(clock)
    remind(clock.now - timedelta(minutes=1))
    item.tick()
    assert item.seen == []
    clock.now = datetime(2026, 11, 5, 8, 1, tzinfo=ZONE)
    item.tick()
    assert any(n["kind"] == "recordatorio" for n in item.seen)


def test_urgent_reminder_passes_at_night():
    clock = Clock(night())
    item = engine(clock)
    remind(clock.now - timedelta(minutes=1), urgent=True)
    item.tick()
    assert [n["kind"] for n in item.seen] == ["recordatorio"]


def test_no_notice_while_renzo_talks():
    clock = Clock()
    item = engine(clock)
    remind(clock.now - timedelta(minutes=1))
    item.activity()
    item.tick()
    assert item.seen == []
    clock.now += timedelta(minutes=2)
    item.tick()
    assert item.seen


def test_busy_session_holds_notices():
    item = engine()
    item.busy = lambda: True
    remind(WEDNESDAY - timedelta(minutes=1))
    item.tick()
    assert item.seen == []


def test_daily_cap_does_not_count_requested_reminders():
    item = engine()
    for number in range(6):
        memoria.add_suggestion(f"Sugerencia {number}")
    for number in range(4):
        remind(WEDNESDAY - timedelta(minutes=1), text=f"Pedido {number}")
    item.tick()
    kinds = [n["kind"] for n in item.seen]
    assert kinds.count("recordatorio") == 4
    assert len([k for k in kinds if k != "recordatorio"]) == 3


def test_morning_summary_once_a_day():
    item = engine()
    item.tick()
    item.tick()
    assert [n["kind"] for n in item.seen].count("resumen") == 1


def test_summary_uses_only_saved_data_in_five_lines():
    for number in range(8):
        remind(WEDNESDAY + timedelta(hours=1), text=f"Tarea {number}")
    text = morning_summary(WEDNESDAY)
    assert len(text.splitlines()) == 5
    assert "Tarea 0" in text


def test_empty_summary_says_so():
    assert morning_summary(WEDNESDAY) == "Hoy no tienes nada agendado."


def stale_goal(days=20):
    memoria.create_goal("Leer un libro")
    with memoria.connect() as db:
        moment = (WEDNESDAY - timedelta(days=days)).isoformat()
        db.execute("UPDATE goals SET updated_at = ?", (moment,))


def test_stale_goal_is_asked_once_a_week():
    clock = Clock()
    item = engine(clock)
    stale_goal()
    item.tick()
    clock.now += timedelta(days=1)
    item.tick()
    asked = [n for n in item.seen if n["kind"] == "seguimiento"]
    assert len(asked) == 1
    clock.now += timedelta(days=7)
    item.tick()
    assert len([n for n in item.seen if n["kind"] == "seguimiento"]) == 2


def test_snooze_brings_it_back_in_an_hour():
    clock = Clock()
    item = engine(clock)
    remind(clock.now - timedelta(minutes=1))
    item.tick()
    notice = next(n for n in item.seen if n["kind"] == "recordatorio")
    act_on(notice["id"], "posponer", clock.now)
    count = len(item.seen)
    item.tick()
    assert len(item.seen) == count
    clock.now += timedelta(minutes=61)
    item.tick()
    assert item.seen[-1]["text"] == "Sacar la basura"


def test_ignored_reminder_repeats_only_once():
    clock = Clock()
    item = engine(clock)
    remind(clock.now - timedelta(minutes=1))
    item.tick()
    for _ in range(4):
        clock.now += timedelta(hours=25)
        item.tick()
    assert len([n for n in item.seen if n["kind"] == "recordatorio"]) == 2


def test_do_not_remind_again_is_respected():
    clock = Clock()
    item = engine(clock)
    stale_goal()
    item.tick()
    notice = next(n for n in item.seen if n["kind"] == "seguimiento")
    act_on(notice["id"], "silenciar", clock.now)
    clock.now += timedelta(days=8)
    item.tick()
    assert len([n for n in item.seen if n["kind"] == "seguimiento"]) == 1


def test_mute_holds_all_but_urgent():
    item = engine()
    memoria.mute_until(datetime(2099, 1, 1, tzinfo=ZONE))
    remind(WEDNESDAY - timedelta(minutes=1), text="Normal")
    remind(WEDNESDAY - timedelta(minutes=1), urgent=True, text="Urgente")
    item.tick()
    assert [n["text"] for n in item.seen] == ["Urgente"]


def test_weekly_report_counts_ratings():
    item = engine()
    remind(WEDNESDAY - timedelta(minutes=1))
    item.tick()
    notice = next(n for n in item.seen if n["kind"] == "recordatorio")
    act_on(notice["id"], "util", WEDNESDAY)
    report = memoria.weekly_report(WEDNESDAY.date())
    assert report["útiles"] == 1


def test_ntfy_failure_does_not_stop_tama():
    def broken(*args, **kwargs):
        raise httpx.ConnectError("sin ntfy")

    item = engine(ntfy_url="http://192.168.1.2:8090", ntfy_topic="x", post=broken)
    remind(WEDNESDAY - timedelta(minutes=1))
    item.tick()
    assert item.seen
    assert "sin ntfy" in item.ntfy_error


def test_ntfy_gets_the_notice():
    sent = []
    item = engine(
        ntfy_url="http://192.168.1.2:8090",
        ntfy_topic="tema",
        post=lambda url, **kwargs: sent.append((url, kwargs["content"])),
    )
    remind(WEDNESDAY - timedelta(minutes=1))
    item.tick()
    assert ("http://192.168.1.2:8090/tema", b"Sacar la basura") in sent


def test_rename_keeps_old_name_as_alias():
    memoria.create_entities(
        [{"name": "Rocky", "entityType": "mascota", "observations": ["Es un perro"]}]
    )
    assert memoria.rename_entity("Rocky", "Toby") == "Listo."
    assert "Toby (mascota)" in memoria.search_nodes("Rocky")


def test_renzo_cannot_be_renamed():
    memoria.ensure_owner("Renzo")
    assert "No encontré" in memoria.rename_entity("Renzo", "Otro")


def test_cancel_is_destructive():
    tools = {t.name: t for t in memoria.server._tool_manager.list_tools()}
    assert tools["cancel_reminder"].annotations.destructive_hint


def test_urgent_passes_while_renzo_talks():
    item = engine()
    remind(WEDNESDAY - timedelta(minutes=1), text="Normal")
    remind(WEDNESDAY - timedelta(minutes=1), urgent=True, text="Urgente")
    item.activity()
    item.tick()
    assert [n["text"] for n in item.seen] == ["Urgente"]


def test_reminder_in_quiet_hours_says_when(monkeypatch):
    monkeypatch.setattr(memoria, "local_now", lambda: night())
    text = remind(night() + timedelta(minutes=2))
    assert "no molestar" in text and "mañana 08:00" in text
    assert "no molestar" not in remind(night() + timedelta(minutes=2), urgent=True)


def test_reminder_state_is_the_same_everywhere(monkeypatch):
    monkeypatch.setattr(memoria, "local_now", lambda: WEDNESDAY)
    item = engine()
    remind(WEDNESDAY - timedelta(minutes=1))
    remind(WEDNESDAY + timedelta(hours=2), text="Llamar a Ana")
    item.tick()
    notice = next(n for n in item.seen if n["kind"] == "recordatorio")
    act_on(notice["id"], "listo", WEDNESDAY)
    lines = memoria.list_reminders()
    assert "hoy 09:59 · Sacar la basura · listo" in lines
    assert "hoy 12:00 · Llamar a Ana · pendiente" in lines
    assert memoria.reminders_on(WEDNESDAY.date()) == [
        "09:59 Sacar la basura (listo)",
        "12:00 Llamar a Ana (pendiente)",
    ]


def test_snoozed_reminder_shows_until_when(monkeypatch):
    monkeypatch.setattr(memoria, "local_now", lambda: WEDNESDAY)
    item = engine()
    remind(WEDNESDAY - timedelta(minutes=1))
    item.tick()
    notice = next(n for n in item.seen if n["kind"] == "recordatorio")
    act_on(notice["id"], "posponer", WEDNESDAY)
    assert "pospuesto hasta las 11:00" in memoria.list_reminders()


@pytest.fixture
def chores(monkeypatch):
    monkeypatch.setattr(memoria, "local_now", lambda: WEDNESDAY)
    item = engine()
    remind(WEDNESDAY - timedelta(minutes=5), text="Sacar la ropa")
    item.tick()
    done = next(n for n in item.seen if n["kind"] == "recordatorio")
    act_on(done["id"], "listo", WEDNESDAY)
    remind(WEDNESDAY + timedelta(hours=2), text="Sacar la basura")
    remind(WEDNESDAY + timedelta(hours=3), text="Llamar a la mamá")
    return item


def test_cancel_by_text_finds_one(chores):
    found, _ = memoria.find_to_cancel("recordatorio de sacar la basura")
    assert [r["text"] for r in found] == ["Sacar la basura"]
    assert memoria.cancel_question(found[0]) == (
        "¿Cancelo el recordatorio «Sacar la basura» de hoy a las 12:00?"
    )


def test_cancel_by_text_ignores_accents_and_case(chores):
    found, _ = memoria.find_to_cancel("LLAMAR A LA MAMA")
    assert [r["text"] for r in found] == ["Llamar a la mamá"]


def test_cancel_by_text_with_several_asks_which(chores):
    remind(WEDNESDAY + timedelta(hours=4), text="Sacar la basura reciclable")
    found, message = memoria.find_to_cancel("basura")
    assert len(found) == 2 and "cuál" in message
    found, _ = memoria.find_to_cancel("sacar la basura")
    assert [r["text"] for r in found] == ["Sacar la basura"]


def test_cancel_done_reminder_says_so(chores):
    found, message = memoria.find_to_cancel("sacar la ropa")
    assert found == []
    assert "ya se avisó y está marcado como listo" in message


def test_cancel_done_reminder_by_number_says_so(chores):
    found, message = memoria.find_to_cancel("1")
    assert found == [] and "marcado como listo" in message


def test_cancel_without_match_lists_pending(chores):
    found, message = memoria.find_to_cancel("regar las plantas")
    assert found == [] and "No encontré" in message and "Sacar la basura" in message


def test_cancel_tool_by_text(chores):
    assert "cancelado" in memoria.cancel_reminder(texto="la basura")
    with pytest.raises(memoria.ToolError, match="ya estaba cancelado"):
        memoria.cancel_reminder(texto="la basura")


def test_missed_summary_goes_out_before_noon_only():
    late = Clock(datetime(2026, 11, 4, 19, 59, tzinfo=ZONE))
    item = engine(late)
    item.tick()
    assert all(n["kind"] != "resumen" for n in item.seen)
    item.clock = Clock(datetime(2026, 11, 5, 11, 30, tzinfo=ZONE))
    item.tick()
    assert [n["kind"] for n in item.seen].count("resumen") == 1


def test_model_cancel_shows_the_real_reminder(chores):
    from tama.chat import confirm_question
    from tama.providers import ToolCall

    call = ToolCall("cancel_reminder", {"texto": "la basura"})
    assert (
        confirm_question(call) == "¿Cancelo el recordatorio «Sacar la basura» de hoy a las 12:00?"
    )


def test_update_reminder_tells_the_exact_time(monkeypatch):
    monkeypatch.setattr(memoria, "local_now", lambda: WEDNESDAY)
    remind(WEDNESDAY + timedelta(minutes=5), text="Sacar la basura")
    text = memoria.update_reminder(1, texto="Sacar la basura y la orgánica")
    assert "avisaré hoy 10:05" in text and "orgánica" in text


def test_test_notice_goes_to_ntfy_without_counting_as_a_notice():
    sent = []
    item = engine(
        ntfy_url="http://127.0.0.1:8090",
        ntfy_topic="tama-abc",
        post=lambda url, **kwargs: sent.append((url, kwargs["content"])),
    )
    assert item.test_push().startswith("Aviso de prueba enviado")
    assert sent[0][0] == "http://127.0.0.1:8090/tama-abc" and b"prueba" in sent[0][1]
    assert memoria.list_notices() == []


def test_test_notice_explains_what_is_missing_or_failing():
    assert "TAMA_NTFY_URL" in engine().test_push()

    class Refused:
        status_code = 403

    item = engine(ntfy_url="http://127.0.0.1:8090", ntfy_topic="x", post=lambda *a, **k: Refused())
    assert item.test_push() == "No pude enviar el aviso de prueba: ntfy respondió 403"
    assert item.ntfy_error == "ntfy respondió 403"


def test_phone_guide_uses_the_tailscale_name_and_a_long_topic():
    from tama import celular
    from tama.config import Settings

    text = celular.guide(Settings(ntfy_topic=""), name="mi-pc.tail1.ts.net")
    assert "tailscale serve --bg --https=443 http://127.0.0.1:8765" in text
    assert "TAMA_WEB_TAILSCALE_HOST=mi-pc.tail1.ts.net" in text
    topic = next(line for line in text.splitlines() if "TAMA_NTFY_TOPIC=" in line).split("=")[1]
    assert topic.startswith("tama-") and len(topic) >= 30
    kept = celular.guide(Settings(ntfy_topic="tama-ya-tengo"), name="x.ts.net")
    assert "TAMA_NTFY_TOPIC=tama-ya-tengo" in kept
