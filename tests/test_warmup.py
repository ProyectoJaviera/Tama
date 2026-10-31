import json
import threading
from functools import partial

import httpx

from tama.net import Connectivity as TolerantConnectivity
from tama.net import watch
from tama.ollama import OllamaClient, OllamaError
from tama.warmup import LocalWarmer
from tests.fakes import FakeClock

Connectivity = partial(TolerantConnectivity, misses_to_offline=1)


class FakeLoader:
    def __init__(self, fail: bool = False) -> None:
        self.loads: list[tuple[str, dict]] = []
        self.fail = fail
        self.done = threading.Event()

        self.chats: list[dict] = []

    def load(self, model, options=None, keep_alive="30m"):
        self.loads.append((model, options))
        self.done.set()
        if self.fail:
            raise OllamaError("caído")

    def chat(self, model, messages, **kw):
        self.chats.append({"model": model, "messages": messages, **kw})
        self.done.set()


def test_going_offline_triggers_one_preload():
    states = iter([True, False, False, True, False])
    calls = []
    connectivity = Connectivity(
        lambda: next(states), ttl=0, clock=FakeClock(), on_offline=lambda: calls.append(1)
    )
    for _ in range(5):
        connectivity.online()
    # Dos cortes (True→False dos veces), no una precarga por cada chequeo sin red.
    assert len(calls) == 2


def test_starting_offline_does_not_count_as_a_cut():
    calls = []
    connectivity = Connectivity(lambda: False, ttl=0, on_offline=lambda: calls.append(1))
    connectivity.online()
    assert calls == []


def test_warmer_loads_with_chat_options_in_background():
    loader = FakeLoader()
    warmer = LocalWarmer(loader, "qwen", {"num_ctx": 8192, "num_gpu": 0}, clock=FakeClock())
    assert warmer.warm() is True
    assert loader.done.wait(2)
    assert loader.loads == [("qwen", {"num_ctx": 8192, "num_gpu": 0})]


def test_warmer_is_rate_limited():
    clock, loader = FakeClock(), FakeLoader()
    warmer = LocalWarmer(loader, "qwen", {}, min_interval=60, clock=clock)
    assert warmer.warm() is True
    warmer.thread.join(2)
    assert warmer.warm() is False
    clock.now += 60
    assert warmer.warm() is True


def test_warmer_survives_ollama_errors():
    loader = FakeLoader(fail=True)
    warmer = LocalWarmer(loader, "qwen", {}, clock=FakeClock())
    warmer.warm()
    warmer.thread.join(2)
    assert loader.loads  # lo intentó y el error no se propagó


def test_client_load_sends_keep_alive_and_options():
    sent = {}

    def handler(request):
        sent.update(path=request.url.path, body=json.loads(request.content))
        return httpx.Response(200, json={"done": True})

    with OllamaClient("http://ollama.test", transport=httpx.MockTransport(handler)) as client:
        client.load("qwen", {"num_ctx": 8192, "num_gpu": 0})
    assert sent["path"] == "/api/generate"
    assert sent["body"] == {
        "model": "qwen",
        "keep_alive": "30m",
        "options": {"num_ctx": 8192, "num_gpu": 0},
    }


def test_watch_detects_cut_in_background_and_stops():
    online = {"value": True}
    cut = threading.Event()
    connectivity = Connectivity(lambda: online["value"], ttl=30, on_offline=cut.set)
    connectivity.online()
    stop = threading.Event()
    ticks = []
    thread = watch(connectivity, 0.01, stop, on_tick=lambda: ticks.append(1))

    online["value"] = False
    assert cut.wait(2)  # sin que nadie escriba, el vigilante vio el corte
    stop.set()
    thread.join(2)
    assert not thread.is_alive() and ticks


def test_invalidate_keeps_last_state_so_cut_is_detected():
    online = {"value": True}
    cuts = []
    connectivity = Connectivity(
        lambda: online["value"], ttl=30, clock=FakeClock(), on_offline=lambda: cuts.append(1)
    )
    connectivity.online()
    online["value"] = False
    connectivity.invalidate()  # p. ej., tras un error de red en la capa nube
    assert connectivity.online() is False
    assert cuts == [1]


def test_warmer_with_identity_caches_the_system_prompt():
    loader = FakeLoader()
    warmer = LocalWarmer(
        loader,
        "qwen",
        {"num_ctx": 8192, "num_gpu": 0},
        clock=FakeClock(),
        system_prompt="Eres Tama.",
    )
    warmer.warm()
    warmer.thread.join(2)
    call = loader.chats[0]
    assert call["messages"][0] == {"role": "system", "content": "Eres Tama."}
    assert call["options"] == {"num_ctx": 8192, "num_gpu": 0, "num_predict": 1}
    assert call["keep_alive"] == "30m" and loader.loads == []


def test_warmer_uses_the_next_turn_context_with_tools():
    loader = FakeLoader()
    warmer = LocalWarmer(loader, "qwen", {"num_ctx": 8192}, clock=FakeClock(), system_prompt="x")
    history = [{"role": "system", "content": "Eres Tama."}, {"role": "user", "content": "hola"}]
    specs = [{"type": "function", "function": {"name": "get_current_time"}}]
    warmer.context = lambda: (history, specs)
    warmer.warm()
    warmer.thread.join(2)
    call = loader.chats[0]
    assert call["messages"][:2] == history
    assert call["tools"] == specs


def test_connectivity_rechecks_sooner_while_offline():
    clock, states = FakeClock(), iter([False, True])
    connectivity = Connectivity(lambda: next(states), ttl=30, offline_ttl=10, clock=clock)
    assert connectivity.online() is False
    clock.now += 10  # sin red no se esperan los 30 s para notar que volvió
    assert connectivity.online() is True
