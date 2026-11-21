"""Sprint 12: fotos y documentos adjuntos, con visión y resúmenes simulados."""

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from tama import attachments as att
from tama.attachments import (
    AttachmentError,
    Attachments,
    AttachmentStore,
    AttachmentTools,
    Senses,
)
from tama.brain import Brain
from tama.cascade import Cascade, Layer
from tama.config import Settings
from tama.events import EventStore
from tama.web.app import create_app
from tests.fakes import FakeConnectivity
from tests.pdf_helper import make_pdf
from tests.softauth import SoftAuthenticator
from tests.test_auth import ORIGIN, TAILSCALE, TS_HOST, web_enroll, web_sign_in
from tests.test_web import Provider, Tools


def photo(width=3000, height=2000, fmt="JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (120, 80, 40)).save(out, fmt)
    return out.getvalue()


def settings_for(tmp_path, **extra):
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memoria",
        anthropic_api_key=SecretStr("sk-ant-prueba"),
        **extra,
    )


class FakeClaude:
    def __init__(self, text="Un taller con un torno y herramientas colgadas.", fail=False):
        self.sent = []
        self.text, self.fail = text, fail
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        if self.fail:
            raise RuntimeError("caída")
        usage = SimpleNamespace(input_tokens=1000, output_tokens=50)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], usage=usage)


class FakeLocal:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def chat(self, model, messages, **kwargs):
        self.calls.append((model, messages, kwargs))
        if self.fail:
            raise RuntimeError("sin modelo")
        return {"message": {"content": f"local:{model}"}}


class FakeSpend:
    def __init__(self, closed=""):
        self.closed, self.added = closed, []

    def gate(self):
        return self.closed

    def add(self, usage):
        self.added.append(usage)


# --- guardar ---------------------------------------------------------------------------------


def test_files_are_kept_with_their_own_name_and_never_overwritten(tmp_path):
    store = AttachmentStore(tmp_path)
    first = store.save("taller.jpg", "image/jpeg", photo())
    second = store.save("taller.jpg", "image/jpeg", photo(10, 10))
    assert first["id"] != second["id"] and first["path"] != second["path"]
    assert first["kind"] == "foto" and store.read("a1")[1] == photo()
    doc = store.save("manual.pdf", "application/pdf", make_pdf([["uno"], ["dos"]]))
    assert doc["kind"] == "pdf" and doc["pages"] == 2
    note = store.save("lista.md", "text/markdown", b"# Compras\n- pan")
    assert note["kind"] == "texto"
    assert [i["id"] for i in store.recent()] == [4, 3, 2, 1]


def test_wrong_type_size_or_broken_files_are_refused(tmp_path, monkeypatch):
    store = AttachmentStore(tmp_path)
    with pytest.raises(AttachmentError, match="Solo puedo"):
        store.save("virus.exe", "application/octet-stream", b"MZ")
    with pytest.raises(AttachmentError, match="dañada"):
        store.save("rota.jpg", "image/jpeg", b"no es una foto")
    with pytest.raises(AttachmentError, match="dañado"):
        store.save("roto.pdf", "application/pdf", b"%PDF-roto")
    monkeypatch.setitem(att.LIMITS_MB, "foto", 0)
    with pytest.raises(AttachmentError, match="pesa más"):
        store.save("grande.jpg", "image/jpeg", photo())
    monkeypatch.setattr(att, "MAX_PAGES", 1)
    with pytest.raises(AttachmentError, match="páginas"):
        store.save("largo.pdf", "application/pdf", make_pdf([["a"], ["b"]]))
    assert store.recent() == []


def test_photos_are_turned_upright_and_shrunk_for_vision():
    out = io.BytesIO()
    img = Image.new("RGB", (4000, 1000))
    exif = img.getexif()
    exif[0x0112] = 6  # la cámara la guardó girada
    img.save(out, "JPEG", exif=exif)
    small = Image.open(io.BytesIO(att.for_vision(out.getvalue())))
    assert small.size == (392, 1568)  # derecha y de 1568 px como máximo


# --- visión y resúmenes ---------------------------------------------------------------------


