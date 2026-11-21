"""Extremo a extremo en un navegador real, con todo lo que no sea localhost bloqueado."""

import contextlib
import os
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
def talk_site(
    tmp_path,
    monkeypatch,
    mic_pattern,
    answer_seconds=0.2,
    scripts=None,
    wait_ms=8000,
    read_aloud=True,
):
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
        Cascade(
            [Layer(Provider(*(scripts or [[Text("Te escuché."), Done(1, 1)]] * 8)))],
            FakeConnectivity(),
        ),
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
            page.add_init_script(f"window.TAMA_VOZ = {{ waitMs: {wait_ms} }};")
            page.add_init_script(
                """
                const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                navigator.mediaDevices.getUserMedia = (c) =>
                  new Promise((ok) => setTimeout(ok, 400)).then(() => real(c));
                """
            )
            page.goto(f"http://127.0.0.1:{port}/")
            if read_aloud:
                page.check("#voz-leer")
            yield page, heard, voice
            browser.close()
    finally:
        server.should_exit = True


SPEECH_THEN_PAUSE = [(1.2, 0.3), (3.0, 0.0)]  # habla 1,2 s y calla 3 s, en bucle

# Guarda cada cambio de estado del botón con su hora, para revisar el orden del ciclo.
TIMELINE = """
window.estados = [];
const talkButton = document.getElementById("hablar");
new MutationObserver(() => {
  const now = talkButton.dataset.estado;
  const last = window.estados[window.estados.length - 1];
  if (!last || last[0] !== now) window.estados.push([now, performance.now()]);
}).observe(talkButton, { attributes: true, attributeFilter: ["data-estado"] });
"""


def wait_turns(page, voice, turns, timeout=40000):
    page.wait_for_function(
        f"document.querySelectorAll('.assistant').length >= {turns}", timeout=timeout
    )
    for _ in range(100):
        if len(voice.said) >= turns:
            break
        page.wait_for_timeout(100)


def test_one_tap_holds_a_three_turn_conversation_without_touching_again(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, SPEECH_THEN_PAUSE) as (page, heard, voice):
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='escuchando']")
        assert page.text_content("#hablar") == "Conversando… (toca para terminar)"
        assert page.evaluate("document.body.dataset.conversando") == "1"
        wait_turns(page, voice, 3)
        page.wait_for_selector("#hablar[data-estado='escuchando']")  # sigue escuchando sola
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='idle']")
    assert len(heard) >= 3 and all(n > 16000 for n in heard[:3])
    assert voice.said[:3] == ["Te escuché."] * 3


def test_it_never_listens_while_tama_speaks(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, SPEECH_THEN_PAUSE, answer_seconds=2) as (
        page,
        _heard,
        voice,
    ):
        page.evaluate(TIMELINE)
        page.click("#hablar")
        wait_turns(page, voice, 2)
        page.wait_for_selector("#hablar[data-estado='escuchando']")
        timeline = page.evaluate("window.estados")
        page.click("#hablar")
    names = [name for name, _ in timeline]
    assert "hablando" in names
    for i, (name, at) in enumerate(timeline):
        if name == "hablando":
            following = timeline[i + 1 :]
            assert following and following[0][0] == "idle"  # termina de hablar y recién ahí...
            listening = next(t for n, t in following if n == "escuchando")
            assert listening - at >= 1900  # ...vuelve a escuchar (la voz dura 2 s)


def test_when_renzo_says_goodbye_the_model_ends_the_conversation(tmp_path, monkeypatch):
    scripts = [
        [Text("Te escuché."), Done(1, 1)],
        [ToolCall("terminar_conversacion", {}, "c1"), Done(1, 1)],
        [Text("Chao, Renzo."), Done(1, 1)],
    ]
    with talk_site(tmp_path, monkeypatch, SPEECH_THEN_PAUSE, scripts=scripts) as (
        page,
        heard,
        voice,
    ):
        page.click("#hablar")
        wait_turns(page, voice, 2)
        page.wait_for_function("document.body.dataset.conversando === ''", timeout=10000)
        page.wait_for_selector("#hablar[data-estado='idle']")
        assert "Conversación terminada" in page.text_content("#voz-estado")
        page.wait_for_timeout(2500)  # ya no vuelve a escuchar
        assert page.get_attribute("#hablar", "data-estado") == "idle"
    assert voice.said == ["Te escuché.", "Chao, Renzo."]
    assert len(heard) == 2


