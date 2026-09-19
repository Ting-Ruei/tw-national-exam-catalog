"""Three-way gold comparison + risk queue + optional blind AI audit.

Modes
  gold   sample human-reviewed candidates, re-extract their official PDF through the
         deterministic pipeline, then compare three views of the same item:
           machine (legacy MinerU normalised json) | human (approved correction) | new (ours)
         Adjudication rule agreed with the owner: the official PDF text layer is the
         authority, so a human-approved value that contradicts the PDF text is reported
         as such instead of being silently trusted.
  queue  rank the never-human-reviewed population by cheap deterministic risk signals so
         the first re-review has a defensible order (Protocol: quarantine, never guess).
  blind  optional AI audit. The model sees stem + options only (never the answer), answers,
         then the answer is compared with the record. Off by default; --endpoint points at
         any OpenAI-compatible service, so a local/cheap model can take this over later.

Reads only: data/db_snapshot/hr_candidates.csv, the throwaway restore container, and the
corpus PDFs. Writes only inside this sandbox (reports/, data/gold/).
"""

import argparse
import collections
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from difflib import SequenceMatcher  # noqa: E402


def similarity(of, seq2):
    """Character-level ratio, cheap and dependency-free."""
    if not of or not seq2:
        return 0.0
    matcher = SequenceMatcher()
    matcher.set_seq2(seq2)
    matcher.set_seq1(of)
    matcher.autojunk = False
    return matcher.ratio()

_HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, _HERE)

from qbr import canon, cjk, extract, paths, repair, triage  # noqa: E402
import qdb  # noqa: E402

# The corpus is **found**, not counted to. `dirname(dirname(PKG))` was correct only while this file
# sat at `pi_test/question_bank_rebuild/`, and correct in the merged layout only by coincidence (the
# two locations are the same depth). A standalone clone is one level shallower and the counted path
# leaves the repository. `qbr.paths` is the one place that decides, so all callers agree.
REPO = paths.repo_root()
PDF_ROOT = os.path.join(paths.asset_root(), "10_official_pdf", "by_official_catalog")
CSV_GOLD = os.path.join(PKG, "data", "db_snapshot", "hr_candidates.csv")
GOLD_DIR = os.path.join(PKG, "data", "gold")
REPORTS = os.path.join(PKG, "reports")
PROBE = "exam_copy_probe"

CSV_COLUMNS = ["id", "candidate_key", "source_registry_key", "answer_source_registry_key",
               "question_number", "question_type", "group_ref", "stem_text",
               "quality_status", "review_status", "issue_count", "parser_version", "normalized_candidate_json"]

_TEXT_STRIP = re.compile(r"[\s\u3000]+")
_PAREN_LETTER = re.compile(r"[\(\[]\s*([A-Da-d])\s*[\)\]]")


def norm(value):
    """Canonical form for comparison: no whitespace, objects serialised stably."""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _TEXT_STRIP.sub("", value or "")


def load_csv_rows(path=CSV_GOLD):
    rows = []
    with open(path, encoding="utf-8", newline="") as handle:
        for record in csv.reader(handle):
            if len(record) != len(CSV_COLUMNS):
                continue
            row = dict(zip(CSV_COLUMNS, record))
            row["id"] = int(row["id"])
            rows.append(row)
    return rows


# ----------------------------------------------------------------- PDF resolution
def corpus_index():
    """(category, year, session) -> list of pdf file paths, from the read-only corpus."""
    index = collections.defaultdict(list)
    for category in sorted(os.listdir(PDF_ROOT)):
        base = os.path.join(PDF_ROOT, category)
        if not os.path.isdir(base):
            continue
        for year in sorted(os.listdir(base)):
            year_dir = os.path.join(base, year)
            if not os.path.isdir(year_dir) or not year.isdigit():
                continue
            for session in sorted(os.listdir(year_dir)):
                session_dir = os.path.join(year_dir, session)
                if not os.path.isdir(session_dir):
                    continue
                for name in os.listdir(session_dir):
                    if name.lower().endswith(".pdf"):
                        index[(category, int(year), session)].append(os.path.join(session_dir, name))
    return index


_REGISTRY = None


def registry_table():
    """The manifests the corpus ships with, read once. `scripts/read_the_registry.py` is the reader.

    They are the authority for which paper a `source_registry_key` was harvested from: the row
    names the year, the ordinal of the examination, the category, the subject, the role of the
    sheet, the very path the asset was written to, and its digest besides - all of which the
    resolver below was made to infer out of the shape of a code by heuristic, and inferred
    wrong for the great part of the records it could not align (measured: 2,492 of the 3,401
    unalignable readings of the pinned sample, the text of the record standing in a paper of
    another session of the same year). Where the registry has a row for a key and the file it
    names is to be had, that is the answer and there is nothing further to be asked. Where it
    has none, the heuristic is gone back upon, and says so on the face of the record.
    """
    global _REGISTRY
    if _REGISTRY is None:
        try:
            import read_the_registry
            _REGISTRY, _rows, _conflicts = read_the_registry.load_registry()
        except Exception:                                   # noqa: BLE001 - no registry is a datum
            _REGISTRY = {}
    return _REGISTRY