def test_claude_looks_first_and_its_cost_counts_for_the_cap(tmp_path):
    claude, local, spend = FakeClaude(), FakeLocal(), FakeSpend()
    senses = Senses(settings_for(tmp_path), spend, lambda: True, claude, local)
    seen = senses.look(att.for_vision(photo()), "")
    assert seen.provider == "Claude" and "torno" in seen.text
    image = claude.sent[0]["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert len(spend.added) == 1 and local.calls == []


@pytest.mark.parametrize(
    ("online", "claude_fails", "closed", "cloud", "why"),
    [
        (False, False, "", True, "sin internet"),
        (True, True, "", True, "no respondió"),
        (True, False, "tope alcanzado", True, "tope alcanzado"),
        (True, False, "", False, "solo en el PC"),
    ],
)
def test_without_internet_or_claude_the_pc_looks(
    tmp_path, online, claude_fails, closed, cloud, why
):
    claude, local = FakeClaude(fail=claude_fails), FakeLocal()
    senses = Senses(
        settings_for(tmp_path, vision_cloud=cloud), FakeSpend(closed), lambda: online, claude, local
    )
    seen = senses.look(att.for_vision(photo()), "¿Qué herramienta hay?")
    assert seen.text == "local:gemma4:e4b" and why in seen.provider
    _model, messages, kwargs = local.calls[0]
    assert messages[-1]["images"] and kwargs["options"]["num_gpu"] == 0


def test_if_nothing_can_look_the_reason_is_clear(tmp_path):
    senses = Senses(
        settings_for(tmp_path), FakeSpend(), lambda: False, FakeClaude(), FakeLocal(True)
    )
    with pytest.raises(AttachmentError, match=r"sin internet.*gemma4:e4b"):
        senses.look(att.for_vision(photo()), "")


def test_long_documents_are_summarized_in_parts_on_the_pc(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "SUMMARY_CHUNK", 100)
    local = FakeLocal()
    senses = Senses(settings_for(tmp_path), FakeSpend(), lambda: False, FakeClaude(), local)
    seen = senses.summarize("x" * 250, "plazos", "manual.pdf")
    assert len(local.calls) == 4  # tres partes y la unión
    assert "plazos" in local.calls[-1][1][-1]["content"] and "en el PC" in seen.provider


# --- herramientas -----------------------------------------------------------------------------


def tools_with(tmp_path, claude=None, local=None, online=True):
    store = AttachmentStore(tmp_path)
    senses = Senses(
        settings_for(tmp_path),
        FakeSpend(),
        lambda: online,
        claude or FakeClaude(),
        local or FakeLocal(),
    )
    return store, Attachments(store, senses)


def test_the_tools_appear_only_inside_the_wrapper_and_answer(tmp_path):
    store, attachments = tools_with(tmp_path)
    store.save("taller.jpg", "image/jpeg", photo())
    pages = [[f"Página {n}: mantención del torno, paso {n}."] for n in range(1, 21)]
    store.save("manual.pdf", "application/pdf", make_pdf(pages))
    wrapped = attachments.wrap(Tools())
    names = [t.name for t in wrapped.available_tools(10)[0]]
    assert {"ver_imagen", "leer_documento", "resumir_documento", "listar_adjuntos"} <= set(names)
    assert "delete_note" in names and isinstance(wrapped, AttachmentTools)
    seen = wrapped.call("ver_imagen", {"adjunto": "a1"})
    assert seen.ok and "torno" in seen.text and "Claude" in seen.text
    read = wrapped.call("leer_documento", {"adjunto": "a2", "desde": 3, "hasta": 4})
    assert "[pág. 3]" in read.text and "[pág. 5]" not in read.text
    summary = wrapped.call("resumir_documento", {"adjunto": "a2"})
    assert summary.ok and "Resumen de «manual.pdf»" in summary.text
    assert wrapped.call("ver_imagen", {"adjunto": "a2"}).ok is False  # un PDF no es foto
    assert "a1" in wrapped.call("listar_adjuntos", {}).text


def test_reading_stops_at_the_limit_and_says_where_it_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "READ_CHARS", 200)
    store, attachments = tools_with(tmp_path)
    store.save("largo.pdf", "application/pdf", make_pdf([["texto " * 20]] * 6))
    text = attachments.leer_documento("a1")
    assert "Sigue en la página" in text and "(de 6)" in text


