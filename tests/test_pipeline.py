# -*- coding: utf-8 -*-

import json
import os
import tempfile
import unittest
import zipfile
import datetime
from unittest import mock

import ai_tools
import process
import search
import train
import maintenance
import scheduled_tasks


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_config = ai_tools.CONFIG_PATH
        self.config = os.path.join(self.tmp.name, "config.json")
        ai_tools.CONFIG_PATH = self.config
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"knowledge_base": os.path.join(self.tmp.name, "kb"),
                       "default_tool": "rule", "ai_tools": {}}, f)
        self.source = os.path.join(self.tmp.name, "安全问题.txt")
        with open(self.source, "w", encoding="utf-8") as f:
            f.write("站场隐患排查不到位，岗位职责不清，问题反复发生，需要落实安全管理责任。")
        basis_dir = os.path.join(self.tmp.name, "kb", "07_管理依据库")
        os.makedirs(basis_dir, exist_ok=True)
        with open(os.path.join(basis_dir, "安全生产责任制.md"), "w", encoding="utf-8") as f:
            f.write("# 安全生产责任制\n\n站场隐患排查和安全管理责任应当落实到岗到人。")

    def tearDown(self):
        ai_tools.CONFIG_PATH = self.old_config
        self.tmp.cleanup()

    def test_rule_pipeline_search_and_training(self):
        result = process.automate_file(self.source, "rule")
        self.assertTrue(result["ok"])
        self.assertEqual(result["tag"]["domain"], "A2")
        for key in ["raw_file", "ledger_file", "solution_file", "event_file", "card_file"]:
            self.assertTrue(os.path.isfile(result[key]), key)
        with open(result["ledger_file"], "r", encoding="utf-8") as f:
            ledger = f.read()
        self.assertIn("后果归纳", ledger)
        self.assertIn("已自动分析", ledger)
        with open(result["event_file"], "r", encoding="utf-8") as f:
            self.assertIn("安全生产责任制", f.read())
        hits = search.search_knowledge("安全 责任")
        self.assertTrue(hits)
        due = train.due_cards()
        self.assertTrue(any(item["file"] == os.path.basename(result["card_file"]) for item in due))
        checkin = train.daily_checkin(result["card_file"])
        self.assertTrue(os.path.isfile(checkin))

    def test_auto_generate_cards_from_knowledge_base_is_incremental(self):
        points_dir = os.path.join(self.tmp.name, "kb", "03_管理要点")
        os.makedirs(points_dir, exist_ok=True)
        with open(os.path.join(points_dir, "A2_隐患闭环.md"), "w", encoding="utf-8") as f:
            f.write(
                "# 一页纸解决思路：隐患闭环\n\n"
                "## 管理判断\n\n隐患排查必须形成闭环。\n\n"
                "## 总经理一句话\n\n把发现的问题管到底。\n\n"
                "## 五维措施\n\n1. 明确责任人和完成时限\n"
            )
        first = train.generate_cards_from_knowledge(topic="隐患", limit=10)
        self.assertGreaterEqual(len(first), 1)
        self.assertTrue(any(os.path.isfile(item["file"]) for item in first))
        self.assertEqual(train.list_pending_cards()[0]["status"], "pending")
        self.assertFalse(any(item["file"] in train.list_cards() for item in first))
        card_texts = []
        for item in first:
            with open(item["file"], encoding="utf-8") as card_file:
                card_texts.append(card_file.read())
        cards_text = "\n".join(card_texts)
        self.assertIn("卡片类型", cards_text)
        self.assertIn("A2_隐患闭环.md", cards_text)
        second = train.generate_cards_from_knowledge(topic="隐患", limit=10)
        self.assertEqual(second, [])

    def test_generated_card_requires_approval_and_can_be_edited(self):
        points_dir = os.path.join(self.tmp.name, "kb", "03_管理要点")
        os.makedirs(points_dir, exist_ok=True)
        with open(os.path.join(points_dir, "隐患闭环.md"), "w", encoding="utf-8") as f:
            f.write("# 隐患闭环\n## 管理判断\n隐患必须闭环。\n## 五维措施\n1. 指定责任人\n")
        draft = train.generate_cards_from_knowledge(topic="隐患", limit=1)[0]
        self.assertEqual(train.due_cards(), [])
        approved = train.approve_card(draft["file"], edits={"problem": "如何让隐患闭环？",
                                                           "action": "指定责任人和期限"})
        self.assertTrue(os.path.isfile(approved))
        self.assertEqual(len(train.due_cards()), 1)
        with open(approved, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("如何让隐患闭环", content)
        self.assertIn("指定责任人和期限", content)
        self.assertFalse(train.list_pending_cards())

    def test_rejected_generated_card_is_archived_and_not_regenerated(self):
        points_dir = os.path.join(self.tmp.name, "kb", "03_管理要点")
        os.makedirs(points_dir, exist_ok=True)
        with open(os.path.join(points_dir, "隐患闭环.md"), "w", encoding="utf-8") as f:
            f.write("# 专项演练闭环\n## 管理判断\n专项演练必须闭环。\n")
        draft = train.generate_cards_from_knowledge(topic="专项演练", limit=1)[0]
        archived = train.reject_card(draft["file"])
        self.assertTrue(os.path.isfile(archived))
        self.assertEqual(train.generate_cards_from_knowledge(topic="专项演练", limit=1), [])
        self.assertEqual(train.list_cards(), [])

    def test_spaced_repetition_mastery_and_weak_card_dashboard(self):
        card = train.generate_card("责任闭环", "隐患为什么反复？", "责任不清", "明确责任人", "把问题管到底")
        progress = None
        for _ in range(5):
            progress = train.record_review(card, "remember")
        self.assertGreaterEqual(progress["mastery"], 80)
        self.assertTrue(progress["mastered"])
        self.assertGreater(progress["due"], datetime.date.today().isoformat())
        weak = train.generate_card("薄弱点", "什么是闭环？", "未形成复盘", "建立复盘", "发现即闭环")
        train.record_review(weak, "forget")
        dashboard = train.training_dashboard()
        self.assertGreaterEqual(dashboard["total"], 2)
        self.assertGreaterEqual(dashboard["mastered"], 1)
        self.assertGreaterEqual(dashboard["weak"], 1)

    def test_training_settings_and_reminder_schedule(self):
        train.update_training_settings(daily_limit=3, mastery_threshold=85,
                                       reminder_time="08:30", reminder_frequency="only_due")
        settings = train.get_training_settings()
        self.assertEqual(settings["daily_limit"], 3)
        self.assertEqual(settings["mastery_threshold"], 85)
        self.assertEqual(settings["reminder_time"], "08:30")
        self.assertEqual(settings["reminder_frequency"], "only_due")
        card = train.generate_card("复习提醒", "何时复习？", "会遗忘", "按期复习", "今天复习")
        self.assertFalse(train.should_show_reminder(now=datetime.datetime(2026, 9, 30, 8, 29)))
        self.assertTrue(train.should_show_reminder(now=datetime.datetime(2026, 9, 30, 8, 30)))
        train.mark_reminder_shown(on_date=datetime.date(2026, 9, 30))
        self.assertFalse(train.should_show_reminder(now=datetime.datetime(2026, 9, 30, 9, 0)))

    def test_batch_continues_after_unsupported_files(self):
        with open(os.path.join(self.tmp.name, "ignore.bin"), "wb") as f:
            f.write(b"data")
        results = process.automate_folder(self.tmp.name, "rule")
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][1]["ok"])

    def test_prepare_waits_for_human_review_before_ledger(self):
        result = process.prepare_file(self.source, "rule")
        self.assertTrue(result["ok"])
        self.assertNotIn("ledger_file", result)
        self.assertIn("raw_text", result)
        self.assertIn("站场隐患排查不到位", result["raw_text"])
        ledger_dir = os.path.join(self.tmp.name, "kb", "02_分类台账")
        self.assertFalse(os.path.isdir(ledger_dir) and os.listdir(ledger_dir))
        result["tag"]["domain"] = "A2"
        result["tag"]["attr"] = "P1"
        final = process.finalize_processed(self.source, result, "rule")
        self.assertTrue(os.path.isfile(final["ledger_file"]))
        self.assertTrue(os.path.isfile(final["solution_file"]))

    def test_hse_review_contains_evidence_backed_findings(self):
        source = os.path.join(self.tmp.name, "HSE奖惩考核制度.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write(
                "HSE奖惩考核制度。全员参与。奖励考核依据行政处罚和事故。"
                "适用范围为HSE委员会成员及安全相关岗位。奖励经党委会、董事会审议。"
                "奖励金额1000-10000元。附件2 HSE考评表为空白模板。"
            )
        result = process.prepare_file(source, "rule")
        self.assertEqual(result["tag"]["attr"], "P1")
        self.assertEqual(len(result["findings"]), 5)
        self.assertTrue(all(item["evidence"] for item in result["findings"]))
        self.assertTrue(all(item["domain"] == "B3" for item in result["findings"]))
        final = process.finalize_processed(source, result, "rule")
        with open(final["ledger_file"], "r", encoding="utf-8") as f:
            ledger = f.read()
        self.assertIn("AI提取的具体问题", ledger)
        self.assertIn("原文依据", ledger)
        self.assertIn("B3 安全管理", ledger)

    def test_finalize_writes_only_agreed_findings(self):
        result = process.prepare_file(self.source, "rule")
        result["analysis"]["findings"] = [result["findings"][0]]
        result["analysis"]["findings"][0]["statement"] = "同意的问题"
        result["analysis"]["findings"].append({
            "title": "不同意的问题", "statement": "不应进入总结", "evidence": "原文",
            "domain": "B3", "attr": "P1", "management_insight": "不应进入",
        })
        result["analysis"]["findings"] = result["analysis"]["findings"][:1]
        final = process.finalize_processed(self.source, result, "rule")
        with open(final["ledger_file"], "r", encoding="utf-8") as f:
            ledger = f.read()
        self.assertIn("同意的问题", ledger)
        self.assertNotIn("不应进入总结", ledger)

    def test_daily_maintenance_creates_review_draft_and_monthly_report(self):
        raw_dir = os.path.join(self.tmp.name, "kb", "01_原始资料库")
        os.makedirs(raw_dir, exist_ok=True)
        source = os.path.join(raw_dir, "新增安全资料.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("新增站场安全隐患，要求落实岗位责任和整改闭环。")
        result = maintenance.run_daily("rule")
        self.assertEqual(len(result["new"]), 1)
        self.assertEqual(len(result["drafts"]), 1)
        with open(result["drafts"][0], "r", encoding="utf-8") as f:
            self.assertIn("review_status: pending", f.read())
        report = maintenance.generate_period_report("monthly")
        self.assertTrue(os.path.isfile(report))
        with open(report, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("A2", content)

    def test_approved_draft_publishes_reviewed_content_once(self):
        raw_dir = os.path.join(self.tmp.name, "kb", "01_原始资料库")
        os.makedirs(raw_dir, exist_ok=True)
        source = os.path.join(raw_dir, "待审核资料.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("站场隐患排查不到位，需要明确安全责任并闭环整改。")
        result = maintenance.run_daily("rule")
        draft = result["drafts"][0]
        with open(draft, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("review_status: pending", "review_status: approved")
        content = content.replace("围绕A2把要求变成责任、流程和结果闭环，避免只部署不落地。", "人工审核确认的管理表达。")
        with open(draft, "w", encoding="utf-8") as f:
            f.write(content)
        published = maintenance.publish_approved("rule")
        self.assertEqual(len(published), 1)
        ledger_dir = os.path.join(self.tmp.name, "kb", "02_分类台账")
        ledgers = [name for name in os.listdir(ledger_dir) if name.endswith(".md")]
        with open(os.path.join(ledger_dir, ledgers[0]), "r", encoding="utf-8") as f:
            self.assertIn("人工审核确认的管理表达。", f.read())
        self.assertEqual(maintenance.publish_approved("rule"), [])

    def test_periodic_task_command_is_noninteractive(self):
        command = scheduled_tasks._job_command("daily")
        self.assertIn("daily", command)
        self.assertNotIn("cmd.exe", command.lower())

    def test_approved_draft_is_stale_if_source_changes_after_review(self):
        raw_dir = os.path.join(self.tmp.name, "kb", "01_原始资料库")
        os.makedirs(raw_dir, exist_ok=True)
        source = os.path.join(raw_dir, "会变化的资料.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("原始版本：岗位责任需要落实。")
        draft = maintenance.run_daily("rule")["drafts"][0]
        with open(draft, "r", encoding="utf-8") as f:
            content = f.read().replace("review_status: pending", "review_status: approved")
        with open(draft, "w", encoding="utf-8") as f:
            f.write(content)
        with open(source, "w", encoding="utf-8") as f:
            f.write("已修改版本：岗位责任和安全整改都需要落实。")
        self.assertEqual(maintenance.publish_approved("rule"), [])
        with open(draft, "r", encoding="utf-8") as f:
            self.assertIn("review_status: stale", f.read())

    def test_removed_source_is_reported_only_once(self):
        raw_dir = os.path.join(self.tmp.name, "kb", "01_原始资料库")
        os.makedirs(raw_dir, exist_ok=True)
        source = os.path.join(raw_dir, "将删除.txt")
        with open(source, "w", encoding="utf-8") as f:
            f.write("资料内容")
        maintenance.scan_incremental()
        os.remove(source)
        self.assertEqual(len(maintenance.scan_incremental()["removed"]), 1)
        self.assertEqual(maintenance.scan_incremental()["removed"], [])

    def test_tabular_files_are_supported_end_to_end(self):
        csv_path = os.path.join(self.tmp.name, "台账.csv")
        with open(csv_path, "w", encoding="gb18030", newline="") as f:
            f.write("问题,责任\n隐患整改,岗位责任")
        csv_result = process.automate_file(csv_path, "rule")
        self.assertTrue(csv_result["ok"])
        self.assertIn("隐患整改", csv_result["text"])

        xlsx_path = os.path.join(self.tmp.name, "台账.xlsx")
        workbook = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
            "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'/>"
        )
        shared = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
            "<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' count='2' uniqueCount='2'>"
            "<si><t>隐患整改</t></si><si><t>岗位责任</t></si></sst>"
        )
        sheet = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
            "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData>"
            "<row><c t='s'><v>0</v></c><c t='s'><v>1</v></c></row>"
            "</sheetData></worksheet>"
        )
        with zipfile.ZipFile(xlsx_path, "w") as z:
            z.writestr("xl/workbook.xml", workbook)
            z.writestr("xl/sharedStrings.xml", shared)
            z.writestr("xl/worksheets/sheet1.xml", sheet)
        xlsx_result = process.prepare_file(xlsx_path, "rule")
        self.assertTrue(xlsx_result["ok"])
        self.assertIn("岗位责任", xlsx_result["text"])

    def test_scanned_pdf_uses_ocr_fallback_when_native_text_is_empty(self):
        original = process._extract_pdf_ocr
        try:
            process._extract_pdf_ocr = lambda _path: "OCR识别出的合规管理办法"
            empty_reader = mock.Mock()
            empty_reader.pages = []
            with mock.patch("pypdf.PdfReader", return_value=empty_reader):
                text, method = process._extract_pdf_with_method("dummy.pdf")
        finally:
            process._extract_pdf_ocr = original
        self.assertEqual(text, "OCR识别出的合规管理办法")
        self.assertEqual(method, "Windows中文OCR")

    def test_issue_summary_groups_similar_reviewed_issues_and_recommends_measures(self):
        ledger_dir = os.path.join(self.tmp.name, "kb", "02_分类台账")
        os.makedirs(ledger_dir, exist_ok=True)
        for index, issue in enumerate(("隐患排查不到位", "隐患排查不及时"), 1):
            with open(os.path.join(ledger_dir, f"台账_{index}.md"), "w", encoding="utf-8") as f:
                f.write(
                    f"# 台账\n- **领域**：B3 安全管理\n- **属性**：P1 管理问题或缺陷\n"
                    f"## 二、问题定性\n\n{issue}\n\n"
                    "### 根因\n\n- 过程检查没有形成闭环\n\n"
                    "## 五、建议措施\n\n1. 明确责任人并建立月度检查\n\n"
                    "## AI提取的具体问题（逐条人工确认）\n\n"
                    f"### 1. {issue}\n\n- **问题判断**：{issue}\n"
                    "- **原文依据**：原文证据\n- **领域/属性**：B3 安全管理 / P1 管理问题或缺陷\n"
                    "- **管理内涵候选**：把检查变成闭环\n"
                )
        groups = search.summarize_similar_issues()
        self.assertTrue(groups)
        self.assertEqual(groups[0]["count"], 2)
        self.assertTrue(groups[0]["measures"])
        self.assertIn("建议措施", search.format_issue_summary(groups))


if __name__ == "__main__":
    unittest.main()