def test_two_silent_listens_end_the_conversation(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, [(2.0, 0.0)], wait_ms=1500) as (page, heard, _voice):
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='escuchando']")
        page.wait_for_function("document.body.dataset.conversando === ''", timeout=15000)
        assert "no te escuché" in page.text_content("#voz-estado")
    assert heard == []


def test_a_tap_ends_the_conversation_at_once(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, [(2.0, 0.0)]) as (page, heard, _voice):
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='escuchando']")
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='idle']")
        assert page.text_content("#hablar") == "Hablar"
        assert "Terminaste" in page.text_content("#voz-estado")
        page.wait_for_timeout(2000)
        assert page.get_attribute("#hablar", "data-estado") == "idle"
    assert heard == []


def test_with_the_microphone_open_the_half_second_before_the_tap_is_sent(tmp_path, monkeypatch):
    """Pre-roll: si Renzo empieza a hablar justo al tocar, la primera sílaba no se corta."""
    with talk_site(tmp_path, monkeypatch, SPEECH_THEN_PAUSE) as (page, _heard, voice):
        page.click("#hablar")  # abre el micrófono, que queda abierto
        wait_turns(page, voice, 1)
        page.click("#hablar")  # termina la conversación
        page.wait_for_selector("#hablar[data-estado='idle']")
        page.wait_for_timeout(1000)
        sent = page.evaluate(
            """() => new Promise((done) => {
                const real = window.fetch;
                window.fetch = (url, options) => {
                  if (url === "/voz/escuchar") done([performance.now(), options.body.size]);
                  return real(url, options);
                };
                window.tapAt = performance.now();
                document.getElementById("hablar").click();
            })"""
        )
        tapped = page.evaluate("window.tapAt")
        page.click("#hablar")
        listened_s = (sent[0] - tapped) / 1000
        audio_s = (sent[1] - 44) / 32000
        assert audio_s > listened_s + 0.3  # el audio empieza antes del toque


def test_raphael_takes_the_chat_place_while_conversing_and_follows_the_states(
    tmp_path, monkeypatch
):
    with talk_site(tmp_path, monkeypatch, SPEECH_THEN_PAUSE, answer_seconds=2) as (
        page,
        _heard,
        voice,
    ):
        page.evaluate(
            """window.raphaelEstados = [];
            new MutationObserver(() => {
              const e = document.getElementById("raphael").dataset.estado;
              const l = window.raphaelEstados;
              if (l[l.length - 1] !== e) l.push(e);
            }).observe(document.getElementById("raphael"), { attributes: true });"""
        )
        assert not page.is_visible("#raphael") and page.is_visible("#log")
        page.click("#hablar")
        page.wait_for_selector("#raphael", state="visible")
        assert not page.is_visible("#log")  # el chat queda detrás; los mensajes se siguen guardando
        wait_turns(page, voice, 1)
        page.wait_for_selector("#raphael[data-estado='hablando']", timeout=10000)
        assert page.text_content(".raphael-renzo") == "Tú: hola por voz"
        assert page.text_content(".raphael-tama") == "Te escuché."
        page.wait_for_selector("#raphael[data-estado='escuchando']", timeout=10000)
        seen = page.evaluate("window.raphaelEstados")
        assert {"escuchando", "pensando", "hablando"} <= set(seen)
        page.click("#raphael")  # tocar a Raphael termina la conversación
        page.wait_for_function("document.body.dataset.conversando === ''")
        page.wait_for_selector("#raphael", state="hidden", timeout=5000)  # tras despedirse
        assert page.is_visible("#log")
        assert page.locator("#log .user").count() >= 1  # la conversación quedó en el chat


def test_without_read_aloud_the_chat_stays(tmp_path, monkeypatch):
    with talk_site(tmp_path, monkeypatch, [(2.0, 0.0)], read_aloud=False) as (page, _h, _v):
        page.click("#hablar")
        page.wait_for_selector("#hablar[data-estado='escuchando']")
        assert not page.is_visible("#raphael") and page.is_visible("#log")
        page.check("#voz-leer")  # al activar la lectura en plena conversación, aparece
        page.wait_for_selector("#raphael", state="visible")
        page.click("#hablar")
        page.wait_for_selector("#raphael", state="hidden", timeout=5000)


