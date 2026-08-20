from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    ScanIssue,
    ScanResult,
    SessionKind,
    SessionRecord,
    SessionStatus,
)
from .catalog import latest_index_titles, read_catalog, read_projects


SESSION_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
SESSION_DIR_NAMES = ("sessions", "archived_sessions")


def default_codex_home() -> Path:
    import os

    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _read_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, UnicodeError):
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") in {"input_text", "text"}:
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _is_injected_message(text: str) -> bool:
    prefixes = (
        "<environment_context>",
        "<recommended_plugins>",
        "# AGENTS.md instructions",
        "<permissions instructions>",
        "<app-context>",
        "<collaboration_mode>",
        "<skills_instructions>",
        "<plugins_instructions>",
    )
    return text.lstrip().startswith(prefixes)


def _classify(payload: dict[str, Any]) -> tuple[SessionKind, str | None]:
    thread_source = str(payload.get("thread_source") or "").casefold()
    parent = payload.get("parent_thread_id")
    source = payload.get("source")
    if isinstance(source, dict):
        subagent = source.get("subagent")
        if isinstance(subagent, dict):
            spawn = subagent.get("thread_spawn")
            if isinstance(spawn, dict):
                parent = parent or spawn.get("parent_thread_id")
            if thread_source != "user":
                return SessionKind.SUBAGENT, str(parent) if parent else None
    if thread_source == "user":
        return SessionKind.USER, None
    if thread_source == "subagent" or parent:
        return SessionKind.SUBAGENT, str(parent) if parent else None
    if isinstance(source, dict) and "subagent" in source:
        return SessionKind.INTERNAL, None
    return SessionKind.UNKNOWN, None


def parse_rollout(path: Path, codex_home: Path) -> SessionRecord:
    metadata: dict[str, Any] | None = None
    first_user = ""
    created_at = ""
    updated_at = ""
    invalid_lines = 0
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeError):
                    invalid_lines += 1
                    continue
                if not isinstance(event, dict):
                    continue
                timestamp = str(event.get("timestamp") or "")
                created_at = created_at or timestamp
                updated_at = timestamp or updated_at
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event.get("type") == "session_meta" and metadata is None:
                    metadata = payload
                if not first_user and event.get("type") == "response_item":
                    if payload.get("type") == "message" and payload.get("role") == "user":
                        text = _extract_text(payload.get("content"))
                        if text and not _is_injected_message(text):
                            first_user = text.splitlines()[0][:160]
                if metadata is not None and first_user:
                    break
    except OSError as exc:
        metadata = None
        invalid_lines += 1
        first_user = f"Unreadable: {exc}"

    match = SESSION_ID_RE.search(path.name)
    fallback_id = match.group(0).lower() if match else path.stem
    payload = metadata or {}
    session_id = str(payload.get("id") or payload.get("session_id") or fallback_id).lower()
    kind, parent_id = _classify(payload)
    root_name = "archived_sessions" if "archived_sessions" in path.parts else "sessions"
    relative = Path(root_name) / path.relative_to(codex_home / root_name)
    warnings: list[str] = []
    if metadata is None:
        warnings.append("missing_session_meta")
    if invalid_lines:
        warnings.append(f"invalid_json_lines:{invalid_lines}")
    stat = path.stat() if path.exists() else None
    filesystem_updated = (
        datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else ""
    )
    return SessionRecord(
        id=session_id,
        path=path,
        relative_path=relative,
        kind=kind,
        parent_id=parent_id.lower() if parent_id else None,
        title=first_user,
        cwd=str(payload.get("cwd") or ""),
        created_at=str(payload.get("timestamp") or created_at),
        updated_at=filesystem_updated or updated_at,
        source=payload.get("source"),
        archived=root_name == "archived_sessions",
        size=stat.st_size if stat else 0,
        metadata_valid=metadata is not None,
        warnings=warnings,
    )


