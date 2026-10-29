from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_session_manager.state_store import filter_global_state, filter_session_index
from agent_session_manager.process_guard import require_codex_stopped
from unittest.mock import patch


class StateStoreTests(unittest.TestCase):
    @patch("agent_session_manager.process_guard.running_codex_processes", return_value=["ChatGPT.exe"])
    def test_running_codex_blocks_writes(self, _processes):
        with self.assertRaisesRegex(RuntimeError, "completely closed"):
            require_codex_stopped()

    def test_invalid_index_refuses_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session_index.jsonl"
            path.write_text("{broken\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid session_index"):
                filter_session_index(path, {"id"})
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken\n")

    def test_global_state_only_removes_known_structural_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".codex-global-state.json"
            target = "00000000-0000-4000-8000-000000000001"
            data = {
                "projectless-thread-ids": [target],
                "unrelated-note": f"text mentions {target}",
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            removed = filter_global_state(path, {target})
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(removed, 1)
            self.assertEqual(result["projectless-thread-ids"], [])
            self.assertIn(target, result["unrelated-note"])


if __name__ == "__main__":
    unittest.main()
