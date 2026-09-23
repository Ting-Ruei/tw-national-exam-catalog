# -*- coding: utf-8 -*-
"""套用「已由量測決定」的修復：字元替換，以及紙本判讀所能證明的修復。

這一支有兩條進來的路，而它們的可信度不同，測試也必須分開：

1. **dispute 自己帶著目標字元**（`substituted-ideograph`：`⻑` → `長`）。目標在爭議裡，
   所以這是一個代換，不是一個決定。
2. **紙本判讀**（模型轉錄的截圖）。這是**意見的來源**，而這個專案量過模型會在轉錄時
   重寫公式、截斷、甚至編造圖片說明。所以它只有在**每一個被改的字元都正好是某個偵測器
   已經標記的位置**時才能套用——見 `anchored_page_changes`。下面的負控制就是拿這一輪
   真實發生的三種壞損讀法當輸入，每一種都必須被拒絕。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import apply_dispute_repairs as apply_mod  # noqa: E402


def _question(stem="下列產品何者屬於吸收性基劑？", options=None, disputes=None):
    options = options or [{"key": k, "text": "選項" + k} for k in "ABCD"]
    return {
        "candidate_key": "moex:111020:305:33:1:question:q030",
        "question_number": 30,
        "stem": stem,
        "options": options,
        "answer": "A",
        "disputes": disputes or [],
    }


def _cyrillic_dispute():
    """The real `q030` shape: a Cyrillic letter where the paper prints a circled number."""
    stem = "下列產品何者屬於吸收性基劑（absorption bases）？ћAquabase  ќEucerin  ѝPlastibase  ўAquaphor"
    return _question(stem=stem, disputes=[{
        "kind": "substituted-script",
        "substitutions": [
            {"field": "stem", "position": 32, "char": "ћ", "script": "CYRILLIC"},
            {"field": "stem", "position": 43, "char": "ќ", "script": "CYRILLIC"},
            {"field": "stem", "position": 53, "char": "ѝ", "script": "CYRILLIC"},
            {"field": "stem", "position": 66, "char": "ў", "script": "CYRILLIC"},
        ]}])


# ---------------------------------------------------------------- anchored in, not out

def test_a_read_that_only_moves_flagged_characters_is_applied():
    question = _cyrillic_dispute()
    page = "下列產品何者屬於吸收性基劑（absorption bases）？①Aquabase  ②Eucerin  ③Plastibase  ④Aquaphor"
    subs = apply_mod.page_read_substitutions(question, [{"field": "stem", "page": page}])
    assert len(subs) == 4
    assert apply_mod.verify(question, subs) == []
    corrected = apply_mod.build_correction(question, subs)["stem"]
    assert "①Aquabase" in corrected and "④Aquaphor" in corrected
    assert "ћ" not in corrected


def test_a_read_that_inserts_a_table_is_refused():
    # The real `113020 q076` reading appended the acceptance-criteria table. It changes the *number*
    # of characters, which means every position after it moved - a rewrite, not a repair.
    stem = "有關血庫試劑允收標準之說明，下列何者正確？"
    question = _question(stem=stem, disputes=[])
    page = stem + "\n允收標準\n①Anti-A 256倍\n②Anti-B 512倍"
    assert apply_mod.page_read_substitutions(question, [{"field": "stem", "page": page}]) == []


def test_a_read_that_deletes_the_options_is_refused():
    # The real `115090 q053` reading truncated the stem to 65 characters and returned empty strings
    # for all four options. Applying it would have deleted the question's options.
    question = _question(stem="已知某抗生素之口服生體可用率為0.8，經口服該藥100 mg 後，"
                              "體內血中濃度經時變化為Cp＝45（e-0.17t－e-1.5t），"
                              "則其在體內之清除率為多少？",
                         options=[{"key": "A", "text": "5.67 L/min"},
                                  {"key": "B", "text": "5.67 mL/min"}],
                         disputes=[{"kind": "flattened-offset"}])
    page_stem = "已知某抗生素之口服生體可用率為 0.8，經口服該藥 100 mg 後，體內血中濃度經時變化為 Cₚ=45（e⁻⁰·¹⁷ᵗ – e⁻"
    changes = [{"field": "stem", "page": page_stem},
               {"field": "option A", "page": ""}, {"field": "option B", "page": ""}]
    assert apply_mod.page_read_substitutions(question, changes) == []


def test_a_read_of_a_flattened_offset_is_refused_because_nothing_is_flagged():
    # `C=5e-0.4t` -> `C=5e⁻⁰·⁴ᵗ` is a real difference and the reading is right about it - but no
    # detector flagged any position, because `flattened-offset` has no substitutions. Rewriting a
    # physics formula on the model's word alone is exactly what the anchor rule exists to stop; that
    # class is repaired at the reading (`extract._body_centre`), not here.
    question = _question(stem="某抗生素以靜脈注射400 mg 後，其血中濃度經時變化以C=5e-0.4t 描述。",
                         disputes=[{"kind": "flattened-offset"}])
    page = "某抗生素以靜脈注射 400 mg 後，其血中濃度經時變化以 C=5e⁻⁰·⁴ᵗ 描述。"
    assert apply_mod.page_read_substitutions(question, [{"field": "stem", "page": page}]) == []


def test_a_read_that_fixes_one_flagged_character_and_edits_prose_is_refused():
    # Partial anchoring is the dangerous middle: the one character the detector flagged is fixed, so
    # the diff looks like a successful repair, while the rest of the sentence is the model's rewrite.
    question = _cyrillic_dispute()
    page = ("下列產品何者屬於吸收性製劑（absorption bases）？①Aquabase  ②Eucerin  ③Plastibase  ўAquaphor")
    assert apply_mod.page_read_substitutions(question, [{"field": "stem", "page": page}]) == []


def test_a_stale_finding_refuses_instead_of_editing_the_wrong_character():
    # The dispute's positions were measured on an older reading; a sentence was prepended since. The
    # anchor check compares positions on the *stored* text, so it finds that the flagged indices no
    # longer hold the flagged characters and refuses the whole repair - rather than replacing whatever
    # now happens to sit at index 32.
    question = _question(stem="前面多了一段話。" + _cyrillic_dispute()["stem"],
                         disputes=_cyrillic_dispute()["disputes"])
    page = "前面多了一段話。" + "下列產品何者屬於吸收性基劑（absorption bases）？①Aquabase  ②Eucerin  ③Plastibase  ④Aquaphor"
    assert apply_mod.page_read_substitutions(question, [{"field": "stem", "page": page}]) == []
    # And the second guard, for the case where the positions *do* still match at apply time but the
    # text moves between measurement and write: `verify` slices the stored field and refuses.
    fresh = _cyrillic_dispute()
    good = apply_mod.page_read_substitutions(
        fresh, [{"field": "stem",
                 "page": "下列產品何者屬於吸收性基劑（absorption bases）？①Aquabase  ②Eucerin  ③Plastibase  ④Aquaphor"}])
    assert good, "同一個讀法套在不含前綴的原文上應該成立"
    good[0]["position"] += 3
    assert apply_mod.verify(fresh, good), "位置過期必須拒絕，不能改到別的字符"


# ---------------------------------------------------------------- idempotence

def test_a_repair_already_applied_is_not_applied_again():
    # The candidate text is deliberately not rewritten (the correction is an event overlay), so the
    # same `⻑ -> 長` is found again on every run. Without the signature check the tool would append a
    # duplicate repair each time; measured: 192 such events were already in the log.
    event = {"source": "qbr_dispute_apply",
             "changes": [{"field": "stem", "from": "⻑", "to": "長"}]}
    subs = [{"field": "stem", "position": 7, "before": "⻑", "after": "長", "rule": "page-read"}]
    assert apply_mod.applied_signature(event) == apply_mod.repair_signature(subs)


def test_a_different_repair_on_the_same_question_is_not_skipped():
    # If the text has moved since (a real second repair), the signature differs and the question is
    # repaired again - that is the whole point of comparing edits rather than remembering a flag.
    event = {"source": "qbr_dispute_apply",
             "changes": [{"field": "stem", "from": "⻑", "to": "長"}]}
    subs = [{"field": "stem", "position": 7, "before": "⻑", "after": "長", "rule": "page-read"},
            {"field": "stem", "position": 30, "before": "⻄", "after": "西", "rule": "page-read"}]
    assert apply_mod.applied_signature(event) != apply_mod.repair_signature(subs)
    # And a human's event is not mistaken for this tool's own past repair.
    assert apply_mod.applied_signature({"action": "block"}) is None


def test_a_human_decision_after_a_repair_does_not_erase_the_memory_of_it():
    # Measured 2026-09-23: the station's clock is UTC, the laptop's is UTC+8, so a person's `accept`
    # stamped `03:28:55` was appended after a repair stamped `11:25:44` on the same question. The
    # projection is last-line-wins, so `latest_events` returned the `accept` and the signature check
    # read `None` from it. The next `--page-read` run would then have appended a *second* identical
    # `reset_review`, re-opening a question a person had just accepted - silently undoing a human
    # decision. The signature must come from the tool's own last repair event, not the last event.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "events.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "candidate_key": "moex:q1", "action": "reset_review",
                "source": "qbr_dispute_apply", "created_at": "2026-09-23T11:25:44",
                "changes": [{"field": "stem", "from": "഻", "to": "長"}]}) + "\n")
            # A human decision, appended later in the file because the station clock is behind.
            handle.write(json.dumps({
                "candidate_key": "moex:q1", "action": "accept", "reviewer": "local",
                "created_at": "2026-09-23T03:28:55"}) + "\n")
        repairs = apply_mod.last_repair_signature(path)
    assert "moex:q1" in repairs, "人的決定不該把修理的記憶洗掉"
    subs = [{"field": "stem", "position": 7, "before": "഻", "after": "長", "rule": "page-read"}]
    assert apply_mod.applied_signature(repairs["moex:q1"]) == apply_mod.repair_signature(subs), \
        "比對必須拿工具自己的上一筆修復，而不是最後一筆事件"


def test_the_negative_control_the_latest_event_alone_would_lose_the_repair():
    # 負對照，寫成獨立一條：拿同一份檔，用「最後一筆事件」而不是「最後一筆修復」來比對，
    # 就會得到 None，於是重跑會多出一筆重複修復。實作上就是把 `last_repair_signature` 與
    # 一個只取最後一筆的讀法相比。
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "events.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "candidate_key": "moex:q1", "action": "reset_review",
                "source": "qbr_dispute_apply", "created_at": "2026-09-23T11:25:44",
                "changes": [{"field": "stem", "from": "഻", "to": "長"}]}) + "\n")
            handle.write(json.dumps({
                "candidate_key": "moex:q1", "action": "accept", "reviewer": "local",
                "created_at": "2026-09-23T03:28:55"}) + "\n")
        last_event = {}
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    event = json.loads(line)
                    last_event[event["candidate_key"]] = event
        # 在 `with` 内取值：離開 TemporaryDirectory 後檔案已經不在了（那是這條測試第一次寫錯的地方）。
        last_event_signature = apply_mod.applied_signature(last_event["moex:q1"])
        repair_signature = apply_mod.applied_signature(
            apply_mod.last_repair_signature(path)["moex:q1"])
    assert last_event_signature is None
    assert repair_signature is not None


def test_the_page_read_repairs_are_exempt_from_the_substitution_whitelist():
    # `--page-read` is how the confirmed readings reach the apply step; the `APPLICABLE` tuple is the
    # *dispute-carried* whitelist and deliberately does not contain `substituted-script`. If the two
    # ever collapse into one, the readings would either never apply or apply unanchored.
    assert "substituted-ideograph" in apply_mod.APPLICABLE
    assert "substituted-script" not in apply_mod.APPLICABLE
    assert "page-read" not in apply_mod.APPLICABLE
