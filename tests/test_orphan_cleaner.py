from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_session_manager.audit import AUDIT_FILE_NAME
from agent_session_manager.orphan_cleaner import execute_orphan_cleanup, scan_orphans

from tests.helpers import USER_ID, create_fixture


ORPHAN_ATTACHMENT = "20000000-0000-4000-8000-000000000001"
STALE_THREAD = "30000000-0000-4000-8000-000000000001"


def make_old(path: Path) -> None:
    old = time.time() - 10 * 86400
    for item in sorted(path.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        os.utime(item, (old, old))
    os.utime(path, (old, old))


class OrphanCleanerTests(unittest.TestCase):
    def fixture(self, home: Path) -> None:
        create_fixture(home)
        attachment = home / "attachments" / ORPHAN_ATTACHMENT
        attachment.mkdir(parents=True)
        (attachment / "pasted-text.txt").write_text("orphan", encoding="utf-8")
        make_old(attachment)
        stale = home / "visualizations" / "2025" / "01" / "01" / STALE_THREAD
        stale.mkdir(parents=True)
        (stale / "index.html").write_text("stale", encoding="utf-8")
        make_old(stale)
        active = home / "generated_images" / USER_ID
        active.mkdir(parents=True)
        (active / "keep.png").write_bytes(b"keep")
        make_old(active)
        (home / AUDIT_FILE_NAME).write_text(
            json.dumps({"deletion_ids": [STALE_THREAD]}) + "\n", encoding="utf-8"
        )

    def test_scan_is_conservative_and_keeps_active_thread_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            result = scan_orphans(home)
            paths = {item.relative_path for item in result.candidates}
            self.assertIn(f"attachments/{ORPHAN_ATTACHMENT}", paths)
            self.assertIn(f"visualizations/2025/01/01/{STALE_THREAD}", paths)
            self.assertNotIn(f"generated_images/{USER_ID}", paths)
            self.assertIn(STALE_THREAD, result.operation_log_ids)

    def test_reparse_tree_is_reported_but_never_auto_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            unsafe = home / "visualizations" / "2025" / "01" / "01" / "40000000-0000-4000-8000-000000000001"
            unsafe.mkdir(parents=True)
            link = unsafe / "link"
            try:
                link.symlink_to(home / "generated_images", target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is not permitted on this filesystem")
            make_old(unsafe)
            result = scan_orphans(home)
            relative = unsafe.relative_to(home).as_posix()
            self.assertNotIn(relative, {item.relative_path for item in result.candidates})
            self.assertIn(relative, {item.relative_path for item in result.review_only})

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_cleanup_deletes_only_previewed_orphans_and_redacts_log(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            result = scan_orphans(home)
            report = execute_orphan_cleanup(result)
            self.assertFalse((home / "attachments" / ORPHAN_ATTACHMENT).exists())
            self.assertFalse((home / "visualizations" / "2025" / "01" / "01" / STALE_THREAD).exists())
            self.assertTrue((home / "generated_images" / USER_ID / "keep.png").is_file())
            self.assertGreater(report.operation_ids_redacted, 0)
            self.assertNotIn(STALE_THREAD, (home / AUDIT_FILE_NAME).read_text(encoding="utf-8"))

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_cleanup_failure_restores_deleted_orphans(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            result = scan_orphans(home)
            with patch(
                "agent_session_manager.orphan_cleaner._redact_operation_ids",
                side_effect=RuntimeError("injected orphan failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected orphan failure"):
                    execute_orphan_cleanup(result)
            self.assertTrue((home / "attachments" / ORPHAN_ATTACHMENT / "pasted-text.txt").is_file())
            self.assertTrue((home / "visualizations" / "2025" / "01" / "01" / STALE_THREAD / "index.html").is_file())

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_snapshot_preparation_failure_never_removes_originals(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            plan = scan_orphans(home)
            with patch("agent_session_manager.orphan_cleaner.shutil.copy2", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    execute_orphan_cleanup(plan)
            self.assertEqual((home / "attachments" / ORPHAN_ATTACHMENT / "pasted-text.txt").read_text(encoding="utf-8"), "orphan")
            self.assertFalse(list(home.glob(".cleaner-orphan-transaction-*")))

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_interrupt_restores_staged_orphans_and_log(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            plan = scan_orphans(home)
            log = home / AUDIT_FILE_NAME
            before = log.read_bytes()
            with patch("agent_session_manager.orphan_cleaner._redact_operation_ids", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    execute_orphan_cleanup(plan)
            self.assertTrue((home / "attachments" / ORPHAN_ATTACHMENT / "pasted-text.txt").is_file())
            self.assertEqual(log.read_bytes(), before)
            self.assertFalse(list(home.glob(".cleaner-orphan-transaction-*")))

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_cross_device_failure_restores_already_staged_paths(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            plan = scan_orphans(home)
            rename = Path.rename
            calls = 0
            def fail_second(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("cross-device link")
                return rename(source, target)
            with patch.object(Path, "rename", fail_second):
                with self.assertRaisesRegex(OSError, "cross-device"):
                    execute_orphan_cleanup(plan)
            self.assertTrue(all((home / item.relative_path).exists() for item in plan.candidates))

    def test_recent_child_modification_prevents_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            os.utime(home / "attachments" / ORPHAN_ATTACHMENT / "pasted-text.txt", None)
            self.assertNotIn(f"attachments/{ORPHAN_ATTACHMENT}", {item.relative_path for item in scan_orphans(home).candidates})

    def test_custom_home_attachment_reference_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            scan = scan_orphans(home)
            from agent_session_manager.scanner import SessionScanner
            rollout = SessionScanner(home).scan().sessions[USER_ID].path
            with rollout.open("a") as stream:
                stream.write(json.dumps({"type": "response_item", "payload": {"path": str(home / "attachments" / ORPHAN_ATTACHMENT / "pasted-text.txt")}}) + "\n")
            self.assertNotIn(f"attachments/{ORPHAN_ATTACHMENT}", {item.relative_path for item in scan_orphans(home).candidates})

    @patch("agent_session_manager.orphan_cleaner.require_codex_stopped")
    def test_failed_rollback_preserves_recovery_data_and_blocks_next_scan(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.fixture(home)
            plan = scan_orphans(home)
            rename = Path.rename
            def fail_restore(source, target):
                if ".cleaner-orphan-transaction-" in str(source):
                    raise OSError("restore denied")
                return rename(source, target)
            with patch.object(Path, "rename", fail_restore), patch("agent_session_manager.orphan_cleaner._redact_operation_ids", side_effect=KeyboardInterrupt):
                with self.assertRaisesRegex(RuntimeError, "recovery data retained"):
                    execute_orphan_cleanup(plan)
            staging = next(home.glob(".cleaner-orphan-transaction-*"))
            self.assertTrue((staging / "recovery.json").is_file())
            self.assertTrue(list((staging / "data").rglob("pasted-text.txt")))
            with self.assertRaisesRegex(RuntimeError, "Unfinished orphan cleanup"):
                scan_orphans(home)


if __name__ == "__main__":
    unittest.main()
