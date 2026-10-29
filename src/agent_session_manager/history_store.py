"""Validate locally observed paginated storage before independent mutation."""
from __future__ import annotations
import sqlite3
from contextlib import closing
from pathlib import Path

REQUIRED = {
    "thread_turns": {"thread_id", "turn_id", "rollout_ordinal"},
    "thread_items": {"thread_id", "turn_id", "item_id", "item_json", "rollout_ordinal"},
    "thread_history_projection_state": {"thread_id", "next_rollout_byte_offset", "next_rollout_ordinal"},
}
OPTIONAL = {"thread_realtime_items": {"thread_id", "item_id", "item_json", "rollout_ordinal"}}


def validate_paginated_history(home: Path) -> None:
    path = home / "thread_history_1.sqlite"
    if not path.is_file():
        raise ValueError("Unsupported history: paginated database is missing.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Unsupported history: database integrity check failed.")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED.keys() <= tables:
            raise ValueError("Unsupported history: required paginated tables are missing.")
        for table in tables:
            # Quote identifiers from the schema, never treat them as SQL syntax.
            quoted = table.replace('"', '""')
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{quoted}")')}
            required = REQUIRED.get(table, OPTIONAL.get(table))
            if required is not None:
                if not required <= columns:
                    raise ValueError(f"Unsupported history schema: {table}")
                foreign_threads = {c for c in columns if c.endswith("thread_id") and c != "thread_id"}
                if foreign_threads:
                    raise ValueError(f"Unsupported shared history references: {table}")
            elif table not in {"_sqlx_migrations", "sqlite_sequence"}:
                raise ValueError(f"Unsupported history table: {table}")
