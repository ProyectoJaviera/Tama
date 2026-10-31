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
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher, get_close_matches
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
# Un hecho se guarda una vez y se enlaza a todas las entidades que menciona.
LINKED_FACTS = """
CREATE TABLE observation_entities (
    observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (observation_id, entity_id)
);
INSERT INTO observation_entities SELECT id, entity_id FROM observations;
CREATE TABLE facts (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source INTEGER,
    valid INTEGER NOT NULL DEFAULT 1,
    superseded_by INTEGER REFERENCES facts(id)
);
INSERT INTO facts (id, content, created_at, source, valid)
    SELECT id, content, created_at, source, valid FROM observations;
DROP TABLE observations;
ALTER TABLE facts RENAME TO observations;
"""
# Un estado se reemplaza; un evento ocurrió en una fecha y se acumula.
FACT_KINDS = """
ALTER TABLE observations ADD COLUMN kind TEXT NOT NULL DEFAULT 'estado';
ALTER TABLE observations ADD COLUMN occurred_at TEXT;
UPDATE observations SET occurred_at = substr(created_at, 1, 10);
UPDATE observations SET kind = 'evento' WHERE content LIKE '%médic%' OR content LIKE '%medic%'
    OR content LIKE '%duele%' OR content LIKE '%dolor%' OR id IN (
        SELECT l.observation_id FROM observation_entities l JOIN entities e ON e.id = l.entity_id
        WHERE e.type IN ('salud', 'evento'));
"""
MIGRATIONS = [SCHEMA, LINKED_FACTS, FACT_KINDS]
MAX_ENTITIES = 5
MAX_OBSERVATIONS = 5
MAX_LISTED = 15
MAX_FOUND = 10
FORGOTTEN = "(olvidado)"
MAX_RELATED = 5
MAX_HISTORY = 3
# Lo de salud nunca se reemplaza: es la historia médica de Renzo.
HEALTH_WORDS = (
    "duele",
    "dolor",
    "medic",
    "doctor",
    "sintoma",
    "fiebre",
    "lesion",
    "operac",
    "opero",
    "fractur",
    "alergi",
    "remedio",
    "pastilla",
    "receta",
    "kinesi",
    "dentista",
    "inflam",
    "hospital",
    "clinica",
    "enferm",
)
MAX_TIMELINE = 30
MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
NUMBERS = dict(
    zip(("un", "dos", "tres", "cuatro", "cinco", "seis", "siete"), range(1, 8), strict=True)
)
ABSOLUTE_DATE = re.compile(
    rf"\b(\d{{1,2}}) de ({'|'.join(MONTHS)})(?: del? (\d{{4}}))?", re.IGNORECASE
)
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
AGO = re.compile(rf"\bhace (\d+|{'|'.join(NUMBERS)}) d[ií]as?\b", re.IGNORECASE)
# "en la mañana" no es una fecha: solo cuenta "mañana" sin artículo delante.
RELATIVE = {"pasado mañana": 2, "anteayer": -2, "ayer": -1, "hoy": 0, "mañana": 1}
SAME_FACT = 0.92
OWNER = "renzo"
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
    db.execute("PRAGMA busy_timeout = 5000")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        # Con las claves foráneas apagadas: rehacer una tabla no debe borrar en cascada.
        db.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
    db.execute("PRAGMA foreign_keys = ON")
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
        if "de" in words | other:  # "esposa de Renzo" es otra persona, no Renzo
            continue
        if words <= other or other <= words:
            return row
    return None


def reindex(db: sqlite3.Connection, entity_id: int) -> None:
    """Rehace la fila de búsqueda de una entidad con su nombre, alias y hechos vigentes."""
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
            "SELECT o.content FROM observations o"
            " JOIN observation_entities l ON l.observation_id = o.id"
            " WHERE l.entity_id = ? AND o.valid = 1",
            (entity_id,),
        )
    ]
    db.execute("INSERT INTO search (entity_id, text) VALUES (?, ?)", (entity_id, "\n".join(parts)))


def linked_entities(db: sqlite3.Connection, ids: list[int]) -> set[int]:
    marks = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT entity_id FROM observation_entities WHERE observation_id IN ({marks})", ids
    )
    return {r[0] for r in rows}


