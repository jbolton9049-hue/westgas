import ast
import ast
import pathlib
import unittest


SOURCE = pathlib.Path(__file__).resolve().parents[1] / "main.py"
PROCESS_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "process.py"
AI_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "ai_tools.py"


class UiWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.process_source = PROCESS_SOURCE.read_text(encoding="utf-8")
        cls.ai_source = AI_SOURCE.read_text(encoding="utf-8")

    def test_file_choosers_are_parented_to_collect_window(self):
        """Native chooser dialogs must stay above the workflow window."""
        chooser_names = {"askopenfilename", "askopenfilenames", "askdirectory"}
        calls = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in chooser_names
        ]
        self.assertGreaterEqual(len(calls), 6)
        for call in calls:
            self.assertTrue(
                any(keyword.arg == "parent" for keyword in call.keywords),
                ast.unparse(call),
            )

    def test_manual_review_is_an_explicit_modal_confirmation(self):
        """Processing must open a modal review window before finalization."""
        self.assertIn('review_win = tk.Toplevel(win)', self.source)
        self.assertIn('review_win.grab_set()', self.source)
        self.assertIn('review_win.focus_force()', self.source)
        self.assertIn('review_win.protocol("WM_DELETE_WINDOW"', self.source)
        self.assertIn('人工审核确认', self.source)

    def test_review_uses_readable_category_labels(self):
        """Code-only labels must be accompanied by plain-language categories."""
        self.assertIn("DOMAIN_LABELS", self.source)
        self.assertIn("ATTR_LABELS", self.source)
        self.assertIn('"A2": "安全生产"', self.ai_source)
        self.assertIn('"B3": "安全管理"', self.ai_source)
        self.assertIn('"P3": "职能消极行为或失职"', self.ai_source)
        self.assertIn('"P4": "公司消极现象或形式主义"', self.ai_source)

    def test_review_shows_material_and_binary_decision(self):
        """A reviewer sees source evidence and one decision per finding."""
        self.assertIn('"raw_text": raw', self.process_source)
        self.assertIn("原始材料（自动提取文本，只读）", self.source)
        self.assertIn("同意", self.source)
        self.assertIn("不同意", self.source)

    def test_review_shows_issue_level_evidence_and_insight(self):
        self.assertIn("逐条审核管理问题", self.source)
        self.assertIn("原文依据：", self.source)
        self.assertIn("问题判断：", self.source)
        self.assertIn("管理内涵：", self.source)
        self.assertIn('"findings"', self.process_source)

    def test_confirm_review_advances_without_second_parent_submit(self):
        self.assertIn("finalize_selected(review_win)", self.source)
        self.assertIn("提交审核并进入下一步", self.source)

    def test_unselected_findings_are_excluded(self):
        self.assertIn('finding_state["decision"].get() == "agree"', self.source)
        self.assertIn("未选择或不同意的条目已排除", self.source)

    def test_review_displays_extraction_method(self):
        self.assertIn("文字来源：", self.source)
        self.assertIn("ocr_windows.ps1", (SOURCE.parent / "天然气管理工具.spec").read_text(encoding="utf-8"))

    def test_search_has_issue_summary_mode(self):
        self.assertIn("同类问题汇总 + 建议措施", self.source)
        self.assertIn("summarize_similar_issues", self.source)
        self.assertIn("保存汇总", self.source)

    def test_training_is_an_intelligent_spaced_repetition_module(self):
        self.assertIn("智能记忆训练", self.source)
        self.assertIn("自动生成卡片", self.source)
        self.assertIn("开始今日训练", self.source)
        self.assertIn("record_review", self.source)
        self.assertIn("掌握度", self.source)
        self.assertIn("审核选中卡片", self.source)
        self.assertIn("通过并加入训练", self.source)
        self.assertIn("训练设置", self.source)


if __name__ == "__main__":
    unittest.main()
