import pytest

from tama import cli
from tama.brain import Brain
from tama.cascade import Cascade, Layer
from tama.config import Settings
from tama.events import EventStore
from tama.ollama import OllamaError
from tests.fakes import FakeConnectivity, FakeProvider, failing


class FakeLocalClient:
    def __init__(self, models=None, down=False):
        self.models = models if models is not None else [Settings().local_model]
        self.down = down
        self.closed = False

    def list_models(self):
        if self.down:
            raise OllamaError("connection refused")
        return self.models

    def close(self):
        self.closed = True


def make_brain(tmp_path, providers, online=True, local=None) -> Brain:
    cascade = Cascade([Layer(p) for p in providers], FakeConnectivity(online))
    return Brain(cascade, EventStore(tmp_path / "events.db"), local or FakeLocalClient())


@pytest.fixture
def store_rows(tmp_path):
    def rows(type=None):
        s = EventStore(tmp_path / "events.db")
        try:
            return s.rows(type)
        finally:
            s.close()

    return rows


def test_one_shot_streams_reply_and_logs_turn(tmp_path, capsys, store_rows):
    local = FakeProvider("local")
    code = cli.main(["hola", "Tama"], brain=make_brain(tmp_path, [local]), settings=Settings())

    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("hola desde local\n")
    assert "· local (local-model)" in out
    assert local.calls[0][0]["content"].startswith("Eres Tama")  # identidad desde archivo
    assert local.calls[0][-1] == {"role": "user", "content": "hola Tama"}
    assert [r["type"] for r in store_rows()] == ["user_msg", "assistant_msg"]
    assert store_rows("assistant_msg")[0]["provider"] == "local"


def test_switch_is_announced_and_logged(tmp_path, capsys, store_rows):
    brain = make_brain(tmp_path, [failing("nube"), FakeProvider("local")])
    assert cli.main(["hola"], brain=brain, settings=Settings()) == 0

    assert "seguí en local" in capsys.readouterr().out
    assert store_rows("provider_switch")[0]["provider"] == "local"


def test_offline_answers_locally(tmp_path, capsys):
    cloud = FakeProvider("nube", needs_network=True)
    brain = make_brain(tmp_path, [cloud, FakeProvider("local")], online=False)
    assert cli.main(["hola"], brain=brain, settings=Settings()) == 0
    assert capsys.readouterr().out.startswith("hola desde local")
    assert cloud.calls == []


def test_no_layer_answers_returns_1_and_logs_error(tmp_path, capsys, store_rows):
    brain = make_brain(tmp_path, [failing("local", needs_network=False)])
    assert cli.main(["hola"], brain=brain, settings=Settings()) == 1
    assert "No pude responder" in capsys.readouterr().err
    assert store_rows("error")


def test_check_reports_layers_and_local_health(tmp_path, capsys):
    brain = make_brain(
        tmp_path, [FakeProvider("nube", missing="falta OLLAMA_API_KEY"), FakeProvider("local")]
    )
    assert cli.main(["--check"], brain=brain, settings=Settings()) == 0
    out = capsys.readouterr().out
    assert "falta OLLAMA_API_KEY" in out and out.strip().endswith(
        f"tiene {Settings().local_model}."
    )


def test_check_fails_when_local_ollama_is_down(tmp_path, capsys):
    brain = make_brain(tmp_path, [FakeProvider("local")], local=FakeLocalClient(down=True))
    assert cli.main(["--check"], brain=brain, settings=Settings()) == 1
    assert "Ollama no está corriendo en el host" in capsys.readouterr().err


def test_missing_model_only_warns_on_one_shot(tmp_path, capsys):
    brain = make_brain(tmp_path, [FakeProvider("local")], local=FakeLocalClient(models=[]))
    assert cli.main(["hola"], brain=brain, settings=Settings()) == 0
    assert "ollama pull" in capsys.readouterr().err


def test_chat_loop_commands_and_history(tmp_path, monkeypatch, capsys):
    inputs = iter(["hola", "/proveedor", "¿y tú?", "/salir"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    local = FakeProvider("local")

    assert cli.main([], brain=make_brain(tmp_path, [local]), settings=Settings()) == 0
    assert len(local.calls) == 2
    assert [m["role"] for m in local.calls[1]] == ["system", "user", "assistant", "user"]
    assert "local-model" in capsys.readouterr().out  # salida de /proveedor


def test_history_is_trimmed_before_sending(tmp_path, monkeypatch):
    inputs = iter(["a" * 7000, "b" * 7000, "corto", "/salir"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    local = FakeProvider("local")
    settings = Settings(history_max_tokens=2000)

    cli.main([], brain=make_brain(tmp_path, [local]), settings=settings)
    last = local.calls[-1]
    assert not any(m["content"] == "a" * 7000 for m in last)
    assert last[-1]["content"] == "corto"


def test_failed_turn_is_not_kept_in_history(tmp_path, monkeypatch):
    inputs = iter(["primero", "segundo", "/salir"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    flaky = FakeProvider("local")
    scripts = iter([failing("x").script, FakeProvider("local").script])
    original = flaky.stream_chat

    def stream_chat(messages, tools=None):
        flaky.script = next(scripts)
        return original(messages, tools)

    flaky.stream_chat = stream_chat
    cli.main([], brain=make_brain(tmp_path, [flaky]), settings=Settings())
    assert [m["content"] for m in flaky.calls[1] if m["role"] == "user"] == ["segundo"]


def test_build_can_keep_only_some_layers(tmp_path):
    from tama import brain as brain_module

    brain = brain_module.build(Settings(data_dir=tmp_path), only=["claude"])
    try:
        assert [layer.provider.name for layer in brain.cascade.layers] == ["claude"]
    finally:
        brain.close()


def test_footer_shows_tokens_per_second_only_when_known(tmp_path, capsys):
    from tama.providers import Done, Text

    with_tokens = FakeProvider("local", [Text("hola"), Done(5, 40, generation_s=2.0)])
    cli.main(["hola"], brain=make_brain(tmp_path / "a", [with_tokens]), settings=Settings())
    assert "· 20 tok/s" in capsys.readouterr().out

    without = FakeProvider("local", [Text("hola"), Done(5, 0)])
    cli.main(["hola"], brain=make_brain(tmp_path / "b", [without]), settings=Settings())
    assert "tok/s" not in capsys.readouterr().out
