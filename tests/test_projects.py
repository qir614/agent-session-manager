from __future__ import annotations

import unittest
from pathlib import Path

from codex_session_cleaner.projects import (
    TEMPORARY_PROJECT_KEY,
    is_temporary_workspace,
    normalize_workspace,
    project_info,
)


class ProjectTests(unittest.TestCase):
    def test_normalizes_windows_paths_case_insensitively(self):
        self.assertEqual(normalize_workspace(r"E:\Projects\Demo\\"), "e:/projects/demo")

    def test_uses_last_directory_as_project_name(self):
        info = project_info(r"E:\projects\harmony-privacy\VIGIL-main", Path(r"C:\Users\qir"))
        self.assertEqual(info.name, "VIGIL-main")
        self.assertFalse(info.temporary)

    def test_recognizes_codex_managed_workspace(self):
        home = Path(r"C:\Users\qir")
        cwd = r"C:\Users\qir\Documents\Codex\2026-08-20\generated"
        self.assertTrue(is_temporary_workspace(cwd, home))
        self.assertEqual(project_info(cwd, home).key, TEMPORARY_PROJECT_KEY)

    def test_empty_workspace_is_temporary(self):
        self.assertTrue(project_info("").temporary)


if __name__ == "__main__":
    unittest.main()
