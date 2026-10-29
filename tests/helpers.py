from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


USER_ID = "00000000-0000-4000-8000-000000000001"
CHILD_ID = "00000000-0000-4000-8000-000000000002"
ORPHAN_ID = "00000000-0000-4000-8000-000000000003"
MISSING_PARENT_ID = "00000000-0000-4000-8000-000000000004"
GUARDIAN_ID = "00000000-0000-4000-8000-000000000005"
UNKNOWN_ID = "00000000-0000-4000-8000-000000000006"
GRANDCHILD_ID = "00000000-0000-4000-8000-000000000007"


@contextmanager
def open_sqlite(*args, **kwargs):
    """Commit on success, roll back on failure, and always close the handle.

    sqlite3's own connection context manager commits but never closes, which
    leaks the database file on Windows until garbage collection.
    """
    connection = sqlite3.connect(*args, **kwargs)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def write_rollout(
    home: Path,
    session_id: str,
    *,
    thread_source: str | None,
    parent_id: str | None = None,
    guardian: bool = False,
    title: str = "hello",
) -> Path:
    folder = home / "sessions" / "2026" / "01" / "01"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-2026-01-01T00-00-00-{session_id}.jsonl"
    if parent_id:
        source = {"subagent": {"thread_spawn": {"parent_thread_id": parent_id, "depth": 1}}}
    elif guardian:
        source = {"subagent": {"other": "guardian"}}
    else:
        source = "vscode"
    meta = {
        "id": session_id,
        "timestamp": "2026-01-01T00:00:00Z",
        "cwd": "C:/demo",
        "source": source,
    }
    if thread_source is not None:
        meta["thread_source"] = thread_source
    if parent_id:
        meta["parent_thread_id"] = parent_id
    events = [
        {"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta", "payload": meta},
        {"timestamp": "2026-01-01T00:00:01Z", "type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": title}],
        }},
    ]
    path.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
    return path


def create_state_db(home: Path, records: list[tuple[str, str]], edges: list[tuple[str, str]]) -> Path:
    path = home / "state_5.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            name TEXT,
            title TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            archived INTEGER,
            thread_source TEXT
        );
        CREATE TABLE thread_spawn_edges (
            parent_thread_id TEXT,
            child_thread_id TEXT,
            status TEXT
        );
        CREATE TABLE thread_dynamic_tools (thread_id TEXT, name TEXT);
    """)
    for session_id, source in records:
        connection.execute(
            "INSERT INTO threads VALUES (?, NULL, ?, 1, 2, 0, ?)",
            (session_id, f"title-{session_id[-4:]}", source),
        )
    for parent, child in edges:
        connection.execute("INSERT INTO thread_spawn_edges VALUES (?, ?, 'complete')", (parent, child))
    connection.commit()
    connection.close()
    return path


def create_fixture(home: Path, include_unknown: bool = False) -> None:
    write_rollout(home, USER_ID, thread_source="user", title="parent")
    write_rollout(home, CHILD_ID, thread_source="subagent", parent_id=USER_ID, title="child")
    write_rollout(home, ORPHAN_ID, thread_source="subagent", parent_id=MISSING_PARENT_ID, title="orphan")
    write_rollout(home, GUARDIAN_ID, thread_source="subagent", guardian=True, title="guardian")
    records = [
        (USER_ID, "user"),
        (CHILD_ID, "subagent"),
        (ORPHAN_ID, "subagent"),
        (GUARDIAN_ID, "subagent"),
    ]
    if include_unknown:
        write_rollout(home, UNKNOWN_ID, thread_source=None, title="unknown")
        records.append((UNKNOWN_ID, ""))
    create_state_db(home, records, [(USER_ID, CHILD_ID), (MISSING_PARENT_ID, ORPHAN_ID)])
    (home / "session_index.jsonl").write_text(
        json.dumps({"id": ORPHAN_ID, "thread_name": "orphan"}) + "\n" +
        json.dumps({"id": USER_ID, "thread_name": "parent"}) + "\n",
        encoding="utf-8",
    )
    (home / ".codex-global-state.json").write_text(json.dumps({
        "projectless-thread-ids": [USER_ID, ORPHAN_ID],
        "thread-projectless-output-directories": {ORPHAN_ID: "C:/out"},
        "electron-persisted-atom-state": {
            "heartbeat-thread-permissions-by-id": {ORPHAN_ID: {"ok": True}},
            "prompt-history": {USER_ID: ["kept"], ORPHAN_ID: ["remove"]},
            "client-thread-bindings-v1": {"client-a": ORPHAN_ID},
        },
    }), encoding="utf-8")
