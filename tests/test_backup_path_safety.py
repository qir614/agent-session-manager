from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_session_cleaner.backup import contained_path, create_manual_backup, verify_backup
from codex_session_cleaner.executor import restore_backup
from codex_session_cleaner.scanner import SessionScanner

from tests.helpers import USER_ID, create_fixture


class BackupPathSafetyTests(unittest.TestCase):
    def test_rejects_path_traversal_absolute_drive_and_unc_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in ("../outside", "/absolute", "C:\\outside", "\\\\server\\share\\file"):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        contained_path(root, value)

    def test_tampered_manifest_cannot_escape_backup_root(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output:
            home = Path(tmp)
            create_fixture(home)
            archive, _manifest = create_manual_backup(
                SessionScanner(home).scan(), [USER_ID], Path(output)
            )
            manifest_path = archive / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][0]["relative_path"] = "../../outside.jsonl"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_backup(archive)

    @patch("codex_session_cleaner.executor.require_codex_stopped")
    def test_session_archive_is_never_restored_as_state_snapshot(self, _guard):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output:
            home = Path(tmp)
            create_fixture(home)
            archive, _manifest = create_manual_backup(
                SessionScanner(home).scan(), [USER_ID], Path(output)
            )
            with self.assertRaisesRegex(RuntimeError, "export-only"):
                restore_backup(archive, home, force=True)


if __name__ == "__main__":
    unittest.main()
