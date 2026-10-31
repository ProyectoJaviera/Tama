import json

import pytest

from tama import cli
from tama.cascade import Answered, Cascade, Layer
from tama.events import EventStore
from tama.mcp_servers import memoria
from tama.providers import Done, Text, ToolCall
from tests.fakes import FakeConnectivity


@pytest.fixture(autouse=True)
def memory_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()


def entity(name, type_, *obs):
    return {"name": name, "entityType": type_, "observations": list(obs)}


def rows():
    with memoria.connect() as db:
        return [tuple(r) for r in db.execute("SELECT content, valid FROM observations ORDER BY id")]


def test_update_keeps_the_old_fact_as_not_valid():
    memoria.create_entities([entity("Halloween", "plan", "Se disfrazará de Shrek")])
    text = memoria.update_observation("Halloween", "Se disfrazará de Shrek", "Irá de Bestia")
    assert "corregido" in text
    assert rows() == [("Se disfrazará de Shrek", 0), ("Irá de Bestia", 1)]
    assert "Shrek" not in memoria.search_nodes("Halloween")


def test_update_is_not_destructive():
    tools = {t.name: t for t in memoria.server._tool_manager.list_tools()}
    annotations = tools["update_observation"].annotations
    assert not (annotations and annotations.destructive_hint)


def test_date_copied_from_the_search_still_matches():
    memoria.create_entities([entity("Halloween", "plan", "Se disfrazará de Shrek")])
    memoria.update_observation("Halloween", "Se disfrazará de Shrek (2026-10-31)", "Irá de Bestia")
    assert rows()[0][1] == 0
    memoria.create_entities([entity("Pie", "salud", "Está inflamado")])
    memoria.delete_observations(
        [{"entityName": "Pie", "observations": ["Está inflamado (2026-10-31)"]}]
    )
    assert rows()[-1] == ("Está inflamado", 0)


def test_delete_says_when_nothing_changed():
    memoria.create_entities([entity("Pie", "salud", "Está inflamado")])
    text = memoria.delete_observations([{"entityName": "Pie", "observations": ["otra cosa"]}])
    assert "nada cambió" in text


def test_update_says_when_the_old_fact_is_missing():
    memoria.create_entities([entity("Pie", "salud", "Está inflamado")])
    assert "no encontré" in memoria.update_observation("Pie", "otra cosa", "Ya sanó")
    assert ("Ya sanó", 1) in rows()


def seed_events(tmp_path):
    store = EventStore(tmp_path / "data" / "events.db")
    store.log("user_msg", "Hoy tuve una reunión pesada con un cliente")
    store.log("assistant_msg", "Qué pesada esa reunión con el cliente")
    store.log("user_msg", "Me duele el pie")
    return store


def test_find_searches_inside_observations(tmp_path):
    seed_events(tmp_path).close()
    memoria.create_entities([entity("trabajo", "proyecto", "Reunión pesada con un cliente")])
    found = json.loads(memoria.find_memory("reunion con cliente"))
    assert [i["texto"] for i in found["items"]] == ["trabajo: Reunión pesada con un cliente"]
    assert found["mensajes"] == 2


def test_find_offers_similar_entities():
    memoria.create_entities([entity("Halloween", "plan", "Disfraz")])
    found = json.loads(memoria.find_memory("Halowen"))
    assert found["items"] == []
    assert found["parecidas"] == ["Halloween"]


def test_forget_removes_the_fact_and_redacts_messages(tmp_path):
    seed_events(tmp_path).close()
    memoria.create_entities([entity("trabajo", "proyecto", "Reunión con un cliente")])
    item = json.loads(memoria.find_memory("reunion cliente"))["items"][0]
    memoria.forget_memory([], [item["dato"]], "reunion cliente")
    assert rows() == []
    assert memoria.recent_events(consulta="reunion") == "No encontré conversaciones en ese período."
    assert "Me duele el pie" in memoria.recent_events()


class Tools:
    def __init__(self, found):
        self.found, self.calls = found, []

    def call(self, name, args):
        self.calls.append((name, args))
        text = json.dumps(self.found) if name == "find_memory" else "listo"
        return type("Outcome", (), {"ok": True, "text": text})()


class Brain:
    def __init__(self, found):
        self.tools = Tools(found)


ITEMS = [{"dato": 1, "texto": "a"}, {"dato": 2, "texto": "b"}, {"entidad": "x", "texto": "c"}]


def answers(monkeypatch, *values):
    pending = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(pending))


def test_olvidar_picks_one_number(monkeypatch):
    brain = Brain({"items": ITEMS, "mensajes": 0, "parecidas": []})
    answers(monkeypatch, "2", "s")
    cli.forget(brain, "tema")
    assert brain.tools.calls[-1] == (
        "forget_memory",
        {"entidades": [], "datos": [2], "consulta": "tema"},
    )


def test_olvidar_all(monkeypatch):
    brain = Brain({"items": ITEMS, "mensajes": 0, "parecidas": []})
    answers(monkeypatch, "todas", "s")
    cli.forget(brain, "tema")
    assert brain.tools.calls[-1][1]["entidades"] == ["x"]
    assert brain.tools.calls[-1][1]["datos"] == [1, 2]


def test_olvidar_needs_confirmation(monkeypatch):
    brain = Brain({"items": ITEMS, "mensajes": 0, "parecidas": []})
    answers(monkeypatch, "1", "n")
    cli.forget(brain, "tema")
    assert [c[0] for c in brain.tools.calls] == ["find_memory"]


def test_olvidar_shows_similar_when_nothing_matches(monkeypatch, capsys):
    brain = Brain({"items": [], "mensajes": 0, "parecidas": ["Halloween"]})
    cli.forget(brain, "tema")
    assert "Halloween" in capsys.readouterr().out


def test_olvidar_only_messages(monkeypatch):
    brain = Brain({"items": [], "mensajes": 3, "parecidas": []})
    answers(monkeypatch, "s")
    cli.forget(brain, "tema")
    assert brain.tools.calls[-1][0] == "forget_memory"


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        (
            "delete_observations",
            {"deletions": [{"entityName": "H", "observations": ["se disfrazará de Shrek"]}]},
            "¿Borro de la memoria: «se disfrazará de Shrek»?",
        ),
        ("delete_note", {"note_id": 3}, "¿Borro la nota 3?"),
        ("overwrite_file", {"path": "a.txt"}, "¿Reemplazo todo el contenido de a.txt?"),
    ],
)
def test_confirmation_is_readable(name, args, expected):
    assert cli.confirm_question(ToolCall(name, args, "c")) == expected


def test_printable_removes_emojis_on_windows(monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    assert cli.printable("Listo 🎃✨😊") == "Listo "
    assert cli.printable("¡Sí, ñandú!") == "¡Sí, ñandú!"


class Provider:
    def __init__(self, name, needs_network):
        self.name, self.model, self.needs_network = name, "m", needs_network

    def unavailable_reason(self):
        return None

    def stream_chat(self, messages, tools=None):
        yield Text("hola")
        yield Done(1, 1)


def test_skipped_layers_are_reported():
    layers = [Layer(Provider("nube", True)), Layer(Provider("local", False))]
    events = list(Cascade(layers, FakeConnectivity(False)).turn([]))
    answered = next(e for e in events if isinstance(e, Answered))
    assert answered.provider == "local"
    assert answered.skipped == ("nube: sin internet",)