class SessionScanner:
    def __init__(self, codex_home: Path):
        self.codex_home = codex_home.expanduser().resolve()

    def _files(self) -> list[Path]:
        files: list[Path] = []
        for name in SESSION_DIR_NAMES:
            root = self.codex_home / name
            if root.is_dir():
                files.extend(root.rglob("*.jsonl"))
        return sorted(files)

    def scan(self) -> ScanResult:
        issues: list[ScanIssue] = []
        sessions: dict[str, SessionRecord] = {}
        for path in self._files():
            record = parse_rollout(path, self.codex_home)
            if record.id in sessions:
                issues.append(ScanIssue(str(path), "duplicate_id", record.id, True))
                continue
            sessions[record.id] = record
            if not record.metadata_valid:
                issues.append(ScanIssue(str(path), "missing_session_meta", record.id, True))

        issues.extend(self._validate_json_state())
        try:
            index_titles = latest_index_titles(self.codex_home)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            index_titles = {}
            issues.append(ScanIssue(
                str(self.codex_home / "session_index.jsonl"),
                "invalid_session_index", str(exc), True,
            ))
        try:
            projects, project_assignments = read_projects(self.codex_home)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            projects, project_assignments = {}, {}
            issues.append(ScanIssue(
                str(self.codex_home / ".codex-global-state.json"),
                "invalid_global_state", str(exc), True,
            ))
        try:
            catalog_rows, catalog_revision, catalog_integrity = read_catalog(self.codex_home)
        except (sqlite3.Error, OSError) as exc:
            catalog_rows, catalog_revision, catalog_integrity = [], 0, "error"
            issues.append(ScanIssue(
                str(self.codex_home / "sqlite" / "codex-dev.db"),
                "catalog_read_error", str(exc), True,
            ))
        if catalog_integrity not in {"ok", "missing"}:
            issues.append(ScanIssue(
                str(self.codex_home / "sqlite" / "codex-dev.db"),
                "catalog_integrity", catalog_integrity, True,
            ))
        db_rows, db_edges, integrity, db_issues = self._read_sqlite()
        issues.extend(db_issues)
        for session_id, values in db_rows.items():
            record = sessions.get(session_id)
            if not record:
                db_kind = str(values.get("thread_source") or "").casefold()
                kind = SessionKind.SUBAGENT if db_kind == "subagent" else SessionKind.LOCAL_GHOST
                state_title = str(values.get("name") or values.get("title") or "").strip()
                record = SessionRecord(
                    id=session_id, path=None, relative_path=None, kind=kind,
                    title=index_titles.get(session_id) or state_title.splitlines()[0][:160],
                    cwd=str(values.get("cwd") or ""), source="state_5.sqlite",
                    metadata_valid=True, catalog_host="local", has_rollout=False,
                )
                sessions[session_id] = record
            state_name = str(values.get("name") or "").strip()
            state_title = str(values.get("title") or "").strip()
            if state_name:
                record.title = state_name
            elif index_titles.get(session_id):
                record.title = index_titles[session_id]
            elif state_title:
                record.title = state_title.splitlines()[0][:160]
            record.created_at = record.created_at or str(values.get("created_at") or "")
            record.updated_at = str(values.get("updated_at") or record.updated_at)
            record.archived = bool(values.get("archived", record.archived))
            db_kind = str(values.get("thread_source") or "").casefold()
            if db_kind == "user" and record.has_rollout:
                record.kind = SessionKind.USER
            elif db_kind == "subagent" and record.kind == SessionKind.UNKNOWN:
                record.kind = SessionKind.SUBAGENT

        catalog_by_id: dict[str, Any] = {}
        for catalog in catalog_rows:
            if catalog.host_id == "local":
                catalog_by_id[catalog.thread_id] = catalog
                record = sessions.get(catalog.thread_id)
                if record:
                    if not str(db_rows.get(record.id, {}).get("name") or "").strip():
                        record.title = index_titles.get(record.id) or catalog.title or record.title
                    record.catalog_host = catalog.host_id
                    record.cwd = record.cwd or catalog.cwd
                else:
                    sessions[catalog.thread_id] = SessionRecord(
                        id=catalog.thread_id, path=None, relative_path=None,
                        kind=SessionKind.LOCAL_GHOST, title=catalog.title,
                        cwd=catalog.cwd, source=catalog.source_kind, size=0,
                        metadata_valid=True, catalog_host="local",
                        project_id=catalog.project_id, has_rollout=False,
                    )
            else:
                # Remote catalog rows are visible for inventory, but a local tool must not
                # pretend that deleting a cache row deletes the server-side conversation.
                if catalog.thread_id not in sessions:
                    sessions[catalog.thread_id] = SessionRecord(
                        id=catalog.thread_id, path=None, relative_path=None,
                        kind=SessionKind.CLOUD, title=catalog.title,
                        cwd=catalog.cwd, source=catalog.source_kind, size=0,
                        metadata_valid=True, catalog_host=catalog.host_id,
                        project_id=catalog.project_id, has_rollout=False,
                    )

        for session_id, title in index_titles.items():
            if session_id and session_id not in sessions:
                sessions[session_id] = SessionRecord(
                    id=session_id, path=None, relative_path=None,
                    kind=SessionKind.LOCAL_GHOST, title=title,
                    source="session_index", metadata_valid=True,
                    catalog_host="local", has_rollout=False,
                )
        for session_id, project_id in project_assignments.items():
            if session_id and session_id not in sessions:
                sessions[session_id] = SessionRecord(
                    id=session_id, path=None, relative_path=None,
                    kind=SessionKind.LOCAL_GHOST, title="(侧边栏残留)",
                    source="global_state", metadata_valid=True,
                    catalog_host="local", project_id=project_id, has_rollout=False,
                )
        for session_id, record in sessions.items():
            project_id = project_assignments.get(session_id) or record.project_id
            project = projects.get(project_id, {})
            record.project_id = project_id
            record.project_name = str(project.get("name") or "")
            roots = project.get("root_paths") or []
            record.project_path = str(roots[0]) if roots else ""

        parent_by_child: dict[str, str] = {}
        for parent, child in db_edges:
            if child in sessions:
                previous = parent_by_child.get(child)
                if previous and previous != parent:
                    issues.append(ScanIssue(
                        str(sessions[child].path), "multiple_parents",
                        f"{child}: {previous} vs {parent}", True,
                    ))
                else:
                    parent_by_child[child] = parent
        for record in sessions.values():
            if record.parent_id:
                previous = parent_by_child.get(record.id)
                if previous and previous != record.parent_id:
                    issues.append(ScanIssue(
                        str(record.path), "parent_conflict",
                        f"{record.id}: SQLite={previous}, rollout={record.parent_id}", True,
                    ))
                else:
                    parent_by_child.setdefault(record.id, record.parent_id)

        children: dict[str, set[str]] = {}
        for child, parent in parent_by_child.items():
            children.setdefault(parent, set()).add(child)

        cycle_ids = self._dependency_cycles(parent_by_child, sessions)
        if cycle_ids:
            issues.append(ScanIssue(
                str(self.codex_home), "dependency_cycle",
                ",".join(sorted(cycle_ids)), True,
            ))

        status: dict[str, SessionStatus] = {}
        for session_id, record in sessions.items():
            parent = parent_by_child.get(session_id)
            if record.kind == SessionKind.LOCAL_GHOST:
                status[session_id] = SessionStatus.LOCAL_GHOST
            elif record.kind == SessionKind.CLOUD:
                status[session_id] = SessionStatus.CLOUD_CATALOG
            elif session_id in cycle_ids:
                status[session_id] = SessionStatus.UNKNOWN
            elif record.kind == SessionKind.USER:
                status[session_id] = SessionStatus.USER_ROOT
            elif parent and parent in sessions:
                status[session_id] = SessionStatus.ATTACHED
            elif parent and parent not in sessions:
                status[session_id] = SessionStatus.ORPHAN
            elif record.kind in {SessionKind.SUBAGENT, SessionKind.INTERNAL}:
                status[session_id] = SessionStatus.UNLINKED_INTERNAL
            else:
                status[session_id] = SessionStatus.UNKNOWN

        return ScanResult(
            codex_home=self.codex_home,
            sessions=sessions,
            children=children,
            parent_by_child=parent_by_child,
            status_by_id=status,
            issues=issues,
            sqlite_available=(self.codex_home / "state_5.sqlite").is_file(),
            sqlite_integrity=integrity,
            projects=projects,
            catalog_revision=catalog_revision,
        )

    def _validate_json_state(self) -> list[ScanIssue]:
        issues: list[ScanIssue] = []
        index_path = self.codex_home / "session_index.jsonl"
        if index_path.is_file():
            try:
                with index_path.open("r", encoding="utf-8-sig") as stream:
                    for line_number, line in enumerate(stream, 1):
                        if line.strip():
                            value = json.loads(line)
                            if not isinstance(value, dict):
                                raise ValueError("entry is not an object")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                issues.append(ScanIssue(
                    str(index_path), "invalid_session_index", str(exc), True,
                ))
        global_path = self.codex_home / ".codex-global-state.json"
        if global_path.is_file():
            try:
                value = json.loads(global_path.read_text(encoding="utf-8-sig"))
                if not isinstance(value, dict):
                    raise ValueError("root is not an object")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                issues.append(ScanIssue(
                    str(global_path), "invalid_global_state", str(exc), True,
                ))
        return issues

    @staticmethod
    def _dependency_cycles(
        parent_by_child: dict[str, str], sessions: dict[str, SessionRecord]
    ) -> set[str]:
        cycles: set[str] = set()
        for start in sessions:
            order: list[str] = []
            positions: dict[str, int] = {}
            current = start
            while current in sessions and current in parent_by_child:
                if current in positions:
                    cycles.update(order[positions[current]:])
                    break
                positions[current] = len(order)
                order.append(current)
                current = parent_by_child[current]
        return cycles

    def _read_sqlite(self) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]], str, list[ScanIssue]]:
        path = self.codex_home / "state_5.sqlite"
        if not path.is_file():
            return {}, [], "missing", []
        issues: list[ScanIssue] = []
        rows: dict[str, dict[str, Any]] = {}
        edges: list[tuple[str, str]] = []
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
            connection.row_factory = sqlite3.Row
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            table_names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "threads" in table_names:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
                wanted = [name for name in (
                    "id", "name", "title", "cwd", "created_at", "updated_at", "archived", "thread_source"
                ) if name in columns]
                if "id" in wanted:
                    query = "SELECT " + ",".join(f'"{name}"' for name in wanted) + " FROM threads"
                    for row in connection.execute(query):
                        values = dict(row)
                        rows[str(values["id"]).lower()] = values
            if "thread_spawn_edges" in table_names:
                edges = [
                    (str(row[0]).lower(), str(row[1]).lower())
                    for row in connection.execute(
                        "SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"
                    )
                ]
            connection.close()
            if integrity != "ok":
                issues.append(ScanIssue(str(path), "sqlite_integrity", integrity, True))
            return rows, edges, integrity, issues
        except (sqlite3.Error, OSError) as exc:
            issues.append(ScanIssue(str(path), "sqlite_read_error", str(exc), True))
            return {}, [], "error", issues
