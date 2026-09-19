#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden path —— 自一卷官方試題，走到一個平台可驗之封裝。

「金路」之大義：全流程最窄的一條完整路徑，以一份真實試卷，走完全部關卡各一次。
其用不在「產出題庫」，在「以失敗告所當決」——凡斷處，即真問題所在。

    S0  intake    官方 Q / ANS / MOD 三卷就位，記 digest
    S1  triage    便宜掃描：文字層、CJK 普查、損壞碼 → 類別
    S2  dual      雙引擎抽取 + 幾何遮罩 + 分段 + 題型偵測
    S3  gate      題號連續、選項完整、答案 ⊆ 選項、告警（簡體／PUA／兼容字）
    S4  records   canonical records；答案唯取官方答案卷
    S5  package   平台契約封裝
    S6  verify    平台 validator / importer dry-run

此檔不讀網路、不讀資料庫、不寫 catalog 之任何物。所產皆在 --out 之下。
凡機器位址（IP、hostname、service、port）不得入產物或程式；--describe 可自查。

    python3 scripts/golden_path.py --describe
    python3 scripts/golden_path.py run --registry-key moex:115090:308:0504:1 \\
        --asset-root <...>/國考題資料夾 --out <dir> --package-version tw-national-exam-medtech-v0.0.1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import (answer_sheets, canon, corrections, extract, package, paths,  # noqa: E402
                 repair, review_queue, triage)

import three_way  # noqa: E402

STAGES = ("S0_intake", "S1_triage", "S2_dual", "S3_gate", "S4_records", "S5_package", "S6_verify")
COMPONENT_ID = "qbr_golden_path"

