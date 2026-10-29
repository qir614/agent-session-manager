"""Post-delete verification for the declared local cleanup scope."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import CleanupPlan
from .state_store import reference_stores


def jsonl_records(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL entry must be an object: {path.name}:{number}")
            yield value


def exact_references(value: object, ids: set[str]) -> int:
    if isinstance(value, str):
        return int(value.lower() in ids)
    if isinstance(value, list):
        return sum(exact_references(item, ids) for item in value)
    if isinstance(value, dict):
        return sum(
            int(str(key).lower() in ids) + exact_references(item, ids)
            for key, item in value.items()
        )
    return 0


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def unsupported_sqlite_references(home: Path, ids: set[str]) -> dict[str, int]:
    """Detect exact IDs in undeclared columns; never guess how to delete them.

    Discovery is bounded to the home and its sqlite directory, excluding backups.
    Text embedded in blobs/JSON or arbitrary nested directories is outside scope.
    """
    if not ids:
        return {}
    stores = reference_stores(home)
    paths: set[Path] = set()
    for folder in (home, home / "sqlite"):
        if folder.is_dir():
            for pattern in ("*.sqlite", "*.db"):
                paths.update(folder.glob(pattern))
    found: dict[str, int] = {}
    placeholders = ",".join("?" for _ in ids)
    for path in sorted(paths):
        if path.is_symlink() or path.resolve().parent not in {home.resolve(), (home / "sqlite").resolve()}:
            raise ValueError(f"SQLite path is not a direct local file: {path}")
        relative = path.relative_to(home).as_posix()
        known = set(stores.get(relative, ()))
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError(f"Damaged SQLite store: {relative}")
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                columns = [row[1] for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]
                for column in columns:
                    if (table, column) in known:
                        continue
                    count = connection.execute(
                        f"SELECT COUNT(*) FROM {_quote(table)} "
                        f"WHERE {_quote(column)} COLLATE NOCASE IN ({placeholders})",
                        tuple(sorted(ids)),
                    ).fetchone()[0]
                    if count:
                        found[f"{relative}:{table}.{column}"] = int(count)
        finally:
            connection.close()
    return found


def verify_deleted_files_and_json(plan: CleanupPlan) -> dict[str, int]:
    home, ids = plan.codex_home, set(plan.deletion_ids)
    found: dict[str, int] = {}
    for item in plan.items:
        if item.path is not None and item.path.exists():
            found[f"rollout:{item.relative_path}"] = 1
    # Rescan all rollout identities, including renamed or newly created files.
    for folder in (home / "sessions", home / "archived_sessions"):
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.jsonl"):
            for event in jsonl_records(path):
                payload = event.get("payload")
                if event.get("type") == "session_meta" and isinstance(payload, dict):
                    sid = str(payload.get("id") or payload.get("session_id") or "").lower()
                    if sid in ids:
                        found[f"rollout:{path.relative_to(home)}"] = 1
                    break
    for name, field in (("session_index.jsonl", "id"), ("history.jsonl", "session_id")):
        count = sum(str(item.get(field) or "").lower() in ids for item in jsonl_records(home / name))
        if count:
            found[name] = count
    for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
        path = home / name
        if path.is_file():
            count = exact_references(json.loads(path.read_text(encoding="utf-8-sig")), ids)
            if count:
                found[name] = count
    found.update(unsupported_sqlite_references(home, ids))
    return found