def registry_entry_for(key):
    """The manifest row for one registry key, when its file is where the row says it is."""
    entry = registry_table().get(key)
    if not entry:
        return None
    destination = entry.get("destination") or ""
    if destination and os.path.isfile(destination):
        return entry
    return None


def registry_parts(key):
    """`moex:104030:105:0201:1:question` -> (paper, year_or_code, subject_code, session, role)."""
    bits = (key or "").split(":")
    if len(bits) < 6:
        return None
    return {"paper": bits[1], "year": bits[2], "subject": bits[3], "session": bits[4], "role": bits[5]}


def resolve_pdf(key, category, index):
    """Locate the official PDF for a candidate. Returns (path, how) or (None, reason)."""
    parts = registry_parts(key)
    if not parts:
        return None, "bad-key"
    wanted_session = "第%s次" % int(parts["session"]) if parts["session"].isdigit() else parts["session"]
    paper = parts["paper"]
    candidates = []
    for (cat, year, session), files in index.items():
        if cat != category:
            continue
        if session not in (wanted_session, parts["session"]):
            continue
        candidates.extend(files)
    if not candidates:
        for (cat, year, session), files in index.items():
            if cat == category:
                candidates.extend(files)
    if not candidates and paper:
        year_hint = paper[:3]
        for files in index.values():
            candidates.extend(path for path in files if os.path.basename(path).startswith(year_hint))
        candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return None, "no-folder-for-category"
    leading = [os.path.basename(p) for p in candidates]
    exact = [p for p, n in zip(candidates, leading) if n.startswith(paper[:3]) or paper[:3] in n]
    pool = exact or candidates
    if len(pool) == 1:
        return pool[0], "single"
    by_subject = []
    for path in pool:
        name = os.path.basename(path)[:-4]
        tokens = name.split("_")
        subject_token = tokens[2] if len(tokens) > 2 else ""
        by_subject.append((len(set(subject_token) & set(paper + parts["subject"])), path))
    by_subject.sort(reverse=True)
    return by_subject[0][1], "heuristic"


# --------------------------------------------------------------------- extraction
_ITEMS_CACHE = {}

#: A line that is a question's own number and nothing else. Used only to recover the question's
#: band, so it is anchored: `18 下列…` is a start, `54.3 mL/min…` inside a stem is not.
#:
#: The trailing requirement is what makes this safe. A question number is followed by a separator
#: (`.`, `．`, `、`) or by a space and then the question's text; a decimal inside a sentence is
#: followed directly by another digit after the dot. Measured on
#: `1152_藥師(二)_藥學(四)(包括調劑學與臨床藥學)` Q53, whose stem runs over three lines and
#: contains `54.3 mL/min/1.73 m²`: reading that as question 54 ended Q53's band at y=416.7, three
#: lines too early, and the figure objects belonging to Q53 (y=472-813) fell outside its own band - so
#: the question looked like an extraction failure instead of a picture question.
_BAND_NUMBER = re.compile(r"^(\d{1,3})(?:\s*[.．、]\s*|\s+)(?!\d)\S")


