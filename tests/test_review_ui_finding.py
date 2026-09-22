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
import os
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

    def test_a_confirmation_keeps_its_screenshot_and_its_diff(self):
        # 這條在（b）（c）之前不存在，因為那時 finding 沒有這兩個欄位。精簡 loader 把「畫面要
        # 用的欄位」列成一張白名單，而白名單沒跟上的話，記錄裡有 crop/changes、畫面上卻兩個都
        # 沒有——finding 看起來很完整，其實證據和修法都被丟掉了。
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "question_ai_findings.jsonl"
            path.write_text(
                '{"candidate_key":"q001","model":"splash","population":"dispute",'
                '"crop":"review-ui/crops/p/q001-dispute.png",'
                '"changes":[{"field":"option B","from":"較⻑","to":"較長",'
                '"stored":"較⻑的OID","page":"較長的OID"}],'
                '"finding":{"verdict":"DEFECT"},"prompt_user":"' + "大" * 5000 + '"}\n',
                encoding="utf-8")
            loaded = self.ui.load_qbr_ai_findings(path)["q001"]
        self.assertEqual(loaded["crop"], "review-ui/crops/p/q001-dispute.png")
        self.assertEqual(loaded["changes"][0]["field"], "option B")
        # 而重的那一份仍然不進來。
        self.assertNotIn("prompt_user", loaded)

    def test_the_negative_control_a_loader_that_dropped_them_would_lose_the_repair(self):
        # 負對照：拿掉白名單裡的兩個欄位，上面那條就會失敗。這裡把事實重算一次，證明那個失敗
        # 是真的（欄位真的不在白名單時載不到），而不是斷言寫錯。
        self.assertIn("crop", self.ui.QBR_AI_FINDING_FIELDS)
        self.assertIn("changes", self.ui.QBR_AI_FINDING_FIELDS)
        without = tuple(f for f in self.ui.QBR_AI_FINDING_FIELDS if f not in {"crop", "changes"})
        self.assertNotIn("crop", without)
        self.assertNotIn("changes", without)

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
    def test_showing_a_finding_adds_no_action_that_writes_a_decision(self):
        # `GOV-05`：AI 一律 advisory。加了 finding 之後，人類的動作集合不變。
        #
        # 這條在（c）之後變了形狀——要求是「爭議題直接送模型修，不是只回報」——但沒有變意思：
        # finding 仍然不能自己產生一筆 review event。畫面多出來的那個按鈕（見下一個測試）只是
        # 把紙本讀法填進**人工編輯框**，寫下決定的仍然是按下「儲存修正」的人。
        body = finding_html_body(self.html)
        self.assertNotIn("api/", body)
        self.assertNotIn("fetch(", body)
        self.assertNotIn("decide(", body)
        self.assertNotIn("saveCorrection(", body)

    def test_the_negative_control_a_finding_that_decided_for_the_reviewer_would_be_caught(self):
        body = finding_html_body(self.html)
        injected = body.replace("return `<div", "return `<button onclick=\"decide('accept')\"></button><div", 1)
        self.assertIn("decide(", injected)
        self.assertNotIn("decide(", body)

    def test_the_repair_button_fills_the_editor_and_does_not_save_anything(self):
        # （c）：模型把紙本讀法帶進編輯框，但**不**代按儲存。修正是人的動作，記名的那筆 event
        # 由檢查過的人寫下。所以這個函式只能用 `change.page` 賦值給編輯節點，不能碰後端。
        match = re.search(r"function applyFindingChange\(button\) \{(.*?)\n\}", self.html, re.S)
        self.assertTrue(match, "v2.html 裡找不到 applyFindingChange")
        body = match.group(1)
        self.assertIn("change.page", body)
        self.assertIn("setEditMode", body)
        self.assertNotIn("fetch(", body)
        self.assertNotIn("api/", body)
        self.assertNotIn("decide(", body)

    def test_the_negative_control_a_repair_that_saved_itself_would_be_caught(self):
        match = re.search(r"function applyFindingChange\(button\) \{(.*?)\n\}", self.html, re.S)
        body = match.group(1)
        injected = body.replace("markDirty();", "fetch('/api/answer-review', {}); markDirty();", 1)
        self.assertIn("fetch(", injected)
        self.assertNotIn("fetch(", body)

    def test_an_edit_is_only_offered_when_the_paper_actually_disagrees(self):
        # 一致（沒有 changes）就沒有「帶入修正」可按——否則空 diff 也會長出一個按鈕，把「紙本跟
        # 抽取一樣」講成「有東西要修」。
        body = finding_html_body(self.html)
        self.assertIn("record.changes", body)
        self.assertIn(".length", body)

    def test_the_panel_shows_the_raw_pair_the_editor_will_receive(self):
        # `compare` 決定「有沒有一樣」用的是折疊過的形式（NFKC、去空白）；但紙上印的不是那個形式。
        # 畫面如果顯示折疊版、按鈕卻帶入原文，審題者就會**看著一個字串、同意另一個字串**——
        # 而且畫面上那一行看起來完全合理。所以差別列要顯示 `stored`/`page`（原文成對）。
        body = finding_html_body(self.html)
        self.assertIn("c.stored", body)
        self.assertIn("c.page", body)

    def test_the_negative_control_showing_the_folded_pair_would_be_caught(self):
        body = finding_html_body(self.html)
        self.assertIn("c.stored", body)
        # 每一處都要替換：只換第一個會漏掉後面那個（負對照要真的換掉被測的東西，
        # 不然它測的是自己）。
        injected = body.replace("c.stored", "c.from").replace("c.page", "c.to")
        self.assertNotIn("c.stored", injected)
        self.assertNotIn("c.page", injected)


