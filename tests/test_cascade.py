from tama.cascade import (
    Answered,
    Cascade,
    CircuitBreaker,
    Layer,
    Switch,
    claude_budget_gate,
)
from tama.providers import Error, Text
from tests.fakes import FakeClock, FakeConnectivity, FakeProvider, failing, mid_stream_failure


def run(cascade: Cascade) -> list:
    return list(cascade.turn([{"role": "user", "content": "hola"}]))


def texts(events) -> str:
    return "".join(e.text for e in events if isinstance(e, Text))


def test_first_available_layer_answers():
    cloud, local = FakeProvider("nube", needs_network=True), FakeProvider("local")
    events = run(Cascade([Layer(cloud), Layer(local)], FakeConnectivity()))

    assert texts(events) == "hola desde nube"
    answered = events[-1]
    assert isinstance(answered, Answered)
    assert (answered.provider, answered.input_tokens, answered.output_tokens) == ("nube", 10, 5)
    assert local.calls == []


def test_error_before_text_falls_back_and_announces_switch():
    cloud, local = failing("nube"), FakeProvider("local")
    connectivity = FakeConnectivity()
    events = run(Cascade([Layer(cloud), Layer(local)], connectivity))

    switch = next(e for e in events if isinstance(e, Switch))
    assert (switch.frm, switch.to, switch.partial) == ("nube", "local", False)
    assert texts(events) == "hola desde local"
    assert connectivity.invalidated == 1  # un fallo de red fuerza un nuevo chequeo


def test_mid_stream_failure_retries_whole_turn_on_next_layer():
    cloud, local = mid_stream_failure("nube"), FakeProvider("local")
    events = run(Cascade([Layer(cloud), Layer(local)], FakeConnectivity()))

    switch_at = next(i for i, e in enumerate(events) if isinstance(e, Switch))
    assert events[switch_at].partial is True
    assert texts(events[switch_at:]) == "hola desde local"
    assert local.calls[0] == cloud.calls[0]  # mismo turno completo


def test_offline_skips_network_layers_without_calling_them():
    cloud, claude, local = (
        FakeProvider("nube", needs_network=True),
        FakeProvider("claude", needs_network=True),
        FakeProvider("local"),
    )
    events = run(Cascade([Layer(cloud), Layer(claude), Layer(local)], FakeConnectivity(False)))

    assert texts(events) == "hola desde local"
    assert cloud.calls == [] and claude.calls == []
    assert not any(isinstance(e, Switch) for e in events)  # saltar no es un fallo


def test_layer_without_api_key_is_skipped():
    cloud = FakeProvider("nube", needs_network=True, missing="falta OLLAMA_API_KEY")
    local = FakeProvider("local")
    cascade = Cascade([Layer(cloud), Layer(local)], FakeConnectivity())

    assert texts(run(cascade)) == "hola desde local"
    assert cloud.calls == []
    assert cascade.status()[0][1] == "falta OLLAMA_API_KEY"


def test_first_token_timeout_moves_to_next_layer():
    slow = FakeProvider("nube", needs_network=True, delay=0.5)
    local = FakeProvider("local")
    events = run(Cascade([Layer(slow, first_token_timeout=0.05), Layer(local)], FakeConnectivity()))

    switch = next(e for e in events if isinstance(e, Switch))
    assert "sin respuesta" in switch.reason
    assert texts(events) == "hola desde local"


def test_all_layers_fail_yields_error():
    events = run(
        Cascade([Layer(failing("nube")), Layer(failing("local", False))], FakeConnectivity())
    )
    assert isinstance(events[-1], Error)
    assert "ninguna capa" in events[-1].message


def test_breaker_opens_after_three_failures_and_skips_layer():
    clock = FakeClock()
    cloud, local = failing("nube"), FakeProvider("local")
    layer = Layer(cloud, breaker=CircuitBreaker(3, 300, clock))
    cascade = Cascade([layer, Layer(local)], FakeConnectivity())

    for _ in range(3):
        run(cascade)
    assert layer.breaker.state == "abierto"
    run(cascade)
    assert len(cloud.calls) == 3  # el cuarto turno ya no la intenta


def test_breaker_half_open_allows_one_trial():
    clock = FakeClock()
    breaker = CircuitBreaker(3, 300, clock)
    for _ in range(3):
        breaker.failure()
    assert not breaker.allow()

    clock.now += 300
    assert breaker.state == "medio abierto" and breaker.allow()
    breaker.failure()  # el intento de prueba falla: vuelve a abrirse
    assert breaker.state == "abierto"

    clock.now += 300
    breaker.success()
    assert breaker.state == "cerrado"


def test_success_resets_failure_count():
    breaker = CircuitBreaker(3, 300, FakeClock())
    breaker.failure()
    breaker.failure()
    breaker.success()
    breaker.failure()
    breaker.failure()
    assert breaker.state == "cerrado"


def test_budget_gate_disables_claude_over_monthly_cap():
    tokens = {"value": (0, 0)}
    gate = claude_budget_gate(lambda: tokens["value"], 0.10, 0.50, monthly_usd=5.0)
    assert gate() is None

    tokens["value"] = (10_000_000, 8_000_000)  # 1,00 + 4,00 = US$ 5,00
    assert "tope mensual" in gate()


def test_budget_gate_skips_layer_in_cascade():
    claude, local = FakeProvider("claude", needs_network=True), FakeProvider("local")
    gate = claude_budget_gate(lambda: (100_000_000, 0), 0.10, 0.50, 5.0)
    events = run(Cascade([Layer(claude, gate=gate), Layer(local)], FakeConnectivity()))

    assert claude.calls == []
    assert texts(events) == "hola desde local"
