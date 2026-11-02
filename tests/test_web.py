import json
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from tama.brain import Brain
from tama.cascade import Cascade, Layer
from tama.config import Settings
from tama.events import EventStore
from tama.mcp_hub import ToolInfo, ToolOutcome
from tama.mcp_servers import memoria, notas
from tama.providers import Done, Text, ToolCall
from tama.web.app import Starting, create_app
from tests.fakes import FakeConnectivity


class Provider:
    name, model, needs_network = "local", "fake", False

    def __init__(self, *scripts):
        self.scripts = list(scripts)

    def unavailable_reason(self):
        return None

    def stream_chat(self, messages, tools=None):
        yield from (self.scripts.pop(0) if self.scripts else [Text("hola"), Done(1, 1)])


class Tools:
    def __init__(self):
        self.executed = []
        self.info = ToolInfo("notas", "delete_note", "Borra", {}, destructive=True)

    def available_tools(self, max_tools=10):
        return [self.info], []

    def find(self, name):
        return self.info if name == "delete_note" else None

    def call(self, name, args):
        if name == "next_suggestion":
            return ToolOutcome(True, memoria.NO_SUGGESTION)
        self.executed.append(name)
        return ToolOutcome(True, "borrada")

    def status(self):
        return [("notas", "listo", ["delete_note"])]


@pytest.fixture
def web(tmp_path, monkeypatch):
    def make(*scripts, timeout=5.0):
        monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
        (tmp_path / "data").mkdir(exist_ok=True)
        settings = Settings(
            data_dir=tmp_path / "data",
            memory_dir=tmp_path / "memoria",
            web_confirm_timeout_s=timeout,
        )
        store = EventStore(tmp_path / "data" / "events.db")
        cascade = Cascade([Layer(Provider(*scripts))], FakeConnectivity())
        brain = Brain(cascade, store, None, tools=Tools())
        client = TestClient(create_app(brain, settings), base_url="http://127.0.0.1")
        token = re.search(r'data-csrf="([^"]+)"', client.get("/").text).group(1)
        client.headers["x-csrf-token"] = token
        return client, brain

    return make


def events(client, turn):
    with client.stream("GET", f"/chat/{turn}/eventos") as response:
        found = []
        for line in response.iter_lines():
            if line.startswith("data: {") and line != "data: {}":
                found.append(json.loads(line[6:]))
            if line == "event: end":
                break
    return found


def test_chat_streams_text_and_grey_line(web):
    client, brain = web()
    turn = client.post("/chat", data={"text": "hola"}).json()["turn"]
    found = events(client, turn)
    assert {"kind": "text", "text": "hola"} in found
    assert any(e["kind"] == "done" and "local (fake)" in e["line"] for e in found)
    assert [r["content"] for r in brain.store.rows("user_msg")] == ["hola"]


def test_commands_run_without_the_model(web, tmp_path):
    client, brain = web()
    brain.tools.call = lambda name, args: ToolOutcome(True, f"{name}: {args}")
    memory = client.post("/chat", data={"text": "/memoria Renzo"}).json()["reply"]
    assert "open_nodes" in memory and "Renzo" in memory
    timeline = client.post("/chat", data={"text": "/memoria historial salud"}).json()["reply"]
    assert "linea_de_tiempo" in timeline and "salud" in timeline
    assert "Capas" in client.post("/chat", data={"text": "/estado"}).json()["reply"]
    assert "No hay diario" in client.post("/chat", data={"text": "/diario"}).json()["reply"]
    assert "Comandos" in client.post("/chat", data={"text": "/ayuda"}).json()["reply"]
    assert "/memoria" in client.post("/chat", data={"text": "/memorio"}).json()["reply"]
    assert "/estado" in client.post("/chat", data={"text": "/estadoo"}).json()["reply"]
    assert brain.store.rows("user_msg") == []


