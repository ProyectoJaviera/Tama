import importlib
import os
import sys

import evals
from tama import brain as brain_module
from tama.cascade import Answered, Layer
from tama.config import Settings
from tama.providers import Done, Error, Text
from tests.fakes import FakeConnectivity


def names(settings):
    brain = brain_module.build(settings, with_tools=False)
    result = [layer.provider.name for layer in brain.cascade.layers]
    brain.close()
    return result


def test_claude_layer_is_on_by_default(tmp_path):
    assert names(Settings(data_dir=tmp_path)) == ["nube", "claude", "local"]


def test_switch_removes_the_claude_layer(tmp_path):
    assert names(Settings(data_dir=tmp_path, claude_enabled=False)) == ["nube", "local"]


class Failing:
    name, model, needs_network = "nube", "m", True

    def unavailable_reason(self):
        return None

    def stream_chat(self, messages, tools=None):
        yield Error("caída")


class Local:
    name, model, needs_network = "local", "m", False

    def unavailable_reason(self):
        return None

    def stream_chat(self, messages, tools=None):
        yield Text("hola")
        yield Done(1, 1)


def test_cascade_goes_from_cloud_to_local_without_claude(tmp_path):
    brain = brain_module.build(Settings(data_dir=tmp_path, claude_enabled=False), with_tools=False)
    brain.cascade.layers = [
        Layer(Failing() if layer.provider.name == "nube" else Local())
        for layer in brain.cascade.layers
    ]
    brain.cascade._connectivity = FakeConnectivity(True)
    answered = [e for e in brain.cascade.turn([]) if isinstance(e, Answered)]
    brain.close()
    assert [a.provider for a in answered] == ["local"]


def test_evals_turn_claude_off_unless_asked(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["evals"])
    importlib.reload(evals)
    assert os.environ["TAMA_CLAUDE_ENABLED"] == "false"
    del os.environ["TAMA_CLAUDE_ENABLED"]
    monkeypatch.setattr(sys, "argv", ["evals", "--con-claude"])
    importlib.reload(evals)
    assert "TAMA_CLAUDE_ENABLED" not in os.environ
