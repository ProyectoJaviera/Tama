import json

import pytest

from evals import runner
from tama.cascade import Cascade, Layer
from tama.providers import Done, Text
from tests.fakes import FakeConnectivity, FakeProvider, failing


def test_there_are_20_valid_scenarios():
    scenarios = runner.load_scenarios()
    assert len(scenarios) == 20
    assert len({s.id for s in scenarios}) == 20
    for s in scenarios:
        assert s.mensaje and s.criterios
        assert s.all_criteria[0] == runner.GENERIC_CRITERION


def test_run_scenario_replays_previous_turns_and_measures_latency():
    local = FakeProvider("local", [Text("Pixel"), Done(5, 1)])
    cascade = Cascade([Layer(local)], FakeConnectivity())
    scenario = runner.Scenario("x", "d", "¿Cómo se llama mi gato?", ["c"], ["Mi gato es Pixel."])

    outcome = runner.run_scenario(cascade, scenario)
    assert outcome.respuesta == "Pixel" and outcome.capa == "local"
    assert outcome.total_s is not None
    roles = [m["role"] for m in local.calls[-1]]
    assert roles == ["system", "user", "assistant", "user"]


def test_scenario_with_failing_cascade_records_error():
    cascade = Cascade([Layer(failing("local", needs_network=False))], FakeConnectivity())
    outcome = runner.run_scenario(cascade, runner.Scenario("x", "d", "hola", ["c"]))
    assert outcome.error and not outcome.respuesta


def test_judgement_is_applied_and_clamped():
    scenario = runner.Scenario("x", "d", "hola", ["breve"])
    outcome = runner.Outcome("x", respuesta="¡Hola!")
    runner.apply_judgement(
        scenario, outcome, lambda prompt: {"puntajes": [5, 7], "comentario": "ok"}
    )
    assert outcome.puntajes == [5, 5] and outcome.promedio == 5


def test_judgement_with_wrong_count_is_an_error():
    scenario = runner.Scenario("x", "d", "hola", ["breve"])
    outcome = runner.Outcome("x", respuesta="¡Hola!")
    runner.apply_judgement(scenario, outcome, lambda prompt: {"puntajes": [5], "comentario": ""})
    assert "puntajes" in outcome.error


def test_judge_prompt_lists_numbered_criteria():
    scenario = runner.Scenario("x", "d", "hola", ["breve", "cálido"])
    prompt = runner.judge_prompt(scenario, runner.Outcome("x", respuesta="¡Hola!"))
    assert "1. " + runner.GENERIC_CRITERION in prompt and "3. cálido" in prompt


@pytest.mark.parametrize(
    ("values", "pct", "expected"),
    [([1, 2, 3, 4, 5], 50, 3), ([1, 2, 3, 4, 5], 95, 5), ([2.0], 95, 2.0), ([], 50, None)],
)
def test_percentile(values, pct, expected):
    assert runner.percentile(values, pct) == expected


def test_report_includes_both_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "EVALS_DIR", tmp_path)
    good = runner.Outcome("saludo", "¡Hola!", "local", "m", 1.0, 2.0, puntajes=[5, 4])
    runner.save_mode("online", "qwen3:4b", [good])
    runner.save_mode("offline", "qwen3:4b", [good])

    report = runner.render_report()
    assert "## Con red" in report and "## Sin red" in report
    assert "| saludo | local | 1.0 s | 2.0 s | 5 4 | 4.5 |" in report
    data = json.loads((tmp_path / "results-online.json").read_text(encoding="utf-8"))
    assert data["resumen"]["aprobados"] == 1


def test_saved_results_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "EVALS_DIR", tmp_path)
    outcome = runner.Outcome("x", "¡Hola!", "local", "m", 0.5, 2.0, error="juez: vacío")
    runner.save_mode("offline", "qwen3:4b", [outcome])
    judge, outcomes = runner.load_mode("offline")
    assert judge == "qwen3:4b" and outcomes == [outcome]


def test_report_shows_layer_modes_and_latency_per_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "EVALS_DIR", tmp_path)
    runner.save_mode("online-claude", "j", [runner.Outcome("a", "x", "claude", "m", 1.5, 2.0)])
    runner.save_mode("online", "j", [runner.Outcome("a", "x", "nube", "m", 0.4, 1.0)])
    report = runner.render_report()
    assert report.index("Con red (cascada completa)") < report.index("Con red, solo claude")
    assert "Primer token en claude: p50 1.5 s, p95 1.5 s" in report


@pytest.mark.parametrize(
    "text",
    [
        '{"puntajes": [5], "comentario": "ok"}',
        '```json\n{"puntajes": [5], "comentario": "ok"}\n```',
        'Aquí va: {"puntajes": [5], "comentario": "ok"} listo',
    ],
)
def test_judge_json_is_parsed_even_inside_code_fences(text):
    assert runner.parse_json_object(text) == {"puntajes": [5], "comentario": "ok"}


def test_judge_text_without_json_is_an_error():
    with pytest.raises(ValueError):
        runner.parse_json_object("no sé")


def test_report_ignores_other_result_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "EVALS_DIR", tmp_path)
    (tmp_path / "results-otra-cosa.json").write_text('{"local": {}}', encoding="utf-8")
    runner.save_mode("online", "j", [runner.Outcome("a", "x", "nube", "m", 0.4, 1.0)])
    assert "Con red (cascada completa)" in runner.render_report()
