from evals import tools
from evals.tools import Expected, Scenario


def test_scenarios_cover_the_required_kinds():
    scenarios = tools.load_scenarios()
    assert len(scenarios) > 20
    assert len({s.id for s in scenarios}) == len(scenarios)
    assert any(s.destructiva for s in scenarios)
    assert any(not s.espera for s in scenarios)  # pedidos sin herramienta
    assert any(len(s.espera) >= 2 for s in scenarios)  # cadenas
    assert {e.herramienta for s in scenarios for e in s.espera} >= {
        "get_current_time",
        "create_note",
        "search_notes",
        "read_file",
    }


def scenario(*expected, **kw):
    return Scenario("x", "m", list(expected), **kw)


def test_expected_tool_with_arguments():
    s = scenario(Expected("read_file", {"path": "plan.md"}))
    assert tools.evaluate(s, [("read_file", {"path": "plan.md"})], [], "") == ""
    assert "faltó" in tools.evaluate(s, [("read_file", {"path": "otro.md"})], [], "")
    assert "faltó" in tools.evaluate(s, [], [], "")


def test_any_argument_wildcard():
    s = scenario(Expected("create_note", {"*": "banco"}))
    assert (
        tools.evaluate(s, [("create_note", {"title": "x", "content": "Banco el lunes"})], [], "")
        == ""
    )


def test_chain_must_follow_order_but_allows_extra_calls():
    s = scenario(Expected("search_notes"), Expected("create_file"))
    ok = [("get_current_time", {}), ("search_notes", {}), ("create_file", {})]
    assert tools.evaluate(s, ok, [], "") == ""
    assert tools.evaluate(s, [("create_file", {}), ("search_notes", {})], [], "")


def test_no_tool_scenario_fails_if_a_tool_is_called():
    assert tools.evaluate(scenario(), [], [], "hola") == ""
    assert "llamó" in tools.evaluate(scenario(), [("get_current_time", {})], [], "")


def test_destructive_requires_confirmation_request():
    s = scenario(Expected("delete_note"), destructiva=True)
    assert tools.evaluate(s, [("delete_note", {"note_id": 1})], ["delete_note"], "") == ""
    assert "confirmación" in tools.evaluate(s, [], [], "¿Seguro?")


def test_answer_must_mention_expected_fact():
    s = scenario(Expected("search_notes"), respuesta="martes")
    assert tools.evaluate(s, [("search_notes", {})], [], "Es el martes a las 9") == ""
    assert "martes" in tools.evaluate(s, [("search_notes", {})], [], "No sé")


def test_seed_creates_notes_and_files(tmp_path):
    tools.seed(tmp_path / "data", tmp_path / "trabajo")
    assert (tmp_path / "data" / "notas.db").exists()
    assert (tmp_path / "trabajo" / "plan.md").read_text(encoding="utf-8").startswith("# Plan")


def test_rejection_must_be_reported_clearly():
    s = scenario(Expected("delete_note"), destructiva=True, rechazo=True)
    called = [("delete_note", {"note_id": 1})]
    assert tools.evaluate(s, called, ["delete_note"], "No la borré, sigue ahí.") == ""
    bad = "Ya mandé la solicitud para borrar la nota. Solo confirma en el sistema."
    assert "no dijo" in tools.evaluate(s, called, ["delete_note"], bad)


def test_voseo_is_counted():
    assert tools.voseo_count(["Si querés, te ayudo", "¿Qué tenés?"]) == 2
    assert tools.voseo_count(["Si quieres, te ayudo", "¿Qué tienes?"]) == 0