def analyse_items(pdf_path):
    """「全卷解析（一次）」——雙引擎讀取、遮罩、分題，產出整卷之「條目」。

    「純凈」：本函數不假緩，無內部之緩存，是為對照之基準——故可久，可為典。
    讀萬卷者，此其一也；對照三者，此之謂也。凡例：以「卷」為「單位」，
    而非以「題」為「單位」——同卷多題，止於一紙之中。

    The reading of a whole paper, uncached and repeatable: read both engines, mask the
    chrome by geometry, segment the declared parts. `_parse_items()` is this function
    memoised, so the equivalence of the two is what the cache test asserts.
    """
    rows_a = extract.extract_lines_a(pdf_path)
    rows_b = extract.extract_lines_b(pdf_path)
    kept_a, _ = repair.mask_chrome(rows_a)
    kept_b, _ = repair.mask_chrome(rows_b)
    # Superscript spans are folded back into their host line before the text is assembled.
    # Without this the ion formulas on page 8 come apart: the superscript's box sits 1.1
    # points higher, so it lands in a different y-bucket and sorts ahead of the line it
    # belongs to, truncating options A and C and pushing their remainder into the stem.
    text_a = repair.text_from_rows(kept_a)
    text_b = repair.text_from_rows(kept_b)
    # Detection and segmentation must be fed the same view of the paper. The style used to
    # be scored on the raw extraction rows and then applied to the reading-order merged
    # lines: a number that is a cell of its own in the one view is part of a merged line in
    # the other, and the paper then silently lost every question but one (measured on
    # 1021_醫事檢驗師_生物化學與臨床生化學.pdf: 1 record out of 80, 75 lines unassigned, while
    # the continuity check still reported full coverage and no gaps at all).
    # `segment_mixed` reads the declared parts of a paper separately, so that an essay part
    # numbered 一、二、… and a test part numbered 1 2 … in one and the same file both come
    # out, instead of one of them being taken for the whole of it.
    items, residual, diag = repair.segment_mixed(
        text_a,
        extra_views=[[row.get("text") or "" for row in kept_a],
                     [row.get("text") or "" for row in kept_b]])
    # The picture objects and the band each question owns, carried out of the reading because
    # deciding whether an option-less question is a *picture* question is a measurement on the
    # page, not a judgement about the text. `1001_醫事檢驗師_臨床生理學與病理學` Q18 prints four
    # electroencephalogram traces as four picture objects at y=379-581 on page 1, all inside Q18's
    # own band; the text layer has no options there because there are none to have.
    #
    # Both are attached here rather than re-derived later, so the gate reads the same page the
    # segmentation did and cannot disagree with it about where a question is.
    images = extract.extract_images_a(pdf_path)
    _attach_bands(items, kept_a)
    # `pdf_path` is carried so a later stage can look at a specific page again without being
    # handed the path separately. Only the pages that need it are re-read.
    return {"items": items, "text_a": text_a, "text_b": text_b, "images": images,
            "pdf_path": pdf_path,
            "style": diag.get("style"), "residual": residual, "diag": diag}


#: How far below a question's number a line may still belong to it before the successor ends it.
#: Not a threshold on meaning: the band ends at the next question's number, and this only bounds
#: how much trailing material is swept into the last question of a page.
def _attach_bands(items, rows):
    """Give every question its page and its band, from the rows it was built out of.

    A band is `(x0, y0, x1, y1)`: from the question's own number down to the next question's number
    on the same page - the rule the segmentation already uses. Only used to decide whether a picture
    object falls inside a question, so it is deliberately generous at the bottom: a picture that
    starts within the question belongs to it even if it extends past the following number.
    """
    located = []
    for row in rows:
        text = (row.get("text") or "").strip()
        match = _BAND_NUMBER.match(text)
        if not match:
            continue
        located.append((int(match.group(1)), int(row.get("page") or 0),
                        float(row.get("x0") or 0.0), float(row.get("y0") or 0.0)))
    located.sort(key=lambda entry: (entry[1], entry[3]))
    starts = {}
    for index, (number, page, x0, y0) in enumerate(located):
        # The successor on the same page ends the band; the last question on a page runs to the
        # foot of it, which is how a figure printed under the final question is claimed.
        bottom = None
        for other_number, other_page, _ox, other_y in located[index + 1:]:
            if other_page != page:
                break
            if other_y > y0:
                bottom = other_y
                break
        starts.setdefault(number, []).append((page, x0, y0, bottom))
    for item in items:
        number = int(item.get("number") or 0)
        found = starts.get(number)
        if not found:
            continue
        page, x0, y0, bottom = found[0]
        item["page"] = page
        item["box"] = [x0, y0, x0, bottom if bottom is not None else y0 + 10_000.0]


def _parse_items(pdf_path):
    """`analyse_items()` memoised per paper: 以「卷」為緩存之單位.

    「分離」之大義：「解析全卷」與「選取一題」判為兩途。Reading the same paper once for all
    of its questions avoids starting the extraction tools again for every single item
    (that was the source of pollution in the earlier design, one subprocess per question).
    """
    if pdf_path not in _ITEMS_CACHE:
        _ITEMS_CACHE[pdf_path] = analyse_items(pdf_path)
    return _ITEMS_CACHE[pdf_path]


def _references_of(reference_stem):
    """Every witness that is to the same record, in the order they are to be trusted.

    A single reference is not enough: the machine reading is the text as the legacy pipeline
    damaged it (broken ToUnicode maps, merged options, invented simplified characters), while
    the human reading is the text as a reviewer repaired it. Asking the one alone finds the
    wrong question about the paper; the best of the two is what a question actually is.
    """
    if reference_stem is None:
        return []
    pool = reference_stem if isinstance(reference_stem, (list, tuple)) else [reference_stem]
    return [item for item in (folded.strip() if isinstance(folded, str) else str(folded)
                              for folded in pool) if item]


