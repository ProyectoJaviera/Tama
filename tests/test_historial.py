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


def events_of(timeline):
    """Las líneas de la línea de tiempo, sin la indicación de arriba."""
    return [line for line in timeline.splitlines() if line.startswith("- ")]


def first_id():
    with memoria.connect() as db:
        return f"o{db.execute('SELECT MAX(id) FROM observations').fetchone()[0]}"


@pytest.mark.parametrize(
    ("text", "expected", "when"),
    [
        ("Almuerza mañana", "Almuerza el 1 de noviembre de 2026", "2026-11-01"),
        ("Fue ayer al médico", "Fue el 30 de octubre de 2026 al médico", "2026-10-30"),
        ("Fui hace tres días", "Fui el 28 de octubre de 2026", "2026-10-28"),
        ("Fui la semana pasada", "Fui la semana pasada", ""),
        ("El 2 de agosto me fracturé", "El 2 de agosto me fracturé", "2026-08-02"),
        ("Corre en la mañana", "Corre en la mañana", ""),
        ("El 3 de marzo de 2026 fue", "El 3 de marzo de 2026 fue", "2026-03-03"),
        ("Pasado mañana viaja", "el 2 de noviembre de 2026 viaja", "2026-11-02"),
        ("En mayo tuve alergia", "En mayo tuve alergia", "2026-05-01"),
        ("Me mudé en mayo de 2024", "Me mudé en mayo de 2024", "2024-05-01"),
        ("Viajo en diciembre", "Viajo en diciembre", "2026-12-01"),
        ("El 3 de marzo me operaron", "El 3 de marzo me operaron", "2026-03-03"),
        ("Fue en septiembre", "Fue en septiembre", "2026-09-01"),
        ("El 20 de febrero nací", "El 20 de febrero nací", "2026-02-20"),
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
    assert "error=true" in text
    assert valid("El 3 de marzo de 2020 se operó") == 1


def test_health_facts_are_events_that_stay_valid_and_in_order():
    memoria.create_entities([entity("Renzo", "persona", "Me duele mucho el brazo")])
    first = first_id()
    memoria.create_entities([entity("Renzo", "persona", "Ya no me duele el brazo")])
    memoria.update_observation([first], "Ya no me duele el brazo")
    assert valid("Me duele mucho el brazo") == 1
    assert valid("Ya no me duele el brazo") == 1
    lines = events_of(memoria.linea_de_tiempo("salud brazo"))
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


def test_fact_of_an_event_entity_can_be_replaced():
    memoria.create_entities([entity("Halloween", "evento", "Irá de Shrek")])
    memoria.update_observation([first_id()], "Irá de piratas")
    assert valid("Irá de Shrek") == 0


def test_wrong_event_is_corrected_and_shown_as_such():
    memoria.create_entities([entity("Renzo", "persona", "Fue operado de la rodilla")])
    memoria.update_observation([first_id()], "No lo operaron: la rodilla sanó sola", error=True)
    assert valid("Fue operado de la rodilla") == 0
    text = memoria.linea_de_tiempo("rodilla")
    assert "corregido, no fue así: No lo operaron" in text


def test_plan_with_a_past_date_can_be_replaced():
    memoria.create_entities([entity("Halloween", "plan", "Irá de Shrek el 3 de marzo de 2020")])
    memoria.update_observation([first_id()], "Irá de piratas")
    assert valid("Irá de Shrek el 3 de marzo de 2020") == 0


def test_alias_links_a_name_to_the_existing_person():
    memoria.create_entities([entity("esposa de Renzo", "persona", "Toma té")])
    memoria.create_entities(
        [
            {
                "name": "esposa de Renzo",
                "entityType": "persona",
                "aliases": ["Lizette"],
                "observations": ["Es de Tacna"],
            }
        ]
    )
    memoria.create_entities([entity("Lizette", "persona", "Vota en diciembre")])
    assert "Vota en diciembre" in memoria.search_nodes("esposa de Renzo")
    with memoria.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1


def test_new_facts_are_events_by_default():
    memoria.create_entities([entity("Halloween", "evento", "Fue de Shrek")])
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
    lines = events_of(memoria.linea_de_tiempo())
    assert "Tuvo alergia" in lines[0] and "Se operó" in lines[1]
    assert any("Irá de Shrek" in line and "reemplazado por" in line for line in lines)


def test_timeline_filters_by_topic_and_dates():
    memoria.create_entities([entity("Renzo", "persona", "Se operó la rodilla el 2026-03-03")])
    memoria.create_entities([entity("Renzo", "persona", "Compró un libro el 2026-05-03")])
    assert "libro" not in memoria.linea_de_tiempo("rodilla")
    assert "rodilla" not in memoria.linea_de_tiempo(desde="2026-04-01")


def test_health_topic_keeps_only_health_facts():
    memoria.create_entities([entity("Renzo", "persona", "Se operó la rodilla")])
    memoria.create_entities([entity("Renzo", "persona", "Almuerza con Ana")])
    for topic in ("historial médico", "salud"):
        text = memoria.linea_de_tiempo(topic)
        assert "rodilla" in text and "Almuerza" not in text


def test_operated_counts_as_health():
    memoria.create_entities([entity("Renzo", "persona", "Fue operado de la rodilla")])
    memoria.create_entities([entity("Ana", "persona", "A Ana le operaron la muñeca")])
    assert "muñeca" in memoria.linea_de_tiempo("salud")
    assert "rodilla" in memoria.linea_de_tiempo("salud")


def test_topic_without_matches_returns_nothing():
    memoria.create_entities([entity("Renzo", "persona", "Almuerza con Ana")])
    assert "No hay nada" in memoria.linea_de_tiempo("astronomía")


def test_timeline_follows_the_whole_chain():
    memoria.create_entities([entity("Halloween", "plan", "Se disfrazará de Shrek")])
    memoria.update_observation([first_id()], "Irá de vaqueros")
    memoria.update_observation([first_id()], "Irá de astronautas")
    text = memoria.linea_de_tiempo("disfraz")
    assert "Shrek" in text and "vaqueros" in text and "astronautas (vigente)" in text


def test_timeline_gives_one_event_per_line():
    memoria.create_entities([entity("Renzo", "persona", "Le duele el pie", "Le duele el brazo")])
    assert len(events_of(memoria.linea_de_tiempo("salud"))) == 2


def test_fact_does_not_link_to_a_plan_by_mention():
    memoria.create_entities([entity("Halloween", "plan", "Habrá fiesta")])
    memoria.create_entities([entity("Renzo", "persona", "Sanó antes de Halloween")])
    with memoria.connect() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM observation_entities l JOIN observations o"
            " ON o.id = l.observation_id WHERE o.content = 'Sanó antes de Halloween'"
        ).fetchone()
    assert row[0] == 1


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


def test_memoria_with_a_name_shows_the_whole_card(capsys):
    facts = [f"Dato número {i}" for i in range(8)]
    memoria.create_entities([entity("Renzo", "persona", *facts)])
    cli.show_memory(Brain(), "Renzo")
    assert capsys.readouterr().out.count("Dato número") == 8


def test_open_nodes_shows_all_facts():
    facts = [f"Dato número {i}" for i in range(8)]
    memoria.create_entities([entity("Ana", "persona", *facts)])
    assert memoria.open_nodes(["Ana"]).count("Dato número") == 8


def test_memoria_historial_command():
    brain = Brain()
    cli.show_memory(brain, "historial salud")
    assert brain.tools.calls == [("linea_de_tiempo", {"tema": "salud"})]


def test_timeline_shows_dates_in_words_and_no_invented_date():
    from datetime import timedelta

    memoria.create_entities(
        [
            entity("Renzo", "persona", "Fui al dentista por una muela hace tres días"),
            entity("Renzo", "persona", "Me operaron de la rodilla"),
        ]
    )
    today = memoria.local_now().date()
    dentist = (today - timedelta(days=3)).isoformat()
    timeline = memoria.linea_de_tiempo("Renzo")
    assert memoria.spoken_date(dentist) in timeline and dentist not in timeline
    told = memoria.spoken_date(today.isoformat())
    assert f"fecha no indicada (contado el {told})" in timeline


def test_a_date_sent_apart_is_kept():
    memoria.create_entities(
        [
            {
                "name": "Renzo",
                "entityType": "persona",
                "observations": ["Me operaron de la rodilla"],
                "fecha": "2026-03-03",
            }
        ]
    )
    assert "- 3 de marzo de 2026: [o1] Me operaron de la rodilla el 3 de marzo de 2026" in (
        memoria.linea_de_tiempo("Renzo")
    )


def test_every_undated_fact_says_so_in_the_timeline():
    memoria.create_entities(
        [
            entity("Renzo", "persona", "El 2 de agosto de 2026 me fracturé la muñeca"),
            entity("Renzo", "persona", "Me quebré un dedo del pie jugando fútbol"),
            entity("Renzo", "persona", "Tuve varicela de niño"),
        ]
    )
    timeline = memoria.linea_de_tiempo("Renzo")
    assert timeline.splitlines()[0] == memoria.UNDATED_NOTE
    lines = events_of(timeline)
    undated = [line for line in lines if "fractur" not in line]
    assert len(undated) == 2
    assert all(line.startswith("- fecha no indicada (contado el ") for line in undated)
    dated = next(line for line in lines if "fractur" in line)
    assert dated.startswith("- 2 de agosto de 2026:") and "no indicada" not in dated


def test_timeline_with_only_dated_facts_has_no_note():
    memoria.create_entities([entity("Renzo", "persona", "El 2 de agosto de 2026 me fracturé")])
    assert memoria.UNDATED_NOTE not in memoria.linea_de_tiempo("salud")


def test_search_does_not_pass_the_day_told_as_the_date():
    memoria.create_entities(
        [entity("Renzo", "persona", "Me operaron de la rodilla", "El 3 de marzo de 2026 viajó")]
    )
    text = memoria.search_nodes("rodilla")
    assert "Me operaron de la rodilla (fecha no indicada; contado el" in text
    assert "viajó (contado el" in text


def test_undated_note_is_stripped_when_copied_back():
    copied = "[o3] Me operaron (fecha no indicada; contado el 6 de noviembre de 2026)"
    assert memoria.without_date(copied) == "Me operaron"


def test_plan_entity_saves_plans():
    memoria.create_entities([entity("Halloween", "plan", "Irá de Shrek")])
    with memoria.connect() as db:
        assert db.execute("SELECT kind FROM observations").fetchone()[0] == "plan"
    assert "- plan, fecha no indicada" in memoria.linea_de_tiempo("Shrek")


def test_plan_date_without_year_looks_forward():
    assert memoria.dated(TODAY, None, 3, 3, future=True) == "2027-03-03"
    assert memoria.dated(TODAY, None, 12, 20, future=True) == "2026-12-20"
    assert memoria.dated(TODAY, None, 3, 3) == "2026-03-03"


def test_plan_is_linked_to_renzo_and_says_its_date():
    memoria.ensure_owner("Renzo")
    memoria.create_entities(
        [
            {
                "name": "Fiesta de disfraces",
                "entityType": "plan",
                "observations": ["Renzo se va a disfrazar de Shrek"],
                "fecha": "el 20 de diciembre",
            }
        ]
    )
    assert "Shrek" in memoria.open_nodes(["Renzo"])
    with memoria.connect() as db:
        row = db.execute("SELECT kind, occurred_at FROM observations").fetchone()
    assert row["kind"] == "plan" and row["occurred_at"][5:] == "12-20"
    assert "plan para el 20 de diciembre de" in memoria.linea_de_tiempo("Shrek")
    assert "plan para el 20 de diciembre de" in memoria.search_nodes("Shrek")


def test_saved_plan_without_date_says_so():
    memoria.ensure_owner("Renzo")
    ids, day = memoria.save_plans(["Renzo va a aprender a tejer"])
    assert ids and day == ""
    assert "(plan; contado el" in memoria.search_nodes("tejer")


def test_saved_plan_keeps_a_date_it_cannot_read():
    memoria.ensure_owner("Renzo")
    memoria.save_plans(["Renzo irá a la playa"], cuando="cuando haga calor", con=["Ana"])
    text = memoria.open_nodes(["Ana"])
    assert "Renzo irá a la playa (cuando haga calor)" in text


def test_timeline_says_whose_fact_it_is():
    memoria.ensure_owner("Renzo")
    memoria.create_entities([entity("Lucía", "persona", "Se muda a Temuco en marzo de 2026")])
    memoria.create_entities([entity("Renzo", "persona", "Me fracturé el 2 de agosto de 2026")])
    lines = events_of(memoria.linea_de_tiempo())
    assert any("Lucía: Se muda a Temuco" in line for line in lines)
    assert any("] Me fracturé" in line for line in lines)


def test_plan_on_a_weekday_gets_its_date():
    friday = date(2026, 11, 6)
    text, found = memoria.resolve_dates("Irá a la playa el sábado con Ana", friday, future=True)
    assert (
        found == "2026-11-07" and text == "Irá a la playa el sábado 7 de noviembre de 2026 con Ana"
    )
    assert memoria.resolve_dates("Irá el próximo viernes", friday, future=True)[1] == "2026-11-13"
    assert memoria.resolve_dates("Fue el sábado", friday)[1] == ""  # solo en planes


def plan_and_fact(first_plan: bool):
    plan = entity("Ir a la playa con Frida", "plan", "Renzo irá a la playa con Frida este sábado")
    fact = entity("Renzo", "persona", "Renzo irá a la playa con Frida este sábado")
    memoria.create_entities([plan, fact] if first_plan else [fact, plan], source=7)


@pytest.mark.parametrize("first_plan", [True, False])
def test_same_plan_sent_twice_in_a_turn_is_saved_once_as_a_plan(first_plan):
    memoria.ensure_owner("Renzo")
    memoria.create_entities([entity("Frida", "persona", "Es amiga de Renzo")])
    plan_and_fact(first_plan)
    with memoria.connect() as db:
        rows = db.execute(
            "SELECT kind, content FROM observations WHERE content LIKE '%playa%'"
        ).fetchall()
    assert len(rows) == 1 and rows[0]["kind"] == "plan"
    assert "este sábado" in rows[0]["content"] and "de 2026" in rows[0]["content"]
    for name in ("Renzo", "Frida"):
        assert memoria.open_nodes([name]).count("playa") == 1


def test_facts_with_other_dates_are_not_joined():
    memoria.ensure_owner("Renzo")
    memoria.create_entities(
        [entity("Renzo", "persona", "Irá al cine el 3 de enero de 2027")], source=3
    )
    memoria.create_entities([entity("Cine", "plan", "Irá al cine el 9 de enero de 2027")], source=3)
    with memoria.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_reflection_plan_turns_the_chat_fact_into_the_plan():
    memoria.ensure_owner("Renzo")
    memoria.create_entities(
        [entity("Renzo", "persona", "Renzo irá a la playa con Frida")], source=5
    )
    memoria.save_plans(["Renzo irá a la playa con Frida"], cuando="el sábado", source=5)
    with memoria.connect() as db:
        rows = db.execute("SELECT kind FROM observations").fetchall()
    assert [r["kind"] for r in rows] == ["plan"]


def test_same_plan_with_words_in_another_order_is_one():
    memoria.ensure_owner("Renzo")
    plan = entity("Curso de cerámica", "plan", "Renzo piensa empezar un curso de cerámica en marzo")
    fact = entity("Renzo", "persona", "En marzo piensa empezar un curso de cerámica")
    memoria.create_entities([plan, fact], source=9)
    with memoria.connect() as db:
        rows = db.execute("SELECT kind FROM observations").fetchall()
    assert [r["kind"] for r in rows] == ["plan"]
