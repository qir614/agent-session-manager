"""Durable operation checkpoints and explicit, conflict-checked recovery."""
from __future__ import annotations

import json
from pathlib import Path

from .backup import contained_path, sha256_file, validate_entry_path, verify_backup
from .state_store import _atomic_write


def pending_transactions(home: Path) -> list[Path]:
    home = home.expanduser().resolve()
    return sorted(
        path
        for prefix in (".cleaner-transaction_", ".cleaner-restore-")
        for path in home.glob(prefix + "*")
        if path.is_dir()
    )


def require_no_pending_transaction(home: Path) -> None:
    pending = pending_transactions(home)
    if pending:
        raise RuntimeError(f"Interrupted operation requires explicit recovery: {pending[0]}")


def file_states(home: Path, manifest: dict) -> dict[str, str | None]:
    states: dict[str, str | None] = {}
    for entry in manifest["files"]:
        relative = entry["relative_path"]
        path = contained_path(home, relative)
        states[relative] = sha256_file(path) if path.is_file() else None
        if entry["category"] == "sqlite":
            for suffix in ("-wal",):
                sidecar = contained_path(home, relative + suffix)
                states[relative + suffix] = sha256_file(sidecar) if sidecar.is_file() else None
    return states


def checkpoint(
    directory: Path,
    home: Path,
    manifest: dict,
    phase: str,
    *,
    dirty: bool = False,
    missing: list[str] | None = None,
) -> None:
    states = file_states(home, manifest)
    for name in missing or []:
        path = contained_path(home, name)
        states[name] = sha256_file(path) if path.is_file() else None
    journal = {
        "version": 1,
        "phase": phase,
        "dirty": dirty,
        "expected_files": states,
        "missing_files": missing or [],
    }
    _atomic_write(directory / "operation.json", json.dumps(journal, sort_keys=True) + "\n")


def check_recovery(
    directory: Path, home: Path, *, overwrite_conflicts: bool = False
) -> tuple[dict, list[str]]:
    manifest = verify_backup(directory)
    journal_path = contained_path(directory, "operation.json")
    if not journal_path.is_file():
        raise RuntimeError("Snapshot has no complete operation journal; preserve it for manual recovery.")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("version") != 1 or not isinstance(journal.get("expected_files"), dict):
        raise ValueError("Unsupported operation journal.")
    expected = journal["expected_files"]
    missing = journal.get("missing_files", [])
    if not isinstance(missing, list) or any(not isinstance(name, str) for name in missing):
        raise ValueError("Operation journal contains invalid missing paths.")
    copied = {entry["relative_path"] for entry in manifest["files"]}
    if len(missing) != len(set(missing)) or copied.intersection(missing):
        raise ValueError("Operation journal has conflicting missing paths.")
    for name in missing:
        # Missing targets may have no rollback copy, but must still be supported paths.
        if not any(
            _valid_missing(name, category)
            for category in ("session", "state", "sqlite", "artifact")
        ):
            raise ValueError("Operation journal contains an unsupported missing path.")
    current = file_states(home, manifest)
    for name in missing:
        path = contained_path(home, name)
        current[name] = sha256_file(path) if path.is_file() else None
    if set(current) != set(expected):
        raise ValueError("Operation journal does not match the snapshot.")
    conflicts = [name for name, value in current.items() if expected[name] != value]
    if (journal.get("dirty") or conflicts) and not overwrite_conflicts:
        raise RuntimeError(
            f"Recovery conflicts or interrupted writes detected: {conflicts}. "
            "Review current data before using --overwrite-conflicts."
        )
    return manifest, missing


def _valid_missing(name: str, category: str) -> bool:
    try:
        validate_entry_path(name, category)
        return True
    except ValueError:
        return False
