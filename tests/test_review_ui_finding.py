"""模型說的話必須畫在畫面上，而且要畫成「意見」，不是「量測」，也不是「決定」。

這條迴圈（人阻擋 → 模型說哪裡錯、怎麼修 → 人確認 → 寫成規則 → 重掃）到目前為止產出了
61,000 筆 finding（`qbr/src/qbr/ai_findings.py::STREAM`），而**它們在 v2 上一筆都看不到**。
使用者要的「（b）把有疑義的題目截圖給 27B-splash 確認」「（c）爭議題直接送模型修，不是只回報」，
前提是審題者在畫面上真的看得到那個模型的意見——看不到的產物，等於沒有產物。

這個檔案釘住的是**顯示本身**，所以讀的是活的節點（`findingHtml` 的呼叫點、伺服器上真的存在
的 `qbr_ai_finding` 欄位、活的 CSS class），不是說明文字。每個檢查都有負對照：拿掉呼叫、
或把 finding 併進 `ai_review`，對應的斷言必須失敗。

三個不能混的東西，這個檔案分開釘：

  1. finding 與 decisions 不同作者：finding 沒有 `action`、沒有 `reviewer`，所以不可能被讀成
     一筆人類決定。伺服器把它載進**另一個**欄位（`qbr_ai_finding`），不是併進 `ai_review`。
  2. finding 與 disputes 不同來源：disputes 是紙本量測，finding 是模型意見，畫面上有兩種標題。
  3. finding 是 advisory（`GOV-05`）：顯示它不會多出任何按鈕。
"""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "review_ui" / "v2.html"
SERVER = ROOT / "scripts" / "serve_question_review_ui.py"


def import_server():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "serve_question_review_ui_finding_test", SERVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def finding_html_body(html: str) -> str:
    match = re.search(r"function findingHtml\(candidate\) \{(.*?)\n\}", html, re.S)
    assert match, "v2.html 裡找不到 findingHtml"
    return match.group(1)


class FindingVisibleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.ui = import_server()

    # --- 伺服器：loading 與欄位 ---------------------------------------------
    def test_the_stream_is_read_from_the_queue_not_a_hard_coded_home(self):
        # 路徑要跟著 `review_log` 的目錄走，因為審題的家（QBR_QUEUE_DIR）是可以搬的。
        self.assertIn("QBR_AI_FINDINGS_STREAM", self.ui.__dict__)
        self.assertTrue(self.ui.QBR_AI_FINDINGS_STREAM.endswith(".jsonl"))
        # 而且它必須和人類事件流**同名同地**：只有 `review-ui/` 裡的 stream 會被重建帶著走。
        self.assertEqual(self.ui.QBR_AI_FINDINGS_STREAM, "question_ai_findings.jsonl")

    def test_the_loader_keeps_the_last_record_per_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "question_ai_findings.jsonl"
            path.write_text(
                '{"candidate_key":"q001","finding":{"verdict":"OK","what":"NONE"},'
                '"prompt_system":"X","prompt_user":"Y"}\n'
                '{"candidate_key":"q001","finding":{"verdict":"DEFECT","what":"GLYPH_DAMAGE"}}\n'
                '{"candidate_key":"q002","finding":{"verdict":"OK","what":"NONE"}}\n',
                encoding="utf-8")
            loaded = self.ui.load_qbr_ai_findings(path)
        self.assertEqual(sorted(loaded), ["q001", "q002"])
        # 最後一筆贏，和人類事件流的規則一樣。
        self.assertEqual(loaded["q001"]["finding"]["what"], "GLYPH_DAMAGE")

    def test_the_negative_control_a_loader_that_kept_the_first_record_would_lie(self):
        # 負對照：如果 loader 是 dict.setdefault（第一筆贏），上面那條「最後一筆贏」會失敗。
        # 這裡直接證明兩筆的 `what` 不同，所以「哪一筆贏」是有意義的問題。
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.jsonl"
            path.write_text(
                '{"candidate_key":"q","finding":{"what":"OK_FIRST"}}\n'
                '{"candidate_key":"q","finding":{"what":"OK_LAST"}}\n', encoding="utf-8")
            loaded = self.ui.load_qbr_ai_findings(path)
        self.assertNotEqual("OK_FIRST", "OK_LAST")
        self.assertEqual(loaded["q"]["finding"]["what"], "OK_LAST")

    def test_the_compact_loader_drops_the_bulk_but_keeps_what_the_screen_needs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "question_ai_findings.jsonl"
            path.write_text(
                '{"candidate_key":"q001","model":"splash","prompt_version":"v1",'
                '"population":"corpus","finding":{"verdict":"DEFECT","what":"X",'
                '"where":"w","fix":"f","rule_worthy":true,"confidence":0.9},'
                '"evidence":{"stem":"s"},"prompt_user":"' + "大" * 5000 + '"}\n',
                encoding="utf-8")
            loaded = self.ui.load_qbr_ai_findings(path)["q001"]
        # 畫面上要用的：作者、世代、以及模型說的話。
        self.assertEqual(loaded["model"], "splash")
        self.assertEqual(loaded["prompt_version"], "v1")
        self.assertEqual(loaded["population"], "corpus")
        self.assertEqual(loaded["finding"]["where"], "w")
        # 不是畫面要用的，而且很重：prompt 留在檔案裡可查，不進記憶體。
        self.assertNotIn("prompt_user", loaded)
        self.assertNotIn("prompt_system", loaded)

    def test_the_finding_is_a_separate_field_from_the_sql_era_ai_review(self):
        # 這是這一條最重要的不變量：finding 沒有 status、沒有 recommended_action，它不是一次
        # 審核。把它併進 `ai_review` 就會讓一則筆記被讀成一次有狀態的審核。
        source = SERVER.read_text(encoding="utf-8")
        self.assertIn('copy["qbr_ai_finding"] = ', source)
        self.assertIn('copy["ai_review"] = {', source)

    def test_the_negative_control_folding_the_finding_into_ai_review_is_not_what_happens(self):
        source = SERVER.read_text(encoding="utf-8")
        # 負對照：把 finding 塞進 ai_review 的寫法長這樣，而它不在檔案裡。
        folded = 'copy["ai_review"]["qbr_finding"]'
        self.assertNotIn(folded, source)

    # --- 顯示：真的有畫，而且畫成意見 ---------------------------------------
    def test_the_finding_is_rendered_on_the_text_side(self):
        # 讀呼叫點，不是讀函式定義：有定義但沒呼叫，等於沒顯示。
        self.assertIn("${findingHtml(candidate)}", self.html)

    def test_the_negative_control_without_the_call_nothing_is_shown(self):
        without = self.html.replace("${findingHtml(candidate)}", "")
        self.assertNotIn("${findingHtml(candidate)}", without)
        # 定義還在，但畫面上不會出現——所以「有畫出來」這條斷言不是恆真。
        self.assertIn("function findingHtml(", without)

    def test_the_panel_says_it_is_the_model_talking_not_the_paper(self):
        body = finding_html_body(self.html)
        self.assertIn("模型意見", body)
        # 誰說的要寫出來，否則讀者無從權衡（可查證性）。
        self.assertIn("record.model", body)

    def test_the_where_and_the_fix_are_both_shown(self):
        body = finding_html_body(self.html)
        self.assertIn("哪裡：", body)
        self.assertIn("怎麼修：", body)

    def test_an_unclassified_verdict_is_not_dressed_up_as_a_defect(self):
        # `parse_finding` 會讓無法歸類的 verdict 保持 None。畫面不能把它印成「模型認為有問題」。
        body = finding_html_body(self.html)
        self.assertIn("無法歸類", body)
        self.assertIn("模型認為不是抽取造成的", body)

    def test_the_population_is_shown_because_being_blocked_changes_what_the_note_means(self):
        body = finding_html_body(self.html)
        self.assertIn("population", body)
        self.assertIn("整庫掃描", body)

    # --- 意見不是決定 -------------------------------------------------------
    def test_showing_a_finding_adds_no_button(self):
        # `GOV-05`：AI 一律 advisory。加了 finding 之後，人類的動作集合不變。
        body = finding_html_body(self.html)
        self.assertNotIn("<button", body)
        self.assertNotIn("onclick", body)

    def test_the_negative_control_a_finding_with_buttons_would_be_caught(self):
        body = finding_html_body(self.html)
        injected = body.replace("return `<div", 'return `<button></button><div', 1)
        self.assertIn("<button", injected)
        self.assertNotIn("<button", body)


if __name__ == "__main__":
    unittest.main()
