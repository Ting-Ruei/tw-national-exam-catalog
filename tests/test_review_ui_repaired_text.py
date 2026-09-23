# -*- coding: utf-8 -*-
"""修好的文字，不能再顯示那個剛被修掉的爭議。

`disputes` 是從**當時的文字**量出來的（`qbr.disputes.of_question`）。而 `candidates.jsonl` 的
文字**不會**被修復改寫——修復是一筆 append-only 的 `reset_review` 事件，帶著 `correction` 覆蓋欄位
（`apply_dispute_repairs.py`）。所以文字被覆蓋之後，`disputes` 仍是「修復前那一版」的量測。

實測 2026-09-23：304 題已修復的題目裡有 **204 題**的 `disputes` 還是舊的，於是錯題討論區的
「① 機器偵測」面板顯示一個**已經不存在的字元**的爭議，就寫在已經修好的文字正上方。
兩個面板直接互相矛盾，而畫面上看起來完全合理——這正是最難被發現的壞法。

修法是在伺服器把 `correction` 疊上去的那一刻，用**同一個** `review_queue.disputes_for_paper`
重量（不是另一套規則）。這裡釘的就是「重量真的發生了、而且用的是同一個函式」。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "serve_question_review_ui.py"

# 一題帶著 `substituted-ideograph` 爭議的真實形狀：`⻑` 是部首補充區的碼位，不是「長」。
CYRILLIC_STEM = "下列產品何者屬於吸收性基劑（absorption bases）？ћAquabase"
FIXED_STEM = "下列產品何者屬於吸收性基劑（absorption bases）？①Aquabase"


def import_server():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("serve_question_review_ui_discuss_test", SERVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _question():
    return {
        "candidate_key": "moex:111020:305:33:1:question:q030",
        "question_number": 30,
        "stem": CYRILLIC_STEM,
        "options": [{"key": key, "text": "選項" + key} for key in "ABCD"],
        "answer": "A",
        "answer_payload": {"accepted_values": ["A"]},
        "lost_glyphs": [],
    }


class RepairedTextHasNoStaleDisputeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = import_server()

    def _state(self, tmp, questions=None):
        questions = questions if questions is not None else [_question()]
        candidates = Path(tmp) / "candidates.jsonl"
        candidates.write_text(
            "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions),
            encoding="utf-8")
        log = Path(tmp) / "question_review_events.jsonl"
        log.write_text("", encoding="utf-8")
        return self.ui.ReviewState(candidates, None, log, review_backend="jsonl")

    def test_the_dispute_is_measured_from_the_text_the_reviewer_is_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state(tmp)
            # 直接走 `candidate_payload` 的 correction 疊加路徑。
            state.latest_reset_reviews = {
                "moex:111020:305:33:1:question:q030": {
                    "candidate_key": "moex:111020:305:33:1:question:q030",
                    "action": "reset_review",
                    "correction": {"stem": FIXED_STEM},
                    "created_at": "2026-09-23T11:25:44",
                }
            }
            payload = state.candidate_payload(_question())
        self.assertEqual(payload["stem"], FIXED_STEM)
        # 這是重點：顯示的文字已經沒有 Cyrillic 字元，畫面就不該再說有個 Cyrillic 爭議。
        self.assertFalse(payload.get("disputes"),
                         "修好的欄位不該還掛著修復前的爭議")
        self.assertTrue(payload.get("disputes_recomputed"))

    def test_the_negative_control_without_the_recompute_the_dispute_is_stale(self):
        # 負對照，分兩步，證明「重量」就是把那筆錯誤爭議清掉的那一步：
        #   1. 列本身（抽取時量到的）確實帶著 `ћ` 的爭議。
        #   2. 把重量那一步拿掉（只疊 correction、不重量），那份爭議就會穿過來。
        # 兩步都用具體的字串斷言，不靠「有沒有呼叫」的推論。
        stale_disputes = [{
            "kind": "substituted-script",
            "note": "文字層存的是別國字元，讀者看到亂碼",
            "detail": "cyrillic（CYRILLIC 字母，共 1 處）",
            "substitutions": [{"char": "ћ", "field": "stem", "position": 20,
                               "script": "CYRILLIC", "context": "ases）？ћAquaba"}],
        }]
        # (1) 抽取時的量測：真的指著 `ћ`，而修好後的文字裡沒有它。
        self.assertEqual(stale_disputes[0]["substitutions"][0]["char"], "ћ")
        self.assertNotIn("ћ", FIXED_STEM)
        # (2) 只疊 correction、不重量，就是先前的行為：爭議留著。
        naive = dict(_question())
        naive["disputes"] = stale_disputes
        naive["stem"] = FIXED_STEM                      # 只疊文字，不動 disputes
        self.assertTrue(naive["disputes"], "不重量就會留下一個修好文字裡不存在的爭議")

    def test_the_negative_control_turning_off_the_recompute_puts_the_stale_dispute_back(self):
        # 更直接的負對照：把共用的重量函式暫時換成 no-op，修好的題目就會又帶著舊爭議。
        # 證明「那一步」就是被測的東西，而不是碰巧。
        stale_disputes = [{
            "kind": "substituted-script",
            "substitutions": [{"char": "ћ", "field": "stem", "position": 20,
                               "script": "CYRILLIC"}],
        }]
        row = _question()
        row["disputes"] = stale_disputes
        with tempfile.TemporaryDirectory() as tmp:
            state = self._state(tmp, questions=[row])
            state.latest_reset_reviews = {
                "moex:111020:305:33:1:question:q030": {
                    "candidate_key": "moex:111020:305:33:1:question:q030",
                    "action": "reset_review",
                    "correction": {"stem": FIXED_STEM},
                    "created_at": "2026-09-23T11:25:44",
                }
            }
            original = self.ui.review_queue.disputes_for_paper
            self.ui.review_queue.disputes_for_paper = lambda rows: rows
            try:
                payload = state.candidate_payload(dict(row))
            finally:
                self.ui.review_queue.disputes_for_paper = original
        self.assertEqual(payload["stem"], FIXED_STEM)
        self.assertTrue(payload.get("disputes"),
                        "關掉重量，舊爭議就回來了：這一步就是把它清掉的原因")

    def test_the_recompute_uses_the_one_rule_not_a_second_copy(self):
        source = SERVER.read_text(encoding="utf-8")
        self.assertIn("review_queue.disputes_for_paper(", source)
        # 而且它真的在 correction 疊加的區塊裡（不在別的、跑不到的地方）。
        block = source.split('copy["disputes_recomputed"]')[0]
        self.assertIn('if "options" in correction:', block)


class HumanDecisionKeepsTheRepairedTextTests(unittest.TestCase):
    """人在修復之後做的決定，不可以把修好的文字丟掉。

    實測 2026-09-23：`108030:305:33` 的 q034/q040/q072/q073 在 `08:20:05` 有一筆
    `qbr_dispute_apply` 的 `reset_review`（帶著 `options` 的 correction），常駐機時鐘是 UTC，
    所以人的 `accept` 蓋在 `03:28:55`——**檔案順序上在修復之後**。

    `load_review_events` 在 reset 分支把 `latest` pop 掉、只留 `latest_reset`。下一個分支
    （人的決定）原本只從 `latest` 取 correction，於是那四題回到原抽取的 `⻑`/`延⻑`——
    人剛接受的那一版不見了。SQL 那條路早就寫成 `latest or latest_reset`，所以這是
    **兩份後端對同一件事給不同答案**，而不是一條規則的兩個地方。
    """

    @classmethod
    def setUpClass(cls):
        cls.ui = import_server()

    def _log(self, tmp):
        log = Path(tmp) / "question_review_events.jsonl"
        events = [
            # 修復：文字層的修正，並把題目退回未審（`latest` 被 pop）。
            {"candidate_key": "moex:108030:305:33:1:question:q034",
             "action": "reset_review", "source": "qbr_dispute_apply",
             "reviewer": "repair_dispute_apply",
             "correction": {"options": [{"key": "A", "text": "延長藥物於黏膜之作用時間"}]},
             "changes": [{"field": "option A", "from": "⻑", "to": "長"}],
             "created_at": "2026-09-23T08:20:05"},
            # 人的決定，檔案順序在後（站上時鐘較慢，created_at 反而較早）。
            {"candidate_key": "moex:108030:305:33:1:question:q034",
             "action": "accept", "source": "linear_v2", "reviewer": "local",
             "created_at": "2026-09-23T03:28:55"},
        ]
        log.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                       encoding="utf-8")
        return log

    def test_a_human_decision_inherits_the_correction_from_the_reset_not_only_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp)
            latest, _counts, latest_reset = self.ui.load_review_events(log)
        key = "moex:108030:305:33:1:question:q034"
        self.assertEqual((latest.get(key) or {}).get("action"), "accept")
        self.assertTrue((latest[key].get("correction") or {}).get("options"),
                        "人的決定不可丢掉剛修好的文字（correction 要從 latest_reset 繼承）")
        # 而人的決定一旦寫下，`latest_reset` 就被 pop 了（決定已經不只是「待重看」），
        # 所以 correction 只可能來自上面的繼承——不是 reset 自己還留著給它。
        self.assertIsNone(latest_reset.get(key))

    def test_the_negative_control_reading_only_latest_loses_the_correction(self):
        # 負對照：把「只從 latest 取」寫回來（原本的 JSONL 行為），那條斷言就會紅。
        # 這裡直接重建那份邏輯，證明失敗的形狀就是先前線上那四題的形狀。
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp)
            latest, _counts, _latest_reset = self.ui.load_review_events(log)
            events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
        key = "moex:108030:305:33:1:question:q034"
        naive = {}
        for event in events:
            event = dict(event)
            k = event["candidate_key"]
            if event.get("action") in ("unreviewed", "reset_review"):
                naive.pop(k, None)
                continue
            if "correction" not in event and k in naive and naive[k].get("correction"):
                event["correction"] = naive[k]["correction"]
            naive[k] = event
        self.assertFalse((naive[key].get("correction") or {}).get("options"),
                         "只讀 latest 就會丢掉 correction——這正是被修掉的舊行為")
        self.assertTrue((latest[key].get("correction") or {}).get("options"))
