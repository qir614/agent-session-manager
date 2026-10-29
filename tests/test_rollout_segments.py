from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_session_manager.conversation_reader import read_conversation
from agent_session_manager.planner import build_plan
from agent_session_manager.scanner import SessionScanner

from tests.helpers import USER_ID, create_fixture


def continuation(home: Path, cwd: str = "C:/demo") -> Path:
    path = home / "sessions" / "2026" / "01" / "02" / f"rollout-2026-01-02T00-00-00-{USER_ID}_run.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {"timestamp": "2026-01-02T00:00:00Z", "type": "session_meta", "payload": {
            "id": USER_ID, "timestamp": "2026-01-02T00:00:00Z", "cwd": cwd,
            "source": "vscode", "thread_source": "user",
        }},
        {"timestamp": "2026-01-02T00:00:01Z", "type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "continued question"}],
        }},
    ]
    path.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
    return path


class RolloutSegmentTests(unittest.TestCase):
    def test_compatible_same_id_files_are_one_session_with_two_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            continuation(home)
            scan = SessionScanner(home).scan()
            record = scan.sessions[USER_ID]
            self.assertEqual(record.segment_count, 2)
            self.assertEqual(record.size, sum(path.stat().st_size for path in record.paths))
            self.assertFalse(any(issue.code == "duplicate_id" for issue in scan.issues))
            self.assertFalse(any(issue.blocking for issue in scan.issues))
            details = read_conversation(record.paths)
            self.assertEqual([item.text for item in details.messages], ["parent", "continued question"])
            plan = build_plan(scan, [USER_ID])
            self.assertEqual(sum(item.id == USER_ID for item in plan.items), 2)

    def test_conflicting_same_id_segment_is_precisely_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_fixture(home)
            continuation(home, cwd="D:/different-project")
            scan = SessionScanner(home).scan()
            issue = next(item for item in scan.issues if item.code == "segment_metadata_conflict")
            self.assertTrue(issue.blocking)
            self.assertIn("cwd", issue.message)
            self.assertIn(" | ", issue.path)


if __name__ == "__main__":
    unittest.main()