def link(db: sqlite3.Connection, observation_id: int, entity_ids: set[int]) -> None:
    for entity_id in entity_ids:
        db.execute(
            "INSERT OR IGNORE INTO observation_entities (observation_id, entity_id) VALUES (?, ?)",
            (observation_id, entity_id),
        )
        reindex(db, entity_id)


def mentioned_entities(db: sqlite3.Connection, text: str) -> set[int]:
    """Entidades cuyo nombre o alias aparece tal cual en el hecho."""
    words = f" {' '.join(re.findall(r'\w+', normalize(text)))} "
    rows = db.execute(
        "SELECT id, norm FROM entities UNION SELECT entity_id, norm FROM aliases"
    ).fetchall()
    return {r[0] for r in rows if len(r[1]) > 1 and f" {r[1]} " in words}


def drop_orphans(db: sqlite3.Connection) -> None:
    """Un hecho sin entidades ya no se puede encontrar: se borra con la última de ellas."""
    db.execute(
        "DELETE FROM observations WHERE id NOT IN (SELECT observation_id FROM observation_entities)"
    )


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
    add_relative_alias(db, cursor.lastrowid, normalize(name))
    return cursor.lastrowid, True


def add_relative_alias(db: sqlite3.Connection, entity_id: int, norm: str) -> None:
    """ "esposa de Renzo" también se reconoce como "esposa" al enlazar hechos."""
    relative, _, owner = norm.rpartition(" de ")
    if relative and owner == OWNER:
        db.execute(
            "INSERT OR IGNORE INTO aliases (norm, entity_id) VALUES (?, ?)", (relative, entity_id)
        )


def as_list(value: Any) -> list[str]:
    """Lista de frases; el modelo local a veces manda una sola como texto."""
    if not value:
        return []
    return [value] if isinstance(value, str) else [str(item) for item in value]


def resolve_dates(text: str, today: date | None = None) -> tuple[str, str]:
    """Cambia fechas relativas por absolutas y devuelve el texto y la fecha que menciona."""
    today = today or date.today()
    found = ""

    def shift(days: int) -> str:
        nonlocal found
        found = (today + timedelta(days=days)).isoformat()
        return found

    def ago(match: re.Match) -> str:
        amount = match.group(1).lower()
        return "el " + shift(-(int(amount) if amount.isdigit() else NUMBERS[amount]))

    text = AGO.sub(ago, text)
    for word, days in RELATIVE.items():
        pattern = rf"(?<!\bla )(?<!\besta )\b{word}\b"
        if re.search(pattern, text, flags=re.IGNORECASE):
            text = re.sub(pattern, f"el {shift(days)}", text, flags=re.IGNORECASE)
    absolute = ABSOLUTE_DATE.search(text)
    if absolute:
        year = int(absolute.group(3) or today.year)
        month = MONTHS.index(absolute.group(2).lower()) + 1
        found = date(year, month, int(absolute.group(1))).isoformat()
    iso = ISO_DATE.search(text)
    return text, found or (iso.group(0) if iso else "")


def fact_key(text: str) -> tuple[str, str, set[str]]:
    """Forma comparable de un hecho: sin mayúsculas, tildes, puntuación ni formato de fecha."""
    _, when = resolve_dates(text)
    plain = ISO_DATE.sub("", ABSOLUTE_DATE.sub("", text))
    words = re.findall(r"\w+", normalize(plain))
    return " ".join(w for w in words if w != "el"), when, {w for w in words if w.isdigit()}


def same_fact(db: sqlite3.Connection, text: str) -> int | None:
    """Hecho vigente que dice lo mismo, salvo el formato; otra fecha u otro número no lo es."""
    wanted, when, digits = fact_key(text)
    for row in db.execute("SELECT id, content FROM observations WHERE valid = 1"):
        current, other_when, other_digits = fact_key(row["content"])
        close = SequenceMatcher(None, wanted, current).ratio() >= SAME_FACT
        if close and when == other_when and digits == other_digits:
            return row["id"]
    return None


