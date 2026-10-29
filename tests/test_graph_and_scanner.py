from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_session_manager.dependency_graph import DependencyError
from agent_session_manager.models import SessionStatus
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner

from tests.helpers import (
    CHILD_ID,
    GRANDCHILD_ID,
    GUARDIAN_ID,
    ORPHAN_ID,
    UNKNOWN_ID,
    USER_ID,
    create_fixture,
    write_rollout,
)


class GraphAndScannerTests(unittest.TestCase):
    def test_classifies_user_attached_orphan_and_unlinked_internal(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[USER_ID], SessionStatus.USER_ROOT)
            self.assertEqual(scan.status_by_id[CHILD_ID], SessionStatus.ATTACHED)
            self.assertEqual(scan.status_by_id[ORPHAN_ID], SessionStatus.ORPHAN)
            self.assertEqual(scan.status_by_id[GUARDIAN_ID], SessionStatus.UNLINKED_INTERNAL)
            self.assertEqual(scan.sqlite_integrity, "ok")

    def test_parent_deletion_cascades_to_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            self.assertEqual(set(plan.deletion_ids), {USER_ID, CHILD_ID})

    def test_attached_child_cannot_be_deleted_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            with self.assertRaisesRegex(DependencyError, "cannot be deleted alone"):
                build_plan(SessionScanner(home).scan(), [CHILD_ID])

    def test_orphan_can_be_selected_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [ORPHAN_ID])
            self.assertEqual(plan.deletion_ids, [ORPHAN_ID])

    def test_orphan_selection_includes_deeper_descendants(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            write_rollout(
                home, GRANDCHILD_ID, thread_source="subagent", parent_id=ORPHAN_ID,
                title="grandchild",
            )
            plan = build_plan(SessionScanner(home).scan(), [ORPHAN_ID])
            self.assertEqual(set(plan.deletion_ids), {ORPHAN_ID, GRANDCHILD_ID})

    def test_unknown_record_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home, include_unknown=True)
            with self.assertRaisesRegex(DependencyError, "Unknown session type"):
                build_plan(SessionScanner(home).scan(), [UNKNOWN_ID])

    def test_corrupt_index_blocks_planning_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            (home / "session_index.jsonl").write_text("{broken\n", encoding="utf-8")
            scan = SessionScanner(home).scan()
            self.assertTrue(any(issue.code == "invalid_session_index" for issue in scan.issues))
            with self.assertRaisesRegex(ValueError, "blocking issues"):
                build_plan(scan, [ORPHAN_ID])

    def test_dependency_cycle_is_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_rollout(home, ORPHAN_ID, thread_source="subagent", parent_id=GRANDCHILD_ID)
            write_rollout(home, GRANDCHILD_ID, thread_source="subagent", parent_id=ORPHAN_ID)
            scan = SessionScanner(home).scan()
            self.assertTrue(any(issue.code == "dependency_cycle" for issue in scan.issues))
            self.assertEqual(scan.status_by_id[ORPHAN_ID], SessionStatus.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
