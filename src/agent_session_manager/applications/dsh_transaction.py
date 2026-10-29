"""DSH Linux cleanup transaction: same-filesystem staging, not a CLI proxy."""
from __future__ import annotations
import json
import os
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path

from .dsh import filter_workspace, load_object, safe_path
from ..models import ExecutionReport
from ..state_store import _atomic_write


def _move(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)


def execute(application, plan, persistent_backup=False, backup_directory=None):
    if plan.application != "dsh":
        raise ValueError("DSH executor refuses another application's plan")
    if os.name != "posix":
        raise ValueError("DSH deletion currently requires Linux flock; this platform supports inspection/export only")
    import fcntl
    home = plan.codex_home
    def stopped():
        running = application.running(home)
        if running:
            raise RuntimeError("Close DSH before local cleanup. Detected: " + ", ".join(running))
    stopped()
    pending = list(home.glob(".cleaner-dsh-transaction-*"))
    if pending:
        raise RuntimeError("Unfinished DSH transaction: inspect recovery.json before continuing: " + str(pending[0]))
    scan = application.scan(home, plan.session_root)
    current = application.plan(scan, plan.requested_ids)
    if current.fingerprint != plan.fingerprint:
        raise RuntimeError("DSH data changed after preview; scan and preview again")
    deletion = set(plan.deletion_ids)
    workspace = home / "storages/workspace.json"
    original = workspace.read_bytes() if workspace.is_file() else None
    updated, removed = filter_workspace(load_object(home, workspace), deletion)
    backup_dir = manifest_path = None
    if persistent_backup:
        backup_dir, _ = application.export(scan, plan.requested_ids, backup_directory or home / "session_manager_backups")
        manifest_path = backup_dir / "manifest.json"
    sources = [scan.sessions[sid].path.parent for sid in plan.deletion_ids]
    sources += [item.path for item in plan.items if item.reason == "projection-cache"]
    impact = application.impact(scan, plan)
    locks = ExitStack()
    transaction = Path(tempfile.mkdtemp(prefix=".cleaner-dsh-transaction-", dir=home))
    journal = transaction / "recovery.json"
    moved = []
    committed = False
    discard = False
    warnings = list(plan.warnings)
    def checkpoint(state):
        _atomic_write(journal, json.dumps({"application": "dsh", "state": state,
            "home": str(home), "moved": moved, "workspace_existed": original is not None,
            "workspace": "storages/workspace.json"}, ensure_ascii=False, indent=2))
    try:
        if original is not None:
            (transaction / "workspace.original.json").write_bytes(original)
        checkpoint("prepared")
        for sid in plan.deletion_ids:
            lock_path = scan.sessions[sid].path.parent / "session.lock"
            safe_path(home, lock_path)
            if lock_path.is_file():
                stream = locks.enter_context(lock_path.open("rb"))
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("DSH session has an active writer: " + sid) from exc
                if os.fstat(stream.fileno()).st_ino != lock_path.stat().st_ino:
                    raise RuntimeError("DSH lock inode changed")
        stopped()
        if application.plan(application.scan(home, plan.session_root), plan.requested_ids).fingerprint != plan.fingerprint:
            raise RuntimeError("DSH data changed before mutation")
        for source in sources:
            safe_path(home, source)
            relative = source.relative_to(home).as_posix()
            # Record intent before rename so an interrupted move is recoverable.
            moved.append(relative); checkpoint("moving")
            _move(source, transaction / "data" / relative)
        if original is not None:
            _atomic_write(workspace, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
        checkpoint("verifying")
        remaining = application.scan(home, plan.session_root)
        if deletion.intersection(remaining.sessions) or any(source.exists() for source in sources):
            raise RuntimeError("DSH verification found retained selected data")
        if any(issue.blocking for issue in remaining.issues):
            raise RuntimeError("DSH post-cleanup scan has blocking issues")
        if workspace.is_file():
            _, residual = filter_workspace(load_object(home, workspace), deletion)
            if residual:
                raise RuntimeError("DSH workspace references remain")
        checkpoint("committed")
        committed = True
        discard = True
    except BaseException as error:
        try:
            for relative in reversed(moved):
                source = transaction / "data" / relative
                target = home / relative
                if source.exists():
                    if target.exists():
                        raise RuntimeError("Cannot overwrite newly created DSH data: " + relative)
                    _move(source, target)
            if original is not None:
                _atomic_write(workspace, original.decode("utf-8"))
            checkpoint("rolled_back")
            discard = True
        except BaseException as recovery_error:
            raise RuntimeError(f"DSH rollback failed; retained {journal}: {recovery_error}") from error
        raise
    finally:
        locks.close()
        if discard:
            try:
                shutil.rmtree(transaction)
            except OSError as error:
                if committed:
                    warnings.append(f"Deletion committed, but staged residue needs removal: {transaction}: {error}")
                else:
                    # Originals were restored; keep evidence if empty staging removal fails.
                    pass
    return ExecutionReport(backup_dir, plan.deletion_ids,
        [str(item.relative_path) for item in plan.items], [], 0, removed, {}, manifest_path,
        warnings=warnings, verification={}, impact=impact,
        unresolved_artifacts=["Global shared attachments and their request caches retained"])
