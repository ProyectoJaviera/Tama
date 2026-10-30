import json
from types import SimpleNamespace

import anthropic
import httpx

from tama.ollama import OllamaClient
from tama.providers import Done, Error, Text, ToolCall
from tama.providers.claude import ClaudeProvider, to_claude_messages, to_claude_tool
from tama.providers.ollama import OllamaCloudProvider, OllamaLocalProvider
from tests.fakes import FakeClock

TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Hora actual",
        "parameters": {"type": "object", "properties": {}},
    },
}


def ollama_client(handler, **kw) -> OllamaClient:
    return OllamaClient("http://ollama.test", transport=httpx.MockTransport(handler), **kw)


def ndjson(*chunks):
    return "\n".join(json.dumps(c) for c in chunks).encode()


def test_local_provider_maps_chunks_to_events_and_forces_cpu():
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "Ho"}},
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"function": {"name": "get_time", "arguments": {}}}],
                    }
                },
                {
                    "message": {"content": "la"},
                    "done": True,
                    "prompt_eval_count": 12,
                    "eval_count": 3,
                },
            ),
        )

    provider = OllamaLocalProvider(ollama_client(handler), "qwen", num_ctx=8192)
    events = list(provider.stream_chat([{"role": "user", "content": "hola"}], tools=[TOOL]))

    assert events == [Text("Ho"), ToolCall("get_time", {}), Text("la"), Done(12, 3)]
    assert sent["options"] == {"num_ctx": 8192, "num_gpu": 0}
    assert sent["think"] is False and sent["tools"] == [TOOL]


def test_local_provider_turns_http_errors_into_error_event():
    provider = OllamaLocalProvider(
        ollama_client(lambda r: httpx.Response(404, json={"error": "model not found"})), "x", 4096
    )
    assert list(provider.stream_chat([])) == [Error("model not found")]


def test_unfinished_stream_is_an_error():
    provider = OllamaLocalProvider(
        ollama_client(lambda r: httpx.Response(200, content=ndjson({"message": {"content": "a"}}))),
        "x",
        4096,
    )
    assert isinstance(list(provider.stream_chat([]))[-1], Error)


def capture_cloud(sent, status=200):
    def handler(request):
        sent["auth"] = request.headers.get("authorization")
        sent["model"] = json.loads(request.content)["model"]
        sent["calls"] = sent.get("calls", 0) + 1
        if status != 200:
            return httpx.Response(status, json={"error": "unauthorized"})
        return httpx.Response(200, content=ndjson({"message": {"content": "ok"}, "done": True}))

    return handler


def test_cloud_through_local_daemon_needs_no_key():
    sent = {}
    provider = OllamaCloudProvider(ollama_client(capture_cloud(sent)), "gemma4:31b-cloud")
    events = list(provider.stream_chat([]))

    assert events[-1] == Done(0, 0)
    assert sent["auth"] is None and sent["model"] == "gemma4:31b-cloud"
    assert provider.needs_network and provider.unavailable_reason() is None


def test_cloud_direct_sends_key_and_base_model_name():
    sent = {}
    client = ollama_client(capture_cloud(sent), api_key="clave-de-prueba")
    provider = OllamaCloudProvider(client, "gemma4:31b-cloud", direct=True)
    list(provider.stream_chat([]))
    assert (sent["auth"], sent["model"]) == ("Bearer clave-de-prueba", "gemma4:31b")


def test_cloud_401_means_no_session_and_is_retried_later():
    sent, clock = {}, FakeClock()
    provider = OllamaCloudProvider(
        ollama_client(capture_cloud(sent, status=401)), "gemma4:31b-cloud", clock=clock
    )
    events = list(provider.stream_chat([]))

    assert isinstance(events[-1], Error)
    assert "ollama signin" in provider.unavailable_reason()
    clock.now += 300
    assert provider.unavailable_reason() is None


def test_cloud_other_errors_do_not_mark_session():
    provider = OllamaCloudProvider(ollama_client(capture_cloud({}, status=500)), "gemma4:31b-cloud")
    assert isinstance(list(provider.stream_chat([]))[-1], Error)
    assert provider.unavailable_reason() is None


