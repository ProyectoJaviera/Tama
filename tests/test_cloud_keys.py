"""Capa nube con varias API keys de Ollama Cloud: orden, cuota, enfriamiento y secretos."""

import json
import logging

import httpx

from tama.cascade import Cascade, Layer
from tama.config import Settings
from tama.ollama import OllamaClient
from tama.providers import Done, Error, Text
from tama.providers.cloud_keys import KeyRing, OllamaCloudKeys
from tests.fakes import FakeConnectivity

KEYS = ["sk-uno-secreto", "sk-dos-secreto", "sk-tres-secreto"]
OK_BODY = "\n".join(
    json.dumps(c)
    for c in (
        {"message": {"content": "Hola"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 3, "eval_count": 1},
    )
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def clients(answers: dict[str, list], seen: list) -> list[OllamaClient]:
    """Un cliente por key; cada uno responde según la lista de su key (status o "ok")."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        seen.append((key, json.loads(request.content)["model"]))
        answer = answers[key].pop(0) if answers[key] else "ok"
        if answer == "ok":
            return httpx.Response(200, content=OK_BODY)
        if answer == "red":
            raise httpx.ConnectError("sin red")
        headers = {"retry-after": "120"} if answer == "429-espera" else {}
        status = 429 if answer == "429-espera" else answer
        return httpx.Response(status, json={"error": "limit reached"}, headers=headers)

    transport = httpx.MockTransport(handler)
    return [OllamaClient("https://ollama.com", api_key=key, transport=transport) for key in KEYS]


def provider(tmp_path, answers, seen, clock=None, only=0):
    ring = KeyRing(3, tmp_path / "nube_keys.json", 3600, clock or Clock())
    return OllamaCloudKeys(clients(answers, seen), "gemma4:31b-cloud", ring, only)


def run(cloud):
    return list(cloud.stream_chat([{"role": "user", "content": "hola"}]))


def test_uses_key_1_directly_on_ollama_com_without_the_cloud_suffix(tmp_path):
    seen = []
    events = run(provider(tmp_path, {k: [] for k in KEYS}, seen))
    assert seen == [("sk-uno-secreto", "gemma4:31b")]
    assert isinstance(events[0], Text) and isinstance(events[-1], Done)


def test_an_exhausted_key_moves_to_the_next_in_the_same_request(tmp_path):
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: [429], KEYS[1]: [], KEYS[2]: []}, seen)
    events = run(cloud)
    assert [k for k, _ in seen] == ["sk-uno-secreto", "sk-dos-secreto"]
    assert isinstance(events[-1], Done)
    assert cloud.keys_status()[:2] == [cloud.keys_status()[0], "key 2: activa"]
    assert cloud.keys_status()[0].startswith("key 1: agotada hasta")


def test_all_keys_exhausted_at_once_go_down_to_the_next_layer_quickly(tmp_path):
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: [429], KEYS[1]: [402], KEYS[2]: [429]}, seen)
    events = run(cloud)
    assert len(seen) == 3 and isinstance(events[-1], Error)
    assert cloud.unavailable_reason().startswith("ninguna API key")

    class Claude:
        name, model, needs_network = "claude", "haiku", True

        def unavailable_reason(self):
            return None

        def stream_chat(self, messages, tools=None):
            yield Text("respondo yo")
            yield Done(1, 1)

    cascade = Cascade([Layer(cloud, 8.0), Layer(Claude(), 8.0)], FakeConnectivity())
    answer = list(cascade.turn([{"role": "user", "content": "hola"}]))
    assert any(isinstance(e, Text) and e.text == "respondo yo" for e in answer)
    assert len(seen) == 3  # las agotadas no se vuelven a probar


def test_a_rejected_key_is_set_aside_too(tmp_path):
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: [401], KEYS[1]: [], KEYS[2]: []}, seen)
    run(cloud)
    assert cloud.keys_status()[0].startswith("key 1: inválida hasta")


def test_network_errors_and_5xx_do_not_mark_the_key(tmp_path):
    for failure in ("red", 503):
        seen = []
        cloud = provider(
            tmp_path / str(failure), {KEYS[0]: [failure], KEYS[1]: [], KEYS[2]: []}, seen
        )
        events = run(cloud)
        assert len(seen) == 1 and isinstance(events[-1], Error)
        assert cloud.keys_status() == ["key 1: activa", "key 2: activa", "key 3: activa"]


def test_after_the_cooldown_key_1_is_preferred_again(tmp_path):
    clock = Clock()
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: [429], KEYS[1]: [], KEYS[2]: []}, seen, clock)
    run(cloud)
    run(cloud)
    assert [k for k, _ in seen] == ["sk-uno-secreto", "sk-dos-secreto", "sk-dos-secreto"]
    clock.now += 3601
    run(cloud)
    assert seen[-1][0] == "sk-uno-secreto"
    assert cloud.keys_status()[0] == "key 1: activa"


def test_retry_after_from_the_server_sets_the_wait(tmp_path):
    clock = Clock()
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: ["429-espera"], KEYS[1]: [], KEYS[2]: []}, seen, clock)
    run(cloud)
    clock.now += 121
    assert cloud.ring.usable() == [1, 2, 3]


def test_the_state_survives_a_restart(tmp_path):
    clock = Clock()
    seen = []
    run(provider(tmp_path, {KEYS[0]: [429], KEYS[1]: [], KEYS[2]: []}, seen, clock))
    again = provider(tmp_path, {k: [] for k in KEYS}, seen, clock)
    assert again.ring.usable() == [2, 3]
    saved = (tmp_path / "nube_keys.json").read_text(encoding="utf-8")
    assert not any(key in saved for key in KEYS)


def test_keys_never_show_in_errors_status_or_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: [429], KEYS[1]: [401], KEYS[2]: ["red"]}, seen)
    text = " ".join(str(e) for e in run(cloud)) + " ".join(cloud.keys_status())
    assert not any(key in text or key in caplog.text for key in KEYS)


def test_evals_can_pin_one_key(tmp_path):
    seen = []
    cloud = provider(tmp_path, {k: [] for k in KEYS}, seen, only=3)
    run(cloud)
    assert seen == [("sk-tres-secreto", "gemma4:31b")]
    cloud = provider(tmp_path / "b", {KEYS[2]: [429], KEYS[0]: [], KEYS[1]: []}, seen, only=3)
    run(cloud)
    assert cloud.unavailable_reason().startswith("ninguna API key")


def test_settings_read_three_keys_and_the_old_name_as_key_1(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "vieja")
    monkeypatch.setenv("OLLAMA_API_KEY_2", "dos")
    monkeypatch.setenv("TAMA_OLLAMA_API_KEY_3", "tres")
    assert Settings(_env_file=None).cloud_keys() == ["vieja", "dos", "tres"]
    monkeypatch.setenv("OLLAMA_API_KEY_1", "uno")
    assert Settings(_env_file=None).cloud_keys() == ["uno", "dos", "tres"]


def test_without_keys_the_layer_is_off():
    ring = KeyRing(0, None)
    cloud = OllamaCloudKeys([], "gemma4:31b-cloud", ring)
    assert cloud.unavailable_reason().startswith("sin API keys")
    assert cloud.keys_status() == ["sin API keys configuradas"]


def test_no_credits_is_shown_as_such_and_said_in_the_console(tmp_path, capsys):
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: [402], KEYS[1]: [], KEYS[2]: []}, seen)
    run(cloud)
    assert cloud.keys_status()[0].startswith("key 1: sin saldo hasta")
    console = capsys.readouterr().err
    assert "key 1 sin saldo (error 402" in console and "usando la key 2" in console
    assert not any(key in console for key in KEYS)


def test_an_invalid_key_waits_five_minutes_not_an_hour(tmp_path):
    clock = Clock()
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: [401], KEYS[1]: [], KEYS[2]: []}, seen, clock)
    run(cloud)
    clock.now += 301
    assert cloud.ring.usable() == [1, 2, 3]


def test_model_not_found_is_not_blamed_on_the_key(tmp_path):
    seen = []
    cloud = provider(tmp_path, {KEYS[0]: [404], KEYS[1]: [], KEYS[2]: []}, seen)
    events = run(cloud)
    assert len(seen) == 1 and "modelo no encontrado" in str(events[-1])
    assert cloud.keys_status()[0] == "key 1: activa"


def test_retry_command_clears_the_marks(tmp_path, monkeypatch, capsys):
    import time

    from tama import cli

    seen = []  # con el reloj real: el comando lee el estado guardado con la hora de verdad
    run(provider(tmp_path, {KEYS[0]: [402], KEYS[1]: [402], KEYS[2]: [402]}, seen, time.time))
    monkeypatch.setenv("OLLAMA_API_KEY_1", "a")
    monkeypatch.setenv("OLLAMA_API_KEY_2", "b")
    monkeypatch.setenv("OLLAMA_API_KEY_3", "c")
    settings = Settings(_env_file=None, data_dir=tmp_path)
    cli.cloud_command(settings, [])
    assert "sin saldo" in capsys.readouterr().out
    cli.cloud_command(settings, ["reintentar"])
    assert KeyRing(3, tmp_path / "nube_keys.json").usable() == [1, 2, 3]


def test_startup_line_lists_each_key_by_number(tmp_path):
    seen = []
    cloud = provider(tmp_path, {k: [] for k in KEYS}, seen, only=3)
    assert (
        cloud.startup_line()
        == "3 keys (solo la key 3): key 1: activa; key 2: activa; key 3: activa"
    )
