from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_session_cleaner.impact_analyzer import analyze_deletion
from codex_session_cleaner.planner import build_plan
from codex_session_cleaner.scanner import SessionScanner

from tests.helpers import CHILD_ID, USER_ID
from tests.test_multistore import create_multistore_fixture


class ImpactAnalyzerTests(unittest.TestCase):
    def test_reports_parent_child_files_logs_and_snapshot_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_multistore_fixture(home)
            scan = SessionScanner(home).scan()
            plan = build_plan(scan, [USER_ID])
            impact = analyze_deletion(scan, plan)
            self.assertEqual(set(plan.deletion_ids), {USER_ID, CHILD_ID})
            self.assertEqual(impact.user_count, 1)
            self.assertEqual(impact.subagent_count, 1)
            self.assertEqual(impact.rollout_count, 2)
            self.assertEqual(impact.log_reference_total, 2)
            self.assertGreaterEqual(impact.sqlite_reference_total, impact.log_reference_total)
            self.assertGreater(impact.transaction_snapshot_bytes, impact.rollout_bytes)


if __name__ == "__main__":
    unittest.main()
