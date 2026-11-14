"""Extremo a extremo en un navegador real, con todo lo que no sea localhost bloqueado."""

import contextlib
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


def wav_file(path, pattern, rate=16000):
    """WAV para el micrófono falso: [(segundos, amplitud)], con un tono de 300 Hz donde suena."""
    import math
    import struct
    import wave

    frames = bytearray()
    for seconds, amplitude in pattern:
        for i in range(int(seconds * rate)):
            value = amplitude * math.sin(2 * math.pi * 300 * i / rate)
            frames += struct.pack("<h", int(value * 32767))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return path


@contextlib.contextmanager
def talk_site(tmp_path, monkeypatch, mic_pattern, answer_seconds=0.2):
    """Tama en un puerto libre con voz simulada y Chrome con un micrófono falso que reproduce
    `mic_pattern` en bucle. El micrófono tarda 400 ms en abrir, como en el PC de Renzo."""
    from tama.voice import Speech
    from tama.web import app as web

    heard: list[int] = []
    answer = wav_file(tmp_path / "respuesta.wav", [(answer_seconds, 0.0)]).read_bytes()

    class Voice:
        def __init__(self):
            self.said: list[str] = []
            self.listener = Listener()

        def speak(self, text):
            self.said.append(text)
            return Speech(answer, "audio/wav", "piper")

        def warm(self):
            pass

        def status(self):
            return {
                "active": "Piper (local)",
                "reason": "",
                "used": 0,
                "cap": 1,
                "piper": True,
                "listen": True,
            }

    class Listener:
        available = True

        def transcribe(self, audio):
            heard.append(len(audio))
            return "hola por voz"

    voice = Voice()
    monkeypatch.setattr(web.voice_module, "build", lambda *a, **k: voice)
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    memoria.list_notices()  # crea la base antes que la página y el servidor la usen a la vez
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memoria")
    brain = Brain(
        Cascade([Layer(Provider(*[[Text("Te escuché."), Done(1, 1)]] * 8))], FakeConnectivity()),
        EventStore(tmp_path / "data" / "events.db"),
        None,
        tools=Tools(),
    )
    app = create_app(brain, settings)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.01)
    mic = wav_file(tmp_path / "mic.wav", mic_pattern)
    args = [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={mic}",
        "--autoplay-policy=no-user-gesture-required",
    ]
    try:
        with sync_api.sync_playwright() as playwright:
            browser = None
            for options in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
                try:
                    browser = playwright.chromium.launch(args=args, **options)
                    break
                except sync_api.Error:
                    continue
            if browser is None:
                pytest.skip("no hay un navegador disponible")
            page = browser.new_page()
            page.add_init_script(
                """
                const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                navigator.mediaDevices.getUserMedia = (c) =>
                  new Promise((ok) => setTimeout(ok, 400)).then(() => real(c));
                """
            )
            page.goto(f"http://127.0.0.1:{port}/")
            page.check("#voz-leer")
            yield page, heard, voice
            browser.close()
    finally:
        server.should_exit = True


SPEECH_THEN_PAUSE = [(1.2, 0.3), (3.0, 0.0)]  # habla 1,2 s y calla 3 s, en bucle


def one_tap_turn(page, voice, turn):
    page.click("#hablar")  # un solo toque, sin mantener
    page.wait_for_function(
        f"document.querySelectorAll('.assistant').length >= {turn}", timeout=20000
    )
    for _ in range(50):  # la respuesta se lee en voz alta
        if len(voice.said) >= turn:
            break
        page.wait_for_timeout(100)
    page.wait_for_selector("#hablar[data-estado='idle']", timeout=10000)


def test_one_tap_listens_and_sends_by_itself_after_a_pause(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, SPEECH_THEN_PAUSE) as (page, heard, voice):
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='escuchando']")
        assert page.text_content("#hablar") == "Escuchando…"
        page.wait_for_selector(".user:has-text('hola por voz')", timeout=20000)
        page.wait_for_selector(".assistant:has-text('Te escuché.')")
        page.wait_for_selector("#hablar[data-estado='idle']", timeout=10000)
        assert not page.is_disabled("#hablar")
    assert len(heard) == 1 and heard[0] > 16000  # más de medio segundo de WAV de 16 kHz
    assert voice.said == ["Te escuché."]


def test_five_turns_in_a_row_with_one_tap_each(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, SPEECH_THEN_PAUSE) as (page, heard, voice):
        for turn in range(1, 6):
            one_tap_turn(page, voice, turn)
    assert len(heard) == 5 and all(n > 16000 for n in heard)
    assert len(voice.said) == 5


def test_a_tap_without_voice_gives_up_and_sends_nothing(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, [(2.0, 0.0)]) as (page, heard, _voice):
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='escuchando']")
        page.wait_for_selector("#hablar[data-estado='idle']", timeout=15000)
        assert "No te escuché" in page.text_content("#voz-estado")
    assert heard == []


def test_a_tap_while_tama_speaks_silences_her_and_listens(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, SPEECH_THEN_PAUSE, answer_seconds=8) as (
        page,
        heard,
        _voice,
    ):
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='hablando']", timeout=20000)
        page.click("#hablar")  # Tama sigue hablando (8 s): el toque la calla y escucha
        page.wait_for_selector("#hablar[data-estado='escuchando']")
        page.wait_for_function("document.querySelectorAll('.assistant').length >= 2", timeout=20000)
    assert len(heard) == 2
