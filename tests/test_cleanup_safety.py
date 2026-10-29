from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_session_manager.cleanup_safety import verify_deleted_files_and_json
from agent_session_manager.executor import CleanupExecutor, restore_backup
from agent_session_manager.impact_analyzer import analyze_deletion
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner

from tests.helpers import CHILD_ID, ORPHAN_ID, USER_ID, create_fixture


@patch("agent_session_manager.executor.require_codex_stopped")
class CleanupSafetyTests(unittest.TestCase):
    def test_history_is_previewed_removed_and_restored(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            history = home / "history.jsonl"
            lines = [json.dumps({"session_id": sid, "text": "synthetic"}) for sid in (USER_ID, CHILD_ID, ORPHAN_ID)]
            original = "\n".join(lines) + "\n"
            history.write_text(original)
            scan = SessionScanner(home).scan()
            plan = build_plan(scan, [USER_ID])
            self.assertEqual(analyze_deletion(scan, plan).history_references, 2)
            report = CleanupExecutor().execute(plan, persistent_backup=True)
            self.assertEqual(report.history_entries_removed, 2)
            remaining = [json.loads(line)["session_id"] for line in history.read_text().splitlines()]
            self.assertEqual(remaining, [ORPHAN_ID])
            restore_backup(report.backup_dir, home, force=True)
            self.assertEqual(history.read_text(), original)

    def test_final_json_verification_detects_noop_filter_and_rolls_back(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            with patch("agent_session_manager.executor.filter_session_index", return_value=0):
                with self.assertRaisesRegex(RuntimeError, "session_index.jsonl"):
                    CleanupExecutor().execute(plan)
            self.assertTrue(plan.items[0].path.exists())
            self.assertIn(USER_ID, SessionScanner(home).scan().sessions)

    def test_final_verification_detects_new_duplicate_rollout_and_global_refs(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            found = verify_deleted_files_and_json(plan)
            self.assertIn("session_index.jsonl", found)
            self.assertIn(".codex-global-state.json", found)
            self.assertTrue(any(key.startswith("rollout:") for key in found))

    def test_same_size_rollout_change_invalidates_preview(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            path = next(item.path for item in plan.items if item.id == USER_ID)
            expected_size = next(item.size for item in plan.items if item.id == USER_ID)
            content = path.read_text()
            path.write_text(content.replace('parent"', 'edited"'))
            self.assertEqual(path.stat().st_size, expected_size)
            with self.assertRaisesRegex(RuntimeError, "changed after preview"):
                CleanupExecutor().execute(plan)

    def test_forged_plan_items_are_rejected(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            plan.items.clear()
            with self.assertRaisesRegex(RuntimeError, "changed after preview"):
                CleanupExecutor().execute(plan)

    def test_unrelated_sqlite_reference_blocks_the_final_verification(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            connection = sqlite3.connect(home / "state_5.sqlite")
            connection.execute("CREATE TABLE future_links (linked_thread TEXT)")
            connection.execute("INSERT INTO future_links VALUES (?)", (USER_ID,))
            connection.commit()
            connection.close()
            found = verify_deleted_files_and_json(plan)
            self.assertIn("state_5.sqlite:future_links.linked_thread", found)
