from tama import cli
from tama.ollama import OllamaError


class FakeClient:
    """LLM simulado: responde con fragmentos fijos y guarda lo que recibió."""

    def __init__(self, chunks=("¡Ho", "la!"), error=None, models=None, down=False):
        self.chunks = chunks
        self.error = error
        self.models = models if models is not None else [cli.get_settings().local_model]
        self.down = down
        self.calls = []
        self.closed = False

    def list_models(self):
        if self.down:
            raise OllamaError("connection refused")
        return self.models

    def stream_chat(self, model, messages, think=None, options=None):
        self.calls.append((model, list(messages)))
        self.options = options
        if self.error:
            raise OllamaError(self.error)
        yield from self.chunks

    def close(self):
        self.closed = True


def test_one_shot_message_streams_reply(capsys):
    client = FakeClient()
    code = cli.main(["hola", "Tama"], client=client)

    assert code == 0
    assert capsys.readouterr().out == "¡Hola!\n"
    model, messages = client.calls[0]
    assert model == cli.get_settings().local_model
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "hola Tama"}
    assert client.closed


def test_model_flag_overrides_settings():
    client = FakeClient(models=["otro:1b"])
    cli.main(["--modelo", "otro:1b", "hola"], client=client)
    assert client.calls[0][0] == "otro:1b"


def test_ollama_down_shows_clear_message(capsys):
    code = cli.main(["hola"], client=FakeClient(down=True))
    assert code == 1
    assert "Ollama no está corriendo en el host" in capsys.readouterr().err


def test_missing_model_suggests_pull(capsys):
    code = cli.main(["hola"], client=FakeClient(models=[]))
    assert code == 1
    assert "ollama pull" in capsys.readouterr().err


def test_check_flag_does_not_chat(capsys):
    client = FakeClient()
    assert cli.main(["--check"], client=client) == 0
    assert client.calls == []
    assert capsys.readouterr().out.startswith("OK")


def test_ollama_error_mid_stream_returns_1(capsys):
    code = cli.main(["hola"], client=FakeClient(error="sin conexión"))
    assert code == 1
    assert "sin conexión" in capsys.readouterr().err


def test_chat_loop_survives_ollama_down(monkeypatch, capsys):
    inputs = iter(["hola", "salir"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))

    assert cli.main([], client=FakeClient(down=True, error="connection refused")) == 0
    err = capsys.readouterr().err
    assert "Ollama no está corriendo en el host" in err
    assert "No pude hablar con Ollama" in err


def test_chat_loop_keeps_history(monkeypatch):
    inputs = iter(["hola", "¿y tú?", "salir"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    client = FakeClient()

    assert cli.main([], client=client) == 0
    assert len(client.calls) == 2
    second = client.calls[1][1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[2]["content"] == "¡Hola!"


def test_num_ctx_and_cpu_only_are_always_sent():
    client = FakeClient()
    cli.main(["hola"], client=client)
    settings = cli.get_settings()
    assert client.options == {"num_ctx": settings.num_ctx, "num_gpu": 0}