def test_forget_command_asks_before_deleting(web, tmp_path):
    client, brain = web()
    memoria.create_entities(
        [{"name": "Ana", "entityType": "persona", "observations": ["Reunión con cliente"]}]
    )
    brain.tools.call = lambda name, args: ToolOutcome(True, getattr(memoria, name)(**args))
    found = client.post("/chat", data={"text": "/olvidar reunion cliente"}).json()
    assert "Reunión con cliente" in found["reply"] and found["confirm"]
    assert "No se borró" in client.post("/chat/olvidar", data={"respuesta": "no"}).json()["reply"]
    assert memoria.list_cards()
    client.post("/chat", data={"text": "/olvidar reunion cliente"})
    client.post("/chat/olvidar", data={"respuesta": "si"})
    assert memoria.list_cards() == []


def test_tool_errors_are_logged(web):
    tool = [ToolCall("delete_note", {}, "c1"), Done(1, 1)], [Text("listo"), Done(1, 1)]
    client, brain = web(*tool)
    brain.tools.call = lambda name, args: ToolOutcome(False, "falló")
    brain.tools.info = ToolInfo("notas", "delete_note", "Borra", {})
    turn = client.post("/chat", data={"text": "x"}).json()["turn"]
    events(client, turn)
    assert any("delete_note" in r["content"] for r in brain.store.rows("error"))


def test_history_is_shown_escaped(web):
    client, brain = web()
    brain.store.log("user_msg", "<script>alert(1)</script>")
    text = client.get("/").text
    assert "<script>alert(1)" not in text
    assert "&lt;script&gt;alert(1)" in text


def destructive():
    return [ToolCall("delete_note", {"note_id": 3}, "c1"), Done(1, 1)], [Text("listo"), Done(1, 1)]


def test_yes_runs_the_destructive_tool(web):
    client, brain = web(*destructive())
    turn = client.post("/chat", data={"text": "borra"}).json()["turn"]
    assert client.post(f"/chat/{turn}/confirmar", data={"respuesta": "si"}).status_code == 204
    found = events(client, turn)
    assert any(
        e["kind"] == "confirm" and e["question"] == "¿Borro la nota 3? No la encuentro."
        for e in found
    )
    assert brain.tools.executed == ["delete_note"]


def test_dialog_shows_the_real_note(web):
    client, _ = web(*destructive())
    notas.create_note("prueba", "hola")
    notas.create_note("otra", "x")
    notas.create_note("tercera", "y")
    turn = client.post("/chat", data={"text": "borra"}).json()["turn"]
    client.post(f"/chat/{turn}/confirmar", data={"respuesta": "no"})
    found = events(client, turn)
    assert any(e.get("question") == "¿Borro la nota «tercera: y»?" for e in found)


def test_no_does_not_run_it(web):
    client, brain = web(*destructive())
    turn = client.post("/chat", data={"text": "borra"}).json()["turn"]
    client.post(f"/chat/{turn}/confirmar", data={"respuesta": "no"})
    events(client, turn)
    assert brain.tools.executed == []


def test_timeout_counts_as_no(web):
    client, brain = web(*destructive(), timeout=0.05)
    turn = client.post("/chat", data={"text": "borra"}).json()["turn"]
    found = events(client, turn)
    assert any(e["kind"] == "confirm_closed" for e in found)
    assert brain.tools.executed == []


def test_posts_need_the_csrf_token(web):
    client, _ = web()
    del client.headers["x-csrf-token"]
    assert client.post("/chat", data={"text": "hola"}).status_code == 403
    assert client.post("/objetivos", data={"titulo": "x"}).status_code == 403


def test_only_localhost_hosts_are_served(web):
    client, _ = web()
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403


def test_profile_is_saved_and_limited(web, tmp_path):
    client, _ = web()
    assert client.post("/memoria/perfil", data={"texto": "Vive en Santiago"}).status_code == 200
    assert "Vive en Santiago" in (tmp_path / "memoria" / "nucleo.md").read_text(encoding="utf-8")
    assert client.post("/memoria/perfil", data={"texto": "x" * 2000}).status_code == 422


def seed():
    memoria.create_entities(
        [{"name": "Ana", "entityType": "persona", "observations": ["Vive en Talca"]}]
    )
    return memoria.list_cards()[0]["id"]


