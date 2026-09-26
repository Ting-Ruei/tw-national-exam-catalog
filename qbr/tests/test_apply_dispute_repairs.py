# -*- coding: utf-8 -*-
"""套用「已由量測決定」的修復：字元替換，以及紙本判讀所能證明的修復。

這一支有三條進來的路，而它們的可信度不同，測試也必須分開：

1. **dispute 自己帶著目標字元**（`substituted-ideograph`：`⻑` → `長`）。目標在爭議裡，
   所以這是一個代換，不是一個決定。
2. **紙本判讀的字元級修復**（模型轉錄的截圖）。這是**意見的來源**，而這個專案量過模型會在
   轉錄時重寫公式、截斷、甚至編造圖片說明。所以它只有在**每一個被改的字元都正好是某個
   偵測器已經標記的位置**時才能套用——見 `anchored_page_changes`。下面的負控制就是拿這一輪
   真實發生的三種壞損讀法當輸入，每一種都必須被拒絕。
3. **紙本判讀的整欄替換**（`--page-read`，`applied="field"`）。這一條不是錨在偵測器上（偵測器
   只認得它自己表裡的字元，所以它對多數真實判讀完全沉默），而是錨在**人的退件**（站得住的
   `block`／`needs_review`，後面沒有 `accept`，見 `standing_rejections`）與**兩個量測**上
   （對齊率、長度比，見 `PAGE_READ_ALIGN_MIN`）。人退過是閘門的一半，量測是另一半；下面每
   一條正向測試都配一個負控制，而負控制證明的是「同一份輸入在舊的路上什麼都不會發生」或
   「同一份輸入只要少了那個人為條件就不會發生」。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import apply_dispute_repairs as apply_mod  # noqa: E402
import repair_loop  # noqa: E402


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


#: 判讀時的截圖，和 `confirm_dispute.queue_relative` 寫進 record 的拼法一樣（queue 相對）。
CROP = "review-ui/crops/111020_物理治療/q030-dispute.png"


def _reading(question, field, stored, page, *, crop=CROP, error=None, population="dispute",
             verdict="TRUST"):
    """一份紙本判讀的紀錄，形狀照 `confirm_dispute` 寫的（`stored`/`page` 是原文那一對）。

    `from`/`to` 是 NFKC＋去空白後的比較形，這支工具不讀它們（它讀 `stored`/`page`），但紀錄裡
    真的有，所以 fixture 也有——不然就測不到「事件裡的 `from` 是原文、不是比較形」這一件事。

    `verdict` 是第二次判讀的結論（`orchestration.verdict`）。預設 `TRUST`，也就是「兩台引擎都說這
    只是機械性的符號還原」；`None` 表示**根本沒有第二次判讀**，那時整條 `orchestration` 都不存在
    （實測：舊的 finding 就是這個形狀）。
    """
    record = {
        "candidate_key": question["candidate_key"], "question_number": question["question_number"],
        "population": population, "crop": crop, "error": error,
        "created_at": "2026-09-24T10:00:00",
        "changes": [{"field": field, "from": stored, "to": page, "stored": stored, "page": page}],
    }
    if verdict is not None:
        record["orchestration"] = {"verdict": verdict}
    return record


def _block_event(question, notes="這一題的選項少了字"):
    """一個人的退件。站得住的意思是：後面沒有 `accept`。"""
    return {"candidate_key": question["candidate_key"], "action": "block", "reviewer": "local",
            "notes": notes, "created_at": "2026-09-24T09:00:00"}


def _write_queue(root, candidates, events=(), findings=()):
    review_ui = os.path.join(root, "review-ui")
    os.makedirs(review_ui, exist_ok=True)
    for name, rows in (("candidates.jsonl", candidates),
                       ("question_review_events.jsonl", events),
                       ("question_ai_findings.jsonl", findings)):
        with open(os.path.join(review_ui, name), "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return root


def _run(queue, capsys, *extra):
    """跑一次真正的 CLI（`main`），回傳 `(exit code, 印出來的東西)`。"""
    argv = sys.argv
    sys.argv = ["apply_dispute_repairs", "--queue", queue, "--page-read", *extra]
    try:
        code = apply_mod.main()
    finally:
        sys.argv = argv
    return code, capsys.readouterr().out


def _appended_events(root):
    """這一輪**寫進去的**修復事件（佇列裡本來就有人的 `block`，那不是「寫出來的」）。"""
    path = os.path.join(root, "review-ui", "question_review_events.jsonl")
    with open(path, encoding="utf-8") as handle:
        return [e for e in (json.loads(line) for line in handle if line.strip())
                if e.get("action") == "reset_review"]


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


def test_a_read_that_also_folds_kangxi_radicals_is_applied():
    # 2026-09-24，工作站實測 `108030:311:11 q070` 的選項 C：偵測器只 flag 了 `⻑`（`RADICAL_SUPPLEMENT_MEANS`
    # 只收 CJK Radicals Supplement），而模型的轉錄在同一個欄位裡還把 `⽽`（康熙部首）寫成 `而`。
    # 那一個位置沒有人 flag，但 Unicode 自己說 `⽽` 與 `而` 是同一個字（NFKC 相同），所以那是同一個字的
    # 另一種寫法，不是模型在改句子。這一條修好之前整個紙本判讀路徑等於沒有出口：站上 `--page-read`
    # 的可用修復數是 **0**，而每一輪都還在寫新的判讀。
    stored = "膝關節⻑期無活動，會導致膝關節伸直攣縮，進⽽在擺盪期缺乏膝屈曲"
    question = _question(stem=stored, disputes=[{
        "kind": "substituted-ideograph",
        "substitutions": [{"field": "stem", "position": stored.index("⻑"),
                           "char": "⻑", "means": "長"}]}])
    page = "膝關節長期無活動，會導致膝關節伸直攣縮，進而在擺盪期缺乏膝屈曲"
    subs = apply_mod.page_read_substitutions(question, [{"field": "stem", "page": page}])
    assert apply_mod.verify(question, subs) == []
    assert {(s["before"], s["after"]) for s in subs} == {("⻑", "長"), ("⽽", "而")}
    assert apply_mod.build_correction(question, subs)["stem"] == page


def test_the_negative_control_an_unflagged_prose_edit_is_still_refused():
    # 負對照，和上一條只差一個字：多改的那個位置不是相容分解（基劑→製劑）。放寬錨定不等於放行
    # 「模型順手改句子」，所以這一種必須維持拒絕。
    stored = "下列產品何者屬於吸收性基劑？⻑期使用"
    question = _question(stem=stored, disputes=[{
        "kind": "substituted-ideograph",
        "substitutions": [{"field": "stem", "position": stored.index("⻑"),
                           "char": "⻑", "means": "長"}]}])
    page = "下列產品何者屬於吸收性製劑？長期使用"
    assert apply_mod.page_read_substitutions(question, [{"field": "stem", "page": page}]) == []


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

def test_the_same_substitution_from_both_sources_becomes_one_edit():
    # 兩個來源會指到同一個位置：dispute 帶的目標（策展的表）與紙本判讀的轉錄。實測 `⻑`→`長` 兩邊
    # 都給得出，而重複的編輯會進到收據、也會讓 `build_correction` 對同一格套兩次。
    dispute_side = [{"field": "stem", "position": 3, "before": "⻑", "after": "長",
                     "rule": "substituted-ideograph"}]
    page_side = [{"field": "stem", "position": 3, "before": "⻑", "after": "長", "rule": "page-read"},
                 {"field": "stem", "position": 21, "before": "⽽", "after": "而", "rule": "page-read"}]
    merged = apply_mod.merge_substitutions(dispute_side + page_side)
    assert [(s["position"], s["before"], s["after"], s["rule"]) for s in merged] == [
        (3, "⻑", "長", "page-read"), (21, "⽽", "而", "page-read")]


def test_the_negative_control_two_measurements_that_disagree_refuse_the_question():
    # 同一位置、兩種說法：不是「哪一筆對」而是兩個量測彼此矛盾。這種題目不該由工具靜默選邊。
    dispute_side = [{"field": "stem", "position": 3, "before": "⻑", "after": "長",
                     "rule": "substituted-ideograph"}]
    page_side = [{"field": "stem", "position": 3, "before": "⻑", "after": "常", "rule": "page-read"}]
    assert apply_mod.merge_substitutions(dispute_side + page_side) is None


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


# ---------------------------------------------------------------- 「已經符合」不是「過期」

# 站上 2026-09-25 量到的那一群（**192 題**被拒，其中 186 題的判讀是 TRUST、自己也寫好了改法）：
# 這一列的 `disputes` 是建佇列時寫下來的快照，**改過之後沒有重測**，所以欄位裡的字已經是紙本那個字
# （`parser_original` 還留著舊的），爭議卻還在說舊的那一個。`verify` 拿位置去切現在的文字，看到的是
# 「已經對的字」，於是回一句「dispute 已過期」＝**拒絕**，而整題一起不動的規則又把同一題其他真的還
# 沒改的欄位一起擋掉。業主看到的就是「你明明有寫應該怎麼改，他們還是卡在這邊」。
_ALREADY_FIXED = "膝關節長期無活動，會導致膝關節伸直攣縮"
_STALE_DISPUTE = [{
    "kind": "substituted-ideograph",
    "substitutions": [{"field": "stem", "position": _ALREADY_FIXED.index("長"),
                       "char": "⻑", "means": "長"}]}]


def test_a_claim_whose_target_is_already_in_the_field_is_satisfied():
    question = _question(stem=_ALREADY_FIXED, disputes=_STALE_DISPUTE)
    sub = apply_mod.substitutions_for(question)[0]
    assert apply_mod.satisfied_sub(question, sub) is True
    # 而且它不是拒絕：這一格沒有東西要寫，`verify` 不該把它當成「過期」擋掉整題。
    assert apply_mod.verify(question, [sub]) == []


def test_the_negative_control_a_moved_position_is_still_expired():
    # 位置真的過期的那一種要繼續拒絕：把位置挪到別的字上，那裡既不是舊字也不是新字。
    question = _question(stem=_ALREADY_FIXED, disputes=_STALE_DISPUTE)
    sub = dict(apply_mod.substitutions_for(question)[0])
    sub["position"] = sub["position"] + 2          # `長期` 的「期」
    assert apply_mod.satisfied_sub(question, sub) is False
    assert apply_mod.verify(question, [sub]), "挪過位置之後必須拒絕（負控制證明上面那條不是恆真）"


def test_an_already_fixed_field_no_longer_blocks_the_rest_of_the_question(tmp_path, capsys):
    """這一條是 192 題的出口：已經對的那一格不再擋住同一題真正還沒改的欄位。"""
    # 選項要夠長才走得過整欄替換的兩個量測（對齊率 ≥0.10、長度比 ≤1.60）；短欄位本來就會被擋，
    # 那是另一條規則，不該混進這一條。
    option = "某抗生素以靜脈注射400 mg 後，其血中濃度經時變化以C=5e-0.4t 描述。"
    question = _question(stem=_ALREADY_FIXED, disputes=_STALE_DISPUTE,
                         options=[{"key": "A", "text": option}, {"key": "B", "text": option},
                                  {"key": "C", "text": option}, {"key": "D", "text": option}])
    page = "某抗生素以靜脈注射 400 mg 後，其血中濃度經時變化以 C=5e<sup>-0.4t</sup> 描述。"
    _write_queue(str(tmp_path), [question],
                 events=[_block_event(question)],
                 findings=[_reading(question, "option B", option, page)])
    code, out = _run(str(tmp_path), capsys, "--apply")
    assert code == 0, out
    assert "已符合       : 1 fields" in out, out
    assert "to repair    : 1" in out, out
    assert "REFUSED" not in out, out
    event = _appended_events(str(tmp_path))[0]
    assert event["correction"]["options"][1]["text"] == page


def test_the_negative_control_a_question_whose_only_claim_is_satisfied_plans_nothing(tmp_path, capsys):
    # 只有那一格「已經符合」的題目：這一輪沒有東西要寫，而且**不進 REFUSED**（它不是缺陷）。
    question = _question(stem=_ALREADY_FIXED, disputes=_STALE_DISPUTE)
    _write_queue(str(tmp_path), [question], events=[_block_event(question)])
    code, out = _run(str(tmp_path), capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "已符合       : 1 fields" in out, out
    assert _appended_events(str(tmp_path)) == []
    assert "REFUSED" not in out, out


# ---------------------------------------------------------------- 整欄替換：閘門的兩半

# 這一對是真的（`111020:305:33 q071` 的判讀）：紙本讀進來的字一樣，只有空白與指數的寫法不同。
# 偵測器對它完全沉默——沒有任何位置被標記，因為被「改」的不是某個字元，是整條式子的寫法。
_UNFLAGGED_STEM = "某抗生素以靜脈注射400 mg 後，其血中濃度經時變化以C=5e-0.4t 描述。"
#: 站上 18:06 那一輪真的寫下去的形狀：判讀把指數寫成 Unicode 上標字元（`⁻⁰·⁴ᵗ`）。**現在會拒絕**
#: ——平台用 `<sub>`／`<sup>` 排版，而那些字元會掉到別的字型（見 `unicode_sub_sup_complaint`）。
_UNFLAGGED_PAGE = "某抗生素以靜脈注射 400 mg 後，其血中濃度經時變化以 C=5e⁻⁰·⁴ᵗ 描述。"
#: 同一題判讀的**想要的**寫法：字一個都沒動，只是把紙本排版的那一段包進 `<sup>`（見
#: `markup_fidelity_complaints`）。空白的位置不同不影響——兩個量測本來就在非空白字元上量。
_UNFLAGGED_MARKUP_PAGE = \
    "某抗生素以靜脈注射 400 mg 後，其血中濃度經時變化以 C=5e<sup>-0.4t</sup> 描述。"


def test_a_read_that_fixes_an_unflagged_sentence_is_applied_on_a_rejected_question(tmp_path, capsys):
    # 這是新的那一條路，而它開的前提有兩個，這一條測的是人為的那一半（另一條測量測）。
    # 站上的實測：閘門關著時 `--page-read` 只找得到 63 筆修復，閘門打開後多出 41 筆整欄替換，
    # 全部落在人退過的題目上。
    question = _question(stem=_UNFLAGGED_STEM)
    _write_queue(str(tmp_path), [question],
                 events=[_block_event(question)],
                 findings=[_reading(question, "stem", _UNFLAGGED_STEM, _UNFLAGGED_MARKUP_PAGE)])
    code, out = _run(str(tmp_path), capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 1" in out, out
    event = _appended_events(str(tmp_path))[0]
    correction = event["correction"]
    assert event["action"] == "reset_review"
    assert event["reviewer"] == "repair_dispute_apply"
    assert event["applied"] == "field"
    assert event["crop"] == CROP
    # 事件的 `from` 是**檔案裡原來的整欄文字**、`to` 是紙本那一欄——產檔那一邊要比對 `from`，
    # 而審核畫面上要看到的是同一個形狀。
    assert event["changes"] == [{"field": "stem", "from": _UNFLAGGED_STEM,
                                 "to": _UNFLAGGED_MARKUP_PAGE}]
    assert correction["stem"] == _UNFLAGGED_MARKUP_PAGE
    # 這一筆不是「只有碼位錯」的那一類，所以旗標不出現（見 `is_normalisation`）。
    assert "normalisation" not in event


def test_the_negative_control_the_detector_anchor_is_silent_for_that_read():
    # 負對照：同一份輸入在舊的路上（`anchored_page_changes`，偵測器標記的位置）什麼都得不到。
    # 這一條如果壞了，就代表上面那條測試沒有在測新的東西。
    assert apply_mod.anchored_page_changes(_UNFLAGGED_STEM, _UNFLAGGED_MARKUP_PAGE, set()) is None
    question = _question(stem=_UNFLAGGED_STEM, disputes=[{"kind": "flattened-offset"}])
    changes = [{"field": "stem", "stored": _UNFLAGGED_STEM, "page": _UNFLAGGED_MARKUP_PAGE}]
    assert apply_mod.page_read_substitutions(question, changes) == []
    # 而少了人的退件，新的那條路也不會開：同一個讀法、同一個問題，只有 `human_rejected` 不同。
    assert apply_mod.page_read_substitutions(question, changes, human_rejected=False,
                                             verdict="TRUST") == []
    subs = apply_mod.page_read_substitutions(question, changes, human_rejected=True, verdict="TRUST")
    assert [s["field"] for s in subs] == ["stem"] and subs[0]["replace"] is True


def test_the_station_read_that_wrote_unicode_superscripts_is_now_refused(tmp_path, capsys):
    # 站上 18:06 真的套用過的那一份判讀（`111020:305:33 q071` 的 `⁻⁰·⁴ᵗ`）。主人報的是「字型異常」，
    # 而平台的排版法是 `<sub>`／`<sup>`（站上實測：6540 筆已經是標記，只有 55 筆帶著這些字元），
    # 所以這一類從今天起不准再寫進去——擋住它的就是條文 1，不是量測（兩個量測都過）。
    question = _question(stem=_UNFLAGGED_STEM)
    changes = [{"field": "stem", "stored": _UNFLAGGED_STEM, "page": _UNFLAGGED_PAGE}]
    why = []
    assert apply_mod.page_read_substitutions(question, changes, human_rejected=True, refused=why,
                                             verdict="TRUST") == []
    assert any("Unicode 上下標字元" in line and "⁻⁰⁴ᵗ" in line for line in why), why
    # `ᵗ`（U+1D57）不在主人指名的那份清單裡，而它就在站上那些欄位裡——所以偵測認的是 Unicode 自己
    # 的相容分解（`<super> 0074`），不是一張只收一半的表。
    assert apply_mod._sub_sup_char("ᵗ") and apply_mod._sub_sup_char("ₐ")
    assert not apply_mod._sub_sup_char("·") and not apply_mod._sub_sup_char("t")
    alignment, factor = apply_mod._page_read_shape(_UNFLAGGED_STEM, _UNFLAGGED_PAGE)
    assert alignment >= apply_mod.PAGE_READ_ALIGN_MIN
    assert 1.0 / apply_mod.PAGE_READ_LENGTH_FACTOR <= factor <= apply_mod.PAGE_READ_LENGTH_FACTOR
    # 同一份輸入在舊的路上（偵測器錨定那條）也是沉默的：擋住它的只可能是這條新條文。
    assert apply_mod.anchored_page_changes(_UNFLAGGED_STEM, _UNFLAGGED_PAGE, set()) is None


def test_the_same_read_on_a_never_reviewed_question_is_held_back(tmp_path, capsys):
    # 閘門的另一半是「有人退過」。同一個讀法、同一份候選，只差有沒有人按過 `block`。
    never = _question(stem=_UNFLAGGED_STEM)
    never["candidate_key"] = "moex:111020:305:33:1:question:q031"
    never_root = _write_queue(str(tmp_path / "never"), [never],
                              findings=[_reading(never, "stem", _UNFLAGGED_STEM,
                                                 _UNFLAGGED_MARKUP_PAGE)])
    code, out = _run(never_root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "held back    : 1" in out, out
    assert _appended_events(never_root) == [], "沒有人退過的題目不該被機器改字"

    blocked = _question(stem=_UNFLAGGED_STEM)
    blocked["candidate_key"] = "moex:111020:305:33:1:question:q032"
    blocked_root = _write_queue(str(tmp_path / "blocked"), [blocked],
                                events=[_block_event(blocked)],
                                findings=[_reading(blocked, "stem", _UNFLAGGED_STEM,
                                                   _UNFLAGGED_MARKUP_PAGE)])
    _, out = _run(blocked_root, capsys, "--apply")
    assert "to repair    : 1" in out, out
    assert len(_appended_events(blocked_root)) == 1


#: `115090:305:0403 q053` 的判讀逐字重建：題幹截在 65 個字（原文 154 個），四個選項讀成空字串。
_HALLUCINATED_STEM = ("已知某抗生素之口服生體可用率為0.8，經口服該藥100 mg 後，"
                      "體內血中濃度經時變化為C<sub>p</sub>＝45（e-0.17t－e<sup>-</sup> 1.5t），"
                      "該藥無flip-flop 現象，則其在體內之清除率為多少？（C<sub>p</sub>：mg/L，t：hr）")
_HALLUCINATED_PAGE = ("已知某抗生素之口服生體可用率為 0.8，經口服該藥 100 mg 後，"
                      "體內血中濃度經時變化為 Cₚ=45（e⁻⁰·¹⁷ᵗ – e⁻")
#: 同一個讀法，只是沒有被截斷（負控制的另一半：拒絕是量出來的，不是閘門剛好關著）。
_COMPLETE_PAGE = ("已知某抗生素之口服生體可用率為 0.8，經口服該藥 100 mg 後，"
                  "體內血中濃度經時變化為 Cₚ=45（e⁻⁰·¹⁷ᵗ – e⁻¹·⁵ᵗ），該藥無 flip-flop 現象，"
                  "則其在體內之清除率為多少？（Cₚ：mg/L，t：hr）")


def _hallucination_question():
    return _question(stem=_HALLUCINATED_STEM,
                     options=[{"key": "A", "text": "5.67 L/min"}, {"key": "B", "text": "5.67 mL/min"},
                              {"key": "C", "text": "0.34 L/min"}, {"key": "D", "text": "0.34 mL/min"}],
                     disputes=[{"kind": "flattened-offset"}])


def test_a_read_that_truncates_the_field_is_refused_with_its_reason():
    # 站上真實發生過一次的壞損讀法：長度比 0.55、四個選項被讀成空字串。套下去就是把題幹截掉、
    # 把選項刪掉——這一條是這條新路上最貴的一種錯，也是為什麼長度比是兩個量測之一。
    # （2026-09-24 起兩個量測都量拆掉標記後的字，所以這一欄帶著 `<sub>`／`<sup>` 的原文量出來是
    # 0.55 而不是含標記時的 0.42——106 個字對 58 個字。還在 0.62 以下，所以讀法照樣被擋下。）
    question = _hallucination_question()
    changes = [{"field": "stem", "stored": _HALLUCINATED_STEM, "page": _HALLUCINATED_PAGE}] + [
        {"field": "option %s" % key, "stored": text, "page": ""}
        for key, text in (("A", "5.67 L/min"), ("B", "5.67 mL/min"),
                          ("C", "0.34 L/min"), ("D", "0.34 mL/min"))]
    why = []
    assert apply_mod.page_read_substitutions(question, changes, human_rejected=True,
                                             refused=why, verdict="TRUST") == []
    assert any("長度比 0.55" in line for line in why), why
    assert sum("空字串" in line for line in why) == 4, why

    # 負控制：同一個讀法、同一個人為條件，只是沒有被截斷——**兩個量測都過**，可見上面拒絕的是那
    # 個量測，不是「紙本判讀一律不准」。這一欄原文帶著 `<sub>`／`<sup>`，所以這一對另外被標記那
    # 一條擋住（下一條測試）；那一條與截斷與否無關，所以在這裡直接量這兩個數字。
    whole = [{"field": "stem", "stored": _HALLUCINATED_STEM, "page": _COMPLETE_PAGE}]
    alignment, factor = apply_mod._page_read_shape(_HALLUCINATED_STEM, _COMPLETE_PAGE)
    assert alignment >= apply_mod.PAGE_READ_ALIGN_MIN
    assert 1.0 / apply_mod.PAGE_READ_LENGTH_FACTOR <= factor <= apply_mod.PAGE_READ_LENGTH_FACTOR
    why = []
    assert apply_mod.page_read_substitutions(question, whole, human_rejected=True, refused=why,
                                             verdict="TRUST") == []
    assert any("拆掉標記" in line for line in why), why

    # 讀失敗的那一輪（`error`）同樣不准：沒有讀到東西就不是一次讀。
    why = []
    assert apply_mod.page_read_substitutions(question, whole, human_rejected=True,
                                             error="http-500", refused=why, verdict="TRUST") == []
    assert any("http-500" in line for line in why), why


#: `104090:305:33 q052` 的判讀逐字重建（工作站實測）：原文這一欄帶著 10 個標記，紙本判讀一個都沒有。
_MARKUP_STEM = ("某藥物在體內之藥物動力學是遵循二室分室模式，且可以用C<sub>p</sub>=4e<sup>-5t</sup>"
                "+1e-0.1t來描述該藥在體內藥物濃度隨時間之變化（C<sub>p</sub>的單位：µg/mL，"
                "時間之單位為小時），已知該藥的排除速率常數（elimination rate constant）"
                "k=0.46 h<sup>-1</sup>，該藥的擬似分布體積 ( VD )β是9.2公升，"
                "則該藥的的中央室分布體積（V<sub>p</sub>）是多少L？")
_MARKUP_PAGE = ("某藥物在體內之藥物動力學是遵循二室分室模式，且可以用Cₚ=4e⁻⁵ᵗ+1e⁻⁰·¹ᵗ來描述該藥"
                "在體內藥物濃度隨時間之變化（Cₚ的單位：μg/mL，時間之單位為小時），已知該藥的排除"
                "速率常數（elimination rate constant）k=0.46 h⁻¹，該藥的擬似分布體積"
                "（V_D）β是9.2公升，則該藥的的中央室分布體積（Vₚ）是多少L？")


def test_a_read_that_would_strip_markup_is_refused_with_its_reason(tmp_path, capsys):
    # 這一條是乾跑時抓到的洞：`<sub>`／`<sup>` 是**平台自己**的下標寫法，而紙本判讀把同一段數學
    # 寫成真正的下標字元（`C<sub>p</sub>` → `Cₚ`）。整欄替換會把標記拆掉，等於把平台讀得懂的
    # 形式換成模型選的形式；站上實測（唯讀）：225 筆帶著 correction 的事件裡 0 筆拆掉標記。
    question = _question(stem=_MARKUP_STEM)
    changes = [{"field": "stem", "stored": _MARKUP_STEM, "page": _MARKUP_PAGE}]
    why = []
    assert apply_mod.page_read_substitutions(question, changes, human_rejected=True, refused=why,
                                             verdict="TRUST") == []
    assert any("拆掉標記" in line for line in why), why

    # 負控制一：這一對**兩個量測都過**（對齊 0.15、長度比 0.76），偵測器也對它沉默——也就是說在
    # 加上標記這一條之前，這一對會被套用、把標記拆掉。閘門補的就是這一個位置。
    alignment, factor = apply_mod._page_read_shape(_MARKUP_STEM, _MARKUP_PAGE)
    assert alignment >= apply_mod.PAGE_READ_ALIGN_MIN
    assert 1.0 / apply_mod.PAGE_READ_LENGTH_FACTOR <= factor <= apply_mod.PAGE_READ_LENGTH_FACTOR
    assert apply_mod.anchored_page_changes(_MARKUP_STEM, _MARKUP_PAGE, set()) is None

    # 負控制二：這一條只關整欄那一條路。同一欄裡、被偵測器標記過的那一個字元照樣代換，標記原封
    # 不動——這一欄不是「不准修」，而是「不准用會拆掉標記的方式修」。
    folded_stem = "膝關節⻑期以C<sub>p</sub>＝5 mg/L 描述其血中濃度"
    folded_page = "膝關節長期以C<sub>p</sub>＝5 mg/L 描述其血中濃度"
    folded = _question(stem=folded_stem, disputes=[{
        "kind": "substituted-ideograph",
        "substitutions": [{"field": "stem", "position": folded_stem.index("⻑"),
                           "char": "⻑", "means": "長"}]}])
    subs = apply_mod.page_read_substitutions(
        folded, [{"field": "stem", "stored": folded_stem, "page": folded_page}],
        human_rejected=True, verdict="TRUST")
    assert [s["field"] for s in subs] == ["stem"], subs
    assert "<sub>p</sub>" in apply_mod.build_correction(folded, subs)["stem"]

    # 而 CLI 會把這筆拒絕**印出來**：人要看得到哪一欄留在外面，以及為什麼。
    root = _write_queue(str(tmp_path), [question], events=[_block_event(question)],
                        findings=[_reading(question, "stem", _MARKUP_STEM, _MARKUP_PAGE)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "拆掉標記" in out, out
    assert _appended_events(root) == [], "把標記拆掉比不修更糟"


#: `113020:308:11 q021` 的形狀（站上實測）：判讀照規範第 4 條把讀不出來的一格寫成 `▢`。
_MARK_OPTION = "PO₂ 40 mmHg 為正常的動脈血氧分壓"
_MARK_PAGE = "P▢▢ 40 mmHg 為正常的動脈血氧分壓"
#: `115090:305:0402 q075` 的形狀：原文那一格本來就是抽取器讀不出來的 `▢`。
_UNREAD_STORED = "主成分honokiold之構造為▢"
_UNREAD_PAGE = "主成分honokiold之構造為長"


def test_a_read_that_brings_in_the_unreadable_mark_is_refused_with_its_reason(tmp_path, capsys):
    # `▢`（U+25A2）是 `reread.SYSTEM` 第 4 條叫模型在讀不出某個字時寫的字。把它套下去，等於把
    # 「我不知道」寫在抽取器**真的讀出來**的字上面——比現在的文字更差。站上實測：117 筆整欄替換
    # 裡有 2 筆帶著這個字（兩筆都是 CARE，也就是下面那一條也會擋住的）。
    question = _question(options=[{"key": "A", "text": _MARK_OPTION}, {"key": "B", "text": "選項B"},
                                  {"key": "C", "text": "選項C"}, {"key": "D", "text": "選項D"}])
    changes = [{"field": "option A", "stored": _MARK_OPTION, "page": _MARK_PAGE}]
    why = []
    assert apply_mod.page_read_substitutions(question, changes, human_rejected=True, refused=why,
                                             verdict="TRUST") == []
    assert any("讀不出來" in line and "▢" in line for line in why), why

    # 負控制：這一對**兩個量測都過**（對齊 0.94、長度比 0.91），偵測器也對它沉默——也就是說在加上
    # 這一條之前，這一對會被套用、把 `▢` 寫進去。擋住它的是這個記號，不是任何一個量測。
    alignment, factor = apply_mod._page_read_shape(_MARK_OPTION, _MARK_PAGE)
    assert alignment >= apply_mod.PAGE_READ_ALIGN_MIN
    assert 1.0 / apply_mod.PAGE_READ_LENGTH_FACTOR <= factor <= apply_mod.PAGE_READ_LENGTH_FACTOR
    assert apply_mod.anchored_page_changes(_MARK_OPTION, _MARK_PAGE, set()) is None

    # 而且這一條只管「讀進來」的那個方向：原文本來就是 `▢`（抽取器讀不出來），判讀把真正的字讀
    # 出來，那是修好它，不是把它寫壞——同一個 `▢`、同一個人為條件，方向相反就會被套用。
    read_back = [{"field": "stem", "stored": _UNREAD_STORED, "page": _UNREAD_PAGE}]
    unread = _question(stem=_UNREAD_STORED)
    subs = apply_mod.page_read_substitutions(unread, read_back, human_rejected=True, verdict="TRUST")
    assert [s["field"] for s in subs] == ["stem"], subs
    assert "▢" not in apply_mod.build_correction(unread, subs)["stem"]

    # CLI 也要把這一筆拒絕印出來（人看得到哪一欄留在外面、為什麼）。
    root = _write_queue(str(tmp_path), [question], events=[_block_event(question)],
                        findings=[_reading(question, "option A", _MARK_OPTION, _MARK_PAGE)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "讀不出來" in out, out
    assert _appended_events(root) == [], "把讀不出來寫進文字比不修更糟"


def test_a_whole_field_rewrite_needs_the_second_engines_trust(tmp_path, capsys):
    # 整欄改寫是這一圈裡最侵入的動作，所以它不該跑在第二次判讀已經舉手的那一筆上：
    # `TRUST` = 差異是機械性符號還原；`CARE` = 可能改變答案；`DOUBT` = 本地判讀明顯有錯或編造；
    # 沒有 verdict = 根本沒有第二次判讀。後三者都是「要人看」，而那些題目本來就在人的佇列裡。
    question = _question(stem=_UNFLAGGED_STEM)
    changes = [{"field": "stem", "stored": _UNFLAGGED_STEM, "page": _UNFLAGGED_MARKUP_PAGE}]

    # 正向控制：TRUST 仍然一路走完（不然這一條就不是閘門，是關門）。
    subs = apply_mod.page_read_substitutions(question, changes, human_rejected=True, verdict="TRUST")
    assert [s["field"] for s in subs] == ["stem"], subs
    assert [s["field"] for s in apply_mod.merge_substitutions(subs)] == ["stem"]

    # 三種拒絕各有自己的訊息，因為它們對讀報告的人意思不同。
    for verdict, needle in (("CARE", "CARE"), ("DOUBT", "DOUBT"), (None, "沒有第二次判讀")):
        why = []
        assert apply_mod.page_read_substitutions(question, changes, human_rejected=True, refused=why,
                                                 verdict=verdict) == []
        assert len(why) == 1 and needle in why[0], (verdict, why)
    # 沒見過的結論也要擋，而且要把它的名字印出來——不然一個新的值會靜靜地放行。
    why = []
    assert apply_mod.page_read_substitutions(question, changes, human_rejected=True, refused=why,
                                             verdict="MAYBE") == []
    assert "MAYBE" in why[0], why

    # 「沒有第二次判讀」在紀錄上的兩種形狀都要讀成沒有（舊的 finding 整條 `orchestration` 都不存在）。
    assert apply_mod.second_read_verdict({"orchestration": {"verdict": "TRUST"}}) == "TRUST"
    for record in ({}, {"orchestration": {}}, {"orchestration": None}, {"orchestration": "TRUST"}):
        assert apply_mod.second_read_verdict(record) is None, record

    # 這一條只關整欄那一條路：字元級那條路在同一個問題上照樣走，沒有 verdict 也走（偵測器標記過
    # 的位置本來就是量出來的，不需要第三個意見）。
    cyrillic = _cyrillic_dispute()
    page = ("下列產品何者屬於吸收性基劑（absorption bases）？"
            "①Aquabase  ②Eucerin  ③Plastibase  ④Aquaphor")
    assert apply_mod.page_read_substitutions(cyrillic, [{"field": "stem", "page": page}],
                                             human_rejected=True, verdict=None)

    # 而 CLI 讀的是 finding 的 `orchestration.verdict`：TRUST 會套用，CARE 與「沒有第二次判讀」
    # 都不會，而且理由會印在報告裡。
    for index, (verdict, needle) in enumerate((("TRUST", None), ("CARE", "CARE"),
                                               (None, "沒有第二次判讀"))):
        blocked = _question(stem=_UNFLAGGED_STEM)
        blocked["candidate_key"] = "moex:111020:305:33:1:question:q%03d" % (40 + index)
        root = _write_queue(str(tmp_path / str(verdict)), [blocked], events=[_block_event(blocked)],
                            findings=[_reading(blocked, "stem", _UNFLAGGED_STEM,
                                               _UNFLAGGED_MARKUP_PAGE, verdict=verdict)])
        code, out = _run(root, capsys, "--apply")
        assert code == 0, out
        assert ("to repair    : 1" if verdict == "TRUST" else "to repair    : 0") in out, (verdict, out)
        if needle:
            assert needle in out, (verdict, out)
            assert _appended_events(root) == [], verdict


def test_a_stale_finding_refuses_instead_of_overwriting_text_nobody_read():
    # 判讀是對舊文字做的（前面被加了一段話）。整欄替換的 `before` 是**判讀當時的原文**，所以
    # `verify` 一定看得出來——不然就會把一段沒有人讀過的欄位覆蓋掉。
    stale = [{"field": "stem", "stored": _UNFLAGGED_STEM, "page": _UNFLAGGED_MARKUP_PAGE}]
    later = _question(stem="（校對註記）" + _UNFLAGGED_STEM)
    subs = apply_mod.page_read_substitutions(later, stale, human_rejected=True, verdict="TRUST")
    assert [s["field"] for s in subs] == ["stem"] and apply_mod.verify(later, subs), "過期必須拒絕"
    # 負控制：同一筆修復套在它真正讀過的那份文字上，`verify` 是乾淨的。
    assert apply_mod.verify(_question(stem=_UNFLAGGED_STEM), subs) == []


def test_the_substitution_event_carries_applied_and_crop_too(tmp_path, capsys):
    # 字元級那條路的事件也要帶 `applied` 與 `crop`：審題的人要能打開同一張截圖，而產檔那一邊
    # 要知道自己讀到的是哪一種形狀。
    question = _cyrillic_dispute()
    page = "下列產品何者屬於吸收性基劑（absorption bases）？①Aquabase  ②Eucerin  ③Plastibase  ④Aquaphor"
    root = _write_queue(str(tmp_path), [question],
                        events=[_block_event(question)],
                        findings=[_reading(question, "stem", question["stem"], page)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    event = _appended_events(root)[0]
    assert event["applied"] == "substitution"
    assert event["crop"] == CROP
    assert {(c["field"], c["from"], c["to"]) for c in event["changes"]} == \
        {("stem", "ћ", "①"), ("stem", "ќ", "②"), ("stem", "ѝ", "③"), ("stem", "ў", "④")}
    assert event["correction"]["stem"] == page
    # `ћ` 是斯拉夫字母，不是「字形一樣、只有碼位錯」的那一類，所以不可以被算成不用看的修復。
    assert "normalisation" not in event


def test_a_radical_glyph_repair_is_flagged_as_normalisation(tmp_path, capsys):
    # 站上實測：354 筆機器修復事件裡 345 筆是這一類——螢幕上的字一模一樣，只有碼位錯。
    # 審核畫面要把這一類跟「真的要看的修復」分開數，而它無法只從 `changes` 反推。
    stored = "膝關節⻑期無活動，會導致膝關節伸直攣縮，進⽽在擺盪期缺乏膝屈曲"
    question = _question(stem=stored, disputes=[{
        "kind": "substituted-ideograph",
        "substitutions": [{"field": "stem", "position": stored.index("⻑"),
                           "char": "⻑", "means": "長"}]}])
    page = "膝關節長期無活動，會導致膝關節伸直攣縮，進而在擺盪期缺乏膝屈曲"
    root = _write_queue(str(tmp_path), [question],
                        events=[_block_event(question)],
                        findings=[_reading(question, "stem", stored, page)])
    _, out = _run(root, capsys, "--apply")
    event = _appended_events(root)[0]
    assert event["applied"] == "substitution"
    assert event["normalisation"] is True, out


def test_the_normalisation_flag_is_only_the_measured_radical_class():
    ideograph = {"field": "stem", "position": 0, "before": "⻑", "after": "長"}
    kangxi = {"field": "stem", "position": 0, "before": "⽣", "after": "生"}
    assert apply_mod.is_normalisation([ideograph]) is True
    assert apply_mod.is_normalisation([kangxi]) is True
    assert apply_mod.is_normalisation([ideograph, kangxi]) is True
    # 兩個字一起的 run（`⽣⻑`→`生長`）也是同一類。
    assert apply_mod.is_normalisation([{"field": "stem", "position": 0,
                                        "before": "⽣⻑", "after": "生長"}]) is True
    # 負控制，每一條都是一個「幾乎一樣但不可以放行」的形狀：
    # 斯拉夫字母（字形完全不同）、長度會變、改句子、以及康熙部首那一半 Unicode 說不通的目標。
    assert apply_mod.is_normalisation([{"field": "stem", "position": 0,
                                        "before": "ћ", "after": "①"}]) is False
    assert apply_mod.is_normalisation([{"field": "stem", "position": 0,
                                        "before": "⻑", "after": "長長"}]) is False
    assert apply_mod.is_normalisation([{"field": "stem", "position": 0,
                                        "before": "基劑", "after": "製劑"}]) is False
    assert apply_mod.is_normalisation([{"field": "stem", "position": 0,
                                        "before": "⽣", "after": "用"}]) is False
    assert apply_mod.is_normalisation([]) is False


def test_the_crop_path_stays_the_one_the_finding_already_stored():
    # 判讀的 `crop` 本來就是 queue 相對（`confirm_dispute.queue_relative`）。再跑一次 `relpath`
    # 會把它對工作目錄解析，變成 `../../..`，審核畫面就打不開那張圖。
    assert apply_mod.queue_relative(CROP, "/queue") == CROP
    assert apply_mod.queue_relative("/queue/review-ui/crops/x.png", "/queue") == \
        "review-ui/crops/x.png"
    assert apply_mod.queue_relative("/somewhere/else/x.png", "/queue") == "/somewhere/else/x.png"
    assert apply_mod.queue_relative(None, "/queue") is None


def test_the_standing_rejection_gate_reads_the_log_not_the_latest_event(tmp_path):
    # 這支工具自己寫的 `reset_review` 會排在人的 `block` **後面**（那是它的工作），所以閘門不能
    # 讀「最後一筆事件」——那樣已經修過的題目全部會讀成「沒有人退過」。而 `accept` 真的會關上
    # 閘門：站上實測 323 題有 `block`，其中 37 題後來被 `accept`。
    path = os.path.join(str(tmp_path), "events.jsonl")
    rows = [
        {"candidate_key": "moex:q1", "action": "block", "created_at": "2026-09-24T09:00:00"},
        {"candidate_key": "moex:q1", "action": "reset_review", "source": "qbr_dispute_apply",
         "created_at": "2026-09-24T10:00:00"},
        {"candidate_key": "moex:q2", "action": "block", "created_at": "2026-09-24T09:00:00"},
        {"candidate_key": "moex:q2", "action": "accept", "created_at": "2026-09-24T11:00:00"},
        {"candidate_key": "moex:q3", "action": "needs_review", "created_at": "2026-09-24T09:00:00"},
        {"candidate_key": "moex:q4", "action": "block", "created_at": "2026-09-24T09:00:00"},
        {"candidate_key": "moex:q4", "action": "unblock", "created_at": "2026-09-24T11:00:00"},
    ]
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    assert apply_mod.standing_rejections(path) == {"moex:q1", "moex:q3"}


# ---------------------------------------------------------------- 系統性字對（普查）
#
# 站上 18:06 那一輪真的發生的事：`癇` 被讀成 `癲` 出現在 4 題上，而兩個引擎都說 TRUST，於是
# `抗癲癇` 變成 `抗癲癲`。那 4 題裡有 2 題的判讀在**這一輪**還提得出整欄改寫（`108100:305:11 q035`
# 的選項 A、`108030:305:11 q061` 的題幹），另外 2 題的最新判讀已經沒有改動了（`115090:305:0401
# q037`、`113090:305:11 q053`，18:37／18:39 那一輪重讀回傳 0 個改動），第 5 題
# （`106020:302:22 q071`）沒有站得住的退件。所以線畫在 3 題：線以下是判讀的一般錯誤，線以上是
# 同一個字被讀錯很多次——那是「判讀在這裡系統性看錯」。
_SYSTEMATIC_STEM = "下列有關抗癲癇藥物的敘述，何者正確？"
_SYSTEMATIC_PAGE = "下列有關抗癲癲藥物的敘述，何者正確？"


def _systematic_question(index):
    question = _question(stem=_SYSTEMATIC_STEM)
    question["candidate_key"] = "moex:115090:305:0401:1:question:q%03d" % (30 + index)
    question["question_number"] = 30 + index
    return question


def _machine_field_event(question, changes, *, applied="field",
                         created_at="2026-09-24T18:06:35"):
    """機器寫下去的修復事件，形狀照站上 18:06 那一輪（`source=qbr_dispute_apply`）。

    `changes` 是 `(field, from, to)` 的序列。機器的時鐘是筆電當地時間，人的是伺服器 UTC——所以
    下面每一條測順序的測試都不是靠 `created_at` 比的。
    """
    return {"candidate_key": question["candidate_key"], "action": "reset_review",
            "source": "qbr_dispute_apply", "reviewer": "repair_dispute_apply",
            "applied": applied, "created_at": created_at,
            "changes": [{"field": field, "from": before, "to": after}
                        for field, before, after in changes]}


def _log_events(root):
    path = os.path.join(root, "review-ui", "question_review_events.jsonl")
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _withdrawals(root):
    """這一輪（或 fixture）寫進去的撤銷事件：契約的形狀就是 `applied == "withdrawn"`。"""
    return [event for event in _log_events(root) if str(event.get("applied") or "") == "withdrawn"]


def _reading_many(question, changes, *, crop=CROP, verdict="TRUST"):
    """一份判讀動了好幾個欄位（`_reading` 只做一個）。"""
    record = _reading(question, changes[0]["field"], changes[0]["stored"], changes[0]["page"],
                      crop=crop, verdict=verdict)
    record["changes"] = [dict(change, **{"from": change["stored"], "to": change["page"]})
                         for change in changes]
    return record


def test_only_cjk_pairs_of_different_characters_enter_the_census():
    assert apply_mod.systematic_pairs("抗癲癇藥", "抗癲癲藥") == [("癇", "癲")]
    # `値`→`值` 是兩個不同的字（Unicode 不認為它們相容相等），所以算；`⽣`→`生` 是同一個字
    # （NFKC 相等），那是抽取器的部首問題，不算判讀把一個字讀成另一個字。
    assert apply_mod.systematic_pairs("酸鹼値應與", "酸鹼值應與") == [("値", "值")]
    assert apply_mod.systematic_pairs("膝關節⻑期", "膝關節⽣期") == []
    # 不是表意文字的配對不算：`KM`→`Kₘ` 由 Unicode 上下標那一條管，拉丁字母不是普查的單位。
    assert apply_mod.systematic_pairs("常數（KM）", "常數（Kₘ）") == []
    assert apply_mod.systematic_pairs("阻斷GABAA型", "阻斷GABAₐ型") == []


def test_a_pair_changed_on_three_questions_is_refused_and_the_applied_repairs_withdrawn(tmp_path,
                                                                                        capsys):
    questions = [_systematic_question(index) for index in range(3)]
    events = []
    for question in questions:
        # 人的退件在前、機器的整欄修復在後：這一題不是「被人打回的」（那一條是下面的
        # bounce-back），所以擋住它的只能是普查。
        events.append(_block_event(question))
        events.append(_machine_field_event(question, [("stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)]))
    root = _write_queue(str(tmp_path), questions, events=events,
                        findings=[_reading(question, "stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)
                                  for question in questions])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "pair census  : 1 CJK→CJK pairs changed by this run's whole-field readings" in out, out
    assert "癇→癲  3 題  ←系統性" in out, out
    # 每一題兩次：撤銷清單裡一行，拒絕清單裡一行。
    assert out.count("同一個字對 癇→癲 在這一輪出現 3 題：判讀在這裡系統性看錯") >= 3, out
    # 第三個觸發（這一輪拒絕掉的、而機器真的寫過的那一欄）——三個題目各一筆。
    assert "withdraw     : 3 questions / 3 fields  (systematic-pair 3題/3欄)" in out, out
    assert "[systematic-pair]" in out, out
    withdrawals = _withdrawals(root)
    assert sorted(event["withdraw"] for event in withdrawals) == [["stem"], ["stem"], ["stem"]]
    for event in withdrawals:
        assert event["reviewer"] == "repair_dispute_apply" and event["applied"] == "withdrawn"
        assert "同一個字對 癇→癲" in event["why"]


def test_the_negative_control_two_questions_with_the_same_pair_still_apply(tmp_path, capsys):
    # 負對照：同一份判讀、同樣的字對，但只有 2 題。線是 3，所以這一組照寫——普查不是「看到
    # 癇→癲 就拒絕」。
    questions = [_systematic_question(index) for index in range(2)]
    root = _write_queue(str(tmp_path), questions,
                        events=[_block_event(question) for question in questions],
                        findings=[_reading(question, "stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)
                                  for question in questions])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 2" in out, out
    assert "癇→癲  2 題" in out and "←系統性" not in out, out
    assert len(_appended_events(root)) == 2
    assert _withdrawals(root) == []


def test_the_negative_control_neutralising_the_census_writes_the_pair():
    # 同一份判讀、同一題，只把普查關掉（`pairs=None`，也就是這條條文出現之前的行為）：那一欄就被
    # 寫下去了。擋住上面那三題的確實是普查，不是別的條文。
    question = _systematic_question(0)
    changes = [{"field": "stem", "stored": _SYSTEMATIC_STEM, "page": _SYSTEMATIC_PAGE}]
    why = []
    assert apply_mod.page_read_substitutions(question, changes, human_rejected=True, refused=why,
                                             verdict="TRUST", pairs={("癇", "癲"): 3}) == []
    assert any("同一個字對 癇→癲 在這一輪出現 3 題" in line for line in why), why
    subs = apply_mod.page_read_substitutions(question, changes, human_rejected=True, verdict="TRUST")
    assert [s["field"] for s in subs] == ["stem"] and subs[0]["replace"] is True
    assert apply_mod.SYSTEMATIC_PAIR_MIN == 3


# ---------------------------------------------------------------- 條文 1／條文 2：Unicode 上下標與標記的忠實度
#
# 站上折進 `candidates.jsonl` 的實際形狀（唯讀實測，2026-09-24）：`114090:305:0401 q033` 的選項 A
# 把紙本的大寫寫成小寫下標，`115020:305:0403 q078` 的兩個選項把 `KM` 寫成 `Kₘ`。平台自己的排版法是
# `<sub>`／`<sup>`（實測 6540 筆已經是標記，只有 55 筆帶著 Unicode 上下標字元），所以 Unicode
# 上下標字元是**字型不對**，而同一件事寫成標記是**想要的**——只要標記裡面的字元就是原文那幾個。
_GABA_OPTION = "藥理作用標的是GABAA 受體"
_KM_OPTION = "Michaelis-Menten 常數（KM）較小"


def _option_question(*texts):
    options = [{"key": key, "text": text} for key, text in zip("ABCD", texts)]
    return _question(options=options)


def test_a_reading_that_writes_unicode_sub_superscripts_is_refused_with_the_characters():
    question = _option_question(_GABA_OPTION, _KM_OPTION, "選項C", "選項D")
    changes = [{"field": "option A", "stored": _GABA_OPTION, "page": "藥理作用標的是 GABAₐ 受體"},
               {"field": "option B", "stored": _KM_OPTION,
                "page": "Michaelis-Menten 常數（Kₘ）較小"}]
    why = []
    assert apply_mod.page_read_substitutions(question, changes, human_rejected=True, refused=why,
                                             verdict="TRUST") == []
    assert any("option A" in line and "Unicode 上下標字元（ₐ）" in line for line in why), why
    assert any("option B" in line and "Unicode 上下標字元（ₘ）" in line for line in why), why
    # 量的是「判讀新寫進來的」那幾個字元：原文自己就是上下標字元時不算引進。
    assert apply_mod.unicode_sub_sup_introduced(_KM_OPTION, "Michaelis-Menten 常數（Kₘ）較小") == ["ₘ"]
    assert apply_mod.unicode_sub_sup_introduced(_KM_OPTION, _KM_OPTION) == []


def test_the_negative_control_the_same_characters_wrapped_in_markup_still_apply():
    # 同一個下標，寫成平台自己的排版法就是想要的形狀（主人要的是「標記」，不是「不要上下標」）。
    for stored, page in ((_GABA_OPTION, "藥理作用標的是 GABA<sub>A</sub> 受體"),
                         (_KM_OPTION, "Michaelis-Menten 常數（K<sub>M</sub>）較小"),
                         ("e-0.35t", "e<sup>-0.35t</sup>")):
        assert apply_mod.markup_introduced(stored, page) is True
        assert apply_mod.unicode_sub_sup_introduced(stored, page) == []
        assert apply_mod.markup_fidelity_complaints(stored, page) == []
    question = _option_question(_GABA_OPTION, "選項B", "選項C", "選項D")
    page = "藥理作用標的是 GABA<sub>A</sub> 受體"
    subs = apply_mod.page_read_substitutions(
        question, [{"field": "option A", "stored": _GABA_OPTION, "page": page}],
        human_rejected=True, verdict="TRUST")
    assert [s["field"] for s in subs] == ["option A"], subs
    assert apply_mod.build_correction(question, subs)["options"][0]["text"] == page


def test_a_reading_that_wraps_markup_but_changes_a_character_is_refused():
    # 標記可以加，裡面的字必須就是原文那幾個、同一個順序。`KM`→`K<sub>m</sub>` 把大寫換成小寫了。
    assert apply_mod.markup_fidelity_complaints(
        _KM_OPTION, "Michaelis-Menten 常數（K<sub>m</sub>）較小") == \
        ["判讀動到了上下標以外的字元（'M'→'m'）"]
    # 換字、多字、少字都是同一條，而且都指名是哪幾個字元。
    assert "'-'→'.'" in apply_mod.markup_fidelity_complaints("AUC0-∞", "AUC<sub>0.∞</sub>")[0]
    assert "多出 'x'" in apply_mod.markup_fidelity_complaints("AB", "A<sub>Bx</sub>")[0]
    assert "'C'→'D'" in apply_mod.markup_fidelity_complaints("ABC", "A<sub>B</sub>D")[0]
    # 空白不算（兩個量測的那個理由：判讀重新排版同一行的空白不是改內容）。
    assert apply_mod.markup_fidelity_complaints("C = 5", "C<sub>= 5</sub>") == []


def test_the_negative_control_clause_2_is_the_only_one_that_refuses_that_reading():
    stored = "Michaelis-Menten 常數（KM）較小，達到Vmax的一半"
    page = "Michaelis-Menten 常數（K<sub>m</sub>）較小，達到Vmax的一半"
    question = _question(stem=stored)
    why = []
    assert apply_mod.page_read_substitutions(
        question, [{"field": "stem", "stored": stored, "page": page}],
        human_rejected=True, refused=why, verdict="TRUST") == []
    assert any("上下標以外的字元" in line for line in why), why
    # 負對照：把條文 2 那一層拿掉，其他每一條對這一對都沉默——兩個量測過、Unicode 上下標沒有引進、
    # 偵測器沉默——所以留下來的只有條文 2。它壞掉的話，這一欄會帶著小寫的 `K<sub>m</sub>` 寫進去。
    assert apply_mod.page_read_shape_complaint(stored, page) is None
    assert apply_mod.unicode_sub_sup_complaint(stored, page) is None
    assert apply_mod._flagged_conflict("stem", stored, page, set()) is None
    assert apply_mod.markup_introduced(stored, page) is True
    assert [trigger for trigger, _why in
            apply_mod.whole_field_refusals(question, "stem", stored, page)] == ["markup-fidelity"]


def test_clause_2_does_not_fire_on_a_reading_that_only_keeps_existing_markup():
    # 原文自己帶著標記時，判讀留著它不是「引進標記」；而標記的組成原樣回來時，整欄那條路也不再因為
    # 「原文含標記」而拒絕——`markup_dropped_complaint` 量的是判讀有沒有把原文的標記帶回來
    # （主人 2026-09-25：「明明是一樣的邏輯」，見下一個測試）。
    stored = "血中濃度C<sub>p</sub>＝45"
    page = "血中濃度C<sub>p</sub> ＝  45"
    assert apply_mod.markup_introduced(stored, page) is False
    assert apply_mod.markup_fidelity_complaints(stored, page) == []
    assert apply_mod.markup_dropped_complaint(stored, page) is None
    triggers = [trigger for trigger, _why in
                apply_mod.whole_field_refusals(_question(stem=stored), "stem", stored, page)]
    assert triggers == [], triggers


def test_the_markup_clause_measures_the_readings_tags_not_the_stored_text():
    """這一條問的是判讀有沒有把原文的標記帶回來，不是「原文有沒有標記」。"""
    stored = "常數K<sub>M</sub>與V<sub>max</sub>"
    assert apply_mod.markup_dropped_complaint(stored, stored) is None
    # 判讀多包一層不算拆掉：多出來的標記由條文 2（`markup_fidelity_complaints`）要求它包著原本那幾個字。
    assert apply_mod.markup_dropped_complaint(
        stored, "常數K<sub><i>M</i></sub>與V<sub>max</sub>") is None
    # 負控制：掉一個下標就是真的拆掉，而且理由要說出少了哪一種標記。
    dropped = apply_mod.markup_dropped_complaint(stored, "常數K<sub>M</sub>與Vmax")
    assert dropped and "拆掉標記" in dropped and "<sub>×1" in dropped, dropped
    # 原文沒有標記時這一條不管：引進標記是條文 2 的事。
    assert apply_mod.markup_dropped_complaint("常數KM", "常數K<sub>M</sub>") is None


def test_clause_2_lets_a_checked_character_form_through_while_markup_is_added():
    """同一欄多引進了標記，不代表那一欄裡**可檢查的機械還原**就變成「動到了上下標以外的字元」。

    站上實測 2026-09-25：被這一條擋下的 283 欄裡，**63 欄（46 題）** 的每一對差異都是別的地方本來
    就放行的那一類（排版變體、相容分解、私用區還原）——它們被擋只因為同一欄裡多了標記。
    """
    stored = "常數 KM＋5 的單位"
    whole = "常數 K<sub>M</sub>+5 的單位"
    assert apply_mod.markup_introduced(stored, whole) is True
    assert apply_mod.markup_fidelity_complaints(stored, whole) == []
    assert apply_mod.whole_field_refusals(_question(stem=stored), "stem", stored, whole) == []

    # 負控制一：大小寫不是排版變體（`s` → `S` 在單位與化學式裡會改意思），所以仍然拒絕。
    lower = "常數 K<sub>m</sub>+5 的單位"
    assert apply_mod.markup_fidelity_complaints(stored, lower) == \
        ["判讀動到了上下標以外的字元（'M'→'m'）"]
    # 負控制二：數字被讀成另一個數字是改字，不是排法。
    misread = "常數 K<sub>M</sub>+0 的單位"
    assert apply_mod.markup_fidelity_complaints(stored, misread) == \
        ["判讀動到了上下標以外的字元（'5'→'0'）"]
    assert apply_mod.whole_field_refusals(
        _question(stem=stored), "stem", stored, misread) != []


def test_a_reading_that_keeps_the_markup_and_fixes_a_subscript_is_applied(tmp_path, capsys):
    """主人 2026-09-25：「有很多上下標的問題…明明是一樣的邏輯。」

    原文這一欄本來就帶著下標（抽取器把紙本的 `K_sp` 讀成 `K_p`），判讀把 `K<sub>sp</sub>` 讀回來
    ——標記的組成與原文相同，要修的是標記裡面的字。舊的寫法（原文含標記就整欄拒絕）把它丟進
    「AI無法判斷」，理由是「紙本判讀會拆掉標記」，而那份判讀恰好把標記完整帶回來了。站上實測
    （2026-09-25，每題最新一筆判讀）：判讀與原文標記逐字相同的 **141 欄**（102 題）就是這個形狀，
    其中 135 欄的可見字元真的不同。
    """
    stored = "碘化銀飽和溶液於25℃時的濃度為1.23×10⁻⁸莫耳／升，則其溶解度積（K<sub>p</sub>）為何？"
    page = "碘化銀飽和溶液於25℃時的濃度為1.23×10⁻⁸莫耳／升，則其溶解度積（K<sub>sp</sub>）為何？"
    question = _question(stem=stored)
    why = []
    subs = apply_mod.page_read_substitutions(
        question, [{"field": "stem", "stored": stored, "page": page}],
        human_rejected=True, refused=why, verdict="TRUST")
    assert why == [], why
    assert [s["field"] for s in subs] == ["stem"], subs
    assert "K<sub>sp</sub>" in apply_mod.build_correction(question, subs)["stem"]

    # 真的寫得進佇列（走一次真正的 CLI，不是只呼那支函式）。
    root = _write_queue(str(tmp_path), [question], events=[_block_event(question)],
                        findings=[_reading(question, "stem", stored, page)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    events = _appended_events(root)
    assert len(events) == 1, events
    assert events[0]["applied"] == "field"
    assert events[0]["correction"]["stem"] == page
    assert events[0]["changes"] == [{"field": "stem", "from": stored, "to": page}]


# ---------------------------------------------------------------- 整題原子性

def test_one_refused_field_drops_the_questions_whole_whole_field_set(tmp_path, capsys):
    # 主人報的形狀：「某些KM 四個選項都有，結果只改某些，還改錯」。同一題的整欄改寫要嘛全部過、
    # 要嘛全部不動——不然同一題裡會同時出現兩種寫法（`C>>Kₘ` 與 `C=KM`）。
    option_a = "藥物濃度遠大於親合常數（C>>KM）"
    option_b = "藥物濃度等於親合常數（C = KM）"
    question = _option_question(option_a, option_b, "選項C", "選項D")
    changes = [{"field": "option A", "stored": option_a, "page": "藥物濃度遠大於親合常數（C>>Kₘ）"},
               {"field": "option B", "stored": option_b, "page": "藥物濃度等於親合常數（C=KM）"}]
    root = _write_queue(str(tmp_path), [question], events=[_block_event(question)],
                        findings=[_reading_many(question, changes)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "option A" in out and "Unicode 上下標字元（ₘ）" in out, out
    assert "atomicity    : 1 questions dropped whole" in out, out
    assert "整題一起不動" in out, out
    assert _appended_events(root) == [], "整題沒過就不可以有半題被寫進去"

    # 負對照：option B 自己一個理由都沒有——讓它留在外面的不是它的判讀，是整題那一條。（把原子性
    # 拿掉，就是舊行為：每一個欄位各自決定，B 會被單獨寫下去。）
    fields, _shape = apply_mod.page_read_fields(question, changes)
    clean = [candidate["field"] for candidate in fields
             if not apply_mod.whole_field_refusals(question, candidate["field"],
                                                   candidate["stored"], candidate["page"])]
    assert clean == ["option B"], clean


# ---------------------------------------------------------------- 撤銷：人打回了機器的整欄改寫

def test_the_bounce_back_withdrawal_is_written_on_apply_and_not_on_a_dry_run(tmp_path, capsys):
    # 站上今晚的形狀：機器整欄寫下去，主人在**那之後**按了 block。
    question = _question(stem=_SYSTEMATIC_STEM)
    machine = _machine_field_event(question, [("stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)])
    human = _block_event(question, notes="上下標亂改")
    # 兩個時鐘不一樣（機器蓋筆電當地時間，人的事件蓋 UTC），所以判順序的是日誌的行序——這一條
    # fixture 的 created_at 就故意讓人的那一筆**比較小**。
    assert human["created_at"] < machine["created_at"]
    root = _write_queue(str(tmp_path), [question], events=[machine, human])
    before = len(_log_events(root))

    code, out = _run(root, capsys)
    assert code == 0, out
    assert "withdraw     : 1 questions / 1 fields  (bounce-back 1題/1欄)" in out, out
    assert "[bounce-back]" in out and "上下標亂改" in out, out
    assert len(_log_events(root)) == before, "乾跑不可以寫任何東西"
    assert _withdrawals(root) == []

    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "appended     : 0 reset_review events (carrying the correction)" in out, out
    assert "withdrawn    : 1 withdrawal events (applied=withdrawn，還原成 parser_original)" in out, out
    assert "sha256 after :" in out, out
    withdrawals = _withdrawals(root)
    assert len(withdrawals) == 1
    event = withdrawals[0]
    # 契約的形狀，一個字都不多（多寫 `source` 或 `changes` 會讓產檔那一邊多一個要猜的東西）。
    assert sorted(event) == ["action", "applied", "candidate_key", "correction", "created_at",
                             "reviewer", "why", "withdraw"], sorted(event)
    assert event["reviewer"] == "repair_dispute_apply"
    assert event["action"] == "reset_review"
    assert event["applied"] == "withdrawn"
    assert event["withdraw"] == ["stem"]
    assert event["correction"] is None
    assert event["why"] == "人把機器的改動打回了（block 2026-09-24T09:00:00）：整欄改寫還原"

    # 幂等：同一題再跑一次不會有第二筆（契約的形狀裡沒有機器事件的 id，欄位集合就是指紋）。
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "already      : 1 questions already carry this withdrawal" in out, out
    assert out.count("withdrawn    : 0 withdrawal events") == 1, out
    assert len(_withdrawals(root)) == 1


def test_a_new_bounce_after_a_later_machine_repair_gets_a_new_withdrawal(tmp_path, capsys):
    question = _question(stem=_SYSTEMATIC_STEM)
    earlier = _machine_field_event(question, [("stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)])
    withdrawn = apply_mod.build_withdrawal_event(
        question["candidate_key"], ["stem"], "first repair rejected",
        "repair_dispute_apply", "2026-09-24T18:10:00")
    later = _machine_field_event(
        question, [("stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)],
        created_at="2026-09-24T18:20:00")
    root = _write_queue(
        str(tmp_path), [question],
        events=[earlier, _block_event(question), withdrawn, later,
                _block_event(question, notes="still wrong")])

    before = len(_log_events(root))
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    appended = _log_events(root)[before:]
    assert len(appended) == 1 and appended[0]["applied"] == "withdrawn", appended
    assert len(_withdrawals(root)) == 2

    before = len(_log_events(root))
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "already      : 1 questions already carry this withdrawal" in out, out
    assert _log_events(root)[before:] == [], "the same repair generation must remain idempotent"


def test_a_human_bounce_withdraws_the_repair_but_does_not_close_the_question(tmp_path, capsys):
    # 站上實測 `110101:305:55:1:question:q061`：`block → 整欄修復 → block → 整欄修復`，人第二次說
    # 不要之後，機器又寫了一次——那個回彈要擋。但 2026-09-24 業主的原話把另一半講清楚了：
    # 「機器改錯就給我重改，為什麼還給我還原回去原本錯的地方」。撤銷一定要發生（錯的改動就是要退），
    # 而「這一題不再自己改」是錯的規則：還原回去的 `parser_original` 本身也是錯的（就是抽取器壓平的
    # 那一份字），停在上面等於這一題永遠不會被修好。
    #
    # 擋回彈的不是永久拒絕，是簽章（下一條測它）——所以這裡的兩輪要一起看。
    question = _question(stem=_UNFLAGGED_STEM)
    root = str(tmp_path)
    _write_queue(root, [question],
                 events=[_block_event(question, notes="上下標亂改"),
                         _machine_field_event(question,
                                              [("stem", _UNFLAGGED_STEM, _UNFLAGGED_PAGE)]),
                         _block_event(question, notes="上下標亂改")],
                 findings=[_reading(question, "stem", _UNFLAGGED_STEM, _UNFLAGGED_MARKUP_PAGE)])

    # ① 撤銷寫下去的那一輪：錯的改動還原成 `parser_original`，同一輪不另外寫修復（同一列不可以
    #    拿到兩個互相矛盾的指令）。
    before = len(_log_events(root))
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "withdraw     : 1 questions / 1 fields  (bounce-back 1題/1欄)" in out, out
    assert "這一輪要撤銷這一題的機器改動（bounce-back）" in out, out
    assert "這一題等新的決定，機器不再自己改" not in out, out
    assert len(_withdrawals(root)) == 1
    assert [event["applied"] for event in _log_events(root)[before:]] == ["withdrawn"]

    # ② 下一輪——撤銷已經在日誌裡，這一題對機器是**開著**的：判讀重讀紙本，新的、只動上下標的修復
    #    照排。這就是「重改」。負對照＝舊行為：舊碼在這一輪會印「這一題等新的決定，機器不再自己改」
    #    並且 `to repair : 0`，於是這一題永遠停在壓平的字上。
    before = len(_log_events(root))
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "already      : 1 questions already carry this withdrawal" in out, out
    assert "to repair    : 1" in out, out
    appended = _log_events(root)[before:]
    assert [event["applied"] for event in appended] == ["field"], appended
    assert appended[0]["changes"] == [{"field": "stem", "from": _UNFLAGGED_STEM,
                                       "to": _UNFLAGGED_MARKUP_PAGE}]
    assert len(_withdrawals(root)) == 1, "撤銷只有第一次那一輪會寫，之後不再重複"


def test_the_negative_control_a_later_human_word_reopens_the_question(tmp_path, capsys):
    # 同一份日誌，只多一筆人的 `correct`（他已經自己處理過這一題）。那不是退件，所以機器對這一題
    # 不再關著：同一份判讀照寫，而且不再有撤銷。
    question = _question(stem=_SYSTEMATIC_STEM)
    corrected = {"candidate_key": question["candidate_key"], "action": "correct", "reviewer": "local",
                 "notes": "我自己改過了", "created_at": "2026-09-24T11:30:00"}
    root = _write_queue(
        str(tmp_path), [question],
        events=[_block_event(question),
                _machine_field_event(question, [("stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)]),
                _block_event(question, notes="上下標亂改"),
                _machine_field_event(question, [("stem", _SYSTEMATIC_PAGE, _SYSTEMATIC_PAGE)],
                                     created_at="2026-09-24T18:43:49"),
                corrected],
        findings=[_reading(question, "stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)])
    before = len(_log_events(root))
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 1" in out, out
    assert "這一題等新的決定" not in out, out
    assert "withdraw     :" not in out, out
    assert _withdrawals(root) == []
    appended = _log_events(root)[before:]
    assert [event["applied"] for event in appended] == ["field"], appended


def test_the_negative_control_the_same_change_is_never_written_twice(tmp_path, capsys):
    # 撤銷不再是永久拒絕，所以擋住回彈的責任全在簽章上：撤銷之後，判讀如果又拿出**同一份**改動，
    # 仍然不會被寫第二次（那就是主人擋的那個回彈）。上一條測的是「不同的、只動上下標的修復會被寫」，
    # 這一條測它的另一半。
    question = _question(stem=_UNFLAGGED_STEM)
    root = str(tmp_path)
    withdrawal = apply_mod.build_withdrawal_event(
        question["candidate_key"], ["stem"],
        "人把機器的改動打回了（block 2026-09-24T09:00:00）：整欄改寫還原",
        "repair_dispute_apply", "2026-09-24T18:45:00")
    _write_queue(root, [question],
                 events=[_machine_field_event(
                             question, [("stem", _UNFLAGGED_STEM, _UNFLAGGED_MARKUP_PAGE)]),
                         withdrawal,
                         _block_event(question, notes="還是不對")],
                 findings=[_reading(question, "stem", _UNFLAGGED_STEM, _UNFLAGGED_MARKUP_PAGE)])
    before = len(_log_events(root))
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "already      : 1 questions already carry this withdrawal" in out, out
    assert len(_log_events(root)) == before, "同一份改動不寫第二次"


def test_the_negative_control_a_substitution_repair_is_never_withdrawn(tmp_path, capsys):
    # 撤銷是給整欄改寫用的（那是唯一一種用判讀覆蓋整個欄位的修復）。人退了一件字元級修復時，
    # 機器沒有東西可以「還原成 parser_original」——那一欄不是機器整欄寫的，所以要撤銷它得由人指名
    # （`--withdraw KEY:FIELD` 也一樣拒絕）。
    question = _question(stem="膝關節⻑期無活動")
    machine = _machine_field_event(question, [("stem", "⻑", "長")], applied="substitution")
    root = _write_queue(str(tmp_path), [question], events=[machine, _block_event(question)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "withdraw     :" not in out, out
    assert _withdrawals(root) == []


def test_an_applied_field_this_run_refuses_under_clause_1_is_withdrawn(tmp_path, capsys):
    # 站上 18:06 的形狀：這一欄被機器整欄寫下去（帶著 Unicode 下標），而同一份判讀現在被條文 1
    # 拒絕——所以這一欄要還原（第三個觸發），而且這一輪不再寫一次。
    stored = "Flumazenil 對GABAA受體的苯二氮平（benzodiazepine）結合位具有高親和力，其臨床用途為何？"
    damaged = "Flumazenil 對 GABAₐ受體的苯二氮平（benzodiazepine）結合位具有高親和力，其臨床用途為何？"
    question = _question(stem=stored)
    root = _write_queue(str(tmp_path), [question],
                        events=[_block_event(question),
                                _machine_field_event(question, [("stem", stored, damaged)])],
                        findings=[_reading(question, "stem", stored, damaged)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "[unicode-sub-sup]" in out, out
    assert "判讀把上下標寫成 Unicode 上下標字元（ₐ）" in out, out
    event = _withdrawals(root)[0]
    assert event["withdraw"] == ["stem"]
    assert event["why"] == "判讀把上下標寫成 Unicode 上下標字元（ₐ）：" \
                           "平台用 <sub>/<sup> 排版，這些字元的字型不對"


# ---------------------------------------------------------------- `--withdraw KEY:FIELD`

def test_withdrawing_a_field_by_hand_writes_the_contract_event(tmp_path, capsys):
    # 主人用手指名一欄（日誌裡看不出來的那種情形，或他自己看了就知道的那一欄）。形狀與自動的
    # 撤銷同一個（同一份契約），只是理由說得出是人指名的。
    question = _question(stem=_SYSTEMATIC_STEM)
    root = _write_queue(str(tmp_path), [question],
                        events=[_machine_field_event(question,
                                                     [("stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)])])
    spec = "%s:stem" % question["candidate_key"]
    code, out = _run(root, capsys, "--apply", "--withdraw", spec)
    assert code == 0, out
    assert "sha256 before:" in out and "sha256 after :" in out, out
    assert "withdrawn    : 1 withdrawal events" in out, out
    event = _withdrawals(root)[0]
    assert event["withdraw"] == ["stem"] and event["applied"] == "withdrawn"
    assert event["why"] == "人退回這筆機器整欄替換（%s）：依契約還原" % spec


def test_withdrawing_a_field_the_repair_never_wrote_is_refused(tmp_path, capsys):
    question = _question(stem=_SYSTEMATIC_STEM)
    root = _write_queue(str(tmp_path), [question],
                        events=[_machine_field_event(question,
                                                     [("stem", _SYSTEMATIC_STEM, _SYSTEMATIC_PAGE)])])
    code, out = _run(root, capsys, "--apply",
                     "--withdraw", "%s:option B" % question["candidate_key"])
    assert code == 0, out
    assert "REFUSED --withdraw" in out and "option B" in out, out
    assert "它寫的是 stem" in out, out
    assert _withdrawals(root) == []
    # 沒有整欄修復可以還原的題目（機器只做過字元級修復，或什麼都沒做）。
    plain = _question(stem="別的題")
    plain["candidate_key"] = "moex:111020:305:33:1:question:q099"
    substitution = _machine_field_event(plain, [("stem", "⻑", "長")], applied="substitution")
    other = _write_queue(str(tmp_path / "plain"), [plain],
                         events=[substitution, _block_event(plain)])
    code, out = _run(other, capsys, "--apply",
                     "--withdraw", "%s:stem" % plain["candidate_key"])
    assert code == 0, out
    assert "REFUSED --withdraw" in out, out
    assert "沒有 `applied == \"field\"` 的事件" in out, out
    assert _withdrawals(other) == []


# ---------------------------------------------------------------- 讀到了卻不能改：去問人
#
# 站上實測（2026-09-25，唯讀）：354 題站得住的 block 裡，最新判讀被閘門擋住的（CARE 109、拆標記 39、
# DOUBT 27、動字元 17、沒有第二次判讀 5）當中只有 **27** 題在介面上有一則未答的反問。其餘的題目是
# 被量過、被擋住、**而且沒有人知道**——機器只有 log 在說。下面這一段把那一批接上人真的會看的地方：
# 反問流（`question_repair_questions.jsonl`，介面的原則區），內容是那兩份讀法的並排。

#: 平台的下標寫法（`<sub>`）被判讀寫成平字：站上「拆掉標記」那一類（39 題）。
_ASK_STEM = "血中濃度C<sub>p</sub>＝45 mg/L，分布體積為 9.2 L"
_ASK_PAGE = "血中濃度Cp＝45 mg/L，分布體積為 9.2 L"


def _asks(root):
    path = os.path.join(root, "review-ui", "question_repair_questions.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _other_question(question, suffix):
    """同一份 fixture 的第二題（普查、上限這些測試需要兩個題號）。"""
    other = dict(question)
    other["candidate_key"] = "moex:111020:305:33:1:question:%s" % suffix
    other["question_number"] = int(suffix.lstrip("q"))
    return other


def test_a_reading_the_fences_refused_is_asked_about_once(tmp_path, capsys):
    # 這一題就停在 block 上：判讀讀到了、閘門說整欄替換會拆掉平台的下標寫法、而機器什麼都沒寫，
    # 也沒有人知道機器讀到了什麼。
    question = _question(stem=_ASK_STEM)
    root = _write_queue(str(tmp_path), [question], events=[_block_event(question)],
                        findings=[_reading(question, "stem", _ASK_STEM, _ASK_PAGE)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "拆掉標記" in out, out
    assert "反問人       : 新問 1 題（判讀被閘門擋住、沒有東西可寫）；已經問過 0 題；還有 0 題等下一輪" \
        in out, out
    asks = _asks(root)
    assert len(asks) == 1, asks
    ask = asks[0]
    assert ask["action"] == "ask" and ask["reviewer"] == "repair_dispute_apply"
    assert ask["candidate_key"] == question["candidate_key"]
    assert ask["question_id"] == apply_mod.ask_id(question["candidate_key"], question)
    # 人一眼要看到的兩件事：哪一欄、紙本與抽取各是什麼。少了這兩行，他就得回去重讀紙本——那正是
    # 他抱怨的那個來回（「你明明很多題目都有自己寫應該怎麼改」）。
    assert "・stem" in ask["question"], ask["question"]
    assert _ASK_STEM in ask["question"], ask["question"]
    assert _ASK_PAGE in ask["question"], ask["question"]
    assert "拆掉標記" in ask["reason"], ask["reason"]
    assert ask["model"] is None and ask["endpoint"] is None and ask["created_at"]

    # 幂等：下一輪（同一份文字、同一份判讀）不再問第二次。迴圈每 30 分鐘跑一次，問一次就好。
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "反問人       : 新問 0 題" in out and "已經問過 1 題" in out, out
    assert len(_asks(root)) == 1

    # 反問是關於**這一份文字**的：題目改過之後是另一個 id（新的問題、新的兩份讀法）。
    assert apply_mod.ask_id(question["candidate_key"], other_question := dict(question, stem="別的題")) \
        != ask["question_id"]
    assert other_question["candidate_key"] == question["candidate_key"]


def test_the_negative_controls_the_ask_channel_stays_shut(tmp_path, capsys):
    question = _question(stem=_ASK_STEM)
    findings = [_reading(question, "stem", _ASK_STEM, _ASK_PAGE)]
    refusals = [_block_event(question)]

    # ① 乾跑不寫反問（反問是寫出去的記錄，和修復同一條規則），但會說有幾題在等。
    root = _write_queue(str(tmp_path / "dry"), [question], events=refusals, findings=findings)
    code, out = _run(root, capsys)
    assert code == 0, out
    assert "反問人       : 1 題判讀被閘門擋住、沒有東西可寫（乾跑沒有寫反問，--apply 才會問）" in out, out
    assert _asks(root) == []

    # ② `--ask-limit 0` 關掉這一條頻道，而且**說出來它被關掉了**（不然「還有幾題沒問」會變成下一個
    #    沒有人知道的數字）。
    root = _write_queue(str(tmp_path / "off"), [question], events=refusals, findings=findings)
    code, out = _run(root, capsys, "--apply", "--ask-limit", "0")
    assert code == 0, out
    assert "反問人       : 1 題判讀被閘門擋住、沒有東西可寫（--ask-limit 0：這一輪不問人）" in out, out
    assert _asks(root) == []

    # ③ 機器自己寫得下去的那一題不問（沒有東西留在外面）。
    applied = _question(stem=_UNFLAGGED_STEM)
    root = _write_queue(str(tmp_path / "applied"), [applied], events=[_block_event(applied)],
                        findings=[_reading(applied, "stem", _UNFLAGGED_STEM, _UNFLAGGED_MARKUP_PAGE)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 1" in out, out
    assert "反問人" not in out, out
    assert _asks(root) == []

    # ④ 沒有判讀的題目不問：那是掃描排給讀取端的工作，問人等於叫人去做機器的讀法。
    root = _write_queue(str(tmp_path / "noread"), [question], events=refusals)
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "反問人" not in out, out
    assert _asks(root) == []

    # ⑤ 人已經把機器的整欄改動打回的題目不問：他裁決過了，而且讀取端已經就那一家人問過一次
    #    （「紙本與抽取一致，人仍阻擋」），機器的下一個動作是換一個修法，不是換一個頻道再問。
    bounced = _question(stem=_ASK_STEM)
    root = _write_queue(str(tmp_path / "bounced"), [bounced],
                        events=[_block_event(bounced),
                                _machine_field_event(bounced, [("stem", _ASK_STEM, "別的寫法")]),
                                _block_event(bounced, notes="上下標亂改")],
                        findings=[_reading(bounced, "stem", _ASK_STEM, _ASK_PAGE)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "反問人" not in out, "他打回過的題目不在反問名單上"
    assert _asks(root) == []

    # ⑥ 只有「判讀已過期」的題目不問：那是重讀紙本的工作，不是人的。
    fresh = "某藥物之半衰期為6小時，故每日給藥兩次"
    page = "某藥物半衰期約為6小時，故每日給藥兩次"
    moved = _question(stem=fresh + "（上一輪已改過）")
    root = _write_queue(str(tmp_path / "stale"), [moved], events=[_block_event(moved)],
                        findings=[_reading(moved, "stem", fresh, page)])
    code, out = _run(root, capsys, "--apply")
    assert code == 0, out
    assert "判讀已過期" in out, out
    assert "反問人" not in out, "過期是重讀的工作，不是人的"
    assert _asks(root) == []


def test_the_ask_limit_asks_a_few_a_round_and_says_how_many_wait(tmp_path, capsys):
    # 一次問完 197 題等於把原則區變成一面牆。一輪 40 題（預設）會把量到的待辦在幾輪內浮出來，
    # 而**剩下多少**一定要印出來——不然「還有幾題沒問」就變成下一個沒有人知道的數字。
    first = _question(stem=_ASK_STEM)
    second = _other_question(first, "q031")
    root = _write_queue(str(tmp_path), [first, second],
                        events=[_block_event(first), _block_event(second)],
                        findings=[_reading(first, "stem", _ASK_STEM, _ASK_PAGE),
                                  _reading(second, "stem", _ASK_STEM, _ASK_PAGE)])
    code, out = _run(root, capsys, "--apply", "--ask-limit", "1")
    assert code == 0, out
    assert "反問人       : 新問 1 題（判讀被閘門擋住、沒有東西可寫）；已經問過 0 題；還有 1 題等下一輪" \
        in out, out
    assert len(_asks(root)) == 1

    # 下一輪把剩下那一題問完，然後停（問過的題目是「已經問過」，不是「等下一輪」）。
    code, out = _run(root, capsys, "--apply", "--ask-limit", "1")
    assert code == 0, out
    assert "新問 1 題" in out and "已經問過 1 題" in out and "還有 0 題等下一輪" in out, out
    assert len(_asks(root)) == 2
    code, out = _run(root, capsys, "--apply", "--ask-limit", "1")
    assert code == 0, out
    assert "新問 0 題" in out and "已經問過 2 題" in out, out
    assert len(_asks(root)) == 2


# ---------------------------------------------------------------- 循環上限（業主 2026-09-25）

def _attempt_events(question, rounds, *, action="block"):
    """`rounds` 次「機器修復 → 人打回」，可以直接寫進事件流。"""
    events = []
    for index in range(rounds):
        events.append({"candidate_key": question["candidate_key"], "action": "reset_review",
                       "source": "qbr_dispute_apply", "reviewer": "repair_dispute_apply",
                       "applied": "field", "created_at": "2026-09-%02dT10:00:00" % (index + 1),
                       "changes": [{"field": "stem", "from": "第 %d 版" % index,
                                    "to": "第 %d 版改" % index}]})
        events.append({"candidate_key": question["candidate_key"], "action": action,
                       "reviewer": "local", "notes": "第 %d 次不對" % (index + 1),
                       "created_at": "2026-09-%02dT11:00:00" % (index + 1)})
    return events


def test_the_cap_counts_the_same_events_the_loop_counts(tmp_path):
    """上限的次數只能有一個定義：寫入端讀的 `withdrawals.rejection_counts` 必須等於掃描端與提示詞
    讀的 `repair_loop.fold_review_events["rejections"]`。

    兩邊數得不一樣的後果不是報表難看，是**上限在錯誤的題目上關門**（一邊說三次、另一邊說一次，
    人就會看到某一題忽然不再被修）。所以這一條在混合事件流上釘住兩個數字相等：有一次退件發生在
    任何機器修復之前（不算一次循環）、有一次是 `needs_review`（不是「改錯了」）、有一次被 `unblock`
    放回（歸零）。
    """
    question = _question()
    other = dict(question, candidate_key="moex:111020:305:33:1:question:q031")
    events = ([_block_event(question)]
              + _attempt_events(question, 3)
              + _attempt_events(other, 1)
              + [{"candidate_key": other["candidate_key"], "action": "needs_review",
                  "reviewer": "local", "created_at": "2026-09-09T12:00:00"},
                 {"candidate_key": other["candidate_key"], "action": "unblock", "reviewer": "local",
                  "created_at": "2026-09-09T13:00:00"},
                 {"candidate_key": other["candidate_key"], "action": "block", "reviewer": "local",
                  "created_at": "2026-09-09T14:00:00"}])
    _write_queue(str(tmp_path), [question, other], events=events)
    path = os.path.join(str(tmp_path), "review-ui", "question_review_events.jsonl")
    folded = repair_loop.fold_review_events(path)
    from_loop = {key: row["rejections"] for key, row in folded.items() if row["rejections"]}
    assert from_loop == apply_mod.rejection_counts(path), (from_loop, apply_mod.rejection_counts(path))
    assert from_loop[question["candidate_key"]] == 3
    assert from_loop[other["candidate_key"]] == 1


def test_a_question_three_rejections_in_is_handed_back_to_the_person(tmp_path, capsys):
    """業主 2026-09-25：循環三次之後才送入「AI無法判斷」。

    退滿的題目這一輪不修（機器不再寫第四次），報告上點名；而人再表態一次，這一題就自己回到可修的
    狀態——上限是數出來的計數，不是墓碑。
    """
    question = _question(stem=_UNFLAGGED_STEM)
    _write_queue(str(tmp_path), [question],
                 events=[_block_event(question)] + _attempt_events(question, 3),
                 findings=[_reading(question, "stem", _UNFLAGGED_STEM, _UNFLAGGED_MARKUP_PAGE)])
    before = _appended_events(str(tmp_path))
    code, out = _run(str(tmp_path), capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 0" in out, out
    assert "已退滿       : 1 題" in out, out
    # 撤回（機器把自己先前的改動收回去）是同一條流上的事件、也是 `action: reset_review`，所以要看
    # 的是「有沒有新的**修復**」：退滿的題目這一輪不該再被改一次。
    fresh = [e for e in _appended_events(str(tmp_path)) if e.get("applied") != "withdrawn"]
    assert [e for e in before if e.get("applied") != "withdrawn"] == fresh

    # 人放回（unblock）把次數歸零，然後再擋一次：同一份判讀又可以修了。
    events_path = os.path.join(str(tmp_path), "review-ui", "question_review_events.jsonl")
    with open(events_path, "a", encoding="utf-8") as handle:
        for action, when in (("unblock", "2026-09-25T12:00:00"), ("block", "2026-09-25T12:05:00")):
            handle.write(json.dumps({"candidate_key": question["candidate_key"], "action": action,
                                     "reviewer": "local", "created_at": when},
                                    ensure_ascii=False) + "\n")
    code, out = _run(str(tmp_path), capsys, "--apply")
    assert code == 0, out
    assert "to repair    : 1" in out, out
    assert len([e for e in _appended_events(str(tmp_path)) if e.get("applied") != "withdrawn"]) \
        == len([e for e in before if e.get("applied") != "withdrawn"]) + 1
