"""Servidor MCP de notas: SQLite con búsqueda de texto completo (FTS5) en `notas.db`.

Se lanza por stdio: `python -m tama.mcp_servers.notas`. La carpeta de datos sale de TAMA_DATA_DIR.
"""

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, body, content='notes', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO notes_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
"""
MAX_BODY_IN_RESULTS = 800

server = MCPServer("notas", instructions="Notas personales de Renzo.")


def db_path() -> Path:
    return Path(os.environ.get("TAMA_DATA_DIR", "data")) / "notas.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def _fts_query(text: str) -> str:
    """Convierte texto libre en una consulta FTS5 segura: cada palabra como prefijo."""
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)


def _format(row: sqlite3.Row) -> str:
    body = row["body"]
    if len(body) > MAX_BODY_IN_RESULTS:
        body = body[:MAX_BODY_IN_RESULTS] + " …"
    return f"[nota {row['id']}] {row['title']} (actualizada {row['updated']})\n{body}"


@server.tool(
    description=(
        "Guarda una nota nueva con título y contenido. Úsala cada vez que Renzo pida anotar, "
        "apuntar o guardar algo; no basta con decir que lo anotaste."
    )
)
def create_note(title: str, content: str) -> str:
    with connect() as db:
        now = _now()
        cursor = db.execute(
            "INSERT INTO notes (title, body, created, updated) VALUES (?, ?, ?, ?)",
            (title.strip(), content.strip(), now, now),
        )
    return f"Nota {cursor.lastrowid} creada: {title.strip()}"


@server.tool(
    description=(
        "Busca en las notas de Renzo y devuelve su contenido. Úsala de inmediato cuando Renzo "
        "pregunte por algo que pudo haber anotado (citas, planes, compras, ideas), sin preguntar "
        "antes. Sin consulta, devuelve las notas más recientes."
    ),
    annotations=ToolAnnotations(read_only_hint=True),
)
def search_notes(query: str = "", limit: int = 5) -> str:
    limit = max(1, min(limit, 20))
    with connect() as db:
        match = _fts_query(query)
        if match:
            rows = db.execute(
                "SELECT notes.* FROM notes_fts JOIN notes ON notes.id = notes_fts.rowid"
                " WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM notes ORDER BY updated DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
    if not rows:
        return "No hay notas que coincidan." if match else "Todavía no hay notas."
    return "\n\n".join(_format(r) for r in rows)


@server.tool(
    description="Cambia el título y/o el contenido de una nota existente (reemplaza lo anterior).",
    annotations=ToolAnnotations(destructive_hint=True),
)
def update_note(note_id: int, title: str | None = None, content: str | None = None) -> str:
    with connect() as db:
        row = db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return f"No existe la nota {note_id}."
        db.execute(
            "UPDATE notes SET title = ?, body = ?, updated = ? WHERE id = ?",
            (
                (title or row["title"]).strip(),
                (content if content is not None else row["body"]).strip(),
                _now(),
                note_id,
            ),
        )
    return f"Nota {note_id} actualizada."


@server.tool(
    description="Borra una nota para siempre.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def delete_note(note_id: int) -> str:
    with connect() as db:
        deleted = db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount
    return f"Nota {note_id} borrada." if deleted else f"No existe la nota {note_id}."


if __name__ == "__main__":
    server.run("stdio")