def add_obs(
    db: sqlite3.Connection,
    entity_ids: int | set[int],
    contents: list[str],
    source: int | None,
    kind: str = "",
) -> int:
    """Guarda hechos enlazados a las entidades; uno repetido solo suma los enlaces que le falten.

    Todo es un evento, salvo lo que el modelo marca como estado al corregir un dato.
    """
    entities = {entity_ids} if isinstance(entity_ids, int) else entity_ids
    added = 0
    for content in contents:
        text, when = resolve_dates(content.strip())
        if not text:
            continue
        repeated = same_fact(db, text)
        if repeated:
            observation_id = repeated
        else:
            state = kind == "estado" and not is_health(db, entities, text)
            observation_id = db.execute(
                "INSERT INTO observations (content, created_at, source, kind, occurred_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    text,
                    now(),
                    source,
                    "estado" if state else "evento",
                    when or date.today().isoformat(),
                ),
            ).lastrowid
            added += 1
        link(db, observation_id, entities | mentioned_entities(db, text))
    return added


def is_health(db: sqlite3.Connection, entities: set[int], text: str) -> bool:
    if any(word in normalize(text) for word in HEALTH_WORDS):
        return True
    marks = ",".join("?" * len(entities))
    query = f"SELECT 1 FROM entities WHERE id IN ({marks}) AND type IN ('salud', 'evento')"
    return db.execute(query, list(entities)).fetchone() is not None


def already_happened(db: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """Eventos que una corrección no toca: salud, o algo con fecha anterior a cuando se contó."""
    told = shown_at(row["created_at"])[:10]
    linked = linked_entities(db, [row["id"]])
    return row["occurred_at"] < told or is_health(db, linked, row["content"])


def stems(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"\w+", normalize(text)) if len(w) > 4}


def recent_observations(db: sqlite3.Connection, entity_id: int, query: str = "") -> list[str]:
    """Hechos vigentes: primero los que coinciden con la búsqueda, luego los más nuevos."""
    rows = db.execute(
        "SELECT o.id, o.content, o.created_at FROM observations o"
        " JOIN observation_entities l ON l.observation_id = o.id"
        " WHERE l.entity_id = ? AND o.valid = 1 ORDER BY o.created_at DESC, o.id DESC",
        (entity_id,),
    ).fetchall()
    wanted = stems(query)
    matching = [r for r in rows if any(w in normalize(r["content"]) for w in wanted)]
    chosen = (matching + [r for r in rows if r not in matching])[:MAX_OBSERVATIONS]
    return [
        f"[o{r['id']}] {r['content']} ({shown_at(r['created_at'])}){earlier_versions(db, r['id'])}"
        for r in chosen
    ]


def earlier_versions(db: sqlite3.Connection, observation_id: int) -> str:
    """Línea corta con lo que este hecho reemplazó, del más nuevo al más antiguo."""
    found: list[str] = []
    frontier = [observation_id]
    while frontier and len(found) < MAX_HISTORY:
        marks = ",".join("?" * len(frontier))
        rows = db.execute(
            "SELECT id, content, occurred_at FROM observations"
            f" WHERE superseded_by IN ({marks}) ORDER BY id DESC",
            frontier,
        ).fetchall()
        found += [f"{r['content']} ({r['occurred_at']})" for r in rows]
        frontier = [r["id"] for r in rows]
    return "\n  antes: " + "; ".join(found[:MAX_HISTORY]) if found else ""


def relations_of(db: sqlite3.Connection, entity_id: int) -> list[str]:
    rows = db.execute(
        "SELECT a.name AS a, r.type AS t, b.name AS b FROM relations r"
        " JOIN entities a ON a.id = r.from_id JOIN entities b ON b.id = r.to_id"
        " WHERE r.from_id = ? OR r.to_id = ?",
        (entity_id, entity_id),
    ).fetchall()
    return [f"{r['a']} {r['t']} {r['b']}" for r in rows]


def describe(db: sqlite3.Connection, row: sqlite3.Row, query: str = "") -> str:
    lines = [f"{row['name']} ({row['type']})"]
    lines += [f"- {o}" for o in recent_observations(db, row["id"], query)]
    lines += [f"- relación: {r}" for r in relations_of(db, row["id"])]
    return "\n".join(lines)


