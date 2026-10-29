"""Real local file transactions and standard compressed fixtures; never launch DSH."""
import contextlib
import ctypes as C
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_session_manager.applications import get_application
from agent_session_manager.applications.dsh import DshApplication
from agent_session_manager.applications.dsh_format import rows, zstd_available, zstd_candidates, zstd_library
from agent_session_manager.backup import verify_backup
from agent_session_manager.cli import main
from agent_session_manager.executor import CleanupExecutor
from agent_session_manager.planner import build_plan

ROOT = "session-main"
CHILD = "session-child"
KEEP = "session-keep"
HAS_ZSTD = zstd_available()


def compress(data):
    lib = zstd_library()
    lib.ZSTD_compressBound.argtypes = [C.c_size_t]
    lib.ZSTD_compressBound.restype = C.c_size_t
    lib.ZSTD_compress.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p, C.c_size_t, C.c_int]
    lib.ZSTD_compress.restype = C.c_size_t
    size = lib.ZSTD_compressBound(len(data))
    out = C.create_string_buffer(size)
    incoming = C.create_string_buffer(data)
    result = lib.ZSTD_compress(out, size, incoming, len(data), 3)
    if lib.ZSTD_isError(result):
        raise ValueError("Fixture compression failed")
    return out.raw[:result]


def session(home, sid, parent=None, seeded=False, version=4, compressed=False):
    directory = home / "sessions/project" / sid
    directory.mkdir(parents=True)
    header = {"type": "session", "version": version, "id": sid, "createdAt": 1000,
              "cwd": "/projects/thesis", "isSeeded": seeded, "delegationDepth": 1 if parent else 0}
    if parent:
        header.update(parentSession=parent, origin="subagent")
    events = [header,
              {"type": "user/message", "seq": 0, "time": 1001, "data": {"role": "user", "content": [{"type": "text", "text": "hello 中文"}]}},
              {"type": "assistant/message", "seq": 1, "time": 1002, "data": {"message": {"role": "assistant", "content": [{"type": "text", "text": "answer 中文"}]}}}]
    parts = [(json.dumps(e, ensure_ascii=False) + "\n").encode() for e in events]
    path = directory / (f"session.v{version}.jsonl" + (".zstd" if compressed else ""))
    path.write_bytes(b"".join(compress(p) for p in parts) if compressed else b"".join(parts))
    (directory / "session.lock").touch()
    cache = home / "storages/session_projcache/sessions" / (sid + ".json")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"version": 1, "record": {"identity": {"formatVersion": version, "createdAt": 1000, "cwd": "/projects/thesis", "isSeeded": seeded}, "rows": {"title": {"ver": 1, "seq": 1, "val": "title-" + sid}}}}), encoding="utf-8")
    return path


def fixture(home, compressed=False):
    session(home, ROOT, compressed=compressed)
    session(home, CHILD, ROOT, compressed=compressed)
    session(home, KEEP, compressed=compressed)
    workspace = {"unit": {"name": "workspace", "version": 1},
                 "global": {"workspaceIds": ["thesis"], "archivedSessionIds": [ROOT, KEEP], "pinnedSessionIds": [CHILD]},
                 "tables": {"workspaces": {"thesis": {"path": "/projects/thesis", "title": "thesis", "sessionIds": [ROOT, CHILD, KEEP]}}}}
    (home / "storages/workspace.json").write_text(json.dumps(workspace), encoding="utf-8")
    (home / ".credentials.yaml").write_text("must remain untouched", encoding="utf-8")
    attachments = home / "attachments/v1/objects/shared"
    attachments.mkdir(parents=True)
    (attachments / "shared").write_text("shared bytes", encoding="utf-8")


