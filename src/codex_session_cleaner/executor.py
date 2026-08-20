from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .backup import (
    RESTORABLE_BACKUP_TYPES, contained_path, create_backup, create_rollback_snapshot,
    discard_snapshot, restore_snapshot,
    update_manifest, verify_backup,
)
from .audit import write_operation
from .models import CleanupPlan, ExecutionReport
from .impact_analyzer import analyze_deletion
from .planner import build_plan
from .process_guard import require_codex_stopped
from .scanner import SessionScanner, default_codex_home
from .state_store import (
    delete_all_sqlite_references, filter_global_state, filter_session_index,
    verify_no_references,
)


def _validate_plan(plan: CleanupPlan):
    scan = SessionScanner(plan.codex_home).scan()
    current = build_plan(scan, plan.requested_ids)
    if current.fingerprint != plan.fingerprint:
        raise RuntimeError("Session data changed after preview. Scan again before deleting.")
    for item in plan.items:
        if item.path is None:
            continue
        if not item.path.is_file() or item.path.stat().st_size != item.size:
            raise RuntimeError(f"Session file changed after preview: {item.path}")
    return scan


def _prune_empty_session_directories(codex_home: Path) -> list[str]:
    removed: list[str] = []
    for root_name in ("sessions", "archived_sessions"):
        root = codex_home / root_name
        if not root.is_dir():
            continue
        directories = sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda value: len(value.parts), reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                continue
            removed.append(str(directory.relative_to(codex_home)))
    return removed


class CleanupExecutor:
    def execute(
        self,
        plan: CleanupPlan,
        persistent_backup: bool = False,
        backup_directory: Path | None = None,
    ) -> ExecutionReport:
        require_codex_stopped()
        scan = _validate_plan(plan)
        impact = analyze_deletion(scan, plan)
        required_free = impact.transaction_snapshot_bytes + 64 * 1024 * 1024
        available_free = shutil.disk_usage(plan.codex_home).free
        if available_free < required_free:
            raise OSError(
                f"Insufficient free disk space for the rollback snapshot: "
                f"need {required_free} bytes, have {available_free} bytes."
            )
        backup_dir: Path | None = None
        manifest: dict | None = None
        if persistent_backup:
            backup_dir, manifest = create_backup(plan, backup_directory)
            verify_backup(backup_dir)
        rollback_dir, _rollback_manifest = create_rollback_snapshot(plan)
        require_codex_stopped()
        discard_rollback = True
        deletion = set(plan.deletion_ids)
        warnings: list[str] = []
        try:
            sqlite_rows_all = delete_all_sqlite_references(
                plan.codex_home, deletion, include_logs=False
            )
            sqlite_rows = sqlite_rows_all.get("state_5.sqlite", {})
            index_removed = filter_session_index(
                plan.codex_home / "session_index.jsonl", deletion
            )
            state_removed = 0
            for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
                state_removed += filter_global_state(plan.codex_home / name, deletion)
            deleted_files: list[str] = []
            for item in plan.items:
                if item.path is not None and item.relative_path is not None:
                    item.path.unlink()
                    deleted_files.append(str(item.relative_path))
            core_remaining = verify_no_references(
                plan.codex_home, deletion, include_logs=False
            )
            if core_remaining:
                raise RuntimeError(f"Core references remain after deletion: {core_remaining}")
            log_rows = delete_all_sqlite_references(
                plan.codex_home, deletion, include_logs=True
            )
            for relative in ("logs_2.sqlite", "sqlite/logs_2.sqlite"):
                if relative in log_rows:
                    sqlite_rows_all[relative] = log_rows[relative]
            remaining = verify_no_references(plan.codex_home, deletion)
            if remaining:
                raise RuntimeError(f"References remain after deletion: {remaining}")
            pruned = _prune_empty_session_directories(plan.codex_home)
            manifest_path = None
            if backup_dir is not None and manifest is not None:
                manifest_path = update_manifest(
                    backup_dir,
                    manifest,
                    status="cleanup_complete",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    deleted_files=deleted_files,
                    pruned_directories=pruned,
                    index_entries_removed=index_removed,
                    global_state_references_removed=state_removed,
                    sqlite_rows_removed=sqlite_rows,
                    sqlite_rows_removed_all=sqlite_rows_all,
                    verification=remaining,
                )
            summary = {
                "backup_dir": str(backup_dir),
                "deleted_sessions": len(plan.deletion_ids),
                "deleted_files": len(deleted_files),
                "freed_bytes": plan.total_bytes,
                "index_entries_removed": index_removed,
                "global_state_references_removed": state_removed,
                "sqlite_rows_removed": sqlite_rows,
                "sqlite_rows_removed_all": sqlite_rows_all,
                "verification": remaining,
                "warnings": warnings,
                "impact": asdict(impact),
            }
            if backup_dir is not None:
                (backup_dir / "cleanup_summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            write_operation(
                plan.codex_home, "cleanup_complete",
                requested_ids=plan.requested_ids,
                deletion_ids=plan.deletion_ids,
                deleted_files=deleted_files,
                sqlite_rows_removed=sqlite_rows_all,
                persistent_backup=str(backup_dir) if backup_dir else None,
                verification=remaining,
                impact=asdict(impact),
            )
            return ExecutionReport(
                backup_dir=backup_dir,
                deleted_ids=plan.deletion_ids,
                deleted_files=deleted_files,
                pruned_directories=pruned,
                index_entries_removed=index_removed,
                global_state_references_removed=state_removed,
                sqlite_rows_removed=sqlite_rows,
                manifest_path=manifest_path,
                warnings=warnings,
                sqlite_rows_removed_all=sqlite_rows_all,
                verification=remaining,
                impact=impact,
            )
        except Exception as exc:
            try:
                restore_snapshot(rollback_dir, plan.codex_home)
            except Exception as restore_exc:
                discard_rollback = False
                raise RuntimeError(
                    f"Cleanup failed and automatic rollback also failed. "
                    f"Snapshot: {rollback_dir}. Cleanup error: {exc}. "
                    f"Rollback error: {restore_exc}"
                ) from restore_exc
            write_operation(
                plan.codex_home, "cleanup_failed_rolled_back",
                requested_ids=plan.requested_ids,
                deletion_ids=plan.deletion_ids,
                error_type=type(exc).__name__,
                error=str(exc)[:1000],
            )
            if backup_dir is not None and manifest is not None:
                update_manifest(
                    backup_dir,
                    manifest,
                    status="cleanup_failed",
                    failed_at=datetime.now(timezone.utc).isoformat(),
                    error_type=type(exc).__name__,
                    recovery="Close Codex and run the restore command against this backup.",
                )
                raise RuntimeError(
                    f"Cleanup failed after backup was created at {backup_dir}. "
                    f"Restore before continuing: {exc}"
                ) from exc
            raise RuntimeError(
                f"Cleanup failed without a persistent backup: {exc}"
            ) from exc
        finally:
            if discard_rollback and rollback_dir.exists():
                discard_snapshot(rollback_dir)


