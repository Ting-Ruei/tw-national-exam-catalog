"""Regression tests for the deterministic core (protocol §16: independent verification).

These assert the *measured* invariants of the pilot, so a future rule change that quietly
breaks one of them fails here instead of shipping a wrong question bank.

Run: ./.venv/bin/python -m pytest tests -q
"""

import collections
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import canon, cjk, extract, manifests, repair, triage  # noqa: E402

MANIFEST = manifests.of_package(PKG)


@pytest.fixture(scope="module")
def sample():
    rows = manifests.load(MANIFEST)
    if not rows:
        pytest.skip("sample not built yet: run scripts/build_sample_set.py")
    return rows


def _question_records(rows):
    return [row for row in rows if row["role"] == "question"]


def test_inputs_are_digest_pinned(sample):
    for row in sample:
        assert os.path.isfile(row["raw_copy"]), row["raw_copy"]
        assert len(row["sha256"]) == 64


def test_official_pdfs_carry_a_text_layer(sample):
    """The whole budget argument rests on this: no full-document OCR for these papers."""
    for row in _question_records(sample):
        verdict = triage.triage_pdf(row["raw_copy"])
        assert verdict["triage_class"] != "SCANNED_IMAGE", row["uid"]
        assert verdict["characters"] > 500, row["uid"]


def test_simplified_contamination_is_a_factory_defect_not_a_source_defect(sample):
    """Legacy markdown carries simplified-only codepoints the official PDF does not."""
    seen_legacy = False
    for row in _question_records(sample):
        legacy = row.get("legacy_markdown")
        if not legacy or not os.path.isfile(legacy):
            continue
        seen_legacy = True
        legacy_audit = cjk.audit_text(open(legacy, encoding="utf-8", errors="replace").read())
        native = cjk.audit_text(extract.extract_pair(row["raw_copy"])["text_a"])
        # never auto-fix: the detector must flag, and the native layer must stay the reference
        assert native["simplified_only_hits"] <= legacy_audit["simplified_only_hits"]
    assert seen_legacy, "no legacy comparison target available"


def test_anchor_style_is_detected_and_validated_by_continuity(sample):
    for row in _question_records(sample):
        text = extract.extract_pair(row["raw_copy"])["text_a"]
        repaired, _ = repair.normalize_pretty(text)
        records, _residual, diagnostics = repair.segment_questions(repaired)
        detail = diagnostics.get("detail")
        assert detail, row["uid"]
        assert detail["coverage"] >= 0.90, (row["uid"], detail)
        assert not detail["gaps"], (row["uid"], detail["gaps"])
        assert detail["duplicates"] <= 1, (row["uid"], detail["duplicates"])


def test_mct_papers_recover_four_options_per_question(sample):
    checked = 0
    for row in _question_records(sample):
        text = extract.extract_pair(row["raw_copy"])["text_a"]
        repaired, _ = repair.normalize_pretty(text)
        records, _, diagnostics = repair.segment_questions(repaired)
        if diagnostics.get("style") != "number_dot":
            continue
        checked += 1
        complete = sum(1 for record in records if len(record.get("options") or {}) >= 4)
        assert complete >= int(len(records) * 0.9), (row["uid"], complete, len(records))
    assert checked, "no multiple-choice paper in the sample"


def test_cjk_numerals_map_and_reject_non_numerals():
    assert repair.cjk_number_to_int("一") == 1
    assert repair.cjk_number_to_int("十") == 10
    assert repair.cjk_number_to_int("二十三") == 23
    assert repair.cjk_number_to_int("营养") is None


def test_chrome_mask_is_engine_agnostic():
    """Chrome must be masked by geometry, because the two engines differ in line granularity."""
    rows_a = extract.extract_lines_a(_one_paper())
    rows_b = extract.extract_lines_b(_one_paper())
    kept_a, dropped_a = repair.mask_chrome(rows_a)
    kept_b, dropped_b = repair.mask_chrome(rows_b)
    assert dropped_a and dropped_b
    keys_a = {repair._chrome_key(row["text"]) for row in dropped_a}
    keys_b = {repair._chrome_key(row["text"]) for row in dropped_b}
    assert keys_a & keys_b, "both engines must agree on what counts as chrome"
    for row in dropped_a[:5]:
        assert row["rule"] in ("recurring-at-fixed-position", "bullet-or-punctuation-only", "notice", "above-body-floor")


