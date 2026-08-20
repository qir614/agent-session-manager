from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_session_cleaner.backup import verify_backup
from codex_session_cleaner.executor import CleanupExecutor, restore_backup
from codex_session_cleaner.planner import build_plan
from codex_session_cleaner.scanner import SessionScanner

from tests.helpers import CHILD_ID, ORPHAN_ID, USER_ID, create_fixture


class ExecutorTests(unittest.TestCase):
    @patch("codex_session_cleaner.executor.require_codex_stopped")
    def test_cleanup_backs_up_and_updates_all_known_stores(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            scan = SessionScanner(home).scan()
            orphan_path = scan.sessions[ORPHAN_ID].path
            plan = build_plan(scan, [ORPHAN_ID])
            report = CleanupExecutor().execute(plan, persistent_backup=True)

            self.assertFalse(orphan_path.exists())
            self.assertTrue(scan.sessions[USER_ID].path.exists())
            manifest = verify_backup(report.backup_dir)
            self.assertEqual(manifest["status"], "cleanup_complete")

            index_ids = {
                json.loads(line)["id"]
                for line in (home / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
            }
            self.assertEqual(index_ids, {USER_ID})
            global_state = json.loads((home / ".codex-global-state.json").read_text(encoding="utf-8"))
            self.assertNotIn(ORPHAN_ID, global_state["projectless-thread-ids"])
            self.assertNotIn(ORPHAN_ID, global_state["thread-projectless-output-directories"])

            connection = sqlite3.connect(home / "state_5.sqlite")
            ids = {row[0] for row in connection.execute("SELECT id FROM threads")}
            edges = set(connection.execute(
                "SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"
            ))
            connection.close()
            self.assertNotIn(ORPHAN_ID, ids)
            self.assertIn((USER_ID, CHILD_ID), edges)

    @patch("codex_session_cleaner.executor.require_codex_stopped")
    def test_restore_recovers_deleted_file_and_state(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            scan = SessionScanner(home).scan()
            orphan_path = scan.sessions[ORPHAN_ID].path
            report = CleanupExecutor().execute(
                build_plan(scan, [ORPHAN_ID]), persistent_backup=True
            )
            restored = restore_backup(report.backup_dir, home, force=True)
            self.assertGreater(restored, 0)
            self.assertTrue(orphan_path.exists())
            connection = sqlite3.connect(home / "state_5.sqlite")
            ids = {row[0] for row in connection.execute("SELECT id FROM threads")}
            connection.close()
            self.assertIn(ORPHAN_ID, ids)

    @patch("codex_session_cleaner.executor.require_codex_stopped")
    def test_cleanup_does_not_keep_backup_by_default(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            scan = SessionScanner(home).scan()
            report = CleanupExecutor().execute(build_plan(scan, [ORPHAN_ID]))
            self.assertIsNone(report.backup_dir)
            self.assertIsNone(report.manifest_path)
            self.assertFalse(scan.sessions[ORPHAN_ID].path.exists())


if __name__ == "__main__":
    unittest.main()
