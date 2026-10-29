"""Linux adapter tests. Never execute Windows integration."""
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_session_manager.platforms import get_platform
from agent_session_manager.platforms import linux
from agent_session_manager.process_guard import running_codex_processes, require_codex_stopped
from agent_session_manager.gui import resolve_orphan_result_path


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux platform tests")
class LinuxPlatformTests(unittest.TestCase):
    def test_linux_selector_does_not_import_windows(self):
        self.assertEqual(get_platform().NAME, "linux")
        self.assertNotIn("agent_session_manager.platforms.windows", sys.modules)

    def test_directory_and_file_location_commands(self):
        self.assertEqual(linux.directory_command(Path("/home/test/data")), ["xdg-open", "/home/test/data"])
        self.assertEqual(linux.directory_command(Path("/home/test/file.txt"), reveal=True), ["xdg-open", "/home/test"])

    def test_config_uses_xdg_directory(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/home/test/config"}):
            self.assertEqual(linux.settings_path(), Path("/home/test/config/AgentSessionManager/settings.json"))

    def test_result_paths_cannot_escape_home(self):
        home = Path("/home/test/.codex")
        self.assertEqual(resolve_orphan_result_path(home, "attachments/example"), home / "attachments/example")
        for path in ("../project/secret.txt", "/home/test/project/secret.txt", "/tmp/outside.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                resolve_orphan_result_path(home, path)

    @patch("agent_session_manager.platforms.linux.subprocess.run")
    def test_process_failure_blocks_cleanup(self, run):
        run.return_value = Mock(returncode=1, stdout="")
        self.assertEqual(running_codex_processes(), ["process-check-unavailable"])
        with self.assertRaisesRegex(RuntimeError, "process-check-unavailable"):
            require_codex_stopped()

    @patch("agent_session_manager.platforms.linux.subprocess.run")
    def test_process_names_are_filtered(self, run):
        run.return_value = Mock(returncode=0, stdout="python\ncodex\nChatGPT\ncodex\n")
        self.assertEqual(running_codex_processes(), ["ChatGPT", "codex"])
        self.assertEqual(run.call_args.args[0], ["ps", "-A", "-o", "comm="])

    def test_symlink_and_unreadable_paths_are_protected(self):
        path = Mock()
        path.lstat.return_value = Mock(st_mode=stat.S_IFLNK)
        self.assertTrue(linux.is_reparse_point(path))
        path.lstat.return_value = Mock(st_mode=stat.S_IFREG)
        self.assertFalse(linux.is_reparse_point(path))
        path.lstat.side_effect = OSError("permission denied")
        self.assertTrue(linux.is_reparse_point(path))

    def test_project_font_overlay_restarts_same_python_without_installing(self):
        with patch.object(Path, "is_file", return_value=True), patch.dict(os.environ, {}, clear=True), patch.object(sys, "orig_argv", ["python", "-B", "run_gui.py"]), patch.object(linux.os, "execve") as execute:
            linux.prepare_gui_runtime()
            arguments = execute.call_args.args
            self.assertEqual(arguments[0], sys.executable)
            self.assertEqual(arguments[1], [sys.executable, "-B", "run_gui.py"])
            self.assertEqual(arguments[2]["ASM_XFT_ACTIVE"], "1")
            self.assertIn("linux/.runtime/tk-xft/lib/libtk8.6.so", arguments[2]["LD_PRELOAD"])
            self.assertTrue(arguments[2]["FONTCONFIG_FILE"].endswith("linux/fonts.conf"))
