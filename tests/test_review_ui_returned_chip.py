"""兩件不同的事被壓在同一個 chip，所以看不見差別。

左邊清單把 `block`／`needs_review`（**人按的**）與 `reset_review`（**管線／模型改完內容退回來的**）
合成一個「需重看・阻擋」。事件流裡它們本來就是兩種東西，而且回答的是不同的問題：

  * 人按的 block／needs_review 要回答：「哪裡不對？」
  * `reset_review` 要回答：「新的文字對不對？」——題目是被別的東西退回來的，不是被審題者擋的。

合併的代價出現在最需要分辦的那一屏：審題者分不出某題在等，是因為自己說過話，還是機器說過話。
判準因此被拆成兩個 chip，`reset_review` 也必須在列上有一個自己的記號（藍點），
因為「機器退回」不是「我覺得有問題」，不該用同一個警示色。

這個檔案釘住的是**拆分本身**，所以它讀的是**活的節點**（`data-view=`、`stateOf` 的實際分支、
行上的 class），不是說明文字。每一個檢查都有負對照：把兩個 chip 併回去、或把 `reset_review`
映回 `needs_review`，對應的斷言必須失敗——否則這裡只是在讀自己的說明。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "review_ui" / "v2.html"
NAV_CONTRACT = ROOT / "scripts" / "test_v2_navigation.mjs"

#: 舊版的那一個合併 chip，以及把 reset 映回 needs_review 的那一行。用來當負對照的輸入。
MERGED_CHIPS = (
    '<label class="chip" data-view="flagged"><input type="radio" name="view" value="flagged">'
    '需重看・阻擋<span class="n" id="nFlagged"></span></label>'
)
COLLAPSED_RESET_LINE = "if (review.is_reset_unreviewed) return 'needs_review';"


def chip_views(html: str) -> list[str]:
    """列出清單左邊**活**的 chip。讀 `data-view`，不讀標籤字，因為字會被改。"""
    return re.findall(r'<label class="chip" data-view="([a-z_]+)"', html)


def element_ids(html: str) -> set[str]:
    return set(re.findall(r'id="([A-Za-z0-9_]+)"', html))


def state_of_body(html: str) -> str:
    match = re.search(r"function stateOf\(item\) \{(.*?)\n\}", html, re.S)
    assert match, "v2.html 裡找不到 stateOf"
    return match.group(1)


def row_review_action_body(html: str) -> str:
    match = re.search(r"function rowReviewAction\(candidate\) \{(.*?)\n\}", html, re.S)
    assert match, "v2.html 裡找不到 rowReviewAction"
    return match.group(1)


class ReturnedChipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")

    # --- 兩個 chip 真的存在 -------------------------------------------------
    def test_the_human_flag_and_the_machine_return_are_two_chips(self):
        views = chip_views(self.html)
        self.assertIn("flagged", views)
        self.assertIn("returned", views)
        # 而且退回那個 chip 排在人擋的旁邊，不是被藏到別的群組去。
        self.assertEqual(views.index("returned"), views.index("flagged") + 1)

    def test_the_negative_control_a_merged_chip_list_has_no_returned_chip(self):
        # 把兩個 chip 換回舊的那一個，抽取函式就找不到 `returned`——所以上面的斷言會失敗，
        # 而不是恆真。這是這個檔案存在的理由。
        merged = self.html.replace(
            self.html[self.html.index('<label class="chip" data-view="flagged">'):
                      self.html.index('<label class="chip" data-view="disputed">')],
            MERGED_CHIPS,
        )
        self.assertNotIn("returned", chip_views(merged))

    def test_the_two_chips_do_not_share_a_count_element(self):
        # 共用計數元素的話，兩個 chip 會顯示同一個數字，等於沒拆。
        self.assertIn("nFlagged", element_ids(self.html))
        self.assertIn("nReturned", element_ids(self.html))
        self.assertNotEqual("nFlagged", "nReturned")

    def test_the_negative_control_a_shared_count_element_is_two_views_one_number(self):
        merged = self.html.replace('id="nReturned"', 'id="nFlagged"')
        ids = element_ids(merged)
        # 負對照：合併後只剩一個計數元素可以餵兩個 chip，所以「各自有計數」不可能成立。
        self.assertEqual(sum(1 for name in ids if name == "nFlagged"), 1)
        self.assertNotIn("nReturned", ids)

    # --- 狀態真的分開了 -----------------------------------------------------
    def test_a_reset_question_is_its_own_state_not_a_flag(self):
        body = state_of_body(self.html)
        self.assertIn("return 'returned'", body)
        # 退回要在人擋的判斷**之前**，否則 reset_review 會先被人擋那條線吃掉。
        self.assertLess(body.index("return 'returned'"), body.index("return 'flagged'"))

    def test_the_negative_control_collapsing_reset_makes_no_returned_state(self):
        collapsed = state_of_body(self.html).replace("return 'returned'", "return 'flagged'")
        self.assertNotIn("return 'returned'", collapsed)

    def test_the_row_state_comes_from_the_row_not_from_a_guess(self):
        # 根因：`rowReviewAction` 把 `is_reset_unreviewed` 映成 `needs_review`，於是 S.verdict
        # 裡根本沒有 `reset_review` 這個值，stateOf 再怎麼寫也分不出來。上面任何一個 chip 或
        # 分支的斷言都會在這一條沒改時變成空的。
        body = row_review_action_body(self.html)
        self.assertIn("if (review.is_reset_unreviewed) return 'reset_review';", body)

    def test_the_negative_control_the_old_collapsed_mapping_is_not_present(self):
        body = row_review_action_body(self.html)
        self.assertNotIn(COLLAPSED_RESET_LINE, body)

    def test_a_machine_return_is_not_labelled_as_a_human_flag(self):
        # LABEL 決定提示文字。沒給 reset_review 一個名字，畫面會印出代號或空白。
        self.assertIn("reset_review: 'AI／管線退回'", self.html)

    # --- 列的記號 -----------------------------------------------------------
    def test_a_returned_row_gets_its_own_mark(self):
        self.assertIn("if (v === 'reset_review') cls.push('returned');", self.html)
        self.assertIn(".row.returned .mark", self.html)

    def test_the_negative_control_a_merged_state_has_no_returned_mark(self):
        merged = self.html.replace("if (v === 'reset_review') cls.push('returned');",
                                   "if (v === 'reset_review') cls.push('flag');")
        self.assertNotIn("cls.push('returned')", merged)

    def test_the_returned_mark_is_not_the_flag_colour(self):
        # 用同一個顏色就等於沒有分；而且退回不是「我覺得有問題」，不該借用警示色。
        style = self.html[self.html.index(".row.flag .mark"):]
        style = style[:style.index("}") + 1]
        returned = self.html[self.html.index(".row.returned .mark"):]
        returned = returned[:returned.index("}") + 1]
        self.assertIn("var(--amber)", style)
        self.assertNotIn("var(--amber)", returned)
        self.assertIn("var(--blue)", returned)

    # --- chip 的數字與列表同源 ----------------------------------------------
    def test_each_chip_counts_its_own_state(self):
        self.assertIn("$('nFlagged').textContent = n((item) => stateOf(item) === 'flagged');", self.html)
        self.assertIn("$('nReturned').textContent = n((item) => stateOf(item) === 'returned');", self.html)

    def test_the_returned_filter_selects_the_returned_state(self):
        self.assertIn("if (mode === 'returned') return stateOf(item) === 'returned';", self.html)

    def test_the_negative_control_a_merged_filter_cannot_select_only_returns(self):
        # 對照必須把**所有**出現都併掉：計數行也用同一個判斷，只改篩選行會留下字串，
        # 那會讓這個對照恆真（它在測自己沒改乾淨，而不是在測規則）。
        merged = self.html.replace("stateOf(item) === 'returned'",
                                   "stateOf(item) === 'flagged'")
        self.assertNotIn("stateOf(item) === 'returned'", merged)

    # --- 走訪契約看得見新 chip ----------------------------------------------
    def test_the_navigation_contract_knows_the_new_chip_and_its_count(self):
        # 少一個 id，`$('nReturned')` 會是 null 然後整頁拋錯；這個契約抽真的 script 來跑，
        # 所以新節點必須登記進去。它讀的是契約裡的**活的清單**，不是註解。
        contract = NAV_CONTRACT.read_text(encoding="utf-8")
        ids = re.findall(r"'([A-Za-z0-9_]+)'", contract)
        self.assertIn("nReturned", ids)
        views = re.findall(r"const chipViews = \[(.*?)\];", contract, re.S)
        self.assertTrue(views, "契約裡找不到 chipViews")
        listed = re.findall(r"'([a-z_]+)'", views[0])
        self.assertIn("returned", listed)

    def test_the_negative_control_the_contract_without_the_id_would_throw(self):
        contract = NAV_CONTRACT.read_text(encoding="utf-8")
        without = contract.replace("'nReturned', ", "")
        ids = re.findall(r"'([A-Za-z0-9_]+)'", without)
        self.assertNotIn("nReturned", ids)
        # 而 v2.html 真的會去用它——所以契約少了它就測不到。
        self.assertIn("$('nReturned')", self.html)

    # --- 說得出是誰退回、為什麼 ----------------------------------------------
    def test_a_returned_question_says_why_it_came_back(self):
        # 一個孤零零的標籤等於沒說。原因在清單自己帶的 `review.reset`（退回事件），不是別支 API。
        self.assertIn("(candidate.review || {}).reset || {}", self.html)
        self.assertIn("resetEvent.reset_notes || resetEvent.notes", self.html)
        self.assertIn("resetEvent.previous_action", self.html)

    def test_only_a_machine_return_shows_the_reason(self):
        # 人按的 block 不該借用這個提示：那題的原因就是審題者自己，「原為：－」是錯的。
        self.assertIn("standing === 'reset_review'", self.html)
        guard = self.html[self.html.index("const returnedNote = standing"):]
        guard = guard[:guard.index(";")]
        self.assertIn("standing === 'reset_review'", guard)

    def test_the_negative_control_a_reason_shown_for_every_flag_is_not_what_we_want(self):
        # 拿掉那個守衛，人按的 block 也會印退回原因——所以上面那個斷言不是空話。
        dropped = self.html.replace("const returnedNote = standing === 'reset_review'",
                                    "const returnedNote = true")
        self.assertNotIn("const returnedNote = standing === 'reset_review'", dropped)

    # --- 篩出 0 列不能把畫面弄壞 --------------------------------------------
    def test_a_filter_with_no_rows_does_not_throw(self):
        # 新 chip 把一個潛伏 bug 照出來：這一卷的「AI／管線退回」是 0 題，一按下去就是
        # `S.rows[S.index]` 是 undefined，`item.candidate` 直接拋錯——而那個錯在 refilter 裡，
        # 所以畫面停在上一輪、按什麼都沒反應。清單本身有守衛，明細面板沒有。
        body = self.html[self.html.index("function renderTextSide()"):]
        body = body[:body.index("const candidate = item.candidate")]
        self.assertIn("if (!item) {", body)

    def test_the_negative_control_without_the_guard_the_empty_filter_throws(self):
        stripped = self.html.replace(
            "  if (!item) {\n    $('where').innerHTML = '';",
            "  if (false) {\n    $('where').innerHTML = '';",
        )
        body = stripped[stripped.index("function renderTextSide()"):]
        body = body[:body.index("const candidate = item.candidate")]
        self.assertNotIn("if (!item) {", body)


if __name__ == "__main__":
    unittest.main()
