from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .dependency_graph import deletion_closure
from .models import CleanupPlan, PlanItem, ScanResult, SessionStatus


def build_plan(scan: ScanResult, requested_ids: list[str]) -> CleanupPlan:
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
    deletion, reasons = deletion_closure(scan, requested)
    items = [
        PlanItem(
            id=session_id,
            path=scan.sessions[session_id].path,
            relative_path=scan.sessions[session_id].relative_path,
            reason=reasons[session_id],
            size=scan.sessions[session_id].size,
        )
        for session_id in sorted(deletion)
    ]
    payload = [
        {"id": item.id, "path": item.relative_path.as_posix() if item.relative_path else None,
         "size": item.size, "reason": item.reason}
        for item in items
    ]
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
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