def test_a_scanned_page_is_read_with_vision(tmp_path):
    store, attachments = tools_with(tmp_path, claude=FakeClaude("Factura 123, total 5.000"))
    scanned = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(scanned, "PDF")
    store.save("escaneo.pdf", "application/pdf", scanned.getvalue())
    assert "Factura 123" in attachments.leer_documento("a1")


# --- web --------------------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    settings = Settings(
        data_dir=tmp_path / "data", memory_dir=tmp_path / "memoria", web_tailscale_host=TS_HOST
    )
    provider = Provider()
    brain = Brain(
        Cascade([Layer(provider)], FakeConnectivity()),
        EventStore(tmp_path / "data" / "events.db"),
        None,
        tools=None,
    )
    app = create_app(brain, settings)
    return app, provider


def token(client) -> str:
    return client.get("/estado").text.split('data-csrf="')[1].split('"')[0]


def test_upload_and_send_a_photo_from_the_pc(web):
    app, _ = web
    pc = TestClient(app, base_url="http://127.0.0.1")
    headers = {"x-csrf-token": token(pc)}
    sent = pc.post(
        "/adjuntos", files={"archivo": ("taller.jpg", photo(), "image/jpeg")}, headers=headers
    )
    assert sent.status_code == 200
    body = sent.json()
    assert body["id"] == "a1" and body["kind"] == "foto" and "foto" in body["notice"]
    shown = pc.get("/adjuntos/a1")
    assert shown.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(shown.content)).size) == 640
    turn = pc.post("/chat", data={"text": "¿Qué ves?", "adjuntos": "a1"}, headers=headers)
    assert turn.status_code == 200
    session = app.state.session
    assert session.attachments is app.state.attachments  # desde aquí, con sus herramientas
    assert any("«taller.jpg» (foto, id a1)" in str(m.get("content")) for m in session.messages)
    refused = pc.post(
        "/adjuntos",
        files={"archivo": ("x.exe", b"MZ", "application/x-msdownload")},
        headers=headers,
    )
    assert refused.status_code == 422 and "Solo puedo" in refused.json()["detail"]


def test_from_outside_uploading_needs_the_passkey_and_then_works(web):
    app, _ = web
    phone = TestClient(app, base_url=ORIGIN, headers=TAILSCALE)
    files = {"archivo": ("foto.jpg", photo(), "image/jpeg")}
    assert phone.post("/adjuntos", files=files).status_code == 401
    device = SoftAuthenticator()
    web_enroll(phone, device, app.state.access.new_code())
    web_sign_in(phone, device)
    sent = phone.post("/adjuntos", files=files, headers={"x-csrf-token": token(phone)})
    assert sent.status_code == 200 and sent.json()["id"] == "a1"


# --- varios adjuntos en un mensaje -----------------------------------------------------------


