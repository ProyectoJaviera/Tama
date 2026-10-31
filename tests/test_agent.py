from dataclasses import dataclass

from tama import agent
from tama.cascade import Cascade, Layer
from tama.mcp_hub import ToolInfo, ToolOutcome
from tama.providers import Done, Text, ToolCall
from tests.fakes import FakeConnectivity

SCHEMA = {"type": "object", "properties": {}}


class ScriptedProvider:
    """Cada llamada devuelve el siguiente guion; guarda los mensajes y herramientas recibidos."""

    name, model, needs_network = "local", "fake", False

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls = []

    def unavailable_reason(self):
        return None

    def stream_chat(self, messages, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        yield from self.scripts.pop(0)


@dataclass
class FakeToolBox:
    tools: list
    down: list
    results: dict

    def __post_init__(self):
        self.executed = []

    def available_tools(self, max_tools=10):
        return self.tools[:max_tools], self.down

    def find(self, name):
        return next((t for t in self.tools if t.name == name), None)

    def call(self, name, arguments):
        self.executed.append((name, arguments))
        return ToolOutcome(True, self.results.get(name, "ok"))


TIME = ToolInfo("tiempo", "get_current_time", "Hora actual", SCHEMA)
DELETE = ToolInfo("notas", "delete_note", "Borra una nota", SCHEMA, destructive=True)


def run(provider, box, confirm=lambda call, info: True, **kw):
    cascade = Cascade([Layer(provider)], FakeConnectivity())
    messages = [{"role": "system", "content": "Eres Tama."}, {"role": "user", "content": "x"}]
    return list(agent.run_turn(cascade, box, messages, confirm, **kw)), messages


def final(events):
    return next(e for e in events if isinstance(e, agent.FinalAnswer)).text


def test_tool_call_then_answer():
    provider = ScriptedProvider(
        [ToolCall("get_current_time", {"timezone": "America/Santiago"}, "c1"), Done(1, 1)],
        [Text("Son las 22:53."), Done(1, 1)],
    )
    box = FakeToolBox([TIME], [], {"get_current_time": "22:53"})
    events, original = run(provider, box)

    assert final(events) == "Son las 22:53."
    assert box.executed == [("get_current_time", {"timezone": "America/Santiago"})]
    second = provider.calls[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "tool_name": "get_current_time",
        "content": "22:53",
    }
    assert provider.calls[0]["tools"][0]["function"]["name"] == "get_current_time"
    assert len(original) == 2  # el historial de la CLI no se toca


def test_no_tool_needed_answers_directly():
    provider = ScriptedProvider([Text("¡Hola!"), Done(1, 1)])
    events, _ = run(provider, FakeToolBox([TIME], [], {}))
    assert final(events) == "¡Hola!"
    assert not any(isinstance(e, agent.ToolUse) for e in events)


def test_destructive_tool_needs_explicit_yes():
    script = ([ToolCall("delete_note", {"note_id": 1}), Done(1, 1)], [Text("Listo."), Done(1, 1)])
    box = FakeToolBox([DELETE], [], {})
    events, _ = run(ScriptedProvider(*script), box, confirm=lambda call, info: False)

    assert box.executed == []  # sin "s" no se ejecuta
    result = next(e for e in events if isinstance(e, agent.ToolResult))
    assert result.skipped and "NO se ejecutó" in result.text

    box_yes = FakeToolBox([DELETE], [], {})
    run(ScriptedProvider(*script), box_yes, confirm=lambda call, info: True)
    assert box_yes.executed == [("delete_note", {"note_id": 1})]


def test_non_destructive_tools_do_not_ask():
    asked = []
    provider = ScriptedProvider(
        [ToolCall("get_current_time", {}), Done(1, 1)], [Text("ok"), Done(1, 1)]
    )
    run(provider, FakeToolBox([TIME], [], {}), confirm=lambda c, i: asked.append(c) or True)
    assert asked == []


def test_step_limit_forces_answer_without_tools():
    looping = [[ToolCall("get_current_time", {}), Done(1, 1)] for _ in range(5)]
    provider = ScriptedProvider(*looping, [Text("Me rindo con las herramientas."), Done(1, 1)])
    box = FakeToolBox([TIME], [], {})
    events, _ = run(provider, box, max_steps=5)

    assert len(box.executed) == 5
    assert provider.calls[-1]["tools"] is None  # el último paso va sin herramientas
    assert final(events) == "Me rindo con las herramientas."


def test_down_servers_are_announced_to_the_model():
    provider = ScriptedProvider([Text("No puedo ver tus notas ahora."), Done(1, 1)])
    run(provider, FakeToolBox([TIME], ["notas"], {}))
    system = provider.calls[0]["messages"][0]["content"]
    assert system.startswith("Eres Tama.") and "no disponibles" in system and "notas" in system


def test_without_toolbox_sends_no_tools():
    provider = ScriptedProvider([Text("hola"), Done(1, 1)])
    events, _ = run(provider, None)
    assert provider.calls[0]["tools"] is None and final(events) == "hola"


def test_max_tools_limits_what_the_model_sees():
    many = [ToolInfo("s", f"t{i}", "d", SCHEMA) for i in range(15)]
    provider = ScriptedProvider([Text("ok"), Done(1, 1)])
    run(provider, FakeToolBox(many, [], {}), max_tools=10)
    assert len(provider.calls[0]["tools"]) == 10


def test_rejected_action_tells_the_model_it_was_not_done():
    provider = ScriptedProvider(
        [ToolCall("delete_note", {"note_id": 1}, "c1"), Done(1, 1)],
        [Text("No la borré; sigue ahí."), Done(1, 1)],
    )
    box = FakeToolBox([DELETE], [], {})
    run(provider, box, confirm=lambda call, info: False)
    result = provider.calls[1]["messages"][-1]
    assert result["role"] == "tool"
    assert "NO se ejecutó" in result["content"] and "CANCELADO" in result["content"]
    assert box.executed == []


def test_prepare_matches_what_the_turn_sends():
    provider = ScriptedProvider([Text("ok"), Done(1, 1)])
    box = FakeToolBox([TIME], ["notas"], {})
    messages = [{"role": "system", "content": "Eres Tama."}, {"role": "user", "content": "x"}]
    work, specs = agent.prepare(box, messages)
    list(agent.run_turn(Cascade([Layer(provider)], FakeConnectivity()), box, messages, None))
    assert provider.calls[0]["messages"] == work and provider.calls[0]["tools"] == specs


def test_final_answer_carries_a_short_tool_trace():
    provider = ScriptedProvider(
        [ToolCall("get_current_time", {}, "c1"), Done(1, 1)],
        [Text("Son las 22:53."), Done(1, 1)],
    )
    box = FakeToolBox([TIME], [], {"get_current_time": "x" * 1000})
    events, _ = run(provider, box)
    answer = next(e for e in events if isinstance(e, agent.FinalAnswer))
    assert [m["role"] for m in answer.trace] == ["assistant", "tool"]
    assert answer.trace[0]["tool_calls"][0]["id"] == "c1"
    assert len(answer.trace[1]["content"]) == agent.MAX_RESULT_IN_HISTORY


def test_cli_history_keeps_tool_calls(tmp_path):
    import tama.brain as brain_module  # noqa: F401
    from tama import cli
    from tama.brain import Brain
    from tama.events import EventStore

    class Local:
        def close(self):
            pass

    provider = ScriptedProvider(
        [ToolCall("get_current_time", {}, "c1"), Done(1, 1)],
        [Text("Son las 22:53."), Done(1, 1)],
    )
    box = FakeToolBox([TIME], [], {})
    box.current_event = None
    cascade = Cascade([Layer(provider)], FakeConnectivity())
    brain = Brain(cascade, EventStore(tmp_path / "e.db"), Local(), tools=box)
    messages = [{"role": "system", "content": "x"}]
    assert cli.turn(brain, messages, "¿hora?", 2000)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "assistant"]
    brain.store.close()
