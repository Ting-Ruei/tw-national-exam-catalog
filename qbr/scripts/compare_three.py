#!/bin/env python3
"""Three-way comparison after canonical normalisation (see qbr.canon for the rules).

    L  legacy machine output   (MinerU-era normalised json, copied database)
    H  human-approved record   (latest corrected json per candidate)
    P  the new method          (official PDF text layer, extracted deterministically)

P is the authority: the numbers below are deviations *from the PDF*, counted per field.
A similarity ratio is used only to rank divergent cases for human review; it never
decides whether an item passes.

    ./.venv/bin/python scripts/compare_three.py --categories 醫事檢驗師 藥師 醫事放射師
    ./.venv/bin/python scripts/compare_three.py --per-bucket 8 --universe 4000 --show 12
"""

import argparse
import collections
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

import qdb  # noqa: E402
import three_way as tw  # noqa: E402
from qbr import canon, extract, repair  # noqa: E402

REPORTS = os.path.join(PKG, "reports")
GOLD_DIR = os.path.join(PKG, "data", "gold")

_KEY_BEFORE = re.compile(r"(?<![\d])(\d{1,3})\s*[.、．:：)）]\s*([A-F])(?![A-Za-z])")
_KEY_BARE = re.compile(r"(?<![\d])(\d{1,3})\s+([A-F])(?![A-Za-z])")


_TABLES = {}


# 「遷移提純」：純淨之函數，歸於核心——parse_answer_table 今居 qbr.canon。
# 「對照實驗」：舊版（此）與新版（canon.parse_answer_table）證明「等價」。
# 「宏觀辨」答案卷之結構、「微觀」析試樣之性質——核心模塊（canon）已具。
parse_answer_table = canon.parse_answer_table


def sheet_table_cached(path):
    if path not in _TABLES:
        _TABLES[path] = parse_answer_table(sheet_text_cached(path))
    return _TABLES[path]


def answer_key_from_sheet(text, number):
    """The official answer for `number`, read from the answer sheet (role=answer)."""
    table = sheet_table_cached(text) if os.path.isfile(str(text)) else parse_answer_table(text)
    got = table.get(int(number)) if str(number).isdigit() else None
    if got:
        return got
    wanted = str(number)
    for pattern in (_KEY_BEFORE, _KEY_BARE):
        for match in pattern.finditer(text):
            if match.group(1) == wanted:
                return (match.group(2),)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(wanted) and canon.extract_answer(stripped):
            return canon.extract_answer(stripped)
    return None


def sheet_files_for(question_pdf):
    """The official answer sheet of a paper, and its 更正答案 when one was published.

    Both are derived from the paper's own path - same directory, file name plus `_ANS` /
    `_MOD` - and never from a search over the corpus: subject names share characters
    (「藥物分析」 and 「藥理學」), and a name match may well return the sheet of another paper,
    which is how an earlier revision of this resolver read official answers out of the
    wrong paper.
    """
    if not question_pdf:
        return None, None
    folder = os.path.dirname(question_pdf)
    stem = os.path.basename(question_pdf)[:-4]
    answer = os.path.join(folder, stem + "_ANS.pdf")
    corrected = os.path.join(folder, stem + "_MOD.pdf")
    return (answer if os.path.isfile(answer) else None,
            corrected if os.path.isfile(corrected) else None)


def answer_authority_for(row, number):
    """The official key for `number`: 答案 first, 更正答案 prevailing wherever it speaks.

    Measured on the gold sample: 39.0% of the records flagged "the recorded answer differs
    from the official one" belong to a paper for which an 更正答案 exists and was never
    opened, because the sheet resolver tested `_ANS` before `_MOD` and stopped at the first
    hit. The corrected sheet is now read as well, and it outranks the original.

    Returns `(labels, source)`, `source` naming the sheet the key came from so that every
    verdict can be traced back to its authority; `(None, None)` says the sheets are silent
    about this number, which is reported as such and not guessed from a neighbour.
    """
    sheets = row.get("_sheets") or {}
    tables = []
    for role in ("answer", "corrected"):
        path = sheets.get(role)
        if path:
            tables.append((role, sheet_table_cached(path)))
    merged = canon.merge_answer_tables(*[table for _role, table in tables])
    if str(number).isdigit():
        key = merged.get(int(number))
        if key:
            said = [role for role, table in tables if table.get(int(number))]
            return canon.as_label_set(key), "+".join(said) or None
    # The grid may be set in a shape the table parser cannot read; fall back to a scan of
    # the corrected sheet first, then of the original one.
    for role in ("corrected", "answer"):
        path = sheets.get(role)
        if not path:
            continue
        found = answer_key_from_sheet(path, number)
        if found:
            return canon.as_label_set(found), role
    return None, None


