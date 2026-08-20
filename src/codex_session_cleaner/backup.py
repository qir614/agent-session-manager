from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import __version__

from .dependency_graph import descendants
from .models import CleanupPlan, ScanResult


JSON_STATE_FILE_NAMES = (
    "session_index.jsonl",
    ".codex-global-state.json",
    ".codex-global-state.json.bak",
)

SQLITE_STATE_FILE_NAMES = (
    "state_5.sqlite",
    "thread_history_1.sqlite",
    "goals_1.sqlite",
    "queue_1.sqlite",
    "memories_1.sqlite",
    "sqlite/codex-dev.db",
    "sqlite/codex-history-snapshots-dev.db",
    "sqlite/codex-thread-summaries-dev.db",
    "sqlite/state_5.sqlite",
)

LOG_SQLITE_FILE_NAMES = (
    "logs_2.sqlite",
    "sqlite/logs_2.sqlite",
)

BACKUP_FORMAT_VERSION = 2
BACKUP_TOOL = "codex-local-session-cleaner"
RESTORABLE_BACKUP_TYPES = {"cleanup_snapshot", "transaction_snapshot"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Backup entry path must be a non-empty string.")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or normalized.startswith("//")
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ValueError(f"Unsafe backup entry path: {value}")
    return Path(*posix.parts)


def contained_path(root: Path, relative: object) -> Path:
    resolved_root = root.expanduser().resolve()
    rel = safe_relative_path(relative)
    candidate = (resolved_root / rel).resolve(strict=False)
    try:
        common = Path(os.path.commonpath((resolved_root, candidate)))
    except ValueError as exc:
        raise ValueError(f"Backup path escapes its root: {relative}") from exc
    if common != resolved_root or candidate == resolved_root:
        raise ValueError(f"Backup path escapes its root: {relative}")
    return candidate


def _unique_backup_dir(parent: Path, prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = parent / f"{prefix}_{stamp}"
    suffix = 1
    while backup_dir.exists():
        backup_dir = parent / f"{prefix}_{stamp}_{suffix}"
        suffix += 1
    parent.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=False)
    return backup_dir


def create_backup(
    plan: CleanupPlan,
    destination: Path | None = None,
    *,
    prefix: str = "backup_before_session_cleanup",
    include_logs: bool = True,
) -> tuple[Path, dict]:
    backup_dir = _unique_backup_dir(
        (destination or (plan.codex_home / "session_manager_backups")).expanduser().resolve(),
        prefix,
    )

    entries: list[dict] = []
    for item in plan.items:
        if item.path is None or item.relative_path is None:
            continue
        details = _copy_stable(item.path, backup_dir / item.relative_path)
        entries.append({"relative_path": item.relative_path.as_posix(),
                        "category": "session", "session_id": item.id, **details})
    for name in JSON_STATE_FILE_NAMES:
        source = plan.codex_home / name
        if source.is_file():
            details = _copy_stable(source, backup_dir / name)
            entries.append({"relative_path": name, "category": "state", **details})
    sqlite_names = SQLITE_STATE_FILE_NAMES + (LOG_SQLITE_FILE_NAMES if include_logs else ())
    for name in sqlite_names:
        source = plan.codex_home.joinpath(*name.split("/"))
        if source.is_file():
            details = _backup_sqlite_online(source, backup_dir.joinpath(*name.split("/")))
            entries.append({"relative_path": name, "category": "sqlite", **details})

    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "tool": BACKUP_TOOL,
        "tool_version": __version__,
        "backup_type": "cleanup_snapshot",
        "restorable": True,
        "created_at": plan.created_at,
        "codex_home": str(plan.codex_home),
        "plan_fingerprint": plan.fingerprint,
        "requested_ids": plan.requested_ids,
        "deletion_ids": plan.deletion_ids,
        "files": entries,
        "status": "backup_complete",
        "include_logs": include_logs,
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return backup_dir, manifest


def _copy_stable(source: Path, target: Path, retries: int = 3) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        before = source.stat()
        shutil.copy2(source, target)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            source_hash = sha256_file(source)
            target_hash = sha256_file(target)
            if source_hash == target_hash:
                return {"size": after.st_size, "sha256": target_hash, "attempts": attempt + 1}
        time.sleep(0.05 * (attempt + 1))
    target.unlink(missing_ok=True)
    raise OSError(f"File kept changing while being backed up: {source}")


def _backup_sqlite_online(source: Path, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
    target_db = sqlite3.connect(target)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()
    check_db = sqlite3.connect(target)
    try:
        integrity = check_db.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check_db.close()
    if integrity != "ok":
        target.unlink(missing_ok=True)
        raise sqlite3.DatabaseError(f"Backup SQLite integrity failed: {integrity}")
    return {"size": target.stat().st_size, "sha256": sha256_file(target), "integrity": integrity}


def create_manual_backup(
    scan: ScanResult,
    selected_ids: list[str] | None,
    destination: Path,
    include_state: bool = False,
) -> tuple[Path, dict]:
    if selected_ids:
        backup_ids: set[str] = set()
        for session_id in selected_ids:
            if session_id not in scan.sessions:
                raise ValueError(f"Session does not exist: {session_id}")
            if scan.sessions[session_id].catalog_host not in {"", "local"}:
                raise ValueError(f"Cloud catalog entry cannot be backed up locally: {session_id}")
            backup_ids.add(session_id)
            backup_ids.update(descendants(scan, session_id))
    else:
        backup_ids = {
            session_id for session_id, record in scan.sessions.items()
            if record.catalog_host in {"", "local"}
        }
    backup_dir = _unique_backup_dir(
        destination.expanduser().resolve(), "backup_codex_sessions"
    )
    entries: list[dict] = []
    try:
        for session_id in sorted(backup_ids):
            record = scan.sessions[session_id]
            if record.path is None or record.relative_path is None:
                continue
            target = backup_dir / record.relative_path
            details = _copy_stable(record.path, target)
            entries.append({
                "relative_path": record.relative_path.as_posix(),
                "category": "session",
                "session_id": session_id,
                **details,
            })
        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "tool": BACKUP_TOOL,
            "tool_version": __version__,
            "backup_type": "session_archive",
            "created_at": datetime.now().astimezone().isoformat(),
            "codex_home": str(scan.codex_home),
            "session_ids": sorted(backup_ids),
            "include_state": False,
            "files": entries,
            "status": "archive_complete",
            "restorable": False,
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return backup_dir, manifest
    except Exception:
        (backup_dir / "BACKUP_INCOMPLETE.txt").write_text(
            "Backup did not complete. Do not treat this directory as a valid backup.\n",
            encoding="utf-8",
        )
        raise


def update_manifest(backup_dir: Path, manifest: dict, **values: object) -> Path:
    manifest.update(values)
    path = backup_dir / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def verify_backup(backup_dir: Path) -> dict:
    backup_dir = backup_dir.expanduser().resolve()
    manifest_path = contained_path(backup_dir, "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("tool") != BACKUP_TOOL:
        raise ValueError("Backup was not created by this tool.")
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported backup format: {manifest.get('format_version')}; "
            f"expected {BACKUP_FORMAT_VERSION}."
        )
    backup_type = str(manifest.get("backup_type") or "")
    if backup_type not in RESTORABLE_BACKUP_TYPES | {"session_archive"}:
        raise ValueError(f"Unsupported backup type: {backup_type}")
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict) or entry.get("category") not in {"session", "state", "sqlite"}:
            raise ValueError("Backup contains an unsupported entry.")
        path = contained_path(backup_dir, entry.get("relative_path"))
        if path.is_symlink():
            raise OSError(f"Backup entry cannot be a symbolic link: {path}")
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            raise OSError(f"Invalid backup entry: {path}")
        if entry.get("category") == "sqlite":
            connection = sqlite3.connect(path)
            try:
                check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            finally:
                connection.close()
            if check != "ok":
                raise sqlite3.DatabaseError(f"Invalid SQLite backup: {path}: {check}")
    return manifest


def create_rollback_snapshot(plan: CleanupPlan) -> tuple[Path, dict]:
    directory, manifest = create_backup(
        plan, plan.codex_home, prefix=".cleaner-transaction", include_logs=True
    )
    update_manifest(directory, manifest, backup_type="transaction_snapshot")
    return directory, manifest


def restore_snapshot(backup_dir: Path, codex_home: Path) -> int:
    manifest = verify_backup(backup_dir)
    if manifest.get("backup_type") not in RESTORABLE_BACKUP_TYPES:
        raise ValueError("A session archive cannot be restored as a full state snapshot.")
    target_root = codex_home.expanduser().resolve()
    restored = 0
    for entry in manifest.get("files", []):
        source = contained_path(backup_dir, entry["relative_path"])
        target = contained_path(target_root, entry["relative_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.get("category") == "sqlite":
            for suffix in ("-wal", "-shm"):
                Path(str(target) + suffix).unlink(missing_ok=True)
        shutil.copy2(source, target)
        restored += 1
    return restored


def discard_snapshot(backup_dir: Path) -> None:
    resolved = backup_dir.resolve()
    if (
        not resolved.name.startswith(".cleaner-transaction_")
        or not (resolved / "manifest.json").is_file()
    ):
        raise ValueError(f"Refusing to remove unexpected snapshot path: {resolved}")
    shutil.rmtree(resolved)
