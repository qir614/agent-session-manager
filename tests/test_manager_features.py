from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from codex_session_cleaner.backup import create_manual_backup, verify_backup
from codex_session_cleaner.codex_controller import CodexController
from codex_session_cleaner.conversation_reader import read_conversation
from codex_session_cleaner.scanner import SessionScanner
from codex_session_cleaner.settings import AppSettings, load_settings, save_settings

from tests.helpers import CHILD_ID, USER_ID, create_fixture


class ManagerFeatureTests(unittest.TestCase):
    def test_settings_default_delete_backup_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = load_settings(path)
            self.assertFalse(settings.backup_before_delete)
            settings.backup_before_delete = True
            save_settings(settings, path)
            self.assertTrue(load_settings(path).backup_before_delete)

    def test_conversation_reader_hides_injected_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            events = [
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>secret</environment_context>"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "visible"}]}},
            ]
            path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
            self.assertEqual([item.text for item in read_conversation(path).messages], ["visible"])
            self.assertEqual(len(read_conversation(path, include_internal=True).messages), 2)

    def test_manual_archive_selected_parent_includes_child_without_state(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as output:
            home = Path(tmp)
            create_fixture(home)
            scan = SessionScanner(home).scan()
            backup_dir, manifest = create_manual_backup(scan, [USER_ID], Path(output))
            self.assertEqual(set(manifest["session_ids"]), {USER_ID, CHILD_ID})
            verified = verify_backup(backup_dir)
            self.assertEqual(verified["status"], "archive_complete")
            self.assertEqual(verified["backup_type"], "session_archive")
            self.assertFalse(verified["restorable"])
            self.assertFalse((backup_dir / "state_5.sqlite").exists())

    def test_controller_archive_uses_codex_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
            controller = CodexController(Path(tmp), command="codex", runner=runner)
            controller.command = "codex"
            controller.set_archived(USER_ID, True)
            self.assertEqual(runner.call_args.args[0], ["codex", "archive", USER_ID])


if __name__ == "__main__":
    unittest.main()
