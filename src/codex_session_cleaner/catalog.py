from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CatalogThread:
    host_id: str
    thread_id: str
    title: str
    cwd: str
    source_kind: str
    project_id: str


def latest_index_titles(codex_home: Path) -> dict[str, str]:
    path = codex_home / "session_index.jsonl"
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for line in stream:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("session_index entry is not an object")
            session_id = str(value.get("id") or "").lower()
            title = str(value.get("thread_name") or "").strip()
            if session_id and title:
                result[session_id] = title
    return result


def read_catalog(codex_home: Path) -> tuple[list[CatalogThread], int, str]:
    path = codex_home / "sqlite" / "codex-dev.db"
    if not path.is_file():
        return [], 0, "missing"
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "local_thread_catalog" not in tables:
            return [], 0, check
        rows = [CatalogThread(
            host_id=str(row[0]),
            thread_id=str(row[1]).lower(),
            title=str(row[2] or ""),
            cwd=str(row[3] or ""),
            source_kind=str(row[4] or ""),
            project_id=str(row[5] or ""),
        ) for row in connection.execute(
            "SELECT host_id,thread_id,display_title,cwd,source_kind,project_id "
            "FROM local_thread_catalog WHERE missing_candidate=0"
        )]
        revision = 0
        if "local_thread_catalog_metadata" in tables:
            found = connection.execute(
                "SELECT catalog_revision FROM local_thread_catalog_metadata WHERE id=1"
            ).fetchone()
            revision = int(found[0]) if found else 0
        return rows, revision, check
    finally:
        connection.close()


def read_projects(codex_home: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    path = codex_home / ".codex-global-state.json"
    if not path.is_file():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        return {}, {}
    raw_projects = data.get("local-projects")
    projects: dict[str, dict[str, Any]] = {}
    if isinstance(raw_projects, dict):
        for project_id, value in raw_projects.items():
            if not isinstance(value, dict):
                continue
            roots = value.get("rootPaths")
            projects[str(project_id)] = {
                "id": str(project_id),
                "name": str(value.get("name") or ""),
                "root_paths": [str(item) for item in roots] if isinstance(roots, list) else [],
            }
    assignments: dict[str, str] = {}
    raw_assignments = data.get("thread-project-assignments")
    if isinstance(raw_assignments, dict):
        for thread_id, value in raw_assignments.items():
            if isinstance(value, dict) and value.get("projectKind") == "local":
                assignments[str(thread_id).lower()] = str(value.get("projectId") or "")
    orders = data.get("sidebar-project-thread-orders")
    if isinstance(orders, dict):
        for project_id, value in orders.items():
            if not isinstance(value, dict):
                continue
            ids = value.get("threadIds")
            if isinstance(ids, list):
                for thread_id in ids:
                    assignments.setdefault(str(thread_id).lower(), str(project_id))
    return projects, assignments