def load_pdfs_sources(records, index, meta, want_answer_sheet=True):
    """For every candidate record, locate its official PDFs (question and answer sheet)."""
    resolved, missing = 0, 0
    for row in records:
        info = meta.get(row["id"]) or {}
        category = info.get("category")
        subject = info.get("subject")
        key = row["source_registry_key"]
        path, how = tw.resolve_pdf(key, category, index, subject) if category else (None, "no-category")
        row["_pdf"], row["_pdf_how"] = path, how
        answer_sheet, corrected_sheet = sheet_files_for(path) if want_answer_sheet else (None, None)
        # Both are kept. The 更正答案 prevails over the 答案 wherever it declares a number,
        # and reading only the first of them (as an earlier revision did - `_ANS` was tested
        # first and the loop stopped) silently discarded every correction published.
        row["_sheets"] = {"answer": answer_sheet, "corrected": corrected_sheet}
        row["_sheet"] = corrected_sheet or answer_sheet
        resolved += 1 if path else 0
        missing += 0 if path else 1
    return resolved, missing


def compare_one(row):
    """Build the three canonical records for one candidate and compare them.

    Two things must hold before a witness may be believed: the PDF must be speaking about
    this very question (`aligned`, from the pick's match ratio and the paper's own
    continuity check), and the answer must have been read out of an official answer sheet
    (`answer_authority`). Where either fails, the record is quarantined rather than judged,
    because a wrong judgement published as a right one is the defect this whole exercise is
    meant to find, not to manufacture.
    """
    blob_l = None
    try:
        blob_l = json.loads(row.get("normalized_candidate_json") or "{}")
    except ValueError:
        blob_l = {}
    rec_l = canon.record("L", blob_l)

    rec_h = None
    blob_h = row.get("_human")
    if isinstance(blob_h, dict) and blob_h:
        rec_h = canon.record("H", blob_h)

    # Two witnesses to one record: what the human reviewer read, and what the machine read.
    # The one is repaired, the other is damaged, so the pick is made on the better of them.
    references = [stem for stem in ((rec_h or {}).get("stem"), (rec_l or {}).get("stem")) if stem]
    rec_p, signals = (None, {})
    if row.get("_pdf"):
        item, signals = extract_item_cached(row["_pdf"], row["question_number"], references)
        if item is not None:
            rec_p = canon.record("P", item)

    # 題號以讀者，字義左右 - but only when the reading is of this question at all.
    aligned = bool(rec_p) and signals.get("alignment") == "accepted"
    number = row.get("question_number")
    if number is None and rec_l:
        number = rec_l.get("number")
    authority, authority_source = (None, None)
    if (row.get("_sheets") or row.get("_sheet")) and number is not None:
        authority, authority_source = answer_authority_for(row, number)

    verdict = canon.compare(rec_l, rec_h, rec_p, answer_authority=authority, aligned=aligned)
    verdict["matched-by"] = signals.get("style")
    verdict["picked-by"] = signals.get("matched_by")
    verdict["match-ratio"] = signals.get("match_ratio")
    verdict["found-in-pdf"] = bool(rec_p)
    verdict["alignment-class"] = ("judged" if aligned else
                                 ("quarantined" if signals.get("alignment") == "rejected"
                                  else "not-found"))
    # Which ground the paper was stood upon: whether a manifest of the corpus named this key and
    # this file (registry), or whether the name of the file was guessed at from its shape. The
    # two must never be read as one another: the heuristic resolved the wrong paper for the
    # great part of the records that could not be aligned, and a reader of the report is
    # entitled to know which of the two put a question in front of the witness.
    verdict["pdf-resolution"] = row.get("_pdf_how") or ("none" if not row.get("_pdf") else "?")
    verdict["rejected-because"] = signals.get("rejected_because") or []
    verdict["paper-reasons"] = signals.get("rejected_because") or []
    if signals.get("items") is not None:
        verdict["items-in-paper"] = signals.get("items")
    if signals.get("residual_lines") is not None:
        verdict["residual-lines"] = signals.get("residual_lines")
    verdict["answer-authority"] = "".join(authority) if authority else None
    verdict["answer-authority-source"] = authority_source
    # 人工優先，但以官方答案卷為準：a missing human answer must not read as a wrong one,
    # so the machine's key is consulted before the comparison is made.
    recorded = canon.as_label_set((rec_h or {}).get("answer") if rec_h else None) \
        or canon.as_label_set((rec_l or {}).get("answer") if rec_l else None)
    verdict["answer-agrees-authority"] = (None if not authority
                                          else ("yes" if recorded == tuple(authority) else "no"))
    return {"rec_l": rec_l, "rec_h": rec_h, "rec_p": rec_p, "verdict": verdict, "signals": signals}


