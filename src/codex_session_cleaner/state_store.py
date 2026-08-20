from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def filter_session_index(path: Path, deletion_ids: set[str]) -> int:
    if not path.is_file():
        return 0
    kept: list[str] = []
    removed = 0
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid session_index.jsonl line {line_number}: {exc}") from exc
            if str(item.get("id") or "").lower() in deletion_ids:
                removed += 1
            else:
                kept.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    _atomic_write(path, "\n".join(kept) + ("\n" if kept else ""))
    return removed


def filter_global_state(path: Path, deletion_ids: set[str]) -> int:
    if not path.is_file():
        return 0
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Global state must be a JSON object.")
    removed = 0

    projectless = data.get("projectless-thread-ids")
    if isinstance(projectless, list):
        before = len(projectless)
        data["projectless-thread-ids"] = [
            value for value in projectless if str(value).lower() not in deletion_ids
        ]
        removed += before - len(data["projectless-thread-ids"])

    output_dirs = data.get("thread-projectless-output-directories")
    if isinstance(output_dirs, dict):
        for key in list(output_dirs):
            if key.lower() in deletion_ids:
                del output_dirs[key]
                removed += 1

    for map_name in (
        "thread-project-assignments",
        "thread-writable-roots",
    ):
        mapping = data.get(map_name)
        if isinstance(mapping, dict):
            for key in list(mapping):
                if str(key).lower() in deletion_ids:
                    del mapping[key]
                    removed += 1

    sidebar_orders = data.get("sidebar-project-thread-orders")
    if isinstance(sidebar_orders, dict):
        for value in sidebar_orders.values():
            if not isinstance(value, dict) or not isinstance(value.get("threadIds"), list):
                continue
            before = len(value["threadIds"])
            value["threadIds"] = [
                item for item in value["threadIds"]
                if str(item).lower() not in deletion_ids
            ]
            removed += before - len(value["threadIds"])

    atoms = data.get("electron-persisted-atom-state")
    if isinstance(atoms, dict):
        for map_name in ("heartbeat-thread-permissions-by-id", "prompt-history"):
            mapping = atoms.get(map_name)
            if isinstance(mapping, dict):
                for key in list(mapping):
                    if key.lower() in deletion_ids:
                        del mapping[key]
                        removed += 1
        bindings = atoms.get("client-thread-bindings-v1")
        if isinstance(bindings, dict):
            for key, value in list(bindings.items()):
                if isinstance(value, str) and value.lower() in deletion_ids:
                    del bindings[key]
                    removed += 1

    _atomic_write(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    return removed


def delete_sqlite_rows(path: Path, deletion_ids: set[str]) -> dict[str, int]:
    if not path.is_file():
        return {}
    connection = sqlite3.connect(path, timeout=10)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"SQLite integrity check failed: {integrity}")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        known = {"threads", "thread_spawn_edges", "thread_dynamic_tools"}
        unsupported: list[str] = []
        for table in tables - known:
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            for column in columns & {"thread_id", "parent_thread_id", "child_thread_id"}:
                placeholders = ",".join("?" for _ in deletion_ids)
                count = connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({placeholders})',
                    tuple(deletion_ids),
                ).fetchone()[0]
                if count:
                    unsupported.append(f"{table}.{column}:{count}")
        if unsupported:
            raise sqlite3.DatabaseError(
                "Unsupported SQLite references prevent safe deletion: " + ", ".join(unsupported)
            )

        placeholders = ",".join("?" for _ in deletion_ids)
        values = tuple(deletion_ids)
        counts: dict[str, int] = {}
        connection.execute("BEGIN IMMEDIATE")
        if "thread_dynamic_tools" in tables:
            cursor = connection.execute(
                f"DELETE FROM thread_dynamic_tools WHERE thread_id IN ({placeholders})", values
            )
            counts["thread_dynamic_tools"] = max(cursor.rowcount, 0)
        if "thread_spawn_edges" in tables:
            cursor = connection.execute(
                f"DELETE FROM thread_spawn_edges WHERE parent_thread_id IN ({placeholders}) "
                f"OR child_thread_id IN ({placeholders})",
                values + values,
            )
            counts["thread_spawn_edges"] = max(cursor.rowcount, 0)
        if "threads" in tables:
            cursor = connection.execute(
                f"DELETE FROM threads WHERE id IN ({placeholders})", values
            )
            counts["threads"] = max(cursor.rowcount, 0)
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


SQLITE_REFERENCE_STORES: dict[str, tuple[tuple[str, str], ...]] = {
    "thread_history_1.sqlite": (
        ("thread_items", "thread_id"),
        ("thread_turns", "thread_id"),
        ("thread_history_projection_state", "thread_id"),
    ),
    "goals_1.sqlite": (
        ("thread_goal_continuation_deferrals", "thread_id"),
        ("thread_goals", "thread_id"),
    ),
    "queue_1.sqlite": (("queued_items", "thread_id"),),
    "memories_1.sqlite": (("stage1_outputs", "thread_id"),),
    "sqlite/codex-history-snapshots-dev.db": (
        ("app_server_history_snapshots", "thread_id"),
    ),
    "sqlite/codex-thread-summaries-dev.db": (
        ("thread_turn_summaries", "thread_id"),
    ),
    "sqlite/state_5.sqlite": (
        ("thread_dynamic_tools", "thread_id"),
        ("thread_spawn_edges", "parent_thread_id"),
        ("thread_spawn_edges", "child_thread_id"),
        ("threads", "id"),
    ),
    "logs_2.sqlite": (("logs", "thread_id"),),
    "sqlite/logs_2.sqlite": (("logs", "thread_id"),),
}


