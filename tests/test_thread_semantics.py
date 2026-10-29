"""Regression coverage for analytics labels versus actual spawned ancestry."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_session_manager.dependency_graph import DependencyError
from agent_session_manager.models import SessionKind, SessionStatus
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner
from tests.helpers import USER_ID, CHILD_ID, UNKNOWN_ID, create_state_db, write_rollout
from tests.helpers import open_sqlite


class ThreadSemanticsTests(unittest.TestCase):
    def test_user_label_does_not_override_rollout_spawn_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="user")
            write_rollout(home, CHILD_ID, thread_source="user", parent_id=USER_ID)
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.sessions[CHILD_ID].kind, SessionKind.SUBAGENT)
            self.assertEqual(scan.status_by_id[CHILD_ID], SessionStatus.ATTACHED)
            with self.assertRaises(DependencyError):
                build_plan(scan, [CHILD_ID])

    def test_database_spawn_edge_wins_over_main_task_labels(self):
        for label in ("user", "agent_created_thread", "agent_forked_thread"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                write_rollout(home, USER_ID, thread_source="user")
                write_rollout(home, CHILD_ID, thread_source=label)
                create_state_db(home, [(USER_ID, "user"), (CHILD_ID, label)], [(USER_ID, CHILD_ID)])
                scan = SessionScanner(home).scan()
                self.assertEqual(scan.status_by_id[CHILD_ID], SessionStatus.ATTACHED)
                with self.assertRaises(DependencyError):
                    build_plan(scan, [CHILD_ID])
                self.assertEqual(set(build_plan(scan, [USER_ID]).deletion_ids), {USER_ID, CHILD_ID})
                # Planning must also consult ancestry independently of presentation status.
                scan.status_by_id[CHILD_ID] = SessionStatus.USER_ROOT
                with self.assertRaises(DependencyError):
                    build_plan(scan, [CHILD_ID])

    def test_database_analytics_cannot_promote_internal_source_to_user(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, CHILD_ID, thread_source="subagent", guardian=True)
            create_state_db(home, [(CHILD_ID, "user")], [])
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[CHILD_ID], SessionStatus.UNLINKED_INTERNAL)

    def test_state_source_recovers_spawn_relation_missing_from_rollout(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="user")
            write_rollout(home, CHILD_ID, thread_source="agent_created_thread")
            create_state_db(home, [(CHILD_ID, "agent_created_thread")], [])
            with open_sqlite(home / "state_5.sqlite") as connection:
                connection.execute("ALTER TABLE threads ADD COLUMN source TEXT")
                connection.execute("UPDATE threads SET source=?", (json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": USER_ID, "depth": 1}}}),))
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.parent_by_child[CHILD_ID], USER_ID)
            self.assertEqual(scan.status_by_id[CHILD_ID], SessionStatus.ATTACHED)
            with self.assertRaises(DependencyError):
                build_plan(scan, [CHILD_ID])

    def test_fork_is_independent_and_not_a_deletion_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="user")
            path = write_rollout(home, CHILD_ID, thread_source="agent_forked_thread")
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            events[0]["payload"]["forked_from_id"] = USER_ID
            path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[CHILD_ID], SessionStatus.USER_ROOT)
            self.assertNotIn(CHILD_ID, scan.parent_by_child)
            self.assertEqual(build_plan(scan, [USER_ID]).deletion_ids, [USER_ID])
            self.assertEqual(build_plan(scan, [CHILD_ID]).deletion_ids, [CHILD_ID])

    def test_unknown_analytics_keeps_project_but_does_not_authorize_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, UNKNOWN_ID, thread_source="future_task_source")
            create_state_db(home, [(UNKNOWN_ID, "future_task_source")], [])
            with open_sqlite(home / "state_5.sqlite") as connection:
                connection.execute("ALTER TABLE threads ADD COLUMN project_id TEXT")
                connection.execute("UPDATE threads SET project_id='project-a'")
            (home / ".codex-global-state.json").write_text(json.dumps({
                "local-projects": {"project-a": {"name": "thesis", "rootPaths": ["/projects/thesis"]}},
            }), encoding="utf-8")
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.sessions[UNKNOWN_ID].project_name, "thesis")
            self.assertEqual(scan.sessions[UNKNOWN_ID].thread_source, "future_task_source")
            self.assertEqual(scan.status_by_id[UNKNOWN_ID], SessionStatus.UNKNOWN)
            with self.assertRaises(DependencyError):
                build_plan(scan, [UNKNOWN_ID])

    def test_ghost_with_explicit_parent_cannot_be_deleted_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="user")
            create_state_db(home, [(CHILD_ID, "user")], [(USER_ID, CHILD_ID)])
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[CHILD_ID], SessionStatus.ATTACHED)
            with self.assertRaises(DependencyError):
                build_plan(scan, [CHILD_ID])

    def test_internal_review_source_wins_over_user_analytics(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = write_rollout(home, USER_ID, thread_source="user")
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            events[0]["payload"]["source"] = {"subagent": "review"}
            path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            scan = SessionScanner(home).scan()
            self.assertEqual(scan.status_by_id[USER_ID], SessionStatus.UNLINKED_INTERNAL)

    def test_invalid_database_source_blocks_planning(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            write_rollout(home, USER_ID, thread_source="user")
            create_state_db(home, [(USER_ID, "user")], [])
            with open_sqlite(home / "state_5.sqlite") as connection:
                connection.execute("ALTER TABLE threads ADD COLUMN source TEXT")
                connection.execute("UPDATE threads SET source='{invalid'")
            scan = SessionScanner(home).scan()
            with self.assertRaisesRegex(ValueError, "invalid_session_source"):
                build_plan(scan, [USER_ID])
