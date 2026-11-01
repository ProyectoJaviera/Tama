from tama.agent import prepare
from tama.mcp_hub import ToolInfo
from tama.selector import ALWAYS, BASE, EXTRA, OPENER, ToolFocus, opener_spec, select


def tool(name, tier=BASE, server="s", group=""):
    return ToolInfo(server, name, "d", {}, tier=tier, group=group)


TOOLS = [
    tool("hora", ALWAYS, "tiempo"),
    tool("buscar", ALWAYS, "memoria"),
    tool("eventos", BASE, "memoria"),
    tool("corregir", EXTRA, "memoria"),
    tool("nota", BASE, "notas"),
    tool("borrar_nota", EXTRA, "notas"),
    tool("meta", EXTRA, "memoria", "objetivos"),
]


def names(tools):
    return [t.name for t in tools]


def test_everything_fits_without_opener():
    assert select(TOOLS, 7) == (TOOLS, False)


def test_always_then_base_and_room_for_the_opener():
    visible, hidden = select(TOOLS, 4)
    assert hidden
    assert names(visible) == ["hora", "buscar", "eventos"]


def test_previous_turn_tools_stay_visible_before_base():
    visible, _ = select(TOOLS, 4, ToolFocus(used={"borrar_nota"}))
    assert names(visible) == ["hora", "buscar", "borrar_nota"]


def test_opened_group_goes_before_base():
    visible, _ = select(TOOLS, 4, ToolFocus(groups={"objetivos"}))
    assert names(visible) == ["hora", "buscar", "meta"]


def test_opener_lists_hidden_tools_by_group():
    visible, _ = select(TOOLS, 4)
    spec = opener_spec(TOOLS, visible)["function"]
    assert spec["name"] == OPENER
    assert spec["parameters"]["properties"]["grupo"]["enum"] == ["memoria", "notas", "objetivos"]
    assert "objetivos: meta" in spec["description"]


class Box:
    def available_tools(self, max_tools=10):
        return TOOLS[:max_tools], []


def test_prepare_adds_opener_within_the_limit():
    _, specs = prepare(Box(), [], 4)
    assert len(specs) == 4
    assert specs[-1]["function"]["name"] == OPENER
