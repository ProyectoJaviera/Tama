import json
import threading
from datetime import UTC, date, datetime, timedelta

import pytest

from tama.events import EventStore
from tama.mcp_servers import memoria
from tama.ollama import OllamaError
from tama.reflection import (
    Cancelled,
    Reflector,
    Thinker,
    diary_path,
    pending_diaries,
    reflect,
    retire_contradictions,
    unify_entities,
    write_diary,
)


@pytest.fixture(autouse=True)
def memory_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))


class FakeClient:
    """Devuelve las respuestas en orden, en dos fragmentos."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def stream_raw(self, model, messages, think=None, options=None, format=None):
        self.prompts.append(messages[-1]["content"])
        text = self.answers.pop(0)
        if not isinstance(text, str):
            text = json.dumps(text)
        yield {"message": {"content": text[:1]}}
        yield {"message": {"content": text[1:]}, "done": True}


def thinker(*answers):
    client = FakeClient(*answers)
    return Thinker(client, "m", {}, threading.Event()), client


def entity(name, type_="persona", *obs):
    return {"name": name, "entityType": type_, "observations": list(obs)}


def seed_duplicates():
    memoria.create_entities([entity("Bruno", "mascota", "Es un perro")])
    memoria.create_entities([entity("Brunno", "perro", "Tiene 3 años")])


def test_merge_moves_everything_to_the_kept_entity():
    memoria.create_entities([entity("Taller", "lugar", "Queda en Maipú")])
    seed_duplicates()
    memoria.create_relations([{"from": "Brunno", "to": "Taller", "relationType": "duerme en"}])
    memoria.create_goal("Adiestrar", entidades=["Brunno"])

    assert memoria.merge_entities("Bruno", "Brunno")
    text = memoria.search_nodes("Brunno")
    assert "Bruno (mascota)" in text
    assert "Tiene 3 años" in text and "Es un perro" in text
    assert "Bruno duerme en Taller" in text
    assert "Relacionado con: Bruno" in memoria.list_goals()


def test_unify_merges_only_what_the_model_confirms():
    seed_duplicates()
    think, _ = thinker({"decision": "misma", "conservar": "Bruno"})
    assert unify_entities(think) == ["fusioné Bruno y Brunno en Bruno"]
    assert memoria.merge_candidates() == []


def test_unify_keeps_distinct_entities_and_does_not_ask_twice():
    seed_duplicates()
    think, _ = thinker({"decision": "distintas", "conservar": ""})
    assert unify_entities(think) == []
    assert "Brunno" in memoria.search_nodes("Brunno")
    assert memoria.merge_candidates() == []


def test_doubt_leaves_a_question_and_changes_nothing():
    seed_duplicates()
    think, _ = thinker({"decision": "dudo", "conservar": ""})
    unify_entities(think)
    assert "Bruno y Brunno" in memoria.next_suggestion()
    assert "Brunno (perro)" in memoria.search_nodes("Brunno")


def test_invalid_answer_is_retried_later():
    seed_duplicates()
    think, _ = thinker("esto no es json")
    assert unify_entities(think) == []
    assert len(memoria.merge_candidates()) == 1


def test_contradiction_is_marked_not_deleted():
    memoria.create_entities([entity("Vale", "persona", "Vive en Talca", "Vive en Temuco")])
    think, client = thinker({"contradicciones": [{"antiguo": 1, "nuevo": 2}]})
    assert retire_contradictions(think) == ["Vale: 1 datos ya no vigentes"]
    assert "1." in client.prompts[0]
    assert "Vive en Talca" not in memoria.search_nodes("Vale")
    with memoria.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_newest_observation_is_never_retired():
    memoria.create_entities([entity("Vale", "persona", "Vive en Talca", "Vive en Temuco")])
    think, _ = thinker(
        {"contradicciones": [{"antiguo": 2, "nuevo": 1}, {"antiguo": 9, "nuevo": 10}]}
    )
    assert retire_contradictions(think) == []
    assert "Vive en Temuco" in memoria.search_nodes("Vale")


def test_reviewed_entity_is_not_read_again_without_news():
    memoria.create_entities([entity("Vale", "persona", "Vive en Talca", "Toca piano")])
    think, client = thinker({"contradicciones": []})
    retire_contradictions(think)
    retire_contradictions(think)
    assert len(client.prompts) == 1


def test_reflect_logs_events_and_stops_at_two_suggestions(tmp_path):
    store = EventStore(":memory:")
    memoria.create_goal("Publicar libro")
    answers = {"sugerencias": ["uno", "dos", "tres"]}
    reflect(thinker(answers)[0], store, tmp_path)
    assert memoria.pending_suggestions() == 2
    assert [r["content"] for r in store.rows("reflection")] == [
        "sugerencia: uno",
        "sugerencia: dos",
    ]


def test_cancel_stops_the_model_and_keeps_what_is_saved():
    seed_duplicates()
    think, _ = thinker({"decision": "misma", "conservar": "Bruno"})
    think.cancel.set()
    with pytest.raises(Cancelled):
        unify_entities(think)
    assert "Brunno (perro)" in memoria.search_nodes("Brunno")


def test_diary_uses_the_day_log(tmp_path):
    store = EventStore(":memory:")
    store.log("user_msg", "Hoy fui al taller")
    today = date.today()
    think, client = thinker("Conversamos del taller.")
    path = write_diary(think, store, tmp_path, today)
    assert path == diary_path(tmp_path, today)
    assert "Conversamos del taller." in path.read_text(encoding="utf-8")
    assert "Hoy fui al taller" in client.prompts[0]


def test_no_messages_no_diary(tmp_path):
    think, client = thinker()
    assert write_diary(think, EventStore(":memory:"), tmp_path, date.today()) is None
    assert client.prompts == []


def test_pending_diaries_skip_today_and_written_days(tmp_path):
    now = datetime.now(UTC)
    moments = iter([now - timedelta(days=2), now - timedelta(days=1), now])
    store = EventStore(":memory:", now=lambda: next(moments))
    for _ in range(3):
        store.log("user_msg", "hola")
    today = now.astimezone().date()
    written = diary_path(tmp_path, today - timedelta(days=1))
    written.parent.mkdir()
    written.write_text("listo", encoding="utf-8")
    assert pending_diaries(store, tmp_path, today) == [today - timedelta(days=2)]


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def reflector(work, ram=True):
    clock = Clock()
    return Reflector(work, 100, lambda: ram, clock), clock


def wait(item):
    if item._job is not None:
        item._job.join(5)


def test_runs_only_after_idle_and_once():
    runs = []
    item, clock = reflector(lambda cancel: runs.append(1))
    item.tick()
    assert runs == []
    clock.now = 100
    item.tick()
    wait(item)
    item.tick()
    wait(item)
    assert runs == [1]


def test_activity_restarts_the_wait_and_allows_another_run():
    runs = []
    item, clock = reflector(lambda cancel: runs.append(1))
    clock.now = 100
    item.tick()
    wait(item)
    clock.now = 150
    item.activity()
    clock.now = 200
    item.tick()
    assert len(runs) == 1
    clock.now = 250
    item.tick()
    wait(item)
    assert len(runs) == 2


def test_no_run_without_ram():
    runs = []
    item, clock = reflector(lambda cancel: runs.append(1), ram=False)
    clock.now = 100
    item.tick()
    assert item._job is None


def test_activity_cancels_a_running_job():
    started, seen = threading.Event(), []

    def work(cancel):
        started.set()
        seen.append(cancel.wait(5))
        raise Cancelled

    item, clock = reflector(work)
    clock.now = 100
    item.tick()
    assert started.wait(5)
    item.activity()
    wait(item)
    assert seen == [True]


def test_ollama_failure_does_not_kill_the_thread():
    def work(cancel):
        raise OllamaError("caído")

    item, clock = reflector(work)
    clock.now = 100
    item.tick()
    wait(item)
    assert not item.busy
