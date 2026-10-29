"""DSH adapter: independent local inspection, exports and cleanup planning."""
from __future__ import annotations
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .dsh_format import CANONICAL, header, rows, selected_generation, text_content
from ..backup import contained_path, create_manual_backup
from ..conversation_reader import ConversationDetails, ConversationMessage
from ..models import CleanupPlan, DeletionImpact, PlanItem, RolloutSegment, ScanIssue, ScanResult, SessionKind, SessionRecord, SessionStatus
from ..platforms import get_platform


def safe_path(home: Path, path: Path) -> Path:
    relative = path.absolute().relative_to(home.resolve())
    current = home.resolve()
    for part in relative.parts:
        current = current / part
        if current.exists() and get_platform().is_reparse_point(current):
            raise ValueError(f"Symlink/reparse path is not managed: {relative}")
        if current.is_symlink():
            raise ValueError(f"Symlink path is not managed: {relative}")
    contained_path(home, relative.as_posix())
    return path


def load_object(home, path):
    safe_path(home, path)
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def timestamp(milliseconds):
    return datetime.fromtimestamp(float(milliseconds) / 1000, timezone.utc).isoformat()


def filter_workspace(value, deletion):
    value = json.loads(json.dumps(value))
    if not value:
        return value, 0
    if not isinstance(value.get("global"), dict) or not isinstance(value.get("tables"), dict):
        raise ValueError("Unsupported DSH workspace storage schema")
    removed = 0
    def clean(owner, key):
        nonlocal removed
        if key not in owner:
            return
        items = owner[key]
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise ValueError(f"Unsupported DSH workspace index: {key}")
        owner[key] = [item for item in items if item not in deletion]
        removed += len(items) - len(owner[key])
    for key in ("archivedSessionIds", "pinnedSessionIds"):
        clean(value["global"], key)
    workspaces = value["tables"].get("workspaces", {})
    if not isinstance(workspaces, dict):
        raise ValueError("Unsupported DSH workspaces table")
    for workspace in workspaces.values():
        if not isinstance(workspace, dict):
            raise ValueError("Invalid DSH workspace row")
        clean(workspace, "sessionIds")
    def contains(value):
        if isinstance(value, str):
            return value in deletion
        if isinstance(value, list):
            return any(contains(item) for item in value)
        if isinstance(value, dict):
            return any(key in deletion or contains(item) for key, item in value.items())
        return False
    if contains(value):
        raise ValueError("Unknown DSH workspace references remain; cleanup is blocked")
    return value, removed


