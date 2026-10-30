import sys

import pytest

from tama.cascade import CircuitBreaker
from tama.mcp_hub import McpHub, ServerConfig, load_config, server_env, truncate
from tests.fakes import FakeClock

NOTES = ServerConfig("notas", [sys.executable, "-m", "tama.mcp_servers.notas"])
FILES = ServerConfig("archivos", [sys.executable, "-m", "tama.mcp_servers.archivos"])
BROKEN = ServerConfig("roto", [sys.executable, "-c", "import sys; sys.exit(3)"])


@pytest.fixture
def make_hub(tmp_path):
    hubs = []

    def make(configs, **kw):
        hub = McpHub(configs, server_env(tmp_path / "data", tmp_path / "trabajo"), **kw)
        hub.start()
        hubs.append(hub)
        return hub

    yield make
    for hub in hubs:
        hub.close()


def test_real_servers_start_and_expose_tools(make_hub):
    hub = make_hub([NOTES, FILES])
    tools, down = hub.available_tools()
    assert down == []
    assert {t.name for t in tools} >= {"create_note", "search_notes", "read_file"}
    assert {t.name for t in tools if t.destructive} == {
        "update_note",
        "delete_note",
        "overwrite_file",
    }
    assert tools[0].as_provider_tool()["function"]["parameters"]["type"] == "object"


def test_call_runs_tool_in_subprocess(make_hub, tmp_path):
    hub = make_hub([NOTES])
    assert hub.call("create_note", {"title": "Compras", "content": "filamento"}).ok
    found = hub.call("search_notes", {"query": "filamento"})
    assert found.ok and "Compras" in found.text
    assert (tmp_path / "data" / "notas.db").exists()


def test_broken_server_is_isolated(make_hub):
    hub = make_hub([BROKEN, NOTES])
    tools, down = hub.available_tools()
    assert down == ["roto"]
    assert tools and all(t.server == "notas" for t in tools)
    states = {name: state for name, state, _ in hub.status()}
    assert states["notas"] == "listo" and "no arrancó" in states["roto"]


def test_broken_server_breaker_opens_after_three_failures(make_hub):
    clock = FakeClock()
    hub = make_hub(
        [BROKEN],
        breaker_factory=lambda: CircuitBreaker(3, 300, clock),
        retry_after_s=0,
        clock=clock,
        start_timeout_s=10,
    )
    for _ in range(3):
        hub.unavailable_reason("roto")  # cada consulta reintenta lanzarlo
    assert "fuera de servicio" in hub.unavailable_reason("roto")


def test_network_server_is_unavailable_offline(make_hub):
    web = ServerConfig("web", NOTES.command, requires_network=True)
    hub = make_hub([web, FILES], online=lambda: False)
    tools, down = hub.available_tools()
    assert down == ["web"] and all(t.server == "archivos" for t in tools)
    assert "sin internet" in hub.call("search_notes", {}).text


def test_tool_whitelist_and_max_tools(make_hub):
    only_search = ServerConfig("notas", NOTES.command, tools=["search_notes"])
    hub = make_hub([only_search, FILES])
    tools, _ = hub.available_tools(max_tools=3)
    assert [t.name for t in tools][:1] == ["search_notes"] and len(tools) == 3


def test_unknown_tool_is_an_error_outcome(make_hub):
    hub = make_hub([NOTES])
    outcome = hub.call("no_existe", {})
    assert not outcome.ok and "no existe" in outcome.text


def test_truncate_keeps_short_text():
    assert truncate("hola", 10) == "hola"
    long = truncate("x" * 50, 10)
    assert long.startswith("x" * 10) and "recortado: 50" in long


def test_config_uses_tama_python(tmp_path):
    config = tmp_path / "mcp.toml"
    config.write_text(
        '[servidores.uno]\ncomando = ["python", "-m", "algo"]\nrequiere_red = true\n'
        'herramientas = ["a"]\n',
        encoding="utf-8",
    )
    [server] = load_config(config)
    assert server.command == [sys.executable, "-m", "algo"]
    assert server.requires_network and server.tools == ["a"]


def test_server_env_drops_avast_keylog(monkeypatch, tmp_path):
    monkeypatch.setenv("SSLKEYLOGFILE", r"\\.\aswMonFltProxy\1")
    env = server_env(tmp_path / "d", tmp_path / "w")
    assert "SSLKEYLOGFILE" not in env and env["TAMA_WORKDIR"].endswith("w")


def test_config_description_overrides_server_text(make_hub):
    custom = ServerConfig(
        "notas", NOTES.command, descriptions={"search_notes": "Busca en las notas (propia)."}
    )
    hub = make_hub([custom])
    found = hub.find("search_notes")
    assert found.description == "Busca en las notas (propia)."
    assert hub.find("create_note").description.startswith("Guarda una nota")


def test_real_config_loads_spanish_descriptions():
    from pathlib import Path

    configs = {c.name: c for c in load_config(Path(__file__).parent.parent / "config" / "mcp.toml")}
    assert "Úsala" in configs["web"].descriptions["fetch"]
    assert configs["tiempo"].tools == ["get_current_time"]