def test_card_search_and_escape(web):
    client, _ = web()
    memoria.create_entities(
        [{"name": "Ana", "entityType": "persona", "observations": ["<b>negrita</b>"]}]
    )
    card = memoria.list_cards()[0]["id"]
    assert "Ana" in client.get("/memoria?q=negrita").text
    detail = client.get(f"/memoria/ficha/{card}").text
    assert "<b>negrita" not in detail


def test_edit_keeps_the_old_fact_as_before(web):
    client, _ = web()
    card = seed()
    fact = memoria.card(card)["facts"][0]["id"]
    text = client.post(f"/memoria/ficha/{card}/dato/{fact}", data={"texto": "Vive en Temuco"}).text
    assert "Vive en Temuco" in text
    assert "antes: Vive en Talca" in text


def test_forget_deletes_the_fact(web):
    client, _ = web()
    card = seed()
    fact = memoria.card(card)["facts"][0]["id"]
    client.post(f"/memoria/ficha/{card}/olvidar/{fact}")
    assert memoria.list_cards() == []


def test_manual_merge_and_split(web):
    client, _ = web()
    first = seed()
    memoria.create_entities([{"name": "Beto", "entityType": "mascota", "observations": ["Ladra"]}])
    second = next(c["id"] for c in memoria.list_cards() if c["name"] == "Beto")
    fact = memoria.card(second)["facts"][0]["id"]
    assert client.post(f"/memoria/ficha/{first}/fusionar", data={"con": second}).status_code == 200
    assert [c["name"] for c in memoria.list_cards()] == ["Ana"]
    client.post(f"/memoria/ficha/{first}/separar", data={"nombre": "Beto", "datos": [fact]})
    assert {c["name"] for c in memoria.list_cards()} == {"Ana", "Beto"}


def test_renzo_cannot_be_merged(web):
    client, _ = web()
    first = seed()
    memoria.ensure_owner("Renzo")
    memoria.create_entities([{"name": "Renzo", "entityType": "persona", "observations": ["Corre"]}])
    renzo = next(c["id"] for c in memoria.list_cards() if c["name"] == "Renzo")
    assert client.post(f"/memoria/ficha/{first}/fusionar", data={"con": renzo}).status_code == 409


def test_goal_flow(web):
    client, _ = web()
    assert "Correr" in client.post("/objetivos", data={"titulo": "Correr"}).text
    assert "12 km" in client.post("/objetivos/1/avance", data={"avance": "12 km"}).text
    client.post("/objetivos/1/editar", data={"estado": "pausado"})
    assert memoria.goals_by_status()["pausado"][0]["title"] == "Correr"
    client.post("/objetivos/1/descartar")
    assert memoria.goals_by_status()["descartado"]


def test_status_and_diary_pages(web):
    client, _ = web()
    memoria.add_suggestion("Retomar el libro")
    assert "Retomar el libro" in client.get("/estado").text
    assert "No hay diario" in client.get("/diario?fecha=2026-01-01").text


def test_static_files_are_local(web):
    client, _ = web()
    page = client.get("/").text
    assert "http://" not in page and "https://" not in page
    assert client.get("/static/htmx.min.js").status_code == 200


def test_server_answers_at_once_while_tama_starts(web):
    client, _ = web()
    release = threading.Event()

    def slow():
        release.wait(30)
        return client.app

    site = TestClient(Starting(slow), base_url="http://127.0.0.1")
    started = time.monotonic()
    response = site.get("/")
    assert time.monotonic() - started < 5
    assert response.status_code == 503
    assert "iniciando" in response.text
    release.set()
    site.app.thread.join(5)
    assert site.get("/").status_code == 200


def test_failed_start_is_shown(capsys):
    def broken():
        raise RuntimeError("sin Ollama <b>")

    starting = Starting(broken)
    starting.thread.join(5)
    response = TestClient(starting, base_url="http://127.0.0.1").get("/")
    assert "sin Ollama &lt;b&gt;" in response.text