def no_match(db: sqlite3.Connection) -> str:
    """Sin coincidencias: da los nombres guardados para que el modelo pueda revisar alguno."""
    rows = db.execute(
        "SELECT e.name, e.type FROM entities e"
        " WHERE EXISTS (SELECT 1 FROM observation_entities l JOIN observations o"
        " ON o.id = l.observation_id WHERE l.entity_id = e.id AND o.valid = 1)"
        " ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
        (MAX_LISTED,),
    ).fetchall()
    if not rows:
        return "No hay nada anotado sobre eso."
    listed = ", ".join(f"{r['name']} ({r['type']})" for r in rows)
    text = (
        "No hay coincidencias con esas palabras. Entidades guardadas: "
        f"{listed}. Si alguna puede servir, búscala por su nombre; si no, no está anotado."
    )
    owner = find_entity(db, OWNER)
    facts = recent_observations(db, owner["id"]) if owner else []
    return f"{text} Datos recientes de Renzo: {'; '.join(facts)}." if facts else text


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
        "evento, salud, objeto, gusto), observations (frases cortas que se entiendan solas: "
        "'el cumpleaños de su mamá es...'). Copia las fechas como las dijo ('hace tres días'): se "
        "calculan solas, no pidas la hora. Algo puntual con alguien (un almuerzo) va en esa "
        "persona, sin entidad nueva."
    )
)
def create_entities(entities: list[dict[str, Any]], source: int | None = None) -> str:
    created, updated, older, saved = [], [], [], []
    with connect() as db:
        for entity in entities:
            name = str(entity.get("name", "")).strip()
            if not name:
                continue
            type_ = entity_type(str(entity.get("entityType") or "objeto"))
            added = as_list(entity.get("observations"))
            people = mentioned_entities(db, name)
            owner = find_entity(db, OWNER)
            if type_ in ("plan", "evento") and people and not find_entity(db, name):
                # Algo puntual con alguien va en sus personas, no en una entidad nueva.
                targets = people | ({owner["id"]} if owner else set())
                add_obs(db, targets, added, source)
                saved += saved_ids(db, added)
                updated.append(name)
                continue
            entity_id, new = upsert_entity(db, name, type_)
            add_obs(db, entity_id, added, source)
            saved += saved_ids(db, added)
            older += same_topic(db, entity_id, added)
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
    if saved:
        parts.append("Guardado como " + ", ".join(dict.fromkeys(saved)))
    if older:
        listed = "; ".join(dict.fromkeys(older))
        parts.append(
            f"Ya había datos del tema: {listed}. Si el nuevo reemplaza alguno, corrígelo ahora con "
            "update_observation"
        )
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
        "Agrega datos nuevos. Cada elemento lleva entityName (o entityNames con TODAS las "
        "que menciona) y contents (frases cortas). Si la entidad no existe, se crea. Si un dato "
        "reemplaza a otro, usa update_observation."
    )
)
def add_observations(observations: list[dict[str, Any]], source: int | None = None) -> str:
    added = 0
    with connect() as db:
        for item in observations:
            names = [str(n).strip() for n in item.get("entityNames") or []]
            names += [str(item.get("entityName", "")).strip()]
            entities = {upsert_entity(db, n, "objeto")[0] for n in names if n}
            if entities:
                added += add_obs(db, entities, as_list(item.get("contents")), source)
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
                "SELECT e.* FROM entities e"
                " WHERE EXISTS (SELECT 1 FROM observation_entities l JOIN observations o"
                " ON o.id = l.observation_id WHERE l.entity_id = e.id AND o.valid = 1)"
                " ORDER BY e.created_at DESC, e.id DESC LIMIT ?",
                (MAX_ENTITIES,),
            ).fetchall()
        if not rows:
            return no_match(db)
        return "\n\n".join(describe(db, r, query) for r in rows)


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
        drop_orphans(db)
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
                rows = db.execute(
                    "SELECT o.id FROM observations o"
                    " JOIN observation_entities l ON l.observation_id = o.id"
                    " WHERE l.entity_id = ? AND o.valid = 1 AND o.content LIKE ?",
                    (row["id"], like),
                ).fetchall()
                ids = [r["id"] for r in rows]
                for observation_id in ids:
                    db.execute("UPDATE observations SET valid = 0 WHERE id = ?", (observation_id,))
                changed += len(ids)
                if ids:
                    for entity_id in linked_entities(db, ids):
                        reindex(db, entity_id)
    if not changed:
        return "No encontré esos datos: nada cambió. Búscalos con search_nodes y copia su texto."
    return f"{changed} datos marcados como no vigentes."