def _pick_target(items, number, reference_stem, align_min=canon.ALIGN_MIN):
    """「選取」——自「條目」中，「按」（題號）「數」，「揀（選）一題」。

    「名從」（題號命中）：以 number 為準，先與 reference_stem「校」（相似度高下）；
    「姓動」（題號未命中）：則「觀」（察）全「題」，「轉」（變）向「內容」檢（索）。
    「宏觀辨識」與「微觀辨析」相須。返回 (target, matched_by, ratio)。
    """
    target = None
    matched_by = "none"
    ratio = 0.0
    witnesses = _references_of(reference_stem)

    def agreement(item):
        """How far this item is the reading of the question the record is the copy of.

        Three measures, of which the best is taken, because which of them fits depends on how
        the paper happened to be typeset and how the record happened to be harvested:

          * stem against stem, the plain case;
          * the whole reading against the record, for the case the segmenter had to leave the
            options inside the stem - 76.8% of the judged records, where the bullets that mark
            them are printer's marks off the private-use plane and so give nothing to split
            apart at;
          * the record *contained* in the whole reading, which is the honest question when one
            of the two texts is a piece of the other: a similarity there measures how alike two
            strings are, and a question with its options appended is not unlike it, it is
            longer. Measured, that dilution is what pulled genuine records below the floor:
            id 1158 (#9) and id 1160 (#11), whose text stands in the very paper at the very
            number each names, scored 0.782 and 0.507 and were quarantined for it.
        """
        if not witnesses:
            return 1.0                       # the number alone identifies the item
        def option_text(option):
            # an item straight out of the segmenter carries dicts; one that has been through a
            # round of serialisation into JSON and back may carry bare strings. Either is read.
            if isinstance(option, str):
                return option
            if isinstance(option, dict):
                return option.get("text") or option.get("body") or ""
            return ""
        stem = canon.comparable((item or {}).get("stem") or "")
        full = stem + "".join(canon.comparable(option_text(option))
                              for option in ((item or {}).get("options") or []))
        scores = []
        for witness in witnesses:
            wanted = canon.comparable(witness)
            if not wanted:
                continue
            scores.append(similarity(stem, wanted))
            scores.append(similarity(full, wanted))
            scores.append(canon.containment(witness, full))
        return max(scores) if scores else 0.0

    by_number = next((it for it in items if str(it.get("number")) == str(number)), None)
    if by_number is not None:
        ratio = agreement(by_number)
        if ratio >= align_min:
            target, matched_by = by_number, "number"
    if target is None:
        # content search: permitted, but only down to the declared floor. Before, the best
        # of an arbitrary number of candidates was adopted however unlike the question asked
        # - a ratio of 0.029 still "matched", and 51.4% of the gold sample was judged on a
        # question other than the one it named. Below the floor there is no match, and the
        # absence is reported as such instead of being scored.
        best, best_ratio = None, 0.0
        if witnesses:
            for item in items:
                score = agreement(item)
                if score > best_ratio:
                    best, best_ratio = item, score
        if best is not None and best_ratio >= align_min and best_ratio >= ratio:
            target, ratio, matched_by = best, best_ratio, "content"
    return target, matched_by, ratio


def pick_item(parsed, number, reference_stem=None):
    """Select one numbered item out of an already read paper. Pure; the other half of the
    「同分異構」: 解析（analyse_items）與選取（pick_item）分居，各司其職。

    選取之法：題號命中，若數相符——不相符，則不取（quarantine, never guess）。「參」曰：配準。
    """
    items, text_a, diag = parsed["items"], parsed["text_a"], parsed["diag"]
    target, matched_by, ratio = _pick_target(items, number, reference_stem)
    signals = {"matched_by": matched_by, "match_ratio": round(ratio, 3)}
    # A paper whose own continuity check failed cannot supply items worth trusting: the
    # numbers themselves are then in question, so the whole paper is refused rather than
    # a pick made out of it. The reason travels into the report, not into a silent repair.
    if not diag.get("ok", True):
        target = None
        signals["alignment"] = "rejected"
        signals["rejected_because"] = list(diag.get("reasons") or [])
    elif target is None:
        signals["alignment"] = "not-found"
    else:
        signals["alignment"] = "accepted"
    signals.update({
        "style": diag.get("style"),
        "items": len(items),
        "residual_lines": len(parsed["residual"]),
        "coverage": (diag.get("detail") or {}).get("coverage"),
        "gaps": len((diag.get("detail") or {}).get("gaps") or []),
    })
    if target is None:
        return None, signals
    options = target.get("options") or {}
    labels = sorted(k for k in options if k)
    return {
        "number": target.get("number"),
        "stem": (target.get("stem") or "").strip(),
        "options": {k: (options.get(k) or "").strip() for k in labels},
        "labels": labels,
        "text": text_a,
    }, signals


