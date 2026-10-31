import asyncio
import json
import sqlite3
import sys

import pytest

from tama.events import EventStore
from tama.mcp_hub import McpHub, ServerConfig, server_env
from tama.mcp_servers import memoria
from tama.memory import MAX_PROFILE_CHARS, last_conversation_line, read_profile, session_prompt


@pytest.fixture(autouse=True)
def memory_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAMA_MEMORY_DIR", str(tmp_path / "memoria"))


def entity(name, type_="persona", *obs):
    return {"name": name, "entityType": type_, "observations": list(obs)}


def count(table):
    with memoria.connect() as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_names_are_normalized_without_duplicates():
    memoria.create_entities([entity("Renzo", "persona", "Vive en Santiago")])
    result = memoria.create_entities([entity("RENZO", "persona", "Le gusta el café")])
    assert "Ya existían" in result and count("entities") == 1
    assert memoria.normalize("  Mónica   Núñez ") == "monica nunez"


def test_longer_name_becomes_alias_of_same_type():
    memoria.create_entities([entity("Renzo", "persona", "Imprime en 3D")])
    memoria.create_entities([entity("Renzo Flores", "persona", "Trabaja en software")])
    assert count("entities") == 1
    found = memoria.open_nodes(["renzo flores"])
    assert "Imprime en 3D" in found and "software" in found


def test_declared_aliases_find_the_entity():
    memoria.create_entities([{**entity("Pixel", "mascota", "Gato gris"), "aliases": ["el gato"]}])
    assert "Gato gris" in memoria.open_nodes(["El Gato"])


def test_observations_keep_date_and_source_and_skip_repeats():
    memoria.create_entities([entity("Tama", "proyecto", "Usa SQLite")], source=7)
    memoria.add_observations([{"entityName": "tama", "contents": ["Usa SQLite"]}], source=8)
    with memoria.connect() as db:
        rows = db.execute("SELECT source, created_at, valid FROM observations").fetchall()
    assert len(rows) == 1 and rows[0]["source"] == 7 and rows[0]["valid"] == 1
    assert rows[0]["created_at"].startswith("20")