@server.tool(
    description=(
        "Corrige datos cuando Renzo cambia de idea o algo cambió (otro plan, otra ciudad). Antes "
        "busca el tema con search_nodes. En UNA llamada: ids (los [o42] de TODOS los datos "
        "antiguos, en todas las entidades), nueva (un solo hecho vigente) y entidades (TODAS las "
        "que menciona, p. ej. Renzo, su esposa y Halloween)."
    )
)
def update_observation(
    ids: list[str], nueva: str, entidades: list[str] | None = None, source: int | None = None
) -> str:
    numbers = [int(i) for i in re.findall(r"\d+", " ".join(str(i) for i in ids))]
    if not numbers or not nueva.strip():
        raise ValueError("Faltan ids o el dato nuevo: busca con search_nodes y reintenta.")
    with connect() as db:
        marks = ",".join("?" * len(numbers))
        found = db.execute(
            f"SELECT * FROM observations WHERE valid = 1 AND id IN ({marks})", numbers
        ).fetchall()
        missing = sorted(set(numbers) - {r["id"] for r in found})
        if missing:
            listed = ", ".join(f"o{n}" for n in missing)
            similar = "; ".join(related_facts(db, nueva, [])) or "ninguno"
            raise ValueError(
                f"Nada cambió: {listed} no existe o ya no está vigente. Datos parecidos: {similar}. "
                "Reintenta con sus ids."
            )
        linked = linked_entities(db, numbers)
        linked |= {upsert_entity(db, n, "objeto")[0] for n in entidades or [] if n.strip()}
        # Corregir marca lo antiguo como estado, salvo lo que ya ocurrió: eso se acumula.
        states = [r["id"] for r in found if not already_happened(db, r)]
        replaced = ",".join("?" * len(states))
        db.execute(f"UPDATE observations SET valid = 0 WHERE id IN ({replaced})", states)
        add_obs(db, linked, [nueva], source, kind="estado")
        new_id = same_fact(db, resolve_dates(nueva.strip())[0])
        db.execute(
            f"UPDATE observations SET kind = 'estado', superseded_by = ? WHERE id IN ({replaced})",
            [new_id, *states],
        )
        for entity_id in linked:
            reindex(db, entity_id)
        others = related_facts(db, nueva, numbers)
    text = f"Corregido: {len(states)} datos antiguos dejaron de estar vigentes."
    if len(states) < len(numbers):
        text += " Los eventos pasados se conservan en la historia."
    if others:
        text += " Otros datos del tema, que quizá también cambiaron: " + "; ".join(others) + "."
        text += " Si alguno quedó antiguo, corrígelo con update_observation."
    return text


def saved_ids(db: sqlite3.Connection, texts: list[str]) -> list[str]:
    """Ids de hechos recién guardados: permiten corregirlos sin buscarlos."""
    found = [same_fact(db, resolve_dates(text.strip())[0]) for text in texts]
    return [f"o{number}" for number in found if number]


def same_topic(db: sqlite3.Connection, entity_id: int, added: list[str]) -> list[str]:
    """Hechos anteriores de la entidad que comparten palabras con los recién guardados."""
    wanted = stems(" ".join(added))
    rows = db.execute(
        "SELECT o.id, o.content FROM observations o"
        " JOIN observation_entities l ON l.observation_id = o.id"
        " WHERE l.entity_id = ? AND o.valid = 1 ORDER BY o.id DESC",
        (entity_id,),
    ).fetchall()
    found = [
        r
        for r in rows
        if r["content"] not in [a.strip() for a in added]
        and any(w in normalize(r["content"]) for w in wanted)
    ]
    return [f"[o{r['id']}] {r['content']}" for r in found[:MAX_RELATED]]


