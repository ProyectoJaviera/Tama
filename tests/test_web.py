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
    memoria.create_entities(
        [{"name": "Renzo", "entityType": "persona", "observations": ["Toma té"]}]
    )
    memory = client.post("/chat", data={"text": "/memoria Renzo"}).json()["reply"]
    assert memory.startswith("Renzo (persona), 1 dato:") and "Toma té" in memory
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
    assert (
        "No se borró"
        in client.post("/chat/comando/confirmar", data={"respuesta": "no"}).json()["reply"]
    )
    assert memoria.list_cards()
    client.post("/chat", data={"text": "/olvidar reunion cliente"})
    client.post("/chat/comando/confirmar", data={"respuesta": "si"})
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


def test_notices_panel_and_actions(web):
    client, _ = web()
    now = memoria.local_now()
    notice = memoria.save_notice(now, "recordatorio", "r:1", "<b>Pan</b>", "pedido")
    text = client.get("/avisos").text
    assert "&lt;b&gt;Pan" in text and "<b>Pan" not in text
    assert client.get("/avisos/pendientes").json() == {"pendientes": 1}
    client.post(f"/avisos/{notice['id']}/listo")
    assert client.get("/avisos/pendientes").json() == {"pendientes": 0}
    assert client.post(f"/avisos/{notice['id']}/nada").status_code == 404


def test_reminder_commands_in_chat(web):
    client, _ = web()
    assert "No hay" in client.post("/chat", data={"text": "/recordatorios"}).json()["reply"]
    assert "agendado" in client.post("/chat", data={"text": "/resumen"}).json()["reply"]
    assert "Silencio" in client.post("/chat", data={"text": "/silencio 1"}).json()["reply"]
    assert "activos" in client.post("/chat", data={"text": "/silencio no"}).json()["reply"]


def test_rename_card_from_the_panel(web):
    client, _ = web()
    card = seed()
    assert "Anita" in client.post(f"/memoria/ficha/{card}/renombrar", data={"nombre": "Anita"}).text


def test_command_replies_stay_in_the_chat(web):
    client, _ = web()
    client.post("/chat", data={"text": "/resumen"})
    page = client.get("/").text
    assert "/resumen" in page and "Hoy no tienes nada agendado." in page


def test_cancel_command_asks_first(web):
    client, _ = web()
    memoria.crear_recordatorio("Sacar la ropa", "en 2 horas")
    shown = client.post("/chat", data={"text": "/cancela recordatorio de sacar la ropa"}).json()
    assert "«Sacar la ropa»" in shown["reply"] and shown["confirm"] == shown["reply"]
    no = client.post("/chat/comando/confirmar", data={"respuesta": "no"}).json()
    assert "No se canceló" in no["reply"]
    client.post("/chat", data={"text": "/cancelar sacar la ropa"})
    client.post("/chat/comando/confirmar", data={"respuesta": "si"})
    assert not memoria.is_pending(1)
    again = client.post("/chat", data={"text": "/cancelar sacar la ropa"}).json()
    assert "ya estaba cancelado" in again["reply"] and "confirm" not in again


def test_cancel_with_several_offers_choices(web):
    client, _ = web()
    memoria.crear_recordatorio("Sacar la basura", "en 2 horas")
    memoria.crear_recordatorio("Sacar la basura reciclable", "en 3 horas")
    shown = client.post("/chat", data={"text": "/cancelar basura"}).json()
    assert [c["command"] for c in shown["choices"]] == ["/cancelar 1", "/cancelar 2"]


def test_cancel_without_text_explains(web):
    client, _ = web()
    assert "Uso" in client.post("/chat", data={"text": "/cancelar"}).json()["reply"]


def test_applied_notice_buttons_are_marked(web):
    client, _ = web()
    notice = memoria.save_notice(memoria.local_now(), "recordatorio", "r:1", "Pan", "pedido")
    text = client.post(f"/avisos/{notice['id']}/util").text
    assert "✓ Útil" in text and 'aria-pressed="true"' in text
    assert "sin responder" in text


