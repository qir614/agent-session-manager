from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .artifact_store import (
    ARTIFACT_ROOTS, THREAD_PRIVATE_ROOTS, UUID_RE, _attachment_ids,
    _is_reparse, _safe_managed_path, _tree_stats,
)
from .audit import audit_paths
from .process_guard import require_codex_stopped
from .scanner import SessionScanner
from .state_store import filter_global_state, global_reference_ids, verify_no_references, count_all_sqlite_references


@dataclass(slots=True)
class OrphanCandidate:
    relative_path: str
    category: str
    files: int
    size: int
    modified_at: str
    reason: str


@dataclass(slots=True)
class OrphanScan:
    codex_home: Path
    active_session_ids: list[str]
    candidates: list[OrphanCandidate]
    review_only: list[OrphanCandidate]
    retained_backups: list[str]
    operation_log_ids: list[str]
    grace_days: int
    fingerprint: str
    warnings: list[str] = field(default_factory=list)
    state_reference_ids: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return sum(item.files for item in self.candidates)

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.candidates)


@dataclass(slots=True)
class OrphanCleanupReport:
    deleted_paths: list[str]
    deleted_files: int
    deleted_bytes: int
    operation_ids_redacted: int
    pruned_directories: list[str]
    state_references_removed: int = 0


def _candidate(path: Path, home: Path, category: str, reason: str) -> OrphanCandidate:
    files, size = _tree_stats(path)
    latest = max((item.stat().st_mtime for item in path.rglob("*")), default=path.stat().st_mtime) if path.is_dir() else path.stat().st_mtime
    latest = max(latest, path.stat().st_mtime)
    return OrphanCandidate(
        relative_path=path.resolve().relative_to(home.resolve()).as_posix(),
        category=category,
        files=files,
        size=size,
        modified_at=datetime.fromtimestamp(latest, timezone.utc).isoformat(),
        reason=reason,
    )


def _uuid_named_paths(root: Path, active_ids: set[str]) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir() or _is_reparse(root):
        return found
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            item = current_path / name
            if _is_reparse(item):
                directories.remove(name)
                continue
            if UUID_RE.fullmatch(name) and name.lower() not in active_ids:
                found.append(item)
                directories.remove(name)
        for name in files:
            item = current_path / name
            matches = {match.group(1).lower() for match in UUID_RE.finditer(name)}
            if matches and not (matches & active_ids) and not _is_reparse(item):
                found.append(item)
    return found