_PDF_CACHE = {}


def extract_item_cached(path, number, stem_hint):
    # the hint may be one stem or a list of them; the cache key must not depend on which
    key = (path, str(number), tw.norm(tw._references_of(stem_hint)))
    if key not in _PDF_CACHE:
        _PDF_CACHE[key] = tw.extract_item(path, number, stem_hint)
    return _PDF_CACHE[key]


def sheet_text_cached(path):
    if path not in _PDF_CACHE:
        rows = extract.extract_lines_a(path)
        kept, _ = repair.mask_chrome(rows)
        _PDF_CACHE[path] = repair.text_from_rows(kept)
    return _PDF_CACHE[path]


def stratified(records, per_bucket):
    """Group candidates into buckets by (category, quality_status) and pick evenly."""
    buckets = collections.defaultdict(list)
    for row in records:
        info = row.get("_meta") or {}
        buckets[((info.get("category") or "?"), row.get("quality_status") or "?")].append(row)
    picked = []
    for _key, members in sorted(buckets.items()):
        step = max(1, len(members) // max(1, per_bucket))
        picked.extend(members[::step][: per_bucket])
    return picked


def _cell(value):
    """One cell of a markdown table: a vertical bar must not end the row it is in."""
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def summarise(results, header):
    """Write the comparison out, and count it.

    Every table comes twice: once over all the records that were read, and once over only
    those of them whose reading was established to be about the same question. The second is
    the one to quote; the first is kept so that the size of the influence of the alignment
    on the figures is visible instead of being silently assumed.
    """
    fields = ("stem", "options", "answer")
    overall = {field: collections.Counter() for field in fields}
    judged = {field: collections.Counter() for field in fields}
    by_category = collections.defaultdict(lambda: {field: collections.Counter() for field in fields})
    alignment = collections.Counter()
    flags = collections.Counter()
    answer_checks = collections.Counter()
    authority_sources = collections.Counter()
    picked_by = collections.Counter()
    resolution = collections.Counter()

    for item in results:
        verdict = item["verdict"]
        category = (item.get("category") or "?")
        grade = verdict.get("alignment-class", "judged")
        alignment[grade] += 1
        picked_by[verdict.get("picked-by") or "?"] += 1
        resolution[verdict.get("pdf-resolution") or "?"] += 1
        for field in fields:
            overall[field][verdict[field]] += 1
            by_category[category][field][verdict[field]] += 1
            if grade == "judged":
                judged[field][verdict[field]] += 1
        for flag in verdict.get("flags", ()):
            flags[flag] += 1
        for reason in verdict.get("rejected-because") or ():
            flags["rejected:" + reason.split("-")[0]] += 1
        if verdict.get("answer-agrees-authority") is not None:
            answer_checks[verdict["answer-agrees-authority"]] += 1
        else:
            answer_checks["no-authority"] += 1
        authority_sources[verdict.get("answer-authority-source") or "none"] += 1

    total = len(results)
    lines = ["# 三路对比报告（正规化之后）", "",
             "依据：官方 PDF 文字层（P）为准则；L = 旧方法（MinerU），H = 人工审核记录。",
             "答案之准则：官方答案卷（_ANS），有更正答案者（_MOD）优先。",
             "样本 %d 题；数字均为「与 P 的偏差」计数，非合格率判定。" % total, "",
             "## 对齐（alignment：先定，后判）", "",
             "| 读数分级 | 题数的 | 占样本 |", "|---|---|---|"]
    for name in ("judged", "quarantined", "not-found"):
        count = alignment.get(name, 0)
        lines.append("| %s | %d | %.1f%% |" % (_cell(name), count, 100.0 * count / max(1, total)))
    lines += ["", "判读方式：" + "、".join("%s=%d" % (_cell(k), v) for k, v in picked_by.most_common()), ""]
    # On what ground each paper was stood under its record. `registry` is a manifest of the
    # corpus naming the file that was downloaded for the key; anything beginning `guessed:` is
    # the resolver having inferred a file from the shape of its name, and is to be read with
    # that allowance. The split belongs in the report because the two are not equal in worth:
    # the one put the wrong paper under every record the other put the right one under, and it
    # was the whole of what had been reported as a corpus that could not be found.
    lines += ["卷之所在：" + "、".join(
        "%s=%d" % (_cell(name), count) for name, count in resolution.most_common()), ""]
    lines += ["## 总计（按字段，全部样本）", ""]
    for field in fields:
        lines += ["**字段：%s**" % _cell(field), "", "| 判定 | 计数 | 佔該字段 |", "|---|---|---|"]
        total = sum(overall[field].values()) or 1
        for name, count in sorted(overall[field].items(), key=lambda kv: -kv[1]):
            lines.append("| %s | %d | %.1f%% |" % (_cell(name), count, 100.0 * count / total))
        lines.append("")
    lines += ["## 可信子集合（仅对齐合格者：judged）", ""]
    for field in fields:
        lines += ["**字段：%s**" % _cell(field), "", "| 判定 | 计数 | 佔可信子集合 |", "|---|---|---|"]
        total = sum(judged[field].values()) or 1
        for name, count in sorted(judged[field].items(), key=lambda kv: -kv[1]):
            lines.append("| %s | %d | %.1f%% |" % (_cell(name), count, 100.0 * count / total))
        lines.append("")
    lines += ["## normalisation 期间发现的标记", ""]
    for name, count in flags.most_common(20):
        lines.append("- %s: %d" % (_cell(name), count))
    lines += ["", "## 答案与官方答案卷的对照", ""]
    for name, count in answer_checks.most_common():
        lines.append("- %s: %d" % (_cell(name), count))
    lines.append("")
    lines.append("准则来源（答案取自哪一卷）：")
    for name, count in authority_sources.most_common():
        lines.append("- %s: %d" % (_cell(name), count))
    lines += ["", "## 分类（按考）", ""]
    for category, tables in sorted(by_category.items()):
        lines.append("### %s" % _cell(category))
        lines.append("")
        lines.append("| 字段 | 判定 | 计数 |")
        lines.append("|---|---|---|")
        for field in fields:
            for name, count in sorted(tables[field].items(), key=lambda kv: -kv[1]):
                lines.append("| %s | %s | %d |" % (field, _cell(name), count))
        lines.append("")
    header += lines
    return overall, flags, answer_checks

# ---------------------------------------------------------------------------
def main(argv):
    parser = argparse.ArgumentParser(description="compare three sources after normalisation")
    parser.add_argument("--categories", nargs="*", default=["醫事檢驗師", "藥師", "醫事放射師"])
    parser.add_argument("--universe", type=int, default=4000)
    parser.add_argument("--per-bucket", type=int, default=6)
    parser.add_argument("--show", type=int, default=8)
    parser.add_argument("--no-sheet", action="store_true", help="skip the official answer sheet")
    parser.add_argument("--allow-unreviewed", action="store_true",
                        help="also compare candidates that have no human correction")
    parser.add_argument("--ids-from", default=None,
                        help="re-read exactly the candidates of an earlier run: a .jsonl of "
                             "records carrying an id (e.g. data/gold/compare_records.jsonl). "
                             "The stratified draw is skipped, so the two runs are comparable "
                             "question for question instead of merely alike in distribution")
    args = parser.parse_args(argv)

    rows = tw.load_csv_rows()
    all_ids = [int(r["id"]) for r in rows if str(r["id"]).strip().isdigit()]
    wanted = tuple(args.categories)
    # 「分離與提純」——先於「源頭」(DB 端)過濾：3 類前綴 ＋（可選）「有人工修正」者，
    # 限於 CSV 之 id 集之內。如此，fetch_meta 不再「每行全表重掃」35,496，而僅取
    # 候選者——是為「綠色化學」之「從源頭減少污染」，「結構決定性質」之「順序」顛倒也。
    pinned = None
    if args.ids_from:
        pinned = set()
        with open(args.ids_from, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    ident = json.loads(line).get("id")
                except ValueError:
                    continue
                if str(ident).isdigit():
                    pinned.add(int(ident))
        pinned &= set(all_ids)
        print("ids pinned for re-reading: %d (from %s)" % (len(pinned), os.path.basename(args.ids_from)))
    keep = qdb.coarse_ids(wanted, only_gold=not args.allow_unreviewed,
                          restrict_ids=sorted(pinned) if pinned else all_ids)
    rows = [r for r in rows if int(r["id"]) in keep]
    if args.universe and not pinned:
        rows = rows[: args.universe]
    ids = [int(r["id"]) for r in rows]
    meta = qdb.fetch_meta(ids)
    for row in rows:
        row["_meta"] = meta.get(row["id"]) or {}
        row["_human"] = (meta.get(row["id"]) or {}).get("human")
    # 「宏觀一致性」自檢：粗篩已於 SQL 端過濾，此乃「複核」，以防「誤差」（有備無患）。
    rows = [r for r in rows if ((meta.get(r["id"]) or {}).get("category") or "").startswith(wanted)]
    if not args.allow_unreviewed:
        rows = [row for row in rows if isinstance(row.get("_human"), dict) and row["_human"]]
    # A run that is meant to be compared with an earlier one reads the same candidates, so
    # the draw is not redrawn; a fresh survey does draw stratified, to be representative.
    picked = sorted(rows, key=lambda row: int(row["id"])) if pinned else stratified(rows, args.per_bucket)
    index = tw.corpus_index()
    resolved, missing = load_pdfs_sources(picked, index, meta, want_answer_sheet=not args.no_sheet)

    results = []
    for row in picked:
        one = compare_one(row)
        one["id"] = row["id"]
        one["category"] = (row["_meta"].get("category") or "")
        one["subject"] = (row["_meta"].get("subject") or "")
        one["number"] = row["question_number"]
        one["pdf"] = os.path.basename(row.get("_pdf") or "")
        results.append(one)

    os.makedirs(REPORTS, exist_ok=True)
    os.makedirs(GOLD_DIR, exist_ok=True)
    with open(os.path.join(GOLD_DIR, "compare_records.jsonl"), "w", encoding="utf-8") as handle:
        for one in results:
            handle.write(json.dumps({
                "id": one["id"], "category": one["category"], "subject": one["subject"],
                "number": one["number"], "pdf": one["pdf"], "verdict": one["verdict"],
                "L": one["rec_l"], "H": one["rec_h"], "P": one["rec_p"],
            }, ensure_ascii=False) + "\n")
    header = []
    overall, flags, answer_checks = summarise(results, header)
    with open(os.path.join(REPORTS, "compare_three.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(header) + "\n")

    print("sampled=%d resolved=%d missing=%d" % (len(results), resolved, missing))
    for field in ("stem", "options", "answer"):
        print("  %-8s %s" % (field, dict(overall[field])))
    print("  flags     %s" % dict(flags))
    print("  answer vs official sheet: %s" % dict(answer_checks))
    resolution = collections.Counter(one["verdict"].get("pdf-resolution") or "?" for one in results)
    print("  paper resolved by: %s" % dict(resolution.most_common()))
    print("report: %s" % os.path.join(REPORTS, "compare_three.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
