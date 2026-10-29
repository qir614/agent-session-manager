from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from agent_session_manager.gui import (
    orphan_category_label,
    orphan_decision_label,
    orphan_id_type_label,
    orphan_ownership_label,
    orphan_reason_label,
    resolve_orphan_result_path,
)


class OrphanResultsUiTests(unittest.TestCase):
    def test_candidate_categories_are_localized(self):
        self.assertEqual(orphan_category_label("attachment"), "孤立附件")
        self.assertEqual(orphan_category_label("visualizations"), "可视化产物")
        self.assertEqual(orphan_category_label("generated_images"), "生成图片")
        self.assertEqual(orphan_category_label("browser"), "浏览器会话")

    def test_internal_reasons_are_not_shown_to_users(self):
        reasons = (
            "no surviving rollout references this attachment ID",
            "UUID is not present in any surviving local session or catalog record",
            "manual review required; unsafe or unreadable tree: junction",
        )
        for reason in reasons:
            label = orphan_reason_label(reason)
            self.assertNotIn("surviving", label)
            self.assertNotIn("manual review", label)
            self.assertTrue(any("\u4e00" <= character <= "\u9fff" for character in label))

    def test_attachment_and_thread_ids_have_distinct_explanations(self):
        self.assertEqual(orphan_id_type_label("attachment"), "附件 ID")
        self.assertEqual(orphan_id_type_label("visualizations"), "任务 ID")
        self.assertIn("现存 rollout", orphan_ownership_label("attachment"))
        self.assertIn("状态数据库", orphan_ownership_label("visualizations"))

    def test_review_decision_explains_protection_deadline_or_junction(self):
        modified = datetime.now(timezone.utc) - timedelta(days=2)
        recent = SimpleNamespace(modified_at=modified.isoformat(), reason="UUID is not present")
        decision = orphan_decision_label(recent, False, 7)
        self.assertIn("暂不清理", decision)
        self.assertIn("保护至", decision)
        unsafe = SimpleNamespace(
            modified_at=modified.isoformat(),
            reason="manual review required; unsafe or unreadable tree: junction",
        )
        self.assertIn("目录联接", orphan_decision_label(unsafe, False, 7))
        self.assertIn("可能越界", orphan_decision_label(unsafe, False, 7))

    def test_legacy_giant_confirmation_dialog_is_removed(self):
        source = (Path(__file__).parents[1] / "src" / "agent_session_manager" / "gui.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('messagebox.askokcancel(\n                "历史残留扫描结果"', source)
        self.assertIn("class OrphanResultsWindow", source)
        self.assertIn("打开所在位置", source)
        self.assertIn("暂不清理或需人工处理", source)


if __name__ == "__main__":
    unittest.main()