def test_several_photos_are_looked_at_together_in_one_call(tmp_path):
    claude = FakeClaude("La primera tiene un torno; la segunda, un taladro.")
    store, attachments = tools_with(tmp_path, claude=claude)
    store.save("antes.jpg", "image/jpeg", photo())
    store.save("despues.png", "image/png", photo(800, 600, "PNG"))
    seen = attachments.wrap(Tools()).call(
        "ver_imagen", {"adjunto": "a1, a2", "pregunta": "¿qué diferencia hay?"}
    )
    assert seen.ok and "a1" in seen.text and "a2" in seen.text
    assert len(claude.sent) == 1  # una sola llamada de visión
    content = claude.sent[0]["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "image", "text"]
    assert "antes.jpg" in content[-1]["text"] and "diferencia" in content[-1]["text"]
    for n in range(4):
        store.save(f"f{n}.jpg", "image/jpeg", photo(50, 50))
    too_many = attachments.wrap(Tools()).call("ver_imagen", {"adjunto": "a1 a2 a3 a4 a5 a6"})
    assert not too_many.ok and "hasta 5" in too_many.text


def test_several_documents_get_one_summary_each(tmp_path):
    claude = FakeClaude("Puntos del documento.")
    store, attachments = tools_with(tmp_path, claude=claude)
    store.save("uno.pdf", "application/pdf", make_pdf([["Manual uno: cambio de aceite cada año"]]))
    store.save("dos.md", "text/markdown", b"# Lista dos")
    text = attachments.resumir_documento(["a1", "a2"])
    assert text.count("Resumen de") == 2 and "uno.pdf" in text and "dos.md" in text
    assert len(claude.sent) == 2  # uno por documento; el modelo los combina


def test_a_message_takes_up_to_the_limit_of_attachments(web):
    app, _ = web
    pc = TestClient(app, base_url="http://127.0.0.1")
    headers = {"x-csrf-token": token(pc)}
    for n in range(11):
        pc.post(
            "/adjuntos", files={"archivo": (f"f{n}.txt", b"hola", "text/plain")}, headers=headers
        )
    refs = ",".join(f"a{n}" for n in range(1, 12))
    refused = pc.post("/chat", data={"text": "léelos", "adjuntos": refs}, headers=headers)
    assert refused.status_code == 422 and "máximo por mensaje es 10" in refused.json()["detail"]
    ok = pc.post("/chat", data={"text": "léelos", "adjuntos": "a1,a2,a3"}, headers=headers)
    assert ok.status_code == 200
    note = next(m["content"] for m in app.state.session.messages if "id a1)" in str(m))
    assert note.count("«f") == 3
    assert 'max-adjuntos="10"' in pc.get("/").text.replace("data-", "")


def test_the_note_names_files_by_name_and_tells_apart_repeated_names(tmp_path):
    store, attachments = tools_with(tmp_path)
    store.save("foto.jpg", "image/jpeg", photo(50, 50))
    store.save("foto.jpg", "image/jpeg", photo(60, 60))
    store.save("pagomayo.pdf", "application/pdf", make_pdf([["Comprobante de pago de mayo"]]))
    note = attachments.note(["a1", "a2", "a3"])
    assert "nunca por el id" in note and "una sola llamada a ver_imagen" in note
    assert "«foto.jpg» (foto, 1 de 3, id a1)" in note
    assert "«foto.jpg» (foto, 2 de 3, id a2)" in note
    assert "«pagomayo.pdf» (pdf, 1 página, id a3)" in note  # sin orden: su nombre es único


def test_after_reloading_the_chat_shows_the_files_by_name(web):
    app, _ = web
    pc = TestClient(app, base_url="http://127.0.0.1")
    headers = {"x-csrf-token": token(pc)}
    pc.post("/adjuntos", files={"archivo": ("taller.jpg", photo(), "image/jpeg")}, headers=headers)
    pc.post("/chat", data={"text": "¿Qué ves?", "adjuntos": "a1"}, headers=headers)
    page = pc.get("/").text
    assert 'class="user-adjuntos"' in page and 'src="/adjuntos/a1"' in page
    assert "taller.jpg" in page and "nunca por el id" not in page  # la nota interna no se ve


def test_tools_find_attachments_by_file_name_too(tmp_path):
    store, attachments = tools_with(tmp_path)
    store.save("WhatsApp Image 2026-11-18 at 15.39.35.jpeg", "image/jpeg", photo(50, 50))
    store.save("manual-taller.pdf", "application/pdf", make_pdf([["Aceite cada 10.000 km"]]))
    # Un nombre con números no se confunde con un id, ni se parte por sus espacios.
    item = store.get("WhatsApp Image 2026-11-18 at 15.39.35.jpeg")
    assert item["id"] == 1
    assert store.get("«manual-taller.pdf»")["id"] == 2
    assert store.get("«manual-taller.pdf» (pdf, 1 página, id a2)")["id"] == 2
    assert att.refs_of("a1 a2") == ["a1", "a2"]
    assert att.refs_of("WhatsApp Image 2026-11-18 at 15.39.35.jpeg, a2") == [
        "WhatsApp Image 2026-11-18 at 15.39.35.jpeg",
        "a2",
    ]
    text = attachments.resumir_documento("manual-taller.pdf")
    assert "Resumen de «manual-taller.pdf»" in text
    with pytest.raises(AttachmentError, match="No encuentro"):
        store.get("no-existe.pdf")
