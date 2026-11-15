"""Gasto de Claude contado en el PC: caché, tope, aviso al 80 % y caché de prompt."""

from datetime import UTC, datetime
from types import SimpleNamespace

from tama.providers.claude import ClaudeProvider
from tama.spend import Spend, usage_cost


def usage(inp=0, out=0, write=0, read=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=write,
        cache_read_input_tokens=read,
    )


def test_cost_counts_cache_writes_and_reads():
    # Haiku 5.5: US$ 0,10 entrada y 0,50 salida por millón.
    cost = usage_cost(usage(1000, 200, write=2000, read=10000), 0.10, 0.50)
    expected = (1000 + 2000 * 1.25 + 10000 * 0.1) * 0.10 / 1e6 + 200 * 0.50 / 1e6
    assert abs(cost - expected) < 1e-12


def test_cap_closes_the_layer_and_warns_once_at_80(tmp_path):
    said = []
    clock = lambda: datetime(2026, 11, 12, tzinfo=UTC)  # noqa: E731
    spend = Spend(tmp_path / "gasto.db", 1.0, 1.0, 1.0, clock, said.append)
    spend.add(usage(700_000))
    assert spend.gate() is None and said == []
    spend.add(usage(150_000))
    assert len(said) == 1 and "80 %" in said[0]
    spend.add(usage(10_000))
    assert len(said) == 1  # una sola vez al mes
    spend.add(usage(200_000))
    assert spend.gate().startswith("tope mensual de Claude alcanzado")
    assert spend.status().startswith("US$ 1.06 de US$ 1.00")


def test_a_new_month_starts_at_zero(tmp_path):
    now = [datetime(2026, 11, 30, tzinfo=UTC)]
    spend = Spend(tmp_path / "gasto.db", 1.0, 1.0, 1.0, lambda: now[0], lambda _: None)
    spend.add(usage(2_000_000))
    assert spend.gate()
    now[0] = datetime(2026, 12, 1, tzinfo=UTC)
    assert spend.gate() is None and spend.month_usd() == 0.0


class Stream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        yield SimpleNamespace(type="text", text="Hola")

    def get_final_message(self):
        return self.final


class Client:
    def __init__(self, final):
        self.params = {}
        outer = self

        class Messages:
            def stream(self, **params):
                outer.params = params
                return Stream(final)

        self.messages = Messages()


def test_claude_uses_prompt_caching_and_reports_each_call():
    final = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="Hola")],
        usage=usage(50, 10, write=2400, read=0),
    )
    seen = []
    client = Client(final)
    provider = ClaudeProvider(None, "claude-haiku-5-5", client=client, on_usage=seen.append)
    events = list(provider.stream_chat([{"role": "user", "content": "hola"}]))
    assert client.params["cache_control"] == {"type": "ephemeral"}
    assert seen == [final.usage]
    assert events[-1].input_tokens == 2450  # el prompt completo, con lo que fue a la caché