class DshApplicationTests(unittest.TestCase):
    def test_raw_and_concatenated_compressed_frames_match(self):
        for compressed in ([False, True] if HAS_ZSTD else [False]):
            with self.subTest(compressed=compressed), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp); fixture(home, compressed)
                app = get_application("dsh"); scan = app.scan(home)
                self.assertFalse(scan.issues)
                self.assertEqual(scan.sessions[ROOT].project_name, "thesis")
                self.assertEqual(scan.parent_by_child[CHILD], ROOT)
                self.assertEqual(scan.sessions[ROOT].title, "title-" + ROOT)
                details = app.read(scan.sessions[ROOT])
                self.assertEqual([(m.role, m.text) for m in details.messages], [("user", "hello 中文"), ("assistant", "answer 中文")])
                self.assertEqual(set(app.plan(scan, [ROOT]).deletion_ids), {ROOT, CHILD})
                with self.assertRaisesRegex(ValueError, "parent"):
                    app.plan(scan, [CHILD])

    @patch.object(DshApplication, "running", return_value=[])
    @unittest.skipUnless(os.name == "posix", "DSH local deletion currently supports Linux")
    def test_local_cleanup_preserves_other_sessions_and_shared_data(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home, compressed=HAS_ZSTD)
            app = get_application("dsh"); scan = app.scan(home)
            # Retired generations in the selected owned directory must also disappear.
            (scan.sessions[ROOT].path.parent / "session.v3.jsonl.zstd").write_bytes(compress(b'{}\n'))
            plan = app.plan(app.scan(home), [ROOT])
            with patch("subprocess.run", side_effect=AssertionError("No CLI proxy")), patch("subprocess.Popen", side_effect=AssertionError("No service proxy")):
                report = app.execute(plan)
            self.assertEqual(set(report.deleted_ids), {ROOT, CHILD})
            self.assertEqual(set(app.scan(home).sessions), {KEEP})
            self.assertFalse((home / "storages/session_projcache/sessions" / (ROOT + ".json")).exists())
            value = json.loads((home / "storages/workspace.json").read_text(encoding="utf-8"))
            self.assertEqual(value["tables"]["workspaces"]["thesis"]["sessionIds"], [KEEP])
            self.assertEqual(value["global"]["archivedSessionIds"], [KEEP])
            self.assertEqual(value["global"]["pinnedSessionIds"], [])
            self.assertEqual((home / ".credentials.yaml").read_text(encoding="utf-8"), "must remain untouched")
            self.assertTrue((home / "attachments/v1/objects/shared/shared").exists())
            self.assertFalse(list(home.glob(".cleaner-dsh-transaction-*")))
            self.assertTrue(report.warnings)

    @patch.object(DshApplication, "running", return_value=[])
    @unittest.skipUnless(os.name == "posix", "DSH local deletion currently supports Linux")
    def test_late_failure_rolls_back_files_caches_and_workspace(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            app = get_application("dsh"); plan = app.plan(app.scan(home), [ROOT])
            before = {p.relative_to(home):p.read_bytes() for p in home.rglob("*") if p.is_file()}
            original = app.scan
            count = 0
            def fail_late(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 3:
                    raise RuntimeError("injected post-delete failure")
                return original(*args, **kwargs)
            with patch.object(app, "scan", side_effect=fail_late):
                with self.assertRaisesRegex(RuntimeError, "post-delete"):
                    app.execute(plan)
            self.assertEqual(before, {p.relative_to(home):p.read_bytes() for p in home.rglob("*") if p.is_file()})

    @patch.object(DshApplication, "running", return_value=[])
    @unittest.skipUnless(os.name == "posix", "DSH local deletion currently supports Linux")
    def test_keyboard_interrupt_rolls_back(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            app = get_application("dsh"); plan = app.plan(app.scan(home), [ROOT])
            from agent_session_manager.applications import dsh_transaction
            original = dsh_transaction._move
            count = 0
            def interrupt(source, target):
                nonlocal count
                count += 1
                if count == 2:
                    raise KeyboardInterrupt()
                return original(source, target)
            with patch.object(dsh_transaction, "_move", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    app.execute(plan)
            self.assertEqual(set(app.scan(home).sessions), {ROOT, CHILD, KEEP})
            self.assertFalse(list(home.glob(".cleaner-dsh-transaction-*")))

    @patch.object(DshApplication, "running", return_value=[])
    @unittest.skipUnless(os.name == "posix", "DSH local deletion currently supports Linux")
    def test_changed_same_size_data_blocks_execution(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            app = get_application("dsh"); scan = app.scan(home); plan = app.plan(scan, [ROOT])
            path = scan.sessions[ROOT].path
            path.write_bytes(path.read_bytes().replace(b'hello', b'jello'))
            with self.assertRaisesRegex(RuntimeError, "changed"):
                app.execute(plan)
            self.assertEqual(len(app.scan(home).sessions), 3)

    @patch.object(DshApplication, "running", return_value=["active DSH"])
    @unittest.skipUnless(os.name == "posix", "DSH local deletion currently supports Linux")
    def test_running_application_blocks_before_mutation(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            app = get_application("dsh"); plan = app.plan(app.scan(home), [ROOT])
            with self.assertRaisesRegex(RuntimeError, "Close DSH"):
                app.execute(plan)
            self.assertFalse(list(home.glob(".cleaner-dsh-transaction-*")))

    @unittest.skipUnless(os.name == "posix", "Linux flock contract")
    @patch.object(DshApplication, "running", return_value=[])
    def test_kernel_write_lock_blocks_cleanup(self, _running):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            app = get_application("dsh"); scan = app.scan(home); plan = app.plan(scan, [ROOT])
            with (scan.sessions[ROOT].path.parent / "session.lock").open("rb") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, "active writer"):
                    app.execute(plan)
            self.assertEqual(len(app.scan(home).sessions), 3)

    def test_unknown_highest_generation_is_not_silently_downgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            path = home / "sessions/project" / ROOT / "session.v5.jsonl"
            value = {"type": "session", "version": 5, "id": ROOT, "createdAt": 1000}
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            app = get_application("dsh"); scan = app.scan(home)
            self.assertEqual(scan.sessions[ROOT].path, path)
            with self.assertRaisesRegex(ValueError, "format"):
                app.plan(scan, [ROOT])

    @unittest.skipUnless(HAS_ZSTD, "libzstd is an optional runtime")
    def test_corrupt_or_truncated_compressed_data_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.v4.jsonl.zstd"
            path.write_bytes(compress(b'{"type":"session"}\n')[:-3])
            with self.assertRaisesRegex(ValueError, "Truncated|Corrupt"):
                list(rows(path))

    def test_unknown_workspace_references_block_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            path = home / "storages/workspace.json"
            value = json.loads(path.read_text(encoding="utf-8"));value["global"]["futureSelection"] = ROOT
            path.write_text(json.dumps(value), encoding="utf-8")
            app = get_application("dsh")
            with self.assertRaisesRegex(ValueError, "Unknown DSH workspace"):
                app.plan(app.scan(home), [ROOT])

    def test_custom_persistence_root_and_home_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            (home / "sessions").rename(home / "custom")
            app = get_application("dsh")
            with patch.dict(os.environ, {"DSH_HOME": str(home)}):
                self.assertEqual(app.default_home(), home)
            scan = app.scan(home, home / "custom")
            self.assertEqual(len(scan.sessions), 3)
            self.assertEqual(set(app.plan(scan, [ROOT]).deletion_ids), {ROOT, CHILD})

    def test_export_hashes_and_application_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            app = get_application("dsh")
            path, manifest = app.export(app.scan(home), [ROOT], home / "exports")
            verified = verify_backup(path)
            self.assertEqual(verified["application"], "dsh")
            self.assertEqual(set(verified["session_ids"]), {ROOT, CHILD})
            self.assertFalse(verified["restorable"])

    def test_codex_entry_points_refuse_dsh_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            app = get_application("dsh"); scan = app.scan(home)
            with self.assertRaisesRegex(ValueError, "adapter"):
                build_plan(scan, [ROOT])
            with self.assertRaisesRegex(ValueError, "another application"):
                CleanupExecutor().execute(app.plan(scan, [ROOT]))

    def test_cli_scan_and_delete_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); fixture(home)
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                self.assertEqual(main(["--app", "dsh", "--home", str(home), "scan", "--json"]), 0)
            self.assertEqual(json.loads(capture.getvalue())["application"], "dsh")
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                self.assertEqual(main(["--app", "dsh", "--home", str(home), "delete", ROOT]), 0)
            self.assertIn("DELETE-2", capture.getvalue())
            self.assertTrue((home / "sessions/project" / ROOT).exists())


class DshAdditionalContracts(unittest.TestCase):
    def test_pid_and_port_file_identifies_active_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".harness.pid").write_text("12345\n3080\n", encoding="utf-8")
            app = get_application("dsh")
            with patch("agent_session_manager.applications.dsh.get_platform") as platform, patch("os.kill") as kill:
                platform.return_value.process_names.return_value = []
                self.assertEqual(app.running(home), ["DSH runtime (PID file)"])
                kill.assert_called_once_with(12345, 0)

    def test_seeded_session_is_not_automatically_attached(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);fixture(home)
            path = home / "sessions/project" / KEEP / "session.v4.jsonl"
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            events[0].update(isSeeded=True, parentSession=ROOT)
            path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
            app = get_application("dsh");scan = app.scan(home)
            self.assertNotIn(KEEP, scan.parent_by_child)
            self.assertEqual(set(app.plan(scan, [ROOT]).deletion_ids), {ROOT, CHILD})

    def test_builtin_replacements_and_internal_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); path = session(home, ROOT)
            extra = [{"type": "assistant/message", "seq": 2, "time": 1003,
                      "surfaceOp": {"op": "replace", "startSeq": 1, "endSeq": 1},
                      "data": {"message": {"content": [{"type": "text", "text": "new answer"}]}}},
                     {"type": "user/message", "seq": 3, "time": 1004,
                      "data": {"source": {"kind": "runtime"}, "content": [{"type": "text", "text": "internal context"}]}}]
            with path.open("a") as stream:
                stream.write("".join(json.dumps(event) + "\n" for event in extra))
            app = get_application("dsh");record = app.scan(home).sessions[ROOT]
            self.assertEqual([message.text for message in app.read(record).messages], ["hello 中文", "new answer"])
            self.assertEqual(app.read(record, include_internal=True).messages[-1].text, "internal context")

    @unittest.skipUnless(os.name == "posix", "Linux cleanup contract")
    @patch.object(DshApplication, "running", return_value=[])
    def test_unfinished_transaction_blocks_next_cleanup(self, _running):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);fixture(home)
            app = get_application("dsh");plan = app.plan(app.scan(home), [ROOT])
            (home / ".cleaner-dsh-transaction-pending").mkdir()
            with self.assertRaisesRegex(RuntimeError, "Unfinished DSH transaction"):
                app.execute(plan)
            self.assertEqual(len(app.scan(home).sessions), 3)

    @unittest.skipUnless(os.name == "posix", "Linux rollback contract")
    @patch.object(DshApplication, "running", return_value=[])
    def test_failed_rollback_retains_journal_and_originals(self, _running):
        from agent_session_manager.applications import dsh_transaction
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);fixture(home)
            app = get_application("dsh");plan = app.plan(app.scan(home), [ROOT])
            original = dsh_transaction._move
            count = 0
            def fail_moves(source, target):
                nonlocal count
                count += 1
                if count >= 2:
                    raise OSError("injected move failure")
                return original(source, target)
            with patch.object(dsh_transaction, "_move", side_effect=fail_moves):
                with self.assertRaisesRegex(RuntimeError, "rollback failed"):
                    app.execute(plan)
            pending = list(home.glob(".cleaner-dsh-transaction-*"))
            self.assertEqual(len(pending), 1)
            self.assertTrue((pending[0] / "recovery.json").is_file())
            self.assertTrue(any((pending[0] / "data").rglob("session.v4.jsonl")))


