from __future__ import annotations

import json
from pathlib import Path

from .backup import JSON_STATE_FILE_NAMES, LOG_SQLITE_FILE_NAMES, SQLITE_STATE_FILE_NAMES
from .models import CleanupPlan, DeletionImpact, ScanResult, SessionKind, SessionStatus
from .state_store import count_all_sqlite_references


def _count_index(path: Path, deletion_ids: set[str]) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid session_index.jsonl line {line_number}: {exc}") from exc
            if str(item.get("id") or "").lower() in deletion_ids:
                count += 1
    return count


def _count_values(value: object, deletion_ids: set[str]) -> int:
    if isinstance(value, str):
        return int(value.lower() in deletion_ids)
    if isinstance(value, list):
        return sum(_count_values(item, deletion_ids) for item in value)
    if isinstance(value, dict):
        return sum(
            int(str(key).lower() in deletion_ids) + _count_values(item, deletion_ids)
            for key, item in value.items()
        )
    return 0


def _count_global_state(path: Path, deletion_ids: set[str]) -> int:
    if not path.is_file():
        return 0
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return _count_values(value, deletion_ids)


def _snapshot_estimate(codex_home: Path, plan: CleanupPlan) -> int:
    paths = {item.path.resolve() for item in plan.items if item.path and item.path.is_file()}
    for relative in JSON_STATE_FILE_NAMES + SQLITE_STATE_FILE_NAMES + LOG_SQLITE_FILE_NAMES:
        path = codex_home.joinpath(*relative.split("/"))
        if path.is_file():
            paths.add(path.resolve())
    return sum(path.stat().st_size for path in paths)


def analyze_deletion(scan: ScanResult, plan: CleanupPlan) -> DeletionImpact:
    deletion = set(plan.deletion_ids)
    sqlite = count_all_sqlite_references(scan.codex_home, deletion)
    sqlite_total = sum(sum(tables.values()) for tables in sqlite.values())
    log_total = sum(
        sum(sqlite.get(relative, {}).values()) for relative in LOG_SQLITE_FILE_NAMES
    )
    statuses = scan.status_by_id
    records = scan.sessions
    return DeletionImpact(
        requested_count=len(plan.requested_ids),
        deletion_count=len(plan.deletion_ids),
        user_count=sum(statuses.get(sid) == SessionStatus.USER_ROOT for sid in deletion),
        subagent_count=sum(records[sid].kind == SessionKind.SUBAGENT for sid in deletion),
        ghost_count=sum(statuses.get(sid) == SessionStatus.LOCAL_GHOST for sid in deletion),
        rollout_count=plan.file_count,
        rollout_bytes=plan.total_bytes,
        index_references=_count_index(scan.codex_home / "session_index.jsonl", deletion),
        global_state_references=sum(
            _count_global_state(scan.codex_home / name, deletion)
            for name in (".codex-global-state.json", ".codex-global-state.json.bak")
        ),
        sqlite_references=sqlite,
        sqlite_reference_total=sqlite_total,
        log_reference_total=log_total,
        transaction_snapshot_bytes=_snapshot_estimate(scan.codex_home, plan),
        blocking_reasons=[f"{issue.code}: {issue.message}" for issue in scan.issues if issue.blocking],
    )