def extract_item(pdf_path, number, reference_stem=None):
    """Deterministic re-extraction of one numbered item from the official PDF.

    「結構」（宏觀）：_parse_items（全卷解析，以 path 緩存）→ pick_item（選取一題）
    → 構建「信號」（比較）。「同分異構」——「解析」與「選取」分居，各司其職，皆
    「比較（三大）」之要也。
    """
    return pick_item(_parse_items(pdf_path), number, reference_stem)


# ------------------------------------------------------------------- three ways
def judge(field, mine, theirs, pdf_authority):
    """Return a verdict for one field given the three witnesses."""
    a, b, c = norm(mine), norm(theirs), norm(pdf_authority)
    if not b and not c:
        return "empty-both"
    if a == c and b == c:
        return "all-agree"
    if a == b and a != c:
        return "both-sides-wrong-vs-pdf"
    if b == c and a != c:
        return "machine-wrong-human-fixed-it"
    if a == c and b != c:
        return "human-introduced-change"
    return "three-ways-differ"


def options_to_dict(blob):
    try:
        data = json.loads(blob) if isinstance(blob, str) else (blob or {})
    except ValueError:
        return {}
    if isinstance(data, dict):
        opts = data.get("options")
        if isinstance(opts, dict):
            return {str(k): str(v) for k, v in opts.items()}
        if isinstance(opts, list):
            out = {}
            for entry in opts:
                if isinstance(entry, dict):
                    key = str(entry.get("label") or entry.get("key") or entry.get("letter") or "").upper()
                    out[key] = str(entry.get("text") or entry.get("value") or "")
            return out
    return {}


def answer_of(blob):
    try:
        data = json.loads(blob) if isinstance(blob, str) else (blob or {})
    except ValueError:
        return None
    if isinstance(data, dict):
        for key in ("answer", "correct_answer", "answer_key"):
            if key in data:
                value = data.get(key)
                if isinstance(value, list):
                    return "".join(str(v).strip() for v in value)
                return str(value).strip()
    return None