def test_on_a_phone_the_chat_box_uses_the_whole_width_below_the_buttons(site, phone):
    url, _ = site
    phone.goto(url)
    phone.wait_for_selector("#hablar:not([hidden])")
    form = phone.locator("#chat-form").bounding_box()
    box = phone.locator("#chat-text").bounding_box()
    send = phone.locator("#chat-form button[type=submit]").bounding_box()
    talk = phone.locator("#hablar").bounding_box()
    assert box["width"] >= 0.95 * form["width"]  # el cuadro usa todo el ancho
    assert send["y"] + send["height"] <= box["y"] and talk["y"] + talk["height"] <= box["y"]
    assert abs(send["y"] - talk["y"]) < 2  # Enviar y Hablar en la misma fila, arriba
    assert phone.is_visible("#voz-leer")
    if os.environ.get("E2E_CAPTURAS"):
        phone.screenshot(path=os.path.join(os.environ["E2E_CAPTURAS"], "celular.png"))
        desk = phone.context.browser.new_page(viewport={"width": 1280, "height": 800})
        desk.goto(url)
        desk.screenshot(path=os.path.join(os.environ["E2E_CAPTURAS"], "pc.png"))


def test_attach_a_photo_and_send_it_with_the_message(site, page, tmp_path):
    import io

    from PIL import Image

    url, _ = site
    memoria.list_notices()  # crea la base antes que la página y el servidor la usen a la vez
    image = tmp_path / "taller.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (90, 60, 30)).save(buffer, "JPEG")
    image.write_bytes(buffer.getvalue())
    page.goto(url)
    page.set_input_files("#archivo", str(image))
    page.wait_for_selector("#adjuntos-pendientes .adjunto img")
    assert "taller.jpg" in page.text_content("#adjuntos-pendientes")
    assert page.locator(".line:has-text('La foto se mira')").count() == 1  # aviso de privacidad
    with page.expect_request("**/chat") as sent:
        page.fill("#chat-text", "¿Qué ves?")
        page.click("#chat-form button[type=submit]")
    assert "adjuntos=a1" in sent.value.post_data
    page.wait_for_selector(".user-adjuntos .adjunto:has-text('taller.jpg') img")
    assert page.is_hidden("#adjuntos-pendientes")


def test_several_files_at_once_remove_one_and_send(site, page, tmp_path):
    import io

    from PIL import Image

    from tests.pdf_helper import make_pdf

    url, _ = site
    memoria.list_notices()  # crea la base antes que la página y el servidor la usen a la vez
    files = []
    buffer = io.BytesIO()
    Image.new("RGB", (300, 200), (90, 60, 30)).save(buffer, "JPEG")
    for name, data in (
        ("foto.jpg", buffer.getvalue()),
        ("manual.pdf", make_pdf([["Manual"]])),
        ("lista.txt", b"pan y leche"),
        ("virus.exe", b"MZ"),
    ):
        (tmp_path / name).write_bytes(data)
        files.append(str(tmp_path / name))
    page.goto(url)
    page.set_input_files("#archivo", files)
    page.wait_for_function(
        "document.querySelectorAll('#adjuntos-pendientes .adjunto.listo').length === 3"
        " && document.querySelectorAll('#adjuntos-pendientes .adjunto.error').length === 1"
    )
    error = page.locator("#adjuntos-pendientes .adjunto.error")
    assert error.count() == 1 and "Solo puedo" in error.text_content()  # los demás siguen
    page.click("button[aria-label='Quitar lista.txt']")
    assert page.locator("#adjuntos-pendientes .adjunto.listo").count() == 2
    with page.expect_request("**/chat") as sent:
        page.fill("#chat-text", "Revisa estos")
        page.click("#chat-form button[type=submit]")
    assert "adjuntos=a1%2Ca2" in sent.value.post_data
    page.wait_for_selector(".user-adjuntos:has-text('manual.pdf'):has-text('foto.jpg')")


def test_more_files_than_the_limit_are_refused_with_a_clear_message(site, page, tmp_path):
    url, _ = site
    memoria.list_notices()
    files = []
    for n in range(12):
        (tmp_path / f"nota{n}.txt").write_bytes(b"hola")
        files.append(str(tmp_path / f"nota{n}.txt"))
    page.goto(url)
    page.set_input_files("#archivo", files)
    page.wait_for_function(
        "document.querySelectorAll('#adjuntos-pendientes .adjunto.listo').length === 10"
    )
    assert "máximo 10 por mensaje" in page.text_content(".error")
