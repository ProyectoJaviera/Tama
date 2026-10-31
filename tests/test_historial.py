from datetime import date

import pytest

from tama import cli
from tama.mcp_servers import memoria

TODAY = date(2026, 10, 31)


@pytest.fixture(autouse=True)
def memory_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))


def entity(name, type_, *obs):
    return {"name": name, "entityType": type_, "observations": list(obs)}


def valid(content):
    with memoria.connect() as db:
        row = db.execute("SELECT valid FROM observations WHERE content = ?", (content,)).fetchone()
    return row and row[0]


def first_id():
    with memoria.connect() as db:
        return f"o{db.execute('SELECT MAX(id) FROM observations').fetchone()[0]}"


@pytest.mark.parametrize(
    ("text", "expected", "when"),
    [
        ("Almuerza mañana", "Almuerza el 2026-11-01", "2026-11-01"),
        ("Fue ayer al médico", "Fue el 2026-10-30 al médico", "2026-10-30"),
        ("Fui hace tres días", "Fui el 2026-10-28", "2026-10-28"),
        ("Corre en la mañana", "Corre en la mañana", ""),
        ("El 3 de marzo de 2026 fue", "El 3 de marzo de 2026 fue", "2026-03-03"),
        ("Pasado mañana viaja", "el 2026-11-02 viaja", "2026-11-02"),
    ],
)
def test_dates_become_absolute(text, expected, when):
    assert memoria.resolve_dates(text, TODAY) == (expected, when)


def test_same_fact_with_relative_and_absolute_date_is_one():
    memoria.create_entities([entity("Ana", "persona", "Almuerza con Ana el 2026-11-01")])
    memoria.create_entities(
        [entity("Ana", "persona", "Almuerza con Ana el 1 de noviembre de 2026")]
    )
    with memoria.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_other_date_is_another_fact():
    memoria.create_entities([entity("Ana", "persona", "Almuerza con Ana el 2026-11-01")])
    memoria.create_entities([entity("Ana", "persona", "Almuerza con Ana el 2026-11-02")])
    with memoria.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_past_fact_is_an_event_that_survives_a_correction():
    memoria.create_entities([entity("Renzo", "persona", "El 3 de marzo de 2020 se operó")])
    text = memoria.update_observation([first_id()], "Ya no le duele")
    assert "eventos" in text
    assert valid("El 3 de marzo de 2020 se operó") == 1


def test_health_facts_are_events_that_stay_valid_and_in_order():
    memoria.create_entities([entity("Renzo", "persona", "Me duele mucho el brazo")])
    first = first_id()
    memoria.create_entities([entity("Renzo", "persona", "Ya no me duele el brazo")])
    memoria.update_observation([first], "Ya no me duele el brazo")
    assert valid("Me duele mucho el brazo") == 1
    assert valid("Ya no me duele el brazo") == 1
    lines = memoria.linea_de_tiempo("salud brazo").splitlines()
    assert "Me duele mucho" in lines[0] and "Ya no me duele" in lines[1]
    with memoria.connect() as db:
        kinds = {r[0] for r in db.execute("SELECT kind FROM observations")}
    assert kinds == {"evento"}


def test_observations_sent_as_plain_text_stay_whole():
    memoria.create_entities(
        [{"name": "Ana", "entityType": "persona", "observations": "Toma té verde"}]
    )
    memoria.add_observations([{"entityName": "Ana", "contents": "Vive en Talca"}])
    assert valid("Toma té verde") == 1 and valid("Vive en Talca") == 1


def test_new_facts_are_events_by_default():
    memoria.create_entities([entity("Halloween", "plan", "Irá de Shrek")])
    with memoria.connect() as db:
        assert db.execute("SELECT kind FROM observations").fetchone()[0] == "evento"


def test_state_is_replaced_and_shown_as_before():
    memoria.create_entities([entity("Halloween", "plan", "Irá de Shrek")])
    memoria.update_observation([first_id()], "Irá de piratas")
    memoria.update_observation([first_id()], "Irá de vaqueros")
    text = memoria.search_nodes("Halloween")
    assert "Irá de vaqueros" in text
    assert "antes: Irá de piratas" in text and "Irá de Shrek" in text


def test_timeline_keeps_everything_in_order():
    memoria.create_entities([entity("Renzo", "persona", "Se operó el 2026-03-03")])
    memoria.create_entities([entity("Renzo", "persona", "Tuvo alergia el 2026-01-10")])
    memoria.create_entities([entity("Halloween", "plan", "Irá de Shrek")])
    memoria.update_observation([first_id()], "Irá de piratas")
    lines = memoria.linea_de_tiempo().splitlines()
    assert "Tuvo alergia" in lines[0] and "Se operó" in lines[1]
    assert any("Irá de Shrek" in line and "reemplazado por" in line for line in lines)


def test_timeline_filters_by_topic_and_dates():
    memoria.create_entities([entity("Renzo", "persona", "Se operó la rodilla el 2026-03-03")])
    memoria.create_entities([entity("Renzo", "persona", "Compró un libro el 2026-05-03")])
    assert "libro" not in memoria.linea_de_tiempo("rodilla")
    assert "rodilla" not in memoria.linea_de_tiempo(desde="2026-04-01")


def test_timeline_without_topic_match_gives_everything():
    memoria.create_entities([entity("Renzo", "persona", "Se operó la rodilla")])
    assert "rodilla" in memoria.linea_de_tiempo("historial médico")


def test_one_off_plan_goes_to_the_people_it_names():
    memoria.ensure_owner("Renzo")
    memoria.create_entities([entity("esposa de Renzo", "persona", "Toma té")])
    memoria.create_entities([entity("Almuerzo con esposa", "plan", "Almuerzan juntos")])
    assert "Almuerzo con esposa" not in memoria.search_nodes("")
    assert "Almuerzan juntos" in memoria.search_nodes("esposa")
    assert "Almuerzan juntos" in memoria.search_nodes("Renzo")


def test_entity_without_valid_facts_is_hidden():
    memoria.create_entities([entity("Trabajo", "proyecto", "Reunión pesada")])
    memoria.create_entities([entity("Ana", "persona", "Toma té")])
    memoria.delete_observations([{"entityName": "Trabajo", "observations": ["Reunión pesada"]}])
    assert "Trabajo" not in memoria.search_nodes("")


class Tools:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        return type("Outcome", (), {"ok": True, "text": "listo"})()


class Brain:
    def __init__(self):
        self.tools = Tools()


def test_memoria_historial_command():
    brain = Brain()
    cli.show_memory(brain, "historial salud")
    assert brain.tools.calls == [("linea_de_tiempo", {"tema": "salud"})]
