import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_session_manager.scanner import SessionScanner
from agent_session_manager.models import SessionStatus
from tests.helpers import USER_ID, CHILD_ID, write_rollout, create_state_db


class AgentCreatedProjectTests(unittest.TestCase):
    def test_independent_agent_created_conversation_keeps_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="agent_created_thread", title="old manager")
            create_state_db(home, [(USER_ID, "agent_created_thread")], [])
            (home / ".codex-global-state.json").write_text(json.dumps({
                "local-projects": {"project-a": {"name": "thesis", "rootPaths": ["/projects/thesis"]}},
                "thread-project-assignments": {USER_ID: {"projectKind": "local", "projectId": "project-a"}},
            }), encoding="utf-8")
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[USER_ID], SessionStatus.USER_ROOT)
            self.assertEqual(scan.sessions[USER_ID].project_name, "thesis")

    def test_agent_created_thread_with_parent_remains_attached(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="user")
            write_rollout(home, CHILD_ID, thread_source="agent_created_thread", parent_id=USER_ID)
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[CHILD_ID], SessionStatus.ATTACHED)

    def test_existing_rollout_uses_catalog_project_without_sidebar_assignment(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="user")
            (home / ".codex-global-state.json").write_text(json.dumps({
                "local-projects": {"project-a": {"name": "thesis", "rootPaths": ["/projects/thesis"]}},
            }), encoding="utf-8")
            (home / "sqlite").mkdir()
            connection = sqlite3.connect(home / "sqlite/codex-dev.db")
            connection.execute("CREATE TABLE local_thread_catalog (host_id, thread_id, display_title, cwd, source_kind, project_id, missing_candidate)")
            connection.execute("INSERT INTO local_thread_catalog VALUES (?, ?, ?, ?, ?, ?, ?)", ("local", USER_ID, "review architecture", "/projects/thesis", "vscode", "project-a", 0))
            connection.commit()
            connection.close()
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.sessions[USER_ID].project_id, "project-a")
            self.assertEqual(scan.sessions[USER_ID].project_name, "thesis")