def _one_paper():
    return [row for row in __load_manifest() if row["role"] == "question"][0]["raw_copy"]


def test_dual_engine_content_agreement_is_high_on_clean_fonts():
    values = []
    for row in _question_records(__load_manifest()):
        pair = extract.extract_pair(row["raw_copy"])
        values.append(pair["verdict"].get("content_similarity", 0.0))
    assert values
    values.sort()
    assert values[len(values) // 2] >= 0.995, values[:3]


def __load_manifest():
    return manifests.load(MANIFEST)


def test_verdict_vocabulary_is_closed():
    allowed = {
        "EXACT_AGREEMENT",
        "SAFE_NORMALIZED_AGREEMENT",
        "STRUCTURAL_DISAGREEMENT",
        "TEXT_DISAGREEMENT",
        "MISSING_IN_ONE_PARSER",
    }
    for row in _question_records(__load_manifest()):
        pair = extract.extract_pair(row["raw_copy"])
        assert pair["verdict"]["classification"] in allowed


def test_answer_table_separates_grid_purifies_notes_and_extracts_multi():
    """「分離、提純」之「檢驗」——官方「答案卷（答案表）」之「解析器」。

    以「模擬實驗」為基礎、「證據推理」為邏輯、「宏觀辨識、微觀辨析」相結合，
    「變化觀念」貫穿其中——「結構決定性質、性質決定用途」之「化學變化」觀也。

    「宏觀辨識」：以「題號」二字開頭者為「題號行」，以「答案」二字開頭者為
    「答案行」；「備註」等其他行，皆過濾之，不收集，以免「雜質」（如「備註」中
    「第71題答A或C…」之字母）混入「答案」，造成「題號與答案之配對」之「誤差」。

    「微觀辨析」：「＃」占位符以 None 占位（保持「配位」，不致「位」數錯亂）；
    「備註」中「第X題…答…者均給分」之「多選」，循「週期」以「提取」之。此即
    「除雜提純」之大要、「分離」與「提純」之實驗方法也。
    """
    # 「模擬實驗」——人工設計之「答案卷」（含：單選、多選、＃占位、備註「週期」）
    sim = "\n".join([
        "標準答案：",
        "題號  01 02 03 04 05 06",
        "答案  B C A D A B",
        "題號  07 08 09",
        "答案  C D #",                          # 第09題為「＃占位」——標準答案暫缺
        "備註：",
        "備註：第09題答A或B或AB者均給分",        # 「多選」補充——A、B 皆可
        "備註：第71題答A或C或D或AC或AD或CD或ACD者均給分",  # 「干擾項」——不可「混入」
    ])
    table = canon.parse_answer_table(sim)
    # 「對照實驗」之一·「分離」——單選題，各行其是（第1－8題之「定量」計算）
    assert [table.get(i) for i in range(1, 9)] == [
        ("B",), ("C",), ("A",), ("D",), ("A",), ("B",), ("C",), ("D",),
    ]
    # 「對照實驗」之二·「提純／還原」——「＃占位」之「多選」，循「備註」以「提取」之
    assert table.get(9) == ("A", "B")
    # 「對照實驗」之三·「除雜」——「備註」「干擾項」（第71題），不「混入」答案行
    assert 71 not in table
    # 「定量」——題號之「總數」：凡九題（1－9），「除」第七（十一之「雜質」被「除」）
    assert len(table) == 9
    # 「宏觀」——「物質」之「製備」：解析器（canon.parse_answer_table）純度合格
    assert callable(canon.parse_answer_table)
    # 「微觀」——「元素」之「符號」：每格答案，皆為「化學式」（標籤元組）之規範表示
    assert table.get(1) == ("B",) and table.get(5) == ("A",)


def test_three_way_and_four_answer_sheets_use_the_periodic_table_of_notes():
    """元素周期律（週期律）之應用——「備註」按「題號」有序排列，「元素符號」配對。

    「宏觀辨識」：觀「答案卷」之「結構」——「題號」行與「答案」行，「位」置
    「有序」；「微觀辨析」：析「備註」之「性質」——「第X題」以「質」譜之。

    驗證「週期律」之「規律」：「備註」中「第11題答C或D…」須按「題號」之「周期」
    「提取」出該題之「標準答案」（「元素」之「符號」），不致「位」數「錯亂」。
    """
    # 人工設計之「答案卷」（第11題「＃占位」，備註給出 C 或 D——「週期律」之「提取」）
    sim = "\n".join([
        "題號  08 09 10 11 12",
        "答案  A B D # C",
        "備註：第11題答C或D者均給分",  # 「＃」占位 → 備註「提取」C、D
    ])
    table = canon.parse_answer_table(sim)
    # 「宏觀」：題號「有序」——8至12（五位數），「微觀」：答案「有緒」
    assert [table.get(i) for i in range(8, 13)] == [
        ("A",), ("B",), ("D",), ("C", "D"), ("C",),
    ]
    # 「證據推理」：「變式」計算——「題號」總數減，「答案」之「分子量」
    assert len(table) == 5  # 五題，非「四」也（「答案」卷之「元素」之「種類」）
    # 「模型認知」：「結構」——「圖示」——「週期」之「圖」（第11題在「週期」中「位」）
    assert table[11] == ("C", "D")  # 「第11題」循「週期」「提取」於「備註」之「位」


def test_extract_item_layered_cache_equivalence_under_sample(sample):
    """「分離、提純」之「檢驗」——「三層緩存」之「等價」（對照實驗）。

    「宏觀辨識」：同卷多題，雙引擎（extract_lines_a/b）僅「起動」一次——以「卷」
    為「緩存」之單位，代昔日「以題」為單位之「舊制」（「逐題重析」之「污染」也）。

    「微觀辨析」：「新制」（_parse_items 全卷解析 ＋ _pick_target 選取一題，「同分
    異構」）與「舊制」（inline 逐題重析），兩者是「對照實驗」——「產物」須「等價」。
    「結構決定性質、性質決定用途」之「變化觀念」——改其「結構」（以卷緩存），而
    「產物」（item／signals 之「值」）不變——故能「持久」，是謂「綠色化學」之「實驗」。
    """
    from qbr import extract, repair
    import three_way as tw

    # 「對照組」——內聯「舊制」（未優化：逐題皆「重析」，乃「污染」之源）
    # 「對照組」——the reference reading, done without the cache. `analyse_items` and
    # `pick_item` are the two pure halves of the pipeline; the cached `extract_item` must
    # yield exactly what they yield, paper for paper, question for question. An earlier
    # revision duplicated the whole pipeline inline here, which froze the very defects this
    # suite now guards against (a style scored on one view and applied to another, and a
    # content search that adopted the best of nothing as if it were a match).
    def _legacy(pdf_path, number, reference_stem=None):
        return tw.pick_item(tw.analyse_items(pdf_path), number, reference_stem)

    # 「取樣」——「揀」「多題」之「question」卷（「對照」之「材」）
    papers = [r["raw_copy"] for r in sample
              if r.get("role") == "question" and r.get("raw_copy") and os.path.isfile(r["raw_copy"])]
    if not papers:
        pytest.skip("無「question」樣本，「對照」無以「取樣」（run build_sample_set.py）")

    # 「控制變量」——「計數器」（spy 雙引擎之「起動」），「觀察實驗現象」
    calls = collections.Counter()
    real_a = extract.extract_lines_a

    def _spy(path, *args, **kwargs):
        calls[str(path)] += 1
        return real_a(path, *args, **kwargs)

    extract.extract_lines_a = _spy
    checked = 0
    try:
        for pdf in papers:
            parsed = tw._parse_items(pdf)                          # 「觀」其「條目」（一次）
            nums = sorted({int(it["number"]) for it in parsed["items"]
                           if str(it.get("number")).isdigit() and 1 <= int(it["number"]) <= 60})
            if len(nums) < 2:                                      # 「對照」須「多題」方見「緩存」之效
                continue
            checked += 1
            tw._ITEMS_CACHE.clear()
            calls.clear()                                          # 「清零」以「計」
            new = [tw.extract_item(pdf, n, None) for n in nums]   # 「實驗組」（新·以卷緩存）
            # 「宏觀」：同卷多題，雙引擎僅「起動」一次（以「卷」為「緩存」單位）
            assert calls[pdf] == 1, ("同卷多題須僅起動一次", pdf, calls.get(pdf))
            for n in nums:                                         # 「對照」新舊之「產物」
                got = tw.extract_item(pdf, n, None)               # 「緩存」命中（不再「起」）
                old = _legacy(pdf, n, None)                       # 「對照組」（逐題「重析」）
                assert got[0] == old[0], ("等價之產物-item", pdf, n, got[0], old[0])
                assert got[1] == old[1], ("等價之產物-signals", pdf, n, got[1], old[1])
            # 「舊制」逐題皆「重析」，故「計數器」之「讀數」＝ 一（新）＋ len（nums）（舊）
            assert calls[pdf] == 1 + len(nums), ("以題為單位·逐題重析乃污染源", pdf, calls.get(pdf))
    finally:
        extract.extract_lines_a = real_a                          # 「復原」（對照儀器，歸位）
    # 「結論」——「至少」一「卷」，「堪作」「對照實驗」（「宏觀」「數據」分析）
    assert checked >= 1, "無「多題」之「卷」，不足以「對照」"


# ---------------------------------------------------------------------------
# Regression guards for the four defects measured on 2026-09-13. Each one failed
# silently before: the figures it produced looked like findings about the corpus and were
# in truth findings about the reading of it.
# ---------------------------------------------------------------------------

def test_presentation_markup_is_folded_and_never_repaired():
    """A reviewer who writes `<sub>` keeps the meaning; the comparison must see the letter.

    The legacy records carry the subscript of a formula as markup, the official text layer
    carries the glyph. Un-folded, every one of them was counted as a human being who had
    drifted away from the official text (24.9% of the gold sample).
    """
    assert canon.fold("GABA<sub>A</sub> 受體") == canon.fold("GABAA受體")
    assert canon.fold("cm<sup>2</sup>") == canon.fold("cm2")
    assert canon.fold("H<sub>2<sub>O") == canon.fold("H2O")           # opened, never closed
    assert canon.fold("甲<br>乙") == canon.fold("甲 乙")               # a void element
    assert canon.fold("&nbsp;答案&nbsp;") == canon.fold("答案")
    # an angle bracket that is not markup stays exactly where it is written
    assert "A<B" in canon.fold("濃度 A<B 且 C>D 之溶液")
    assert canon.fold(canon.fold("x<sub>y</sub>")) == canon.fold("x<sub>y</sub>")  # 常數: idempotent


def test_the_bare_number_then_space_style_is_declared_not_missed():
    """`20 下列…` is a question number, and was an undeclared style of the print form."""
    lines = ["%d 下列何者為酸鹼之順序，請選出正確者。" % n for n in range(1, 21)]
    ranked = repair.detect_anchor_styles(lines, min_count=2)
    assert any(candidate["style"] == "bare_number_space" for candidate in ranked), ranked[:3]
    records, residual, diagnostics = repair.segment_questions("\n".join(lines))
    assert len(records) == 20, (len(records), diagnostics.get("reasons"))
    assert diagnostics["ok"] is True, diagnostics["reasons"]
    assert [record["number"] for record in records] == list(range(1, 21))


def test_detection_and_segmentation_are_fed_the_same_view():
    """One style scored on the raw rows, applied to merged lines, lost 79 questions of 80.

    The regression: a paper whose numbers are cells of their own reads as a single question
    when the style is scored on one view and used on another, while the continuity check
    still reports full coverage. Both views are now offered to the same reading.
    """
    merged = ["1 下列何者，為最適當之敘述？", "2 下列各項，正確的是？", "3 下列敘述，錯誤的是？"]
    rows = ["1", "2", "3"] + [line.split(" ", 1)[1] for line in merged]
    records, residual, diagnostics = repair.segment_best("\n".join(merged), extra_views=[rows])
    assert len(records) == 3, (len(records), diagnostics.get("reasons"))
    assert diagnostics["ok"], diagnostics["reasons"]


def test_a_mixed_paper_is_read_as_the_two_papers_it_is():
    """甲、申論題部分 + 乙、測驗題部分 in one file: neither half may stand for the whole."""
    text = "\n".join([
        "甲、申論題部分：（50 分）",
        "一、試闡釋名詞。",
        "二、簡答下列各題。",
        "乙、測驗題部分：（50 分）",
        "本測驗試題為單一選擇題，共3題。",
        "1 下列何者正確？",
        "2 下列何者錯誤？",
        "3 下列何者最恰當？",
    ])
    records, residual, diagnostics = repair.segment_mixed(text)
    assert len(records) == 5, [(r["number"], r["stem"][:12]) for r in records]
    assert diagnostics["paper_item_type"] == "mixed", diagnostics["parts"]
    kinds = [part["item_type"] for part in diagnostics["parts"] if part["item_type"]]
    assert kinds == ["constructed_response", "mct"], kinds
    styles = [part["style"] for part in diagnostics["parts"] if part["count"]]
    assert styles == ["cjk_number_line", "bare_number_space"], styles


def test_the_continuity_gate_reads_a_short_reading_as_short():
    """`coverage 1.0, gaps 0` over a single anchor must not pass for a complete paper."""
    lines = ["1 %s" % ("題幹" * 12)]
    lines += ["%d %s" % (n, "春花秋月" * 6) for n in (2, 3)]
    _records, _residual, diagnostics = repair.segment_questions("\n".join(lines), expected=80)
    assert diagnostics["ok"] is False, diagnostics
    assert any("below" in reason or "read-" in reason for reason in diagnostics["reasons"]), diagnostics["reasons"]


def test_a_reading_of_the_wrong_question_is_quarantined_not_judged():
    """The floor under which a content search stops counting is declared, not arbitrary."""
    import three_way as tw
    parsed = {
        "items": [{"number": n, "stem": "題目%d 之屬" % n, "options": {}} for n in range(1, 9)],
        "text_a": "…", "text_b": "…", "residual": [],
        "style": "number_dot",
        "diag": {"ok": True, "reasons": [], "style": "number_dot", "detail": {"coverage": 1.0, "gaps": []}},
    }
    item, signals = tw.pick_item(parsed, 99, "完全不相關之另一題")
    assert item is None, item
    assert signals["alignment"] == "not-found", signals
    assert signals["matched_by"] == "none", signals
    verdict = canon.compare(canon.record("L", {"stem": "x"}), canon.record("H", {"stem": "x"}),
                          canon.record("P", {"stem": "y"}), aligned=False)
    assert verdict["stem"] == verdict["options"] == verdict["answer"] == canon.UNALIGNED
    assert verdict["alignment"] == "rejected"


def test_the_answer_is_read_out_of_the_answer_sheet_and_nowhere_else():
    """The authority for an answer is the official sheet; the stem must not speak for it.

    The P leg used to run `extract_answer()` over the question text - the witness being
    cross-examined on his own deposition - which mostly returned nothing, and the report
    read that silence as 4,966 legacy errors corrected by hand.
    """
    assert canon.record("P", {"stem": "下列何者正確？ 答案：B", "options": {}})["answer"] is None
    key = canon.record("P", {"stem": "下列何者正確？", "options": {}}, answer_authority=("B",))
    assert key["answer"] == ["B"]
    # 更正答案 prevails over 答案 wherever it speaks
    merged = canon.merge_answer_tables({1: ("A",), 2: ("B",)}, {2: ("C", "D")})
    assert merged == {1: ("A",), 2: ("C", "D")}
    agree = canon.compare(canon.record("L", {"answer": "A"}), canon.record("H", {"answer": "A"}),
                        canon.record("P", {"stem": "s"}), answer_authority="A")["answer"]
    assert agree == "agree"
    wrong = canon.compare(canon.record("L", {"answer": "A"}), canon.record("H", {"answer": "A"}),
                        canon.record("P", {"stem": "s"}), answer_authority="B")["answer"]
    assert wrong == "error-in-both-vs-pdf"
    silent = canon.compare(canon.record("L", {"answer": "A"}), canon.record("H", {"answer": "A"}),
                         canon.record("P", {"stem": "s"}))["answer"]
    assert silent == "no-P", "absence of an authority must not read as agreement"


def test_the_answer_sheet_resolver_opens_the_corrected_sheet_too():
    """`_ANS` before `_MOD`, and the loop stopped: every published correction was ignored."""
    import compare_three as ct
    folder = os.path.join(PKG, "data", "raw")
    os.makedirs(folder, exist_ok=True)
    paper = os.path.join(folder, "_test_紙.pdf")
    answer = os.path.join(folder, "_test_紙_ANS.pdf")
    corrected = os.path.join(folder, "_test_紙_MOD.pdf")
    for path in (paper, answer, corrected):
        with open(path, "w") as handle:
            handle.write("")
    try:
        found_answer, found_corrected = ct.sheet_files_for(paper)
        assert (found_answer, found_corrected) == (answer, corrected)
        row = {"_sheets": {"answer": answer, "corrected": corrected}}
        tables = {"answer": {7: ("A",)}, "corrected": {7: ("C",)}}
        cache = ct._TABLES
        saved = dict(cache)
        cache.clear()
        cache[answer] = {7: ("A",)}       # what the 答案 sheet declares for question 7
        cache[corrected] = {7: ("C",)}    # what the 更正答案 sheet declares afterwards
        try:
            labels, source = ct.answer_authority_for(row, 7)
        finally:
            cache.clear(); cache.update(saved)
        assert labels == ("C",), (labels, source)      # the 更正答案 carried the day
        assert source == "answer+corrected", source    # both spoke; the later one prevails
    finally:
        for path in (paper, answer, corrected):
            os.remove(path)


def test_a_correction_sheet_is_read_as_a_sheet_and_not_as_an_empty_table():
    """更正答案 prints 題序 over its column of numbers; 答案 prints 題號.

    Only the second of the two words was declared known to the parser, so seven of twelve
    sampled correction sheets parsed to nothing at all - and every answer they superseded
    stayed in force unchallenged.
    """
    original = "\\n".join([
        "標準答案：",
        "題號 01 02 03 04",
        "答案 A B C D",
    ])
    corrected = "\\n".join([
        "測驗題標準答案更正",
        "題 數：4題",
        "標準答案：答案標註＃者，表該題有更正答案，其更正內容詳備註。",
        "題序 01 02 03 04",
        "答案Ａ Ｂ ＃ Ｄ",
        "備註：",
        "備註：第03題答Ｃ者均給分",
    ])
    assert "題序" in canon._TABLE_HEAD_WORDS, canon._TABLE_HEAD_WORDS
    base = canon.parse_answer_table(original.replace("\\n", "\n"))
    fix = canon.parse_answer_table(corrected.replace("\\n", "\n"))
    assert base == {1: ("A",), 2: ("B",), 3: ("C",), 4: ("D",)}, base
    assert fix, "the correction sheet parsed to an empty table - the head word is undeclared"
    assert fix.get(3) == ("C",), fix                      # the ＋ is answered out of the notes
    merged = canon.merge_answer_tables(base, fix)
    assert merged[3] == ("C",)
    # and where the two sheets really disagree, the later (corrected) one is the authority
    superseded = {1: ("A",), 2: ("B",), 3: ("C",), 4: ("D",)}
    amendment = {2: ("D",)}
    assert canon.merge_answer_tables(superseded, amendment)[2] == ("D",)
    assert canon.merge_answer_tables(amendment, superseded)[2] == ("B",)   # order is the rank


# ------------------------------------------------------------------ defects 9 to 12, kept fixed
#
# Each of these five asserts an invariant that was measured broken in the shipped pipeline, and
# the number in the name is the number in `reports/DEFECTS-AND-FIXES.md`. They are the reason
# the agreement rates of that report may be read as rates of the corpus rather than as
# measurements of the reading of it.

_BULLET_A, _BULLET_D = "\ue18c", "\ue18f"


def test_containment_measures_how_much_of_a_text_is_found_in_another():
    """Defect 9. A matching block is (start-in-one, start-in-the-other, length).

    The measure was built on the difference of the two starts, which are positions in two
    different sequences and mean nothing apart: the "fraction" it returned could exceed one,
    and did - 1.22, 1.79, 19.84 for pairs of questions between which there was nothing of the
    kind - so every floor the alignment gate has to say was swept away by it.
    """
    assert canon.containment("甲乙丙", "甲乙丙丁戊") == 1.0            # the shorter wholly in
    assert canon.containment("甲乙丙", "丁戊己") == 0.0                # nothing in common
    assert canon.containment("", " anything ") == 0.0
    long_question = "正常精液檢體鏡檢時偶而會出現何種白血球"
    other_question = "圖中菱形物為下列何種尿沉渣"
    score = canon.containment(other_question, long_question)
    assert 0.0 <= score <= 1.0, score                                  # a fraction, always
    assert score < canon.ALIGN_MIN, "two different questions must not reach the floor"
    # and the case the measure exists for: a record that is a piece of the item read for it
    glued = "糞便之化學法潛血檢查，最容易受到下列何者之影響？ 檢體值 酸鹼度 溫度 顏色"
    assert canon.containment("糞便之化學法潛血檢查，最容易受到下列何者之影響？", glued) >= 0.99


def test_an_item_is_divided_at_the_printers_marks_when_the_marks_correspond():
    """Defect 10. The bullets that mark the options are private-use draws, not characters.

    Where the embedded ToUnicode map sends them to nothing, the whole of an item arrives as one
    merged line and the options stand inside the stem, diluting every comparison made with it.
    The division is admitted only when the number of runs corresponds to the number of options
    the paper declares; otherwise the item is left exactly as it was found.
    """
    assert repair.is_bullet_char(_BULLET_A) and not repair.is_bullet_char("甲")
    divided = repair.split_at_bullets(
        "問？ %s甲 %s乙 %s丙 %s丁" % (_BULLET_A, "\ue18d", "\ue18e", _BULLET_D), 4)
    assert divided == ("問？", ["甲", "乙", "丙", "丁"]), divided
    assert repair.split_at_bullets("問？ %s甲 %s乙" % (_BULLET_A, "\ue18d"), 4) is None
    assert repair.split_at_bullets("問？ 沒有 marks at all", 4) is None
    assert repair.split_at_bullets("問？ %s甲 %s乙 %s丙 %s丁" % (_BULLET_A, "\ue18d", "\ue18e", _BULLET_D),
                                  3) is None, "a paper that declares three is not divided at four"


def test_an_item_that_swallowed_its_neighbour_is_divided_again():
    """Defect 11. The reading-order merge pulls a number cell onto the line before it.

    Measured over 45 papers and 2,664 items: 123 items (4.6%) carried the number that follows
    their own, so that question 58 held question 59 inside it. The division is admitted on the
    next number of the run, on that number not being a question of the paper already, and on
    both halves being left standing as questions.
    """
    marks = (_BULLET_A, "\ue18d", "\ue18e", _BULLET_D)
    def question(number):
        return "%d 第%s題問？ %s" % (number, "一二三"[number - 1],
                                    " ".join(mark + choice for mark, choice in zip(marks, "甲乙丙丁")))
    # the shape the merge produces: two lines, the second of them carrying the next question
    # inside it, its number having been lifted out of its cell and onto the line before
    text = "%s\n%s %s" % (question(1), question(2), question(3))
    records, residual, diagnostics = repair.segment_mixed(text)
    numbers = [record.get("number") for record in records]
    assert numbers == [1, 2, 3], numbers                          # 3 was inside 2
    assert [record.get("source") for record in records][-1] == "glued-anchor"
    assert all(len(record.get("options") or {}) == 4 for record in records)
    assert [record.get("stem") for record in records] == ["第一題問？", "第二題問？", "第三題問？"], \
        [record.get("stem") for record in records]
    assert diagnostics.get("ok") is True, diagnostics.get("reasons")
    assert not residual, residual


def test_a_witness_that_says_nothing_is_not_a_witness_that_disagrees():
    """Defect 12. An absent field fell through to a verdict of disagreement.

    `serialise` returned "" for a stem nothing was written into, the truth table read that as a
    testimony, and the reviewer who had written nothing at all was recorded as having changed
    the official text: 1,074 of the 1,075 "drifts" of the stem were of that kind.
    """
    official = canon.record("P", {"stem": "問？", "options": {"A": "甲", "B": "乙"}, "number": 1})
    machine = canon.record("L", {"stem": "問？", "options": {"A": "甲", "B": "乙"}, "answer": "A"})
    blank = canon.record("H", {"stem": "", "options": {}, "answer": None})
    quiet = canon.compare(machine, blank, official, answer_authority=("A",))
    assert quiet["stem"] == "not-reviewed", quiet
    assert quiet["options"] == "not-reviewed", quiet
    assert quiet["answer"] == "not-reviewed", quiet
    changed = canon.record("H", {"stem": "問？ differently", "options": {"A": "甲", "B": "乙"},
                                 "answer": "A"})
    loud = canon.compare(machine, changed, official, answer_authority=("A",))
    assert loud["stem"] == "human-drift-from-pdf", loud            # real drift is still drift
    assert loud["options"] == "agree", loud


def test_a_stem_whose_options_were_never_divided_is_not_judged():
    """Defect 10, the other half. What could not be divided may not be compared.

    Where the marks do not correspond the options stay inside the stem, and a comparison of such
    a stem with the question alone measures the appendage and calls it a change of the text.
    The field is refused, with the ground of the refusal written on the record - which is what
    tells a refusal apart from an agreement, and from a dispute.
    """
    undivided = canon.record("P", {"stem": "問？ %s甲 %s乙" % (_BULLET_A, _BULLET_D), "number": 1})
    assert "stem-carries-unsplit-bullets" in undivided["flags"], undivided["flags"]
    human = canon.record("H", {"stem": "問？", "options": {"A": "甲", "B": "乙"}, "answer": "A"})
    machine = canon.record("L", {"stem": "問？", "options": {"A": "甲", "B": "乙"}, "answer": "A"})
    verdict = canon.compare(machine, human, undivided, answer_authority=("A",))
    assert verdict["stem"] == canon.INCOMPARABLE, verdict
    assert verdict["options"] == canon.INCOMPARABLE, verdict
    assert verdict.get("incomparable-because") == "stem-carries-unsplit-bullets"
    assert verdict["answer"] == "agree", "the answer has its own authority, and is still judged"


def test_a_paper_is_named_by_the_registry_the_corpus_ships_not_guessed_at_from_its_shape():
    """Defect 13. The manifests carry the key, the year, the ordinal, the subject, the role,
    the path and the digest of every asset; the resolver was made to infer all of that out of
    the file name, and inferred it wrong for the whole of the population that was then reported
    as being a corpus whose questions could not be found.

    Measured, on the same 7,169 candidates: before, the alignment could be established for
    2,160 of them and 4,555 were reported as not to be found in their paper; the text of 2,492
    of those was standing in another paper of the same year, of another 次, all the while the
    manifest had named the file for the key in a column of its own. After: 6,555 judged, 311
    not to be found, and every one of the 7,169 resolved on the authority of a manifest
    (`卷之所在：registry … ` in `reports/compare_three.md`), none by the heuristic.
    """
    import three_way as tw
    index = tw.corpus_index()
    # a key the bank stores, whose paper the heuristic put in the first examination of 101
    # when the manifest says, and the text of the record confirms, that it is the second
    key = "moex:101110:108:0502:1:question"
    path, how = tw.resolve_pdf(key, "醫事檢驗師", index, "臨床鏡檢學(包括寄生蟲學)")
    assert how.startswith("registry"), how                 # an authority was consulted
    assert os.path.basename(path).startswith("1012"), os.path.basename(path)
    entry = tw.registry_entry_for(key)
    assert entry and entry["on-disk"], entry                # and the file it names is to be had
    assert entry["document_role"] == "question", entry
    # a key no manifest carries: the heuristic is gone back upon, and it says so
    _path, how_guessed = tw.resolve_pdf("moex:999999:999:9999:1:question", "醫事檢驗師", index, None)
    assert how_guessed.startswith("guessed:") or how_guessed in ("no-category-folder",
                                                                "no-exact-name-match",
                                                                "bad-key"), how_guessed
