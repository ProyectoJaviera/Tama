"""Extremo a extremo en un navegador real, con todo lo que no sea localhost bloqueado."""

import socket
import threading
import time

import pytest
import uvicorn

from tama.brain import Brain
from tama.cascade import Cascade, Layer
from tama.config import Settings
from tama.events import EventStore
from tama.mcp_servers import memoria
from tama.providers import Done, Text, ToolCall
from tama.web.app import create_app
from tests.fakes import FakeConnectivity
from tests.test_web import Provider, Tools

sync_api = pytest.importorskip("playwright.sync_api")


def launch(playwright):
    for options in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return playwright.chromium.launch(**options)
        except sync_api.Error:
            continue
    pytest.skip("no hay un navegador disponible")


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memoria")
    provider = Provider(
        [Text("Hola, Renzo."), Done(1, 1)],
        [ToolCall("delete_note", {"note_id": 3}, "c1"), Done(1, 1)],
        [Text("Listo, la borré."), Done(1, 1)],
    )
    tools = Tools()
    cascade = Cascade([Layer(provider)], FakeConnectivity())
    brain = Brain(cascade, EventStore(tmp_path / "data" / "events.db"), None, tools=tools)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(brain, settings), port=port, log_level="error")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}", tools
    server.should_exit = True


@pytest.fixture
def page():
    with sync_api.sync_playwright() as playwright:
        browser = launch(playwright)
        tab = browser.new_page()
        tab.outside = []

        def guard(route):
            url = route.request.url
            if url.startswith("http://127.0.0.1"):
                route.continue_()
            else:
                tab.outside.append(url)
                route.abort()

        tab.route("**/*", guard)
        yield tab
        browser.close()


def say(page, text):
    page.fill("#chat-text", text)
    page.click("#chat-form button")


def test_chat_and_destructive_confirmation(site, page):
    url, tools = site
    page.goto(url)
    say(page, "hola")
    page.wait_for_selector(".assistant:has-text('Hola, Renzo.')")
    page.wait_for_selector(".line:has-text('local (fake)')")

    page.wait_for_function("!document.querySelector('#chat-form button').disabled")
    say(page, "borra la nota 3")
    page.wait_for_selector("#confirm[open]")
    assert page.inner_text("#confirm-text") == "¿Borro la nota 3? No la encuentro."
    page.click("#confirm-yes")
    page.wait_for_selector(".assistant:has-text('Listo, la borré.')")
    assert tools.executed == ["delete_note"]
    assert page.outside == []


def test_edit_a_fact_in_the_memory_panel(site, page):
    url, _ = site
    memoria.create_entities(
        [{"name": "Ana", "entityType": "persona", "observations": ["Vive en Talca"]}]
    )
    page.goto(url + "/memoria")
    page.click("text=Ana")
    page.fill("#ficha-actual input[name=texto]", "Vive en Temuco")
    page.click("#ficha-actual button:has-text('Guardar')")
    page.wait_for_selector("text=antes: Vive en Talca")
    assert "Vive en Temuco" in memoria.search_nodes("Ana")
    assert page.outside == []


def test_goal_panel_uses_no_outside_requests(site, page):
    url, _ = site
    page.goto(url + "/objetivos")
    page.fill("input[name=titulo]", "Correr 10K")
    page.click("button:has-text('Crear')")
    page.wait_for_selector("input[value='Correr 10K']")
    assert memoria.goals_by_status()["activo"][0]["title"] == "Correr 10K"
    assert page.outside == []


def test_cancel_offers_buttons_that_ask_first(site, page):
    url, _ = site
    memoria.crear_recordatorio("Sacar la basura", "en 2 horas")
    memoria.crear_recordatorio("Sacar la basura reciclable", "en 3 horas")
    page.goto(url)
    say(page, "/cancelar basura")
    page.wait_for_selector(".choices button >> nth=1")
    assert page.locator(".choices button").count() == 2
    page.reload()
    page.click(".choices button:has-text('reciclable')")
    page.wait_for_selector("#confirm[open]")
    assert "«Sacar la basura reciclable»" in page.inner_text("#confirm-text")
    page.click("#confirm-yes")
    page.wait_for_selector(".command:has-text('cancelado')")
    assert not memoria.is_pending(2)
    assert page.outside == []


