from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from .process_guard import require_codex_stopped


LOG_DATABASES = ("logs_2.sqlite", "sqlite/logs_2.sqlite")


def _stats(path: Path) -> dict[str, int | str]:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        rows = int(connection.execute("SELECT COUNT(*) FROM logs").fetchone()[0])
        check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        connection.close()
    return {
        "path": str(path),
        "file_bytes": path.stat().st_size,
        "page_size": page_size,
        "page_count": page_count,
        "free_pages": free_pages,
        "reclaimable_bytes": free_pages * page_size,
        "estimated_compact_bytes": (page_count - free_pages) * page_size,
        "rows": rows,
        "integrity": check,
    }


def log_database_stats(codex_home: Path) -> list[dict[str, int | str]]:
    result = []
    for relative in LOG_DATABASES:
        path = codex_home.joinpath(*relative.split("/"))
        if path.is_file():
            result.append(_stats(path))
    return result


def _compact_one(path: Path) -> dict[str, int | str]:
    before = _stats(path)
    if before["integrity"] != "ok":
        raise sqlite3.DatabaseError(f"Refusing to compact damaged database: {path}")
    estimated = int(before["estimated_compact_bytes"])
    required_free = estimated + max(64 * 1024 * 1024, estimated // 10)
    available_free = shutil.disk_usage(path.parent).free
    if available_free < required_free:
        raise OSError(
            "Insufficient free disk space to compact "
            f"{path}: need at least {required_free} bytes, have {available_free} bytes"
        )
    token = uuid.uuid4().hex
    compact = path.with_name(path.name + f".compact-{token}")
    rollback = path.with_name(path.name + f".precompact-{token}")
    try:
        source = sqlite3.connect(path)
        try:
            source.execute("VACUUM INTO ?", (str(compact),))
        finally:
            source.close()
        compact_stats = _stats(compact)
        if compact_stats["integrity"] != "ok" or compact_stats["rows"] != before["rows"]:
            raise sqlite3.DatabaseError(f"Compacted database verification failed: {path}")
        os.replace(path, rollback)
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        try:
            os.replace(compact, path)
            after = _stats(path)
            if after["integrity"] != "ok" or after["rows"] != before["rows"]:
                raise sqlite3.DatabaseError(f"Replacement verification failed: {path}")
        except Exception:
            path.unlink(missing_ok=True)
            os.replace(rollback, path)
            raise
        rollback.unlink()
        return {
            "path": str(path),
            "before_bytes": int(before["file_bytes"]),
            "after_bytes": int(after["file_bytes"]),
            "released_bytes": int(before["file_bytes"]) - int(after["file_bytes"]),
            "rows": int(after["rows"]),
            "integrity": str(after["integrity"]),
        }
    finally:
        compact.unlink(missing_ok=True)


def compact_log_databases(codex_home: Path) -> list[dict[str, int | str]]:
    require_codex_stopped()
    results = []
    for relative in LOG_DATABASES:
        path = codex_home.joinpath(*relative.split("/"))
        if path.is_file():
            results.append(_compact_one(path))
    return results
