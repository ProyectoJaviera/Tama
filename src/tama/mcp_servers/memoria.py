"""Servidor MCP de memoria: grafo de conocimiento en SQLite con FTS5 (`memoria.db`).

Usa los nombres de herramientas del servidor oficial MCP Memory, con mejoras: observaciones con
fecha, origen y vigencia; nombres normalizados con alias; y búsqueda con ranking que devuelve poco.
Se lanza por stdio: `python -m tama.mcp_servers.memoria`.
"""

import json
import os
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    norm TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL DEFAULT 'cosa',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
    norm TEXT PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source INTEGER,
    valid INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS relations (
    from_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, type)
);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
    entity_id UNINDEXED, text, tokenize='unicode61 remove_diacritics 2'
);
"""
MAX_ENTITIES = 5
MAX_OBSERVATIONS = 5
MAX_LISTED = 15
MAX_FOUND = 10
FORGOTTEN = "(olvidado)"
MAX_RELATED = 5
# Lo que muestran las búsquedas alrededor de un dato: el modelo a veces lo copia con el texto.
SHOWN_ID = re.compile(r"^\s*\[o\d+\]\s*")
SHOWN_DATE = re.compile(r"\s*\(\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?\)\s*$")
ENTITY_TYPES = (
    "persona",
    "mascota",
    "lugar",
    "organizacion",
    "proyecto",
    "plan",
    "evento",
    "salud",
    "objeto",
    "gusto",
)
TYPE_ALIASES = {
    "cosa": "objeto",
    "empresa": "organizacion",
    "trabajo": "organizacion",
    "libro": "objeto",
    "animal": "mascota",
}

server = MCPServer("memoria", instructions="Lo que Tama recuerda de Renzo.")


def db_path() -> Path:
    return Path(os.environ.get("TAMA_DATA_DIR", "data")) / "memoria.db"


def events_path() -> Path:
    return Path(os.environ.get("TAMA_DATA_DIR", "data")) / "events.db"


def profile_path() -> Path:
    return Path(os.environ.get("TAMA_MEMORY_DIR", "memoria")) / "nucleo.md"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    return db


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize(name: str) -> str:
    """Minúsculas, sin tildes y con espacios simples: 'Renzo  Flóres' → 'renzo flores'."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def find_entity(db: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """Busca por nombre normalizado o por alias."""
    norm = normalize(name)
    row = db.execute("SELECT * FROM entities WHERE norm = ?", (norm,)).fetchone()
    if row:
        return row
    return db.execute(
        "SELECT e.* FROM aliases a JOIN entities e ON e.id = a.entity_id WHERE a.norm = ?", (norm,)
    ).fetchone()


def same_person(db: sqlite3.Connection, name: str, type_: str) -> sqlite3.Row | None:
    """Entidad del mismo tipo cuyo nombre contiene al nuevo o al revés ('renzo', 'renzo flores')."""
    words = set(normalize(name).split())
    for row in db.execute("SELECT * FROM entities WHERE type = ?", (type_,)):
        other = set(row["norm"].split())
        if words <= other or other <= words:
            return row
    return None


def reindex(db: sqlite3.Connection, entity_id: int) -> None:
    """Rehace la fila de búsqueda de una entidad con su nombre, alias y observaciones vigentes."""
    row = db.execute("SELECT name, type FROM entities WHERE id = ?", (entity_id,)).fetchone()
    db.execute("DELETE FROM search WHERE entity_id = ?", (entity_id,))
    if row is None:
        return
    parts = [row["name"], row["type"]]
    parts += [
        r[0] for r in db.execute("SELECT norm FROM aliases WHERE entity_id = ?", (entity_id,))
    ]
    parts += [
        r[0]
        for r in db.execute(
            "SELECT content FROM observations WHERE entity_id = ? AND valid = 1", (entity_id,)
        )
    ]
    db.execute("INSERT INTO search (entity_id, text) VALUES (?, ?)", (entity_id, "\n".join(parts)))


def upsert_entity(db: sqlite3.Connection, name: str, type_: str) -> tuple[int, bool]:
    """Devuelve (id, nueva). Un nombre parecido del mismo tipo queda como alias, sin duplicar."""
    existing = find_entity(db, name)
    if existing:
        if existing["type"] in ("cosa", "objeto") and type_ != "objeto":
            db.execute("UPDATE entities SET type = ? WHERE id = ?", (type_, existing["id"]))
        return existing["id"], False
    similar = same_person(db, name, type_)
    if similar:
        db.execute(
            "INSERT OR IGNORE INTO aliases (norm, entity_id) VALUES (?, ?)",
            (normalize(name), similar["id"]),
        )
        return similar["id"], False
    cursor = db.execute(
        "INSERT INTO entities (name, norm, type, created_at) VALUES (?, ?, ?, ?)",
        (name.strip(), normalize(name), type_, now()),
    )
    return cursor.lastrowid, True


def add_obs(db: sqlite3.Connection, entity_id: int, contents: list[str], source: int | None) -> int:
    added = 0
    for content in contents:
        text = content.strip()
        repeated = db.execute(
            "SELECT 1 FROM observations WHERE entity_id = ? AND content = ? AND valid = 1",
            (entity_id, text),
        ).fetchone()
        if text and not repeated:
            db.execute(
                "INSERT INTO observations (entity_id, content, created_at, source)"
                " VALUES (?, ?, ?, ?)",
                (entity_id, text, now(), source),
            )
            added += 1
    return added


def recent_observations(db: sqlite3.Connection, entity_id: int) -> list[str]:
    rows = db.execute(
        "SELECT id, content, created_at FROM observations WHERE entity_id = ? AND valid = 1"
        " ORDER BY created_at DESC, id DESC LIMIT ?",
        (entity_id, MAX_OBSERVATIONS),
    ).fetchall()
    return [f"[o{r['id']}] {r['content']} ({shown_at(r['created_at'])})" for r in rows]


def relations_of(db: sqlite3.Connection, entity_id: int) -> list[str]:
    rows = db.execute(
        "SELECT a.name AS a, r.type AS t, b.name AS b FROM relations r"
        " JOIN entities a ON a.id = r.from_id JOIN entities b ON b.id = r.to_id"
        " WHERE r.from_id = ? OR r.to_id = ?",
        (entity_id, entity_id),
    ).fetchall()
    return [f"{r['a']} {r['t']} {r['b']}" for r in rows]


def describe(db: sqlite3.Connection, row: sqlite3.Row) -> str:
    lines = [f"{row['name']} ({row['type']})"]
    lines += [f"- {o}" for o in recent_observations(db, row["id"])]
    lines += [f"- relación: {r}" for r in relations_of(db, row["id"])]
    return "\n".join(lines)


def no_match(db: sqlite3.Connection) -> str:
    """Sin coincidencias: da los nombres guardados para que el modelo pueda revisar alguno."""
    rows = db.execute(
        "SELECT name, type FROM entities ORDER BY created_at DESC, id DESC LIMIT ?",
        (MAX_LISTED,),
    ).fetchall()
    if not rows:
        return "No hay nada anotado sobre eso."
    listed = ", ".join(f"{r['name']} ({r['type']})" for r in rows)
    return (
        "No hay coincidencias con esas palabras. Entidades guardadas: "
        f"{listed}. Si alguna puede servir, búscala por su nombre; si no, no está anotado."
    )


def without_date(text: str) -> str:
    """Deja solo el texto de un dato copiado de una búsqueda."""
    return SHOWN_DATE.sub("", SHOWN_ID.sub("", str(text))).strip()


def entity_type(value: str) -> str:
    """Uno de los tipos de ENTITY_TYPES; lo desconocido queda como objeto."""
    norm = normalize(value)
    norm = TYPE_ALIASES.get(norm, norm)
    return norm if norm in ENTITY_TYPES else "objeto"


def shown_at(iso: str) -> str:
    """Fecha y hora locales: entre dos versiones de un dato, vale la más reciente."""
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


def fts_query(text: str) -> str:
    return " OR ".join(f'"{w}"*' for w in re.findall(r"\w+", text, flags=re.UNICODE))


@server.tool(
    description=(
        "Guarda en la memoria de Tama lo que Renzo cuente de su vida. Llámala en cada turno con un "
        "dato nuevo, antes de responder: lo que no se guarda se olvida al cerrar la sesión. Cada "
        "entidad lleva name, entityType (persona, mascota, lugar, organizacion, proyecto, plan, "
        "evento, salud, objeto, gusto) y "
        "observations (frases cortas). Si la entidad ya existe, se completa."
    )
)
def create_entities(entities: list[dict[str, Any]], source: int | None = None) -> str:
    created, updated = [], []
    with connect() as db:
        for entity in entities:
            name = str(entity.get("name", "")).strip()
            if not name:
                continue
            type_ = entity_type(str(entity.get("entityType") or "objeto"))
            entity_id, new = upsert_entity(db, name, type_)
            add_obs(db, entity_id, list(entity.get("observations") or []), source)
            for alias in entity.get("aliases") or []:
                db.execute(
                    "INSERT OR IGNORE INTO aliases (norm, entity_id) VALUES (?, ?)",
                    (normalize(str(alias)), entity_id),
                )
            reindex(db, entity_id)
            (created if new else updated).append(name)
    parts = []
    if created:
        parts.append("Creadas: " + ", ".join(created))
    if updated:
        parts.append("Ya existían y se completaron: " + ", ".join(updated))
    return ". ".join(parts) or "No había nada que guardar."


@server.tool(
    description=(
        "Relaciona dos entidades ya guardadas. Cada relación lleva from, to y relationType "
        "(p. ej. 'trabaja en', 'es hermana de')."
    )
)
def create_relations(relations: list[dict[str, str]]) -> str:
    done = 0
    missing = []
    with connect() as db:
        for relation in relations:
            a = find_entity(db, relation.get("from", ""))
            b = find_entity(db, relation.get("to", ""))
            if a is None or b is None:
                missing.append(relation.get("from" if a is None else "to", "?"))
                continue
            db.execute(
                "INSERT OR IGNORE INTO relations (from_id, to_id, type) VALUES (?, ?, ?)",
                (a["id"], b["id"], relation.get("relationType", "relacionado con")),
            )
            done += 1
    text = f"{done} relaciones guardadas."
    return text + (f" No existen: {', '.join(missing)}." if missing else "")


@server.tool(
    description=(
        "Agrega datos nuevos a entidades. Cada elemento lleva entityName y "
        "contents (frases cortas). Si la entidad no existe, se crea. Si un dato reemplaza a otro, "
        "usa update_observation."
    )
)
def add_observations(observations: list[dict[str, Any]], source: int | None = None) -> str:
    added = 0
    with connect() as db:
        for item in observations:
            name = str(item.get("entityName", "")).strip()
            if not name:
                continue
            entity_id, _ = upsert_entity(db, name, "objeto")
            added += add_obs(db, entity_id, list(item.get("contents") or []), source)
            reindex(db, entity_id)
    return f"{added} datos guardados."


@server.tool(
    description=(
        "Busca lo que Renzo te contó. Cada dato trae su id ([o42]), fecha y hora. "
        "Llámala siempre que pregunte algo sobre él o su vida, antes de responder; tu memoria de "
        "sesiones anteriores solo está aquí. Si no hay resultados, dile que no tienes ese dato."
    ),
    annotations=ToolAnnotations(read_only_hint=True),
)
def search_nodes(query: str = "") -> str:
    with connect() as db:
        match = fts_query(query)
        if match:
            rows = db.execute(
                "SELECT e.* FROM search s JOIN entities e ON e.id = s.entity_id"
                " WHERE search MATCH ? ORDER BY rank LIMIT ?",
                (match, MAX_ENTITIES),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT e.* FROM entities e ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
                (MAX_ENTITIES,),
            ).fetchall()
        if not rows:
            return no_match(db)
        return "\n\n".join(describe(db, r) for r in rows)


@server.tool(
    description="Muestra todo lo guardado sobre entidades concretas, por nombre.",
    annotations=ToolAnnotations(read_only_hint=True),
)
def open_nodes(names: list[str]) -> str:
    with connect() as db:
        found = [row for row in (find_entity(db, n) for n in names) if row]
        if not found:
            return "No hay nada anotado sobre eso."
        return "\n\n".join(describe(db, r) for r in found)


@server.tool(
    description="Borra entidades completas de la memoria, con sus datos y relaciones.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def delete_entities(entityNames: list[str]) -> str:
    deleted = []
    with connect() as db:
        for name in entityNames:
            row = find_entity(db, name)
            if row:
                db.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                reindex(db, row["id"])
                deleted.append(row["name"])
    return f"Olvidé: {', '.join(deleted)}." if deleted else "No había nada con ese nombre."


@server.tool(
    description=(
        "Olvida datos: úsala solo cuando Renzo pida olvidar o borrar algo; para corregir un dato "
        "usa update_observation. Cada elemento lleva entityName y observations (el texto de los "
        "datos)."
    ),
    annotations=ToolAnnotations(destructive_hint=True),
)
def delete_observations(deletions: list[dict[str, Any]]) -> str:
    changed = 0
    with connect() as db:
        for item in deletions:
            row = find_entity(db, str(item.get("entityName", "")))
            if row is None:
                continue
            for content in item.get("observations") or []:
                like = f"%{without_date(content)}%"
                changed += db.execute(
                    "UPDATE observations SET valid = 0"
                    " WHERE entity_id = ? AND valid = 1 AND content LIKE ?",
                    (row["id"], like),
                ).rowcount
            reindex(db, row["id"])
    if not changed:
        return "No encontré esos datos: nada cambió. Búscalos con search_nodes y copia su texto."
    return f"{changed} datos marcados como no vigentes."


@server.tool(
    description=(
        "Corrige datos cuando Renzo cambia de idea o algo cambió (otro plan, otra ciudad). Antes "
        "busca el tema con search_nodes. Lleva ids (los [o42] de TODOS los datos antiguos, en "
        "todas las entidades) y nueva (el dato vigente). Los antiguos dejan de usarse."
    )
)
def update_observation(
    ids: list[str], nueva: str, entityName: str = "", source: int | None = None
) -> str:
    numbers = [int(i) for i in re.findall(r"\d+", " ".join(str(i) for i in ids))]
    if not numbers or not nueva.strip():
        raise ValueError("Faltan ids o el dato nuevo: busca con search_nodes y reintenta.")
    with connect() as db:
        marks = ",".join("?" * len(numbers))
        found = db.execute(
            f"SELECT id, entity_id FROM observations WHERE valid = 1 AND id IN ({marks})", numbers
        ).fetchall()
        missing = sorted(set(numbers) - {r["id"] for r in found})
        if missing:
            listed = ", ".join(f"o{n}" for n in missing)
            raise ValueError(
                f"Nada cambió: {listed} no existe o ya no está vigente. Busca con search_nodes "
                "y reintenta con los ids que aparezcan."
            )
        db.execute(f"UPDATE observations SET valid = 0 WHERE id IN ({marks})", numbers)
        entities = {r["entity_id"] for r in found}
        target = upsert_entity(db, entityName, "objeto")[0] if entityName else found[0]["entity_id"]
        add_obs(db, target, [nueva], source)
        for entity_id in entities | {target}:
            reindex(db, entity_id)
        others = related_facts(db, nueva, numbers)
    text = f"Corregido: {len(numbers)} datos antiguos dejaron de estar vigentes."
    if others:
        text += " Otros datos del tema, que quizá también cambiaron: " + "; ".join(others) + "."
        text += " Si alguno quedó antiguo, corrígelo con update_observation."
    return text


def related_facts(db: sqlite3.Connection, text: str, retired: list[int]) -> list[str]:
    """Datos vigentes, en cualquier entidad, que comparten palabras con lo recién corregido."""
    marks = ",".join("?" * len(retired))
    old = db.execute(f"SELECT content FROM observations WHERE id IN ({marks})", retired).fetchall()
    joined = " ".join([text, *[r["content"] for r in old]])
    words = {normalize(w)[:6] for w in re.findall(r"\w+", joined, flags=re.UNICODE) if len(w) > 4}
    rows = db.execute(
        "SELECT o.id, e.name, o.content FROM observations o JOIN entities e ON e.id = o.entity_id"
        " WHERE o.valid = 1 AND o.content != ? ORDER BY o.id DESC",
        (text.strip(),),
    ).fetchall()
    found = [r for r in rows if any(w in normalize(r["content"]) for w in words)]
    return [f"[o{r['id']}] {r['name']}: {r['content']}" for r in found[:MAX_RELATED]]


@server.tool(description="Crea la entidad de Renzo si falta. La usa Tama al iniciar.")
def ensure_owner(nombre: str) -> str:
    with connect() as db:
        entity_id, _ = upsert_entity(db, nombre, "persona")
        db.execute("UPDATE entities SET type = 'persona' WHERE id = ?", (entity_id,))
        db.execute("INSERT OR IGNORE INTO aliases (norm, entity_id) VALUES ('yo', ?)", (entity_id,))
        reindex(db, entity_id)
    return "Listo."


@server.tool(
    description="Borra relaciones entre entidades. Cada una lleva from, to y relationType.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def delete_relations(relations: list[dict[str, str]]) -> str:
    deleted = 0
    with connect() as db:
        for relation in relations:
            a = find_entity(db, relation.get("from", ""))
            b = find_entity(db, relation.get("to", ""))
            if a and b:
                deleted += db.execute(
                    "DELETE FROM relations WHERE from_id = ? AND to_id = ? AND type = ?",
                    (a["id"], b["id"], relation.get("relationType", "")),
                ).rowcount
    return f"{deleted} relaciones borradas."


@server.tool(
    description=(
        "Consulta lo conversado con Renzo en los últimos días. Úsala para preguntas como '¿de qué "
        "hablamos ayer?'. `dias` limita cuánto mirar hacia atrás y `consulta` filtra por palabras."
    ),
    annotations=ToolAnnotations(read_only_hint=True),
)
def recent_events(dias: int = 2, consulta: str = "") -> str:
    path = events_path()
    if not path.exists():
        return "Todavía no hay conversaciones registradas."
    since = (datetime.now(UTC) - timedelta(days=max(1, dias))).isoformat(timespec="seconds")
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        match = fts_query(consulta)
        if match:
            rows = db.execute(
                "SELECT e.ts, e.type, e.content FROM events_fts f JOIN events e ON e.id = f.rowid"
                " WHERE events_fts MATCH ? AND e.ts >= ? ORDER BY e.id DESC LIMIT 12",
                (match, since),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT ts, type, content FROM events WHERE ts >= ?"
                " AND type IN ('user_msg', 'assistant_msg') ORDER BY id DESC LIMIT 12",
                (since,),
            ).fetchall()
    finally:
        db.close()
    if not rows:
        return "No encontré conversaciones en ese período."
    who = {"user_msg": "Renzo", "assistant_msg": "Tama"}
    lines = [
        f"{r['ts'][:16]} {who.get(r['type'], r['type'])}: {(r['content'] or '')[:200]}"
        for r in reversed(rows)
    ]
    return "\n".join(lines)


@server.tool(
    description=(
        "Propone agregar un dato estable al perfil de Renzo (nombre, ciudad, intereses, cómo le "
        "gusta que lo traten). Se escribe solo si Renzo lo confirma."
    ),
    annotations=ToolAnnotations(destructive_hint=True),
)
def proponer_cambio_perfil(cambio: str) -> str:
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    separator = "" if not current or current.endswith("\n") else "\n"
    path.write_text(f"{current}{separator}- {cambio.strip()}\n", encoding="utf-8")
    return "Perfil actualizado."


def message_query(text: str) -> str:
    """Todas las palabras importantes: para no tocar mensajes que solo mencionan una."""
    return " ".join(f'"{w}"*' for w in re.findall(r"\w+", text, flags=re.UNICODE) if len(w) > 3)


def matching_messages(db: sqlite3.Connection, query: str) -> list[tuple[int, str]]:
    match = message_query(query)
    if not match:
        return []
    return db.execute(
        "SELECT e.id, e.content FROM events_fts f JOIN events e ON e.id = f.rowid"
        " WHERE events_fts MATCH ?",
        (match,),
    ).fetchall()


def events_db() -> sqlite3.Connection | None:
    return sqlite3.connect(events_path()) if events_path().exists() else None


@server.tool(
    description="Busca qué recuerda Tama sobre un tema, para que Renzo elija qué olvidar.",
    annotations=ToolAnnotations(read_only_hint=True),
)
def find_memory(query: str) -> str:
    """JSON con las coincidencias en entidades y datos; si no hay, las entidades parecidas."""
    words = [normalize(w) for w in re.findall(r"\w+", query, flags=re.UNICODE) if len(w) > 3]
    items: list[dict[str, Any]] = []
    with connect() as db:
        match = fts_query(" ".join(words))
        rows = (
            db.execute(
                "SELECT e.* FROM search s JOIN entities e ON e.id = s.entity_id"
                " WHERE search MATCH ? ORDER BY rank LIMIT ?",
                (match, MAX_FOUND),
            ).fetchall()
            if match
            else []
        )
        for row in rows:
            if any(w in row["norm"] for w in words):
                text = f"Todo sobre {row['name']} ({row['type']})"
                items.append({"entidad": row["name"], "texto": text})
            observations = db.execute(
                "SELECT id, content FROM observations WHERE entity_id = ? AND valid = 1",
                (row["id"],),
            ).fetchall()
            for observation in observations:
                if any(w in normalize(observation["content"]) for w in words):
                    text = f"{row['name']}: {observation['content']}"
                    items.append({"dato": observation["id"], "texto": text})
        names = [r["name"] for r in db.execute("SELECT name FROM entities ORDER BY name")]
    events = events_db()
    messages = len(matching_messages(events, query)) if events else 0
    if events:
        events.close()
    similar = get_close_matches(query, names, n=3, cutoff=0.5)
    return json.dumps(
        {"items": items[:MAX_FOUND], "mensajes": messages, "parecidas": similar or names[:5]},
        ensure_ascii=False,
    )


@server.tool(
    description="Borra de verdad lo elegido en /olvidar: entidades, datos y mensajes del tema.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def forget_memory(entidades: list[str], datos: list[int], consulta: str = "") -> str:
    with connect() as db:
        for name in entidades:
            row = find_entity(db, name)
            if row:
                db.execute("DELETE FROM entities WHERE id = ?", (row["id"],))
                reindex(db, row["id"])
        for observation in datos:
            row = db.execute(
                "SELECT entity_id FROM observations WHERE id = ?", (observation,)
            ).fetchone()
            if row:
                db.execute("DELETE FROM observations WHERE id = ?", (observation,))
                reindex(db, row["entity_id"])
    redacted = 0
    events = events_db()
    if events and consulta:
        with events:
            for event_id, content in matching_messages(events, consulta):
                events.execute(
                    "INSERT INTO events_fts(events_fts, rowid, content) VALUES ('delete', ?, ?)",
                    (event_id, content),
                )
                events.execute("UPDATE events SET content = ? WHERE id = ?", (FORGOTTEN, event_id))
                redacted += 1
    if events:
        events.close()
    return f"Olvidado: {len(entidades) + len(datos)} recuerdos y {redacted} mensajes."


def export_graph(target: Path) -> int:
    """Vuelca el grafo a JSONL ordenado y estable. Devuelve la cantidad de entidades."""
    with connect() as db:
        lines = []
        rows = db.execute("SELECT * FROM entities ORDER BY norm").fetchall()
        for row in rows:
            observations = db.execute(
                "SELECT content, created_at, source, valid FROM observations"
                " WHERE entity_id = ? ORDER BY created_at, id",
                (row["id"],),
            ).fetchall()
            aliases = db.execute(
                "SELECT norm FROM aliases WHERE entity_id = ? ORDER BY norm", (row["id"],)
            ).fetchall()
            entity = {
                "type": "entity",
                "name": row["name"],
                "entityType": row["type"],
                "aliases": [a["norm"] for a in aliases],
                "observations": [dict(o) for o in observations],
            }
            lines.append(json.dumps(entity, ensure_ascii=False, sort_keys=True))
        relations = db.execute(
            "SELECT a.name AS a, r.type AS t, b.name AS b FROM relations r"
            " JOIN entities a ON a.id = r.from_id JOIN entities b ON b.id = r.to_id"
            " ORDER BY a.norm, r.type, b.norm"
        ).fetchall()
        for r in relations:
            relation = {"type": "relation", "from": r["a"], "relationType": r["t"], "to": r["b"]}
            lines.append(json.dumps(relation, ensure_ascii=False, sort_keys=True))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    server.run("stdio")
