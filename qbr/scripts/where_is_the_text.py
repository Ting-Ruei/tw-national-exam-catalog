# -*- coding: utf-8 -*-
"""Where, in the whole of the official corpus, is the text of a record that cannot be aligned?

BLOCKERS §1 rests on a control of 14 records searched inside the *session folder the registry
key names*. That is one step short of the decisive question. This script asks the whole corpus:

  * if the text is found in some other paper                     -> a resolution defect, mine to fix;
  * if it is found in the same paper but under another number    -> a numbering defect, mine to fix;
  * if it is found nowhere, in neither witness                   -> a finding about the bank,
    and the only thing that may be reported to the owner as such.

Searching is done by exact substrings of several short windows taken from both witnesses of the
record (what the reviewer read, and what the machine read), because a legacy string that has been
damaged by a broken ToUnicode map still carries runs of characters that were never touched. The
corpus is folded once and cached (`data/cache/corpus_folded.jsonl`), since the fold is the same
for every question asked of it.
"""
import json
import os
import re
import sys
import time

# the sandbox root: this file lives one level down, in scripts/
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "src"))
sys.path.insert(0, os.path.join(HERE, "scripts"))

from qbr import canon, extract, repair                                    # noqa: E402
import three_way as tw                                                     # noqa: E402

CORPUS = ("/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog"
          "/國考題資料夾/10_official_pdf/by_official_catalog")
CACHE = os.path.join(HERE, "data", "cache", "corpus_folded.jsonl")
WINDOWS = (10, 8, 6)          # widest window first: a hit at ten is evidence, at six is a hint
MIN_RUN_CHARS = 24            # a stem shorter than this cannot carry a distinguishable window


def cjk_runs(text):
    """The unbroken runs of CJK the string carries, long enough to be searched for."""
    return re.findall(r"[⼀-鿿]{6,}", canon.fold(text or ""))


def windows_of(text, count=5):
    """Evenly spaced exact substrings of the longest CJK runs the stem carries."""
    runs = sorted(cjk_runs(canon.fold(text)), key=len, reverse=True)[:3]
    out = []
    for run in runs:
        for width in WINDOWS:
            if len(run) < width:
                continue
            step = max(1, (len(run) - width) // max(1, count - 1))
            for start in range(0, len(run) - width + 1, step):
                out.append(run[start:start + width])
    return list(dict.fromkeys(out))[:18]


def corpus_texts():
    """path -> folded text of its native layer, cached on disk."""
    if os.path.isfile(CACHE):
        table = {}
        with open(CACHE, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                table[row["p"]] = row["t"]
        return table
    paths = []
    for base, _dirs, names in os.walk(CORPUS):
        for name in names:
            if name.lower().endswith(".pdf"):
                paths.append(os.path.join(base, name))
    paths.sort()
    table = {}
    started = time.time()
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE + ".part", "w", encoding="utf-8") as handle:
        for position, path in enumerate(paths):
            try:
                rows = extract.extract_lines_a(path)
                kept, _masked = repair.mask_chrome(rows)
                text = canon.fold(repair.text_from_rows(kept or rows))
            except Exception:                                      # noqa: BLE001 - a bad PDF is a datum
                text = ""
            table[path] = text
            handle.write(json.dumps({"p": path, "t": text}, ensure_ascii=False) + "\n")
            if position % 800 == 0:
                print("  folded %d/%d PDFs (%.0f s)" % (position, len(paths), time.time() - started))
                handle.flush()
    os.replace(CACHE + ".part", CACHE)
    return table


def main(argv):
    import qdb
    limit = int(argv[0]) if argv else 40
    records = []
    with open(os.path.join(HERE, "data", "gold", "compare_records.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            verdict = row.get("verdict")
            verdict = eval(verdict) if isinstance(verdict, str) else (verdict or {})
            if verdict.get("alignment-class") == "not-found":
                records.append(row)
    print("not-found records in the shipped sample: %d" % len(records))
    sample = records[:limit] if limit else records
    print("folding the corpus …")
    table = corpus_texts()
    print("corpus folded: %d PDFs, %.1f MB of text" %
          (len(table), sum(len(v) for v in table.values()) / 1024.0 / 1024))

    index = tw.corpus_index()
    rows_by_id = {int(r["id"]): r for r in tw.load_csv_rows() if str(r.get("id")).isdigit()}
    meta = qdb.fetch_meta([int(r["id"]) for r in sample])

    buckets = {"same-paper": 0, "other-paper": 0, "nowhere": 0, "no-witness": 0}
    detail = []
    started = time.time()
    for position, row in enumerate(sample):
        ident = int(row["id"])
        human = row.get("H") or {}
        machine = row.get("L") or {}
        needles = []
        for witness in (human.get("stem"), machine.get("stem")):
            if witness and len(canon.fold(witness)) >= MIN_RUN_CHARS:
                needles += windows_of(witness)
        needles = [item for item in list(dict.fromkeys(needles)) if len(item) >= 6]
        if not needles:
            # A record whose two witnesses are both too short to be searched for is not a
            # record whose text is absent from the corpus: nothing has been asked, so nothing
            # may be answered. It is counted apart, and it is never reported as an absence.
            buckets["no-witness"] += 1
            detail.append({"id": ident, "verdict": "no witness long enough to search for",
                          "h-stem": canon.fold((human.get("stem") or ""))[:60],
                          "l-stem": canon.fold((machine.get("stem") or ""))[:60],
                          "category": row.get("category"), "number": row.get("number")})
            continue
        best_path, best_hits = None, 0
        hit_paths = []
        for path, text in table.items():
            hits = sum(1 for needle in needles if needle in text)
            if hits > best_hits:
                best_path, best_hits = path, hits
            if hits >= max(3, len(needles) // 3):
                hit_paths.append((hits, path))
        resolved = row.get("pdf")
        if best_hits == 0:
            buckets["nowhere"] += 1
            where = "nowhere"
        elif resolved and os.path.basename(best_path or "") == resolved:
            buckets["same-paper"] += 1
            where = "same paper (a numbering defect)"
        else:
            buckets["other-paper"] += 1
            where = "ANOTHER PAPER (a resolution defect)"
        detail.append({
            "id": ident, "needles": len(needles), "best-hits": best_hits,
            "resolved": resolved, "found-in": os.path.basename(best_path or "") or None,
            "papers-with-hits": len(hit_paths), "verdict": where,
            "category": row.get("category"), "number": row.get("number"),
        })
        if len(sample) <= 60 or position % 200 == 0:
            print("  [%4d/%d] id %s  %s  (best %d/%d windows%s)" % (
                position + 1, len(sample), str(ident)[:8], where, best_hits, len(needles),
                "" if not best_path else " → " + os.path.basename(best_path)[:40]))

    print("\n== where the %d unalignable records really are (%.0f s) ==" %
          (len(sample), time.time() - started))
    for key, value in sorted(buckets.items()):
        print("  %-12s %d" % (key, value))
    out = os.path.join(HERE, "reports", "where_the_text_is.jsonl")
    with open(out, "w", encoding="utf-8") as handle:
        for row in detail:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("detail: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
