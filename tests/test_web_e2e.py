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
    assert page.inner_text("#confirm-text") == "¿Borro la nota 3?"
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
