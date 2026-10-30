import json
from pathlib import Path

import pytest

from tama import cli
from tama.events import EventStore
from tama.mcp_hub import load_config
from tama.mcp_servers import memoria
from tama.memory import session_prompt


CONFIG = Path(__file__).parent.parent / "config" / "mcp.toml"


@pytest.fixture(autouse=True)
def memory_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))


def test_create_and_list_with_date():
    assert "#1" in memoria.create_goal("Correr 10K", "Antes de fin de año", "2026-12-31")
    text = memoria.list_goals()
    assert "#1 Correr 10K (activo, para 2026-12-31)" in text
    assert "Antes de fin de año" in text


def test_update_by_title_fragment_keeps_other_fields():
    memoria.create_goal("Correr 10K", "Antes de fin de año")
    assert "pausado" in memoria.update_goal("correr", estado="pausado")
    assert memoria.list_goals() == "No hay objetivos en ese estado."
    assert "Antes de fin de año" in memoria.list_goals("pausado")


def test_update_cannot_discard():
    memoria.create_goal("Correr 10K")
    assert "discard_goal" in memoria.update_goal("1", estado="descartado")
    assert "activo" in memoria.list_goals()


def test_progress_is_dated_and_listed():
    memoria.create_goal("Correr 10K")
    memoria.add_goal_progress("#1", "Corrí 5 km")
    assert "Corrí 5 km" in memoria.list_goals()


def test_unknown_goal():
    assert memoria.add_goal_progress("volar", "x") == "No encontré ese objetivo."


def test_discard_and_delete_are_destructive():
    tools = {t.name: t for t in memoria.server._tool_manager.list_tools()}
    assert tools["discard_goal"].annotations.destructive_hint
    assert tools["delete_goal"].annotations.destructive_hint


def test_model_only_sees_goal_tools_through_their_group():
    config = next(c for c in load_config(CONFIG) if c.name == "memoria")
    assert "delete_goal" not in config.tools
    assert config.groups["list_goals"] == "objetivos"


def test_export_includes_goals(tmp_path):
    memoria.create_goal("Correr 10K")
    memoria.add_goal_progress("1", "Corrí 5 km")
    target = tmp_path / "grafo.jsonl"
    memoria.export_graph(target)
    line = json.loads(target.read_text(encoding="utf-8").splitlines()[-1])
    assert line["type"] == "goal"
    assert line["progress"][0]["content"] == "Corrí 5 km"


def test_suggestion_is_given_once_and_never_repeated():
    assert memoria.add_suggestion("Retomar el libro")
    assert not memoria.add_suggestion("Retomar el libro")
    assert memoria.next_suggestion() == "Retomar el libro"
    assert memoria.next_suggestion() == memoria.NO_SUGGESTION


def test_suggestion_goes_to_the_session_prompt(tmp_path):
    prompt = session_prompt(tmp_path, EventStore(":memory:"), "Retomar el libro")
    assert "Retomar el libro" in prompt
    assert "sin insistir" in prompt


class Tools:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        return type("Outcome", (), {"ok": True, "text": "listo"})()


class FakeBrain:
    def __init__(self):
        self.tools = Tools()


def test_goals_command_deletes_only_after_confirmation(monkeypatch, capsys):
    brain = FakeBrain()
    monkeypatch.setattr("builtins.input", lambda _: "n")
    cli.show_goals(brain, "borrar correr")
    assert brain.tools.calls == []
    monkeypatch.setattr("builtins.input", lambda _: "s")
    cli.show_goals(brain, "borrar correr")
    assert brain.tools.calls == [("delete_goal", {"objetivo": "correr"})]
