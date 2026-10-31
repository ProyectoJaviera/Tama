from collections.abc import Iterator

from tama.memory import MAX_AUTO_RECALL_CHARS, recall_context
from tama.providers import Done, Text
from tama.providers.ollama import OllamaLocalProvider


class Outcome:
    def __init__(self, text: str, ok: bool = True) -> None:
        self.text, self.ok = text, ok


class Tools:
    def __init__(self, text: str, ok: bool = True) -> None:
        self.outcome = Outcome(text, ok)
        self.queries: list[str] = []

    def call(self, name, args):
        assert name == "search_nodes"
        self.queries.append(args["query"])
        return self.outcome


class FakeClient:
    def __init__(self) -> None:
        self.messages = None

    def stream_raw(self, model, messages, tools=None, think=None, options=None) -> Iterator[dict]:
        self.messages = messages
        yield {"message": {"content": "ok"}, "done": True}


def provider(found):
    client = FakeClient()
    local = OllamaLocalProvider(client, "m", 8192)
    local.recall = lambda text: found
    return local, client


def test_recall_searches_with_message_words_only():
    tools = Tools("Bruno (mascota)")
    assert "De tu memoria" in recall_context(tools, "¿Cómo se llama mi perro?")
    assert tools.queries == ["¿Cómo llama perro?"]


def test_recall_adds_nothing_without_matches():
    assert recall_context(Tools("No hay coincidencias con esas palabras."), "marte lejano") == ""
    assert recall_context(Tools("No hay nada anotado."), "marte lejano") == ""
    assert recall_context(Tools("falló", ok=False), "marte lejano") == ""


def test_recall_skips_short_messages():
    tools = Tools("algo")
    assert recall_context(tools, "sí, ok") == ""
    assert tools.queries == []


def test_recall_is_capped():
    text = recall_context(Tools("x" * 5000), "palabra larga")
    assert len(text) <= MAX_AUTO_RECALL_CHARS + len("De tu memoria:\n")


def test_local_provider_adds_recall_to_system_prompt():
    local, client = provider("De tu memoria:\nBruno")
    messages = [{"role": "system", "content": "Eres Tama."}, {"role": "user", "content": "x"}]
    assert list(local.stream_chat(messages)) == [Text("ok"), Done(0, 0)]
    assert client.messages[0]["content"] == "Eres Tama.\n\nDe tu memoria:\nBruno"
    assert messages[0]["content"] == "Eres Tama."  # no toca el historial


def test_local_provider_without_matches_keeps_prompt():
    local, client = provider("")
    messages = [{"role": "system", "content": "Eres Tama."}, {"role": "user", "content": "x"}]
    list(local.stream_chat(messages))
    assert client.messages[0]["content"] == "Eres Tama."


def test_recall_runs_only_on_user_turns():
    local, client = provider("De tu memoria:\nBruno")
    messages = [
        {"role": "system", "content": "Eres Tama."},
        {"role": "tool", "content": "resultado"},
    ]
    list(local.stream_chat(messages))
    assert client.messages[0]["content"] == "Eres Tama."