def test_search_is_accent_insensitive_and_limited():
    for name in ("Alfa", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta"):
        memoria.create_entities([entity(name, "proyecto", "impresión de piezas")])
    result = memoria.search_nodes("impresion")
    assert result.count("(proyecto)") == memoria.MAX_ENTITIES


def test_search_returns_only_recent_valid_observations():
    obs = [f"dato {i}" for i in range(8)]
    memoria.create_entities([entity("Ana", "persona", *obs)])
    result = memoria.search_nodes("ana")
    assert result.count("- dato") == memoria.MAX_OBSERVATIONS
    assert "dato 7" in result and "dato 0" not in result


def test_search_without_results_says_so():
    assert "No hay nada" in memoria.search_nodes("marte")


def test_search_finds_entities_by_type():
    memoria.create_entities([entity("Cien años de soledad", "libro", "Lo está leyendo")])
    assert "Cien años" in memoria.search_nodes("libro")


def test_no_match_lists_saved_entities():
    memoria.create_entities([entity("Cilantro", "gusto", "No le gusta")])
    result = memoria.search_nodes("hierba")
    assert "No hay coincidencias" in result and "Cilantro (gusto)" in result


def test_contradicted_observation_is_kept_but_not_used():
    memoria.create_entities([entity("Renzo", "persona", "Vive en Valparaíso")])
    memoria.delete_observations([{"entityName": "Renzo", "observations": ["Vive en Valparaíso"]}])
    memoria.add_observations([{"entityName": "Renzo", "contents": ["Vive en Santiago"]}])
    result = memoria.search_nodes("renzo")
    assert "Santiago" in result and "Valparaíso" not in result
    assert "No hay coincidencias" in memoria.search_nodes("valparaiso")
    with memoria.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM observations WHERE valid = 0").fetchone()[0] == 1


def test_relations_and_delete_cascade():
    memoria.create_entities([entity("Renzo"), entity("Tama", "proyecto")])
    assert "1 relaciones" in memoria.create_relations(
        [{"from": "Renzo", "to": "Tama", "relationType": "desarrolla"}]
    )
    assert "Renzo desarrolla Tama" in memoria.search_nodes("tama")
    assert "Olvidé" in memoria.delete_entities(["tama"])
    assert count("relations") == 0
    assert "Tama" not in memoria.search_nodes("tama")


def test_delete_relations():
    memoria.create_entities([entity("Ana"), entity("Tama", "proyecto")])
    memoria.create_relations([{"from": "ana", "to": "tama", "relationType": "usa"}])
    deleted = memoria.delete_relations([{"from": "ana", "to": "tama", "relationType": "usa"}])
    assert "1 relaciones" in deleted


def test_destructive_tools_are_annotated_and_read_graph_is_not_exposed():
    tools = {t.name: t for t in asyncio.run(memoria.server.list_tools())}
    assert "read_graph" not in tools
    for name in (
        "delete_entities",
        "delete_observations",
        "delete_relations",
        "proponer_cambio_perfil",
    ):
        assert tools[name].annotations.destructive_hint


def test_recent_events_reads_events_db(tmp_path):
    store = EventStore(tmp_path / "data" / "events.db")
    store.log("user_msg", "Ayer calibré la impresora")
    store.log("assistant_msg", "Bien hecho")
    store.log("error", "secreto interno")
    store.close()
    everything = memoria.recent_events()
    assert "Renzo: Ayer calibré" in everything and "Tama: Bien hecho" in everything
    assert "secreto" not in everything
    assert "calibré" in memoria.recent_events(consulta="calibre")
    assert "No encontré" in memoria.recent_events(consulta="marte")


def test_profile_change_appends_line(tmp_path):
    assert "actualizado" in memoria.proponer_cambio_perfil("Prefiere respuestas cortas")
    profile = (tmp_path / "memoria" / "nucleo.md").read_text(encoding="utf-8")
    assert "- Prefiere respuestas cortas" in profile


def test_export_is_sorted_and_stable(tmp_path):
    memoria.create_entities([entity("Zoe", "persona", "uno"), entity("Ana", "persona", "dos")])
    memoria.create_relations([{"from": "Zoe", "to": "Ana", "relationType": "conoce a"}])
    target = tmp_path / "memoria" / "grafo.jsonl"
    assert memoria.export_graph(target) == 2
    first = target.read_text(encoding="utf-8")
    memoria.export_graph(target)
    assert target.read_text(encoding="utf-8") == first
    lines = [json.loads(line) for line in first.splitlines()]
    assert [x.get("name") for x in lines[:2]] == ["Ana", "Zoe"]
    assert lines[-1] == {"type": "relation", "from": "Zoe", "relationType": "conoce a", "to": "Ana"}


def test_session_prompt_has_identity_profile_and_recall(tmp_path):
    memory_dir = tmp_path / "memoria"
    memory_dir.mkdir()
    (memory_dir / "nucleo.md").write_text("Se llama Renzo.", encoding="utf-8")
    store = EventStore(tmp_path / "e.db")
    store.log("user_msg", "Hablemos del viaje a Chiloé")
    prompt = session_prompt(memory_dir, store)
    store.close()
    assert prompt.startswith("Eres Tama")
    assert "Perfil de Renzo:\nSe llama Renzo." in prompt
    assert "Última conversación" in prompt and "Chiloé" in prompt


def test_session_prompt_without_memory_is_just_identity(tmp_path):
    store = EventStore(tmp_path / "e.db")
    assert "Perfil" not in session_prompt(tmp_path / "nada", store)
    assert last_conversation_line(store) == ""
    store.close()


def test_profile_is_capped(tmp_path):
    (tmp_path / "nucleo.md").write_text("x" * 5000, encoding="utf-8")
    assert len(read_profile(tmp_path)) == MAX_PROFILE_CHARS


def test_events_log_returns_id_and_indexes_only_conversation(tmp_path):
    store = EventStore(tmp_path / "e.db")
    first = store.log("user_msg", "Hablemos del viaje")
    second = store.log("assistant_msg", "Bueno")
    store.log("error", "indexar")
    assert second == first + 1
    query = "SELECT rowid FROM events_fts WHERE events_fts MATCH ?"
    assert [r[0] for r in store._db.execute(query, ("viaje",))] == [first]
    assert list(store._db.execute(query, ("indexar",))) == []
    store.close()


def test_last_conversation_keeps_latest_messages(tmp_path):
    store = EventStore(tmp_path / "e.db")
    assert store.last_conversation() is None
    for text in ("uno", "dos", "tres", "cuatro"):
        store.log("user_msg", text)
    _, messages = store.last_conversation()
    assert messages == ["dos", "tres", "cuatro"]
    store.close()


def test_source_is_filled_by_the_hub_and_hidden_from_the_model(tmp_path):
    server = ServerConfig("memoria", [sys.executable, "-m", "tama.mcp_servers.memoria"])
    hub = McpHub([server], server_env(tmp_path / "data", tmp_path / "w", tmp_path / "memoria"))
    hub.start()
    try:
        tool = hub.find("create_entities")
        assert "source" not in tool.as_provider_tool()["function"]["parameters"]["properties"]
        hub.current_event = 42
        args = {"entities": [{"name": "Tama", "entityType": "proyecto", "observations": ["x"]}]}
        assert hub.call("create_entities", args).ok
    finally:
        hub.close()
    db = sqlite3.connect(tmp_path / "data" / "memoria.db")
    assert db.execute("SELECT source FROM observations").fetchone()[0] == 42
    db.close()


class Tools:
    current_event = None

    def __init__(self):
        self.calls = []

    def available_tools(self, max_tools=10):
        return [], []

    def call(self, name, args):
        self.calls.append((name, args))

        class Outcome:
            text = "Olvidé: Pixel."

        return Outcome()


class FakeBrain:
    def __init__(self):
        self.tools = Tools()