def _operation_orphan_ids(home: Path, active_ids: set[str]) -> list[str]:
    found: set[str] = set()
    id_keys = {"requested_ids", "deletion_ids", "session_ids", "thread_id", "parent_thread_id"}

    def collect(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                collect(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif isinstance(value, str) and key in id_keys:
            match = UUID_RE.fullmatch(value)
            if match and match.group(1).lower() not in active_ids:
                found.add(match.group(1).lower())

    for path in audit_paths(home):
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                collect(value)
    return sorted(found)



def _completed_state_orphans(home, active_ids):
    """Repair only IDs attested by this cleaner's successful deletion journal."""
    hashes = set()
    for journal in audit_paths(home):
        if not journal.is_file():
            continue
        for line in journal.read_text(encoding="utf-8-sig").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("event") == "cleanup_complete":
                values = record.get("deletion_ids", [])
                if isinstance(values, list):
                    hashes.update(value for value in values if isinstance(value, str))
    candidates = set()
    for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
        path = home / name
        if path.is_file():
            candidates.update(global_reference_ids(json.loads(path.read_text(encoding="utf-8-sig"))))
    result = []
    for sid in sorted(candidates - active_ids):
        attestation = "thread#" + hashlib.sha256(sid.encode()).hexdigest()[:16]
        if attestation not in hashes:
            continue
        refs = count_all_sqlite_references(home, {sid})
        if any(sum(rows.values()) for rows in refs.values()):
            continue
        # Unknown layouts are review-only; known state filtering must remove all references.
        supported = True
        from .state_store import filter_global_state_data
        for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
            path = home / name
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                filter_global_state_data(data, {sid})
                if sid in global_reference_ids(data):
                    supported = False
        if supported:
            result.append(sid)
    return result

def scan_orphans(codex_home: Path, grace_days: int = 7) -> OrphanScan:
    home = codex_home.expanduser().resolve()
    pending = list(home.glob(".cleaner-orphan-transaction-*"))
    if pending:
        raise RuntimeError(f"Unfinished orphan cleanup; preserve and inspect recovery data: {pending[0]}")
    scan = SessionScanner(home).scan()
    active_ids = set(scan.sessions)
    from .artifact_store import attachment_references
    references = attachment_references(scan)
    active_ids.update(references)
    attachment_refs = set().union(*references.values())

    paths: dict[str, tuple[Path, str, str]] = {}
    attachments = home / "attachments"
    if attachments.is_dir() and not _is_reparse(attachments):
        for item in attachments.iterdir():
            if item.is_dir() and UUID_RE.fullmatch(item.name) and item.name.lower() not in attachment_refs:
                paths[item.resolve().relative_to(home).as_posix()] = (
                    item, "attachment", "no surviving rollout references this attachment ID",
                )
    for root_name in ("visualizations", "generated_images") + THREAD_PRIVATE_ROOTS:
        for item in _uuid_named_paths(home / root_name, active_ids):
            relative = item.resolve().relative_to(home).as_posix()
            paths[relative] = (
                item, root_name, "UUID is not present in any surviving local session or catalog record",
            )

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, grace_days))
    candidates: list[OrphanCandidate] = []
    review: list[OrphanCandidate] = []
    warnings = [f"{issue.code}: {issue.message}" for issue in scan.issues if issue.blocking]
    if warnings:
        raise RuntimeError("Cannot establish orphan ownership: " + "; ".join(warnings))
    for _relative, (path, category, reason) in sorted(paths.items()):
        _safe_managed_path(home, path.resolve().relative_to(home).as_posix())
        try:
            item = _candidate(path, home, category, reason)
        except (OSError, ValueError) as exc:
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            except OSError:
                modified_at = ""
            review.append(OrphanCandidate(
                relative_path=path.resolve().relative_to(home).as_posix(),
                category=category, files=0, size=0, modified_at=modified_at,
                reason=f"manual review required; unsafe or unreadable tree: {exc}",
            ))
            continue
        modified = datetime.fromisoformat(item.modified_at)
        (candidates if modified <= cutoff else review).append(item)

    retained = []
    for name in ("session_manager_backups", "db-backups"):
        root = home / name
        if root.exists():
            retained.append(str(root))
    operation_ids = _operation_orphan_ids(home, active_ids)
    state_ids = _completed_state_orphans(home, active_ids)
    payload = {
        "active_ids": sorted(active_ids),
        "candidates": [asdict(item) for item in candidates],
        "operation_log_ids": operation_ids,
        "grace_days": max(0, grace_days),
        "state_reference_ids": state_ids,
        "state_fingerprints": {name: hashlib.sha256((home / name).read_bytes()).hexdigest()
            for name in (".codex-global-state.json", ".codex-global-state.json.bak")
            if state_ids and (home / name).is_file()},
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return OrphanScan(
        codex_home=home, active_session_ids=sorted(active_ids), candidates=candidates,
        review_only=review, retained_backups=retained, operation_log_ids=operation_ids,
        grace_days=max(0, grace_days), fingerprint=fingerprint, warnings=warnings, state_reference_ids=state_ids,
    )


def _redact_operation_ids(path: Path, ids: set[str]) -> int:
    if not path.is_file() or not ids:
        return 0
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    count = 0
    for session_id in sorted(ids):
        pattern = re.compile(re.escape(session_id), re.I)
        replacement = "thread#orphaned-" + hashlib.sha256(session_id.encode()).hexdigest()[:12]
        text, replaced = pattern.subn(replacement, text)
        count += replaced
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)
    return count


