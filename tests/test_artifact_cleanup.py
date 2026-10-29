from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_session_manager.executor as executor_module
from agent_session_manager.audit import AUDIT_FILE_NAME
from agent_session_manager.artifact_store import delete_artifacts
from agent_session_manager.executor import CleanupExecutor
from agent_session_manager.impact_analyzer import analyze_deletion
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner

from tests.helpers import CHILD_ID, ORPHAN_ID, USER_ID, create_fixture


PRIVATE_ATTACHMENT = "10000000-0000-4000-8000-000000000001"
SHARED_ATTACHMENT = "10000000-0000-4000-8000-000000000002"


def append_attachment(path: Path, home: Path, attachment_id: str) -> None:
    attachment = home / "attachments" / attachment_id / "pasted-text.txt"
    event = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": f"file: {attachment}"}],
        },
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\n")


class ArtifactCleanupTests(unittest.TestCase):
    def _fixture(self, root: Path):
        home = root / ".codex"
        home.mkdir()
        create_fixture(home)
        scan = SessionScanner(home).scan()
        append_attachment(scan.sessions[USER_ID].path, home, PRIVATE_ATTACHMENT)
        append_attachment(scan.sessions[USER_ID].path, home, SHARED_ATTACHMENT)
        append_attachment(scan.sessions[ORPHAN_ID].path, home, SHARED_ATTACHMENT)
        for attachment_id in (PRIVATE_ATTACHMENT, SHARED_ATTACHMENT):
            directory = home / "attachments" / attachment_id
            directory.mkdir(parents=True)
            (directory / "pasted-text.txt").write_text(attachment_id, encoding="utf-8")
        visualization = home / "visualizations" / "2026" / "01" / "01" / USER_ID
        visualization.mkdir(parents=True)
        (visualization / "index.html").write_text("private visualization", encoding="utf-8")
        generated = home / "generated_images" / CHILD_ID
        generated.mkdir(parents=True)
        (generated / "image.png").write_bytes(b"private image")
        private_temp = home / ".tmp" / USER_ID
        private_temp.mkdir(parents=True)
        (private_temp / "tool-output.txt").write_text("thread-private", encoding="utf-8")
        cache = home / "cache"
        cache.mkdir()
        (cache / "shared-index.json").write_text(
            json.dumps({"thread": USER_ID, "shared": True}), encoding="utf-8"
        )
        project = root / "generated-project"
        project.mkdir()
        (project / "keep.txt").write_text("must survive", encoding="utf-8")
        (home / AUDIT_FILE_NAME).write_text(
            json.dumps({"event": "old", "deletion_ids": [USER_ID]}) + "\n",
            encoding="utf-8",
        )
        return home, project

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_deletes_unique_private_artifacts_but_preserves_shared_and_project(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home, project = self._fixture(Path(tmp))
            scan = SessionScanner(home).scan()
            plan = build_plan(scan, [USER_ID])
            impact = analyze_deletion(scan, plan)
            self.assertEqual(impact.attachment_directories, 1)
            self.assertEqual(len(impact.shared_artifacts), 1)
            self.assertEqual(impact.visualization_directories, 1)
            self.assertEqual(impact.generated_image_directories, 1)
            self.assertEqual(len(impact.unresolved_artifacts), 1)
            report = CleanupExecutor().execute(plan)
            self.assertFalse((home / "attachments" / PRIVATE_ATTACHMENT).exists())
            self.assertTrue((home / "attachments" / SHARED_ATTACHMENT).is_dir())
            self.assertFalse((home / "visualizations" / "2026" / "01" / "01" / USER_ID).exists())
            self.assertFalse((home / "generated_images" / CHILD_ID).exists())
            self.assertFalse((home / ".tmp" / USER_ID).exists())
            self.assertTrue((home / "cache" / "shared-index.json").is_file())
            self.assertEqual((project / "keep.txt").read_text(encoding="utf-8"), "must survive")
            self.assertEqual(report.artifact_files_deleted, 4)
            self.assertEqual(len(report.unresolved_artifacts), 1)
            audit = (home / AUDIT_FILE_NAME).read_text(
                encoding="utf-8"
            )
            self.assertNotIn(USER_ID, audit)
            self.assertIn("thread#", audit)
            connection = sqlite3.connect(home / "state_5.sqlite")
            try:
                self.assertEqual(connection.execute("PRAGMA freelist_count").fetchone()[0], 0)
            finally:
                connection.close()

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_artifacts_are_restored_when_later_verification_fails(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home, _project = self._fixture(Path(tmp))
            scan = SessionScanner(home).scan()
            plan = build_plan(scan, [USER_ID])
            analyze_deletion(scan, plan)
            original = executor_module.verify_no_references

            def fail_after_artifacts(codex_home, deletion_ids, *, include_logs=True):
                if include_logs:
                    raise RuntimeError("injected post-artifact failure")
                return original(codex_home, deletion_ids, include_logs=False)

            with patch("agent_session_manager.executor.verify_no_references", side_effect=fail_after_artifacts):
                with self.assertRaisesRegex(RuntimeError, "injected post-artifact failure"):
                    CleanupExecutor().execute(plan)
            self.assertTrue((home / "attachments" / PRIVATE_ATTACHMENT / "pasted-text.txt").is_file())
            self.assertTrue((home / "visualizations" / "2026" / "01" / "01" / USER_ID / "index.html").is_file())
            self.assertTrue((home / "generated_images" / CHILD_ID / "image.png").is_file())

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_changed_artifact_blocks_execution_after_preview(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home, _project = self._fixture(Path(tmp))
            scan = SessionScanner(home).scan()
            plan = build_plan(scan, [USER_ID])
            analyze_deletion(scan, plan)
            target = home / "attachments" / PRIVATE_ATTACHMENT / "pasted-text.txt"
            target.write_text("changed after preview", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "artifacts changed after preview"):
                CleanupExecutor().execute(plan)
            self.assertTrue(target.is_file())

    def test_refuses_artifact_path_outside_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / ".codex"
            home.mkdir()
            project = root / "project"
            project.mkdir()
            with self.assertRaisesRegex(ValueError, "escapes Codex home"):
                delete_artifacts(home, ["../project"])
            self.assertTrue(project.is_dir())


if __name__ == "__main__":
    unittest.main()
