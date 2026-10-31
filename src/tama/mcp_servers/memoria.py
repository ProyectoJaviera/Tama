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
from difflib import SequenceMatcher
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
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    norm TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'activo'
        CHECK (status IN ('activo', 'pausado', 'logrado', 'descartado')),
    target_date TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_progress (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source INTEGER
);
CREATE TABLE IF NOT EXISTS goal_entities (
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (goal_id, entity_id)
);
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    mentioned_at TEXT
);
CREATE TABLE IF NOT EXISTS reviewed_pairs (
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    verdict TEXT NOT NULL,
    PRIMARY KEY (a, b)
);
CREATE TABLE IF NOT EXISTS reviewed_entities (
    entity_id INTEGER PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
    reviewed_at TEXT NOT NULL
);
"""
GOAL_STATES = ("activo", "pausado", "logrado", "descartado")
MAX_ENTITIES = 5
MAX_OBSERVATIONS = 5
MAX_LISTED = 15
MIN_SIMILARITY = 0.75
NO_SUGGESTION = "Nada pendiente."
MAX_MENTIONS = 3

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
        "SELECT content, created_at FROM observations WHERE entity_id = ? AND valid = 1"
        " ORDER BY created_at DESC, id DESC LIMIT ?",
        (entity_id, MAX_OBSERVATIONS),
    ).fetchall()
    return [f"{r['content']} ({r['created_at'][:10]})" for r in rows]


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


def fts_query(text: str) -> str:
    return " OR ".join(f'"{w}"*' for w in re.findall(r"\w+", text, flags=re.UNICODE))


@server.tool(
    description=(
        "Guarda lo que Renzo cuente de su vida. Llámala en cada turno con un dato nuevo, antes de "
        "responder: lo que no se guarda se olvida. Cada entidad lleva name, entityType (persona, "
        "mascota, proyecto, lugar, gusto, libro, plan) y observations (frases cortas). Si la "
        "entidad ya existe, se completa. Las metas de Renzo van en manage_goals."
    )
)
def create_entities(entities: list[dict[str, Any]], source: int | None = None) -> str:
    created, updated = [], []
    with connect() as db:
        for entity in entities:
            name = str(entity.get("name", "")).strip()
            if not name:
                continue
            type_ = normalize(str(entity.get("entityType") or "cosa"))
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
        "Agrega datos nuevos a entidades que ya existen. Cada elemento lleva entityName y "
        "contents (frases cortas). Si la entidad no existe, se crea."
    )
)
def add_observations(observations: list[dict[str, Any]], source: int | None = None) -> str:
    added = 0
    with connect() as db:
        for item in observations:
            name = str(item.get("entityName", "")).strip()
            if not name:
                continue
            entity_id, _ = upsert_entity(db, name, "cosa")
            added += add_obs(db, entity_id, list(item.get("contents") or []), source)
            reindex(db, entity_id)
    return f"{added} datos guardados."


@server.tool(
    description=(
        "Busca lo que Renzo te contó de su vida. Llámala siempre que pregunte algo sobre él, antes "
        "de responder: lo de sesiones anteriores solo está aquí. Si no hay resultados, dile que no "
        "tienes ese dato."
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
        "Marca datos como ya no vigentes, por ejemplo si Renzo corrige algo o cambió de opinión. "
        "No se borran: dejan de usarse. Cada elemento lleva entityName y observations (el texto "
        "de los datos antiguos)."
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
                like = f"%{str(content).strip()}%"
                changed += db.execute(
                    "UPDATE observations SET valid = 0"
                    " WHERE entity_id = ? AND valid = 1 AND content LIKE ?",
                    (row["id"], like),
                ).rowcount
            reindex(db, row["id"])
    return f"{changed} datos marcados como no vigentes."


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


def find_goal(db: sqlite3.Connection, ref: str | int) -> sqlite3.Row | None:
    """Busca un objetivo por número o por parte de su título."""
    text = str(ref).strip().lstrip("#")
    if text.isdigit():
        return db.execute("SELECT * FROM goals WHERE id = ?", (int(text),)).fetchone()
    like = f"%{normalize(text)}%"
    return db.execute(
        "SELECT * FROM goals WHERE norm LIKE ? ORDER BY status = 'activo' DESC, id DESC LIMIT 1",
        (like,),
    ).fetchone()


def describe_goal(db: sqlite3.Connection, goal: sqlite3.Row) -> str:
    target = f", para {goal['target_date']}" if goal["target_date"] else ""
    lines = [f"#{goal['id']} {goal['title']} ({goal['status']}{target})"]
    if goal["detail"]:
        lines.append(goal["detail"])
    progress = db.execute(
        "SELECT content, created_at FROM goal_progress WHERE goal_id = ? ORDER BY id DESC LIMIT ?",
        (goal["id"], MAX_OBSERVATIONS),
    ).fetchall()
    lines += [f"- {p['created_at'][:10]}: {p['content']}" for p in reversed(progress)]
    entities = db.execute(
        "SELECT e.name FROM goal_entities g JOIN entities e ON e.id = g.entity_id"
        " WHERE g.goal_id = ? ORDER BY e.norm",
        (goal["id"],),
    ).fetchall()
    if entities:
        lines.append("Relacionado con: " + ", ".join(e["name"] for e in entities))
    return "\n".join(lines)


@server.tool(
    description=(
        "Crea un objetivo de Renzo: algo que quiere lograr y a lo que dar seguimiento. Lleva "
        "titulo y, si los dijo, detalle, fecha (AAAA-MM-DD) y entidades relacionadas."
    )
)
def create_goal(
    titulo: str, detalle: str = "", fecha: str = "", entidades: list[str] | None = None
) -> str:
    title = titulo.strip()
    if not title:
        return "Falta el título del objetivo."
    with connect() as db:
        stamp = now()
        cursor = db.execute(
            "INSERT INTO goals (title, norm, detail, target_date, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (title, normalize(title), detalle.strip(), fecha.strip(), stamp, stamp),
        )
        for name in entidades or []:
            row = find_entity(db, name)
            if row:
                db.execute(
                    "INSERT OR IGNORE INTO goal_entities (goal_id, entity_id) VALUES (?, ?)",
                    (cursor.lastrowid, row["id"]),
                )
    return f"Objetivo #{cursor.lastrowid} creado: {title}."


@server.tool(
    description=(
        "Cambia un objetivo existente, indicado por número o título: titulo, detalle, fecha o "
        "estado (activo, pausado o logrado). Para descartarlo usa discard_goal."
    )
)
def update_goal(
    objetivo: str, titulo: str = "", detalle: str = "", fecha: str = "", estado: str = ""
) -> str:
    status = estado.strip().lower()
    if status == "descartado":
        return "Para descartar un objetivo usa discard_goal: Renzo debe confirmarlo."
    if status and status not in GOAL_STATES:
        return "Estado no válido: usa activo, pausado o logrado."
    with connect() as db:
        goal = find_goal(db, objetivo)
        if goal is None:
            return "No encontré ese objetivo."
        title = titulo.strip() or goal["title"]
        db.execute(
            "UPDATE goals SET title = ?, norm = ?, detail = ?, target_date = ?, status = ?,"
            " updated_at = ? WHERE id = ?",
            (
                title,
                normalize(title),
                detalle.strip() or goal["detail"],
                fecha.strip() or goal["target_date"],
                status or goal["status"],
                now(),
                goal["id"],
            ),
        )
        return "Actualizado: " + describe_goal(db, find_goal(db, goal["id"]))


@server.tool(
    description=(
        "Muestra los objetivos de Renzo con su avance. `estado` filtra: activo (por defecto), "
        "pausado, logrado, descartado o todos."
    ),
    annotations=ToolAnnotations(read_only_hint=True),
)
def list_goals(estado: str = "activo") -> str:
    status = estado.strip().lower()
    with connect() as db:
        if status == "todos":
            rows = db.execute("SELECT * FROM goals ORDER BY id").fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM goals WHERE status = ? ORDER BY id", (status,)
            ).fetchall()
        if not rows:
            return "No hay objetivos en ese estado."
        return "\n\n".join(describe_goal(db, r) for r in rows)


@server.tool(
    description=(
        "Anota un avance de un objetivo, indicado por número o título, cuando Renzo cuente que "
        "dio un paso hacia él."
    )
)
def add_goal_progress(objetivo: str, avance: str, source: int | None = None) -> str:
    with connect() as db:
        goal = find_goal(db, objetivo)
        if goal is None:
            return "No encontré ese objetivo."
        stamp = now()
        db.execute(
            "INSERT INTO goal_progress (goal_id, content, created_at, source) VALUES (?, ?, ?, ?)",
            (goal["id"], avance.strip(), stamp, source),
        )
        db.execute("UPDATE goals SET updated_at = ? WHERE id = ?", (stamp, goal["id"]))
    return f"Avance anotado en: {goal['title']}."


@server.tool(
    description=(
        "Objetivos de Renzo: metas que quiere lograr y a las que das seguimiento. Úsala siempre "
        "que hable de una meta o cuente un paso hacia ella, antes de responder. accion: crear "
        "(titulo, y detalle y fecha si los dijo), avance (objetivo y avance), ver (estado, "
        "opcional) o cambiar (objetivo y lo que cambia: titulo, detalle, fecha o estado activo, "
        "pausado o logrado)."
    )
)
def manage_goals(
    accion: str,
    objetivo: str = "",
    titulo: str = "",
    detalle: str = "",
    fecha: str = "",
    estado: str = "",
    avance: str = "",
    source: int | None = None,
) -> str:
    """Una sola herramienta a la vista: el modelo local no llega a las otras por su grupo."""
    action = accion.strip().lower()
    if action == "crear":
        return create_goal(titulo or objetivo, detalle, fecha)
    if action == "avance":
        return add_goal_progress(objetivo or titulo, avance or detalle, source)
    if action == "ver":
        return list_goals(estado or "activo")
    if action == "cambiar":
        return update_goal(objetivo or titulo, titulo if objetivo else "", detalle, fecha, estado)
    return "Acción no válida: usa crear, avance, ver o cambiar."


@server.tool(
    description="Descarta un objetivo que Renzo ya no quiere seguir. Se conserva su historial.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def discard_goal(objetivo: str) -> str:
    with connect() as db:
        goal = find_goal(db, objetivo)
        if goal is None:
            return "No encontré ese objetivo."
        db.execute(
            "UPDATE goals SET status = 'descartado', updated_at = ? WHERE id = ?",
            (now(), goal["id"]),
        )
    return f"Objetivo descartado: {goal['title']}."


@server.tool(
    description="Borra un objetivo y su avance.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def delete_goal(objetivo: str) -> str:
    with connect() as db:
        goal = find_goal(db, objetivo)
        if goal is None:
            return "No encontré ese objetivo."
        db.execute("DELETE FROM goals WHERE id = ?", (goal["id"],))
    return f"Objetivo borrado: {goal['title']}."


@server.tool(description="Entrega una sugerencia pendiente y la marca como ya mencionada.")
def next_suggestion() -> str:
    with connect() as db:
        row = db.execute(
            "SELECT id, text FROM suggestions WHERE mentioned_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return NO_SUGGESTION
        db.execute("UPDATE suggestions SET mentioned_at = ? WHERE id = ?", (now(), row["id"]))
        return row["text"]


# --- Reflexión: la usa tama.reflection directamente; el modelo de chat no ve estas funciones. ---


def similarity(a: str, b: str) -> float:
    """1 si un nombre contiene al otro por palabras; si no, parecido entre 0 y 1."""
    first, second = set(a.split()), set(b.split())
    if first <= second or second <= first:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def merge_candidates(limit: int = 5) -> list[dict[str, str]]:
    """Pares que quizá sean la misma entidad y no se han revisado, los más parecidos primero."""
    with connect() as db:
        rows = db.execute("SELECT * FROM entities ORDER BY id").fetchall()
        reviewed = {(r["a"], r["b"]) for r in db.execute("SELECT a, b FROM reviewed_pairs")}
        by_id = {r["id"]: r for r in rows}
        pairs: dict[tuple[int, int], float] = {}
        for i, first in enumerate(rows):
            for second in rows[i + 1 :]:
                score = similarity(first["norm"], second["norm"])
                if score >= MIN_SIMILARITY:
                    pairs[(first["id"], second["id"])] = score
            # Un nombre poco común que aparece en los datos de otra entidad también es candidato.
            mentions = db.execute(
                "SELECT entity_id FROM search WHERE search MATCH ? LIMIT ?",
                (f'"{first["norm"]}"', MAX_MENTIONS + 1),
            ).fetchall()
            others = [m["entity_id"] for m in mentions if m["entity_id"] != first["id"]]
            if len(mentions) <= MAX_MENTIONS:
                for other in others:
                    key = (min(first["id"], other), max(first["id"], other))
                    pairs.setdefault(key, MIN_SIMILARITY)
        result = []
        for (a, b), _ in sorted(pairs.items(), key=lambda item: -item[1]):
            first, second = by_id[a], by_id[b]
            if (first["norm"], second["norm"]) not in reviewed:
                result.append(
                    {
                        "a": first["name"],
                        "b": second["name"],
                        "text_a": describe(db, first),
                        "text_b": describe(db, second),
                    }
                )
        return result[:limit]


def review_pair(a: str, b: str, verdict: str) -> None:
    """Deja anotado un par ya decidido para no preguntarlo de nuevo."""
    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO reviewed_pairs (a, b, verdict) VALUES (?, ?, ?)",
            (normalize(a), normalize(b), verdict),
        )


def merge_entities(keep: str, drop: str) -> bool:
    """Funde `drop` en `keep`: datos, alias, relaciones y objetivos. Nada se pierde."""
    with connect() as db:
        kept, dropped = find_entity(db, keep), find_entity(db, drop)
        if kept is None or dropped is None or kept["id"] == dropped["id"]:
            return False
        new, old = kept["id"], dropped["id"]
        db.execute("UPDATE observations SET entity_id = ? WHERE entity_id = ?", (new, old))
        db.execute("UPDATE aliases SET entity_id = ? WHERE entity_id = ?", (new, old))
        db.execute(
            "INSERT OR IGNORE INTO aliases (norm, entity_id) VALUES (?, ?)", (dropped["norm"], new)
        )
        db.execute(
            "INSERT OR IGNORE INTO relations (from_id, to_id, type)"
            " SELECT ?, to_id, type FROM relations WHERE from_id = ? AND to_id != ?",
            (new, old, new),
        )
        db.execute(
            "INSERT OR IGNORE INTO relations (from_id, to_id, type)"
            " SELECT from_id, ?, type FROM relations WHERE to_id = ? AND from_id != ?",
            (new, old, new),
        )
        db.execute(
            "INSERT OR IGNORE INTO goal_entities (goal_id, entity_id)"
            " SELECT goal_id, ? FROM goal_entities WHERE entity_id = ?",
            (new, old),
        )
        db.execute("DELETE FROM entities WHERE id = ?", (old,))
        reindex(db, old)
        reindex(db, new)
    return True


def entities_to_review(limit: int = 5) -> list[dict[str, Any]]:
    """Entidades con dos o más datos vigentes y novedades desde su última revisión."""
    with connect() as db:
        rows = db.execute(
            "SELECT e.id, e.name, MAX(o.created_at) AS last FROM entities e"
            " JOIN observations o ON o.entity_id = e.id AND o.valid = 1"
            " LEFT JOIN reviewed_entities r ON r.entity_id = e.id"
            " GROUP BY e.id HAVING COUNT(o.id) >= 2"
            " AND (MAX(r.reviewed_at) IS NULL OR MAX(o.created_at) > MAX(r.reviewed_at))"
            " ORDER BY last DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            observations = db.execute(
                "SELECT id, content, created_at FROM observations"
                " WHERE entity_id = ? AND valid = 1 ORDER BY created_at, id",
                (row["id"],),
            ).fetchall()
            result.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "observations": [
                        {"id": o["id"], "text": o["content"], "date": o["created_at"][:10]}
                        for o in observations
                    ],
                }
            )
        return result


def retire_observations(entity_id: int, ids: list[int]) -> int:
    """Marca datos de una entidad como no vigentes; no se borran."""
    with connect() as db:
        changed = 0
        for observation in ids:
            changed += db.execute(
                "UPDATE observations SET valid = 0 WHERE id = ? AND entity_id = ?",
                (observation, entity_id),
            ).rowcount
        reindex(db, entity_id)
    return changed


def mark_reviewed(entity_id: int) -> None:
    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO reviewed_entities (entity_id, reviewed_at) VALUES (?, ?)",
            (entity_id, now()),
        )


def add_suggestion(text: str) -> bool:
    """Guarda una sugerencia pendiente, sin repetirla."""
    with connect() as db:
        repeated = db.execute("SELECT 1 FROM suggestions WHERE text = ?", (text,)).fetchone()
        if repeated:
            return False
        db.execute("INSERT INTO suggestions (text, created_at) VALUES (?, ?)", (text, now()))
    return True


def pending_suggestions() -> int:
    with connect() as db:
        query = "SELECT COUNT(*) FROM suggestions WHERE mentioned_at IS NULL"
        return db.execute(query).fetchone()[0]


def goals_overview() -> str:
    """Objetivos activos con los días desde su último movimiento, para pensar sugerencias."""
    with connect() as db:
        rows = db.execute("SELECT * FROM goals WHERE status = 'activo' ORDER BY id").fetchall()
    today = datetime.now(UTC)
    lines = []
    for row in rows:
        days = (today - datetime.fromisoformat(row["updated_at"])).days
        lines.append(f"- {row['title']}: {days} días sin avance")
    return "\n".join(lines)


def day_learned(start: str, end: str) -> list[str]:
    """Datos y avances guardados entre dos instantes UTC (ISO), para el diario."""
    with connect() as db:
        observations = db.execute(
            "SELECT e.name, o.content FROM observations o JOIN entities e ON e.id = o.entity_id"
            " WHERE o.created_at >= ? AND o.created_at < ? ORDER BY o.id",
            (start, end),
        ).fetchall()
        progress = db.execute(
            "SELECT g.title, p.content FROM goal_progress p JOIN goals g ON g.id = p.goal_id"
            " WHERE p.created_at >= ? AND p.created_at < ? ORDER BY p.id",
            (start, end),
        ).fetchall()
    return [f"Dato sobre {r['name']}: {r['content']}" for r in observations] + [
        f"Avance en {r['title']}: {r['content']}" for r in progress
    ]


def export_graph(target: Path) -> int:
    """Vuelca grafo y objetivos a JSONL ordenado y estable. Devuelve cuántas entidades."""
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
        for goal in db.execute("SELECT * FROM goals ORDER BY id").fetchall():
            progress = db.execute(
                "SELECT content, created_at FROM goal_progress WHERE goal_id = ? ORDER BY id",
                (goal["id"],),
            ).fetchall()
            item = {
                "type": "goal",
                "title": goal["title"],
                "detail": goal["detail"],
                "status": goal["status"],
                "targetDate": goal["target_date"],
                "progress": [dict(p) for p in progress],
            }
            lines.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    server.run("stdio")
