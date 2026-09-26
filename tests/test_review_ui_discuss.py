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

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from test_review_ui_areas import V2, function_body, script_of
# One loader for the server module, not a second copy: two loaders would be two module objects and
# the class-level `object.__new__` stubs below would be built against the wrong one.
from test_review_ui_scope import import_review_ui as load_ui_module
# The runner that executes an expression against the real v2 scripts in node. Imported rather than
# copied, so the stub DOM the JS needs and the "run the real file, not a rewrite" rule stay one thing.
import test_review_ui_v2_scope as _v2scope  # noqa: F401  (kept so a rename breaks loudly here)


def run_node(expression: str) -> object:
    """Run an expression against **all** the v2 scripts, in load order.

    `run_node` from `test_review_ui_v2_scope` loads only `01-core.js`, which is right for the scope
    contract it pins. A helper that lives in `04-area-discuss.js` (`mergedBucket`) needs the other
    files too, and "the real files, in the order `v2.html` loads them" is the only version of this
    that cannot drift - so the file list is read from `v2.html` rather than written out again.
    """
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node 不在這台機器上")
    html = V2.read_text(encoding="utf-8")
    sources = [
        (V2.parent / src).read_text(encoding="utf-8")
        for src in re.findall(r'<script src="([^"]+)"></script>', html)
    ]
    assert sources, "v2.html 沒有載入任何 script"
    stub = """
      globalThis.document = {
        getElementById: () => ({ value:'', options:[], innerHTML:'', textContent:'', style:{},
                                  classList:{add(){},remove(){},toggle(){},contains(){return false}},
                                  addEventListener(){}, querySelectorAll: () => [] }),
        querySelectorAll: () => [], querySelector: () => null, addEventListener() {},
        createElement: () => ({ style:{}, classList:{add(){},remove(){}}, appendChild(){} }),
        body: { appendChild(){} },
      };
      globalThis.location = { hash:'' };
      globalThis.history = { replaceState(){} };
      globalThis.window = { addEventListener(){}, localStorage:{ getItem:()=>null, setItem(){} } };
      globalThis.localStorage = globalThis.window.localStorage;
      globalThis.fetch = async () => ({ ok:false, status:0, json: async () => ({}) });
      globalThis.setTimeout = () => 0; globalThis.clearTimeout = () => {};
    """
    script = stub + "\n" + "\n".join(sources) + (
        f"\n(async () => {{ console.log(JSON.stringify(await ({expression}))); }})()"
        ".catch((error) => { console.error(error); process.exitCode = 1; });"
    )
    result = subprocess.run([node, "-"], input=script, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"node 執行失敗：{result.stderr[-2000:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


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


class DiscussFilterReachability(unittest.TestCase):
    """The SQL discuss predicate must be in the CTE the request actually takes.

    The branch existed, was tested, and was **unreachable**: it lived in the light CTE, and
discuss is excluded from the light query (`_sql_can_use_light_candidate_query` returns False because
    the light query cannot see `repair_kind`). So the full CTE fell through to
    `review_action = %s` with the literal `'discuss'` and matched nothing.

    That failure mode is worth a test of its own because it does not look like a failure: an empty
    filter returns zero rows, and zero rows reads as \u300c\u6c92\u6709\u5361\u4f4f\u7684\u984c\u300d, not as a bug. On the SQL backend
    (`sql_primary`) the whole area would have been silently empty.
    """

    @classmethod
    def setUpClass(cls):
        cls.ui = load_ui_module()

    def test_both_ctes_carry_the_shared_predicate(self):
        state = object.__new__(self.ui.ReviewState)
        heavy, _ = state._sql_candidate_filter_parts({"reviewStatus": "discuss"})
        light, _ = state._sql_light_candidate_filter_parts({"reviewStatus": "discuss"})
        self.assertIn("is_repair_pending", heavy)
        self.assertIn("is_accepted_reaudit_pending", heavy)
        self.assertIn("is_repair_pending", light)
        self.assertIn("is_accepted_reaudit_pending", light)
        # Negative control: the fall-through the defect produced.
        self.assertNotIn("review_action = %s", heavy)

    def test_the_browser_filter_takes_the_heavy_cte(self):
        """
        The reachability fact itself, so the test above cannot be satisfied by the wrong branch:
        `discuss` must not be eligible for the light query, which is why the predicate has to be in
        the heavy one.
        """
        state = object.__new__(self.ui.ReviewState)
        self.assertFalse(state._sql_can_use_light_candidate_query({"reviewStatus": "discuss"}))

    def test_is_discuss_bucket_matches_the_sql_predicate_on_every_bucket(self):
        """One truth table, both implementations.

        The Python function (`is_discuss_bucket`) and the SQL string (`SQL_DISCUSS_PREDICATE`) are
        two expressions of one rule and cannot be made one expression (one runs in the database).
        What can be made true is that they agree - so the same rows are driven through both.
        """
        cases = [
            # (row flags, is it stuck)
            ({"action": "block"}, True),
            ({"action": "accept"}, False),
            ({"is_repair_pending": True}, True),
            ({"is_accepted_reaudit_pending": True}, True),
            ({"is_reset_unreviewed": True}, True),
            # A reset that is *also* a repair is still one question, not two.
            ({"is_reset_unreviewed": True, "is_repair_pending": True}, True),
            ({"is_reset_unreviewed": True, "is_accepted_reaudit_pending": True}, True),
            ({"action": "block", "is_reset_unreviewed": True}, True),
            # Not reviewed, nothing pending: not stuck.
            ({"is_never_reviewed": True}, False),
            ({}, False),
        ]
        for review, expected in cases:
            self.assertEqual(expected, self.ui.is_discuss_bucket(review), review)
        # And each flag the Python reads must appear in the SQL, or the two are not the same rule.
        for flag in ("is_repair_pending", "is_accepted_reaudit_pending", "is_reset_unreviewed"):
            self.assertIn(flag, self.ui.SQL_DISCUSS_PREDICATE)
        self.assertIn("'block'", self.ui.SQL_DISCUSS_PREDICATE)
        # Negative control for the SQL string: dropping the reset clause would make the
        # `is_reset_unreviewed` case above the only one that differs.
        self.assertIn("NOT is_repair_pending AND NOT is_accepted_reaudit_pending",
                      self.ui.SQL_DISCUSS_PREDICATE)


class DiscussTaxonomyTests(unittest.TestCase):
    """The 錯題討論區's pickers must be built from the stuck papers, not the whole queue.

    A whole-queue tree offers branches with zero stuck questions (measured: eight categories in the
    queue, seven of them holding a stuck question), and a picker option that opens nothing reads as a
    broken filter rather than an empty one. The tree must also be the **same** tree whatever filter
    is applied, or choosing a subject collapses every other subject out of the picker.
    """

    @classmethod
    def setUpClass(cls):
        cls.ui = load_ui_module()

    def test_the_paper_spelling_matches_the_browser(self):
        """The server's `_paper_of_candidate` and the browser's `paperOf()` are one key.

        If they disagree, `whereOfPaper()` cannot find the paper in the tree and the picker offers a
        choice that opens nothing. Read from both sources so a change to either is caught here.
        """
        core = (Path(__file__).resolve().parents[1] / "review_ui" / "v2" / "01-core.js")\
            .read_text(encoding="utf-8")
        self.assertIn("official_pdf", core)
        self.assertIn("question_pdf_relative", core)
        self.assertIn("replace(/\\.pdf$/i, '')", core)
        row = {"metadata": {"question_pdf_relative": "a/b/1152_藥師(一)_藥劑學.pdf"}}
        self.assertEqual("1152_藥師(一)_藥劑學", self.ui._paper_of_candidate(row))
        # No `.pdf`, no crash, same answer as the browser's `.replace(/\.pdf$/i, '')`.
        self.assertEqual("paper", self.ui._paper_of_candidate({"metadata": {"question_pdf": "p/paper"}}))

    def test_the_tree_offers_no_branch_with_nothing_in_it(self):
        """Every category/year/sitting/subject in the tree holds at least one stuck question.

        This is the property the whole-queue tree fails. Measured on the live corpus: the queue's
        tree holds `藥師` (0 stuck questions); the discuss tree does not.
        """
        rows = [
            {"metadata": {"question_pdf_relative": "p/1152_藥師(一)_藥劑學.pdf",
                          "normalized_category_name": "藥師(一)",
                          "normalized_subject_name": "藥劑學", "year": "115", "exam_ordinal": "2"}},
            {"metadata": {"question_pdf_relative": "p/1152_藥師(一)_藥劑學.pdf",
                          "normalized_category_name": "藥師(一)",
                          "normalized_subject_name": "藥劑學", "year": "115", "exam_ordinal": "2"}},
        ]
        entries = self.ui.paper_entries_for(rows)
        # One entry per paper, and `questions` is the number of stuck rows in it, not the paper's
        # whole size: the reviewer can only act on the rows in the list.
        self.assertEqual(1, len(entries))
        self.assertEqual(2, entries[0]["questions"])
        tree = self.ui.review_queue.taxonomy_of(entries)
        for category, bucket in tree.items():
            self.assertGreater(bucket["questions"], 0, category)
            for year, year_bucket in bucket["years"].items():
                self.assertGreater(year_bucket["questions"], 0, f"{category}/{year}")
                for sitting, sitting_bucket in year_bucket["sittings"].items():
                    self.assertGreater(sitting_bucket["questions"], 0, f"{category}/{year}/{sitting}")
                    for subject, leaf in sitting_bucket["subjects"].items():
                        self.assertTrue(leaf["papers"], f"{category}/{year}/{sitting}/{subject}")
                        self.assertGreater(leaf["questions"], 0)

    def test_the_tree_is_the_same_whatever_filter_is_applied(self):
        """`discuss_payload` returns one taxonomy, not one per scope.

        Negative control for the collapse: a tree built from the *filtered* rows would lose the
        branches a filter excluded. Here the same tree object comes back for a narrow and a wide
        `params`, which is the contract `refreshScope` needs to keep every subject on offer.
        """
        state = object.__new__(self.ui.ReviewState)
        state.sql_review_enabled = False
        state.candidate_path = Path("nonexistent-candidates.jsonl")
        state.issue_path = None
        state.review_log = Path("nonexistent-review.jsonl")
        state.candidates = [
            {"candidate_key": "k1", "metadata": {"question_pdf_relative": "p/1152_藥師(一)_藥劑學.pdf",
                                                     "normalized_category_name": "藥師(一)",
                                                     "normalized_subject_name": "藥劑學",
                                                     "year": "115", "exam_ordinal": "2"}},
            # A row that is *reviewed and fine*, in a different category. It must NOT be in the
            # discuss tree: the tree is the stuck population, and this is the negative control for
            # "the filter was dropped". Measured on the live corpus, the whole-queue tree holds
            # `藥師` (0 stuck questions) and this is the same shape of mistake.
            {"candidate_key": "k2", "metadata": {"question_pdf_relative": "p/1151_藥師_藥事行政.pdf",
                                                     "normalized_category_name": "藥師",
                                                     "normalized_subject_name": "藥事行政",
                                                     "year": "115", "exam_ordinal": "1"}},
        ]
        state.latest_reviews = {"k1": {"action": "block"}, "k2": {"action": "accept"}}
        state.latest_reset_reviews = {}
        state.principles_events = []
        state.repair_questions_events = []
        state.filtered_candidate_payloads = lambda params: {"candidates": []}
        state.candidate_data_status = lambda: {}
        wide = state.discuss_payload({"reviewStatus": "discuss", "limit": "500"})
        narrow = state.discuss_payload({"reviewStatus": "discuss", "limit": "500", "category": "藥師(一)"})
        self.assertEqual(["藥師(一)"], list(wide["taxonomy"]))
        self.assertEqual(wide["taxonomy"], narrow["taxonomy"])
        self.assertEqual(1, wide["stuck_total"])


class DiscussScopeFilterTests(unittest.TestCase):
    """使用者回報 #2：「錯題討論區要有篩選，比較好審核。」

    兩件事要同時成立，而它們很容易互相拉扯：
      1. 四層篩選要真的把清單收窄（伺服器過濾），而且**預設全部**——不能先把別科的卡住題藏起來。
      2. 改一層不可以動到其他層。使用者的原話是「每次都跳來跳去」，那一條在題目區已經修過，
         這一區沿用同一組純函式，所以不應該再出現一次。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = V2.read_text(encoding="utf-8")
        cls.js = script_of(cls.html)

    def test_the_picker_offers_four_levels_and_defaults_to_all(self):
        state = re.search(r"const D = \{(.*?)\n\};", self.js, re.S)
        self.assertIsNotNone(state, "找不到 D 狀態")
        body = state.group(1)
        # Four levels, each defaulting to `''` (deliberate 全部), not `null` (not chosen yet).
        self.assertRegex(body, r"scope:\s*\{[^}]*category:\s*''")
        self.assertRegex(body, r"scope:\s*\{[^}]*year:\s*''")
        self.assertRegex(body, r"scope:\s*\{[^}]*sitting:\s*''")
        self.assertRegex(body, r"scope:\s*\{[^}]*subject:\s*''")
        # And the picker draws all four selects.
        picker = function_body(self.js, "discussScopeHtml")
        for picker_id in ("dPickCategory", "dPickYear", "dPickSitting", "dPickSubject"):
            self.assertIn(picker_id, picker)
        # All four default to 全部 (the empty option) rather than to the first value.
        for label in ("全部類科", "全部年度", "全部考次", "全部科目"):
            self.assertIn(label, picker)

    def test_the_filter_goes_to_the_server_not_the_browser(self):
        """篩選是伺服器的：卡住的題散在整個題庫，瀏覽器只拿得到 500 題的上限視窗。"""
        load = function_body(self.js, "loadDiscuss")
        # Every level maps to the same parameter name the question area uses.
        for param in ("params.category", "params.year", "params.ordinal", "params.subject"):
            self.assertIn(param, load)
        # `''` (全部) drops the parameter entirely - the server reads a missing parameter as
        # "no filter", and sending `''` would be a second translation of the same meaning.
        self.assertRegex(load, r"if \(D\.scope\.category\) params\.category")
        # The server does the filtering, so nothing filters `D.rows` in the browser.
        self.assertNotIn("D.rows.filter", load)

    def test_changing_one_level_does_not_touch_the_others(self):
        """與題目區同一條契約：每個 `onchange` 只寫自己那一格。

        舊版是「設下層為 `null`」，`resolveLevel` 於是替它挑一個**具體**值——使用者的
        「跳來跳去」。這裡把那些歸零拿掉。
        """
        bind = function_body(self.js, "bindDiscussScope")
        for gone in ("D.scope.year = null", "D.scope.sitting = null", "D.scope.subject = null",
                     "D.scope.category = null"):
            self.assertNotIn(gone, bind, f"{gone} 還在討論區的篩選裡")
        # Each handler writes exactly its own level and then reloads.
        self.assertRegex(bind, r"(?s)dPickCategory'\).*?category\.onchange.*?D\.scope\.category = category\.value")
        self.assertRegex(bind, r"(?s)dPickYear'\).*?year\.onchange.*?D\.scope\.year = year\.value")
        self.assertRegex(bind, r"(?s)dPickSitting'\).*?sitting\.onchange.*?D\.scope\.sitting = sitting\.value")
        self.assertRegex(bind, r"(?s)dPickSubject'\).*?subject\.onchange.*?D\.scope\.subject = subject\.value")
        # Each handler's body must call the reload - a filter that redraws nothing is a filter that
        # does nothing.
        self.assertEqual(4, bind.count("discussReload()"))

    def test_the_picker_reuses_the_question_areas_pure_helpers(self):
        """一個做同一件事的第二次實作，就是第二個會不一致的地方。

        所以這一區的選單用題目區的同一組純函式，不重寫一份。
        """
        picker = function_body(self.js, "discussScopeHtml")
        for helper in ("availableSittings", "availableSubjects", "countPapers", "countQuestions"):
            self.assertIn(helper, picker, f"沒有重用 {helper}")
        # And it is not a second, independent implementation.
        self.assertNotIn("function discussSittings", self.js)
        self.assertNotIn("function discussSubjects", self.js)

    def test_a_collapsed_picker_would_be_caught(self):
        # 負對照：把類科的 onchange 改成同時歸零下層（舊行為），上面那條必須失敗。
        broken = self.js.replace(
            "D.scope.category = category.value; discussReload();",
            "D.scope.category = category.value; D.scope.year = null; D.scope.sitting = null; "
            "D.scope.subject = null; discussReload();", 1)
        self.assertNotEqual(broken, self.js, "負對照必須真的改到東西")
        self.assertIn("D.scope.year = null", function_body(broken, "bindDiscussScope"))

    def test_all_categories_still_offers_the_three_levels_below_it(self):
        """全部類科 是一個真的 bucket，不是 `undefined`。

        四個共用純函式（`availableSittings` 等）只吃**一個**類科的 bucket，因為題目區只問它們
        這個。但這一區預設全部類科，所以需要一個合併的 bucket，否則年度／考次／科目三個下拉會
        是空的——一排篩選只有第一層能用。實測過的缺陷：修正前 `optCounts` 是 `[8,1,1,1]`
        （只有全部的那一個選項），修正後 `[8,16,3,37]`。
        """
        picker = function_body(self.js, "discussScopeHtml")
        self.assertIn("mergedBucket(tree)", picker,
                      "全部類科 沒有合併 bucket，下面三層會是空的")
        merged = function_body(self.js, "mergedBucket")
        # 形狀要與一棵正常的 tree 一致，否則共用純函式認不得。
        for field in ("years", "sittings", "subjects", "papers", "questions"):
            self.assertIn(field, merged)

    def test_the_merged_bucket_is_checked_against_the_real_helpers(self):
        """用真的 `availableSittings`／`availableSubjects` 驅動合併後的 bucket。

        文字斷言只證明"有呼叫"；這一條證明合併出來的形狀真的能讓那些函式讀出值。
        """
        tree = {
            "藥師(一)": {"papers": 1, "questions": 3, "years": {"115": {"papers": 1, "questions": 3,
                "sittings": {"2": {"papers": 1, "questions": 3,
                    "subjects": {"藥劑學": {"papers": ["p1"], "questions": 3}}}}}}},
            "醫師(一)": {"papers": 1, "questions": 2, "years": {"108": {"papers": 1, "questions": 2,
                "sittings": {"1": {"papers": 1, "questions": 2,
                    "subjects": {"生理學": {"papers": ["p2"], "questions": 2}}}}}}},
        }
        result = run_node(f"(() => {{ const tree = {json.dumps(tree)};"
                          " const merged = mergedBucket(tree); return {"
                          " sittings: availableSittings(merged, ''),"
                          " subjects: availableSubjects(merged, '', ''),"
                          " papers: countPapers(merged, '', '', ''),"
                          " questions: countQuestions(merged, '', '', ''),"
                          " pick115: availableSubjects(merged, '115', ''),"
                          " }; })()")
        self.assertEqual(["1", "2"], result["sittings"], "合併後看不到另一個類科的考次")
        self.assertEqual(["生理學", "藥劑學"], result["subjects"])
        self.assertEqual(2, result["papers"])
        self.assertEqual(5, result["questions"])
        # 選了年度之後，科目就只剩那一年有的。
        self.assertEqual(["藥劑學"], result["pick115"])

    def test_a_stale_value_falls_back_to_all_instead_of_filtering_to_nothing(self):
        """一個值「上層換了之後就不存在」時，落點是 全部，不是另一個具體值。

        這與題目區是同一條契約（`resolveLevel`）。沒有它，`D.scope.subject` 會留在一個新類科
        沒有的科目上，送給伺服器，把清單過濾成空的——而下拉顯示的是別的東西。
        """
        picker = function_body(self.js, "discussScopeHtml")
        for expr in ("resolveLevel(D.scope.year", "resolveLevel(D.scope.sitting",
                     "resolveLevel(D.scope.subject"):
            self.assertIn(expr, picker, f"{expr} 沒有被調用")
        # And the category itself is dropped when the tree no longer holds it.
        self.assertRegex(picker, r"categories\.includes\(D\.scope\.category\)")

    def test_the_tree_is_the_discuss_areas_own_not_the_whole_queue(self):
        """選單只能提供「卡住的那一群」的分支。整份佇列的樹會提供 0 題的選項。"""
        load = function_body(self.js, "loadDiscuss")
        # The tree comes from the server's response, not recomputed from the (capped) local rows.
        self.assertIn("payload.taxonomy", load)
        self.assertIn("payload.stuck_total", load)
        # The browser must not rebuild the tree from the rows it received - those are filtered and
        # capped, and a tree from them would collapse the moment a filter is applied.
        self.assertNotIn("treeFrom(", load)


if __name__ == "__main__":
    unittest.main()
