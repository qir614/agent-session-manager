from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_session_cleaner.executor as executor_module
from codex_session_cleaner.executor import CleanupExecutor
from codex_session_cleaner.planner import build_plan
from codex_session_cleaner.scanner import SessionScanner

from tests.helpers import CHILD_ID, USER_ID
from tests.test_multistore import create_multistore_fixture


class FailureRollbackTests(unittest.TestCase):
    @patch("codex_session_cleaner.executor.require_codex_stopped")
    def test_failure_after_log_deletion_restores_logs_and_rollouts(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_multistore_fixture(home)
            scan = SessionScanner(home).scan()
            parent_path = scan.sessions[USER_ID].path
            child_path = scan.sessions[CHILD_ID].path
            original_verify = executor_module.verify_no_references

            def fail_final_verification(codex_home, deletion_ids, *, include_logs=True):
                if include_logs:
                    raise RuntimeError("injected failure after log deletion")
                return original_verify(codex_home, deletion_ids, include_logs=False)

            with patch("codex_session_cleaner.executor.verify_no_references",
                       side_effect=fail_final_verification):
                with self.assertRaisesRegex(RuntimeError, "injected failure"):
                    CleanupExecutor().execute(build_plan(scan, [USER_ID]))

            self.assertTrue(parent_path.is_file())
            self.assertTrue(child_path.is_file())
            logs = sqlite3.connect(home / "logs_2.sqlite")
            try:
                ids = {row[0] for row in logs.execute("SELECT thread_id FROM logs")}
            finally:
                logs.close()
            self.assertEqual(ids, {USER_ID, CHILD_ID})
            self.assertFalse(any(home.glob(".cleaner-transaction_*")))


if __name__ == "__main__":
    unittest.main()
