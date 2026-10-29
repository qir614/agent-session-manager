"""Independent cleanup: real fixture files/SQLite, with process execution forbidden."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_session_manager.executor import CleanupExecutor, restore_backup
from agent_session_manager.models import SessionStatus
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner
from agent_session_manager.storage import locations
from tests.helpers import USER_ID, CHILD_ID, write_rollout, create_state_db
from tests.helpers import open_sqlite


class LocalCleanupContractTests(unittest.TestCase):
    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_cleanup_without_codex_and_history_restore(self, _guard):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            selected = write_rollout(home, USER_ID, thread_source="user")
            retained = write_rollout(home, CHILD_ID, thread_source="user")
            create_state_db(home, [(USER_ID, "user"), (CHILD_ID, "user")], [])
            records = [{"session_id": USER_ID, "text": "private"},
                       {"session_id": CHILD_ID, "text": USER_ID},
                       {"session_id": USER_ID + "-suffix", "text": "keep"},
                       {"text": "unowned: keep"}]
            original = "".join(json.dumps(row) + "\n" for row in records)
            history = home / "history.jsonl"
            history.write_text(original, encoding="utf-8")
            with patch("subprocess.run", side_effect=AssertionError("Cleanup must not launch Codex")), \
                 patch("subprocess.Popen", side_effect=AssertionError("Cleanup must not launch app-server")):
                report = CleanupExecutor().execute(build_plan(SessionScanner(home).scan(), [USER_ID]), persistent_backup=True)
            self.assertFalse(selected.exists())
            self.assertTrue(retained.exists())
            self.assertEqual(report.history_entries_removed, 1)
            self.assertEqual([json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()], records[1:])
            with open_sqlite(home / "state_5.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT id FROM threads").fetchall(), [(CHILD_ID,)])
            restore_backup(report.backup_dir, home, force=True)
            self.assertEqual(history.read_text(encoding="utf-8"), original)
            self.assertTrue(selected.exists())

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_history_is_restored_on_late_failure(self, _guard):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            rollout = write_rollout(home, USER_ID, thread_source="user")
            history = home / "history.jsonl"
            original = json.dumps({"session_id": USER_ID, "text": "restore me"}) + "\n"
            history.write_text(original, encoding="utf-8")
            with patch("agent_session_manager.executor.verify_no_references", side_effect=RuntimeError("late failure")):
                with self.assertRaisesRegex(RuntimeError, "late failure"):
                    CleanupExecutor().execute(build_plan(SessionScanner(home).scan(), [USER_ID]))
            self.assertEqual(history.read_text(encoding="utf-8"), original)
            self.assertTrue(rollout.exists())
            self.assertFalse(list(home.glob(".cleaner-transaction*")))

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_malformed_history_does_not_mutate_data(self, _guard):
        for contents in ("{broken\n", "[]\n"):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                rollout = write_rollout(home, USER_ID, thread_source="user")
                (home / "history.jsonl").write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "history.jsonl"):
                    CleanupExecutor().execute(build_plan(SessionScanner(home).scan(), [USER_ID]))
                self.assertTrue(rollout.exists())
                self.assertEqual((home / "history.jsonl").read_text(encoding="utf-8"), contents)

    def test_external_storage_is_scanned_but_cannot_be_partly_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home, external = base / "home", base / "database"
            home.mkdir(); external.mkdir()
            write_rollout(home, USER_ID, thread_source="user")
            database = create_state_db(external, [(USER_ID, "user")], [])
            original = database.read_bytes()
            (home / "config.toml").write_text("sqlite_home = " + json.dumps(str(external)), encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_SQLITE_HOME": str(home)}):
                self.assertEqual(locations(home).sqlite, external)
                scan = SessionScanner(home).scan()
                self.assertTrue(scan.sqlite_available)
                self.assertTrue(scan.sessions[USER_ID].title.startswith("title-"))
                with self.assertRaisesRegex(ValueError, "cross-directory rollback"):
                    build_plan(scan, [USER_ID])
            self.assertEqual(database.read_bytes(), original)

    def test_paginated_child_cannot_be_deleted_indirectly(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="user")
            write_rollout(home, CHILD_ID, thread_source="subagent", parent_id=USER_ID)
            create_state_db(home, [(USER_ID, "user"), (CHILD_ID, "subagent")], [(USER_ID, CHILD_ID)])
            with open_sqlite(home / "state_5.sqlite") as connection:
                connection.execute("ALTER TABLE threads ADD COLUMN history_mode TEXT")
                connection.execute("UPDATE threads SET history_mode='paginated' WHERE id=?", (CHILD_ID,))
            scan = SessionScanner(home).scan()
            with self.assertRaisesRegex(ValueError, "Unsupported history"):
                build_plan(scan, [USER_ID])

    def test_paginated_root_is_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            create_state_db(home, [(USER_ID, "user")], [])
            with open_sqlite(home / "state_5.sqlite") as connection:
                connection.execute("ALTER TABLE threads ADD COLUMN history_mode TEXT")
                connection.execute("UPDATE threads SET history_mode='paginated'")
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[USER_ID], SessionStatus.PAGINATED)
            with self.assertRaises(ValueError):
                build_plan(scan, [USER_ID])

    def test_absent_rollout_does_not_prove_garbage(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            create_state_db(home, [(USER_ID, "user")], [])
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[USER_ID], SessionStatus.UNKNOWN)
            with self.assertRaises(ValueError):
                build_plan(scan, [USER_ID])


class PaginatedCleanupTests(unittest.TestCase):
    def fixture(self, home):
        write_rollout(home, USER_ID, thread_source="user")
        write_rollout(home, CHILD_ID, thread_source="user")
        create_state_db(home, [(USER_ID, "user"), (CHILD_ID, "user")], [])
        with open_sqlite(home / "state_5.sqlite") as connection:
            connection.execute("ALTER TABLE threads ADD COLUMN history_mode TEXT")
            connection.execute("UPDATE threads SET history_mode='paginated'")
            connection.execute("CREATE TABLE thread_attachments(id TEXT PRIMARY KEY, thread_id TEXT, payload TEXT)")
            connection.executemany("INSERT INTO thread_attachments VALUES (?, ?, '{}')", [("a", USER_ID), ("b", CHILD_ID)])
        with open_sqlite(home / "thread_history_1.sqlite") as connection:
            connection.executescript("""
                CREATE TABLE thread_turns(thread_id TEXT, turn_id TEXT, rollout_ordinal INTEGER);
                CREATE TABLE thread_items(thread_id TEXT, turn_id TEXT, item_id TEXT, item_json TEXT, rollout_ordinal INTEGER);
                CREATE TABLE thread_history_projection_state(thread_id TEXT, next_rollout_byte_offset INTEGER, next_rollout_ordinal INTEGER);
                CREATE TABLE thread_realtime_items(thread_id TEXT, item_id TEXT, item_json TEXT, rollout_ordinal INTEGER);
            """)
            for sid in [USER_ID, CHILD_ID]:
                connection.execute("INSERT INTO thread_turns VALUES (?, 't', 1)", (sid,))
                connection.execute("INSERT INTO thread_items VALUES (?, 't', 'i', '{}', 1)", (sid,))
                connection.execute("INSERT INTO thread_history_projection_state VALUES (?, 0, 1)", (sid,))
                connection.execute("INSERT INTO thread_realtime_items VALUES (?, 'r', '{}', 1)", (sid,))

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_paginated_local_cleanup_covers_every_table_and_restores(self, _guard):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.fixture(home)
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[USER_ID], SessionStatus.USER_ROOT)
            with patch("subprocess.Popen", side_effect=AssertionError("No Codex process")):
                report = CleanupExecutor().execute(build_plan(scan, [USER_ID]), persistent_backup=True)
            with open_sqlite(home / "thread_history_1.sqlite") as connection:
                for table in ["thread_turns", "thread_items", "thread_history_projection_state", "thread_realtime_items"]:
                    self.assertEqual(connection.execute(f"SELECT thread_id FROM {table}").fetchall(), [(CHILD_ID,)])
            with open_sqlite(home / "state_5.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT thread_id FROM thread_attachments").fetchall(), [(CHILD_ID,)])
            restore_backup(report.backup_dir, home, force=True)
            with open_sqlite(home / "thread_history_1.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_realtime_items").fetchone()[0], 2)

    def test_unknown_history_table_blocks_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.fixture(home)
            with open_sqlite(home / "thread_history_1.sqlite") as connection:
                connection.execute("CREATE TABLE future_history(thread_id TEXT)")
            with self.assertRaisesRegex(ValueError, "Unsupported history table"):
                build_plan(SessionScanner(home).scan(), [USER_ID])
            self.assertTrue(SessionScanner(home).scan().sessions[USER_ID].path.exists())

    def test_retained_fork_dependency_blocks_history_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.fixture(home)
            scan = SessionScanner(home).scan()
            path = scan.sessions[CHILD_ID].path
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            events[0]["payload"]["forked_from_id"] = USER_ID
            path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
            scan = SessionScanner(home).scan()
            self.assertNotIn(CHILD_ID, scan.parent_by_child)
            with self.assertRaisesRegex(ValueError, "Retained forks"):
                build_plan(scan, [USER_ID])
            self.assertEqual(build_plan(scan, [CHILD_ID]).deletion_ids, [CHILD_ID])

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_paginated_failure_restores_history_and_attachment_metadata(self, _guard):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            with patch("agent_session_manager.executor.verify_no_references", side_effect=RuntimeError("late failure")):
                with self.assertRaisesRegex(RuntimeError, "late failure"):
                    CleanupExecutor().execute(plan)
            with open_sqlite(home / "thread_history_1.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_realtime_items").fetchone()[0], 2)
            with open_sqlite(home / "state_5.sqlite") as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM thread_attachments").fetchone()[0], 2)