def test_bold_marks_render_as_bold_without_html(site, page, tmp_path):
    url, _ = site
    EventStore(tmp_path / "data" / "events.db").log("assistant_msg", "Va a **Iquique** <b>no</b>")
    page.goto(url)
    assert page.inner_text(".assistant strong") == "Iquique"
    assert page.locator(".assistant b").count() == 0
    assert "<b>no</b>" in page.inner_text(".assistant")


@pytest.fixture
def phone():
    with sync_api.sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(**playwright.devices["Pixel 7"])
        yield context.new_page()
        browser.close()


def test_pages_fit_a_phone_screen_and_the_app_installs(site, phone):
    url, _ = site
    memoria.create_entities(
        [{"name": "Ana", "entityType": "persona", "observations": ["Vive en Talca " * 12]}]
    )
    for path in ("/", "/memoria", "/memoria/ficha/Ana", "/objetivos", "/avisos", "/estado"):
        phone.goto(url + path)
        wide = phone.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert wide <= 0, f"{path} se sale {wide} px de la pantalla"
    phone.goto(url)
    say(phone, "hola")
    phone.wait_for_selector(".assistant:has-text('Hola, Renzo.')")
    scope = phone.evaluate("navigator.serviceWorker.ready.then(r => r.scope)")
    assert scope == url + "/"


def test_a_notice_missed_while_asleep_shows_when_the_page_comes_back(site, page):
    url, _ = site
    memoria.list_notices()  # crea la base antes de que la página y el test la usen a la vez
    page.goto(url)
    page.wait_for_timeout(300)
    assert page.locator("#banner").is_hidden()
    # Llega mientras la página estaba suspendida: se guarda, pero no le llega por SSE.
    memoria.save_notice(memoria.local_now(), "recordatorio", "r1", "tomar agua", "pedido por Renzo")
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector("#banner:has-text('Recordatorio: tomar agua')")
    assert page.inner_text("#avisos-n") == "(1)"
    assert page.outside == []


@pytest.fixture
def remote_site(tmp_path, monkeypatch):
    """Tama vista "desde fuera del PC": cada pedido trae la cabecera de tailscale serve."""
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    settings = Settings(
        data_dir=tmp_path / "data", memory_dir=tmp_path / "memoria", web_tailscale_host="localhost"
    )
    provider = Provider([Text("Hola desde el celular."), Done(1, 1)])
    cascade = Cascade([Layer(provider)], FakeConnectivity())
    brain = Brain(cascade, EventStore(tmp_path / "data" / "events.db"), None, tools=Tools())
    app = create_app(brain, settings)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://localhost:{port}", app.state.access
    server.should_exit = True


def test_the_phone_registers_a_passkey_and_signs_in(remote_site):
    url, access = remote_site
    with sync_api.sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(
            extra_http_headers={"Tailscale-User-Login": "renzo@example.com"}
        )
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("WebAuthn.enable")
        cdp.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                }
            },
        )
        page.goto(url + "/")
        assert page.url.endswith("/entrar")
        page.click("text=Registrar esta passkey")
        page.fill("#codigo", access.new_code())
        page.fill("#nombre", "Celular de prueba")
        page.click("#alta button")
        page.wait_for_url("**/entrar")
        assert [k.name for k in access.passkeys()] == ["Celular de prueba"]
        page.click("#entrar")
        page.wait_for_url(url + "/")
        page.fill("#chat-text", "hola")
        page.click("#chat-form button")
        page.wait_for_selector(".assistant:has-text('Hola desde el celular.')")
        browser.close()
