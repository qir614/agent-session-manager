from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .dependency_graph import deletion_closure
from .models import CleanupPlan, PlanItem, ScanResult, SessionStatus


def _file_fingerprint(path):
    from .backup import sha256_file
    if not path.is_file():
        return None
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise RuntimeError("Session data changed during preview; scan again before deleting.")
    return digest


def build_plan(scan: ScanResult, requested_ids: list[str]) -> CleanupPlan:
    if scan.application != "codex":
        raise ValueError("Use the matching application adapter to plan cleanup")
    requested = list(dict.fromkeys(value.lower() for value in requested_ids))
    if not requested:
        raise ValueError("No sessions selected.")
    cloud = [value for value in requested if scan.status_by_id.get(value) == SessionStatus.CLOUD_CATALOG]
    if cloud:
        raise ValueError("Cloud catalog entries cannot be deleted by a local-only tool.")
    blocking = [issue for issue in scan.issues if issue.blocking]
    if blocking:
        summary = "; ".join(f"{item.code}: {item.message}" for item in blocking[:5])
        raise ValueError(f"Scan has blocking issues: {summary}")
    from .storage import locations
    storage = locations(scan.codex_home)
    if storage.sqlite != storage.home or storage.logs != storage.home / "log":
        raise ValueError("External sqlite_home/log_dir is read-only: cross-directory rollback is not supported.")
    deletion, reasons = deletion_closure(scan, requested)
    from .state_store import validate_sqlite_references, validate_global_state, reference_stores
    validate_sqlite_references(scan.codex_home, deletion)
    validate_global_state(scan.codex_home, deletion)
    items: list[PlanItem] = []
    for session_id in sorted(deletion):
        record = scan.sessions[session_id]
        if record.segments:
            items.extend(PlanItem(
                id=session_id, path=segment.path, relative_path=segment.relative_path,
                reason=reasons[session_id], size=segment.size,
            ) for segment in record.segments)
        else:
            items.append(PlanItem(
                id=session_id, path=record.path, relative_path=record.relative_path,
                reason=reasons[session_id], size=record.size,
            ))
    payload = [
        {"id": item.id, "path": item.relative_path.as_posix() if item.relative_path else None,
         "size": item.size, "reason": item.reason,
         "sha256": _file_fingerprint(item.path) if item.path else None}
        for item in items
    ]
    from .backup import JSON_STATE_FILE_NAMES
    state_paths = set(JSON_STATE_FILE_NAMES) | {"config.toml"}
    for relative in reference_stores(scan.codex_home):
        state_paths.update((relative, relative + "-wal"))
    state_fingerprints = {relative: _file_fingerprint(scan.codex_home / relative) for relative in sorted(state_paths)}
    fingerprint = hashlib.sha256(
        json.dumps({"sessions": payload, "state": state_fingerprints}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    warnings: list[str] = []
    if not scan.sqlite_available:
        warnings.append("SQLite state is missing; file metadata will be used as the source of truth.")
    return CleanupPlan(
        codex_home=scan.codex_home,
        requested_ids=requested,
        deletion_ids=sorted(deletion),
        items=items,
        total_bytes=sum(item.size for item in items),
        created_at=datetime.now(timezone.utc).isoformat(),
        fingerprint=fingerprint,
        warnings=warnings,
    )
