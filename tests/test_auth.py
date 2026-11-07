"""Sprint 10: passkeys, sesiones y herramientas limitadas fuera del PC."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tama import agent
from tama.auth import Access, AuthError
from tama.brain import Brain
from tama.cascade import Cascade, Layer
from tama.config import Settings
from tama.events import EventStore
from tama.mcp_hub import ToolInfo, ToolOutcome
from tama.providers import Done, Text, ToolCall
from tama.remote import BLOCKED, RemoteTools
from tama.web.app import create_app
from tests.fakes import FakeConnectivity
from tests.softauth import SoftAuthenticator
from tests.test_web import Provider

TS_HOST = "mi-pc.tail1234.ts.net"
ORIGIN = f"https://{TS_HOST}"
TAILSCALE = {"Tailscale-User-Login": "renzo@example.com"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def enroll(access: Access, device: SoftAuthenticator, code: str | None) -> None:
    flow, options = access.registration_options(TS_HOST, code)
    credential = device.create(json.loads(options), ORIGIN)
    access.register(flow, credential, TS_HOST, ORIGIN, "Celular", code)


def sign_in(access: Access, device: SoftAuthenticator, days: float = 30) -> str:
    flow, options = access.login_options(TS_HOST)
    credential = device.get(json.loads(options), ORIGIN, TS_HOST)
    return access.login(flow, credential, TS_HOST, ORIGIN, days)


# --- Access ---------------------------------------------------------------------------------


def test_enroll_with_a_code_from_the_pc_and_sign_in(tmp_path):
    access = Access(tmp_path / "acceso.db")
    device = SoftAuthenticator()
    enroll(access, device, access.new_code())
    token = sign_in(access, device)
    assert access.valid_session(token)
    assert [k.name for k in access.passkeys()] == ["Celular"]
    assert access.passkeys()[0].last_used


def test_codes_are_single_use_and_expire(tmp_path):
    clock = Clock()
    access = Access(tmp_path / "acceso.db", clock=clock)
    code = access.new_code()
    enroll(access, SoftAuthenticator(), code)
    with pytest.raises(AuthError, match="no es válido"):
        access.registration_options(TS_HOST, code)
    late = access.new_code()
    clock.now += timedelta(minutes=11)
    with pytest.raises(AuthError, match="caducó"):
        access.registration_options(TS_HOST, late)
    with pytest.raises(AuthError, match="no es válido"):
        access.registration_options(TS_HOST, "")


def test_revoking_a_passkey_closes_its_sessions_and_keeps_the_record(tmp_path):
    access = Access(tmp_path / "acceso.db")
    device = SoftAuthenticator()
    enroll(access, device, access.new_code())
    token = sign_in(access, device)
    assert access.revoke(access.passkeys()[0].id)
    assert not access.valid_session(token)
    with pytest.raises(AuthError):
        sign_in(access, device)
    with access._db() as db:
        assert db.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0] == 1


def test_sessions_expire_and_can_be_closed(tmp_path):
    clock = Clock()
    access = Access(tmp_path / "acceso.db", clock=clock)
    device = SoftAuthenticator()
    enroll(access, device, access.new_code())
    token = sign_in(access, device, days=1)
    clock.now += timedelta(days=2)
    assert not access.valid_session(token)
    clock.now -= timedelta(days=2)
    other = sign_in(access, device)
    access.logout(other)
    assert not access.valid_session(other)


def test_a_signature_for_another_site_is_rejected(tmp_path):
    access = Access(tmp_path / "acceso.db")
    device = SoftAuthenticator()
    enroll(access, device, access.new_code())
    flow, options = access.login_options(TS_HOST)
    credential = device.get(json.loads(options), "https://otro-sitio.example", TS_HOST)
    with pytest.raises(AuthError, match="No pude verificar"):
        access.login(flow, credential, TS_HOST, ORIGIN, 30)


# --- web --------------------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    settings = Settings(
        data_dir=tmp_path / "data", memory_dir=tmp_path / "memoria", web_tailscale_host=TS_HOST
    )
    cascade = Cascade([Layer(Provider())], FakeConnectivity())
    brain = Brain(cascade, EventStore(tmp_path / "data" / "events.db"), None, tools=None)
    app = create_app(brain, settings)
    phone = TestClient(app, base_url=ORIGIN, headers=TAILSCALE)
    pc = TestClient(app, base_url="http://127.0.0.1")
    return phone, pc, app.state.access


def web_enroll(phone, device, code):
    started = phone.post("/auth/registrar/opciones", json={"code": code})
    if started.status_code != 200:
        return started
    data = started.json()
    credential = device.create(data["options"], ORIGIN)
    body = {"flow": data["flow"], "credential": credential, "code": code, "name": "Samsung"}
    return phone.post("/auth/registrar", json=body)


def web_sign_in(phone, device):
    data = phone.post("/auth/entrar/opciones").json()
    credential = device.get(data["options"], ORIGIN, TS_HOST)
    return phone.post("/auth/entrar", json={"flow": data["flow"], "credential": credential})


def test_without_a_passkey_nothing_with_data_opens_from_outside(site):
    phone, _, _ = site
    page = phone.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/entrar"
    for path in ("/memoria", "/avisos/pendientes", "/estado", "/memoria/ficha/Renzo"):
        assert phone.get(path).status_code == 401, path
    assert phone.post("/chat", data={"text": "hola"}).status_code == 401
    for path in ("/sw.js", "/manifest.webmanifest", "/static/style.css", "/entrar", "/registrar"):
        assert phone.get(path).status_code == 200, path


def test_the_phone_enrolls_with_a_code_signs_in_and_out(site):
    phone, pc, _ = site
    code = pc.post("/acceso/codigo", headers={"x-csrf-token": csrf(pc)}).text
    digits = "".join(ch for ch in code.split("</strong>")[0] if ch.isdigit())
    device = SoftAuthenticator()
    assert web_enroll(phone, device, digits).json() == {"ok": True, "name": "Samsung"}
    signed = web_sign_in(phone, device)
    cookie = signed.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert phone.get("/").status_code == 200
    estado = phone.get("/estado").text
    assert "Cerrar sesión" in estado and "Generar código" not in estado
    token = csrf(phone)
    assert phone.post("/auth/salir", headers={"x-csrf-token": token}).status_code == 204
    phone.cookies.clear()
    assert phone.get("/memoria").status_code == 401


def test_remote_enrollment_without_or_with_an_old_code_is_refused(site):
    phone, _, access = site
    assert web_enroll(phone, SoftAuthenticator(), "").status_code == 400
    assert "no es válido" in web_enroll(phone, SoftAuthenticator(), "12345678").json()["error"]
    access.clock = lambda: datetime.now(UTC) - timedelta(minutes=30)
    old = access.new_code()
    access.clock = lambda: datetime.now(UTC)
    assert "caducó" in web_enroll(phone, SoftAuthenticator(), old).json()["error"]


def test_codes_and_revoking_are_only_for_the_pc(site):
    phone, pc, access = site
    device = SoftAuthenticator()
    web_enroll(phone, device, access.new_code())
    web_sign_in(phone, device)
    token = csrf(phone)
    assert phone.post("/acceso/codigo", headers={"x-csrf-token": token}).status_code == 403
    assert phone.post("/acceso/revocar/1", headers={"x-csrf-token": token}).status_code == 403
    revoked = pc.post("/acceso/revocar/1", headers={"x-csrf-token": csrf(pc)})
    assert "Todavía no hay passkeys" in revoked.text
    assert phone.get("/memoria").status_code == 401  # su sesión se cerró


def test_the_pc_keeps_working_without_a_login(site):
    _, pc, _ = site
    assert pc.get("/").status_code == 200
    assert "Generar código para un celular" in pc.get("/estado").text


def test_forgetting_for_good_is_refused_from_outside(site):
    phone, _, access = site
    device = SoftAuthenticator()
    web_enroll(phone, device, access.new_code())
    web_sign_in(phone, device)
    token = csrf(phone)
    reply = phone.post("/chat", data={"text": "/olvidar Bruno"}, headers={"x-csrf-token": token})
    assert reply.json()["reply"] == "Olvidar para siempre solo se puede desde el PC."
    forget = phone.post("/memoria/ficha/1/olvidar/1", headers={"x-csrf-token": token})
    assert forget.status_code == 403


def csrf(client) -> str:
    page = client.get("/estado").text
    return page.split('data-csrf="')[1].split('"')[0]


# --- herramientas fuera del PC --------------------------------------------------------------


class Hub:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.infos = {
            name: ToolInfo("x", name, name, {}, destructive=False)
            for name in (
                "read_file",
                "find_files",
                "delete_note",
                "search_nodes",
                "cancel_reminder",
            )
        }

    def available_tools(self, max_tools=10):
        return list(self.infos.values()), []

    def find(self, name):
        return self.infos.get(name)

    def call(self, name, args):
        self.executed.append(name)
        return ToolOutcome(True, "hecho")


def test_remote_tools_hide_and_block_files_and_lossy_deletions():
    hub = Hub()
    tools = RemoteTools(hub)
    visible, down = tools.available_tools()
    assert {t.name for t in visible} == {"search_nodes", "cancel_reminder"}
    assert "desde el PC" in down[-1]
    assert tools.find("read_file") is None
    assert not tools.call("read_file", {"path": "x"}).ok
    assert hub.executed == []
    assert {"read_file", "find_files", "create_file", "overwrite_file", "delete_note"} <= BLOCKED


class Asks:
    """Un modelo que insiste en leer un archivo aunque no lo tenga a la vista."""

    name, model, needs_network = "nube", "fake", True

    def __init__(self) -> None:
        self.turns = 0
        self.seen_tools: list[str] = []

    def unavailable_reason(self):
        return None

    def stream_chat(self, messages, tools=None):
        self.turns += 1
        self.seen_tools = [t["function"]["name"] for t in tools or []]
        if self.turns == 1:
            yield ToolCall("read_file", {"path": "plan.md"}, "c1")
        else:
            yield Text("Eso solo lo puedo hacer desde el PC.")
        yield Done(1, 1)


def test_the_server_blocks_a_file_tool_even_if_the_model_asks():
    hub = Hub()
    provider = Asks()
    cascade = Cascade([Layer(provider)], FakeConnectivity())
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "lee plan.md"}]
    events = list(
        agent.run_turn(cascade, RemoteTools(hub), messages, lambda *_: True, max_tools=10)
    )
    assert hub.executed == []
    assert "read_file" not in provider.seen_tools
    assert any(isinstance(e, agent.FinalAnswer) for e in events)
