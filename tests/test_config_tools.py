from pathlib import Path

from tama.mcp_hub import load_config

CONFIG = Path(__file__).parent.parent / "config" / "mcp.toml"


def test_essential_tools_stay_within_the_limit():
    servers = load_config(CONFIG)
    always = [name for server in servers for name in server.always]
    assert set(always) == {"get_current_time", "search_nodes", "create_entities"}
    base = [name for server in servers if server.base for name in server.base]
    # Notas (4), archivos (4) y web (1) no indican base; abrir_herramientas ocupa uno más.
    assert len(always) + len(base) + 9 + 1 <= 16
