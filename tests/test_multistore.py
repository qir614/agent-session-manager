from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_session_cleaner.executor import CleanupExecutor
from codex_session_cleaner.maintenance import compact_log_databases, log_database_stats
from codex_session_cleaner.models import SessionStatus
from codex_session_cleaner.planner import build_plan
from codex_session_cleaner.scanner import SessionScanner

from tests.helpers import CHILD_ID, USER_ID, create_fixture


GHOST_ID = "00000000-0000-4000-8000-000000000101"
CLOUD_ID = "00000000-0000-4000-8000-000000000102"
PROJECT_ID = "00000000-0000-4000-8000-000000000103"


def create_catalog(home: Path) -> None:
    folder = home / "sqlite"
    folder.mkdir()
    connection = sqlite3.connect(folder / "codex-dev.db")
    connection.executescript("""
        CREATE TABLE local_thread_catalog (
            host_id TEXT, thread_id TEXT, display_title TEXT, cwd TEXT,
            source_kind TEXT, project_id TEXT, missing_candidate INTEGER DEFAULT 0
        );
        CREATE TABLE local_thread_catalog_metadata (
            id INTEGER PRIMARY KEY, catalog_revision INTEGER
        );
        CREATE TABLE thread_timeline_ledger (thread_id TEXT);
        CREATE TABLE inbox_items (thread_id TEXT);
        CREATE TABLE automation_runs (thread_id TEXT);
    """)
    connection.execute("INSERT INTO local_thread_catalog_metadata VALUES (1, 7)")
    connection.executemany(
        "INSERT INTO local_thread_catalog VALUES (?,?,?,?,?,?,0)",
        [
            ("local", USER_ID, "renamed parent", "C:/demo", "vscode", PROJECT_ID),
            ("local", GHOST_ID, "ghost", "C:/demo", "vscode", PROJECT_ID),
            ("chatgpt:test", CLOUD_ID, "cloud", "", "chatgpt", ""),
        ],
    )
    connection.commit()
    connection.close()


def create_reference_databases(home: Path) -> None:
    connection = sqlite3.connect(home / "thread_history_1.sqlite")
    connection.executescript("""
        CREATE TABLE thread_items (thread_id TEXT);
        CREATE TABLE thread_turns (thread_id TEXT);
        CREATE TABLE thread_history_projection_state (thread_id TEXT);
    """)
    connection.executemany("INSERT INTO thread_items VALUES (?)", [(USER_ID,), (CHILD_ID,)])
    connection.commit()
    connection.close()
    connection = sqlite3.connect(home / "logs_2.sqlite")
    connection.execute("CREATE TABLE logs (thread_id TEXT, payload TEXT)")
    connection.executemany("INSERT INTO logs VALUES (?,?)", [(USER_ID, "a"), (CHILD_ID, "b")])
    connection.commit()
    connection.close()


def create_multistore_fixture(home: Path) -> None:
    create_fixture(home)
    create_catalog(home)
    create_reference_databases(home)
    index = home / "session_index.jsonl"
    with index.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"id": USER_ID, "thread_name": "renamed parent"}) + "\n")
    state = json.loads((home / ".codex-global-state.json").read_text(encoding="utf-8"))
    state.update({
        "local-projects": {PROJECT_ID: {
            "id": PROJECT_ID, "name": "Real Project", "rootPaths": ["C:/demo"]
        }},
        "thread-project-assignments": {
            USER_ID: {"projectKind": "local", "projectId": PROJECT_ID}
        },
        "sidebar-project-thread-orders": {
            PROJECT_ID: {"threadIds": [USER_ID, GHOST_ID]}
        },
        "thread-writable-roots": {USER_ID: ["C:/demo"]},
    })
    (home / ".codex-global-state.json").write_text(json.dumps(state), encoding="utf-8")


class MultiStoreTests(unittest.TestCase):
    def test_scan_uses_latest_title_real_project_and_catalog_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_multistore_fixture(home)
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.sessions[USER_ID].title, "renamed parent")
            self.assertEqual(scan.sessions[USER_ID].project_name, "Real Project")
            self.assertEqual(scan.status_by_id[GHOST_ID], SessionStatus.LOCAL_GHOST)
            self.assertEqual(scan.status_by_id[CLOUD_ID], SessionStatus.CLOUD_CATALOG)

    @patch("codex_session_cleaner.executor.require_codex_stopped")
    def test_delete_removes_all_core_and_log_references(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_multistore_fixture(home)
            scan = SessionScanner(home).scan()
            report = CleanupExecutor().execute(build_plan(scan, [USER_ID]))
            self.assertEqual(set(report.deleted_ids), {USER_ID, CHILD_ID})
            catalog = sqlite3.connect(home / "sqlite" / "codex-dev.db")
            ids = {row[0] for row in catalog.execute("SELECT thread_id FROM local_thread_catalog")}
            catalog.close()
            self.assertNotIn(USER_ID, ids)
            self.assertIn(GHOST_ID, ids)
            self.assertIn(CLOUD_ID, ids)
            history = sqlite3.connect(home / "thread_history_1.sqlite")
            self.assertEqual(history.execute("SELECT COUNT(*) FROM thread_items").fetchone()[0], 0)
            history.close()
            logs = sqlite3.connect(home / "logs_2.sqlite")
            self.assertEqual(logs.execute("SELECT COUNT(*) FROM logs").fetchone()[0], 0)
            logs.close()
            global_state = json.loads((home / ".codex-global-state.json").read_text())
            self.assertNotIn(USER_ID, global_state["thread-project-assignments"])
            self.assertNotIn(USER_ID, global_state["sidebar-project-thread-orders"][PROJECT_ID]["threadIds"])
            self.assertNotIn(USER_ID, global_state["thread-writable-roots"])

    @patch("codex_session_cleaner.maintenance.require_codex_stopped")
    def test_compaction_preserves_rows_and_releases_space(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            connection = sqlite3.connect(home / "logs_2.sqlite")
            connection.execute("CREATE TABLE logs (thread_id TEXT, payload BLOB)")
            connection.executemany(
                "INSERT INTO logs VALUES (?,?)",
                [(str(index), b"x" * 4096) for index in range(500)],
            )
            connection.commit()
            connection.execute("DELETE FROM logs WHERE CAST(thread_id AS INTEGER) < 450")
            connection.commit()
            connection.close()
            before = log_database_stats(home)[0]
            result = compact_log_databases(home)[0]
            after = log_database_stats(home)[0]
            self.assertEqual(before["rows"], after["rows"])
            self.assertLess(result["after_bytes"], result["before_bytes"])
            self.assertEqual(after["integrity"], "ok")


if __name__ == "__main__":
    unittest.main()
