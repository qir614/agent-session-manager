from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_session_manager.backup_manager import delete_backup, list_backups, retention_candidates
from agent_session_manager.models import BackupRecord


class BackupManagerTests(unittest.TestCase):
    def test_lists_and_deletes_only_managed_incomplete_direct_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "backups"
            root.mkdir()
            target = root / "backup_codex_sessions_20260820_120000"
            target.mkdir()
            (target / "BACKUP_INCOMPLETE.txt").write_text("incomplete", encoding="utf-8")
            (target / "partial.bin").write_bytes(b"x" * 16)
            records = list_backups(root)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "incomplete")
            with patch("agent_session_manager.backup_manager.require_codex_stopped"):
                released = delete_backup(root, target, Path(tmp))
            self.assertGreater(released, 0)
            self.assertFalse(target.exists())

    def test_refuses_root_nested_and_unmanaged_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "backups"
            root.mkdir()
            unmanaged = root / "photos"
            unmanaged.mkdir()
            nested = root / "backup_codex_sessions_20260820_120000" / "nested"
            nested.mkdir(parents=True)
            with patch("agent_session_manager.backup_manager.require_codex_stopped"):
                for candidate in (root, unmanaged, nested):
                    with self.subTest(candidate=candidate):
                        with self.assertRaises(ValueError):
                            delete_backup(root, candidate, Path(tmp))

    def test_retention_selects_oldest_without_touching_invalid_entries(self):
        records = [
            BackupRecord(Path(f"b{index}"), f"b{index}", "session_archive",
                         f"2026-08-0{index}T00:00:00+00:00", "archive_complete", "0.6.0",
                         1, 1, 100, True, False)
            for index in range(1, 4)
        ]
        records.append(BackupRecord(Path("invalid"), "invalid", "invalid", "", "invalid",
                                    "", 0, 0, 1000, False, False, "bad"))
        selected = retention_candidates(records, keep_count=2)
        self.assertEqual([item.name for item in selected], ["b1"])


if __name__ == "__main__":
    unittest.main()
