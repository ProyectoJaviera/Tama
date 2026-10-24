from datetime import UTC, datetime

import pytest

from tama.events import MIGRATIONS, EventStore


@pytest.fixture
def clock():
    state = {"now": datetime(2026, 10, 24, 12, tzinfo=UTC)}
    return state


@pytest.fixture
def store(tmp_path, clock):
    s = EventStore(tmp_path / "datos" / "events.db", now=lambda: clock["now"])
    yield s
    s.close()


def test_creates_db_in_wal_mode_with_latest_schema(tmp_path, store):
    assert (tmp_path / "datos" / "events.db").exists()
    assert store.schema_version == len(MIGRATIONS)
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_reopening_does_not_rerun_migrations(tmp_path, store):
    store.log("user_msg", "hola")
    store.close()
    again = EventStore(tmp_path / "datos" / "events.db")
    assert len(again.rows()) == 1
    again.close()


def test_logs_turn_fields(store):
    store.log("assistant_msg", "¡Hola!", "nube", "gemma4", 120, 30, 800, 2100)
    row = store.rows("assistant_msg")[0]
    assert (row["provider"], row["model"], row["tokens_in"], row["tokens_out"]) == (
        "nube",
        "gemma4",
        120,
        30,
    )
    assert (row["ttft_ms"], row["total_ms"], row["content"]) == (800, 2100, "¡Hola!")
    assert row["ts"].startswith("2026-10-24T12:00:00")


def test_rejects_unknown_event_type(store):
    with pytest.raises(Exception, match="CHECK"):
        store.log("otra_cosa")


def test_month_tokens_only_counts_current_month_and_provider(store, clock):
    clock["now"] = datetime(2026, 9, 30, 23, tzinfo=UTC)
    store.log("assistant_msg", "viejo", "claude", tokens_in=1000, tokens_out=1000)
    clock["now"] = datetime(2026, 10, 1, 1, tzinfo=UTC)
    store.log("assistant_msg", "a", "claude", tokens_in=100, tokens_out=20)
    store.log("assistant_msg", "b", "claude", tokens_in=50, tokens_out=10)
    store.log("assistant_msg", "c", "nube", tokens_in=999, tokens_out=999)

    assert store.month_tokens("claude") == (150, 30)
    assert store.month_tokens("local") == (0, 0)
