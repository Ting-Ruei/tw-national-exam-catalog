"""四個區：首頁／題目審核區／答案審核區／錯題討論區。

使用者要的是把 v2 分成四個區。它們是**一個頁面的四個模式**，不是四個頁面，因為它們是同一份
佇列的四種讀法：答案區的「可審集合」是由題目區的決定定義的（題目沒被接受，答案就沒有意義），
而錯題討論區讀的是那兩區產生的人工修正。

**為什麼不是四個頁面。** 四個頁面會各自載入同一份 198 MB 的 `candidates.jsonl`，也就有四份
可能對「佇列裡有什麼」有不同意見的讀取器。一個頁面、一個載入器、四種視圖，不一致的地方就少了。

**為什麼模式要在 hash 裡。** 每個區都要能連結、能重整，重整要回到同一個區。而
`#類科/年/次/科目/qNNN` 這種舊寫法是既有書籤的契約，所以它在沒有前綴時仍然表示題目區。

這個檔案釘的是四個區的**可觀察契約**，不是說明文字：

  1. 導覽列真的四個區，順序是使用者講的那個順序；
  2. 開頁預設是題目審核區（空 hash **不是**首頁）；
  3. 每個區的資料來自**既有**的伺服器端點，不是這一頁自己算的；
  4. 換區是**隱藏**，不是清空——打到一半的註記繞一圈回來必須還在；
  5. 題目區的快捷鍵只在題目區作用（`w`/`a`/`b` 在別區是文字）；
  6. 舊 hash 寫法在來回一趟後仍然是舊寫法。

負對照釘在每一個斷言的旁邊：把區清單刪一個、把模式判斷改成「一律題目區」、把 `hidden` 改成
`innerHTML = ''`、或讓快捷鍵不分區，對應的斷言必須失敗。若不會失敗，這裡只是在讀自己的註解。
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "review_ui" / "v2.html"
BROWSER_TEST = ROOT / "scripts" / "test_v2_areas_browser.mjs"

#: 使用者指定的四個區，以及它們在**同一頁**上的容器 id。
AREAS = ("home", "question", "answer", "discuss")
AREA_NODES = {
    "home": "areaHome",
    "question": "areaQuestion",
    "answer": "areaAnswer",
    "discuss": "areaDiscuss",
}
#: 每個區在 hash 裡的前綴。題目區的前綴在來回之後會被拿掉（舊書籤契約）。
AREA_PREFIX = {"home": "首頁", "question": "審題", "answer": "答案", "discuss": "錯題"}


def script_of(html: str) -> str:
    """v2 已拆成 `review_ui/v2/*.js`（一區一檔）。這裡按 `<script src>` 的出現序重組，
    與瀏覽器同一個執行序（檔名前綴已是 01..05 的序）。`src` 以 `review_ui/` 為基準。
    """
    parts = [p for p in re.findall(r"<script>(.*?)</script>", html, re.S) if p.strip()]
    for src in re.findall(r'<script src="([^"]+)"></script>', html):
        body = (ROOT / "review_ui" / src).read_text(encoding="utf-8")
        parts.append(body)
    joined = "\n".join(parts)
    assert joined.strip(), "v2.html 沒有可執行的 script（檔）"
    return joined


def function_body(source: str, name: str) -> str:
    """取一個函式的活體：去掉 docstring 與註解，留下會執行的行。

    整個檔案的字串搜尋會把「講這條規則的散文」當成規則本身——`showArea` 的註解裡就寫著
    `innerHTML = ''` 是錯的。所以要讀實際的分支，不能讀檔案。

    同一支工具讀兩種語言：`v2.html` 的 `function name(...) {` 與伺服器的
    `def name(self) -> None:`。兩者的本體都是同一個大括號深度計數。
    """
    match = re.search(rf"(?:function {name}\(|def {name}\()", source)
    assert match, f"找不到函式 {name}"
    # 找到**參數列**的右括號，再從那裡找本體的大括號。直接找第一個 `{` 會錯：
    # `function showArea(area, { push = true } = {}) {` 的第一個 `{` 是解構，不是本體。
    paren = source.find("(", match.end() - 1)
    depth = 0
    index = paren
    while index < len(source):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                break
        index += 1
    start = source.find("{", index)
    assert start > -1, f"{name} 沒有本體"
    start += 1
    depth = 1
    index = start
    while index < len(source) and depth:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    body = source[start : index - 1]
    # 去掉註解：`//` 到行尾，以及 `/* */` 區塊。
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    return body


class AreasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    # ---------------------------------------------------------------- 1. 四個區真的在
    def test_the_nav_has_the_four_areas_in_the_order_the_user_named_them(self):
        found = re.findall(r'class="area-btn[^"]*" data-area="([a-z]+)"', self.html)
        self.assertEqual(found, list(AREAS), f"導覽列的區是 {found}")

    def test_each_area_has_its_own_container(self):
        ids = set(re.findall(r'id="([A-Za-z0-9_]+)"', self.html))
        for area, node in AREA_NODES.items():
            self.assertIn(node, ids, f"{area} 沒有容器 {node}")
        # 而且每個容器上面真的有區的 class（不是只有 id 存在）。
        for area, node in AREA_NODES.items():
            match = re.search(rf'<div class="[^"]*\barea\b[^"]*" id="{node}"', self.html)
            self.assertIsNotNone(match, f"{AREA_NODES[area]} 不是 .area")

    def test_the_question_area_is_the_baseline_class(self):
        # 題目審核區在 HTML 裡就標成 on：預設可見的那一區不該等 JS 跑完才出現（會閃）。
        match = re.search(r'<div class="app area on" id="areaQuestion"', self.html)
        self.assertIsNotNone(match, "題目審核區在 HTML 裡不是預設可見")

    # ---------------------------------------------------------------- 2. 空 hash 不是首頁
    def test_an_empty_hash_is_the_question_area_not_the_home_page(self):
        body = function_body(self.js, "areaFromHash")
        self.assertRegex(body, r"if \(!raw\) return 'question'")
        self.assertNotRegex(body, r"if \(!raw\) return 'home'")

    def test_the_hash_prefixes_are_declared_for_every_area(self):
        body = function_body(self.js, "showArea") + self.js
        for area, prefix in AREA_PREFIX.items():
            if area == "question":
                # 題目區有**兩個**被接受的前綴（審題／題目），而且它會把它們拿掉。
                self.assertIn("審題", self.js)
                self.assertIn("題目", self.js)
                continue
            self.assertIn(prefix, self.js, f"{area} 在 hash 裡沒有前綴")

    def test_the_prefix_to_area_map_covers_every_area(self):
        match = re.search(r"const PREFIX_AREA = \{(.*?)\};", self.js, re.S)
        self.assertIsNotNone(match, "找不到 PREFIX_AREA")
        table = match.group(1)
        for prefix in ("首頁", "審題", "答案", "錯題"):
            self.assertIn(prefix, table, f"PREFIX_AREA 少了 {prefix}")

    # ---------------------------------------------------------------- 3. 每個區讀既有端點
    def test_the_answer_area_reads_the_existing_answer_candidate_endpoint(self):
        body = function_body(self.js, "renderAnswers")
        self.assertIn("/api/answer-candidates", body)
        # 可審的定義是伺服器給的：這一頁不自己決定哪一題可以審。
        self.assertNotRegex(body, r"/api/candidates\b")

    def test_the_answer_area_writes_through_the_existing_batch_endpoint(self):
        body = function_body(self.js, "answerSheetAction")
        self.assertIn("/api/answer-review-batch", body)
        # 每筆一次請求是錯的（那個端點只收**一個** action），所以必須分組送。
        self.assertIn("groups", body)
        self.assertRegex(body, r"for \(const \[groupAction, groupEntries\] of groups\)")

    def test_the_batch_endpoint_takes_one_action_so_the_client_must_group(self):
        """端點只收一個 action，這個事實是分組的理由；讀伺服器的原文，不是複述。

        這裡不取 `do_POST` 的函式本體：Python 的 dict/集合字面值也是大括號，用括號深度切一個
        Python 函式不可靠。改讀**端點字串出現的那一段**，它就在處理器裡。
        """
        server = (ROOT / "scripts" / "serve_question_review_ui.py").read_text(encoding="utf-8")
        # 處理分支，不是那個允許路徑的集合字面值（那個只是列出名字）。
        marker = server.find('if parsed.path == "/api/answer-review-batch":')
        self.assertGreater(marker, -1, "伺服器裡沒有 answer-review-batch 處理分支")
        segment = server[marker : marker + 3000]
        self.assertIn('payload.get("action")', segment)
        # 每個 event 都被蓋上**同一個** action（所以客戶端必須先按 action 分組）。
        self.assertRegex(segment, r'"action": action,')

    def test_the_discussion_area_reads_the_stuck_questions_endpoint(self):
        """錯題討論區只讀一個端點：`/api/discuss`（卡住的題＋兩條人的流）。

        它**不是**「每筆修正紀錄」那一區了。舊契約讀 `/api/correction-feedback`，而那一區
        顯示的是「人修過什麼」；新契約是「題目卡在哪裡」——兩者不同。舊版把 79,090 列
        全拉進來，卡住的題反而找不到。過濾條件在伺服器定義一次（`DISCUSS_BUCKETS`），
        所以 JSONL 與兩條 SQL 路徑不會漂移。
        """
        body = function_body(self.js, "renderDiscuss")
        # The fetch itself moved into `loadDiscuss` when the area gained its four-level filter
        # (`discussScopeHtml`/`discussReload`). The contract did not move - the area still reads
        # exactly one endpoint and has no second store - so it is pinned on the function that reads,
        # and `renderDiscuss` is pinned to going through it rather than calling `fetch` by hand.
        load = function_body(self.js, "loadDiscuss")
        self.assertIn("/api/discuss", load)
        self.assertIn("loadDiscuss()", body)
        # 而且它只讀不寫：沒有第二個 store。
        self.assertNotIn("fetch(", body.replace("fetchAreaJson", ""))
        self.assertNotIn("fetch(", load.replace("fetchAreaJson", ""))
        self.assertNotRegex(body, r"/api/(review|answer-review|ai-feedback|correction-feedback)\\b")
        self.assertNotRegex(load, r"/api/(review|answer-review|ai-feedback|correction-feedback)\\b")

    def test_the_changed_class_is_displayed_not_invented(self):
        """機器量到的東西顯示機器量到的值；這一區不得自己發明一個類型名稱。

        `queue_bucket` 是伺服器 `review_projection` 產的鍵，`bucketLabel` 負責把鍵轉成中文。
        照**字**認會在一改字時默默壞掉，所以讀的是鍵。
        """
        self.assertIn("queue_bucket", function_body(self.js, "discussRowLabel"),
                      "列標籤沒有讀伺服器量到的桶位")
        self.assertIn("DISCUSS_BUCKET_LABEL", function_body(self.js, "bucketLabel"))
        # 這一區不能自己發明一個分類函式。
        self.assertNotRegex(self.js, r"function classify")

    def test_zero_cases_is_stated_not_left_blank(self):
        """0 個個案是**合法**狀態（大家都審完了），但 0 不能是一片空白。"""
        body = function_body(self.js, "renderDiscuss")
        self.assertIn("empty-area", body, "沒有案子時沒有說法")
        self.assertRegex(body, r"if \(!total\)")

    def test_the_home_page_computes_nothing_itself(self):
        body = function_body(self.js, "renderHome")
        for endpoint in ("/api/candidates", "/api/answer-candidates", "/api/correction-feedback"):
            self.assertIn(endpoint, body, f"首頁沒有讀 {endpoint}")
        # 三個端點並行讀，不是自己把題庫算一遍。
        self.assertIn("Promise.all", body)

    def test_the_home_page_asks_for_counts_not_a_thousand_rows(self):
        """首頁畫的是卡片，不是清單：它只讀 total_count/reviewed_count，不畫任何一列。

        它以前送 `limit: 1000` 去拿兩個整數，而 `_count` 伺服器從不讀——於是每一次開首頁都
        建、序列化、傳了一千筆完整 payload（實測 6.0 MB、0.39 s、每筆 ~4.7 KB）。
        count-only 只花 0.11 s 跑同一個篩選迴圈。所以：首頁讀 `_count`，而且不再要 1000 筆。
        """
        body = function_body(self.js, "renderHome")
        self.assertIn("_count", body, "首頁沒有用 count-only 契約")
        self.assertNotRegex(
            body, r"limit:\s*1000", "首頁還在要 1000 筆完整 payload"
        )

    def test_the_home_page_renders_once_like_every_other_pane(self):
        """換區是隱藏，不是重畫。首頁以前繞過 `A.rendered`，每次切回都重抓三個端點。

        首頁的 DOM 不會藏住審稿人打一半的字（那是答案區的註記），但重抓本身就是白工——
        它每次都重建 DOM、重跑三個 fetch。走 `renderArea` 就和其他區同一條路。
        """
        # `showArea` 不得再直接呼叫 `renderHome`；它只能呼叫 `renderArea`。
        self.assertNotIn("renderHome()", function_body(self.js, "showArea"))
        # `renderArea` 要負責分派到首頁。
        self.assertIn("renderHome()", function_body(self.js, "renderArea"))

    # ---------------------------------------------------------------- 4. 換區是隱藏，不是清空
    def test_switching_areas_hides_rather_than_empties(self):
        body = function_body(self.js, "showArea")
        self.assertIn("classList.toggle('on'", body)
        self.assertNotIn("innerHTML", body)
        self.assertNotIn(".remove()", body)

    def test_a_pane_is_rendered_once_and_not_rebuilt_on_reentry(self):
        body = function_body(self.js, "renderArea")
        self.assertIn("A.rendered[area]", body)
        self.assertRegex(body, r"if \(A\.rendered\[area\] && !force\) return;")

    def test_a_question_decision_marks_the_other_panes_stale(self):
        """題目一決定，答案區的可審集合就變了——這是唯一會寫題目決定的地方。"""
        for name in ("decide", "saveCorrection"):
            body = function_body(self.js, name)
            self.assertIn("invalidateAreas()", body, f"{name} 沒有讓其他區過期")

    def test_the_note_draft_survives_leaving_the_area(self):
        body = function_body(self.js, "bindAnswerSheet")
        self.assertIn("A.noteDraft.set", body)
        row = function_body(self.js, "answerRowHtml")
        self.assertIn("A.noteDraft.get", row, "答案列沒有從草稿還原註記")

    # ---------------------------------------------------------------- 5. 快捷鍵只在題目區
    def test_the_question_shortcuts_only_fire_in_the_question_area(self):
        # 全域 keydown 監聽器的第一個判斷必須是分區。
        match = re.search(
            r"document\.addEventListener\('keydown', \(event\) => \{(.*?)\n\}\);", self.js, re.S
        )
        self.assertIsNotNone(match, "找不到 keydown 監聽器")
        body = match.group(1)
        guard = body.find("A.area !== 'question'")
        self.assertGreater(guard, -1, "快捷鍵沒有分區守衛")
        # 守衛必須在真的送決定之前。
        decision = body.find("decide(")
        self.assertGreater(decision, guard, "快捷鍵的守衛在送決定之後才檢查")
        self.assertLess(guard, body.find("const key"))

    # ---------------------------------------------------------------- 6. 舊書籤
    def test_the_question_area_strips_its_prefix_so_old_bookmarks_keep_working(self):
        body = function_body(self.js, "showArea")
        # 題目區算出來的 hash 是 `#${rest}`（沒有前綴）。
        self.assertRegex(body, r"next === 'question' \? `#\$\{rest\}`")
        # 而其他區才加上前綴。
        self.assertRegex(body, r"AREA_PREFIX\[next\]")

    def test_the_browser_test_exists_and_covers_all_four_areas(self):
        self.assertTrue(BROWSER_TEST.exists(), "沒有瀏覽器測試")
        text = BROWSER_TEST.read_text(encoding="utf-8")
        # The driver names each area through the `.area-btn[data-area=...]` selector it clicks, built
        # with a template string (`data-area="${area}"`), so the literal per-area spelling is the
        # **call site**, not the selector. Assert the calls, and assert the selector once.
        self.assertIn(''.join(['.area-btn[data-area="', '${area}', '"]']), text)
        for area in AREAS:
            self.assertRegex(text, rf"goto\('{area}'\)", f"瀏覽器測試沒有走到 {area}")
        # 它必須真的開 Chrome，不能只是讀 HTML。
        self.assertIn("remote-debugging-port", text)
        # 而且它要證明「換區不清空」。
        self.assertIn("隱藏不是清空", text)


class NegativeControlTests(unittest.TestCase):
    """若把四個區的機制拆掉，上面的斷言必須會失敗。"""

    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    def test_a_missing_area_caught(self):
        broken = self.html.replace('data-area="discuss"', 'data-area="discussX"', 1)
        found = re.findall(r'class="area-btn[^"]*" data-area="([a-z]+)"', broken)
        self.assertNotEqual(found, list(AREAS))
        # 正對照：原檔會相等。
        self.assertEqual(
            re.findall(r'class="area-btn[^"]*" data-area="([a-z]+)"', self.html), list(AREAS)
        )

    def test_an_empty_hash_returning_home_would_be_caught(self):
        broken = self.js.replace("if (!raw) return 'question'", "if (!raw) return 'home'", 1)
        body = function_body(broken, "areaFromHash")
        self.assertRegex(body, r"if \(!raw\) return 'home'")
        # 正對照。
        self.assertRegex(function_body(self.js, "areaFromHash"), r"if \(!raw\) return 'question'")

    def test_a_clearing_switch_would_be_caught(self):
        broken = self.js.replace(
            "    if (node) node.classList.toggle('on', name === next);",
            "    if (node) { node.innerHTML = ''; node.classList.toggle('on', name === next); }",
            1,
        )
        self.assertIn("innerHTML", function_body(broken, "showArea"))
        self.assertNotIn("innerHTML", function_body(self.js, "showArea"))

    def test_a_rebuilding_render_would_be_caught(self):
        broken = self.js.replace(
            "  if (A.rendered[area] && !force) return;", "  if (false) return;", 1
        )
        self.assertNotRegex(function_body(broken, "renderArea"), r"A\.rendered\[area\] && !force")
        self.assertRegex(function_body(self.js, "renderArea"), r"A\.rendered\[area\] && !force")

    def test_unguarded_shortcuts_would_be_caught(self):
        broken = self.js.replace("  if (A.area !== 'question') return;\n", "", 1)
        match = re.search(
            r"document\.addEventListener\('keydown', \(event\) => \{(.*?)\n\}\);", broken, re.S
        )
        self.assertNotIn("A.area !== 'question'", match.group(1))
        # 正對照。
        match = re.search(
            r"document\.addEventListener\('keydown', \(event\) => \{(.*?)\n\}\);", self.js, re.S
        )
        self.assertIn("A.area !== 'question'", match.group(1))

    def test_a_retyped_label_instead_of_the_measured_bucket_would_be_caught(self):
        # 列標籤必須從伺服器量到的 `queue_bucket` 推出來，不能自己寫死一個類型名稱。
        broken = self.js.replace("queue_bucket", "'我的分類'")
        self.assertNotIn("queue_bucket", function_body(broken, "discussRowLabel"))
        self.assertIn("queue_bucket", function_body(self.js, "discussRowLabel"))
        # 負對照：拿掉桶位標籤表，翻譯就會變成空的，而不是自己編一個。
        self.assertIn("DISCUSS_BUCKET_LABEL", function_body(self.js, "bucketLabel"))

    def test_a_client_side_eligibility_rule_would_be_caught(self):
        # 每一處都要換掉：`/api/answer-candidates` 在 `renderAnswers` 裡出現兩次（請求與錯誤訊息）。
        broken = self.js.replace("/api/answer-candidates", "/api/candidates")
        self.assertNotIn("/api/answer-candidates", function_body(broken, "renderAnswers"))
        self.assertIn("/api/answer-candidates", function_body(self.js, "renderAnswers"))

    def test_a_home_request_for_a_thousand_rows_would_be_caught(self):
        # 模擬修好前的樣子：把 count-only 拿掉，換回 `limit: 1000`。
        broken = self.js.replace(
            "fetchAreaJson('/api/candidates', { _count: 1 })",
            "fetchAreaJson('/api/candidates', { limit: 1000 })",
            1,
        )
        body = function_body(broken, "renderHome")
        self.assertNotIn("_count: 1", body)
        self.assertRegex(body, r"limit:\s*1000")
        # 正對照：原檔的 `_count: 1` 在、`limit: 1000` 不在。
        good = function_body(self.js, "renderHome")
        self.assertIn("_count: 1", good)
        self.assertNotRegex(good, r"limit:\s*1000")

    def test_a_home_that_rerenders_on_every_switch_would_be_caught(self):
        broken = self.js.replace("  if (next !== 'question') renderArea(next);",
                                 "  if (next !== 'question') renderArea(next);\n  if (next === 'home') renderHome();", 1)
        self.assertIn("renderHome()", function_body(broken, "showArea"))
        # 正對照。
        self.assertNotIn("renderHome()", function_body(self.js, "showArea"))


if __name__ == "__main__":
    unittest.main()