def related_facts(db: sqlite3.Connection, text: str, retired: list[int]) -> list[str]:
    """Datos vigentes, en cualquier entidad, que comparten palabras con lo recién corregido."""
    marks = ",".join("?" * len(retired))
    old = db.execute(f"SELECT content FROM observations WHERE id IN ({marks})", retired).fetchall()
    joined = " ".join([text, *[r["content"] for r in old]])
    words = {normalize(w)[:6] for w in re.findall(r"\w+", joined, flags=re.UNICODE) if len(w) > 4}
    rows = db.execute(
        "SELECT id, content FROM observations WHERE valid = 1 AND content != ? ORDER BY id DESC",
        (text.strip(),),
    ).fetchall()
    found = [r for r in rows if any(w in normalize(r["content"]) for w in words)]
    return [f"[o{r['id']}] {r['content']}" for r in found[:MAX_RELATED]]


@server.tool(
    description=(
        "Historial en orden cronológico, incluidos los datos antiguos. Úsala para preguntas de "
        'historial o "desde cuándo". tema (o entidad), desde y hasta (AAAA-MM-DD) son opcionales.'
    ),
    annotations=ToolAnnotations(read_only_hint=True),
)
def linea_de_tiempo(tema: str = "", desde: str = "", hasta: str = "") -> str:
    with connect() as db:
        rows = db.execute(
            "SELECT o.id, o.content, o.valid, o.superseded_by, o.occurred_at,"
            " (SELECT group_concat(e.norm || ' ' || e.type) FROM observation_entities l"
            " JOIN entities e ON e.id = l.entity_id WHERE l.observation_id = o.id) AS names"
            " FROM observations o ORDER BY o.occurred_at, o.id"
        ).fetchall()
    rows = [
        r for r in rows if r["occurred_at"] >= desde and (not hasta or r["occurred_at"] <= hasta)
    ]
    wanted = stems(tema)
    found = [
        r for r in rows if any(w in normalize(f"{r['content']} {r['names'] or ''}") for w in wanted)
    ]
    # Las palabras del tema pueden no aparecer ("historial médico"): el modelo elige de todo.
    chosen = (found or rows)[-MAX_TIMELINE:]
    if not chosen:
        return "No hay nada anotado en ese período."
    lines = []
    for row in chosen:
        state = "vigente" if row["valid"] else "ya no vigente"
        if row["superseded_by"]:
            state = f"reemplazado por o{row['superseded_by']}"
        lines.append(f"{row['occurred_at']} [o{row['id']}] {row['content']} ({state})")
    return "\n".join(lines)


@server.tool(description="Crea la entidad de Renzo si falta. La usa Tama al iniciar.")
def ensure_owner(nombre: str) -> str:
    with connect() as db:
        entity_id, _ = upsert_entity(db, nombre, "persona")
        db.execute("UPDATE entities SET type = 'persona' WHERE id = ?", (entity_id,))
        db.execute("INSERT OR IGNORE INTO aliases (norm, entity_id) VALUES ('yo', ?)", (entity_id,))
        reindex(db, entity_id)
        # Entidades guardadas antes de que existiera este alias.
        for row in db.execute("SELECT id, norm FROM entities").fetchall():
            add_relative_alias(db, row["id"], row["norm"])
            reindex(db, row["id"])
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
                "SELECT o.id, o.content FROM observations o"
                " JOIN observation_entities l ON l.observation_id = o.id"
                " WHERE l.entity_id = ? AND o.valid = 1",
                (row["id"],),
            ).fetchall()
            for observation in observations:
                if any(w in normalize(observation["content"]) for w in words):
                    text = f"{row['name']}: {observation['content']}"
                    item = {"dato": observation["id"], "texto": text}
                    if observation["id"] not in {i.get("dato") for i in items}:
                        items.append(item)
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
            linked = linked_entities(db, [observation])
            db.execute("DELETE FROM observations WHERE id = ?", (observation,))
            for entity_id in linked:
                reindex(db, entity_id)
        drop_orphans(db)
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
                "SELECT o.id, o.content, o.created_at, o.source, o.valid, o.superseded_by"
                " FROM observations o JOIN observation_entities l ON l.observation_id = o.id"
                " WHERE l.entity_id = ? ORDER BY o.created_at, o.id",
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
