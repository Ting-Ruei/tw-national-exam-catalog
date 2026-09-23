"""錯題討論區：跟題目審核區一致的版面，加上「原題／擷圖／字體／註解」四塊。

使用者回報的四件事，這裡各釘一個可觀察的契約：

  1. **右邊的紙本要跟題目審核區一樣大。** 題目區的左清單是 `238px`，其餘分成兩個一半
     （`.compare` 是 `1fr 1fr`）。討論區原本是 `214px | 1fr | 330px`——紙本只有題目區的四分之一，
     而這一區的工作（拿抽取文字對紙本、缺圖就裁）比題目區**更需要**那張紙。所以版面必須是同樣的
     算術：`238px | 1fr | 1fr`。這一條用真的瀏覽器量出來（見 `test_v2_ui_audit.mjs`），
     這裡釘的是 CSS 的三欄定義與「兩邊同一個算式」。
  2. **要能看到原題。** 抽出的文字要以 `richText()` 畫成原樣（上下標是上下標），而且**不是**編輯框——
     編輯框裡是「我準備改成什麼」，原題是「紙本上是什麼」，兩者混在一起就分不出來了。
  3. **要有圖片擷圖與抽換。** 貼上／拖入／選檔三條路都要在，位置要能選題幹／A–D／表格／題組共用，
     寫入要走既有 `/api/manual-asset`（含 `replace_existing`）——不是新發明的寫入路徑。
  4. **要能調整字體。** 抽取文字的字級與 PDF 的縮放是兩件事（PDF viewer 有自己的 52%），
     所以字級必須自己可調。大小存在**一個** CSS 變數裡，不是八個各自的 font-size。
  5. **基本原則上面要有註解區。** 順序是「先說這題（註解），再說通則（原則）」；寫的是 `comment`
     事件，與題目區的「只加註記」同一種，所以不會把已有決定蓋掉。

**負對照**：每一條斷言旁邊都有一個「改回舊樣子就必須失敗」的版本。沒有負對照的檢查，
只是在讀自己的註解。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from test_review_ui_areas import V2, function_body, script_of


class DiscussLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    # ------------------------------------------------- 1. 版面與題目區同一個算式
    def test_the_paper_pane_is_the_same_size_as_the_question_area(self):
        """討論區的三欄＝題目區的算式：`238px | 1fr | 1fr`。

        題目區：`.app` 是 `238px minmax(0,1fr)`，`.compare` 是 `1fr 1fr`。
        討論區原本把紙本壓成 `330px`，在 1680 的視窗上等於題目區給同一份文件的大約四分之一。
        """
        match = re.search(r"\.discuss\s*\{\s*grid-template-columns:([^;]+);", self.html)
        self.assertIsNotNone(match, "找不到 .discuss 的欄定義")
        columns = match.group(1)
        self.assertIn("238px", columns, "左清單的寬度要與題目區一致")
        self.assertEqual(2, columns.count("minmax(0,1fr)"),
                         f"右邊要是兩個等寬的一半（與 .compare 同一個算式），現在是 {columns!r}")
        # 舊的 330px 紙本欄不可以還在。
        self.assertNotIn("330px", columns)

    def test_a_narrow_paper_column_would_be_caught(self):
        # 負對照：改回舊的 `214px | 1fr | 330px`，上面那條必須失敗。
        broken = self.html.replace(
            ".discuss { grid-template-columns:238px minmax(0,1fr) minmax(0,1fr); }",
            ".discuss { grid-template-columns:214px minmax(0,1fr) 330px; }", 1)
        self.assertNotEqual(broken, self.html, "負對照必須真的改到東西")
        match = re.search(r"\.discuss\s*\{\s*grid-template-columns:([^;]+);", broken)
        self.assertNotIn("238px", match.group(1))
        self.assertIn("330px", match.group(1))

    def test_the_paper_frame_fills_the_column_instead_of_a_fixed_height(self):
        """紙本框要**填滿那一欄**，不是一個固定高度的小框。

        舊的 `.discuss-frame` 是 `min-height:440px`、塞在一個 `overflow-y:auto` 的窄欄裡，
        所以在高視窗上它不會變高——右邊永遠只有 440px 的紙，而題目區的紙跟視窗一樣高。
        """
        frame = re.search(r"\.discuss-frame\s*\{([^}]*)\}", self.html)
        self.assertIsNotNone(frame, "找不到 .discuss-frame")
        body = frame.group(1)
        self.assertIn("flex:1", body, "紙本框要在欄內長高")
        self.assertNotIn("440px", body)
        pane = re.search(r"\.discuss-pdf\s*\{([^}]*)\}", self.html)
        self.assertIsNotNone(pane)
        self.assertIn("flex-direction:column", pane.group(1))

    def test_a_fixed_height_paper_would_be_caught(self):
        broken = self.html.replace(
            ".discuss-frame { flex:1; width:100%; min-height:0; border:0; display:block; background:#eef2f6; }",
            ".discuss-frame { flex:1 1 auto; min-height:440px; width:100%; border:1px solid var(--line); }", 1)
        self.assertNotEqual(broken, self.html, "負對照必須真的改到東西")
        frame = re.search(r"\.discuss-frame\s*\{([^}]*)\}", broken)
        self.assertIn("440px", frame.group(1))


class DiscussOriginalQuestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    def test_the_original_question_is_drawn_with_the_paper_markup(self):
        """③ 原題要畫出紙本的樣子：走 `richText()`（所以 `<sub>` 真的是下標）。

        這一條是使用者回報的第一件事（「上下標顯示成 `<sup></sup>`」）在討論區的版本。
        """
        body = function_body(self.js, "discussOriginalHtml")
        self.assertIn("richText(", body, "原題沒有走共用的 richText()")
        self.assertIn("candidate.stem", body)
        # 每一個選項也要走同一條路。
        self.assertRegex(body, r"richText\(option\.text\)")

    def test_the_original_is_not_an_editor(self):
        """原題是**讀**的，不是改的：它裡面不可以有 textarea。

        使用者的回報是「可以看到原題」。把編輯框當原題讀，就分不出「紙本這樣印」與
        「我把它改成這樣」——而這兩件事正是這一區要累積的知識。
        """
        body = function_body(self.js, "discussOriginalHtml")
        self.assertNotIn("<textarea", body)
        # 編輯框是另一個函式，兩者都必須在。
        self.assertIn("<textarea", function_body(self.js, "discussStem"))
        self.assertIn("<textarea", function_body(self.js, "discussOptions"))

    def test_an_original_that_escapes_instead_of_rendering_would_be_caught(self):
        # 負對照：把 richText 換回 esc（＝文字被逃脫，上下標變成字面上的標籤）。
        broken = self.js.replace("+ `<div class=\"stem\">${richText(candidate.stem",
                                 "+ `<div class=\"stem\">${esc(candidate.stem", 1)
        self.assertNotEqual(broken, self.js, "負對照必須真的改到東西")
        body = function_body(broken, "discussOriginalHtml")
        self.assertNotIn("richText(candidate.stem", body)


class DiscussCropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    def test_the_crop_region_offers_paste_drag_and_file(self):
        """擷圖的輸入要有三條路：貼上（主要用法）、拖入、選檔。"""
        body = function_body(self.js, "discussCropHtml")
        self.assertIn("dCropDrop", body)
        self.assertIn("dCropFile", body)
        self.assertIn('accept="image/*"', body)
        bind = function_body(self.js, "bindDiscussCrop")
        self.assertIn("ondrop", bind)
        self.assertIn("ondragover", bind)
        self.assertIn("onchange", bind)
        # 貼上掛在 document 上（PDF iframe 拿不到鍵盤），而且只認這一區。
        self.assertIn("addEventListener('paste'", self.js)
        paste = self.js[self.js.index("addEventListener('paste'"):]
        self.assertIn("A.area !== 'discuss'", paste[:600],
                      "貼上事件必須只認討論區，否則在別區貼圖會變成補圖")

    def test_every_placement_the_server_accepts_is_offered(self):
        """位置選項要對得上伺服器收的 `placement`：stem／option／table／group。

        少一個就是「機器裁不到、介面也補不了」的那一種缺陷。選項要逐個點名（A–D），
        因為伺服器要的是 `placement=option` 加上 `target_option`。
        """
        body = function_body(self.js, "discussCropHtml")
        for place in ("'stem'", "'table'", "'group'", "'A'", "'B'", "'C'", "'D'"):
            self.assertIn(place, body, f"擷圖位置少了 {place}")

    def test_the_crop_writes_through_the_existing_endpoint(self):
        """寫入走既有的 `/api/manual-asset`，參數名與伺服器同名。

        不新增寫入路徑：`save_manual_image_asset` 已經會把圖存檔、把 `asset_ref` 嵌進 correction，
        而 correction 是 append-only 的事件。
        """
        body = function_body(self.js, "saveDiscussCrop")
        self.assertIn("/api/manual-asset", body)
        for field in ("placement", "target_option", "replace_existing", "caption", "notes", "data_url"):
            self.assertIn(field, body, f"送出的 payload 少了 {field}")

    def test_a_crop_that_ignores_target_option_would_be_caught(self):
        # 負對照：拿掉 target_option，上面的欄位檢查必須失敗。
        broken = self.js.replace("    target_option: D.cropOption,\n", "", 1)
        self.assertNotEqual(broken, self.js, "負對照必須真的改到東西")
        body = function_body(broken, "saveDiscussCrop")
        self.assertNotIn("target_option", body)

    def test_saving_an_empty_crop_is_refused(self):
        """空手按「儲存補圖」要擋下來，不可以送出一筆空的補圖事件。"""
        body = function_body(self.js, "saveDiscussCrop")
        self.assertIn("pendingCrop", body)
        self.assertRegex(body, r"if \(!D\.pendingCrop\)")


class DiscussFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    def test_the_reading_size_is_one_variable(self):
        """字級是**一個** CSS 變數，不是分散在各處的 font-size。

        兩個地方各自調字級，就是兩個可以不一致的地方。
        """
        self.assertIn("--reading-size", self.html)
        case = re.search(r"\.case \.orig,.*?\{[^}]*\}", self.html, re.S)
        self.assertIsNotNone(case, "找不到吃 --reading-size 的規則")
        self.assertIn("var(--reading-size", case.group(0))

    def test_the_font_control_changes_the_variable(self):
        body = function_body(self.js, "setDiscussFont")
        self.assertIn("--reading-size", body)
        self.assertIn("localStorage", body, "字級是人的偏好，重載後要還在")
        # 有夾限，不是無限放大到把版面拉壞。
        self.assertRegex(body, r"Math\.min\(|Math\.max\(")

    def test_the_font_control_does_not_redraw_the_question(self):
        """改字級不可以重畫整個題目——那會丟掉正在打的草稿與游標位置。"""
        body = function_body(self.js, "setDiscussFont")
        self.assertNotIn("renderDiscuss(", body)
        self.assertNotIn("innerHTML", body)

    def test_a_font_control_that_redraws_would_be_caught(self):
        broken = self.js.replace("  const pane = $('discussCase');\n",
                                 "  renderDiscuss();\n  const pane = $('discussCase');\n", 1)
        self.assertNotEqual(broken, self.js, "負對照必須真的改到東西")
        self.assertIn("renderDiscuss(", function_body(broken, "setDiscussFont"))


class DiscussNoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    def test_the_note_sits_above_the_principles(self):
        """註解要在「基本原則」**上面**：先說這題，再說從它歸納出來的通則。"""
        body = function_body(self.js, "discussCenterHtml")
        note_at = body.index("discussNoteHtml(")
        principles_at = body.index("discussPrinciplesHtml(")
        self.assertLess(note_at, principles_at,
                        "註解必須畫在基本原則之前（使用者的順序要求）")

    def test_the_note_is_a_comment_not_a_verdict(self):
        """存的是 `comment` 事件——與題目區的「只加註記」同一種。

        這一條很重要：它保證在討論區寫字不會把已通過的題目算成待審。
        伺服器的 `_reaffirm_standing_action` 會重申底下那個決定。
        """
        body = function_body(self.js, "saveDiscussNote")
        self.assertIn("/api/review", body)
        self.assertRegex(body, r"action:\s*'comment'")
        # 不可以送任何判決型的 action。
        for verdict in ("'accept'", "'block'", "'needs_review'"):
            self.assertNotIn(verdict, body, f"註解不可以送判決 {verdict}")

    def test_an_empty_note_is_refused(self):
        body = function_body(self.js, "saveDiscussNote")
        self.assertRegex(body, r"if \(!notes\)")

    def test_a_note_that_writes_a_verdict_would_be_caught(self):
        broken = self.js.replace("candidate_key: key, action: 'comment', notes, reviewer: 'local',",
                                 "candidate_key: key, action: 'accept', notes, reviewer: 'local',", 1)
        self.assertNotEqual(broken, self.js, "負對照必須真的改到東西")
        body = function_body(broken, "saveDiscussNote")
        self.assertIn("'accept'", body)

    def test_the_recorded_note_is_read_from_the_rows_own_field(self):
        """已存的註解讀 `review.notes`——與題目區讀的是同一個欄位。

        若討論區自己另存一份，兩區就會對「上一個人說了什麼」有不同意見。
        """
        body = function_body(self.js, "discussNoteHtml")
        self.assertIn("review", body)
        self.assertIn("notes", body)
        # 不可以自己發明一個 comments 欄位（伺服器沒有這個投影）。
        self.assertNotIn("comments", body)


class DiscussButtonWiringTests(unittest.TestCase):
    """**每一個按鈕都要接上。** 這是使用者最在意的一條。

    前面幾個缺陷的形狀都一樣：控制項畫出來了、看起來可以按，但沒有 handler。
    截圖完全看不出來，所以這一類要用「DOM 上的控制項集合」對「被綁定的 id 集合」來釘。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    def test_every_control_the_pane_draws_is_bound(self):
        """討論區畫出來的每一個 id，都要在某個 `bind*`／儲存函式裡被用到。

        讀值型的框（文字／檔案／核取）不需要 handler——它們的值由儲存函式讀取，
        所以「被提到」就算接上了。
        """
        html_body = "\n".join(
            function_body(self.js, name) for name in
            ("discussCenterHtml", "discussCropHtml", "discussNoteHtml", "discussOriginalHtml",
             "discussFontHtml", "discussPrinciplesHtml", "discussQuestionsHtml"))
        drawn = set(re.findall(r'id="(d[A-Za-z]+)"', html_body))
        self.assertGreater(len(drawn), 10, f"抓到的 id 太少（{sorted(drawn)}），探針可能壞了")
        # 所有會被「用」的地方：綁定與儲存函式。
        used = "\n".join(
            function_body(self.js, name) for name in
            ("bindDiscuss", "bindDiscussCrop", "saveDiscussCrop", "saveDiscussNote",
             "saveDiscuss", "resetDiscuss", "focusDiscuss", "setDiscussFont", "readDiscussCrop",
             "restoreDiscussCropPreview"))
        # 這幾個是**容器**，不是控制項：它們只把子元素分組（`discussCase` 是吃字級變數的範圍，
        # `discussSide`／`dCropPlace` 是排版用的包裝）。它們本身不該被按、也不該被讀值。
        containers = {"discussCase", "discussSide", "dCropPlace"}
        unbound = sorted(i for i in drawn if i not in used and i not in containers)
        self.assertEqual([], unbound,
                         f"這些控制項畫出來了但沒有任何地方用它：{unbound}")

    def test_the_audit_finds_an_unbound_button(self):
        # 負對照：把「儲存補圖」的綁定拿掉。上面的檢查**不會**抓到它（它出現在
        # html 字串裡也算「被提到」），所以另外用 handler 檢查——這正是
        # `scripts/test_v2_ui_audit.mjs` 存在的理由：只有真的按下去才測得出來。
        # 這裡用文字釘住那支稽核腳本真的在驗 handler。
        audit = (Path(__file__).resolve().parents[1] / "scripts" / "test_v2_ui_audit.mjs")
        source = audit.read_text(encoding="utf-8")
        self.assertIn("handlers.length === 0", source)
        self.assertIn("typeof n.onclick === 'function'", source)
        self.assertIn("每一個可見動作控制項", source)


if __name__ == "__main__":
    unittest.main()