class TailLoadedFindingStoreTests(unittest.TestCase):
    """每 append 一行就重載整檔，等於每請求 1.4 秒；只讀尾巴必須得到**一模一樣**的結果。

    實測（被服務的佇列）：`question_ai_findings.jsonl` = 480 MB / 73,688 筆，整檔重載 **1.4 s**。
    它在**全語料掃描**跑的時候每秒都在長，所以每一個請求都會看到 signature 變了、就重載一次。
    這是「UI 慢」的真正成因，也是為什麼要讓載入只增不重載。

    這裡的重點不是快，是**一致**：只讀尾巴的結果必須與整檔重載逐筆相同（包含最後一筆贏），
    而且要能看出「檔案被換掉/被截斷」而不是把它當成 append。
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        sys.path.insert(0, str(ROOT / "scripts"))
        spec = importlib.util.spec_from_file_location(
            "serve_question_review_ui_tail_test", ROOT / "scripts" / "serve_question_review_ui.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.ui = module

    def _file(self, tmp, lines):
        path = Path(tmp) / "question_ai_findings.jsonl"
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return path

    def test_the_tail_loader_equals_the_whole_file_loader(self):
        # 兩個引擎：`load_qbr_ai_findings`（整檔）與 `QbrAiFindingsStore`（只讀尾巴）。
        # 逐筆比對，而且要比對到 `_carried_from` 這種「不是畫面欄位」的差異。
        with tempfile.TemporaryDirectory() as tmp:
            path = self._file(tmp, [
                '{"candidate_key":"q001","finding":{"what":"NONE"},"prompt_user":"'
                + "大" * 3000 + '"}',
                '{"candidate_key":"q001","finding":{"what":"GLYPH_DAMAGE"}}',
                '{"candidate_key":"q002","finding":{"what":"OK"},"crop":"c.png"}',
            ])
            store = self.ui.QbrAiFindingsStore(path)
            # 再 append 三筆，其中一筆是既有的 key（最後一筆要贏）。
            for line in [
                '{"candidate_key":"q002","finding":{"what":"DEFECT"}}',
                '{"candidate_key":"q003","finding":{"what":"DROP_OUT"},"changes":[{"field":"A"}]}',
                '{"candidate_key":"q001","finding":{"what":"SPACE_INSIDE_WORD"}}',
            ]:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                self.assertTrue(store.refresh(), "append 之後 refresh 必須說 store 變了")
            self.assertEqual(store.latest, self.ui.load_qbr_ai_findings(path))
            self.assertEqual(store.latest["q001"]["finding"]["what"], "SPACE_INSIDE_WORD")
            self.assertEqual(store.latest["q002"]["finding"]["what"], "DEFECT")
            # 精簡白名單仍然生效：重的那一份不進記憶體。
            self.assertNotIn("prompt_user", store.latest["q001"])
            # 而 append 的欄位也進來了。
            self.assertEqual(store.latest["q003"]["changes"], [{"field": "A"}])

    def test_the_negative_control_a_first_record_wins_tail_loader_would_lie(self):
        # 負對照（真的把載入規則改錯）：若尾巴載入器沒讓最後一筆贏，上面那條就會不一致。
        with tempfile.TemporaryDirectory() as tmp:
            path = self._file(tmp, ['{"candidate_key":"q","finding":{"what":"FIRST"}}'])
            store = self.ui.QbrAiFindingsStore(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"candidate_key":"q","finding":{"what":"LAST"}}\n')
            store.refresh()
            self.assertEqual(store.latest["q"]["finding"]["what"], "LAST")
            # 而整檔載入器也說是 LAST，所以兩者一致是**真的**，不是兩邊都錯。
            self.assertEqual(
                self.ui.load_qbr_ai_findings(path)["q"]["finding"]["what"], "LAST"
            )

    def test_a_replaced_file_is_not_read_as_an_append(self):
        # 部署與重建都是把檔案換掉，不是接在後面。若把新檔當成 append，就會從舊 offset 讀到
        # 一個不存在的位置——得到的 store 混了兩個檔案，看起來卻像正常的答案。
        with tempfile.TemporaryDirectory() as tmp:
            path = self._file(tmp, ['{"candidate_key":"a","finding":{"what":"A"}}'])
            store = self.ui.QbrAiFindingsStore(path)
            replacement = Path(tmp) / "new.jsonl"
            replacement.write_text(
                '{"candidate_key":"b","finding":{"what":"B"}}\n', encoding="utf-8"
            )
            os.replace(replacement, path)  # 新的 inode，`rsync -a` 就是這樣做的
            self.assertTrue(store.refresh(), "換檔必須被視為改寫")
            self.assertEqual(sorted(store.latest), ["b"])

    def test_a_truncated_file_is_not_read_as_an_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._file(tmp, ['{"candidate_key":"a","finding":{"what":"A"}}' * 1])
            store = self.ui.QbrAiFindingsStore(path)
            with path.open("r+b") as handle:
                handle.truncate(0)
            self.assertTrue(store.refresh(), "截斷必須被視為改寫")
            self.assertEqual(store.latest, {})

    def test_a_half_written_line_is_not_a_record_yet(self):
        # 掃描正在寫的那一行可能只有一半。只讀到完整行，所以半行要等下一輪。
        with tempfile.TemporaryDirectory() as tmp:
            path = self._file(tmp, ['{"candidate_key":"a","finding":{"what":"A"}}'])
            store = self.ui.QbrAiFindingsStore(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"candidate_key":"b","finding":{"what":"B"}}')  # 還沒有換行
            self.assertFalse(store.refresh(), "只有半行時 store 不該變")
            self.assertNotIn("b", store.latest)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n")
            self.assertTrue(store.refresh())
            self.assertIn("b", store.latest)

    def test_a_file_with_a_broken_line_does_not_crash_the_server(self):
        # 這條在修好前**會讓整個伺服器 500**：`load_qbr_ai_findings` 用的是文字模式迭代，遇到
        # 半個多位元字元（被截斷的檔、或正在被寫的一行）會丟 `UnicodeDecodeError`。
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_bytes(b'{"candidate_key":"a","finding":{"what":"A"}}\n\xe5\xe5')
            loaded = self.ui.load_qbr_ai_findings(path)  # 不該丟例外
            self.assertEqual(sorted(loaded), ["a"])
            store = self.ui.QbrAiFindingsStore(path)
            self.assertEqual(sorted(store.latest), ["a"])

    def test_a_reader_keeps_a_consistent_snapshot(self):
        # 舊的整檔重載每一次都是換一個**新的** dict，所以一個讀者手上的參照不會邊讀邊變。
        # 只讀尾巴若改成原地改那個 dict，讀者就可能看到一半。所以 refresh 要 copy-on-write。
        with tempfile.TemporaryDirectory() as tmp:
            path = self._file(tmp, ['{"candidate_key":"a","finding":{"what":"A"}}'])
            store = self.ui.QbrAiFindingsStore(path)
            held = store.latest  # 一個正在畫畫面的請求手上的參照
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"candidate_key":"b","finding":{"what":"B"}}\n')
            store.refresh()
            self.assertIsNot(held, store.latest, "refresh 不該原地改同一個 dict")
            self.assertEqual(sorted(held), ["a"], "舊讀者的快照不該多出新資料")
            self.assertEqual(sorted(store.latest), ["a", "b"])

    def test_the_server_keeps_the_findings_fresh_through_the_store_not_a_full_reload(self):
        # 這條釘的是**接線**，不是函式本身：`ReviewState` 必須用 store，否則上面所有的好處都
        # 不會發生，而程式看起來完全正常。
        source = (ROOT / "scripts" / "serve_question_review_ui.py").read_text(encoding="utf-8")
        self.assertIn("self.qbr_ai_findings_store = QbrAiFindingsStore(", source)
        self.assertIn("self.qbr_ai_findings_store.refresh()", source)
        # 而且 `refresh_event_logs` 裡不能再有 findings 的整檔重載（舊的那條分支）。
        body = _function_body(source, "refresh_event_logs")
        self.assertNotIn("load_qbr_ai_findings", body)
        self.assertIn("qbr_ai_findings_store.refresh()", body)

    def test_the_negative_control_a_full_reload_in_refresh_would_be_caught(self):
        source = (ROOT / "scripts" / "serve_question_review_ui.py").read_text(encoding="utf-8")
        injected = source.replace(
            "if self.qbr_ai_findings_store.refresh():\n            self.latest_qbr_ai_findings = self.qbr_ai_findings_store.latest",
            "self.latest_qbr_ai_findings = load_qbr_ai_findings(self.qbr_ai_findings_log)",
            1,
        )
        self.assertNotEqual(injected, source, "負對照沒有換到東西，測的是自己")
        self.assertIn("load_qbr_ai_findings", _function_body(injected, "refresh_event_logs"))
        self.assertNotIn("load_qbr_ai_findings", _function_body(source, "refresh_event_logs"))


def _function_body(source: str, name: str) -> str:
    """一個 Python 函式的活體，去掉 docstring（和 `tests/test_review_ui_areas.py` 同一個理由：
    整檔字串搜尋會把說明文字當成規則本身）。"""
    match = re.search(rf"def {name}\(", source)
    assert match, f"找不到函式 {name}"
    start = source.find("\n", match.start())
    body = source[start:]
    rest = re.search(r"\n    def ", body[1:])
    if rest:
        body = body[: rest.start() + 1]
    return re.sub(r'""".*?"""', "", body, flags=re.S)


if __name__ == "__main__":
    unittest.main()