def execute_orphan_cleanup(plan: OrphanScan) -> OrphanCleanupReport:
    require_codex_stopped()
    current = scan_orphans(plan.codex_home, plan.grace_days)
    if current.fingerprint != plan.fingerprint or current.candidates != plan.candidates or current.operation_log_ids != plan.operation_log_ids:
        raise RuntimeError("Orphan data changed after preview. Scan again before cleaning.")
    home = current.codex_home
    relative_paths = [item.relative_path for item in current.candidates]
    sources = [_safe_managed_path(home, relative) for relative in relative_paths]
    operation_paths = [path for path in audit_paths(home) if path.is_file()]
    staging = Path(tempfile.mkdtemp(prefix=".cleaner-orphan-transaction-", dir=home))
    moves: list[tuple[Path, Path]] = []
    operation_log_backups: list[Path] = []
    state_saved = []
    state_removed = 0
    committed = False
    try:
        # Record destinations before moving; no attachment contents are copied.
        journal = {"phase": "staging", "paths": relative_paths, "operation_log_saved": bool(operation_paths)}
        (staging / "recovery.json").write_text(json.dumps(journal), encoding="utf-8")
        for index, operation_path in enumerate(operation_paths):
            backup_path = staging / f"operation-log-{index}.jsonl"
            shutil.copy2(operation_path, backup_path)
            operation_log_backups.append(backup_path)
        if current.state_reference_ids:
            for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
                source = home / name
                if source.is_file():
                    shutil.copy2(source, staging / name)
                    state_saved.append(name)
        journal["state_files"] = state_saved
        (staging / "recovery.json").write_text(json.dumps(journal), encoding="utf-8")
        require_codex_stopped()
        for relative, source in zip(relative_paths, sources):
            destination = staging / "data" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            moves.append((source, destination))
            # Cross-device moves deliberately fail and roll back, never copy.
            source.rename(destination)
        for name in state_saved:
            state_removed += filter_global_state(home / name, set(current.state_reference_ids))
        if current.state_reference_ids:
            remaining = verify_no_references(home, set(current.state_reference_ids))
            if remaining:
                raise RuntimeError(f"References remain after repair: {remaining}")
        redacted = sum(
            _redact_operation_ids(path, set(current.operation_log_ids)) for path in operation_paths
        )
        if any(source.exists() for source in sources):
            raise RuntimeError("An orphan path reappeared while cleaning; cleanup stopped.")
        journal["phase"] = "committed"
        (staging / "recovery.json").write_text(json.dumps(journal), encoding="utf-8")
        committed = True
        if (staging / "data").exists():
            shutil.rmtree(staging / "data")
        for backup_path in operation_log_backups:
            backup_path.unlink(missing_ok=True)
        for name in state_saved:
            (staging / name).unlink()
        (staging / "recovery.json").unlink()
        staging.rmdir()
    except BaseException:
        if committed:
            # Some garbage may already be removed. Preserve remaining staging
            # and its journal instead of claiming that rollback is possible.
            raise RuntimeError(f"Cleanup committed, but staged garbage removal was interrupted. Remaining data: {staging}")
        try:
            for source, destination in reversed(moves):
                if destination.exists():
                    if source.exists():
                        raise RuntimeError(f"Refusing to overwrite a reappeared path: {source}")
                    source.parent.mkdir(parents=True, exist_ok=True)
                    destination.rename(source)
            for name in state_saved:
                os.replace(staging / name, home / name)
            for backup_path, operation_path in zip(operation_log_backups, operation_paths):
                os.replace(backup_path, operation_path)
        except BaseException as restore_error:
            raise RuntimeError(f"Rollback incomplete; recovery data retained at {staging}: {restore_error}") from restore_error
        shutil.rmtree(staging)
        raise
    return OrphanCleanupReport(
        deleted_paths=relative_paths, deleted_files=current.file_count,
        deleted_bytes=current.total_bytes, operation_ids_redacted=redacted,
        pruned_directories=[], state_references_removed=state_removed,
    )
