from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SessionKind(str, Enum):
    USER = "user"
    SUBAGENT = "subagent"
    INTERNAL = "internal"
    LOCAL_GHOST = "local_ghost"
    CLOUD = "cloud"
    UNKNOWN = "unknown"


class SessionStatus(str, Enum):
    USER_ROOT = "user_root"
    ATTACHED = "attached"
    ORPHAN = "orphan"
    UNLINKED_INTERNAL = "unlinked_internal"
    LOCAL_GHOST = "local_ghost"
    CLOUD_CATALOG = "cloud_catalog"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class SessionRecord:
    id: str
    path: Path | None
    relative_path: Path | None
    kind: SessionKind
    parent_id: str | None = None
    title: str = ""
    cwd: str = ""
    created_at: str = ""
    updated_at: str = ""
    source: Any = None
    archived: bool = False
    size: int = 0
    metadata_valid: bool = True
    warnings: list[str] = field(default_factory=list)
    catalog_host: str = ""
    project_id: str = ""
    project_name: str = ""
    project_path: str = ""
    has_rollout: bool = True


@dataclass(slots=True)
class ScanIssue:
    path: str
    code: str
    message: str
    blocking: bool = False


@dataclass(slots=True)
class ScanResult:
    codex_home: Path
    sessions: dict[str, SessionRecord]
    children: dict[str, set[str]]
    parent_by_child: dict[str, str]
    status_by_id: dict[str, SessionStatus]
    issues: list[ScanIssue]
    sqlite_available: bool
    sqlite_integrity: str
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    catalog_revision: int = 0

    def ids_with_status(self, status: SessionStatus) -> list[str]:
        return sorted(sid for sid, value in self.status_by_id.items() if value == status)


@dataclass(slots=True)
class PlanItem:
    id: str
    path: Path | None
    relative_path: Path | None
    reason: str
    size: int


@dataclass(slots=True)
class CleanupPlan:
    codex_home: Path
    requested_ids: list[str]
    deletion_ids: list[str]
    items: list[PlanItem]
    total_bytes: int
    created_at: str
    fingerprint: str
    warnings: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return sum(item.path is not None for item in self.items)


@dataclass(slots=True)
class ExecutionReport:
    backup_dir: Path | None
    deleted_ids: list[str]
    deleted_files: list[str]
    pruned_directories: list[str]
    index_entries_removed: int
    global_state_references_removed: int
    sqlite_rows_removed: dict[str, int]
    manifest_path: Path | None
    warnings: list[str] = field(default_factory=list)
    sqlite_rows_removed_all: dict[str, dict[str, int]] = field(default_factory=dict)
    verification: dict[str, int] = field(default_factory=dict)
    impact: "DeletionImpact | None" = None


@dataclass(slots=True)
class DeletionImpact:
    requested_count: int
    deletion_count: int
    user_count: int
    subagent_count: int
    ghost_count: int
    rollout_count: int
    rollout_bytes: int
    index_references: int
    global_state_references: int
    sqlite_references: dict[str, dict[str, int]]
    sqlite_reference_total: int
    log_reference_total: int
    transaction_snapshot_bytes: int
    blocking_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BackupRecord:
    path: Path
    name: str
    backup_type: str
    created_at: str
    status: str
    tool_version: str
    session_count: int
    file_count: int
    total_bytes: int
    valid: bool
    restorable: bool
    error: str = ""
