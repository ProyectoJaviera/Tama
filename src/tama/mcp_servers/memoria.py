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
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
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
UPDATE observations SET occurred_at = date(created_at, 'localtime');
UPDATE observations SET kind = 'evento' WHERE content LIKE '%médic%' OR content LIKE '%medic%'
    OR content LIKE '%duele%' OR content LIKE '%dolor%' OR id IN (
        SELECT l.observation_id FROM observation_entities l JOIN entities e ON e.id = l.entity_id
        WHERE e.type IN ('salud', 'evento'));
"""
# La primera versión de FACT_KINDS usó el día UTC, que en Chile puede ser el día siguiente.
LOCAL_DATES = """
UPDATE observations SET occurred_at = date(created_at, 'localtime')
    WHERE occurred_at = substr(created_at, 1, 10);
"""
GOALS = """
CREATE TABLE goals (
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
CREATE TABLE goal_progress (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source INTEGER
);
CREATE TABLE goal_entities (
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (goal_id, entity_id)
);
CREATE TABLE suggestions (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    mentioned_at TEXT
);
CREATE TABLE reviewed_pairs (
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    verdict TEXT NOT NULL,
    PRIMARY KEY (a, b)
);
CREATE TABLE reviewed_entities (
    entity_id INTEGER PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
    reviewed_at TEXT NOT NULL
);
"""
CORRECTION_REASON = """
ALTER TABLE observations ADD COLUMN reason TEXT;
"""
PROACTIVE = """
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    urgent INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pendiente',
    created_at TEXT NOT NULL
);
CREATE TABLE notices (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    text TEXT NOT NULL,
    reason TEXT NOT NULL,
    shown_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'mostrado',
    snooze_until TEXT,
    value TEXT,
    repeats INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE silenced (key TEXT PRIMARY KEY);
CREATE TABLE silence_until (until_at TEXT NOT NULL);
"""
MIGRATIONS = [SCHEMA, LINKED_FACTS, FACT_KINDS, LOCAL_DATES, GOALS, CORRECTION_REASON, PROACTIVE]
GOAL_STATES = ("activo", "pausado", "logrado", "descartado")
NO_SUGGESTION = "Nada pendiente."
NO_GOALS = "No hay objetivos en ese estado."
SEARCH_NOTE = (
    "Si el mensaje de Renzo niega o corrige uno de estos datos, antes de responder llama "
    "update_observation con su id y error=true, aunque lo nuevo ya esté anotado."
)
MIN_SIMILARITY = 0.75
MAX_ENTITIES = 5
MAX_OBSERVATIONS = 5
MAX_LISTED = 15
MAX_FOUND = 10
FORGOTTEN = "(olvidado)"
MAX_RELATED = 5
MAX_HISTORY = 3
GOAL_SOON_DAYS = 7
WEEKDAYS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
RELATIVE_TIME = re.compile(
    r"\ben (?:un|una|(\d+|dos|tres|cuatro|cinco|seis|siete)) ?(min\w*|hora\w*|dia\w*|semana\w*)"
)
HOUR = re.compile(r"\ba las? (\d{1,2})(?:[:.](\d{2}))?(?: ?(am|pm|de la tarde|de la noche))?")
MAX_DETAIL = 200
SOON_DAYS = 60
# Lo de salud nunca se reemplaza: es la historia médica de Renzo.
HEALTH_TOPICS = ("salud", "medic", "enferm", "doctor", "clinic")
HEALTH_WORDS = (
    "duele",
    "dolor",
    "operad",
    "operar",
    "cirug",
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
MONTH_ONLY = re.compile(rf"\ben ({'|'.join(MONTHS)})(?: del? (\d{{4}}))?\b", re.IGNORECASE)
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
    """Personas, lugares o cosas nombradas en el hecho; un plan no se enlaza solo por mención."""
    words = f" {' '.join(re.findall(r'\w+', normalize(text)))} "
    rows = db.execute(
        "SELECT e.id, e.norm FROM entities e WHERE e.type NOT IN ('plan', 'evento', 'proyecto')"
        " UNION SELECT a.entity_id, a.norm FROM aliases a JOIN entities e ON e.id = a.entity_id"
        " WHERE e.type NOT IN ('plan', 'evento', 'proyecto')"
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


def dated(today: date, year: str | None, month: int, day: int) -> str:
    """Sin año, lo más probable es el pasado cercano, salvo un plan de las próximas semanas."""
    if year:
        return date(int(year), month, day).isoformat()
    result = date(today.year, month, day)
    if result > today + timedelta(days=SOON_DAYS):
        result = date(today.year - 1, month, day)
    return result.isoformat()


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
    month_only = MONTH_ONLY.search(text)
    if absolute:
        month = MONTHS.index(absolute.group(2).lower()) + 1
        found = dated(today, absolute.group(3), month, int(absolute.group(1)))
    elif month_only:
        month = MONTHS.index(month_only.group(1).lower()) + 1
        found = dated(today, month_only.group(2), month, 1)
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
    query = f"SELECT 1 FROM entities WHERE id IN ({marks}) AND type = 'salud'"
    return db.execute(query, list(entities)).fetchone() is not None


def already_happened(db: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """Eventos que una corrección no toca: salud, o algo con fecha anterior a cuando se contó."""
    told = shown_at(row["created_at"])[:10]
    linked = linked_entities(db, [row["id"]])
    return row["occurred_at"] < told or is_health(db, linked, row["content"])


def stems(text: str) -> set[str]:
    """Raíz corta de cada palabra: "operación" y "operado" deben coincidir."""
    return {w[:5] for w in re.findall(r"\w+", normalize(text)) if len(w) > 4}


def recent_observations(
    db: sqlite3.Connection, entity_id: int, query: str = "", limit: int = MAX_OBSERVATIONS
) -> list[str]:
    """Hechos vigentes: primero los que coinciden con la búsqueda, luego los más nuevos."""
    rows = db.execute(
        "SELECT o.id, o.content, o.created_at FROM observations o"
        " JOIN observation_entities l ON l.observation_id = o.id"
        " WHERE l.entity_id = ? AND o.valid = 1 ORDER BY o.created_at DESC, o.id DESC",
        (entity_id,),
    ).fetchall()
    wanted = stems(query)
    matching = [r for r in rows if any(w in normalize(r["content"]) for w in wanted)]
    chosen = (matching + [r for r in rows if r not in matching])[:limit]
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


def describe(
    db: sqlite3.Connection, row: sqlite3.Row, query: str = "", limit: int = MAX_OBSERVATIONS
) -> str:
    lines = [f"{row['name']} ({row['type']})"]
    lines += [f"- {o}" for o in recent_observations(db, row["id"], query, limit)]
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
        "Guarda lo que Renzo cuente de su vida: llámala con cada dato nuevo, antes de responder. "
        "Cada entidad lleva name, entityType (persona, mascota, lugar, organizacion, proyecto, "
        "plan, evento, salud, objeto, gusto) y observations (frases que digan de quién hablan, "
        "con su nombre, y las fechas como las dijo: se calculan solas). Lo puntual con alguien "
        "va en esa persona. Si dice el nombre de alguien ya guardado (su esposa Lizette), usa esa "
        "entidad con aliases: [nombre]."
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
        found = "\n\n".join(describe(db, r, query) for r in rows)
        return f"{found}\n\n{SEARCH_NOTE}" if query.strip() else found


@server.tool(
    description="Muestra todo lo guardado sobre entidades concretas, por nombre.",
    annotations=ToolAnnotations(read_only_hint=True),
)
def open_nodes(names: list[str]) -> str:
    with connect() as db:
        found = [row for row in (find_entity(db, n) for n in names) if row]
        if not found:
            return "No hay nada anotado sobre eso."
        return "\n\n".join(describe(db, r, limit=MAX_DETAIL) for r in found)


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
        "Reemplaza un dato que ya no es cierto (otro plan, otra ciudad). Un dato nuevo que no "
        "contradice nada va en create_entities, y las metas en manage_goals. Antes busca el tema "
        "con search_nodes. En UNA llamada: ids (los [o42] de TODOS los datos antiguos), nueva (un "
        "solo hecho vigente), entidades (lista con TODAS las que menciona) y error=true si el dato "
        "antiguo nunca fue cierto (Renzo aclara que no pasó)."
    )
)
def update_observation(
    ids: list[str],
    nueva: str,
    entidades: Any = None,
    error: bool = False,
    source: int | None = None,
) -> str:
    # El modelo a veces manda {} cuando no tiene entidades que agregar.
    if entidades and not isinstance(entidades, list):
        raise ToolError(
            "entidades debe ser una lista de nombres. Si es un dato nuevo que no reemplaza a "
            "otro, usa create_entities."
        )
    numbers = [int(i) for i in re.findall(r"\d+", " ".join(str(i) for i in ids))]
    if not numbers or not nueva.strip():
        raise ToolError("Faltan ids o el dato nuevo: busca con search_nodes y reintenta.")
    with connect() as db:
        marks = ",".join("?" * len(numbers))
        found = db.execute(
            f"SELECT * FROM observations WHERE valid = 1 AND id IN ({marks})", numbers
        ).fetchall()
        missing = sorted(set(numbers) - {r["id"] for r in found})
        if missing:
            listed = ", ".join(f"o{n}" for n in missing)
            similar = "; ".join(related_facts(db, nueva, [])) or "ninguno"
            raise ToolError(
                f"Nada cambió: {listed} no existe o ya no está vigente. "
                f"Datos parecidos: {similar}. Reintenta con sus ids.{goal_reminder(db)}"
            )
        linked = linked_entities(db, numbers)
        linked |= {upsert_entity(db, n, "objeto")[0] for n in entity_names(entidades)}
        # Lo de salud se acumula como historia, salvo que el dato fuera un error.
        states = [
            r["id"]
            for r in found
            if error or not is_health(db, linked_entities(db, [r["id"]]), r["content"])
        ]
        replaced = ",".join("?" * len(states))
        db.execute(f"UPDATE observations SET valid = 0 WHERE id IN ({replaced})", states)
        add_obs(db, linked, [nueva], source, kind="estado")
        new_id = same_fact(db, resolve_dates(nueva.strip())[0])
        db.execute(
            f"UPDATE observations SET kind = 'estado', superseded_by = ?, reason = ?"
            f" WHERE id IN ({replaced})",
            [new_id, "corregido" if error else None, *states],
        )
        for entity_id in linked:
            reindex(db, entity_id)
        others = related_facts(db, nueva, numbers)
    text = f"Corregido: {len(states)} datos antiguos dejaron de estar vigentes."
    if len(states) < len(numbers):
        text += (
            " Los de salud siguen vigentes como historia: si nunca fueron ciertos, repite con "
            "error=true."
        )
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


def goal_reminder(db: sqlite3.Connection) -> str:
    """Si Renzo habla de una meta, el camino correcto es manage_goals."""
    rows = db.execute("SELECT title FROM goals WHERE status = 'activo'").fetchall()
    titles = ", ".join(r["title"] for r in rows)
    return f" Si es sobre una meta ({titles}), usa manage_goals." if titles else ""


def entity_names(value: Any) -> list[str]:
    """Nombres de entidades; el modelo a veces los manda como objetos con `name`."""
    items = [item.get("name", "") if isinstance(item, dict) else item for item in value or []]
    return [str(name).strip() for name in items if str(name).strip()]


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


def about(row: sqlite3.Row, tema: str) -> bool:
    """Si un hecho trata del tema: por sus palabras, sus entidades o, en salud, sus sinónimos."""
    text = normalize(f"{row['content']} {row['names'] or ''}")
    if any(word in normalize(tema) for word in HEALTH_TOPICS):
        return "salud" in text or any(word in text for word in HEALTH_WORDS)
    return any(word in text for word in stems(tema) - {"histor"})


def with_chain(ids: set[int], rows: list[sqlite3.Row]) -> set[int]:
    """Agrega toda la cadena de reemplazos, hacia atrás y hacia adelante."""
    links = [(r["id"], r["superseded_by"]) for r in rows if r["superseded_by"]]
    result = set(ids)
    size = 0
    while size != len(result):
        size = len(result)
        for old, new in links:
            if old in result or new in result:
                result |= {old, new}
    return result


@server.tool(
    description=(
        "Historial en orden cronológico, incluidos los datos antiguos. Úsala para preguntas de "
        "historial o 'desde cuándo' (p. ej., tema salud). tema (o entidad), desde y hasta "
        "(AAAA-MM-DD) son opcionales. Cuenta un evento por línea, sin fusionarlos."
    ),
    annotations=ToolAnnotations(read_only_hint=True),
)
def linea_de_tiempo(tema: str = "", desde: str = "", hasta: str = "") -> str:
    with connect() as db:
        rows = db.execute(
            "SELECT o.id, o.content, o.valid, o.superseded_by, o.occurred_at,"
            " o.reason, (SELECT group_concat(e.norm || ' ' || e.type) FROM observation_entities l"
            " JOIN entities e ON e.id = l.entity_id WHERE l.observation_id = o.id) AS names"
            " FROM observations o ORDER BY o.occurred_at, o.id"
        ).fetchall()
    by_id = {r["id"]: r for r in rows}
    found = [r for r in rows if about(r, tema)] if tema.strip() else rows
    chosen_ids = with_chain({r["id"] for r in found}, rows)
    chosen = [
        by_id[n]
        for n in sorted(chosen_ids, key=lambda n: (by_id[n]["occurred_at"], n))
        if by_id[n]["occurred_at"] >= desde and (not hasta or by_id[n]["occurred_at"] <= hasta)
    ][-MAX_TIMELINE:]
    if not chosen:
        return "No hay nada anotado sobre eso en ese período."
    lines = []
    for row in chosen:
        state = "vigente" if row["valid"] else "ya no vigente"
        if row["superseded_by"]:
            state = f"reemplazado por o{row['superseded_by']}"
        if row["reason"] == "corregido":
            right = by_id[row["superseded_by"]]["content"]
            state = f"corregido, no fue así: {right}"
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
            return NO_GOALS
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


@server.tool(
    description="Muestra las sugerencias que Tama tiene pendientes para Renzo.",
    annotations=ToolAnnotations(read_only_hint=True),
)
def list_suggestions() -> str:
    with connect() as db:
        rows = db.execute("SELECT text FROM suggestions ORDER BY id DESC LIMIT 5").fetchall()
    return "\n".join(f"- {r['text']}" for r in rows) or "No hay sugerencias pendientes."


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


def open_goals() -> str:
    """Lo que sigue en curso: un objetivo pausado también existe."""
    found = [list_goals(state) for state in ("activo", "pausado")]
    return "\n\n".join(f for f in found if f != NO_GOALS) or NO_GOALS


@server.tool(
    description=(
        "Objetivos de Renzo: metas que quiere lograr. Úsala siempre que hable de una meta o cuente "
        "un paso hacia ella, antes de responder. accion: crear (titulo, detalle, fecha), avance "
        "(objetivo y avance), ver (estado) o cambiar (objetivo y titulo, detalle, fecha o estado "
        "activo, pausado o logrado)."
    )
)
def manage_goals(
    accion: str = "",
    objetivo: str = "",
    titulo: str = "",
    detalle: str = "",
    fecha: str = "",
    estado: str = "",
    avance: str = "",
    source: int | None = None,
) -> str:
    """Una sola herramienta a la vista: el modelo local no abre grupos de herramientas."""
    action = accion.strip().lower()
    if not action:
        action = "avance" if avance else "cambiar" if objetivo and (estado or titulo) else "ver"
    if action == "crear":
        return create_goal(titulo or objetivo, detalle, fecha)
    if action == "avance":
        return add_goal_progress(objetivo or titulo, avance or detalle, source)
    if action == "ver":
        return list_goals(estado) if estado else open_goals()
    if action == "cambiar":
        return update_goal(objetivo or titulo, titulo if objetivo else "", detalle, fecha, estado)
    return "Acción no válida: usa crear, avance, ver o cambiar."


# --- Reflexión: la usa tama.reflection directamente; el modelo de chat no ve estas funciones. ---


def similarity(a: str, b: str) -> float:
    """1 si un nombre contiene al otro por palabras; si no, parecido entre 0 y 1."""
    first, second = set(a.split()), set(b.split())
    if "de" not in first | second and (first <= second or second <= first):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def can_merge(first: sqlite3.Row, second: sqlite3.Row) -> bool:
    """Renzo nunca se fusiona, y dos entidades de distinto tipo tampoco ("objeto" es genérico)."""
    if OWNER in (first["norm"], second["norm"]):
        return False
    return first["type"] == second["type"] or "objeto" in (first["type"], second["type"])


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
                if score >= MIN_SIMILARITY and can_merge(first, second):
                    pairs[(first["id"], second["id"])] = score
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


def merge_entities(keep: str, drop: str, manual: bool = False) -> bool:
    """Funde `drop` en `keep`: hechos, alias, relaciones y objetivos. Nada se pierde.

    `manual` es la decisión de Renzo en la web: basta con que ninguna de las dos sea él.
    """
    with connect() as db:
        kept, dropped = find_entity(db, keep), find_entity(db, drop)
        if kept is None or dropped is None or kept["id"] == dropped["id"]:
            return False
        if OWNER in (kept["norm"], dropped["norm"]):
            return False
        automatic_ok = (
            can_merge(kept, dropped) and similarity(kept["norm"], dropped["norm"]) >= MIN_SIMILARITY
        )
        if not manual and not automatic_ok:
            return False
        new, old = kept["id"], dropped["id"]
        db.execute(
            "INSERT OR IGNORE INTO observation_entities (observation_id, entity_id)"
            " SELECT observation_id, ? FROM observation_entities WHERE entity_id = ?",
            (new, old),
        )
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
    """Entidades con dos o más hechos vigentes y novedades desde su última revisión."""
    with connect() as db:
        rows = db.execute(
            "SELECT e.id, e.name, MAX(o.created_at) AS last FROM entities e"
            " JOIN observation_entities l ON l.entity_id = e.id"
            " JOIN observations o ON o.id = l.observation_id AND o.valid = 1"
            " LEFT JOIN reviewed_entities r ON r.entity_id = e.id"
            " GROUP BY e.id HAVING COUNT(o.id) >= 2"
            " AND (MAX(r.reviewed_at) IS NULL OR MAX(o.created_at) > MAX(r.reviewed_at))"
            " ORDER BY last DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            observations = db.execute(
                "SELECT o.* FROM observations o"
                " JOIN observation_entities l ON l.observation_id = o.id"
                " WHERE l.entity_id = ? AND o.valid = 1 ORDER BY o.created_at, o.id",
                (row["id"],),
            ).fetchall()
            listed = [
                {"id": o["id"], "text": o["content"], "date": o["created_at"][:10]}
                for o in observations
            ]
            result.append({"id": row["id"], "name": row["name"], "observations": listed})
        return result


def retire_observations(pairs: list[tuple[int, int]]) -> int:
    """Marca hechos (antiguo, nuevo) como reemplazados; nunca lo que ya ocurrió."""
    changed = 0
    with connect() as db:
        for old_id, new_id in pairs:
            row = db.execute(
                "SELECT * FROM observations WHERE id = ? AND valid = 1", (old_id,)
            ).fetchone()
            if row is None or already_happened(db, row):
                continue
            db.execute(
                "UPDATE observations SET valid = 0, kind = 'estado', superseded_by = ?"
                " WHERE id = ?",
                (new_id, old_id),
            )
            changed += 1
            for entity_id in linked_entities(db, [old_id]):
                reindex(db, entity_id)
    return changed


def is_history(observation_id: int) -> bool:
    """Lo que ya ocurrió o es de salud no se retira solo: se le pregunta a Renzo."""
    with connect() as db:
        row = db.execute("SELECT * FROM observations WHERE id = ?", (observation_id,)).fetchone()
        return row is not None and already_happened(db, row)


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
    """Hechos y avances guardados entre dos instantes UTC (ISO), para el diario."""
    with connect() as db:
        observations = db.execute(
            "SELECT o.content, (SELECT group_concat(e.name, ', ')"
            " FROM observation_entities l JOIN entities e ON e.id = l.entity_id"
            " WHERE l.observation_id = o.id) AS names FROM observations o"
            " WHERE o.created_at >= ? AND o.created_at < ? ORDER BY o.id",
            (start, end),
        ).fetchall()
        progress = db.execute(
            "SELECT g.title, p.content FROM goal_progress p JOIN goals g ON g.id = p.goal_id"
            " WHERE p.created_at >= ? AND p.created_at < ? ORDER BY p.id",
            (start, end),
        ).fetchall()
    return [f"Dato sobre {r['names']}: {r['content']}" for r in observations] + [
        f"Avance en {r['title']}: {r['content']}" for r in progress
    ]


def repair_chains() -> int:
    """Deja una cadena por tema: cada hecho apunta a su sucesor, no todos al último.

    Arregla datos de antes de esta versión: reemplazos que apuntan a un mismo id y hechos no
    vigentes sin sucesor. Se usa una vez, con `tama memoria reparar`.
    """
    changed = 0
    with connect() as db:
        rows = db.execute("SELECT id, content, valid, superseded_by FROM observations").fetchall()
        for row in rows:
            if row["valid"] or row["superseded_by"]:
                continue
            wanted = stems(row["content"])
            later = [r for r in rows if r["id"] > row["id"] and wanted & stems(r["content"])]
            if later:
                db.execute(
                    "UPDATE observations SET superseded_by = ? WHERE id = ?",
                    (later[0]["id"], row["id"]),
                )
                changed += 1
        targets = db.execute(
            "SELECT superseded_by, COUNT(*) FROM observations WHERE superseded_by IS NOT NULL"
            " GROUP BY superseded_by HAVING COUNT(*) > 1"
        ).fetchall()
        for target, _ in targets:
            olds = db.execute(
                "SELECT id FROM observations WHERE superseded_by = ? ORDER BY id", (target,)
            ).fetchall()
            chain = [r["id"] for r in olds] + [target]
            for old_id, next_id in pairwise(chain):
                db.execute(
                    "UPDATE observations SET superseded_by = ? WHERE id = ?", (next_id, old_id)
                )
                changed += 1
    return changed


# --- Paneles de la web: leen y corrigen la memoria sin pasar por el modelo. ---


def list_cards(query: str = "") -> list[dict[str, Any]]:
    """Fichas con datos vigentes, o las que coinciden con la búsqueda."""
    with connect() as db:
        match = fts_query(query)
        if match:
            rows = db.execute(
                "SELECT e.* FROM search s JOIN entities e ON e.id = s.entity_id"
                " WHERE search MATCH ? ORDER BY rank",
                (match,),
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM entities ORDER BY norm").fetchall()
        cards = []
        for row in rows:
            count = db.execute(
                "SELECT COUNT(*) FROM observation_entities l JOIN observations o"
                " ON o.id = l.observation_id WHERE l.entity_id = ? AND o.valid = 1",
                (row["id"],),
            ).fetchone()[0]
            if count:
                cards.append(
                    {"id": row["id"], "name": row["name"], "type": row["type"], "facts": count}
                )
        return cards


def card(entity_id: int) -> dict[str, Any] | None:
    """Una ficha completa: datos vigentes con su historial."""
    with connect() as db:
        row = db.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            return None
        facts = db.execute(
            "SELECT o.* FROM observations o JOIN observation_entities l"
            " ON l.observation_id = o.id WHERE l.entity_id = ? AND o.valid = 1"
            " ORDER BY o.created_at DESC, o.id DESC",
            (entity_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "owner": row["norm"] == OWNER,
            "facts": [
                {
                    "id": f["id"],
                    "text": f["content"],
                    "when": shown_at(f["created_at"]),
                    "kind": f["kind"],
                    "before": earlier_versions(db, f["id"]).replace("\n  antes: ", ""),
                }
                for f in facts
            ],
        }


def replace_fact(observation_id: int, text: str) -> bool:
    """Renzo corrigió un dato: el nuevo lo reemplaza y el antiguo queda en el historial."""
    with connect() as db:
        row = db.execute(
            "SELECT id FROM observations WHERE id = ? AND valid = 1", (observation_id,)
        ).fetchone()
        if row is None or not text.strip():
            return False
        linked = linked_entities(db, [observation_id])
        db.execute("UPDATE observations SET valid = 0 WHERE id = ?", (observation_id,))
        add_obs(db, linked, [text], None)
        new_id = same_fact(db, resolve_dates(text.strip())[0])
        db.execute(
            "UPDATE observations SET kind = 'estado', superseded_by = ? WHERE id = ?",
            (new_id, observation_id),
        )
        for entity_id in linked:
            reindex(db, entity_id)
    return True


def split_card(entity_id: int, fact_ids: list[int], name: str) -> bool:
    """Mueve hechos de una ficha a otra nueva; deshace una fusión equivocada."""
    with connect() as db:
        source = db.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        if source is None or not name.strip() or not fact_ids:
            return False
        # Tras una fusión el nombre antiguo es un alias: al separar, deja de serlo.
        db.execute(
            "DELETE FROM aliases WHERE norm = ? AND entity_id = ?", (normalize(name), entity_id)
        )
        new_id, _ = upsert_entity(db, name, source["type"])
        for fact in fact_ids:
            moved = db.execute(
                "DELETE FROM observation_entities WHERE observation_id = ? AND entity_id = ?",
                (fact, entity_id),
            ).rowcount
            if moved:
                link(db, fact, {new_id})
        reindex(db, entity_id)
    return True


def goals_by_status() -> dict[str, list[dict[str, Any]]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM goals ORDER BY id DESC").fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {state: [] for state in GOAL_STATES}
        for row in rows:
            progress = db.execute(
                "SELECT content, created_at FROM goal_progress WHERE goal_id = ? ORDER BY id DESC",
                (row["id"],),
            ).fetchall()
            grouped[row["status"]].append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "detail": row["detail"],
                    "target": row["target_date"],
                    "progress": [
                        {"text": p["content"], "when": p["created_at"][:10]} for p in progress
                    ],
                }
            )
        return grouped


def pending_list() -> list[str]:
    with connect() as db:
        rows = db.execute(
            "SELECT text FROM suggestions WHERE mentioned_at IS NULL ORDER BY id"
        ).fetchall()
    return [r["text"] for r in rows]


# --- Motor proactivo. El modelo crea y cambia recordatorios; el resto es de tama.proactive. ---


def local_now() -> datetime:
    return datetime.now(ZoneInfo(os.environ.get("TAMA_TIMEZONE", "America/Santiago")))


def quiet_hours() -> tuple[int, int]:
    """El horario de no molestar; Tama lo pasa al servidor de memoria al iniciarlo."""
    start = os.environ.get("TAMA_QUIET_START", "22")
    end = os.environ.get("TAMA_QUIET_END", "8")
    return int(start), int(end)


def in_quiet_window(moment: datetime, start: int, end: int) -> bool:
    """No molestar: el tramo puede cruzar la medianoche."""
    if start > end:
        return moment.hour >= start or moment.hour < end
    return start <= moment.hour < end


def delivery_time(moment: datetime, urgent: bool) -> datetime:
    """Cuándo sale de verdad un recordatorio: si cae en no molestar, al terminar ese horario."""
    start, end = quiet_hours()
    if urgent or not in_quiet_window(moment, start, end):
        return moment
    release = moment.replace(hour=end, minute=0, second=0, microsecond=0)
    return release if release > moment else release + timedelta(days=1)


def day_label(moment: datetime) -> str:
    days = (moment.date() - local_now().date()).days
    names = {0: "hoy", 1: "mañana", -1: "ayer"}
    return f"{names.get(days, moment.strftime('%Y-%m-%d'))} {moment.strftime('%H:%M')}"


def resolve_when(text: str, now: datetime | None = None) -> datetime | None:
    """Fecha y hora de un recordatorio: "en 2 minutos", "mañana a las 9", "el viernes a las 10"."""
    now = now or local_now()
    plain = normalize(text)
    try:
        moment = datetime.fromisoformat(text.strip())
        return moment if moment.tzinfo else moment.replace(tzinfo=now.tzinfo)
    except ValueError:
        pass
    if "media hora" in plain:
        return now + timedelta(minutes=30)
    if "proxima semana" in plain or "semana que viene" in plain:
        return (now + timedelta(days=7)).replace(hour=9, minute=0, second=0, microsecond=0)
    relative = RELATIVE_TIME.search(plain)
    if relative:
        amount = relative.group(1) or "1"
        count = int(amount) if amount.isdigit() else NUMBERS[amount]
        unit = relative.group(2)
        units = {"min": "minutes", "hor": "hours", "dia": "days", "sem": "weeks"}
        return now + timedelta(**{units[unit[:3]]: count})
    clock = HOUR.search(plain)
    hour, minute = (int(clock.group(1)), int(clock.group(2) or 0)) if clock else (9, 0)
    if clock and clock.group(3) in ("pm", "de la tarde", "de la noche") and hour < 12:
        hour += 12
    day = now.date()
    for number, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", plain):
            day += timedelta(days=(number - day.weekday() - 1) % 7 + 1)
            break
    else:
        _, found = resolve_dates(plain.replace("manana", "mañana"), day)
        if found:
            day = date.fromisoformat(found)
        elif not clock:
            return None
    moment = now.replace(
        year=day.year, month=day.month, day=day.day, hour=hour, minute=minute, second=0
    )
    moment = moment.replace(microsecond=0)
    return moment + timedelta(days=1) if moment <= now else moment


@server.tool(
    description=(
        "Agenda un recordatorio: Tama avisará sola en ese momento. Lleva texto y cuando (como lo "
        "dijo Renzo: 'en 2 minutos', 'mañana a las 9', 'el viernes a las 18'). urgente=true solo "
        "si Renzo lo pide: avisa también de noche. Dile a Renzo la hora en que avisará."
    )
)
def crear_recordatorio(texto: str, cuando: str, urgente: bool = False) -> str:
    moment = resolve_when(cuando)
    if moment is None:
        raise ToolError(f"No entendí la fecha «{cuando}». Pídele a Renzo el día y la hora.")
    with connect() as db:
        number = db.execute(
            "INSERT INTO reminders (text, due_at, urgent, created_at) VALUES (?, ?, ?, ?)",
            (texto.strip(), moment.isoformat(timespec="seconds"), int(urgente), now()),
        ).lastrowid
    sent = delivery_time(moment, urgente)
    if sent == moment:
        return f"Recordatorio #{number}: avisaré {day_label(moment)}. {texto.strip()}."
    start, end = quiet_hours()
    return (
        f"Recordatorio #{number}: avisaré {day_label(sent)}, no {day_label(moment)}, porque es"
        f" horario de no molestar ({start}:00 a {end}:00). {texto.strip()}. Dile a Renzo esa"
        " hora y que, si lo quiere antes, puede pedirlo urgente."
    )


NOTICE_STATES = {
    "mostrado": "avisado",
    "ignorado": "avisado, sin respuesta",
    "listo": "listo",
    "pospuesto": "pospuesto",
    "silenciado": "no se repite",
}


def reminder_rows(day: date) -> list[dict[str, Any]]:
    """Los pendientes y todos los de `day`, con el estado de su aviso."""
    with connect() as db:
        rows = db.execute(
            "SELECT r.*, n.state AS notice_state, n.snooze_until FROM reminders r"
            " LEFT JOIN notices n ON n.key = 'recordatorio:' || r.id"
            " WHERE r.status = 'pendiente' OR substr(r.due_at, 1, 10) = ? ORDER BY r.due_at",
            (day.isoformat(),),
        ).fetchall()
    return [dict(r) for r in rows]


def reminder_state(row: dict[str, Any]) -> str:
    """El mismo estado en /recordatorios, /resumen y el panel Avisos."""
    if row["status"] == "pendiente":
        due = datetime.fromisoformat(row["due_at"])
        sent = delivery_time(due, bool(row["urgent"]))
        return "pendiente" if sent == due else f"pendiente, avisaré {day_label(sent)}"
    if row["status"] != "avisado":
        return row["status"]
    state = NOTICE_STATES.get(row["notice_state"], "avisado")
    if row["notice_state"] == "pospuesto" and row["snooze_until"]:
        state += f" hasta las {row['snooze_until'][11:16]}"
    return state


def reminder_line(row: dict[str, Any]) -> str:
    urgent = " (urgente)" if row["urgent"] else ""
    when = day_label(datetime.fromisoformat(row["due_at"]))
    return f"#{row['id']} {when} · {row['text']}{urgent} · {reminder_state(row)}"


@server.tool(
    description="Muestra los recordatorios pendientes y los de hoy, con su estado.",
    annotations=ToolAnnotations(read_only_hint=True),
)
def list_reminders() -> str:
    rows = reminder_rows(local_now().date())
    if not rows:
        return "No hay recordatorios pendientes ni para hoy."
    return "\n".join(reminder_line(r) for r in rows)


def pending_reminder(number: int) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            "SELECT r.*, NULL AS notice_state, NULL AS snooze_until FROM reminders r"
            " WHERE id = ? AND status = 'pendiente'",
            (number,),
        ).fetchone()
    return dict(row) if row else None


@server.tool(description="Cambia el texto o la fecha de un recordatorio, indicado por su número.")
def update_reminder(numero: int, texto: str = "", cuando: str = "") -> str:
    moment = resolve_when(cuando) if cuando else None
    if cuando and moment is None:
        raise ToolError(f"No entendí la fecha «{cuando}».")
    with connect() as db:
        row = db.execute(
            "SELECT * FROM reminders WHERE id = ? AND status = 'pendiente'", (numero,)
        ).fetchone()
        if row is None:
            raise ToolError(f"No hay un recordatorio pendiente #{numero}.")
        db.execute(
            "UPDATE reminders SET text = ?, due_at = ? WHERE id = ?",
            (
                texto.strip() or row["text"],
                moment.isoformat(timespec="seconds") if moment else row["due_at"],
                numero,
            ),
        )
    return f"Recordatorio #{numero} actualizado."


@server.tool(
    description="Cancela un recordatorio, indicado por su número.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def cancel_reminder(numero: int) -> str:
    with connect() as db:
        changed = db.execute(
            "UPDATE reminders SET status = 'cancelado' WHERE id = ? AND status = 'pendiente'",
            (numero,),
        ).rowcount
    return f"Recordatorio #{numero} cancelado." if changed else "No encontré ese recordatorio."


@server.tool(
    description=(
        "Cambia el nombre de una ficha cuando Renzo corrige cómo se llama algo (Rocky es Toby). "
        "El nombre anterior queda como alias."
    )
)
def rename_entity(actual: str, nuevo: str) -> str:
    return (
        "Listo." if rename_card(actual, nuevo) else "No encontré esa ficha o el nombre ya existe."
    )


def rename_card(current: str, new: str) -> bool:
    with connect() as db:
        row = find_entity(db, current)
        norm = normalize(new)
        taken = find_entity(db, new)
        if row is None or not norm or row["norm"] == OWNER:
            return False
        if taken is not None and taken["id"] != row["id"]:
            return False
        db.execute("DELETE FROM aliases WHERE norm = ?", (norm,))
        db.execute(
            "INSERT OR IGNORE INTO aliases (norm, entity_id) VALUES (?, ?)",
            (row["norm"], row["id"]),
        )
        db.execute(
            "UPDATE entities SET name = ?, norm = ? WHERE id = ?", (new.strip(), norm, row["id"])
        )
        reindex(db, row["id"])
    return True


def due_reminders(moment: datetime) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM reminders WHERE status = 'pendiente'").fetchall()
    return [dict(r) for r in rows if datetime.fromisoformat(r["due_at"]) <= moment]


def finish_reminder(reminder_id: int) -> None:
    with connect() as db:
        db.execute("UPDATE reminders SET status = 'avisado' WHERE id = ?", (reminder_id,))


def reminders_on(day: date) -> list[str]:
    return [
        f"{r['due_at'][11:16]} {r['text']} ({reminder_state(r)})"
        for r in reminder_rows(day)
        if r["due_at"].startswith(day.isoformat()) and r["status"] != "cancelado"
    ]


def stale_goals(day: date, stale_days: int) -> list[dict[str, Any]]:
    """Objetivos activos sin movimiento hace `stale_days` días o más."""
    with connect() as db:
        rows = db.execute("SELECT * FROM goals WHERE status = 'activo' ORDER BY id").fetchall()
    found = []
    for row in rows:
        days = (day - datetime.fromisoformat(row["updated_at"]).date()).days
        if days >= stale_days:
            found.append({"id": row["id"], "title": row["title"], "days": days})
    return found


def goal_notes(day: date, stale_days: int) -> list[str]:
    """Objetivos con fecha cercana o sin avance, para el resumen matutino."""
    with connect() as db:
        rows = db.execute(
            "SELECT title, target_date FROM goals WHERE status = 'activo' AND target_date != ''"
            " ORDER BY target_date"
        ).fetchall()
    notes = []
    for row in rows:
        left = (date.fromisoformat(row["target_date"]) - day).days
        if 0 <= left <= GOAL_SOON_DAYS:
            notes.append(f"Objetivo: {row['title']}, quedan {left} días")
    notes += [f"Objetivo sin avance: {g['title']}" for g in stale_goals(day, stale_days)]
    return notes


def save_notice(
    moment: datetime, kind: str, key: str, text: str, reason: str, notice_id: int | None = None
) -> dict[str, Any]:
    with connect() as db:
        stamp = moment.isoformat(timespec="seconds")
        if notice_id is None:
            notice_id = db.execute(
                "INSERT INTO notices (kind, key, text, reason, shown_at) VALUES (?, ?, ?, ?, ?)",
                (kind, key, text, reason, stamp),
            ).lastrowid
        else:
            db.execute(
                "UPDATE notices SET state = 'mostrado', shown_at = ?, snooze_until = NULL,"
                " repeats = repeats + 1 WHERE id = ?",
                (stamp, notice_id),
            )
        row = db.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
        return dict(row)


def list_notices(limit: int = 30) -> list[dict[str, Any]]:
    """Los últimos avisos; los de un recordatorio traen su hora y si era urgente."""
    with connect() as db:
        rows = db.execute(
            "SELECT n.*, r.due_at, r.urgent FROM notices n"
            " LEFT JOIN reminders r ON n.key = 'recordatorio:' || r.id"
            " ORDER BY n.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def open_reminders() -> list[dict[str, Any]]:
    """Recordatorios ya avisados que Renzo no ha respondido: quedan a la vista."""
    return [
        n
        for n in list_notices(100)
        if n["kind"] == "recordatorio" and n["state"] in ("mostrado", "ignorado")
    ]


def notice_count() -> int:
    with connect() as db:
        query = "SELECT COUNT(*) FROM notices WHERE state = 'mostrado'"
        return db.execute(query).fetchone()[0]


def notice_today(kind: str, day: date) -> bool:
    with connect() as db:
        row = db.execute(
            "SELECT 1 FROM notices WHERE kind = ? AND shown_at LIKE ?",
            (kind, f"{day.isoformat()}%"),
        ).fetchone()
    return row is not None


def unrequested_today(day: date) -> int:
    with connect() as db:
        return db.execute(
            "SELECT COUNT(*) FROM notices WHERE kind != 'recordatorio' AND shown_at LIKE ?",
            (f"{day.isoformat()}%",),
        ).fetchone()[0]


def asked_since(key: str, moment: datetime) -> bool:
    with connect() as db:
        row = db.execute(
            "SELECT 1 FROM notices WHERE key = ? AND shown_at >= ?",
            (key, moment.isoformat(timespec="seconds")),
        ).fetchone()
    return row is not None


def age_notices(moment: datetime) -> None:
    """Lo que se mostró hace tiempo sin respuesta cuenta como ignorado."""
    with connect() as db:
        db.execute(
            "UPDATE notices SET state = 'ignorado' WHERE state = 'mostrado' AND shown_at < ?",
            (moment.isoformat(timespec="seconds"),),
        )


def ignored_once() -> list[dict[str, Any]]:
    """Recordatorios ignorados que aún no se repiten: la repetición es una sola."""
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM notices WHERE state = 'ignorado' AND repeats = 0"
            " AND kind = 'recordatorio'"
        ).fetchall()
    return [dict(r) for r in rows]


def snoozed_notices(moment: datetime) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM notices WHERE state = 'pospuesto'").fetchall()
    return [dict(r) for r in rows if datetime.fromisoformat(r["snooze_until"]) <= moment]


def snooze_notice(notice_id: int, until: datetime) -> None:
    with connect() as db:
        db.execute(
            "UPDATE notices SET state = 'pospuesto', snooze_until = ? WHERE id = ?",
            (until.isoformat(timespec="seconds"), notice_id),
        )


def close_notice(notice_id: int) -> None:
    with connect() as db:
        db.execute("UPDATE notices SET state = 'listo' WHERE id = ?", (notice_id,))


def silence_notice(notice_id: int) -> None:
    with connect() as db:
        row = db.execute("SELECT key FROM notices WHERE id = ?", (notice_id,)).fetchone()
        if row:
            db.execute("INSERT OR IGNORE INTO silenced (key) VALUES (?)", (row["key"],))
            db.execute("UPDATE notices SET state = 'silenciado' WHERE id = ?", (notice_id,))


def is_silenced(key: str) -> bool:
    with connect() as db:
        return db.execute("SELECT 1 FROM silenced WHERE key = ?", (key,)).fetchone() is not None


def rate_notice(notice_id: int, value: str) -> None:
    with connect() as db:
        db.execute("UPDATE notices SET value = ? WHERE id = ?", (value, notice_id))


def weekly_report(day: date) -> dict[str, int]:
    """Bitácora de valor de los últimos 7 días."""
    since = (day - timedelta(days=7)).isoformat()
    with connect() as db:
        rows = db.execute(
            "SELECT state, value FROM notices WHERE shown_at >= ?", (since,)
        ).fetchall()
    return {
        "avisos": len(rows),
        "útiles": sum(r["value"] == "util" for r in rows),
        "molestos": sum(r["value"] == "molesto" for r in rows),
        "ignorados": sum(r["state"] == "ignorado" for r in rows),
    }


def mute_until(moment: datetime | None) -> None:
    with connect() as db:
        db.execute("DELETE FROM silence_until")
        if moment is not None:
            stamp = moment.isoformat(timespec="seconds")
            db.execute("INSERT INTO silence_until (until_at) VALUES (?)", (stamp,))


def muted_until(moment: datetime) -> bool:
    with connect() as db:
        row = db.execute("SELECT until_at FROM silence_until").fetchone()
    return row is not None and datetime.fromisoformat(row["until_at"]) > moment


def take_suggestion() -> str | None:
    text = next_suggestion()
    return None if text == NO_SUGGESTION else text


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