# ------------------------------------------------------------------------ modes
def run_gold(args):
    """Draw a gold set: reviewed, spread over the corpus, and re-readable.

    Three stages, in this order, because each of them discards records the later ones would
    otherwise have read in vain:

      1. the source end - ask the copy of the review database which candidates of the wanted
         categories carry a human correction at all. An unreviewed candidate is not gold;
         `--allow-unreviewed` is there for when the reviewed population is wanted to be
         enlarged for a special purpose.
      2. a systematic draw over the whole of what remains (`members[::step]`), not the first
         N rows of the export - the head of the file is one category of one year, and a gold
         set drawn from it is a sample of one paper, not of the corpus.
      3. the fetch of the metadata, only for the records actually drawn, so that the
         per-row look-up over the latest correction is not paid for the whole universe.
    """
    rows = load_csv_rows()
    index = corpus_index()
    wanted = tuple(args.categories) if args.categories else ()
    keep = qdb.coarse_ids(list(wanted), only_gold=not args.allow_unreviewed)
    universe = [r for r in rows if str(r["id"]).isdigit() and int(r["id"]) in keep] if keep else list(rows)
    if args.universe and len(universe) > args.universe:
        step = max(1, len(universe) // args.universe)
        universe = universe[::step][: args.universe]
    meta = qdb.fetch_meta([int(r["id"]) for r in universe])
    if wanted:
        universe = [r for r in universe
                    if ((meta.get(r["id"]) or {}).get("category") or "").startswith(wanted)]
    buckets = collections.defaultdict(list)
    for row in universe:
        info = meta.get(row["id"]) or {}
        buckets[((info.get("category") or "?"), row.get("quality_status") or "?")].append(row)
    picked = []
    for _key, members in sorted(buckets.items()):
        stride = max(1, len(members) // max(1, args.per_bucket))
        picked.extend(members[::stride][: args.per_bucket])
    os.makedirs(GOLD_DIR, exist_ok=True)
    results, verdicts, unresolved = [], collections.Counter(), 0
    alignments = collections.Counter()
    ratios = []
    for row in picked:
        number = row["question_number"]
        key = row["source_registry_key"]
        info = meta.get(row["id"]) or {}
        category = info.get("category") or guess_category(key, row)
        # The subject has to be carried down to the resolver: several papers of one session
        # share the same <year><session> prefix (1061_藥師_藥事行政與法規 and
        # 1061_藥師_藥物分析與生藥學), so without it the first file of the pool is taken, which is
        # a paper of another subject entirely.
        pdf_path, how = resolve_pdf(key, category, index, info.get("subject"))
        if not pdf_path:
            unresolved += 1
            continue
        # the reviewer's own reading of the stem, when there is one: the second witness
        human_hand = info.get("human")
        human_stem_for_pick = human_hand.get("stem") if isinstance(human_hand, dict) else None
        item, signals = extract_item(pdf_path, number,
                                    [human_stem_for_pick, _machine_stem_of(row)])
        grade = ("judged" if item is not None and signals.get("alignment") == "accepted"
                 else ("quarantined" if signals.get("alignment") == "rejected" else "not-found"))
        alignments[grade] += 1
        if signals.get("match_ratio") is not None:
            ratios.append(float(signals["match_ratio"]))
        machine = options_to_dict(row["normalized_candidate_json"])
        human_blob = info.get("human")
        human = options_to_dict(human_blob) if human_blob else {}
        machine_stem = None
        try:
            machine_stem = (json.loads(row["normalized_candidate_json"]) or {}).get("stem")
        except ValueError:
            machine_stem = None
        human_stem = (human_blob or {}).get("stem") if isinstance(human_blob, dict) else None
        pdf_stem = (item or {}).get("stem")
        record = {
            "id": row["id"], "candidate_key": row["candidate_key"], "registry": key,
            "category": category, "pdf": os.path.relpath(pdf_path, PDF_ROOT), "match": how,
            "found_in_pdf": bool(item), "signals": signals,
            "verdict_options": judge("options", machine, human, (item or {}).get("options") or {}) if item else "no-item",
            "verdict_stem": judge("stem", machine_stem, human_stem, pdf_stem) if item else "no-item",
            "answer_machine": answer_of(row["normalized_candidate_json"]),
            "answer_human": answer_of(human_blob) if human_blob else None,
            "simplified_in_pdf": (item or {}).get("stem") and cjk.audit_text(pdf_stem or "")["simplified_only_hits"],
        }
        verdicts[record["verdict_options"]] += 1
        verdicts["stem:" + record["verdict_stem"]] += 1
        results.append(record)
    with open(os.path.join(GOLD_DIR, "three_way.jsonl"), "w", encoding="utf-8") as handle:
        for record in results:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    lines = ["# 三路对账 (three-way comparison: machine / human / official PDF)", "",
             "Authority on disagreement: the official PDF text layer (`pdf`); for an answer, "
             "the official answer sheet, the 更正答案 prevailing where it speaks.",
             "Sampled %d of %d human-reviewed candidates; %d could not be traced to a PDF." % (len(results), len(rows), unresolved), "",
             "## 读前必定（alignment: a reading is only judged when it reads the same question）", "",
             "| 分级 | 题数 |", "|---|---|"]
    for name in ("judged", "quarantined", "not-found"):
        lines.append("| %s | %d |" % (name, alignments.get(name, 0)))
    if ratios:
        ordered = sorted(ratios)
        lines += ["", "配准度（match ratio）: min %.3f, p50 %.3f, max %.3f"
                  % (ordered[0], ordered[len(ordered) // 2], ordered[-1])]
    lines += ["", "## 判定（verdicts）", "", "Only the rows of the `judged` grade above are "
              "a statement about the corpus; the rest is a statement about the reading of it.",
              "", "| verdict | count |", "|---|---|"]
    for name, count in verdicts.most_common(24):
        lines.append("| %s | %d |" % (name, count))
    lines.append("")
    lines.append("## How to read it")
    lines.append("")
    lines.append("- `machine-wrong-human-fixed-it` - the legacy pipeline was wrong and the human corrected it (expected, healthy).")
    lines.append("- `human-introduced-change` - machine equalled the PDF, the human moved away from it: a possible hidden error in the gold set.")
    lines.append("- `both-sides-wrong-vs-pdf` - both differ from the official text: the defect is still live in the database.")
    lines.append("- `three-ways-differ` - needs a ruling; send to the review queue.")
    lines.append("")
    with open(os.path.join(REPORTS, "three_way.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(json.dumps({"sampled": len(results), "unresolved": unresolved, "verdicts": dict(verdicts)}, ensure_ascii=False))
    return 0


def _machine_stem_of(row):
    try:
        return (json.loads(row.get("normalized_candidate_json") or "{}") or {}).get("stem") or row.get("stem_text")
    except ValueError:
        return row.get("stem_text")


def guess_category(key, row):
    for token in ("醫事檢驗師", "藥師", "醫事放射師", "醫師", "中醫師", "護理師", "物理治療師"):
        if token in (row.get("candidate_key") or "") or token in (row.get("stem_text") or ""):
            return token
    name = os.environ.get("QBR_GUESSED_CATEGORY", "")
    return name


def fetch_meta(ids):
    """Latest human correction + official category/subject, from the throwaway restore copy.

    Read-only: this container is a disposable restore of the dumped copy, and only SELECTs
    are issued against it.
    """
    if not ids:
        return {}
    wanted = ",".join(str(int(i)) for i in ids)
    sql = ("select (json_build_object("
           "  'id', c.id, 'category', d.official_category_name, 'subject', d.official_subject_name,"
           "  'human', (select e.corrected_candidate_json from exam.question_review_events e"
           "            where e.candidate_id = c.id and e.corrected_candidate_json is not null"
           "            order by e.id desc limit 1)))::text "
           "from exam.question_candidates c "
           "left join exam.official_documents d on d.registry_key = c.source_registry_key "
           "where c.id in (%s)" % wanted)
    try:
        proc = subprocess.run(["docker", "exec", PROBE, "psql", "-At", "-U", "probe", "-d", "qbank_copy", "-R", "", "-c", sql],
                              capture_output=True, text=True, timeout=240)
    except Exception as error:
        print("human corrections unavailable:", error)
        return {}
    out = {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            blob = json.loads(line)
        except ValueError:
            continue
        if blob.get("id") is None:
            continue
        out[int(blob["id"])] = {"category": blob.get("category"), "subject": blob.get("subject"),
                                "human": blob.get("human")}
    return out


def run_queue(args):
    rows = load_csv_rows()
    pool = [r for r in rows if r["review_status"] == "unreviewed"] or rows
    scored = []
    for row in pool:
        signals, score = risk(row)
        scored.append((score, row, signals))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "review_queue.csv"), "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["risk", "id", "candidate_key", "question_number", "quality_status", "signals"])
        for score, row, signals in scored:
            writer.writerow([score, row["id"], row["candidate_key"], row["question_number"], row["quality_status"], ";".join(signals)])
    band = collections.Counter()
    for score, _row, _s in scored:
        band["high" if score >= 6 else ("medium" if score >= 3 else "low")] += 1
    summary = {"population": len(pool), "bands": dict(band),
               "top": [{"id": r["id"], "risk": s, "signals": sig} for s, r, sig in scored[: args.show]]}
    with open(os.path.join(REPORTS, "review_queue_summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(json.dumps(summary["bands"], ensure_ascii=False), "written:", os.path.join(REPORTS, "review_queue.csv"))
    return 0


def risk(row):
    """Deterministic risk signals; no model, no guessing."""
    signals, score = [], 0
    options = options_to_dict(row["normalized_candidate_json"])
    filled = [k for k, v in options.items() if norm(v)]
    if options and len(filled) < 2:
        signals.append("options<2")
        score += 3
    if len(options) > 0 and len(set(norm(v) for v in options.values())) < len(options):
        signals.append("duplicate-options")
        score += 3
    stem = row["stem_text"] or ""
    if len(norm(stem)) < 12:
        signals.append("stem-too-short")
        score += 2
    if "？" not in stem and "?" not in stem and len(norm(stem)) > 40:
        signals.append("no-question-mark")
        score += 1
    if re.search(r"[，、]\s*[A-D][．.]", stem):
        signals.append("options-merged-into-stem")
        score += 3
    audit = cjk.audit_text(stem)
    if audit["simplified_only_hits"]:
        signals.append("simplified-only:%d" % audit["simplified_only_hits"])
        score += 2
    if audit["pua_characters"] or audit["replacement_characters"]:
        signals.append("unmapped-glyph")
        score += 3
    if row["question_type"] in ("", "unknown"):
        signals.append("type-unknown")
        score += 2
    if int(row["issue_count"] or 0) > 0:
        signals.append("issue-count:%s" % row["issue_count"])
        score += int(row["issue_count"])
    return signals, score


def run_blind(args):
    """Blind answering: the model never sees the answer, then we compare."""
    rows = load_csv_rows()
    index = corpus_index()
    sample = rows[:: max(1, len(rows) // max(1, args.items))][: args.items]
    meta = qdb.fetch_meta([r["id"] for r in sample])
    payload, scores = [], collections.Counter()
    for row in sample:
        key = row["source_registry_key"]
        category = (meta.get(row["id"]) or {}).get("category") or guess_category(key, row)
        pdf_path, _how = resolve_pdf(key, category, index,
                                    (meta.get(row["id"]) or {}).get("subject"))
        item, _signals = extract_item(pdf_path, row["question_number"]) if pdf_path else (None, {})
        if not item or not item["options"] or len(item["options"]) < 2:
            scores["skipped"] += 1
            continue
        options = item["options"]
        prompt = "以下是一題單一選擇題，請只回應選項字母。\n%s\n%s" % (
            item["stem"][:900], "\n".join("%s. %s" % (k, options[k][:200]) for k in sorted(options)))
        answer = answer_of(row["normalized_candidate_json"])
        record = {"id": row["id"], "registry": key, "record_answer": answer, "options": sorted(options)}
        if args.endpoint:
            started = time.perf_counter()
            reply, usage = call_model(args, prompt)
            record["model_answer"] = (reply or "").strip()[:8]
            record["usage"] = usage
            record["seconds"] = round(time.perf_counter() - started, 2)
            scores["agrees" if norm(record["model_answer"]) == norm(answer) else "differs"] += 1
        else:
            scores["prompts-built"] += 1
            record["prompt"] = prompt
        payload.append(record)
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "blind_audit.jsonl"), "w", encoding="utf-8") as handle:
        for record in payload:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps({"items": len(payload), "scores": dict(scores), "endpoint": args.endpoint or "(offline: prompts only)"}, ensure_ascii=False))
    return 0


def call_model(args, prompt):
    import urllib.error
    import urllib.request

    body = json.dumps({"model": args.model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": 12}).encode("utf-8")
    request = urllib.request.Request(args.endpoint.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + args.api_key})
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        return "", {"error": str(error)[:160]}
    text = ""
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = ""
    return text, data.get("usage") or {}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("gold", "queue", "blind"), default="queue")
    parser.add_argument("--per-bucket", type=int, default=6)
    parser.add_argument("--universe", type=int, default=1200)
    parser.add_argument("--categories", nargs="*", default=["醫事檢驗師", "藥師", "醫事放射師"])
    parser.add_argument("--items", type=int, default=12)
    parser.add_argument("--allow-unreviewed", action="store_true",
                        help="let candidates without any human correction into the gold draw")
    parser.add_argument("--show", type=int, default=12)
    parser.add_argument("--endpoint", default=os.environ.get("QBR_AUDIT_ENDPOINT", ""))
    parser.add_argument("--model", default=os.environ.get("QBR_AUDIT_MODEL", "default"))
    parser.add_argument("--api-key", default=os.environ.get("QBR_AUDIT_KEY", ""))
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    if args.mode == "gold":
        return run_gold(args)
    if args.mode == "blind":
        return run_blind(args)
    return run_queue(args)




# --------------------------------------------------------------------------------------
# Definitive PDF resolution (overrides the first sketch above).
# The official file name encodes everything: <year><session>_<category>_<subject>[_ANS|_MOD].pdf
# so the lookup is exact - no guessing, no fuzzy ratio, and the sheet role is respected.
# A candidate must be resolved against the *question* sheet unless its own registry key
# says it is an answer/corrected sheet.
# --------------------------------------------------------------------------------------
def resolve_pdf(key, category, index, subject=None):  # noqa: F811
    entry = registry_entry_for(key)
    if entry is not None:
        # An authority was consulted and it answered: this row pairs the key with the file that
        # was downloaded under it, and the digests of the two were verified to agree.
        return entry["destination"], ("registry"
                                      + ("+rebased" if entry.get("rebased") else ""))
    parts = registry_parts(key)
    if not parts:
        return None, "bad-key"
    # the year is carried in the first segment of the paper code (e.g. 115020 -> 115),
    # the second segment is the subject code, so it must not be read as a year
    head = (parts["paper"] or "")[:3]
    year = head if (head.isdigit() and 100 <= int(head) <= 119) else parts["year"]
    session, role = parts["session"], parts["role"]
    wanted_session = "第%s次" % int(session) if session.isdigit() else session
    prefix = "%s%s" % (year, session) if (year.isdigit() and session.isdigit()) else year
    pool = []
    for (cat, yr, sess), files in index.items():
        if cat != category or sess not in (wanted_session, session):
            continue
        pool.extend(files)
    if not pool:
        return None, "no-category-folder"

    def role_ok(name):
        upper = name.upper()
        if role == "answer":
            return "_ANS" in upper
        if role in ("corrected_answer", "mod"):
            return "_MOD" in upper
        return "_ANS" not in upper and "_MOD" not in upper

    strict = [p for p in pool if os.path.basename(p).startswith(prefix) and role_ok(os.path.basename(p))]
    # Everything from here on is an inference from the shape of a file name, and is labelled as
    # such: no registry row stood behind this key, so the paper is guessed at, and whoever reads
    # the record must be able to see that it was guessed.
    how = "guessed:exact"
    if not strict:
        # never silently substitute another year/session/paper: an unresolved lookup is a
        # finding in itself, and it is reported as such
        return None, "no-exact-name-match"
    if subject and len(strict) > 1:
        wanted = re.sub(r"[\s\u3000（）()《》\[\]/、，,。]", "", subject)
        best, score = None, -1.0
        for path in strict:
            tokens = os.path.basename(path)[:-4].split("_")
            have = re.sub(r"[\s\u3000（）()《》\[\]/、，,。]", "", tokens[2] if len(tokens) > 2 else "")
            if not have or not wanted:
                continue
            common = len(set(wanted) & set(have)) / float(max(len(set(wanted)), 1))
            if common > score:
                best, score = path, common
        if best:
            return best, how + "+subject"
    return strict[0], how


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