def test_claude_message_conversion():
    system, turns = to_claude_messages(
        [
            {"role": "system", "content": "Eres Tama."},
            {"role": "assistant", "content": "huérfano"},
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "¡hola!"},
        ]
    )
    assert system == "Eres Tama."
    assert turns == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡hola!"},
    ]


def test_claude_tool_conversion():
    assert to_claude_tool(TOOL) == {
        "name": "get_time",
        "description": "Hora actual",
        "input_schema": {"type": "object", "properties": {}},
        "eager_input_streaming": True,
    }


class FakeStream:
    def __init__(self, texts, final):
        self._events = [SimpleNamespace(type="text", text=t) for t in texts]
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


class FakeAnthropic:
    def __init__(self, stream=None, error=None):
        self.params = None
        self._stream, self._error = stream, error
        self.messages = self

    def stream(self, **params):
        self.params = params
        if self._error:
            raise self._error
        return self._stream


def final(stop_reason="end_turn", content=()):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=list(content),
        usage=SimpleNamespace(input_tokens=40, output_tokens=7),
    )


def test_claude_provider_streams_text_and_usage():
    client = FakeAnthropic(FakeStream(["Ho", "la"], final()))
    provider = ClaudeProvider(None, "claude-haiku-5-5", client=client)
    events = list(
        provider.stream_chat(
            [{"role": "system", "content": "Eres Tama."}, {"role": "user", "content": "hola"}]
        )
    )

    assert events == [Text("Ho"), Text("la"), Done(40, 7)]
    assert client.params["system"] == "Eres Tama."
    assert client.params["model"] == "claude-haiku-5-5"
    assert "temperature" not in client.params


def test_claude_provider_reports_tool_calls():
    block = SimpleNamespace(type="tool_use", name="get_time", input={}, id="toolu_0")
    provider = ClaudeProvider(
        None, "m", client=FakeAnthropic(FakeStream([], final("tool_use", [block])))
    )
    events = list(provider.stream_chat([{"role": "user", "content": "¿qué hora es?"}]))
    assert events[0] == ToolCall("get_time", {}, "toolu_0")


def test_claude_refusal_is_an_error():
    provider = ClaudeProvider(None, "m", client=FakeAnthropic(FakeStream([], final("refusal"))))
    assert isinstance(list(provider.stream_chat([{"role": "user", "content": "x"}]))[-1], Error)


def test_claude_api_error_becomes_error_event():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    provider = ClaudeProvider(None, "m", client=FakeAnthropic(error=error))
    events = list(provider.stream_chat([{"role": "user", "content": "x"}]))
    assert len(events) == 1 and isinstance(events[0], Error)


def test_claude_without_key_is_unavailable():
    assert "ANTHROPIC_API_KEY" in ClaudeProvider(None, "m").unavailable_reason()


def test_claude_uses_low_effort_and_never_sends_sampling_params():
    client = FakeAnthropic(FakeStream(["ok"], final()))
    list(
        ClaudeProvider(None, "claude-haiku-5-5", client=client).stream_chat(
            [{"role": "user", "content": "hola"}]
        )
    )
    assert client.params["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k"} & client.params.keys()


def test_claude_effort_is_configurable():
    client = FakeAnthropic(FakeStream(["ok"], final()))
    provider = ClaudeProvider(None, "claude-haiku-5-5", client=client, effort="medium")
    list(provider.stream_chat([{"role": "user", "content": "hola"}]))
    assert client.params["output_config"] == {"effort": "medium"}


def test_claude_conversion_of_tool_turns():
    _, turns = to_claude_messages(
        [
            {"role": "system", "content": "Eres Tama."},
            {"role": "user", "content": "¿qué hora es?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "function": {"name": "get_time", "arguments": {}}}],
            },
            {"role": "tool", "tool_call_id": "t1", "tool_name": "get_time", "content": "22:53"},
        ]
    )
    assert turns[1] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t1", "name": "get_time", "input": {}}],
    }
    assert turns[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "22:53"}],
    }


def test_claude_tool_call_keeps_id():
    block = SimpleNamespace(type="tool_use", name="get_time", input={}, id="toolu_1")
    provider = ClaudeProvider(
        None, "m", client=FakeAnthropic(FakeStream([], final("tool_use", [block])))
    )
    events = list(provider.stream_chat([{"role": "user", "content": "x"}]))
    assert events[0] == ToolCall("get_time", {}, "toolu_1")