def _create_restore_guard(manifest: dict, target_root: Path) -> tuple[Path, list[Path]]:
    guard = Path(tempfile.mkdtemp(prefix=".cleaner-restore-", dir=target_root))
    missing: list[Path] = []
    for entry in manifest.get("files", []):
        relative = entry["relative_path"]
        target = contained_path(target_root, relative)
        guard_target = contained_path(guard, relative)
        if target.is_file():
            guard_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, guard_target)
        else:
            missing.append(target)
    return guard, missing


def _restore_guard(guard: Path, target_root: Path, manifest: dict, missing: list[Path]) -> None:
    for target in missing:
        target.unlink(missing_ok=True)
    for entry in manifest.get("files", []):
        source = contained_path(guard, entry["relative_path"])
        if source.is_file():
            target = contained_path(target_root, entry["relative_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def restore_backup(backup_dir: Path, codex_home: Path | None = None, force: bool = False) -> int:
    require_codex_stopped()
    backup_dir = backup_dir.expanduser().resolve()
    manifest = verify_backup(backup_dir)
    if manifest.get("backup_type") not in RESTORABLE_BACKUP_TYPES:
        raise RuntimeError("Session archives are export-only and cannot overwrite Codex state.")
    target_root = (codex_home or default_codex_home()).expanduser().resolve()
    if not force:
        raise RuntimeError("Restoring a state snapshot requires explicit --force confirmation.")
    target_root.mkdir(parents=True, exist_ok=True)
    guard, missing = _create_restore_guard(manifest, target_root)
    restored = 0
    try:
        require_codex_stopped()
        for entry in manifest.get("files", []):
            source = contained_path(backup_dir, entry["relative_path"])
            target = contained_path(target_root, entry["relative_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.get("category") == "sqlite":
                for suffix in ("-wal", "-shm"):
                    Path(str(target) + suffix).unlink(missing_ok=True)
            shutil.copy2(source, target)
            if entry.get("category") == "sqlite":
                connection = sqlite3.connect(target)
                try:
                    check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                finally:
                    connection.close()
                if check != "ok":
                    raise RuntimeError(f"Restored SQLite failed quick_check: {target}: {check}")
            restored += 1
        update_manifest(
            backup_dir,
            manifest,
            last_restored_at=datetime.now(timezone.utc).isoformat(),
            last_restore_file_count=restored,
        )
        shutil.rmtree(guard)
        return restored
    except Exception:
        _restore_guard(guard, target_root, manifest, missing)
        shutil.rmtree(guard, ignore_errors=True)
        raise
