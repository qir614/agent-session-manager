import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_session_manager.artifact_store import attachment_references
from agent_session_manager.executor import CleanupExecutor
from agent_session_manager.impact_analyzer import analyze_deletion
from agent_session_manager.models import SessionStatus
from agent_session_manager.orphan_cleaner import scan_orphans
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner
from agent_session_manager.state_store import verify_no_references
from agent_session_manager.applications import get_application
from agent_session_manager import executor as executor_module
from tests.helpers import USER_ID, CHILD_ID, create_state_db, write_rollout
from tests.helpers import open_sqlite
from tests.test_artifact_cleanup import append_attachment
from tests import test_local_cleanup_contract
from tests import test_dsh_application


ATTACHMENT = "10000000-0000-4000-8000-000000000001"


class ReviewRegressionTests(unittest.TestCase):
    def test_empty_title_database_only_paginated_record_remains_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_state_db(home, [(USER_ID, "user")], [])
            with open_sqlite(home / "state_5.sqlite") as db:
                db.execute("ALTER TABLE threads ADD COLUMN history_mode TEXT")
                db.execute("UPDATE threads SET title='', name=NULL, history_mode='paginated'")
            scan = SessionScanner(home).scan()
            self.assertIn(USER_ID, scan.sessions)
            self.assertTrue(scan.sessions[USER_ID].title)
            self.assertFalse(scan.sessions[USER_ID].has_rollout)
            self.assertEqual(scan.status_by_id[USER_ID], SessionStatus.PAGINATED)
            with self.assertRaises(ValueError):
                build_plan(scan, [USER_ID])

    def attachment_fixture(self, home):
        test_local_cleanup_contract.PaginatedCleanupTests().fixture(home)
        attachment = home / "attachments" / ATTACHMENT
        attachment.mkdir(parents=True)
        (attachment / "pasted-text.txt").write_text("保留对话仍需使用", encoding="utf-8")
        os.utime(attachment / "pasted-text.txt", (1_700_000_000, 1_700_000_000))
        os.utime(attachment, (1_700_000_000, 1_700_000_000))
        with open_sqlite(home / "thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET item_json=? WHERE thread_id=?",
                       (json.dumps({"path": str(attachment / "pasted-text.txt")}), CHILD_ID))
        return attachment

    def test_historical_scan_protects_database_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            attachment = self.attachment_fixture(home)
            result = scan_orphans(home)
            self.assertNotIn(attachment.relative_to(home).as_posix(), [r.relative_path for r in result.candidates])

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_selected_and_retained_paginated_threads_keep_shared_attachment(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            attachment = self.attachment_fixture(home)
            scan = SessionScanner(home).scan()
            append_attachment(scan.sessions[USER_ID].path, home, ATTACHMENT)
            scan = SessionScanner(home).scan()
            plan = build_plan(scan, [USER_ID])
            self.assertIn(f"attachments/{ATTACHMENT}", analyze_deletion(scan, plan).shared_artifacts)
            report = CleanupExecutor().execute(plan)
            self.assertEqual(report.verification, {})
            self.assertTrue((attachment / "pasted-text.txt").is_file())
            with open_sqlite(home / "thread_history_1.sqlite") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM thread_items WHERE thread_id=?", (CHILD_ID,)).fetchone()[0], 1)

    def test_unindexed_database_history_also_protects_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            attachment = self.attachment_fixture(home)
            with open_sqlite(home / "thread_history_1.sqlite") as db:
                db.execute("UPDATE thread_items SET thread_id='not-in-catalog' WHERE thread_id=?", (CHILD_ID,))
            refs = attachment_references(SessionScanner(home).scan())
            self.assertIn(ATTACHMENT, refs["not-in-catalog"])
            self.assertNotIn(attachment.relative_to(home).as_posix(), [r.relative_path for r in scan_orphans(home).candidates])

    def test_attachment_metadata_without_rollout_reference_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_rollout(home, USER_ID, thread_source="user")
            create_state_db(home, [(USER_ID, "user")], [])
            with open_sqlite(home / "state_5.sqlite") as db:
                db.execute("CREATE TABLE thread_attachments(thread_id TEXT, attachment_id TEXT)")
                db.execute("INSERT INTO thread_attachments VALUES (?, ?)", (USER_ID, ATTACHMENT))
            refs = attachment_references(SessionScanner(home).scan())
            self.assertIn(ATTACHMENT, refs[USER_ID])

    def test_unreadable_history_attachment_json_stops_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.attachment_fixture(home)
            with open_sqlite(home / "thread_history_1.sqlite") as db:
                db.execute("UPDATE thread_items SET item_json='{invalid'")
            with self.assertRaisesRegex(ValueError, "attachment ownership"):
                scan_orphans(home)

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_queue_revision_and_current_primary_tables_are_deleted(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_rollout(home, USER_ID, thread_source="user")
            write_rollout(home, CHILD_ID, thread_source="user")
            create_state_db(home, [(USER_ID, "user"), (CHILD_ID, "user")], [])
            with open_sqlite(home / "state_5.sqlite") as db:
                db.executescript("CREATE TABLE guardian_review_feedback(id TEXT PRIMARY KEY, thread_id TEXT, record BLOB); CREATE TABLE thread_read_receipts(thread_id TEXT PRIMARY KEY, revision TEXT);")
                for sid in (USER_ID, CHILD_ID):
                    db.execute("INSERT INTO guardian_review_feedback VALUES (?, ?, ?)", (sid, sid, b"private"))
                    db.execute("INSERT INTO thread_read_receipts VALUES (?, 'revision')", (sid,))
            with open_sqlite(home / "queue_1.sqlite") as db:
                db.execute("CREATE TABLE queued_thread_revisions(revision INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL UNIQUE)")
                for sid in (USER_ID, CHILD_ID):
                    db.execute("INSERT INTO queued_thread_revisions(thread_id) VALUES (?)", (sid,))
            CleanupExecutor().execute(build_plan(SessionScanner(home).scan(), [USER_ID]))
            self.assertEqual(verify_no_references(home, {USER_ID}), {})
            for filename, table in (("queue_1.sqlite", "queued_thread_revisions"), ("state_5.sqlite", "guardian_review_feedback"), ("state_5.sqlite", "thread_read_receipts")):
                with open_sqlite(home / filename) as db:
                    self.assertEqual(db.execute(f'SELECT thread_id FROM "{table}"').fetchall(), [(CHILD_ID,)])

    def test_unknown_auxiliary_table_is_visible_and_blocks_before_snapshot(self):
        for filename in ("queue_1.sqlite", "new_store_2.sqlite"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                rollout = write_rollout(home, USER_ID, thread_source="user")
                with open_sqlite(home / filename) as db:
                    db.execute("CREATE TABLE future_rows(owner_thread_id TEXT, payload TEXT)")
                    db.execute("INSERT INTO future_rows VALUES (?, 'private')", (USER_ID,))
                self.assertTrue(verify_no_references(home, {USER_ID}))
                with patch("agent_session_manager.executor.create_rollback_snapshot") as snapshot:
                    with self.assertRaisesRegex(ValueError, "Unsupported SQLite"):
                        build_plan(SessionScanner(home).scan(), [USER_ID])
                    snapshot.assert_not_called()
                self.assertTrue(rollout.is_file())

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_current_global_state_layouts_clear_only_selected_local_thread(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_rollout(home, USER_ID, thread_source="user")
            write_rollout(home, CHILD_ID, thread_source="user")
            state = {
                "local-projects": {"project": {"name": "保留项目"}},
                "app-server-migrated-pinned-thread-ids-by-host": {"local:home": [USER_ID, CHILD_ID], "remote:host": [CHILD_ID]},
                "electron-persisted-atom-state": {
                    f"thread-tab-routes-v1:{USER_ID}": {"version": 1, "routes": [], "topology": {}},
                    f"thread-tab-routes-v1:{CHILD_ID}": {"version": 1, "routes": [], "topology": {}},
                },
            }
            for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
                (home / name).write_text(json.dumps(state), encoding="utf-8")
            CleanupExecutor().execute(build_plan(SessionScanner(home).scan(), [USER_ID]))
            for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
                after = json.loads((home / name).read_text(encoding="utf-8"))
                self.assertNotIn(f"thread-tab-routes-v1:{USER_ID}", after["electron-persisted-atom-state"])
                self.assertEqual(after["electron-persisted-atom-state"][f"thread-tab-routes-v1:{CHILD_ID}"], state["electron-persisted-atom-state"][f"thread-tab-routes-v1:{CHILD_ID}"])
                self.assertEqual(after["app-server-migrated-pinned-thread-ids-by-host"], {"local:home": [CHILD_ID], "remote:host": [CHILD_ID]})
                self.assertEqual(after["local-projects"], state["local-projects"])

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_same_size_rollout_or_state_change_rejects_stale_plan(self, _guard):
        for source in ("rollout", "json", "database"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                rollout = write_rollout(home, USER_ID, thread_source="user", title="hello")
                create_state_db(home, [(USER_ID, "user")], [])
                state = home / ".codex-global-state.json"
                state.write_text('{"theme":"dark"}', encoding="utf-8")
                plan = build_plan(SessionScanner(home).scan(), [USER_ID])
                if source == "rollout":
                    rollout.write_bytes(rollout.read_bytes().replace(b"hello", b"jello"))
                elif source == "json":
                    state.write_text('{"theme":"lite"}', encoding="utf-8")
                else:
                    with open_sqlite(home / "state_5.sqlite") as db:
                        db.execute("UPDATE threads SET title=replace(title,'title','other')")
                before = rollout.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    CleanupExecutor().execute(plan)
                self.assertEqual(rollout.read_bytes(), before)
                self.assertFalse(list(home.glob(".cleaner-transaction*")))

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_change_during_snapshot_is_preserved_without_rollback_overwrite(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            rollout = write_rollout(home, USER_ID, thread_source="user", title="hello")
            plan = build_plan(SessionScanner(home).scan(), [USER_ID])
            original = executor_module.create_rollback_snapshot
            def snapshot_then_edit(*args, **kwargs):
                result = original(*args, **kwargs)
                rollout.write_bytes(rollout.read_bytes().replace(b"hello", b"jello"))
                return result
            with patch.object(executor_module, "create_rollback_snapshot", side_effect=snapshot_then_edit):
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    CleanupExecutor().execute(plan)
            self.assertIn(b"jello", rollout.read_bytes())
            self.assertFalse(list(home.glob(".cleaner-transaction*")))

    def test_dsh_optional_delegation_depth_and_invalid_explicit_values(self):
        for depth in ("absent", -1, True, None, "0"):
            with self.subTest(depth=depth), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                path = test_dsh_application.session(home, "optional-depth")
                lines = path.read_text(encoding="utf-8").splitlines()
                header = json.loads(lines[0])
                if depth == "absent":
                    header.pop("delegationDepth")
                else:
                    header["delegationDepth"] = depth
                lines[0] = json.dumps(header)
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                app = get_application("dsh")
                scan = app.scan(home)
                if depth == "absent":
                    self.assertFalse(scan.issues)
                    self.assertEqual(app.plan(scan, ["optional-depth"]).deletion_ids, ["optional-depth"])
                else:
                    self.assertTrue(any(issue.blocking for issue in scan.issues))

    def test_missing_required_auxiliary_column_blocks_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_rollout(home, USER_ID, thread_source="user")
            with open_sqlite(home / "queue_1.sqlite") as db:
                db.execute("CREATE TABLE queued_items(session_id TEXT)")
                db.execute("INSERT INTO queued_items VALUES (?)", (USER_ID,))
            with self.assertRaisesRegex(ValueError, "Unsupported SQLite schema"):
                build_plan(SessionScanner(home).scan(), [USER_ID])

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_changed_attachment_content_rejects_preview(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            rollout = write_rollout(home, USER_ID, thread_source="user")
            directory = home / "attachments" / ATTACHMENT
            directory.mkdir(parents=True)
            content = directory / "pasted-text.txt"
            content.write_text("before", encoding="utf-8")
            append_attachment(rollout, home, ATTACHMENT)
            scan = SessionScanner(home).scan()
            plan = build_plan(scan, [USER_ID])
            analyze_deletion(scan, plan)
            content.write_text("edited", encoding="utf-8")
            for _attempt in range(2):
                with self.assertRaisesRegex(RuntimeError, "artifacts changed"):
                    CleanupExecutor().execute(plan)
            self.assertEqual(content.read_text(encoding="utf-8"), "edited")
            self.assertTrue(rollout.is_file())

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_catalog_additions_preserve_remote_and_other_local_rows(self, _guard):
        from tests.test_multistore import create_catalog
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_rollout(home, USER_ID, thread_source="user")
            create_catalog(home)
            with open_sqlite(home / "sqlite/codex-dev.db") as db:
                for table in ("local_thread_catalog_scan_entries", "live_visualization_suggestions"):
                    db.execute(f'CREATE TABLE "{table}"(host_id TEXT, thread_id TEXT, payload TEXT)')
                    db.executemany(f'INSERT INTO "{table}" VALUES (?, ?, ?)', [("local", USER_ID, "delete"), ("remote", USER_ID, "retain remote"), ("local", CHILD_ID, "retain local")])
            CleanupExecutor().execute(build_plan(SessionScanner(home).scan(), [USER_ID]))
            with open_sqlite(home / "sqlite/codex-dev.db") as db:
                for table in ("local_thread_catalog_scan_entries", "live_visualization_suggestions"):
                    self.assertEqual(db.execute(f'SELECT host_id,thread_id FROM "{table}" ORDER BY host_id').fetchall(), [("local", CHILD_ID), ("remote", USER_ID)])

    @patch("agent_session_manager.executor.require_codex_stopped")
    def test_archived_parent_includes_active_child_and_protects_artifacts(self, _guard):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = write_rollout(home, USER_ID, thread_source="user")
            write_rollout(home, CHILD_ID, thread_source="subagent", parent_id=USER_ID)
            archived = home / "archived_sessions" / path.name
            archived.parent.mkdir()
            path.rename(archived)
            artifact = home / "visualizations" / USER_ID
            artifact.mkdir(parents=True)
            (artifact / "index.html").write_text("保留归档内容", encoding="utf-8")
            os.utime(artifact / "index.html", (1_700_000_000, 1_700_000_000)); os.utime(artifact, (1_700_000_000, 1_700_000_000))
            scan = SessionScanner(home).scan()
            self.assertTrue(scan.sessions[USER_ID].archived)
            self.assertNotIn(artifact.relative_to(home).as_posix(), [r.relative_path for r in scan_orphans(home).candidates])
            report = CleanupExecutor().execute(build_plan(scan, [USER_ID]))
            self.assertEqual(set(report.deleted_ids), {USER_ID, CHILD_ID})
            self.assertFalse(artifact.exists())
