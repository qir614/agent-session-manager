import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_session_manager.audit import write_operation
from agent_session_manager.cleanup_dialogs import CleanupConfirmation
from agent_session_manager.executor import CleanupExecutor
from agent_session_manager.orphan_cleaner import scan_orphans, execute_orphan_cleanup
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner
from agent_session_manager.state_store import filter_global_state, global_reference_ids, verify_no_references
from tests.helpers import USER_ID, CHILD_ID, write_rollout, create_state_db
from tests.helpers import open_sqlite


def state(sid=USER_ID):
    return {
        "local-projects": {"project-a": {"name": "thesis", "rootPaths": ["/projects/thesis"]}},
        "electron-persisted-atom-state": {"thread-descriptions-v1": {sid: "private description", CHILD_ID: "keep"}},
        "thread-project-membership-host-ids": {sid: "local"},
        "thread-workspace-root-hints": {sid: "/projects/thesis"},
        "electron-remote-hosted-pip-task-visibility-state": {sid: "visible"},
        "app-server-projects-migration-by-host": {"local:/demo": {"pendingThreadAssignmentIds": [sid]}, "remote:demo": {"pendingThreadAssignmentIds": [CHILD_ID]}},
        "electron-thread-read-state-v1": {"version": 1, "unreadByIdentity": {"account": {"local:home": [sid], "durable:remote": [CHILD_ID]}}},
    }


class CleanupFeedbackTests(unittest.TestCase):
    def test_confirmation_rejects_typo_without_accepting_or_dismissing(self):
        dialog = object.__new__(CleanupConfirmation)
        dialog.phrase = "DELETE"
        dialog.entry = Mock();dialog.entry.get.return_value = "delete"
        dialog.error = Mock()
        self.assertFalse(dialog.validate())
        self.assertIn("尚未执行", dialog.error.set.call_args.args[0])
        dialog.entry.selection_range.assert_called_once_with(0, "end")
        dialog.entry.get.return_value = "DELETE"
        self.assertTrue(dialog.validate())
        dialog.apply();self.assertTrue(dialog.result)

    def test_known_state_cleanup_preserves_projects_and_remote_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);path = home / ".codex-global-state.json"
            value = state();projects = value["local-projects"]
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertGreater(len(global_reference_ids(value)), 0)
            self.assertTrue(verify_no_references(home, {USER_ID}))
            self.assertEqual(filter_global_state(path, {USER_ID}), 6)
            after = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn(USER_ID, global_reference_ids(after))
            self.assertEqual(after["local-projects"], projects)
            self.assertEqual(after["electron-thread-read-state-v1"]["unreadByIdentity"]["account"]["durable:remote"], [CHILD_ID])
            self.assertEqual(verify_no_references(home, {USER_ID}), {})

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_unknown_global_reference_forces_rollback(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);rollout = write_rollout(home, USER_ID, thread_source="user")
            create_state_db(home, [(USER_ID, "user")], [])
            path = home / ".codex-global-state.json"
            path.write_text(json.dumps({"future-unknown-state": {USER_ID: "retain"}}), encoding="utf-8")
            original = path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "rollback|rolled back|cleanup|Cleanup"):
                CleanupExecutor().execute(build_plan(SessionScanner(home).scan(), [USER_ID]))
            self.assertTrue(rollout.is_file());self.assertEqual(path.read_bytes(), original)

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_sidebar_only_cleanup_keeps_project_and_other_conversation(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);retained = write_rollout(home, CHILD_ID, thread_source="user")
            create_state_db(home, [(CHILD_ID, "user")], [])
            path = home / ".codex-global-state.json"
            value = state();value["thread-project-assignments"] = {USER_ID: {"projectKind": "local", "projectId": "project-a"}}
            path.write_text(json.dumps(value), encoding="utf-8");scan = SessionScanner(home).scan()
            plan = build_plan(scan, [USER_ID]);self.assertEqual(plan.file_count, 0)
            report = CleanupExecutor().execute(plan)
            self.assertEqual(report.deleted_ids, [USER_ID]);self.assertTrue(retained.exists())
            self.assertNotIn(USER_ID, SessionScanner(home).scan().sessions)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["local-projects"], value["local-projects"])

    def test_sidebar_candidate_with_hidden_history_is_rejected(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);value = state()
            value["thread-project-assignments"] = {USER_ID: {"projectKind": "local", "projectId": "project-a"}}
            (home / ".codex-global-state.json").write_text(json.dumps(value), encoding="utf-8")
            with open_sqlite(home / "thread_history_1.sqlite") as c:
                c.execute("CREATE TABLE thread_items(thread_id TEXT)")
                c.execute("INSERT INTO thread_items VALUES (?)", (USER_ID,))
            with self.assertRaises(ValueError):build_plan(SessionScanner(home).scan(), [USER_ID])

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_deleted_state_repair_requires_successful_cleanup_attestation(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);path = home / ".codex-global-state.json"
            path.write_text(json.dumps(state()), encoding="utf-8")
            self.assertEqual(scan_orphans(home).state_reference_ids, [])
            write_operation(home, "cleanup_failed_rolled_back", deletion_ids=[USER_ID])
            self.assertEqual(scan_orphans(home).state_reference_ids, [])
            write_operation(home, "cleanup_complete", deletion_ids=[USER_ID])
            plan = scan_orphans(home);self.assertEqual(plan.state_reference_ids, [USER_ID])
            report = execute_orphan_cleanup(plan)
            self.assertEqual(report.state_references_removed, 6)
            self.assertEqual(verify_no_references(home, {USER_ID}), {})
            self.assertIn(CHILD_ID, global_reference_ids(json.loads(path.read_text(encoding="utf-8"))))
            self.assertFalse(list(home.glob(".cleaner-orphan-transaction-*")))

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_reference_repair_rolls_back_global_state_on_late_failure(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);path = home / ".codex-global-state.json"
            path.write_text(json.dumps(state()), encoding="utf-8");original = path.read_bytes()
            write_operation(home, "cleanup_complete", deletion_ids=[USER_ID])
            plan = scan_orphans(home)
            with patch("agent_session_manager.orphan_cleaner._redact_operation_ids", side_effect=OSError("injected late failure")):
                with self.assertRaises(OSError):execute_orphan_cleanup(plan)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(home.glob(".cleaner-orphan-transaction-*")))

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_reference_repair_refuses_changed_preview(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);path = home / ".codex-global-state.json"
            path.write_text(json.dumps(state()), encoding="utf-8");write_operation(home, "cleanup_complete", deletion_ids=[USER_ID])
            plan = scan_orphans(home);data = json.loads(path.read_text(encoding="utf-8"));data["new-user-setting"] = True;path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed"):execute_orphan_cleanup(plan)
            self.assertIn(USER_ID, global_reference_ids(json.loads(path.read_text(encoding="utf-8"))))