class DshApplication:
    id = "dsh"
    label = "DSH"

    def default_home(self):
        return Path(os.environ.get("DSH_HOME", "").strip() or Path.home() / ".dsh").expanduser().resolve()

    def scan(self, home, session_root=None):
        home = home.expanduser().resolve()
        root = (session_root or home / "sessions").expanduser().resolve()
        sessions, children, parents, statuses, issues = {}, {}, {}, {}, []
        encodings = set()
        workspace_path = home / "storages/workspace.json"
        try:
            workspace = load_object(home, workspace_path)
            filter_workspace(workspace, set())
        except (OSError, ValueError) as error:
            workspace = {}
            issues.append(ScanIssue(str(workspace_path), "dsh_workspace", str(error), True))
        archived = set(workspace.get("global", {}).get("archivedSessionIds", []))
        project_by_session = {}
        for key, value in workspace.get("tables", {}).get("workspaces", {}).items():
            for sid in value.get("sessionIds", []):
                project_by_session[sid] = (key, value)
        if root.exists():
            for project in sorted(root.iterdir()):
                if not project.is_dir() or project.is_symlink():
                    if project.is_symlink():
                        issues.append(ScanIssue(str(project), "dsh_symlink", "Unscannable project may hide children", True))
                    continue
                for directory in sorted(project.iterdir()):
                    if not directory.is_dir():
                        continue
                    try:
                        safe_path(root, directory)
                        chosen = selected_generation(directory)
                        if chosen is None:
                            continue
                        version, compressed, path = chosen
                        encodings.add(compressed)
                        meta = header(path, version)
                        sid = meta["id"]
                        if sid in sessions:
                            raise ValueError("Duplicate DSH session identity across directories")
                        parent = meta.get("parentSession") if meta.get("origin") == "subagent" else None
                        if parent is not None and not isinstance(parent, str):
                            raise ValueError("Invalid DSH parentSession")
                        kind = SessionKind.SUBAGENT if meta.get("origin") == "subagent" else SessionKind.USER
                        if version != 4:
                            kind = SessionKind.UNKNOWN
                        canonical = sorted(p for p in directory.iterdir() if CANONICAL.fullmatch(p.name))
                        segments = []
                        for source in canonical:
                            safe_path(root, source)
                            try:
                                relative = source.relative_to(home)
                            except ValueError:
                                relative = None
                            if relative:
                                segments.append(RolloutSegment(source, relative, size=source.stat().st_size))
                        info = project_by_session.get(sid)
                        cwd = str(meta.get("cwd") or "")
                        title = sid
                        cache = home / "storages/session_projcache/sessions" / (directory.name + ".json")
                        cached = load_object(home, cache)
                        record = cached.get("record", {})
                        identity = record.get("identity", {})
                        if all(identity.get(k) == meta.get(k) for k in ("createdAt", "cwd", "isSeeded")) and identity.get("formatVersion") == version:
                            candidate = record.get("rows", {}).get("title", {}).get("val")
                            if isinstance(candidate, str) and candidate.strip():
                                title = candidate.strip()
                        sessions[sid] = SessionRecord(
                            id=sid, path=path, relative_path=path.relative_to(home) if path.is_relative_to(home) else None,
                            kind=kind, parent_id=parent, title=title, cwd=cwd,
                            created_at=timestamp(meta["createdAt"]), updated_at=timestamp(path.stat().st_mtime * 1000),
                            source=meta, archived=sid in archived, size=sum(p.stat().st_size for p in canonical),
                            project_id=info[0] if info else cwd,
                            project_name=str(info[1].get("title") or "") if info else Path(cwd).name,
                            project_path=str(info[1].get("path") or cwd) if info else cwd,
                            segments=segments, thread_source=str(meta.get("origin") or "user"), history_mode=f"dsh-v{version}",
                            warnings=[] if version == 4 else ["Historical/unknown DSH format: inspect/export only; no automatic migration"],
                        )
                        if parent:
                            parents[sid] = parent
                            children.setdefault(parent, set()).add(sid)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        issues.append(ScanIssue(str(directory), "dsh_session", str(error), True))
        if len(encodings) > 1:
            issues.append(ScanIssue(str(root), "dsh_mixed_encoding", "Mixed raw/compressed DSH persistence root", True))
        for sid, record in sessions.items():
            parent = parents.get(sid)
            status = SessionStatus.ATTACHED if parent in sessions else SessionStatus.ORPHAN if parent else SessionStatus.USER_ROOT
            if record.kind == SessionKind.SUBAGENT and not parent:
                status = SessionStatus.UNLINKED_INTERNAL
            if record.kind == SessionKind.UNKNOWN:
                status = SessionStatus.UNKNOWN
            # A cycle is not a usable root or deletion graph.
            seen, current = set(), sid
            while current in parents:
                if current in seen:
                    status = SessionStatus.UNKNOWN
                    issues.append(ScanIssue(str(record.path), "dsh_cycle", "Parent cycle", True))
                    break
                seen.add(current); current = parents[current]
            statuses[sid] = status
        return ScanResult(home, sessions, children, parents, statuses, issues, False, "not-applicable", application="dsh", session_root=root)

    def read(self, record, include_internal=False):
        messages, counts, metadata = [], {}, {}
        surface = []
        for event in rows(record.path):
            kind = str(event.get("type", "")); counts[kind] = counts.get(kind, 0) + 1
            if kind == "session":
                metadata = event
                continue
            if kind not in {"user/message", "assistant/message", "system/message", "developer/message", "tool/result"}:
                continue
            role = kind.split("/")[0]
            data = event.get("data", {})
            source = data.get("source", {}) if isinstance(data, dict) else {}
            source_kind = source.get("kind") if isinstance(source, dict) else None
            internal = role not in {"user", "assistant"} or (role == "user" and source_kind not in {None, "user"})
            text = text_content(data.get("message", data.get("content", data.get("text", ""))), include_internal=include_internal) if isinstance(data, dict) else ""
            operation = event.get("surfaceOp", {})
            if isinstance(operation, dict) and operation.get("op") == "replace":
                start, end = operation.get("startSeq"), operation.get("endSeq")
                if type(start) is not int or type(end) is not int or start > end:
                    raise ValueError("Invalid DSH message replacement range")
                surface = [(seq, message) for seq, message in surface if not start <= seq <= end]
            text = text.strip()
            if text and (include_internal or not internal):
                surface.append((event.get("seq", -1), ConversationMessage(role, text, timestamp(event.get("time", 0)), "", internal)))
        messages = [message for _, message in surface]
        metadata["reader_note"] = "Built-in committed message surface; third-party plugin projections are not executed."
        return ConversationDetails(messages, metadata, sum(counts.values()), 0, counts)

    def plan(self, scan, selected):
        if scan.application != self.id:
            raise ValueError("Application mismatch")
        requested = list(dict.fromkeys(selected))
        if not requested:
            raise ValueError("No DSH sessions selected")
        if any(issue.blocking for issue in scan.issues):
            raise ValueError("DSH scan has blocking issues: " + "; ".join(issue.message for issue in scan.issues if issue.blocking))
        if not scan.session_root.is_relative_to(scan.codex_home):
            raise ValueError("External DSH persistence roots are read-only; cross-root transactions are unsupported")
        deletion, queue = set(), list(requested)
        for sid in requested:
            if sid not in scan.sessions:
                raise ValueError(f"Unknown DSH session: {sid}")
            if scan.parent_by_child.get(sid) in scan.sessions:
                raise ValueError("Select the DSH parent; attached child cannot be deleted alone")
        while queue:
            sid = queue.pop()
            if sid in deletion:
                continue
            deletion.add(sid); queue.extend(scan.children.get(sid, set()))
        items = []
        for sid in sorted(deletion):
            record = scan.sessions[sid]
            if record.history_mode != "dsh-v4":
                raise ValueError("Unsupported DSH format in deletion closure")
            directory = record.path.parent
            # Only verified session-owned directories; identity is never interpolated into a path.
            for path in sorted(directory.rglob("*")):
                safe_path(scan.codex_home, path)
                if path.is_file():
                    items.append(PlanItem(sid, path, path.relative_to(scan.codex_home), "selected" if sid in requested else "descendant", path.stat().st_size))
            for event in rows(record.path):
                pass  # Full checksum/tail validation before offering mutation.
        for sid in sorted(deletion):
            cache = scan.codex_home / "storages/session_projcache/sessions" / (scan.sessions[sid].path.parent.name + ".json")
            safe_path(scan.codex_home, cache)
            if cache.is_file():
                items.append(PlanItem(sid, cache, cache.relative_to(scan.codex_home), "projection-cache", cache.stat().st_size))
        workspace = scan.codex_home / "storages/workspace.json"
        filter_workspace(load_object(scan.codex_home, workspace), deletion)
        digest = hashlib.sha256()
        for item in items:
            digest.update(str(item.relative_path).encode()); digest.update(str(item.size).encode())
            with item.path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
        if workspace.is_file():
            digest.update(workspace.read_bytes())
        digest.update(json.dumps(sorted(deletion)).encode())
        return CleanupPlan(scan.codex_home, requested, sorted(deletion), items, sum(item.size for item in items), datetime.now(timezone.utc).isoformat(), digest.hexdigest(),
            warnings=["Global shared attachments, request-image caches, credentials, settings, projects and previous exports are retained."], application="dsh", session_root=scan.session_root)

    def impact(self, scan, plan):
        _, removed = filter_workspace(load_object(scan.codex_home, scan.codex_home / "storages/workspace.json"), set(plan.deletion_ids))
        return DeletionImpact(len(plan.requested_ids), len(plan.deletion_ids),
            sum(scan.sessions[sid].kind == SessionKind.USER for sid in plan.deletion_ids),
            sum(scan.sessions[sid].kind == SessionKind.SUBAGENT for sid in plan.deletion_ids),
            0, plan.file_count, plan.total_bytes, 0, removed, {}, 0, 0, 0,
            unresolved_artifacts=["Shared DSH attachments are retained; this is not complete privacy erasure."])

    def running(self, home=None):
        import subprocess
        try:
            names = get_platform().process_names()
        except (OSError, subprocess.TimeoutExpired):
            return ["process-check-unavailable"]
        found = {name for name in names if name.lower() in {"dsh", "dsh.exe", "dsh-tauri", "dsh-tauri.exe", "deepseek-harness", "deepseek-harness.exe"}}
        pid_file = (home or self.default_home()) / ".harness.pid"
        if pid_file.is_file():
            try:
                pid = int(pid_file.read_text().splitlines()[0])
                if pid <= 0:
                    raise ValueError("Invalid PID")
                os.kill(pid, 0)
                found.add("DSH runtime (PID file)")
            except ProcessLookupError:
                pass
            except (OSError, ValueError):
                found.add("DSH process state unavailable")
        return sorted(found)

    def export(self, scan, selected_ids, destination):
        if scan.application != self.id:
            raise ValueError("DSH exporter refuses another application's data")
        if any(record.relative_path is None for record in scan.sessions.values()):
            raise ValueError("External DSH persistence roots support inspection only; export path mapping is not implemented")
        directory, manifest = create_manual_backup(scan, selected_ids, destination, include_state=False)
        from ..backup import update_manifest
        update_manifest(directory, manifest, application="dsh")
        return directory, manifest

    def execute(self, plan, **kwargs):
        from .dsh_transaction import execute
        return execute(self, plan, **kwargs)
