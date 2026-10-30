"""Registro de episodios en `events.db` (SQLite en modo WAL)."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# Cada migración lleva la base de la versión i a la i+1 (PRAGMA user_version).
MIGRATIONS = [
    """
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        type TEXT NOT NULL
            CHECK (type IN ('user_msg', 'assistant_msg', 'provider_switch', 'error')),
        provider TEXT,
        model TEXT,
        tokens_in INTEGER,
        tokens_out INTEGER,
        ttft_ms INTEGER,
        total_ms INTEGER,
        content TEXT
    );
    CREATE INDEX events_provider_ts ON events (provider, ts);
    """,
    """
    CREATE VIRTUAL TABLE events_fts USING fts5(
        content, content='events', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER events_ai AFTER INSERT ON events
        WHEN new.type IN ('user_msg', 'assistant_msg') AND new.content IS NOT NULL
    BEGIN
        INSERT INTO events_fts(rowid, content) VALUES (new.id, new.content);
    END;
    INSERT INTO events_fts(rowid, content)
        SELECT id, content FROM events
        WHERE type IN ('user_msg', 'assistant_msg') AND content IS NOT NULL;
    """,
]


def _now() -> datetime:
    return datetime.now(UTC)


class EventStore:
    def __init__(self, path: Path | str, now=_now) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._now = now
        self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            with self._db:
                self._db.executescript(script)
                self._db.execute(f"PRAGMA user_version = {i}")

    @property
    def schema_version(self) -> int:
        return self._db.execute("PRAGMA user_version").fetchone()[0]

    def log(
        self,
        type: str,
        content: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        ttft_ms: int | None = None,
        total_ms: int | None = None,
    ) -> int:
        """Registra un evento y devuelve su id."""
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO events (ts, type, provider, model, tokens_in, tokens_out, ttft_ms,"
                " total_ms, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._now().isoformat(timespec="seconds"),
                    type,
                    provider,
                    model,
                    tokens_in,
                    tokens_out,
                    ttft_ms,
                    total_ms,
                    content,
                ),
            )
        return cursor.lastrowid

    def last_conversation(self, max_messages: int = 3) -> tuple[str, list[str]] | None:
        """Fecha y últimos mensajes de Renzo de la conversación anterior, si hay alguna."""
        rows = self._db.execute(
            "SELECT ts, content FROM events WHERE type = 'user_msg' ORDER BY id DESC LIMIT ?",
            (max_messages,),
        ).fetchall()
        if not rows:
            return None
        return rows[0][0][:10], [r[1] for r in reversed(rows)]

    def month_tokens(self, provider: str) -> tuple[int, int]:
        """Tokens de entrada y salida del mes en curso (UTC) para un proveedor."""
        month = self._now().strftime("%Y-%m")
        row = self._db.execute(
            "SELECT COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) FROM events"
            " WHERE provider = ? AND type = 'assistant_msg' AND substr(ts, 1, 7) = ?",
            (provider, month),
        ).fetchone()
        return row[0], row[1]

    def rows(self, type: str | None = None) -> list[sqlite3.Row]:
        self._db.row_factory = sqlite3.Row
        try:
            query = "SELECT * FROM events" + (" WHERE type = ?" if type else "") + " ORDER BY id"
            return self._db.execute(query, (type,) if type else ()).fetchall()
        finally:
            self._db.row_factory = None

    def close(self) -> None:
        self._db.close()
