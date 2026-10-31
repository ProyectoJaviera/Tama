from pathlib import Path

from tama.config import Settings
from tama.mcp_hub import load_config

CONFIG = Path(__file__).parent.parent / "config" / "mcp.toml"
ROOT_TOOLS = {"notas": 4, "archivos": 4, "web": 1}


def test_every_configured_tool_fits_in_the_limit():
    total = sum(
        len(server.tools) if server.tools else ROOT_TOOLS[server.name]
        for server in load_config(CONFIG)
    )
    assert total <= Settings().tools_max
