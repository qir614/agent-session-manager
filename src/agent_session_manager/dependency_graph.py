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



def validate_sidebar_placeholder(scan, session_id):
    """Only explicit local sidebar assignments with no stored history qualify."""
    from .catalog import read_projects, read_catalog, latest_index_titles
    from .state_store import count_all_sqlite_references, count_jsonl_references
    record = scan.sessions[session_id]
    _, assignments = read_projects(scan.codex_home)
    if (record.source != "global_state" or record.catalog_host != "local"
            or record.has_rollout or record.paths or record.parent_id
            or scan.children.get(session_id) or session_id not in assignments):
        raise DependencyError("This catalog record is not a verified sidebar-only reference.")
    catalog, _, _ = read_catalog(scan.codex_home)
    if any(row.thread_id == session_id for row in catalog) or session_id in latest_index_titles(scan.codex_home):
        raise DependencyError("Catalog or index history exists; sidebar-only cleanup is blocked.")
    refs = count_all_sqlite_references(scan.codex_home, {session_id})
    if any(sum(v.values()) for v in refs.values()) or count_jsonl_references(scan.codex_home / "history.jsonl", {session_id}, id_field="session_id"):
        raise DependencyError("Stored conversation data exists; sidebar-only cleanup is blocked.")

def deletion_closure(scan: ScanResult, requested_ids: list[str]) -> tuple[set[str], dict[str, str]]:
    deletion: set[str] = set()
    reasons: dict[str, str] = {}
    for session_id in requested_ids:
        if session_id not in scan.sessions:
            raise DependencyError(f"Session does not exist: {session_id}")
        status = scan.status_by_id[session_id]
        if status == SessionStatus.ATTACHED or scan.parent_by_child.get(session_id) in scan.sessions:
            parent = scan.parent_by_child.get(session_id, "")
            raise DependencyError(
                f"Attached subagent {session_id} cannot be deleted alone; select parent {parent}."
            )
        if status == SessionStatus.LOCAL_GHOST:
            validate_sidebar_placeholder(scan, session_id)
        if status == SessionStatus.PAGINATED:
            raise DependencyError(f"Unverified or unsupported history cannot be deleted: {session_id}")
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
    unsupported = [sid for sid in deletion if scan.sessions[sid].history_mode not in {"legacy", "paginated"}]
    if unsupported:
        raise DependencyError("Unsupported history in deletion closure: " + ", ".join(sorted(unsupported)))
    paginated = [sid for sid in deletion if scan.sessions[sid].history_mode == "paginated"]
    if paginated:
        if any(not scan.sessions[sid].has_rollout for sid in paginated):
            raise DependencyError("Unsupported history: paginated rollout is missing.")
        from .history_store import validate_paginated_history
        validate_paginated_history(scan.codex_home)
        retained_forks = [r.id for r in scan.sessions.values()
                          if r.id not in deletion and r.forked_from_id in deletion]
        if retained_forks:
            raise DependencyError("Retained forks may depend on selected history: " + ", ".join(sorted(retained_forks)))
    return deletion, reasons