class DshZstdDiscovery(unittest.TestCase):
    def test_explicit_library_path_is_tried_first(self):
        with patch.dict(os.environ, {"AGENT_SESSION_MANAGER_ZSTD": "/nonexistent/libzstd-custom.so"}, clear=False):
            first = next(iter(zstd_candidates()))
        self.assertEqual(first, ("AGENT_SESSION_MANAGER_ZSTD", "/nonexistent/libzstd-custom.so"))

    def test_availability_matches_loadability(self):
        try:
            zstd_library()
        except (OSError, ValueError):
            self.assertFalse(zstd_available())
        else:
            self.assertTrue(zstd_available())


class DshReaderBoundaries(unittest.TestCase):
    def test_reasoning_is_hidden_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp);path = session(home, ROOT)
            with path.open("a") as stream:
                stream.write(json.dumps({"type": "assistant/message", "seq": 2, "time": 1004,
                    "data": {"message": {"content": [{"type": "reasoning", "text": "internal analysis"},
                    {"type": "text", "text": "visible answer"}]}}}) + "\n")
            app = get_application("dsh");record = app.scan(home).sessions[ROOT]
            with path.open("a") as stream:
                stream.write(json.dumps({"type": "assistant/message", "seq": 3, "time": 1005,
                    "data": {"message": {"content": [{"type": "reasoning", "text": "hidden one"},
                    {"type": "reasoning", "text": "hidden two"}]}}}) + "\n")
            self.assertEqual(len(app.read(record).messages), 3)
            self.assertEqual(app.read(record).messages[-1].text, "visible answer")
            self.assertIn("internal analysis", app.read(record, include_internal=True).messages[-2].text)

    @unittest.skipUnless(HAS_ZSTD, "libzstd optional runtime")
    def test_mixed_encoding_across_sessions_blocks_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            session(home, ROOT, compressed=False)
            session(home, KEEP, compressed=True)
            app = get_application("dsh");scan = app.scan(home)
            self.assertTrue(any(issue.code == "dsh_mixed_encoding" for issue in scan.issues))
            with self.assertRaisesRegex(ValueError, "Mixed raw/compressed"):
                app.plan(scan, [ROOT])

    def test_external_persistence_root_does_not_produce_empty_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp);home = base / "home";other = base / "other"
            home.mkdir();other.mkdir();session(other, ROOT)
            app = get_application("dsh");scan = app.scan(home, other / "sessions")
            self.assertIn(ROOT, scan.sessions)
            with self.assertRaisesRegex(ValueError, "External DSH"):
                app.export(scan, [ROOT], home / "exports")
            self.assertFalse((home / "exports").exists())
