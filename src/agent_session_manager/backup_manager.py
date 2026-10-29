from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .audit import write_operation
from .backup import is_known_backup_tool, verify_backup
from .platforms import get_platform
from .models import BackupRecord
from .process_guard import require_codex_stopped


MANAGED_PREFIXES = (
    "backup_before_session_cleanup_",
    "backup_codex_sessions_",
)


def _is_reparse_point(path: Path) -> bool:
    return get_platform().is_reparse_point(path)

def _direct_child(root: Path, candidate: Path) -> Path:
    resolved_root = root.expanduser().resolve()
    resolved = candidate.expanduser().resolve()
    if resolved == resolved_root or resolved.parent != resolved_root:
        raise ValueError(f"Backup must be a direct child of the configured root: {candidate}")
    if not resolved.name.startswith(MANAGED_PREFIXES):
        raise ValueError(f"Directory name is not managed by this tool: {resolved.name}")
    return resolved


def _directory_size(path: Path) -> int:
    total = 0
    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        directories[:] = [name for name in directories if not _is_reparse_point(current_path / name)]
        for name in files:
            item = current_path / name
            if not _is_reparse_point(item):
                total += item.stat().st_size
    return total


def _record(path: Path) -> BackupRecord:
    total = _directory_size(path)
    incomplete = path / "BACKUP_INCOMPLETE.txt"
    manifest_path = path / "manifest.json"
    if incomplete.is_file() and not manifest_path.is_file():
        return BackupRecord(path, path.name, "incomplete", "", "incomplete", "", 0, 1,
                            total, False, False, "Backup did not finish.")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not is_known_backup_tool(raw.get("tool")):
            raise ValueError("Unrecognized backup tool marker.")
        manifest = verify_backup(path)
        files = manifest.get("files", [])
        session_ids = manifest.get("session_ids") or manifest.get("deletion_ids") or []
        return BackupRecord(
            path=path,
            name=path.name,
            backup_type=str(manifest.get("backup_type") or "unknown"),
            created_at=str(manifest.get("created_at") or ""),
            status=str(manifest.get("status") or ""),
            tool_version=str(manifest.get("tool_version") or ""),
            session_count=len(session_ids),
            file_count=len(files),
            total_bytes=total,
            valid=True,
            restorable=bool(manifest.get("restorable")),
        )
    except Exception as exc:
        return BackupRecord(path, path.name, "invalid", "", "invalid", "", 0, 0,
                            total, False, False, str(exc))


def list_backups(root: Path) -> list[BackupRecord]:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        return []
    records: list[BackupRecord] = []
    for path in resolved.iterdir():
        if not path.is_dir() or not path.name.startswith(MANAGED_PREFIXES):
            continue
        if _is_reparse_point(path):
            records.append(BackupRecord(path, path.name, "unsafe", "", "unsafe", "", 0, 0,
                                        0, False, False, "Reparse points are not managed."))
            continue
        records.append(_record(path))
    return sorted(records, key=lambda item: (item.created_at, item.name), reverse=True)


def retention_candidates(
    records: list[BackupRecord], *, keep_count: int = 0, max_age_days: int = 0,
    max_bytes: int = 0, now: datetime | None = None,
) -> list[BackupRecord]:
    ordered = sorted(
        (item for item in records if item.valid or item.status == "incomplete"),
        key=lambda item: (item.created_at, item.name), reverse=True,
    )
    selected: dict[Path, BackupRecord] = {}
    if keep_count > 0:
        for record in ordered[keep_count:]:
            selected[record.path] = record
    if max_age_days > 0:
        threshold = (now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)
        for record in ordered:
            try:
                created = datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if created < threshold:
                selected[record.path] = record
    if max_bytes > 0:
        current = sum(record.total_bytes for record in ordered)
        for record in reversed(ordered):
            if current <= max_bytes:
                break
            selected[record.path] = record
            current -= record.total_bytes
    return sorted(selected.values(), key=lambda item: (item.created_at, item.name))


def delete_backup(root: Path, candidate: Path, codex_home: Path) -> int:
    require_codex_stopped()
    target = _direct_child(root, candidate)
    if _is_reparse_point(target):
        raise ValueError("Refusing to delete a reparse point or symbolic link.")
    record = _record(target)
    if record.backup_type == "invalid" and not (target / "BACKUP_INCOMPLETE.txt").is_file():
        try:
            raw = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError("Invalid backups without a trusted tool marker require manual inspection.") from exc
        if not is_known_backup_tool(raw.get("tool")):
            raise ValueError("Invalid backups without a trusted tool marker require manual inspection.")
    size = record.total_bytes
    shutil.rmtree(target)
    if target.exists():
        raise OSError(f"Backup directory still exists after deletion: {target}")
    write_operation(codex_home, "backup_deleted", backup_name=target.name,
                    backup_type=record.backup_type, deleted_bytes=size)
    return size
