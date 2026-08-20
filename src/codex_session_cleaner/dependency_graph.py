from __future__ import annotations

from collections import deque

from .models import ScanResult, SessionStatus


class DependencyError(ValueError):
    pass


def descendants(scan: ScanResult, root_id: str) -> set[str]:
    found: set[str] = set()
    queue: deque[str] = deque([root_id])
    while queue:
        current = queue.popleft()
        for child in scan.children.get(current, set()):
            if child not in found:
                found.add(child)
                queue.append(child)
    return found


def deletion_closure(scan: ScanResult, requested_ids: list[str]) -> tuple[set[str], dict[str, str]]:
    deletion: set[str] = set()
    reasons: dict[str, str] = {}
    for session_id in requested_ids:
        if session_id not in scan.sessions:
            raise DependencyError(f"Session does not exist: {session_id}")
        status = scan.status_by_id[session_id]
        if status == SessionStatus.ATTACHED:
            parent = scan.parent_by_child.get(session_id, "")
            raise DependencyError(
                f"Attached subagent {session_id} cannot be deleted alone; select parent {parent}."
            )
        if status == SessionStatus.UNKNOWN:
            raise DependencyError(f"Unknown session type cannot be deleted safely: {session_id}")
        if status == SessionStatus.CLOUD_CATALOG:
            raise DependencyError(
                f"Cloud thread {session_id} cannot be deleted from local storage."
            )
        deletion.add(session_id)
        reasons[session_id] = "selected"
        for child in descendants(scan, session_id):
            deletion.add(child)
            reasons.setdefault(child, f"descendant_of:{session_id}")
    return deletion, reasons
