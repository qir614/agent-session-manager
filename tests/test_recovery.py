from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_session_manager.executor as executor
from agent_session_manager.backup import create_backup, create_rollback_snapshot, verify_backup
from agent_session_manager.executor import CleanupExecutor, recover_transaction, restore_backup
from agent_session_manager.planner import build_plan
from agent_session_manager.recovery import checkpoint, pending_transactions
from agent_session_manager.scanner import SessionScanner

from tests.helpers import USER_ID, create_fixture


@patch("agent_session_manager.executor.require_codex_stopped")
class RecoveryTests(unittest.TestCase):
    def test_abrupt_process_exit_keeps_durable_snapshot_and_can_be_recovered(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            script = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
from agent_session_manager.executor import CleanupExecutor
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner
from tests.helpers import USER_ID
home = Path(sys.argv[1])
plan = build_plan(SessionScanner(home).scan(), [USER_ID])
with patch("agent_session_manager.executor.require_codex_stopped"), patch(
    "agent_session_manager.executor.filter_global_state", side_effect=lambda *args: os._exit(23)
):
    CleanupExecutor().execute(plan)
'''
            project = Path(__file__).resolve().parents[1]
            result = subprocess.run(
                [sys.executable, "-B", "-c", script, str(home)],
                cwd=project,
                env=dict(os.environ, PYTHONPATH=str(project / "src")),
                timeout=60,
            )
            self.assertEqual(result.returncode, 23)
            pending = pending_transactions(home)
            self.assertEqual(len(pending), 1)
            verify_backup(pending[0])
            self.assertTrue(any(
                issue.code == "interrupted_operation" for issue in SessionScanner(home).scan().issues
            ))
            with self.assertRaisesRegex(ValueError, "interrupted_operation"):
                build_plan(SessionScanner(home).scan(), [USER_ID])
            recover_transaction(pending[0], home, force=True, overwrite_conflicts=True)
            self.assertIn(USER_ID, SessionScanner(home).scan().sessions)
            self.assertEqual(pending_transactions(home), [])

    def test_pending_transaction_blocks_planning_until_recovered(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            snapshot, manifest = create_rollback_snapshot(plan)
            checkpoint(snapshot, home, manifest, "deleting", dirty=True)
            scan = SessionScanner(home).scan()
            self.assertTrue(any(issue.blocking for issue in scan.issues))
            with self.assertRaisesRegex(ValueError, "interrupted_operation"):
                build_plan(scan, [USER_ID])
            with self.assertRaisesRegex(RuntimeError, "Interrupted operation"):
                CleanupExecutor().execute(plan)
            with self.assertRaisesRegex(RuntimeError, "--force"):
                recover_transaction(pending_transactions(home)[0], home)
            with self.assertRaisesRegex(RuntimeError, "conflicts"):
                recover_transaction(pending_transactions(home)[0], home, force=True)
            recover_transaction(
                pending_transactions(home)[0], home, force=True, overwrite_conflicts=True
            )
            self.assertIn(USER_ID, SessionScanner(home).scan().sessions)
            self.assertEqual(pending_transactions(home), [])

    def test_clean_checkpoint_can_be_recovered_without_overriding(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            snapshot, manifest = create_rollback_snapshot(plan)
            checkpoint(snapshot, home, manifest, "verified")
            recover_transaction(snapshot, home, force=True)
            self.assertIn(USER_ID, SessionScanner(home).scan().sessions)
            self.assertEqual(pending_transactions(home), [])

    def test_restore_refuses_changed_current_state_without_separate_override(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            report = CleanupExecutor().execute(
                build_plan(SessionScanner(home).scan(), [USER_ID]), persistent_backup=True
            )
            index = home / "session_index.jsonl"
            index.write_text(
                index.read_text(encoding="utf-8")
                + json.dumps({"id": "00000000-0000-4000-8000-000000000099", "thread_name": "new"})
                + "\n",
                encoding="utf-8",
            )
            changed = index.read_text(encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Restore conflicts"):
                restore_backup(report.backup_dir, home, force=True)
            self.assertEqual(index.read_text(encoding="utf-8"), changed)
            restore_backup(report.backup_dir, home, force=True, overwrite_conflicts=True)
            self.assertIn(USER_ID, SessionScanner(home).scan().sessions)

    def test_interrupted_restore_has_recoverable_guard_including_missing_targets(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            report = CleanupExecutor().execute(
                build_plan(SessionScanner(home).scan(), [USER_ID]), persistent_backup=True
            )
            original = executor.restore_snapshot

            def interrupt(directory, target):
                count = original(directory, target)
                if directory == report.backup_dir:
                    raise KeyboardInterrupt()
                return count

            with patch.object(executor, "restore_snapshot", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    restore_backup(report.backup_dir, home, force=True)
            pending = pending_transactions(home)
            self.assertEqual(len(pending), 1)
            recover_transaction(pending[0], home, force=True, overwrite_conflicts=True)
            self.assertNotIn(USER_ID, SessionScanner(home).scan().sessions)
            self.assertEqual(pending_transactions(home), [])

    def test_restore_failure_rolls_back_and_discards_guard(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            report = CleanupExecutor().execute(
                build_plan(SessionScanner(home).scan(), [USER_ID]), persistent_backup=True
            )
            original = executor.restore_snapshot

            def fail(directory, target):
                count = original(directory, target)
                if directory == report.backup_dir:
                    raise RuntimeError("injected restore failure")
                return count

            with patch.object(executor, "restore_snapshot", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    restore_backup(report.backup_dir, home, force=True)
            self.assertNotIn(USER_ID, SessionScanner(home).scan().sessions)
            self.assertEqual(pending_transactions(home), [])

    def test_old_backup_without_baseline_refuses_different_existing_files(self, _guard):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output:
            home = Path(tmp)
            create_fixture(home)
            backup, _ = create_backup(
                build_plan(SessionScanner(home).scan(), [USER_ID]), Path(output)
            )
            (home / "session_index.jsonl").write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Restore conflicts"):
                restore_backup(backup, home, force=True)

    def test_manifest_cannot_restore_unrelated_files_inside_home(self, _guard):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output:
            home = Path(tmp)
            create_fixture(home)
            backup, manifest = create_backup(
                build_plan(SessionScanner(home).scan(), [USER_ID]), Path(output)
            )
            entry = manifest["files"][0]
            entry["relative_path"] = "auth.json"
            entry["category"] = "state"
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "restore scope"):
                verify_backup(backup)

    def test_recovery_rejects_path_outside_selected_home(self, _guard):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            with self.assertRaisesRegex(ValueError, "direct transaction"):
                recover_transaction(Path(outside), Path(tmp), force=True)

class RecoveryCliTests(unittest.TestCase):
    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_recover_lists_then_recovers_a_pending_transaction(self, _guard):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from agent_session_manager import cli

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            snapshot, manifest = create_rollback_snapshot(plan)
            checkpoint(snapshot, home, manifest, "deleting", dirty=True)

            listing = StringIO()
            with redirect_stdout(listing):
                code = cli.main(["--home", str(home), "recover"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(listing.getvalue()), [str(snapshot)])

            without_force = StringIO()
            with redirect_stderr(without_force):
                code = cli.main(["--home", str(home), "recover", str(snapshot)])
            self.assertEqual(code, 2)
            self.assertIn("--force", without_force.getvalue())
            self.assertTrue(snapshot.exists())

            recovered = StringIO()
            with redirect_stdout(recovered), patch("agent_session_manager.executor.require_codex_stopped"):
                code = cli.main([
                    "--home", str(home), "recover", str(snapshot), "--force", "--overwrite-conflicts",
                ])
            self.assertEqual(code, 0)
            self.assertIn("Recovered", recovered.getvalue())
            self.assertEqual(pending_transactions(home), [])
            self.assertIn(USER_ID, SessionScanner(home).scan().sessions)

    def test_recover_is_not_offered_for_dsh(self):
        from contextlib import redirect_stderr
        from io import StringIO

        from agent_session_manager import cli

        with tempfile.TemporaryDirectory() as tmp:
            error = StringIO()
            with redirect_stderr(error):
                code = cli.main(["--app", "dsh", "--home", tmp, "recover"])
        self.assertEqual(code, 2)
        self.assertIn("DSH does not support recover", error.getvalue())


class RecoveryCliGuardTests(unittest.TestCase):
    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_restore_reports_value_error_without_traceback(self, _guard):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from agent_session_manager import cli

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            error = StringIO()
            output = StringIO()
            with redirect_stderr(error), redirect_stdout(output):
                code = cli.main(["--home", str(home), "restore", str(home / "missing"), "--force"])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(error.getvalue().startswith("Error: "))
