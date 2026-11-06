import json

import httpx
import pytest

from tama.ollama import OllamaClient, OllamaError


def ndjson(*chunks: dict) -> bytes:
    return "\n".join(json.dumps(c) for c in chunks).encode()


def make_client(handler) -> OllamaClient:
    return OllamaClient("http://ollama.test", transport=httpx.MockTransport(handler))


def test_stream_chat_yields_content_chunks():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        body = ndjson(
            {"message": {"role": "assistant", "content": "Ho"}, "done": False},
            {"message": {"role": "assistant", "content": "la"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True},
        )
        return httpx.Response(200, content=body)

    with make_client(handler) as client:
        chunks = list(client.stream_chat("m", [{"role": "user", "content": "hola"}], think=False))

    assert chunks == ["Ho", "la"]
    assert sent["stream"] is True
    assert sent["think"] is False


def test_stream_chat_omits_think_when_none():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, content=ndjson({"done": True}))

    with make_client(handler) as client:
        list(client.stream_chat("m", []))

    assert "think" not in sent


def test_http_error_becomes_ollama_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'x' not found"})

    with make_client(handler) as client, pytest.raises(OllamaError, match="not found"):
        list(client.stream_chat("x", []))


def test_connect_error_becomes_ollama_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with make_client(handler) as client, pytest.raises(OllamaError, match="no se pudo hablar"):
        client.list_models()


def test_chat_sends_tools():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"}})

    tools = [{"type": "function", "function": {"name": "get_time"}}]
    with make_client(handler) as client:
        result = client.chat("m", [], tools=tools)

    assert sent["tools"] == tools
    assert sent["stream"] is False
    assert result["message"]["content"] == "ok"


def test_stream_raw_sends_the_json_format():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, text='{"message": {"content": "{}"}, "done": true}\n')

    client = OllamaClient(transport=httpx.MockTransport(handler))
    list(client.stream_raw("m", [], format={"type": "object"}))
    assert seen["format"] == {"type": "object"}


def test_client_keep_alive_goes_in_every_local_request():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if sent[-1].get("stream"):
            return httpx.Response(200, content=ndjson({"message": {"content": "x"}, "done": True}))
        return httpx.Response(200, json={"message": {"content": "x"}})

    transport = httpx.MockTransport(handler)
    with OllamaClient("http://ollama.test", transport=transport, keep_alive=-1) as client:
        list(client.stream_chat("m", [{"role": "user", "content": "hola"}]))
        client.chat("m", [{"role": "user", "content": "hola"}])
        client.chat("m", [], keep_alive="30m")
        client.load("m")
    assert [s["keep_alive"] for s in sent] == [-1, -1, "30m", -1]


def test_client_without_keep_alive_lets_ollama_decide():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "x"}})

    with make_client(handler) as client:
        client.chat("m", [])
    assert "keep_alive" not in sent
