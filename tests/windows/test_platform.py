"""Native Windows integration tests; skipped on Linux."""
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_session_manager.platforms import get_platform


@unittest.skipUnless(os.name == "nt", "Windows platform tests")
class WindowsPlatformTests(unittest.TestCase):
    def test_selector_and_explorer_commands(self):
        platform = get_platform()
        self.assertEqual(platform.NAME, "windows")
        home = Path("C:/Users/test/.codex")
        target = home / "attachments/example.txt"
        self.assertEqual(platform.directory_command(target, reveal=True), ["explorer.exe", f"/select,{target}"])
        self.assertEqual(platform.directory_command(home), ["explorer.exe", str(home)])

    def test_config_uses_local_app_data(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/test/AppData/Local"}):
            self.assertEqual(get_platform().settings_path(), Path("C:/Users/test/AppData/Local/AgentSessionManager/settings.json"))

    def test_native_enumeration_falls_back_to_tasklist(self):
        platform = get_platform()
        with patch.object(platform, "_toolhelp_process_names", side_effect=OSError), patch.object(platform.subprocess, "run", return_value=Mock(returncode=0, stdout='"codex.exe","123"\n')) as run:
            self.assertEqual(platform.process_names(), ["codex.exe"])
            self.assertEqual(run.call_args.args[0], ["tasklist", "/FO", "CSV", "/NH"])