# A machine address in a delivered artifact is a defect, not a configuration. The check is a
# grep, so that the claim "no host is baked into the pipeline" is measured rather than believed.
# A machine address, as distinct from a number that merely looks like one.
#
# The rule exists so that a delivered package can be moved to another machine: an absolute path
# or a hostname in an artifact means the package only works where it was built. It is therefore
# about *addressing this machine*, and three shapes say that: a URL, an `ssh` invocation, and a
# dotted quad.
#
# The dotted-quad form is where it went wrong. `3.1.1.3` is the Enzyme Commission number of the
# lipase reaction, printed in question 51 of `1141_醫事檢驗師_生物化學與臨床生化學`, and the pattern
# matched it, so S6 failed the run with `machine-address-in-artifact` on a package that contains no
# address at all. A real address in this corpus looks like `192.168.10.90`; an EC number looks
# like an EC number.
#
# They are told apart by what the first octet would have to be. A host on a real network starts
# at 10 (private), 127 (loopback), 169 (link-local), 172, 192, or has a three-digit first octet;
# `3.1.1.3` starts with a one-digit octet below 9, which names no network anyone routes to. The
# first branch is written out rather than expressed as "octet >= 10" so that the ranges a reader
# would recognise are the ones written down, and the fallback covers the remaining three-digit
# space.
_ADDRESS = re.compile(
    r"\b(?:"
    r"https?://|ssh\s|"
    r"(?:10|127|169|172|192|22[0-3]|2[4-9]\d|[1-9]\d\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}"
    r")")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def jsonable(value):
    """Anything -> something JSON can hold. Diagnostics carry compiled patterns and sets;
    a run manifest must never fail to be written because a stage was informative."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return str(value)


def write_json(path, value):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(jsonable(value), ensure_ascii=False, indent=2,
                                sort_keys=True) + "\n")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- S0 intake

def sheet_paths(asset_root, *, year, category, ordinal, subject, registry_key):
    """Locate Q / ANS / MOD for one paper. See `qbr.answer_sheets.sheet_paths`.

    This was written here first, for the golden path, and then the batch driver needed the
    same thing. Rather than let the two drift apart - which they already had, one of them
    walking the directory and the other not - the body moved into the library and both
    drivers call it. What remains is the `read_the_registry` import, which lives in the
    scripts directory and so cannot be imported from inside the package.
    """
    def reader():
        import read_the_registry
        return read_the_registry.load_registry()

    return answer_sheets.sheet_paths(asset_root, year=year, category=category, ordinal=ordinal,
                                     subject=subject, registry_key=registry_key,
                                     registry_reader=reader)


def stage_intake(paths):
    """S0: freeze the three sheets by digest, and say which ones are actually present."""
    sheets = {}
    for role in ("question", "answer", "corrected"):
        path = paths.get(role)
        if not path or not os.path.isfile(path):
            continue
        sheets[role] = {"path": path, "bytes": os.path.getsize(path),
                        "sha256": sha256_file(path)}
    if "question" not in sheets:
        raise ValueError("no question sheet; nothing to read")
    if "answer" not in sheets and "corrected" not in sheets:
        raise ValueError("no answer sheet; an answer would have to be guessed, which is refused")
    return sheets


# --------------------------------------------------------------------------- S1 triage

def stage_triage(sheets):
    report = triage.triage_pdf(sheets["question"]["path"])
    return {key: report.get(key) for key in
            ("path", "sha256", "bytes", "pages", "pages_with_text", "words", "images",
             "fonts", "characters", "cjk_characters", "damaged_characters",
             "replacement_characters", "pua_characters", "simplified_only_hits",
             "simplified_ratio", "damage_ratio", "question_anchors",
             "max_gap_in_anchors", "option_markers", "latin_words", "triage_class",
             "reasons", "reference_coverage")}


# --------------------------------------------------------------------------- S2 dual parse

def stage_dual(sheets):
    parsed = three_way.analyse_items(sheets["question"]["path"])
    diag = parsed.get("diag") or {}
    return parsed, {
        "style": parsed.get("style"),
        "paper_item_type": diag.get("paper_item_type"),
        "items": len(parsed["items"]),
        "residual_lines": len(parsed.get("residual") or []),
        "count": diag.get("count"),
        "expected": diag.get("expected"),
        "coverage": diag.get("residual_ratio"),
        "sequence": jsonable(diag.get("sequence")),
        "ok": diag.get("ok"),
        "reasons": jsonable(diag.get("reasons")),
        "tried": jsonable(diag.get("tried")),
    }


# --------------------------------------------------------------------------- S3 gate

def _sheet_question_count(sheets):
    """How many questions the official answer sheet lists, or 0 when it does not say.

    The count is the highest number the sheet prints, and the lowest, checked against each other:
    a sheet that prints `1` to `80` is a statement that there are eighty questions, and it is the
    same statement the person marking the paper used. Reading the table's own numbers rather than
    counting its cells means a row the extraction dropped shows up as a gap instead of shrinking
    the total.

    A sheet that covers only part of a paper - the pharmacists' 50-question subjects do this -
    would understate the count if the highest number were taken blindly, so the two bounds have to
    agree on starting at 1. Where they do not, the sheet is not making a whole-paper statement and
    says nothing (0), leaving the anchor count to bound the reading.
    """
    for role in ("answer", "corrected"):
        entry = sheets.get(role)
        if not entry:
            continue
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not path:
            continue
        try:
            table = canon.parse_answer_table(answer_sheets.read_table_text(path, role=role))
        except Exception:
            continue
        if table:
            numbers = sorted(int(number) for number in table)
            if numbers and numbers[0] == 1:
                return numbers[-1]
    return 0


def _picture_boxes_of_page(pdf_path, page_number):
    """Every picture-like region on one page, as `(y0, x0, y1, x1)`.

    Two kinds of thing are a picture on these papers, and checking only the first missed most of
    them:

      * **An embedded image object** - a scan, a photograph, a half-tone.
      * **Vector drawings** - a plot, a waveform, a chemical structure, a graph. Measured on
        `1001_藥師_藥理學與藥物化學` page 4: zero image objects and 31 drawing operations, because
        the figure is a set of lines. This is the class that kept those questions looking like
        broken extractions.

    Drawings are filtered by their own geometry so a rule or a table border is not mistaken for a
    figure: a drawing that is very wide and very short is a line, not a picture. The filter uses the
    box the page states, so it needs no threshold on meaning.
    """
    try:
        import pymupdf
        document = pymupdf.open(pdf_path)
    except Exception:
        return []
    try:
        if not (1 <= int(page_number) <= len(document)):
            return []
        page = document[int(page_number) - 1]
        boxes = []
        for info in page.get_image_info(xrefs=True):
            bbox = info.get("bbox")
            if bbox:
                boxes.append((float(bbox[1]), float(bbox[0]), float(bbox[3]), float(bbox[2])))
        for drawing in page.get_drawings():
            rect = drawing.get("rect")
            if rect is None:
                continue
            x0, y0, x1, y1 = (float(v) for v in rect)
            if (x1 - x0) < 40.0 or (y1 - y0) < 12.0:
                continue
            boxes.append((y0, x0, y1, x1))
        return boxes
    except Exception:
        return []
    finally:
        document.close()


def _classify_optionless(items, triage_report, parsed):
    """Which option-less questions are picture-optioned, and which are really broken.

    Returns `(picture_optioned, alphabet_optioned)` - two sets of question numbers.

    A question comes out of the text layer with no option text whenever the paper printed its four
    options as pictures, which is common on the papers that ask about waveforms, scans and
    structures. The paper says so in a way that can be measured: picture objects sit inside the
    question's own band on its page. That is the evidence used here, and it is deliberately *two*
    conditions:

      * **no option text**, and
      * **picture objects inside the question's own band on its page**.

    The first alone is also true of an extraction that genuinely failed, so on its own it would
    silence a real alarm. Requiring both keeps the alarm for the failure and clears it for the
    picture. `alphabet_optioned` is that second case: no option text and no picture either - a real
    defect, which keeps blocking.

    Measured over the corpus, the picture class is 10 papers / 20 questions and it is the ONLY
    remaining blocking class; `three_way` already reads every one of those papers at 80/80.
    """
    no_options = {int(item["number"]) for item in items if not (item.get("options") or {})}
    if not no_options:
        return set(), set()
    pictures = parsed.get("images") or []
    if not pictures and not parsed.get("pdf_path"):
        # Nothing was measured and there is no way to look, so no question can be shown to be
        # picture-optioned. Falling back to "clear them all" here would be the wrong direction: an
        # unread paper is not evidence that its option-less questions are picture questions.
        return set(), set(no_options)
    pictures_by_page = {}
    for entry in pictures:
        page = entry.get("page")
        if page is None:
            continue
        pictures_by_page.setdefault(int(page), []).append(
            (float(entry.get("y0") or 0.0), float(entry.get("x0") or 0.0),
             float(entry.get("y1") or 0.0), float(entry.get("x1") or 0.0)))
    # Vector-drawn figures are not carried in `parsed`, and they are most of this class: measured
    # on `1001_藥師_藥理學與藥物化學` page 4, zero image objects and 31 drawing operations. Read
    # here for the pages that actually hold an option-less question, which is a couple of pages per
    # paper rather than the whole document.
    pdf_path = parsed.get("pdf_path")
    if pdf_path:
        for item in items:
            page = item.get("page")
            if int(item["number"]) in no_options and page is not None:
                if int(page) not in pictures_by_page:
                    pictures_by_page[int(page)] = _picture_boxes_of_page(pdf_path, int(page))
    picture_optioned, alphabet_optioned = set(), set()
    for item in items:
        number = int(item["number"])
        if number not in no_options:
            continue
        page = item.get("page")
        box = item.get("box")
        found = False
        if page is not None and box:
            top, bottom = float(box[1]), float(box[3])
            for y0, x0, y1, x1 in pictures_by_page.get(int(page), ()):
                # A picture that begins inside the question's band belongs to it. Measured on
                # `1001_醫事檢驗師_臨床生理學與病理學`: Q18's four trace objects sit at y=379 to 581 on
                # page 1, all inside Q18's own band. A picture outside the band is another
                # question's, and claiming it would clear the alarm for the wrong question.
                if y0 >= top - 2.0 and y0 <= bottom + 2.0:
                    found = True
                    break
        (picture_optioned if found else alphabet_optioned).add(number)
    return picture_optioned, alphabet_optioned


def stage_gate(parsed, triage_report, sheets):
    """The deterministic gate. No model, no judgement of meaning: only structure and
    character classes that can be decided outright."""
    items = parsed["items"]
    numbers = sorted(int(item["number"]) for item in items)
    # How many questions the paper has is a fact the official answer sheet already states, and it
    # states it as a printed table numbered from 1. Counting anchors in the question paper is an
    # inference from the same page that produced the items, so the two agree except where the
    # reading is wrong - which is exactly the case the gate is trying to catch. Using the
    # inference as the standard therefore cannot catch it: an item invented from a figure axis
    # label is counted as an anchor too, and the counts match.
    #
    # Measured across the corpus, the sheet is decisive: 189 of 192 medical-technologist papers
    # print exactly `1` to `80`, and the pharmacists' papers print `1` to `80` or `1` to `50`.
    # The two exceptions are papers whose sheet could not be read at all, and there the anchor
    # count is used instead, because a paper with no readable sheet has no stated count and
    # something must bound the reading.
    sheet_count = _sheet_question_count(sheets)
    expected = sheet_count or triage_report.get("question_anchors") or 0
    gaps = sorted(set(range(1, max(numbers) + 1)) - set(numbers)) if numbers else []

    # A question whose options are pictures has no option text to extract, so the gate saw
    # `options: 0` and reported both `options-not-four` and `answer-not-on-sheet` - two alarms for
    # one fact, neither of which could ever clear. The fact is not a defect: the paper prints four
    # pictures, the answer sheet names one of them, and the picture IS the option. Measured: this is
    # the only blocking class left in the corpus - 10 papers, 20 questions, e.g.
    # `1001_醫事檢驗師_臨床生理學與病理學` Q18 `下列腦波圖中何者為多棘慢波複合波？` and Q38
    # `此附圖中，何者是左肋緣下斜掃描？`. `three_way` already reads all of them at 80/80.
    #
    # Recognised by MEASUREMENT, not by a keyword:
    #   * the question yielded no option text at all, AND
    #   * the page carries picture objects inside the question's own band.
    # The first alone is not enough - a genuinely broken extraction also yields no options - so the
    # second is required. Without it this would silence the alarm for real extraction failures.
    picture_optioned, alphabet_optioned = _classify_optionless(items, triage_report, parsed)

    bad_options, empty_stem, duplicate_labels = [], [], []
    for item in items:
        number = int(item["number"])
        options = item.get("options") or {}
        order = item.get("option_order") or sorted(options)
        if len(set(order)) != len(order):
            duplicate_labels.append(number)
        # A picture-option question HAS four options; they are printed as pictures. Counting them
        # as missing blocked 10 papers behind an alarm that could never clear.
        if len(options) != 4 and number not in picture_optioned:
            bad_options.append({"number": number, "options": len(options)})
        if not (item.get("stem") or "").strip():
            empty_stem.append(number)

    # The answers must come from the official sheet and must name options that exist.
    #
    # A corrections sheet is a re-issued table, not only a note: it reprints every answer
    # and marks the changed cells `＃`, with the meaning of `＃` in a note at the foot. So it
    # is merged as a table *and* read as corrections, in that order, and a `＃` cell is left
    # to the note rather than guessed at. See `qbr.corrections.authoritative_answers`.
    answer_texts = []
    correction_texts = []
    for role in ("answer", "corrected"):
        if role in sheets:
            (correction_texts if role == "corrected" else answer_texts).append(
                _answer_sheet_text(sheets[role]["path"], role=role))
    # A question whose options are pictures has no option text to extract, so the gate saw
    # `options: 0` and `answer: 18` and reported both `options-not-four` and
    # `answer-not-on-sheet` - two alarms for one fact. The fact is not a defect: the paper prints
    # four pictures, the answer sheet names one of them, and the picture is the option. Measured:
    # this is the ONLY blocking class left in the corpus - 10 papers, 20 questions, e.g.
    # `1001_醫事檢驗師_臨床生理學與病理學` Q18 `下列腦波圖中何者為多棘慢波複合波？` and Q38.
    #
    # The class is recognised by *measurement*, not by a keyword:
    #   * the question yielded no option text at all, AND
    #   * the page it sits on carries picture objects inside the question's own band.
    # Only the first is not enough - a genuinely broken extraction also yields no options - which
    # is exactly why the second is required. A question that is picture-optioned is then checked
    # against the alphabet rather than against the (empty) option keys, and is reported as its own
    # class so the reviewer is told *why* there is no option text to compare.
    picture_optioned, alphabet_optioned = _classify_optionless(items, triage_report, parsed)

    def _labels_for(item):
        number = int(item["number"])
        if number in picture_optioned:
            # The four printed pictures stand where A/B/C/D would; the key still names a letter.
            return list(corrections._LETTERS[:4])
        return list((item.get("options") or {}).keys())

    table, corrections_by_number = corrections.authoritative_answers(
        answer_texts, correction_texts,
        options_by_number={int(item["number"]): _labels_for(item) for item in items})
    keys = set(table)
    unmatched = []
    for item in items:
        number = int(item["number"])
        labels = table.get(number)
        if labels is None:
            unmatched.append(number)                     # no sheet speaks: an absence, not a dissent
        elif any(label not in _labels_for(item) for label in labels):
            unmatched.append(number)

    alarms = {
        "simplified_only_hits": triage_report.get("simplified_only_hits", 0),
        "pua_characters": triage_report.get("pua_characters", 0),
        "pua_marker_characters": triage_report.get("pua_marker_characters", 0),
        "lost_glyph_characters": triage_report.get("lost_glyph_characters", 0),
        "replacement_characters": triage_report.get("replacement_characters", 0),
        "damaged_characters": triage_report.get("damaged_characters", 0),
    }

    # A character the font could not map is a defect at a known place, not a reason to refuse a
    # paper. The character renders correctly on the page - `轉胺\ue2c6` reads as 轉胺酶 - so the
    # question is answerable by a person reading the PDF, which is exactly what the review UI asks
    # them to do; only the text layer lacks it. Measured over 192 papers of medical technologist:
    # 11 papers carry 2 to 10 of them, and each one is a single character inside one word.
    #
    # They are therefore carried to the question that contains them, the same way a voided answer
    # is carried to its question, and the reviewer sees precisely where to look. What still blocks
    # a paper is damage that is *pervasive* - replacement characters, control characters, or a
    # damage ratio over the declared bound - because that is a statement about the whole reading
    # rather than about one word in it.
    lost_glyph_items = []
    for item in items:
        blob = (item.get("stem") or "") + "".join((item.get("options") or {}).values())
        if any(repair.is_lost_glyph(blob, position)
               for position, char in enumerate(blob) if repair.is_bullet_char(char)):
            lost_glyph_items.append(int(item["number"]))

    blocking = []
    if not items:
        blocking.append("no-items")
    if gaps:
        blocking.append("numbering-gaps:%d" % len(gaps))
    if expected and len(items) != expected:
        blocking.append("count-mismatch:items=%d expected=%d(%s)"
                        % (len(items), expected,
                           "answer-sheet" if sheet_count else "anchors"))
    if bad_options:
        blocking.append("options-not-four:%d" % len(bad_options))
    if empty_stem:
        blocking.append("empty-stem:%d" % len(empty_stem))
    if duplicate_labels:
        blocking.append("duplicate-option-labels:%d" % len(duplicate_labels))
    if unmatched:
        blocking.append("answer-not-on-sheet:%d" % len(unmatched))
    if alarms["replacement_characters"]:
        blocking.append("replacement-codepoints:%d" % alarms["replacement_characters"])

    return {
        "verdict": "publish" if not blocking else "quarantine",
        "blocking": blocking,
        "item_count": len(items),
        "expected_count": expected,
        "expected_count_source": "answer-sheet" if sheet_count else "anchor-count",
        "numbering_gaps": gaps,
        "options_not_four": bad_options,
        # Reported as its own class rather than silently cleared: not a defect, but the reviewer
        # has to be told that these questions have no option text because the options are pictures,
        # or the empty rows look like an extraction failure.
        "picture_optioned": sorted(picture_optioned),
        "alphabet_optioned": sorted(alphabet_optioned),
        "empty_stem": empty_stem,
        "duplicate_option_labels": duplicate_labels,
        "answer_not_on_sheet": unmatched,
        "answer_numbers_known": len(keys),
        "lost_glyph_items": lost_glyph_items,
        "alarms": alarms,
    }, table


def _answer_sheet_text(path, *, role="answer"):
    """Read an answer sheet with the engine that keeps the grid's gaps.

    Kept as a name because the two stages below call it and the log lines read better for it;
    the reading itself is `qbr.answer_sheets.read_table_text`, where the whole argument about
    which engine measures a table well is written out. It moved there when the batch driver
    needed the same reading, and the two copies had already begun to differ.
    """
    return answer_sheets.read_table_text(path, role=role)


# --------------------------------------------------------------------------- S4 records

def stage_records(parsed, gate, table, sheets, meta, registry_key, review_status):
    rows, sources = [], {}
    answer_texts, correction_texts = [], []
    for role in ("answer", "corrected"):
        if role not in sheets:
            continue
        text = _answer_sheet_text(sheets[role]["path"], role=role)
        (correction_texts if role == "corrected" else answer_texts).append(text)
    _table, corrections_by_number = corrections.authoritative_answers(
        answer_texts, correction_texts,
        options_by_number={int(item["number"]): list((item.get("options") or {}).keys())
                           for item in parsed["items"]})
    lost_glyphs = set(gate.get("lost_glyph_items") or ())
    answer_tables = {}
    for role, texts in (("answer", answer_texts), ("corrected", correction_texts)):
        if texts:
            merged = {}
            for text in texts:
                merged = canon.merge_answer_tables(merged, canon.parse_answer_table(text))
            answer_tables[role] = merged
    for item in parsed["items"]:
        number = int(item["number"])
        labels = table.get(number)
        said = [role for role, parsed_table in answer_tables.items() if parsed_table.get(number)]
        if number in corrections_by_number:
            said.append("corrected")
        source = "+".join(said) or None
        sources[number] = source
        correction = corrections_by_number.get(number)
        flags = list(item.get("_flags") or [])
        # A character the font could not map is carried to its own question, so the reviewer is
        # sent to the one place that needs looking at instead of being told the paper is suspect.
        if number in lost_glyphs:
            flags.append("lost-glyph-in-text-layer")
        answer_text = None
        if correction is not None:
            # The correction's *meaning* is carried alongside the answer, because a tuple
            # of letters cannot say "this question was voided". `送分` is written where a
            # letter would be, so that nothing downstream reads `ABCD` as four answers.
            answer_text = correction.as_answer()
            if correction.is_void:
                flags.append("answer-voided-by-correction")
            else:
                flags.append("answer-widened-by-correction")
        rows.append(package.build_question(
            item, meta=meta, answer=labels, registry_key=registry_key,
            answer_source=source, flags=flags, review_status=review_status,
            answer_text=answer_text))
    return rows, sources


# --------------------------------------------------------------------------- S6 verify

def stage_verify(package_dir, platform_app, db_container, review_status):
    """Run the platform's own validators against the package.

    Two gates, both of which already exist and neither of which was written here: the
    catalog's package validator, and the platform's importer in dry-run. This stage decides
    nothing; it reports what they said. A validator that cannot be reached is reported as
    unreached, never as passed.
    """
    results = {}

    # A validator that refuses the package *because no human has reviewed it* is reporting a
    # governance gap, not a defect in what was built. The two are told apart, because a
    # pipeline that reads them alike will either hide a real break or grow a workaround that
    # certifies machine output as human-reviewed.
    review_marker = 'metadata.review_status=accepted'
    # Found, not counted to: `dirname(dirname(PKG)) + "tw-national-exam-catalog"` pointed outside
    # the repository in a standalone clone, where the validator would simply never run and the
    # gate would silently pass. A gate that cannot find its validator must not look satisfied.
    catalog_validator = os.path.join(paths.repo_root(), "scripts",
                                     "validate_question_bank_package.py")
    if os.path.isfile(catalog_validator):
        proc = subprocess.run([sys.executable, catalog_validator, package_dir,
                               "--format", "json"],
                              capture_output=True, text=True, check=False)
        try:
            report = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            report = {}
        structural, governance = [], []
        for issue in report.get("issues") or []:
            if issue.get("severity") != "error":
                continue
            for example in issue.get("examples") or [{}]:
                missing = example.get("missing") or []
                # An issue whose *only* unmet field is the human-review marker is a
                # governance gap. Anything else is a defect in what was built.
                if missing and all(item == review_marker for item in missing):
                    finding = {"code": issue.get("code"), "count": issue.get("count")}
                    if finding not in governance:
                        governance.append(finding)
                else:
                    finding = {"code": issue.get("code"), "count": issue.get("count"),
                               "missing": missing[:5]}
                    if finding not in structural:
                        structural.append(finding)
        results["catalog_package_validator"] = {
            "exit_code": proc.returncode,
            "review_status": review_status,
            "error_count": report.get("error_count"),
            "warning_count": report.get("warning_count"),
            "structural_findings": structural,
            "governance_findings": governance,
        }
    else:
        results["catalog_package_validator"] = {
            "exit_code": None, "summary": "validator not found",
            "structural_findings": [{"code": "validator-missing"}], "governance_findings": []}

    importer = os.path.join(platform_app, "scripts", "import_question_bank_package.py")
    if os.path.isfile(importer):
        report_json = os.path.join(package_dir, "_platform_dry_run_report.json")
        proc = subprocess.run(
            [sys.executable, importer, "--package-dir", package_dir,
             "--db-container", db_container, "--report-json", report_json],
            capture_output=True, text=True, check=False)
        results["platform_import_dry_run"] = {
            "exit_code": proc.returncode,
            "summary": (proc.stdout or proc.stderr or "").strip()[-1500:],
            "report_json": report_json if os.path.isfile(report_json) else None,
        }
    else:
        results["platform_import_dry_run"] = {"exit_code": None,
                                              "summary": "importer not found at %s" % importer}
    return results


def assert_no_addresses(paths):
    """Every delivered artifact, grepped for a host, an IP or a URL. Reported, not assumed."""
    offenders = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for match in _ADDRESS.finditer(text):
            offenders.append({"path": os.path.basename(path), "match": match.group(0)})
    return offenders


# --------------------------------------------------------------------------- driver

def run_stages(args):
    registry_key = args.registry_key
    # `moex:115090:308:0504:1` -> paper 115090, subject_code 0504, ordinal 1. The key is
    # colon-separated and the roles (`:question`, `:answer`) hang off the end of it, so the
    # paper is the second field whether or not a role is present.
    bits = str(registry_key or "").split(":")
    parts = {"paper": bits[1] if len(bits) > 1 else None,
             "year": bits[2] if len(bits) > 2 else None,
             "subject": bits[3] if len(bits) > 3 else None,
             "session": bits[4] if len(bits) > 4 else None}
    meta = {
        "year": int(args.year or (parts.get("year") or 0)),
        "exam_number": int(args.ordinal),
        "category_name": args.category,
        "subject_name": args.subject,
        "category_code": args.category_code or parts.get("subject"),
        "subject_code": args.category_code or parts.get("subject"),
        "exam_code": parts.get("paper"),
        "question_set": 1,
        "slug": args.slug,
        "subject_mapping_note": None,
    }

    run_dir = os.path.abspath(args.out)
    os.makedirs(run_dir, exist_ok=True)

    manifest = {
        "component_id": COMPONENT_ID,
        "git_sha": _git_sha(),
        "started_at": utc_now(),
        "finished_at": None,
        "registry_key": registry_key,
        "package_version": args.package_version,
        "stages": {},
        "database_written": False,
        "review_events_written": False,
    }

    sheets = {}
    triage_report = {}
    parsed = None
    gate = {}
    table = {}
    rows = []

    for stage in STAGES:
        if args.only and stage not in args.only:
            manifest["stages"][stage] = {"status": "skipped-by-request"}
            continue
        try:
            if stage == "S0_intake":
                paths, how = sheet_paths(
                    args.asset_root, year=meta["year"], category=meta["category_name"],
                    ordinal=meta["exam_number"], subject=meta["subject_name"],
                    registry_key=registry_key)
                if args.question_pdf:
                    paths["question"] = args.question_pdf
                if args.answer_pdf:
                    paths["answer"] = args.answer_pdf
                if args.corrected_pdf:
                    paths["corrected"] = args.corrected_pdf
                sheets = stage_intake(paths)
                detail = {"resolution": how, "sheets": {role: {
                    "bytes": info["bytes"], "sha256": info["sha256"],
                    "relative": package.relative_asset(info["path"])}
                    for role, info in sheets.items()},
                    "corrected_present": "corrected" in sheets}
            elif stage == "S1_triage":
                triage_report = stage_triage(sheets)
                detail = triage_report
            elif stage == "S2_dual":
                parsed, detail = stage_dual(sheets)
            elif stage == "S3_gate":
                gate, table = stage_gate(parsed, triage_report, sheets)
                detail = gate
                if gate["verdict"] != "publish" and not args.force:
                    manifest["stages"][stage] = {"status": "blocked", "detail": detail}
                    manifest["finished_at"] = utc_now()
                    write_json(os.path.join(run_dir, "run_manifest.json"), manifest)
                    print(json.dumps({"stage": stage, "status": "blocked",
                                      "blocking": gate["blocking"]}, ensure_ascii=False))
                    return 1
            elif stage == "S4_records":
                # The sheet paths belong to the record: a reader of the package must be able
                # to walk from an answer back to the paper that spoke it.
                meta.update({
                    "question_pdf": sheets.get("question", {}).get("path"),
                    "answer_pdf": sheets.get("answer", {}).get("path"),
                    "corrected_pdf": sheets.get("corrected", {}).get("path"),
                })
                rows, sources = stage_records(parsed, gate, table, sheets, meta, registry_key,
                                              args.review_status)
                detail = {"records": len(rows),
                          "answer_sources": {str(k): v for k, v in sorted(sources.items())},
                          "review_status": args.review_status}
            elif stage == "S5_package":
                if not rows:
                    raise ValueError("S5 needs S4; no records to package")
                manifest_doc = package.build_package(
                    rows, meta=meta, registry_key=registry_key,
                    package_version=args.package_version,
                    output_dir=os.path.join(run_dir, "package"),
                    provenance={
                        "source_sheets": {role: {"sha256": info["sha256"],
                                                 "relative": package.relative_asset(info["path"])}
                                          for role, info in sheets.items()},
                        "deterministic_gate": {"verdict": gate.get("verdict"),
                                               "blocking": gate.get("blocking")},
                        "triage_class": triage_report.get("triage_class"),
                        "parser": ADAPTER_PARSER,
                    },
                    review_status=args.review_status)
                detail = {"package_version": manifest_doc["package_version"],
                          "questions": manifest_doc["counts"]["questions"],
                          "manifest_sha256": sha256_file(
                              os.path.join(run_dir, "package", "manifest.json"))}
                # The queue is a view of the same records, not a second pipeline. It is
                # written beside the package so that `serve` can open it directly.
                if args.emit_candidates:
                    queue_dir = os.path.join(run_dir, "review-ui")
                    candidates_path = os.path.join(queue_dir, "candidates.jsonl")
                    issues_path = os.path.join(queue_dir, "issues.csv")
                    # The queue is a view of the same records, not a second pipeline. It
                    # carries no page numbers and no crops: the reviewer reads the paper in
                    # a PDF pane beside the text, which is what human eyes are for. Locating
                    # a question in a printed table and cutting it out is work for the image
                    # questions, where the question *is* the picture - and that is a later
                    # step, done by a local model, after this flow is stable.
                    review_queue.write_candidates(candidates_path, rows, gate=gate,
                                                  extra_metadata={"review_pipeline": ADAPTER_PARSER})
                    review_queue.write_issues(issues_path, rows, gate=gate)
                    detail["review_ui"] = {
                        "candidates": candidates_path,
                        "candidates_sha256": sha256_file(candidates_path),
                        "issues": issues_path,
                        "candidate_count": len(rows),
                    }
            elif stage == "S6_verify":
                package_dir = os.path.join(run_dir, "package")
                if not os.path.isdir(package_dir):
                    raise ValueError("S6 needs S5; no package to verify")
                detail = stage_verify(package_dir, args.platform_app, args.db_container,
                                      args.review_status)
                detail["machine_addresses_in_artifacts"] = assert_no_addresses([
                    os.path.join(package_dir, "manifest.json"),
                    os.path.join(package_dir, "questions.jsonl"),
                    os.path.join(package_dir, "subjects.json")])
                # Only a *structural* failure fails the run. A governance gap is carried on
                # the record and reported, never papered over by relabelling the data.
                failures = []
                for name, result in detail.items():
                    if not isinstance(result, dict):
                        continue
                    if result.get("exit_code") not in (0, None):
                        if result.get("structural_findings"):
                            failures.append(name)
                if detail["machine_addresses_in_artifacts"]:
                    failures.append("machine-address-in-artifact")
                detail["failures"] = failures
                detail["unmet_requirements"] = [
                    {"requirement": "human review accepted",
                     "state": "metadata.review_status=accepted",
                     "why": ("GOV-05 fixes AI as advisory only. No human has reviewed these "
                             "records, so the catalog validator refuses the package. This is "
                             "a governance gap to be decided, not a build defect."),
                     "decides": "REV-04 / REL-01 / GOV-05"}
                ] if args.review_status != "accepted" else []
                manifest["stages"][stage] = {
                    "status": "failed" if failures else "passed", "detail": detail}
                if failures:
                    manifest["finished_at"] = utc_now()
                    write_json(os.path.join(run_dir, "run_manifest.json"), manifest)
                    print(json.dumps({"stage": stage, "status": "failed",
                                      "failures": failures}, ensure_ascii=False))
                    return 1
                continue
            manifest["stages"][stage] = {"status": "passed", "detail": detail}
        except Exception as error:                        # noqa: BLE001 - a failed stage is the finding
            manifest["stages"][stage] = {"status": "error",
                                         "error": "%s: %s" % (type(error).__name__, error)}
            manifest["finished_at"] = utc_now()
            write_json(os.path.join(run_dir, "run_manifest.json"), manifest)
            print(json.dumps({"stage": stage, "status": "error", "error": str(error)},
                             ensure_ascii=False))
            return 2

    manifest["finished_at"] = utc_now()
    manifest["input_artifacts"] = [
        {"relative": package.relative_asset(info["path"]), "sha256": info["sha256"]}
        for info in sheets.values()]
    manifest["output_artifacts"] = _hash_tree(os.path.join(run_dir, "package"))
    write_json(os.path.join(run_dir, "run_manifest.json"), manifest)
    print(json.dumps({
        "run": run_dir,
        "stages": {name: data["status"] for name, data in manifest["stages"].items()},
        "questions": (manifest["stages"].get("S5_package", {}).get("detail") or {}).get("questions"),
    }, ensure_ascii=False, indent=2))
    return 0


ADAPTER_PARSER = "qbr_dual_engine_deterministic"


def _hash_tree(root):
    if not os.path.isdir(root):
        return []
    out = []
    for base, _dirs, names in os.walk(root):
        for name in sorted(names):
            if name.startswith("_"):
                continue
            path = os.path.join(base, name)
            out.append({"relative": os.path.relpath(path, root).replace(os.sep, "/"),
                        "sha256": sha256_file(path), "bytes": os.path.getsize(path)})
    return out


def _git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=PKG, capture_output=True,
                              text=True, check=False).stdout.strip() or None
    except Exception:                                     # noqa: BLE001
        return None


def describe():
    return {
        "component_id": COMPONENT_ID,
        "stages": list(STAGES),
        "inputs": {"registry_key": "the catalog's stable key, e.g. moex:115090:308:0504:1",
                   "asset_root": "directory holding the official PDFs (no default; never baked in)",
                   "out": "run directory; every artifact is written below it",
                   "platform_app": "path to platform-app, for the dry-run verifier"},
        "outputs": {"run_manifest.json": "every stage, its status, and the hash of its artifacts",
                    "package/": "manifest.json, questions.jsonl, subjects.json, groups.jsonl, "
                                "asset_manifest.jsonl"},
        "side_effects": {"database": "none", "review_events": "none", "catalog_checkpoint": "none",
                         "network": "none"},
        "exit_codes": {"0": "all stages passed", "1": "a gate blocked (quarantine)",
                       "2": "a stage errored", "3": "retryable external error",
                       "4": "unretryable data error", "5": "validation failed"},
        "host_addresses_in_artifacts": "refused; S6 greps for them and fails the run",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="run", choices=("run", "describe"))
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--registry-key", required=False)
    parser.add_argument("--asset-root", required=False)
    parser.add_argument("--out", required=False)
    parser.add_argument("--year", type=int)
    parser.add_argument("--ordinal", type=int, default=1)
    parser.add_argument("--category", default="醫事檢驗師")
    parser.add_argument("--subject", default="生物化學與臨床生化學")
    parser.add_argument("--category-code")
    parser.add_argument("--slug", default="medtech")
    parser.add_argument("--package-version", default="tw-national-exam-medtech-v0.0.1")
    parser.add_argument("--question-pdf")
    parser.add_argument("--answer-pdf")
    parser.add_argument("--corrected-pdf")
    parser.add_argument("--platform-app",
                        default=os.path.join(paths.workspace_root(), "platform-app"))
    parser.add_argument("--db-container", default="exam_repat_dev_db")
    parser.add_argument("--review-status", default=package.REVIEW_STATUS_MACHINE_ONLY,
                        help="metadata.review_status written into the package. The catalog "
                             "validator only accepts 'accepted', which only a human review "
                             "may produce (GOV-05); the default therefore does not claim it.")
    parser.add_argument("--emit-candidates", action="store_true", default=True,
                        help="also write review-ui/candidates.jsonl for the Review UI")
    parser.add_argument("--no-emit-candidates", dest="emit_candidates", action="store_false")
    parser.add_argument("--only", nargs="*", choices=STAGES)
    parser.add_argument("--force", action="store_true",
                        help="publish despite blocking gate findings; the findings stay on record")
    args = parser.parse_args(argv)

    if args.describe or args.command == "describe":
        print(json.dumps(describe(), ensure_ascii=False, indent=2))
        return 0
    for required in ("registry_key", "asset_root", "out"):
        if not getattr(args, required):
            parser.error("--%s is required" % required.replace("_", "-"))
    return run_stages(args)


if __name__ == "__main__":
    raise SystemExit(main())