def test_open_reminders_feed_the_banner(web):
    client, _ = web()
    notice = memoria.save_notice(memoria.local_now(), "recordatorio", "r:1", "Pan", "pedido")
    assert [n["text"] for n in client.get("/avisos/abiertos").json()] == ["Pan"]
    client.post(f"/avisos/{notice['id']}/listo")
    assert client.get("/avisos/abiertos").json() == []


def test_choice_buttons_return_only_while_pending(web):
    client, _ = web()
    memoria.crear_recordatorio("Sacar la basura", "en 2 horas")
    memoria.crear_recordatorio("Sacar la basura reciclable", "en 3 horas")
    client.post("/chat", data={"text": "/cancelar basura"})
    assert client.get("/").text.count('data-command="/cancelar') == 2
    memoria.cancel_one(1)
    page = client.get("/").text
    assert 'data-command="/cancelar 2"' in page and 'data-command="/cancelar 1"' not in page


class Stuck(Provider):
    """Un modelo que no termina hasta que se le avisa."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def stream_chat(self, messages, tools=None):
        self.release.wait(5)
        yield from [Text("listo"), Done(1, 1)]


def stuck_web(web):
    client, brain = web()
    provider = Stuck()
    brain.cascade.layers[0].provider = provider
    return client, brain, provider


def test_second_message_while_answering_says_why(web):
    client, _, provider = stuck_web(web)
    assert client.post("/chat", data={"text": "hola"}).status_code == 200
    busy = client.post("/chat", data={"text": "otra"})
    assert busy.status_code == 409 and "mensaje anterior" in busy.json()["detail"]
    provider.release.set()


def test_a_turn_past_the_limit_no_longer_blocks_the_chat(web):
    client, _, provider = stuck_web(web)
    session = client.app.state.session
    session.turn_limit_s = 0.2
    first = client.post("/chat", data={"text": "hola"}).json()["turn"]
    time.sleep(0.3)
    second = client.post("/chat", data={"text": "otra"}).json()["turn"]
    session.finish(first)  # el turno viejo termina tarde y no libera el nuevo
    assert session._current[0] == second
    provider.release.set()


def test_an_error_in_the_turn_frees_the_chat(web):
    client, brain = web()
    brain.cascade.layers[0].provider.stream_chat = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("roto")
    )
    turn = client.post("/chat", data={"text": "hola"}).json()["turn"]
    assert any(e["kind"] == "error" and "roto" in e["text"] for e in events(client, turn))
    assert not client.app.state.session.busy()


class BusyWarmer:
    busy = True
    context = None

    def seconds(self):
        return 12


def test_chat_shows_preparing_only_offline_while_the_model_loads(web):
    client, brain = web()
    assert 'hx-get="/preparando"' in client.get("/").text
    brain.warmer = BusyWarmer()
    brain.connectivity = FakeConnectivity(online=False)
    shown = client.get("/preparando").text
    assert "preparando el modelo local… (12 s)" in shown and "every 2s" in shown
    brain.connectivity = FakeConnectivity(online=True)
    assert client.get("/preparando").text == '<div id="preparando"></div>'
    brain.connectivity = FakeConnectivity(online=False)
    brain.warmer.busy = False
    assert "every" not in client.get("/preparando").text


def seed_many(name, count):
    facts = [f"Dato número {n} de {name}" for n in range(1, count + 1)]
    memoria.create_entities([{"name": name, "entityType": "persona", "observations": facts}])


def test_card_is_opened_by_name_with_pages(web):
    client, _ = web()
    seed_many("Lucía", 25)
    first = client.get("/memoria/ficha/Luc%C3%ADa", headers={"hx-request": "true"}).text
    assert "25 datos" in first and "Página 1 de 2" in first and "Siguiente" in first
    second = client.get("/memoria/ficha/lucia?pagina=2", headers={"hx-request": "true"}).text
    assert "Página 2 de 2" in second and "Anterior" in second
    assert second.count('name="texto" value="Dato número') == 5
    full = client.get("/memoria/ficha/Lucía").text  # enlace directo: la pestaña con la ficha
    assert "<h2>Perfil</h2>" in full and "25 datos" in full
    assert "No encontré" in client.get("/memoria/ficha/Nadie").text
    assert 'href="/memoria/ficha/Luc%C3%ADa"' in client.get("/memoria").text


def test_card_counts_say_dato_in_singular(web):
    client, _ = web()
    memoria.create_entities([{"name": "Ana", "entityType": "persona", "observations": ["Toma té"]}])
    assert "1 dato<" in client.get("/memoria").text
    assert "1 dato<" in client.get("/memoria/ficha/Ana", headers={"hx-request": "true"}).text


def test_manifest_and_icons_are_served(web):
    client, _ = web()
    manifest = client.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert data["start_url"] == "/" and data["display"] == "standalone"
    sizes = {i["sizes"] for i in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in data["icons"]:
        image = client.get(icon["src"])
        assert image.status_code == 200 and image.content[:4] == b"\x89PNG"
    page = client.get("/").text
    assert 'rel="manifest"' in page and "/static/pwa.js" in page


def test_service_worker_only_caches_the_interface(web):
    client, _ = web()
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"
    code = response.text
    assert "__VERSION__" not in code
    # Nada que no sea GET, y de lo GET solo /static/; las páginas van siempre a la red.
    assert 'if (request.method !== "GET") return;' in code
    assert 'url.pathname.startsWith("/static/")' in code
    assert "cache.put" not in code  # nunca guarda respuestas que pasan por la red
    shell = re.findall(r'"(/[^"]+)"', code.split("const SHELL = [")[1].split("];")[0])
    assert shell and all(path.startswith("/static/") for path in shell)
    assert not any(word in code for word in ("/api", '/chat"', '/memoria"'))
    assert client.get("/static/offline.html").status_code == 200


TS_HOST = "mi-pc.tail1234.ts.net"


@pytest.fixture
def remote(tmp_path, monkeypatch):
    def make(user="", login=False):
        # Sin login por defecto: estas pruebas miran el nombre y la cuenta de Tailscale.
        monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
        (tmp_path / "data").mkdir(exist_ok=True)
        settings = Settings(
            data_dir=tmp_path / "data",
            memory_dir=tmp_path / "memoria",
            web_tailscale_host=TS_HOST,
            web_tailscale_user=user,
            web_login_remote=login,
        )
        cascade = Cascade([Layer(Provider())], FakeConnectivity())
        brain = Brain(cascade, EventStore(tmp_path / "data" / "events.db"), None, tools=Tools())
        return TestClient(create_app(brain, settings), base_url=f"https://{TS_HOST}")

    return make


def test_tailscale_name_needs_the_header_of_tailscale_serve(remote):
    client = remote()
    assert client.get("/").status_code == 403  # sin la cabecera: no viene de tailscale serve
    ok = client.get("/", headers={"Tailscale-User-Login": "renzo@example.com"})
    assert ok.status_code == 200 and "Tama" in ok.text
    other = TestClient(client.app, base_url="https://otro.tail1234.ts.net")
    assert other.get("/", headers={"Tailscale-User-Login": "renzo@example.com"}).status_code == 403


def test_tailscale_user_must_match_when_set(remote):
    client = remote(user="renzo@example.com")
    assert client.get("/", headers={"Tailscale-User-Login": "Renzo@Example.com"}).status_code == 200
    assert client.get("/", headers={"Tailscale-User-Login": "otra@example.com"}).status_code == 403
    local = TestClient(client.app, base_url="http://127.0.0.1")
    assert local.get("/").status_code == 200  # en el PC, igual que antes
    assert local.get("/", headers={"Tailscale-User-Login": "otra@example.com"}).status_code == 403


def test_without_tailscale_settings_only_localhost(web):
    client, _ = web()
    remote = TestClient(client.app, base_url=f"https://{TS_HOST}")
    assert remote.get("/", headers={"Tailscale-User-Login": "renzo@example.com"}).status_code == 403


class FakeEngine:
    ntfy_url, ntfy_topic, ntfy_error = "http://127.0.0.1:8090", "tama-x", ""

    def test_push(self):
        return "Aviso de prueba enviado."


def test_status_page_sends_a_test_notice(web):
    client, brain = web()
    assert "sin configurar" in client.get("/estado").text
    brain.proactive = FakeEngine()
    assert "activos, a http://127.0.0.1:8090" in client.get("/estado").text
    assert client.post("/estado/aviso-prueba").text == "Aviso de prueba enviado."
    reply = client.post("/chat", data={"text": "/aviso-prueba"}).json()["reply"]
    assert reply == "Aviso de prueba enviado."


def test_rejection_says_why_without_showing_the_settings(remote, web, capsys):
    client = remote(user="renzo@example.com")
    login = {"Tailscale-User-Login": "renzo@example.com"}
    no_header = client.get("/")
    assert no_header.text == "Tama solo acepta conexiones desde el PC o por tailscale serve."
    other = TestClient(client.app, base_url="https://otro.tail1234.ts.net").get("/", headers=login)
    assert "otro.tail1234.ts.net no coincide con TAMA_WEB_TAILSCALE_HOST" in other.text
    assert TS_HOST not in other.text
    wrong = client.get("/", headers={"Tailscale-User-Login": "otra@example.com"})
    assert "no es la de TAMA_WEB_TAILSCALE_USER" in wrong.text and "renzo@" not in wrong.text
    assert f"TAMA_WEB_TAILSCALE_HOST='{TS_HOST}'" in capsys.readouterr().err  # solo en el PC
    unset, _ = web()
    empty = TestClient(unset.app, base_url=f"https://{TS_HOST}").get("/", headers=login)
    assert empty.text.startswith("Falta TAMA_WEB_TAILSCALE_HOST")


def test_host_with_trailing_dot_or_capitals_is_the_same(remote):
    client = remote()
    login = {"Tailscale-User-Login": "renzo@example.com"}
    upper = TestClient(client.app, base_url=f"https://{TS_HOST.upper()}.")
    assert upper.get("/", headers=login).status_code == 200


def test_status_links_the_ntfy_app_to_the_own_server(tmp_path, monkeypatch):
    from tama.web.app import ntfy_link

    settings = Settings(ntfy_public_url="https://pc.tail1.ts.net:8443/", ntfy_topic="tama-a b")
    assert ntfy_link(settings) == "ntfy://pc.tail1.ts.net:8443/tama-a%20b"
    assert ntfy_link(Settings(ntfy_topic="x")) == ""
    assert ntfy_link(Settings(ntfy_public_url="http://pc:8090", ntfy_topic="x")) == ""


def test_status_shows_server_and_topic_to_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memoria",
        ntfy_public_url="https://pc.tail1.ts.net:8443",
        ntfy_topic="tama-secreto",
    )
    cascade = Cascade([Layer(Provider())], FakeConnectivity())
    brain = Brain(cascade, EventStore(tmp_path / "data" / "events.db"), None, tools=Tools())
    page = TestClient(create_app(brain, settings), base_url="http://127.0.0.1").get("/estado").text
    assert 'value="https://pc.tail1.ts.net:8443"' in page and 'value="tama-secreto"' in page
    assert "Usar otro servidor" in page


def test_day_label_names_today_yesterday_and_older_days():
    from datetime import date

    from tama.web.app import day_label

    today = date(2026, 11, 14)
    assert day_label(today, today) == "Hoy"
    assert day_label(date(2026, 11, 13), today) == "Ayer"
    assert day_label(date(2026, 11, 12), today) == "jueves 12 de noviembre"