def _delete_known_references(
    path: Path, deletion_ids: set[str], references: tuple[tuple[str, str], ...]
) -> dict[str, int]:
    if not path.is_file() or not deletion_ids:
        return {}
    connection = sqlite3.connect(path, timeout=15)
    try:
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise sqlite3.DatabaseError(f"SQLite quick_check failed: {path}")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        placeholders = ",".join("?" for _ in deletion_ids)
        values = tuple(sorted(deletion_ids))
        counts: dict[str, int] = {}
        connection.execute("BEGIN IMMEDIATE")
        # Child tables are deliberately listed before parent tables.
        for table, column in references:
            if table not in tables:
                continue
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if column not in columns:
                continue
            cursor = connection.execute(
                f'DELETE FROM "{table}" WHERE "{column}" IN ({placeholders})', values
            )
            key = f"{table}.{column}"
            counts[key] = counts.get(key, 0) + max(cursor.rowcount, 0)
        connection.commit()
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _delete_catalog_references(path: Path, deletion_ids: set[str]) -> dict[str, int]:
    references = (
        ("thread_timeline_ledger", "thread_id"),
        ("inbox_items", "thread_id"),
        ("automation_runs", "thread_id"),
    )
    counts = _delete_known_references(path, deletion_ids, references)
    if not path.is_file() or not deletion_ids:
        return counts
    connection = sqlite3.connect(path, timeout=15)
    try:
        placeholders = ",".join("?" for _ in deletion_ids)
        values = tuple(sorted(deletion_ids))
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            f"DELETE FROM local_thread_catalog WHERE host_id='local' "
            f"AND thread_id IN ({placeholders})", values,
        )
        removed = max(cursor.rowcount, 0)
        counts["local_thread_catalog.thread_id"] = removed
        if removed:
            connection.execute(
                "UPDATE local_thread_catalog_metadata "
                "SET catalog_revision=catalog_revision+1 WHERE id=1"
            )
        connection.commit()
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def delete_all_sqlite_references(
    codex_home: Path, deletion_ids: set[str], *, include_logs: bool = True
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    primary = codex_home / "state_5.sqlite"
    if primary.is_file():
        result["state_5.sqlite"] = delete_sqlite_rows(primary, deletion_ids)
    for relative, references in SQLITE_REFERENCE_STORES.items():
        if not include_logs and relative.endswith("logs_2.sqlite"):
            continue
        path = codex_home.joinpath(*relative.split("/"))
        if path.is_file():
            result[relative] = _delete_known_references(path, deletion_ids, references)
    catalog = codex_home / "sqlite" / "codex-dev.db"
    if catalog.is_file():
        result["sqlite/codex-dev.db"] = _delete_catalog_references(catalog, deletion_ids)
    return result


def count_all_sqlite_references(
    codex_home: Path, deletion_ids: set[str], *, include_logs: bool = True
) -> dict[str, dict[str, int]]:
    if not deletion_ids:
        return {}
    stores = dict(SQLITE_REFERENCE_STORES)
    stores["state_5.sqlite"] = (
        ("thread_dynamic_tools", "thread_id"),
        ("thread_spawn_edges", "parent_thread_id"),
        ("thread_spawn_edges", "child_thread_id"),
        ("threads", "id"),
    )
    stores["sqlite/codex-dev.db"] = (
        ("thread_timeline_ledger", "thread_id"),
        ("inbox_items", "thread_id"),
        ("automation_runs", "thread_id"),
        ("local_thread_catalog", "thread_id"),
    )
    placeholders = ",".join("?" for _ in deletion_ids)
    values = tuple(sorted(deletion_ids))
    result: dict[str, dict[str, int]] = {}
    for relative, references in stores.items():
        if not include_logs and relative.endswith("logs_2.sqlite"):
            continue
        path = codex_home.joinpath(*relative.split("/"))
        if not path.is_file():
            continue
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise sqlite3.DatabaseError(f"SQLite quick_check failed: {path}")
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            counts: dict[str, int] = {}
            for table, column in references:
                if table not in tables:
                    continue
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if column not in columns:
                    continue
                extra = " AND host_id='local'" if table == "local_thread_catalog" else ""
                count = int(connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({placeholders}){extra}',
                    values,
                ).fetchone()[0])
                if count:
                    key = f"{table}.{column}"
                    counts[key] = counts.get(key, 0) + count
            if counts:
                result[relative] = counts
        finally:
            connection.close()
    return result


def verify_no_references(
    codex_home: Path, deletion_ids: set[str], *, include_logs: bool = True
) -> dict[str, int]:
    found: dict[str, int] = {}
    stores = dict(SQLITE_REFERENCE_STORES)
    stores["state_5.sqlite"] = (
        ("thread_dynamic_tools", "thread_id"),
        ("thread_spawn_edges", "parent_thread_id"),
        ("thread_spawn_edges", "child_thread_id"),
        ("threads", "id"),
    )
    stores["sqlite/codex-dev.db"] = (
        ("thread_timeline_ledger", "thread_id"),
        ("inbox_items", "thread_id"),
        ("automation_runs", "thread_id"),
        ("local_thread_catalog", "thread_id"),
    )
    if not deletion_ids:
        return found
    placeholders = ",".join("?" for _ in deletion_ids)
    values = tuple(sorted(deletion_ids))
    for relative, references in stores.items():
        if not include_logs and relative.endswith("logs_2.sqlite"):
            continue
        path = codex_home.joinpath(*relative.split("/"))
        if not path.is_file():
            continue
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            for table, column in references:
                if table not in tables:
                    continue
                extra = " AND host_id='local'" if table == "local_thread_catalog" else ""
                count = int(connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({placeholders}){extra}',
                    values,
                ).fetchone()[0])
                if count:
                    found[f"{relative}:{table}.{column}"] = count
        finally:
            connection.close()
    return found
