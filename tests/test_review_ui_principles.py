"""原則區的三個新契約（2026-09-24）：**核准**、**類似題**、**機器動過的字**。

使用者原文：「判讀 → 文字 跟 文字 →抽取檔要打通，並且改標籤送到「AI已解決」，我才能知道有沒有
改過」，以及「全部卡在同一道閘門…打開閘門」。打開閘門之後，畫面上的每一格都必須回答「這是誰改的、
用什麼方式改的」，而人要有辦法**退回**機器改過的東西。所以這一輪新增三件事，這個檔案各釘一次：

  1. **核准**：原則與反問一樣是 append-only 的流上的一筆事件。`approve`／`unapprove` **不重寫**
     `add` 那一行；重複的決定不搬 `approved_at`；重新 `add` 同一個 id 等於一句新的話，核准狀態
     回到未核准。折疊出來的 `approved_count`／`pending_count` 是畫面上那兩個數字的**唯一**來源。
  2. **提示詞只讀已核准的**：`ai_findings.principles_for_prompt` 是提示詞那一邊**唯一**的讀者，
     而畫面那一邊讀的是 `discuss.active_principles`（全部生效中的原則）。兩個存取器並存是刻意的
     ——合併成一個，「誰在用哪一種」就只能用猜的。
  3. **類似題**：清單由伺服器在整份 finding 上算。原則自己的 `evidence` **排在最前面**（那是它
     自己說它在講哪一題，不是猜的），其餘是形狀相符的題目，而且上限與被截掉的數量要說出來。
  4. **機器改過的字分成三類**：`field`（依紙本改字）／`glyph`（字形替換）／`normalisation`
     （正規化（部首碼位））。同一格「AI已修改」（2026-09-25 前叫「AI已解決」）底下是三種不同的
     東西，要複核的方式也不同。
  5. **撤回不是修改**（2026-09-25）：`withdrawn`（機器把自己改錯的字收回、文字回到紙本）**不進**
     那一格——它回到那個人自己的判決，沒有任何人為決定時回到未看。

**負對照**：每一條斷言旁邊都有一個「改回舊樣子就必須失敗」的版本——沒有核准事件的原則必須**不**
被當成已核准（而不是預設放行）、未核准的原則必須**不**出現在提示詞裡（而它仍然在畫面上）、沒有
evidence 的原則必須拿到空清單（而不是「什麼都像」）、一筆純退回必須拿不到任何一個新標籤（而不是
每一列都掛上一個）。沒有負對照的檢查，只是在讀自己的註解。
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_review_ui_areas import function_body, script_of  # noqa: E402
from test_review_ui_discuss import run_node  # noqa: E402
from test_review_ui_scope import import_review_ui as load_ui_module  # noqa: E402

V2 = ROOT / "review_ui" / "v2.html"


def qbr_module(name: str):
    """`qbr.*` 的載入器：先走一次 server（它會把 `qbr/src` 放進 `sys.path`）。"""
    load_ui_module()
    return importlib.import_module(f"qbr.{name}")


#: 三種機器動作，以及它們在畫面上的字（`queue_view.APPLIED_KINDS` 的值）。字是給人看的，
#: 值是契約——這裡把兩邊寫在一起，改字就必須同時改這一張表。
#: `withdrawn` 是第四格但**不是**那一組：機器把自己改過的字收回（人打回、或下一輪自己判定
#: 判讀不可信），文字回到紙本，所以它不在「還有多少要讀」的數字裡（`queue_view.WITHDRAWN_KIND`）。
APPLIED_WORDS = {"field": "依紙本改字", "glyph": "字形替換", "normalisation": "正規化（部首碼位）",
                 "withdrawn": "已還原（機器改錯）"}


def principle_add(principle_id: str, text: str, **extra) -> dict:
    """一筆 `add`，形狀與 `curate_principles_from_comments.py` 寫的相同。"""
    return {"schema": "qbr_review_principle_v0.1", "action": "add", "principle_id": principle_id,
            "text": text, "scope": "question", "reviewer": "curated", "created_at": "2026-09-24T08:00:00",
            **extra}


def finding_record(key: str, *, kinds=(), fields=(), what="", number="1", paper="115090:311",
                   subject="微生物學", disputes_stored=True) -> dict:
    """一筆 `question_ai_findings.jsonl` 記錄（**compact 形狀**，與 store 的投影一致）。

    `disputes_stored` 是負對照用的開關：少了它，這一筆就沒有「機器量到哪一種爭議」可言，形狀只剩
    欄位——而兩種都要能比。
    """
    evidence = {"stem": "…", "answer": "A"}
    if disputes_stored:
        evidence["disputes"] = [{"kind": kind, "note": kind} for kind in kinds]
    return {
        "candidate_key": key, "schema": "qbr_ai_finding_v0.1", "model": "test-model",
        "crop": "review-ui/crops/q001-dispute.png", "question_number": number, "paper": paper,
        "subject": subject, "finding": {"verdict": "DISPUTED", "what": what, "where": "選項 A"},
        "evidence": evidence,
        "changes": [{"field": field, "from": "壞", "to": "好", "stored": "壞的字", "page": "好的字"}
                    for field in fields],
    }


class PrincipleApprovalTests(unittest.TestCase):
    """核准是事件，不是改寫；折疊是三條規則的乘積。"""

    @classmethod
    def setUpClass(cls):
        cls.discuss = qbr_module("discuss")

    def test_approval_is_a_second_event_and_the_fold_counts_it(self):
        events = [principle_add("p1", "中文詞中間不該有空格。"),
                  principle_add("p2", "選項標號一律半形大寫。")]
        before = self.discuss.principles_projection(events)
        self.assertEqual((2, 0, 2), (before["count"], before["approved_count"],
                                     before["pending_count"]))
        self.assertFalse(before["principles"][0]["approved"])
        self.assertIsNone(before["principles"][0]["approved_by"])
        # 負對照：**沒有核准事件就不是已核准**。舊行為（把寫下的原則直接當成生效的提示詞內容）
        # 正是 `approved` 缺席時預設放行——這裡釘住它是 False。
        self.assertEqual(0, before["approved_count"])

        decision = self.discuss.principle_decision_event("p1", "approve", "owner")
        self.assertEqual(self.discuss.PRINCIPLE_SCHEMA, decision["schema"])
        self.assertEqual("approve", decision["action"])
        events.append(dict(decision, created_at="2026-09-24T10:00:00"))
        after = self.discuss.principles_projection(events)
        self.assertEqual((1, 1), (after["approved_count"], after["pending_count"]))
        first, second = after["principles"]
        self.assertEqual((True, "owner", "2026-09-24T10:00:00"),
                         (first["approved"], first["approved_by"], first["approved_at"]))
        self.assertFalse(second["approved"])

    def test_a_repeat_decision_appends_but_does_not_move_the_time(self):
        events = [principle_add("p1", "一句話。"),
                  dict(self.discuss.principle_decision_event("p1", "approve", "owner"),
                       created_at="2026-09-24T10:00:00"),
                  dict(self.discuss.principle_decision_event("p1", "approve", "someone-else"),
                       created_at="2026-09-24T11:00:00")]
        projection = self.discuss.principles_projection(events)
        row = projection["principles"][0]
        # 歷史留著（兩筆決定都在流上），但「這個決定是什麼時候、誰做的」留在第一次。
        self.assertEqual((True, "owner", "2026-09-24T10:00:00"),
                         (row["approved"], row["approved_by"], row["approved_at"]))
        self.assertEqual(3, projection["event_count"])
        # 負對照：舊行為＝再按一次核准會被當成「新的決定」（或更糟：改寫第一次那一行）。
        self.assertNotEqual("someone-else", row["approved_by"])
        self.assertEqual(3, len(events), "append-only：重複決定是多一筆，不是改一筆")

    def test_unapprove_and_re_add_take_the_approval_away(self):
        events = [principle_add("p1", "第一句。"),
                  dict(self.discuss.principle_decision_event("p1", "approve", "owner"),
                       created_at="2026-09-24T10:00:00")]
        events.append(dict(self.discuss.principle_decision_event("p1", "unapprove", "owner"),
                           created_at="2026-09-24T12:00:00"))
        self.assertFalse(self.discuss.principles_projection(events)["principles"][0]["approved"])
        # 取消核准之後再核准：時間是**這一次**的決定（決定確實變了）。
        events.append(dict(self.discuss.principle_decision_event("p1", "approve", "owner"),
                           created_at="2026-09-24T13:00:00"))
        self.assertEqual("2026-09-24T13:00:00",
                         self.discuss.principles_projection(events)["principles"][0]["approved_at"])
        # 重新 add 同一個 id ＝ 一句新的話：核准的對象是那句話，所以狀態回到未核准。
        events.append(principle_add("p1", "第二句。", created_at="2026-09-24T14:00:00"))
        row = self.discuss.principles_projection(events)["principles"][0]
        self.assertEqual(("第二句。", False, None, None),
                         (row["text"], row["approved"], row["approved_by"], row["approved_at"]))
        # 移除帶走核准狀態。
        events.append({"schema": self.discuss.PRINCIPLE_SCHEMA, "action": "remove",
                       "principle_id": "p1", "reviewer": "local", "created_at": "2026-09-24T15:00:00"})
        self.assertEqual(0, self.discuss.principles_projection(events)["count"])

    def test_a_decision_that_is_not_a_decision_is_refused(self):
        # 負對照：寫得出「action: approve 之外」的東西，就等於折疊會安靜地忽略它——
        # 一筆看起來成功的寫入比一次失敗的寫入糟。
        with self.assertRaises(ValueError):
            self.discuss.principle_decision_event("p1", "approve_all")
        with self.assertRaises(ValueError):
            self.discuss.principle_decision_event("", "approve")

    def test_the_decision_lands_in_the_same_append_only_file(self):
        """真的寫一筆：同一條流、append（舊的行一個字都沒動），而且重讀得到。"""
        events_module = qbr_module("review_ui.events")
        principles = self.discuss
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "question_review_principles.jsonl"
            principles.append_event(path, principle_add("p1", "一句話。"))
            original = path.read_text(encoding="utf-8")
            principles.append_event(
                path, principles.principle_decision_event("p1", "approve", "owner"))
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(2, len(lines), "核准是一行新的記錄")
            self.assertTrue(path.read_text(encoding="utf-8").startswith(original),
                            "append-only：原本那一行必須原封不動")
            reread = events_module.load_append_only_events(path)
            row = principles.principles_projection(reread)["principles"][0]
            self.assertEqual((True, "owner"), (row["approved"], row["approved_by"]))
            self.assertTrue(row["approved_at"], "created_at 由 append_event 蓋")


class PromptReadsApprovedOnlyTests(unittest.TestCase):
    """提示詞那一邊只讀已核准的；畫面那一邊讀全部。兩份清單必須分開。"""

    @classmethod
    def setUpClass(cls):
        cls.discuss = qbr_module("discuss")
        cls.ai = qbr_module("ai_findings")

    def _events(self):
        events = [principle_add("p1", "中文詞中間不該有空格。"),
                  principle_add("p2", "選項標號一律半形大寫。")]
        events.append(dict(self.discuss.principle_decision_event("p1", "approve", "owner"),
                           created_at="2026-09-24T10:00:00"))
        return events

    def test_the_prompt_carries_the_approved_principle_and_not_the_pending_one(self):
        events = self._events()
        approved = self.ai.principles_for_prompt(events)
        self.assertEqual(["中文詞中間不該有空格。"], approved)
        # 負對照：**同一批事件**在畫面那一邊是兩條。少了這一條，上面那條可以由「事件本來就只有
        # 一條」滿足——那樣的測試證明不了任何事。
        self.assertEqual(["中文詞中間不該有空格。", "選項標號一律半形大寫。"],
                         self.discuss.active_principles(events))
        block = self.ai.principles_note(approved)
        self.assertIn("中文詞中間不該有空格。", block)
        self.assertNotIn("選項標號一律半形大寫。", block)

    def test_the_rendered_prompt_and_its_hash_use_the_same_list(self):
        events = self._events()
        approved = self.ai.principles_for_prompt(events)
        system, user = self.ai.build_prompt({"stem": "題幹", "options": [], "answer": ""},
                                            population="blocked", principles=approved)
        text = system + user
        self.assertIn("中文詞中間不該有空格。", text)
        self.assertNotIn("選項標號一律半形大寫。", text)
        # 同一個清單 → 同一個版本；把未核准那一條偷偷加回去 → 版本必須變（否則兩份不同的提示詞
        # 會共用一個版本號，而版本號存在的理由正是分辨這種事）。
        same = self.ai.prompt_version(population="blocked", principles=approved)
        other = self.ai.prompt_version(
            population="blocked", principles=self.discuss.active_principles(events))
        self.assertNotEqual(same, other)

    def test_only_the_one_reader_decides_which_principles_the_model_sees(self):
        """兩個 prompt 呼叫端**都**走 `principles_for_prompt`，不是各自讀流。"""
        source = (ROOT / "qbr" / "src" / "qbr" / "ai_findings.py").read_text(encoding="utf-8")
        # 只有一份過濾：那個函式就是 `discuss.approved_principles` 的別名，不是第二個實作。
        self.assertEqual(1, source.count("def principles_for_prompt("))
        self.assertIn("discuss.approved_principles(events)", source)
        for script in ("confirm_dispute.py", "ask_about_blocks.py"):
            text = (ROOT / "qbr" / "scripts" / script).read_text(encoding="utf-8")
            self.assertIn("principles_for_prompt(", text, script)
            # 負對照：呼叫端自己去讀全部原則，未核准的那些就會進提示詞。
            self.assertNotIn("active_principles(", text,
                             f"{script} 直接讀全部原則——那些未核准的會進提示詞")


class SimilarQuestionsTests(unittest.TestCase):
    """類似題：自己的 evidence 在最前面，其餘照形狀算，上限要說出來。"""

    @classmethod
    def setUpClass(cls):
        cls.queue_view = load_ui_module()

    def setUp(self):
        self.K1 = "moex:115090:311:0704:1:question:q001"
        self.K2 = "moex:115090:311:0704:1:question:q002"
        self.K3 = "moex:115090:311:0704:1:question:q003"
        self.K4 = "moex:115090:311:0704:1:question:q004"
        self.findings = {
            self.K1: finding_record(self.K1, kinds=["substituted-glyph"], fields=["option A"],
                                    what="OPTION_GLYPH"),
            self.K2: finding_record(self.K2, kinds=["substituted-glyph"], fields=["option B"],
                                    what="OPTION_GLYPH"),
            self.K3: finding_record(self.K3, kinds=["spacing"], fields=["stem"], what="STEM_SPACE"),
            self.K4: finding_record(self.K4, kinds=["figure-missing"], fields=["answer"],
                                    what="FIGURE"),
        }
        self.principle = {"principle_id": "p1", "text": "A 選項的字形照紙本。",
                          "evidence": [self.K1]}

    def test_the_principles_own_evidence_is_first_and_the_shape_follows(self):
        result = self.queue_view.similar_questions(self.principle, self.findings)
        rows = result["rows"]
        self.assertEqual(self.K1, rows[0]["candidate_key"])
        self.assertEqual("evidence", rows[0]["source"])
        # 同形狀的 K2 在：它的欄位是 `option B`，與種子的 `option A` **同一個欄位**
        # （`change_field_key` 把選項字母拿掉——「同一個選項被讀錯」講的是欄位，不是字母，
        # 否則同一種問題的兩題會因為換了一個選項就變成不同形狀）。
        self.assertEqual([self.K2], [row["candidate_key"] for row in rows[1:]])
        self.assertEqual("shape", rows[1]["source"])
        self.assertGreater(rows[1]["score"], 0)
        self.assertEqual({"kinds": ["substituted-glyph"], "fields": ["option"],
                          "what": "OPTION_GLYPH"}, result["signature"])
        # 不同形狀的 K3（stem／spacing）與 K4（answer／figure-missing）不在清單裡。
        self.assertNotIn(self.K3, [row["candidate_key"] for row in rows])
        self.assertNotIn(self.K4, [row["candidate_key"] for row in rows])
        # 一列要**讀得出來**：題號／卷／科目。
        self.assertEqual(("1", "115090:311", "微生物學"),
                         (rows[0]["question_number"], rows[0]["paper"], rows[0]["subject"]))

    def test_a_key_without_a_finding_is_still_a_row(self):
        """`evidence` 指的是原則自己講的那一題——那一題沒有 finding 也還是一列（不能消失）。"""
        missing = "moex:115090:311:0704:1:question:q999"
        result = self.queue_view.similar_questions(
            {"principle_id": "p1", "evidence": [missing]}, self.findings)
        self.assertEqual([missing], [row["candidate_key"] for row in result["rows"]])
        self.assertEqual("evidence", result["rows"][0]["source"])
        self.assertIsNone(result["rows"][0]["question_number"])

    def test_a_principle_with_nothing_to_match_returns_nothing(self):
        # 負對照：沒有 evidence 就沒有形狀——回傳**空**清單，不是「什麼都像」。舊的形狀若用
        # 「有的話就比、沒有的話全收」，這一條會拿到 4 列。
        result = self.queue_view.similar_questions({"principle_id": "p2", "text": "無出處。"},
                                                   self.findings)
        self.assertEqual([], result["rows"])
        self.assertEqual(0, result["matched"])
        self.assertFalse(result["capped"])
        # 而且真的有一堆可以比的東西——否則上面那條是空的而不是否定的。
        self.assertGreater(len(self.findings), 0)

    def test_the_cap_is_stated_and_does_not_reorder(self):
        # 兩題同形狀的再加進來，清單才有尾巴可以被截掉。
        findings = dict(self.findings)
        extra = "moex:115090:311:0704:1:question:q005"
        more = "moex:115090:311:0704:1:question:q006"
        findings[extra] = finding_record(extra, kinds=["substituted-glyph"], fields=["option C"])
        findings[more] = finding_record(more, kinds=["substituted-glyph"], fields=["option D"])
        small = self.queue_view.similar_questions(self.principle, findings, limit=2)
        self.assertEqual(2, small["returned"])
        self.assertEqual(4, small["matched"])
        self.assertTrue(small["capped"])
        self.assertEqual(self.K1, small["rows"][0]["candidate_key"], "上限不可以把證據擠掉")
        big = self.queue_view.similar_questions(self.principle, findings, limit=25)
        self.assertFalse(big["capped"])
        self.assertEqual(4, big["returned"])
        # 上限只截尾，不改順序：小清單是大清單的前綴（一個會自己重排的清單，看起來每次都像
        # 新的資訊）。
        self.assertEqual([row["candidate_key"] for row in small["rows"]],
                         [row["candidate_key"] for row in big["rows"]][:2])
        # 負對照：把上限當成「只算前幾筆」時，被截掉的那幾題不會出現在 `matched` 裡。
        self.assertNotEqual(big["matched"], small["returned"])

    def test_shape_matching_survives_a_finding_without_disputes(self):
        """只有欄位、沒有 dispute 的 finding 也要能比（`kinds` 空、`fields` 有）。"""
        findings = {self.K1: finding_record(self.K1, disputes_stored=False, fields=["option A"]),
                    self.K2: finding_record(self.K2, disputes_stored=False, fields=["option A"])}
        result = self.queue_view.similar_questions(self.principle, findings)
        self.assertEqual([self.K1, self.K2], [row["candidate_key"] for row in result["rows"]])


class MachineAppliedLabelTests(unittest.TestCase):
    """同一格「AI已修改」底下的三種機器動作，以及純退回與撤回不該拿到標籤。"""

    @classmethod
    def setUpClass(cls):
        cls.queue_view = load_ui_module()
        cls.js = script_of(V2.read_text(encoding="utf-8"))

    def test_the_three_classes_are_decided_by_the_change_itself(self):
        kind = self.queue_view.machine_applied_kind
        # 整欄換掉：`field`（絕不會是 normalisation，字是什麼都一樣）。
        self.assertEqual("field", kind({"action": "reset_review", "applied": "field",
                                        "changes": [{"field": "stem", "from": "⺟", "to": "母"}]}))
        # 換掉的字**全部**落在部首碼位區：螢幕上換的是寫法，不是讀法。
        self.assertEqual("normalisation", kind(
            {"action": "reset_review", "applied": "substitution",
             "changes": [{"field": "stem", "from": "⺟", "to": "⽏"}]}))
        # 換掉的字**不是**全部落在那一區：字形真的變了（Cyrillic ћ → ①）。
        self.assertEqual("glyph", kind(
            {"action": "reset_review", "applied": "substitution",
             "changes": [{"field": "option A", "from": "ћ", "to": "①"}]}))
        # 部首字換成它的正體字（`⺟`→`母`）**不是**這一類：兩邊的字不全在部首區，所以要眼睛看。
        # 真實資料裡這一種是由修復器自己的旗標標出來的（`normalisation`），不是靠碼位猜的
        # ——見下面兩條；猜錯時落在「需要眼睛」那一邊是刻意的。
        self.assertEqual("glyph", kind(
            {"action": "reset_review", "applied": "substitution",
             "changes": [{"field": "stem", "from": "⺟親", "to": "母親"}]}))
        # 修復器自己的旗標優先於碼位推測（布林，兩個方向都算）。
        self.assertEqual("normalisation", kind(
            {"action": "reset_review", "applied": "substitution", "normalisation": True}))
        self.assertEqual("glyph", kind(
            {"action": "reset_review", "applied": "substitution", "normalisation": False}))
        # 記錄了 substitution 但沒有留下差異文字 → 落在需要眼睛的那一邊（安全的一邊）。
        self.assertEqual("glyph", kind({"action": "reset_review", "applied": "substitution"}))

    def test_a_reset_without_applied_gets_no_label(self):
        # 負對照：管線退回、模型提問這一類 reset 沒有動到任何字。給它們一個標籤，就等於把
        # 「有改過」變成每一列都有的字——那正是這一輪要修掉的東西。
        kind = self.queue_view.machine_applied_kind
        self.assertIsNone(kind({"action": "reset_review", "repair_kind": "backfill_repair"}))
        self.assertIsNone(kind({"action": "block"}))
        self.assertIsNone(kind(None))
        self.assertIsNone(kind({"action": "reset_review", "applied": "moved"}))

    def test_the_projection_carries_the_class_to_the_browser(self):
        projection = self.queue_view.review_projection(
            {"action": "reset_review", "applied": "field"},
            {"action": "reset_review", "applied": "field",
             "changes": [{"field": "stem", "from": "舊", "to": "新"}]}, {})
        self.assertEqual("field", projection["applied"])
        self.assertEqual("field", projection["applied_kind"])
        plain = self.queue_view.review_projection(
            {"action": "reset_review", "repair_kind": "backfill_repair"},
            {"action": "reset_review", "repair_kind": "backfill_repair"},
            {})
        self.assertIsNone(plain["applied_kind"])

    def test_a_machine_retraction_is_its_own_kind(self):
        """機器把自己改過的字收回：文字回到紙本，所以它與「沒動過」和「改過」都不同。

        實測 2026-09-24：審題者把上下標被亂改的那一批全部打回 block，撤銷事件就是把那些列
        還原成 `parser_original`。它不進「還有多少要讀」的三個數字（那三個是待讀的工作量），
        但它必須有名字——否則畫面上「已還原」與「沒改過」長得一樣，人看不出機器曾經改錯。
        """
        kind = self.queue_view.machine_applied_kind
        self.assertEqual("withdrawn", kind({"action": "reset_review", "applied": "withdrawn",
                                           "withdraw": ["stem"]}))
        # 負對照：撤銷不是 substitution，就算留了差異文字也不能被算成字形替換。
        self.assertEqual("withdrawn", kind({"action": "reset_review", "applied": "withdrawn",
                                            "changes": [{"field": "stem", "from": "⺟", "to": "母"}]}))

    def test_a_withdrawal_never_lands_in_the_modified_bucket(self):
        """撤回不是修改：機器把自己改錯的字收回去的那一題，**不**在「AI已修改」那一格。

        owner 原文（2026-09-25）：「AI以解決裡面會有一些題目寫「已還原(機器改錯)」…你這樣做是多此
        一舉，因為你退回等於沒有解決…又退回到我一定會認真看的「AI已解決」，就會讓我很火大…可以改成
        AI已修改，但是「還原」這種事情不是修改」。

        站上量到（唯讀）：`applied=withdrawn` 130 筆、127 題。其中 123 題的人為判決還在 `latest`
        （那幾列本來就回到那個人自己的 `block`，這是 2026-09-24 修好的那一條），**3 題**的撤回是
        這一題最後一筆、後面沒有任何人的決定——那 3 題當天落在「AI已解決」，而同一列自己的字是
        「已還原（機器改錯）」，讀起來就是「AI 修好了」。
        """
        withdrawal = {"action": "reset_review", "reviewer": "repair_experience_apply",
                      "applied": "withdrawn", "withdraw": ["stem"]}
        # 撤回是這一題最後一筆事件、沒有任何人的決定：這一列不是待複核，也不是「AI 改了它」。
        projection = self.queue_view.review_projection(None, withdrawal, {})
        self.assertFalse(projection["is_reset_unreviewed"])
        self.assertNotEqual("reset_review", projection["queue_bucket"])
        self.assertEqual("withdrawn", projection["applied_kind"],
                         "撤回仍然是這一列的事實，畫面上要說得出來")
        # 那一列的籤與桶位讀的是同一份投影（瀏覽器讀 `review`，不重算一次）。
        chip = run_node("rowReviewAction({review:{action:'reset_review', applied:'withdrawn',"
                        " applied_kind:'withdrawn'}})")
        self.assertEqual("", chip, "撤回不是人的決定，也不是 AI 改了這一題")
        self.assertEqual("unseen", run_node(
            "(() => { S.verdict.set('w1', rowReviewAction({review:{action:'reset_review',"
            " applied:'withdrawn', applied_kind:'withdrawn'}}));"
            " return stateOf({candidate_key:'w1'}); })()"))
        # 撤回的事實沒有消失：那一列自己的字還在，而且與「AI已修改」那一籤的字不同。
        labels = run_node("({modified: LABEL.reset_review,"
                          " withdrawn: machineAppliedLabel({applied_kind:'withdrawn'})})")
        self.assertEqual("已還原（機器改錯）", labels["withdrawn"])
        self.assertNotEqual(labels["modified"], labels["withdrawn"], "同一個字串就等於沒分開")
        # 人為判決還在那 123 題上：撤回之後那一列回到他自己的決定，不是回到機器的籤。
        self.assertEqual("block", run_node(
            "rowReviewAction({review:{action:'block', applied:'withdrawn',"
            " applied_kind:'withdrawn'}})"))

        # **負控制**：同一條路徑上的 `field`（機器真的依紙本把字改掉了，站上 22 題的那個形狀）
        # 仍然在那一格裡。少了這一條，上面每一句都可以靠「把整個機器修復關掉」而恆真。
        repaired = self.queue_view.review_projection(
            None, {"action": "reset_review", "reviewer": "repair_experience_apply",
                   "repair_kind": "content_change", "applied": "field",
                   "changes": [{"field": "stem", "from": "舊", "to": "新"}]}, {})
        self.assertTrue(repaired["is_reset_unreviewed"])
        self.assertEqual("repair_pending", repaired["queue_bucket"])
        self.assertEqual("field", repaired["applied_kind"])
        self.assertNotEqual(projection["queue_bucket"], repaired["queue_bucket"],
                            "撤回與真的改過字不是同一格")
        self.assertEqual("reset_review", run_node(
            "rowReviewAction({review:{is_reset_unreviewed:true, action:'reset_review',"
            " applied:'field', applied_kind:'field'}})"))

    def test_the_home_counts_and_the_row_labels_read_one_fold(self):
        """首頁三個數字與一列上的標籤必須來自同一次折疊——數字對不上任何一列比沒有數字更糟。"""
        resets = {
            "k1": {"action": "reset_review", "applied": "field",
                   "changes": [{"field": "stem", "from": "舊", "to": "新"}]},
            "k2": {"action": "reset_review", "applied": "substitution",
                   "changes": [{"field": "stem", "from": "⺟", "to": "⽏"}]},
            "k3": {"action": "reset_review", "applied": "substitution",
                   "changes": [{"field": "option A", "from": "ћ", "to": "①"}]},
            "k4": {"action": "reset_review", "repair_kind": "backfill_repair"},
            "k5": {"action": "reset_review", "applied": "withdrawn", "withdraw": ["option A"]},
        }
        counts = self.queue_view.machine_activity_counts(resets)
        self.assertEqual({"field": 1, "glyph": 1, "normalisation": 1, "withdrawn": 1, "total": 3}, counts)
        for key, event in resets.items():
            projection = self.queue_view.review_projection(event, event, {})
            kind = projection["applied_kind"]
            self.assertEqual(kind is not None, key != "k4",
                             "只有動過字的那三題算進數字，也只在那三題上有標籤")
        self.assertEqual(3, counts["total"], "撤銷不算工作量：它不是還沒讀的題，是還原過的題")

    def test_the_browser_turns_the_class_into_the_owners_words(self):
        labels = run_node("({labels: APPLIED_LABEL, "
                          "field: machineAppliedLabel({applied_kind:'field'}), "
                          "glyph: machineAppliedLabel({applied_kind:'glyph'}), "
                          "normalisation: machineAppliedLabel({applied_kind:'normalisation'}), "
                          "withdrawn: machineAppliedLabel({applied_kind:'withdrawn'}), "
                          "plain: machineAppliedLabel({applied_kind:null}), "
                          "nothing: machineAppliedLabel({})})")
        self.assertEqual(APPLIED_WORDS, labels["labels"])
        self.assertEqual("依紙本改字", labels["field"])
        self.assertEqual("字形替換", labels["glyph"])
        self.assertEqual("正規化（部首碼位）", labels["normalisation"])
        self.assertEqual("已還原（機器改錯）", labels["withdrawn"],
                         "撤回要有自己的字：與「沒改過」同一個樣子，人就看不出機器曾經改錯")
        # 負對照：沒有 `applied_kind` 的（純退回）**一個字都不給**，畫面維持原本那一格。
        self.assertEqual("", labels["plain"])
        self.assertEqual("", labels["nothing"])

    def test_a_machine_applied_row_and_a_plain_reset_read_differently(self):
        """同一格「AI已修改」裡，機器改過字的列與純退回的列要讀起來不一樣。"""
        rows = run_node("({changed: discussRowLabel({review:{queue_bucket:'reset_review',"
                        "action:'reset_review', applied:'field', applied_kind:'field'}}),"
                        "normalised: discussRowLabel({review:{queue_bucket:'reset_review',"
                        "action:'reset_review', applied:'substitution',"
                        "applied_kind:'normalisation'}}),"
                        "plain: discussRowLabel({review:{queue_bucket:'reset_review',"
                        "action:'reset_review', repair_kind:'backfill_repair'}}),"
                        "blocked: discussRowLabel({review:{queue_bucket:'reviewed',"
                        "action:'block'}})})")
        self.assertEqual("依紙本改字", rows["changed"])
        self.assertEqual("正規化（部首碼位）", rows["normalised"])
        # 純退回：維持原本那一格（`DISCUSS_BUCKET_LABEL.reset_review`），而不是三個新標籤之一。
        self.assertEqual("退回未審", rows["plain"])
        self.assertNotIn(rows["plain"], APPLIED_WORDS.values())
        # **這一條就是「分得出來」**：同一格「AI已修改」裡的兩種列，讀起來必須不一樣。
        self.assertNotEqual(rows["changed"], rows["plain"])
        self.assertNotEqual(rows["normalised"], rows["plain"])
        # 人阻擋仍然是「人阻擋」——機器那一格不可以蓋掉人的那一格。
        self.assertEqual("人阻擋", rows["blocked"])

    def test_the_row_chip_is_drawn_from_the_servers_projection(self):
        """標籤是**讀投影**畫出來的，不是這一頁重算一次三分類。"""
        body = function_body(self.js, "machineAppliedLabel")
        self.assertIn("APPLIED_LABEL", body)
        self.assertIn("applied_kind", body)
        # 三分類的判定只活在伺服器上：這一支只查表，沒有第二個實作（碼位範圍、normalisation
        # 旗標都不該出現在瀏覽器裡——兩份判定就是兩個會漂的答案）。
        self.assertNotIn("2E80", body)
        self.assertNotIn("normalisation", body)
        list_body = function_body(self.js, "renderList")
        self.assertIn("machineAppliedLabel", list_body, "清單列的標籤要來自同一個翻譯")
        hint = function_body(self.js, "renderTextSide")
        self.assertIn("machineAppliedLabel", hint, "右欄的狀態句也要說出是哪一種")
        discuss_body = function_body(self.js, "discussRowLabel")
        self.assertIn("machineAppliedLabel", discuss_body)
        # 負對照：舊的實作只讀 bucket，所以一筆純退回與一筆依紙本改字會拿到同一個標籤。
        legacy = 'function discussRowLabel(candidate) { const review = candidate.review || {}; ' \
                 'return bucketLabel(review.queue_bucket || \'\'); }'
        self.assertNotIn("machineAppliedLabel", legacy)


class MachineActivityEndpointTests(unittest.TestCase):
    """首頁三個數字讀的是既有端點，不是這一頁自己掃。"""

    def test_the_home_card_asks_the_server_for_the_three_numbers(self):
        js = script_of(V2.read_text(encoding="utf-8"))
        body = function_body(js, "renderHome")
        self.assertIn("/api/machine-activity", body)
        # 負對照：這一頁不可以自己從事件流算——首頁的數字與它指到的那一頁不一致比沒有首頁更糟。
        self.assertNotIn("reset_review", body)
        # 卡片上的字**只在一份**（`APPLIED_LABEL`）：它在 renderHome 裡被讀，不是被重寫一次。
        # 先前這裡是「卡片自己寫四個標籤」，而列的標籤在另一支——那正是兩個會漂的說法。
        self.assertIn("APPLIED_LABEL", body)
        for word in ("依紙本改字", "字形替換", "正規化（部首碼位）", "已還原（機器改錯）"):
            self.assertNotIn(word, body, "字的來源只有 APPLIED_LABEL 一份，卡片不可以自己寫一次")
            # 這些字在註解裡被引用是好的（那是在說明它們的意思）；**字串實字**只能有一份。
            self.assertEqual(1, js.count("'%s'" % word),
                             "整個 v2 腳本裡「%s」的字串實字只能出現一次（APPLIED_LABEL）" % word)

    def test_the_endpoint_serves_the_same_counts_the_labels_use(self):
        source = (ROOT / "qbr" / "src" / "qbr" / "review_ui" / "handlers.py").read_text(
            encoding="utf-8")
        self.assertIn('"/api/machine-activity"', source)
        # 端點真的去呼叫那一個計數（不是自己再數一次），而且那一個計數只有一份實作——
        # 首頁的三個數字與一列上的標籤因此來自同一次折疊。
        self.assertIn("machine_activity_counts(self.state.latest_reset_reviews)", source)
        # 只有一份計數實作，而且它用的就是標籤用的那一支（首頁的三個數字與一列上的標籤
        # 因此來自同一次折疊）。
        self.assertEqual(0, source.count("def machine_activity_counts("))
        queue = (ROOT / "qbr" / "src" / "qbr" / "review_ui" / "queue_view.py").read_text(
            encoding="utf-8")
        self.assertEqual(1, queue.count("def machine_activity_counts("))
        self.assertIn("machine_applied_kind(event)", queue)


class QuestionIdentityTests(unittest.TestCase):
    """原則區要看得出「這一則在講哪一題」（卷／年／次／科目／第 N 題）。

    使用者回報（2026-09-25）：「看不出哪一則對應哪一題（卷／年／次／科目／第 N 題）」。站上量到的
    是：121 則反問全部只印 key 的尾段（`03:1:question:q054`），左欄連那個都沒有。

    身分跟著 `/api/discuss` 回來（`questions`，key → 五個欄位），因為一次要印 121 則——每一則各問
    一次 `/api/candidates` 是 121 個請求，而這一頁的 `limit=1` 已經在說「不要把候選題搬過來」。
    """

    @classmethod
    def setUpClass(cls):
        cls.ui = load_ui_module()

    def state_with_rows(self, rows):
        """一個只帶「候選題表」的狀態：問身分不需要真的事件流或資料庫。"""
        state = object.__new__(self.ui.ReviewState)
        state.sql_review_enabled = False
        state.candidate_by_key = {row["candidate_key"]: row for row in rows}
        return state

    def test_the_five_fields_come_from_the_candidate_row(self):
        state = self.state_with_rows([{
            "candidate_key": "k1", "question_number": 54,
            "metadata": {"normalized_category_name": "藥師(一)", "normalized_subject_name": "藥學(一)",
                         "year": "115", "exam_ordinal": "2"},
        }])
        self.assertEqual({"question_number": "54", "category": "藥師(一)", "subject": "藥學(一)",
                          "year": "115", "ordinal": "2"}, state.question_identities(["k1"])["k1"])
        # 這一支與題目區那一列（`workflow_rows` 的 summary）是**同一支投影**：同一筆候選題在兩個
        # 地方印出來的年次科目不可以不一樣。所以這裡直接比同一筆資料的兩個輸出。
        self.assertEqual(state.question_identities(["k1"])["k1"],
                         self.ui.question_identity(state.candidate_by_key["k1"]))

    def test_a_key_the_queue_no_longer_holds_is_answered_not_dropped(self):
        """負對照：查不到的那一筆要**在**（五個欄位都是空的），不是被安靜地拿掉。

        拿掉的話，畫面上那一則反問會退回印 key——而「看不出是哪一題」正是這一輪要修的缺陷；
        五個空欄位則讓畫面的字變成「查不到這一題」，那是真的答案。
        """
        state = self.state_with_rows([])
        resolved = state.question_identities(["gone"])
        self.assertIn("gone", resolved)
        self.assertEqual({"question_number": "", "category": "", "subject": "", "year": "",
                          "ordinal": ""}, resolved["gone"])

    def test_a_row_without_metadata_is_not_a_crash_and_not_a_guess(self):
        """舊佇列有 `metadata` 缺席的列（打包到一半的候選題）：欄位空著，而不是編一個年次出來。"""
        state = self.state_with_rows([{"candidate_key": "k1", "question_number": "7"}])
        self.assertEqual({"question_number": "7", "category": "", "subject": "", "year": "",
                          "ordinal": ""}, state.question_identities(["k1"])["k1"])

    def test_the_payload_carries_every_key_it_mentions(self):
        """`discuss_payload` 的 `questions` 涵蓋它提到過的**每一個** key：反問、原則的 evidence、
        代理自己標記的不一致題。少一個，那一則就退回印 key。"""
        state = object.__new__(self.ui.ReviewState)
        state.sql_review_enabled = False
        state.candidate_path = Path("nonexistent-candidates.jsonl")
        state.issue_path = None
        state.review_log = Path("nonexistent-review.jsonl")
        state.candidate_by_key = {
            "k-ask": {"candidate_key": "k-ask", "question_number": "5",
                      "metadata": {"normalized_category_name": "醫師(一)", "year": "114",
                                   "exam_ordinal": "1", "normalized_subject_name": "內科"}},
            "k-principle": {"candidate_key": "k-principle", "question_number": "9",
                            "metadata": {"normalized_category_name": "藥師(一)", "year": "115",
                                         "exam_ordinal": "2", "normalized_subject_name": "藥劑學"}},
        }
        state.principles_events = [principle_add("p1", "表格以紙本圖為準。", evidence=["k-principle"])]
        state.repair_questions_events = [
            {"action": "ask", "question_id": "rq1", "candidate_key": "k-ask",
             "question": "這一題要修嗎？", "reason": "紙本與抽取一致，人仍阻擋", "reviewer": "repair_agent",
             "created_at": "2026-09-25T10:49:52", "model": "qwen3.8-flash-next"}]
        state.filtered_candidate_payloads = lambda params: {"candidates": []}
        state.candidate_data_status = lambda: {}
        state.agent_progress = lambda: {"disagreements": [{"candidate_key": "k-ask", "why": "兩個模型不一致"}]}
        state.discuss_taxonomy = lambda: ({}, 0)
        payload = state.discuss_payload({"reviewType": "discuss"})
        self.assertEqual({"k-ask", "k-principle"}, set(payload["questions"]))
        self.assertEqual("第 5 題", "第 %s 題" % payload["questions"]["k-ask"]["question_number"])
        self.assertEqual("藥師(一)", payload["questions"]["k-principle"]["category"])
        # 這一頁要的是**一句話**，所以欄位到畫面那一段用真正的 JS 組一次（下一條）。
        self.assertEqual("醫師(一)", payload["questions"]["k-ask"]["category"])
        self.assertEqual("114", payload["questions"]["k-ask"]["year"])

    def test_the_screen_prints_the_paper_and_the_number_not_the_key(self):
        """畫面上的身分是「第 N 題 · 類別 · YYYY年第N次 · 科目」——與題目區 `#where` 同一個寫法。

        負對照：**key 不出現在那句話裡**（`03:1:question:q054` 就是這樣，121 則一樣）。
        """
        text = run_node("(() => { P.identities = {'moex:1:2:3:4:question:q054':"
                        " {question_number:'54', category:'藥師(一)', subject:'藥學(一)',"
                        " year:'115', ordinal:'2'}};"
                        " return whereText(identityOf('moex:1:2:3:4:question:q054')); })()")
        self.assertEqual("第 54 題 · 藥師(一) · 115年第2次 · 藥學(一)", text)
        self.assertNotIn("question", text)
        self.assertNotIn("moex", text)
        # 查不到就說查不到（不是印 key）：那一則仍然看得到，只是沒有身分。
        empty = run_node("(() => { P.identities = {};"
                         " return whereText(identityOf('moex:1:2:3:4:question:q999')); })()")
        self.assertEqual("", empty)
        self.assertNotIn("q999", empty)

    def test_the_left_column_splits_the_same_fields_into_three_lines(self):
        """左欄只有 238px：同一組欄位拆成題號／卷年次／科目，順序與中欄那句一樣。"""
        blocks = run_node("(() => { P.identities = {'k1': {question_number:'54', category:'藥師(一)',"
                          " subject:'藥學(一)', year:'115', ordinal:'2'}};"
                          " return whereBlocksHtml('k1'); })()")
        self.assertLess(blocks.index("第 54 題"), blocks.index("藥師(一)"))
        self.assertLess(blocks.index("藥師(一)"), blocks.index("藥學(一)"))
        self.assertIn("115年第2次", blocks)
        # 沒有 key 的那一列（種子裡的原則 p2）要說出來，不是畫一個空的格子。
        self.assertIn("沒有指定題目", run_node("whereBlocksHtml('')"))


class WhyTheMachineDidNotChangeItTests(unittest.TestCase):
    """「為什麼沒有自動改」：那一列要說得出原因，而原因只有一個來源。

    業主原文（2026-09-25）：「機械對比後面有一個『帶入修正』來告訴我可以怎麼修正了，可是你已經幫
    我修正了」「同樣情況，在block的題目，你也提出一堆建議，但是卻沒有改，為什麼會有這樣的差異」。

    兩個來源、順序固定：**代理的反問**（`candidate.repair_ask`，只有還沒被回答的）優先；沒有反問
    的那些說一句規則（機器只改人擋過的題）。這條測試同時釘住「反問的理由是機器自己寫的字，原封不
    動送上去」與「已經改過字的題目不會拿到這一句」。
    """

    @classmethod
    def setUpClass(cls):
        cls.ui = load_ui_module()

    #: 這一組測試只在乎「那一列畫什麼」，所以 key 用一個固定的字串，不借用別的測試檔的常數。
    KEY = "moex:108100:309:33:1:question:q004"

    def _payload(self, *, ask_events, review_events=(("block", "local"),)):
        row = {"candidate_key": self.KEY, "question_number": 4, "stem": "題幹", "options": [],
               "metadata": {}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "candidates.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n",
                                                   encoding="utf-8")
            (root / "question_review_events.jsonl").write_text(
                "".join(json.dumps({"candidate_key": self.KEY, "action": action,
                                    "reviewer": reviewer},
                                   ensure_ascii=False) + "\n" for action, reviewer in review_events),
                encoding="utf-8")
            (root / "question_repair_questions.jsonl").write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in ask_events),
                encoding="utf-8")
            state = self.ui.ReviewState(root / "candidates.jsonl", None,
                                        root / "question_review_events.jsonl",
                                        review_backend="jsonl")
            return state.candidate_payload(row)

    def _finding_html(self, **row):
        base = {"candidate_key": self.KEY, "review": {"action": "block"},
                "qbr_ai_finding": {"model": "m", "population": "dispute",
                                   "finding": {"verdict": "DEFECT", "where": "第 3 行",
                                               "fix": "改成紙本那個字"},
                                   "changes": [{"field": "stem", "from": "⻑", "to": "長"}]}}
        base.update(row)
        return run_node("findingHtml(%s, true)" % json.dumps(base, ensure_ascii=False))

    def test_the_row_carries_the_reason_the_machine_wrote_for_not_changing_it(self):
        reason = "紙本判讀被閘門擋住：第二次判讀的結論是 CARE（差異可能改變答案或判讀自相矛盾）"
        payload = self._payload(ask_events=[{"action": "ask", "question_id": "rq1",
                                             "candidate_key": self.KEY, "question": "請你看一眼紙本",
                                             "reason": reason, "reviewer": "repair_dispute_apply"}])
        self.assertEqual(reason, (payload.get("repair_ask") or {}).get("reason"))
        html = self._finding_html(repair_ask=payload["repair_ask"])
        self.assertIn("af-fence", html)
        self.assertIn("機器沒有自己改", html)
        self.assertIn(reason, html, "理由要原封不動：那是機器自己寫的字，不是畫面重講一次")

    def test_an_answered_ask_is_not_shown_as_still_open(self):
        ask = {"action": "ask", "question_id": "rq1", "candidate_key": self.KEY, "reason": "舊的理由",
               "question": "請你看一眼紙本", "reviewer": "repair_dispute_apply"}
        answer = {"action": "answer", "question_id": "rq1", "candidate_key": self.KEY,
                  "answer": "紙本是對的"}
        payload = self._payload(ask_events=[ask, answer])
        self.assertIsNone(payload.get("repair_ask"),
                          "反問已經被回答，這一題不再是機器停在這裡的地方")
        # 這一列的 `review.action` 是人的 `block`，所以也不會掉到「你還沒拒絕過」那一句：那一句
        # 是給**沒有人做過決定**的題目用的，套在這一題上會是錯的敘述。
        html = self._finding_html()
        self.assertNotIn("af-fence", html)
        self.assertNotIn("舊的理由", html)

    def test_a_question_nobody_rejected_says_the_rule_that_holds_it(self):
        # 272 題是這一種（量到的）：機器讀到了差異，但這一題沒有人拒絕過，所以整欄改寫這一條路
        # 根本沒有開始。畫面上要說的是**規則**（機器只改人擋過的題），而不是假裝有一個判斷。
        html = self._finding_html(review={})
        self.assertIn("af-fence", html)
        self.assertIn("機器只改你擋過的題", html)

    def test_a_question_the_machine_changed_gets_no_such_line(self):
        # 機器真的改過字：卡片下方那一列已經寫著「已標記：AI已修改・依紙本改字」，再一句
        # 「沒有自己改」會互相矛盾。
        html = self._finding_html(review={"action": "reset_review", "applied": "field",
                                          "applied_kind": "field"})
        self.assertNotIn("af-fence", html)
        self.assertNotIn("機器只改你擋過的題", html)

    def test_without_any_reading_to_compare_there_is_nothing_to_explain(self):
        # 沒有 `changes`（模型沒讀出差異）：沒有「建議卻沒改」這件事可以解釋。
        html = self._finding_html(qbr_ai_finding={
            "model": "m", "population": "dispute",
            "finding": {"verdict": "OK", "where": "第 1 行", "fix": "（沒說）"}})
        self.assertNotIn("af-fence", html)


class PrincipleGroupingTests(unittest.TestCase):
    """清單要分兩層：**狀態**（已核准／待你核准）與**來源**（人寫的／機器提案的）。

    業主原文（2026-09-25）：「原則區非常混亂」、新原則與舊邏輯重複。站上量到的事實：p6（人寫的，
    上下標）與 p8（機器提的，上下標）是同一件事各說一次，而它們在清單裡隔了好幾列；p9／p10 又
    是同一個 `change_class` 的新提案。分組不是排版偏好——同一個 `change_class` 相鄰，重複才看得
    出來。
    """

    PRINCIPLES = [
        {"principle_id": "p6", "text": "上下標的核對要看到字母。", "approved": True,
         "source": "comment_review", "created_at": "2026-09-24T11:47:05",
         "evidence": ["moex:103090:312:11:1:question:q054"]},
        {"principle_id": "p8", "text": "下標要補 <sub>。", "approved": True,
         "source": "feedback_learning", "change_class": "format_rule",
         "created_at": "2026-09-25T14:43:55", "evidence": ["moex:103090:312:22:1:question:q007"]},
        {"principle_id": "p10", "text": "紙本上的下標要寫成 <sub>。", "approved": False,
         "source": "feedback_learning", "change_class": "format_rule",
         "created_at": "2026-09-25T16:10:21", "evidence": ["moex:100030:103:0204:1:question:q027"]},
        {"principle_id": "p11", "text": "形近字要改回常用字。", "approved": False,
         "source": "feedback_learning", "change_class": "exact_ocr_rule",
         "created_at": "2026-09-25T16:24:08", "evidence": ["moex:103090:311:44:1:question:q057"]},
    ]

    def _html(self):
        return run_node("(() => { P.principles = %s;"
                        " return principlePrinciplesHtml(); })()"
                        % json.dumps({"principles": self.PRINCIPLES, "count": 4,
                                      "approved_count": 2, "pending_count": 2},
                                     ensure_ascii=False))

    def test_the_same_change_class_sits_together_so_a_repeat_is_visible(self):
        html = self._html()
        # p8（已核准）與 p10（待核准）是同一個 `change_class`：它們要在**同一組相鄰兩列**，
        # 否則「新原則跟舊原則邏輯類似」這件事只能靠把兩句話都讀完才看得出來。
        self.assertIn("機器從已確認的修復提案（格式（上下標標籤））", html)
        self.assertIn("機器從已確認的修復提案（字形（形近字、標點））", html)
        self.assertLess(html.index('id="pp_p8"'), html.index('id="pp_p10"'))
        between = html[html.index('id="pp_p8"'):html.index('id="pp_p10"')]
        self.assertNotIn('id="pp_p6"', between, "別的組的原則不該插在這一組中間")
        self.assertNotIn('id="pp_p11"', between, "不同 class 的不該插在這一組中間")
        # 組頭要說得出這一組裡幾條已核准、幾條待核准：狀態不再是一層標題，但也不能消失。
        self.assertIn("2 條（已核准 1／待你核准 1）", html)

    def test_the_human_principles_are_one_group_and_come_first(self):
        html = self._html()
        self.assertIn("人從註解寫的", html)
        self.assertLess(html.index("人從註解寫的"),
                        html.index("機器從已確認的修復提案"))
        self.assertLess(html.index('id="pp_p6"'), html.index('id="pp_p8"'),
                        "人寫的判斷是來源，排在機器提案前面")

    def test_a_group_is_sorted_approved_first(self):
        html = self._html()
        # 已核准的 p8 在待核准的 p10 之前——正在改題目文字的先看到。
        self.assertLess(html.index('id="pp_p8"'), html.index('id="pp_p10"'))

    def test_every_principle_is_still_drawn_exactly_once(self):
        html = self._html()
        for pid in ("p6", "p8", "p10", "p11"):
            self.assertEqual(1, html.count('id="pp_%s"' % pid), "%s 要被畫一次，不能漏也不能重" % pid)


if __name__ == "__main__":
    unittest.main()
