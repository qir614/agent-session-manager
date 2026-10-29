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


def _jsonl_records(path: Path):
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid {path.name} line {line_number}: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"Invalid {path.name} line {line_number}: expected an object")
            yield item, line


def count_jsonl_references(path: Path, deletion_ids: set[str], *, id_field: str = "id") -> int:
    if not path.is_file():
        return 0
    return sum(str(item.get(id_field) or "").lower() in deletion_ids
               for item, _ in _jsonl_records(path))


def filter_session_index(path: Path, deletion_ids: set[str], *, id_field: str = "id") -> int:
    if not path.is_file():
        return 0
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    removed = 0
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as target:
            for item, line in _jsonl_records(path):
                if str(item.get(id_field) or "").lower() in deletion_ids:
                    removed += 1
                else:
                    target.write(line if line.endswith("\n") else line + "\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return removed


def filter_global_state(path: Path, deletion_ids: set[str]) -> int:
    if not path.is_file():
        return 0
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Global state must be a JSON object.")
    removed = filter_global_state_data(data, deletion_ids)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    return removed


def filter_global_state_data(data: dict, deletion_ids: set[str]) -> int:
    """Filter verified local state layouts, preserving project definitions."""
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
        "thread-project-membership-host-ids",
        "thread-workspace-root-hints",
        "electron-remote-hosted-pip-task-visibility-state",
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
        for key in list(atoms):
            if key.startswith("thread-tab-routes-v1:") and key.removeprefix("thread-tab-routes-v1:").lower() in deletion_ids:
                del atoms[key]
                removed += 1
        for map_name in ("heartbeat-thread-permissions-by-id", "prompt-history", "thread-descriptions-v1"):
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

    migrations = data.get("app-server-projects-migration-by-host")
    if isinstance(migrations, dict):
        for host, entry in migrations.items():
            if not str(host).startswith("local:") or not isinstance(entry, dict):
                continue
            values = entry.get("pendingThreadAssignmentIds")
            if isinstance(values, list):
                kept = [v for v in values if str(v).lower() not in deletion_ids]
                removed += len(values) - len(kept)
                entry["pendingThreadAssignmentIds"] = kept
    pinned_migrations = data.get("app-server-migrated-pinned-thread-ids-by-host")
    if isinstance(pinned_migrations, dict):
        for host, values in list(pinned_migrations.items()):
            if str(host).startswith("local:") and isinstance(values, list):
                kept = [value for value in values if str(value).lower() not in deletion_ids]
                removed += len(values) - len(kept)
                pinned_migrations[host] = kept
    read_state = data.get("electron-thread-read-state-v1")
    identities = read_state.get("unreadByIdentity") if isinstance(read_state, dict) else None
    if isinstance(identities, dict):
        for hosts in identities.values():
            if not isinstance(hosts, dict):
                continue
            for host, values in list(hosts.items()):
                if str(host).startswith("local:") and isinstance(values, list):
                    kept = [v for v in values if str(v).lower() not in deletion_ids]
                    removed += len(values) - len(kept)
                    hosts[host] = kept
    return removed


def global_reference_ids(value):
    """Exact UUID keys/values only; never match arbitrary prompt substrings."""
    import re
    uuid = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
    found = []
    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                identity = str(key).removeprefix("thread-tab-routes-v1:")
                if uuid.fullmatch(identity):
                    found.append(identity.lower())
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and uuid.fullmatch(item):
            found.append(item.lower())
    visit(value)
    return found


def validate_global_state(home: Path, deletion_ids: set[str]) -> None:
    for filename in (".codex-global-state.json", ".codex-global-state.json.bak"):
        path = home / filename
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError(f"Global state must be an object: {filename}")
        filter_global_state_data(data, deletion_ids)
        if deletion_ids.intersection(global_reference_ids(data)):
            raise RuntimeError(f"Cleanup preview blocked: unsupported global state references in {filename}")


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
        known = {"threads", "thread_spawn_edges", "thread_dynamic_tools", "thread_attachments", "guardian_review_feedback", "thread_read_receipts"}
        unsupported: list[str] = []
        for table in tables - known:
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            for column in columns:
                if not _is_thread_column(table, column):
                    continue
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
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("BEGIN IMMEDIATE")
        for table in ("guardian_review_feedback", "thread_read_receipts"):
            if table in tables:
                cursor = connection.execute(f'DELETE FROM "{table}" WHERE thread_id IN ({placeholders})', values)
                counts[table] = max(cursor.rowcount, 0)
        if "thread_attachments" in tables:
            cursor = connection.execute(
                f"DELETE FROM thread_attachments WHERE thread_id IN ({placeholders})", values
            )
            counts["thread_attachments"] = max(cursor.rowcount, 0)
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
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise sqlite3.DatabaseError(f"SQLite verification failed after secure deletion: {path}")
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


SQLITE_REFERENCE_STORES: dict[str, tuple[tuple[str, str], ...]] = {
    "thread_history_1.sqlite": (
        ("thread_realtime_items", "thread_id"),
        ("thread_items", "thread_id"),
        ("thread_turns", "thread_id"),
        ("thread_history_projection_state", "thread_id"),
    ),
    "goals_1.sqlite": (
        ("thread_goal_continuation_deferrals", "thread_id"),
        ("thread_goals", "thread_id"),
    ),
    "queue_1.sqlite": (("queued_items", "thread_id"), ("queued_thread_revisions", "thread_id")),
    "memories_1.sqlite": (("stage1_outputs", "thread_id"),),
    "sqlite/codex-history-snapshots-dev.db": (
        ("app_server_history_snapshots", "thread_id"),
    ),
    "sqlite/codex-thread-summaries-dev.db": (
        ("thread_turn_summaries", "thread_id"),
    ),
    "sqlite/state_5.sqlite": (
        ("guardian_review_feedback", "thread_id"),
        ("thread_read_receipts", "thread_id"),
        ("thread_attachments", "thread_id"),
        ("thread_dynamic_tools", "thread_id"),
        ("thread_spawn_edges", "parent_thread_id"),
        ("thread_spawn_edges", "child_thread_id"),
        ("threads", "id"),
    ),
    "logs_2.sqlite": (("logs", "thread_id"),),
    "sqlite/logs_2.sqlite": (("logs", "thread_id"),),
}


PRIMARY_REFERENCES = (
    ("guardian_review_feedback", "thread_id"), ("thread_read_receipts", "thread_id"),
    ("thread_attachments", "thread_id"), ("thread_dynamic_tools", "thread_id"),
    ("thread_spawn_edges", "parent_thread_id"), ("thread_spawn_edges", "child_thread_id"), ("threads", "id"),
)
CATALOG_REFERENCES = (
    ("thread_timeline_ledger", "thread_id"), ("inbox_items", "thread_id"),
    ("automation_runs", "thread_id"), ("local_thread_catalog_scan_entries", "thread_id"),
    ("live_visualization_suggestions", "thread_id"), ("local_thread_catalog", "thread_id"),
)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_thread_column(table: str, column: str) -> bool:
    return column == "thread_id" or column.endswith("_thread_id") or (table == "threads" and column == "id")


def reference_stores(home: Path) -> dict[str, tuple[tuple[str, str], ...]]:
    stores = {**SQLITE_REFERENCE_STORES, "state_5.sqlite": PRIMARY_REFERENCES,
              "sqlite/codex-dev.db": CATALOG_REFERENCES}
    # 新增存储也必须参与只读检查，不能因为文件名升级就跳过核查。
    for path in [*home.glob("*.sqlite"), *(home / "sqlite").glob("*.db"), *(home / "sqlite").glob("*.sqlite")]:
        stores.setdefault(path.relative_to(home).as_posix(), ())
    return stores


def _schema_references(connection, path, references):
    known = set(references)
    result = []
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({_quote(table)})")}
        missing = {column for name, column in known if name == table} - columns
        if missing:
            raise ValueError(f"Unsupported SQLite schema: {path.name}:{table} missing {','.join(sorted(missing))}")
        for column in sorted(columns):
            if (table, column) in known or _is_thread_column(table, column):
                local = " AND host_id='local'" if path.name == "codex-dev.db" and "host_id" in columns else ""
                result.append((table, column, local, (table, column) in known))
    return result


def validate_sqlite_references(home: Path, deletion_ids: set[str]) -> None:
    if not deletion_ids:
        return
    placeholders = ",".join("?" for _ in deletion_ids)
    for relative, references in reference_stores(home).items():
        path = home / relative
        if not path.is_file():
            continue
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            for table, column, local, known in _schema_references(connection, path, references):
                if not known and connection.execute(
                    f"SELECT 1 FROM {_quote(table)} WHERE {_quote(column)} IN ({placeholders}){local} LIMIT 1", tuple(deletion_ids)
                ).fetchone():
                    raise ValueError(f"Unsupported SQLite references prevent safe deletion: {relative}:{table}.{column}")
        finally:
            connection.close()


def _delete_known_references(
    path: Path, deletion_ids: set[str], references: tuple[tuple[str, str], ...], *, allowed=None
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
        for table, column, local, known in _schema_references(connection, path, allowed or references):
            if not known and connection.execute(
                f"SELECT 1 FROM {_quote(table)} WHERE {_quote(column)} IN ({placeholders}){local} LIMIT 1", values
            ).fetchone():
                raise sqlite3.DatabaseError(f"Unsupported SQLite references prevent safe deletion: {table}.{column}")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("BEGIN IMMEDIATE")
        # Child tables are deliberately listed before parent tables.
        for table, column in references:
            if table not in tables:
                continue
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if column not in columns:
                continue
            local = " AND host_id='local'" if path.name == "codex-dev.db" and "host_id" in columns else ""
            cursor = connection.execute(
                f'DELETE FROM "{table}" WHERE "{column}" IN ({placeholders}){local}', values
            )
            key = f"{table}.{column}"
            counts[key] = counts.get(key, 0) + max(cursor.rowcount, 0)
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise sqlite3.DatabaseError(f"SQLite verification failed after secure deletion: {path}")
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _delete_catalog_references(path: Path, deletion_ids: set[str]) -> dict[str, int]:
    references = tuple(pair for pair in CATALOG_REFERENCES if pair[0] != "local_thread_catalog")
    counts = _delete_known_references(path, deletion_ids, references, allowed=CATALOG_REFERENCES)
    if not path.is_file() or not deletion_ids:
        return counts
    connection = sqlite3.connect(path, timeout=15)
    try:
        placeholders = ",".join("?" for _ in deletion_ids)
        values = tuple(sorted(deletion_ids))
        connection.execute("PRAGMA secure_delete=ON")
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
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise sqlite3.DatabaseError(f"SQLite verification failed after secure deletion: {path}")
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def delete_all_sqlite_references(
    codex_home: Path, deletion_ids: set[str], *, include_logs: bool = True
) -> dict[str, dict[str, int]]:
    validate_sqlite_references(codex_home, deletion_ids)
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


def delete_log_references(
    codex_home: Path, deletion_ids: set[str]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for relative in ("logs_2.sqlite", "sqlite/logs_2.sqlite"):
        path = codex_home.joinpath(*relative.split("/"))
        if path.is_file():
            result[relative] = _delete_known_references(
                path, deletion_ids, (("logs", "thread_id"),)
            )
    return result


def count_all_sqlite_references(
    codex_home: Path, deletion_ids: set[str], *, include_logs: bool = True
) -> dict[str, dict[str, int]]:
    if not deletion_ids:
        return {}
    stores = reference_stores(codex_home)
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
            counts: dict[str, int] = {}
            for table, column, extra, _known in _schema_references(connection, path, references):
                count = int(connection.execute(
                    f'SELECT COUNT(*) FROM {_quote(table)} WHERE {_quote(column)} IN ({placeholders}){extra}',
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
    for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
        path = codex_home / name
        if path.is_file():
            count = sum(sid in deletion_ids for sid in global_reference_ids(json.loads(path.read_text(encoding="utf-8-sig"))))
            if count:
                found[name] = count
    for filename, field in (("session_index.jsonl", "id"), ("history.jsonl", "session_id")):
        count = count_jsonl_references(codex_home / filename, deletion_ids, id_field=field)
        if count:
            found[filename] = count
    for relative, counts in count_all_sqlite_references(codex_home, deletion_ids, include_logs=include_logs).items():
        for reference, count in counts.items():
            found[f"{relative}:{reference}"] = count
    return found
