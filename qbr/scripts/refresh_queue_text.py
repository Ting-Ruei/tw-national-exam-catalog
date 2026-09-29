# -*- coding: utf-8 -*-
"""就地刷新既有佇列的文字層表徵，並重算爭議索引。不重跑 MinerU、不改寫審核事件。

為什麼要這個腳本：`build_review_queue.py` 要從 `--work` 的 run 目錄重建，而本機常駐的
`data/review-queues/live` 是多次合併出來的（989 papers / 79,090 列），run 目錄已不完整。
所以對「已在盤上的佇列」做可重現的原地刷新，語意與 `build_review_queue.py` 一致：
① 上下標走 `qbr.extract` 的兩種拼法（閱讀面 `<sup>/<sub>`＝UA 樣式，≈Word；
   量測面 ASCII 壓平形）；② 爭議由 `qbr.disputes` 重算；③ `queue_index.json` 跟著重產。
**事件流與 findings 流不寫**（append-only，保帶；與 `build_review_queue.py` 同一契約）。

    .venv/bin/python scripts/refresh_queue_text.py --queue data/review-queues/live            # 寫
    .venv/bin/python scripts/refresh_queue_text.py --queue data/review-queues/live --check     # 只量

判準（写進回執）：列數不變、每列 `candidate_key` 集合不變、orphan=0、
`question_review_events` 的 key 全部仍在候選集內。任何一條不合 → 非零碼退出。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import extract, review_queue  # noqa: E402

TEXT_FIELDS = ("stem", "answer", "explanation", "shared_stem")
PAYLOAD_FIELDS = ("answer", "raw_answer")


def paper_of(row: dict) -> str:
    """列的卷標識，取 `metadata.question_pdf_relative` 的檔名（與 `queue_index.per_paper` 同鍵）。"""
    relative = str((row.get("metadata") or {}).get("question_pdf_relative") or "")
    return os.path.basename(relative).removesuffix(".pdf") if relative else ""


def to_markup(row: dict) -> int:
    """本列換了几个文字欄位（以 `1` 計數，非字串長度）。無變更返 `0`。"""
    changed = 0
    for field in TEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value:
            folded = extract.html_sup_sub(value)
            if folded != value:
                changed += 1
            row[field] = folded
    payload = row.get("answer_payload")
    if isinstance(payload, dict):
        for field in PAYLOAD_FIELDS:
            value = payload.get(field)
            if isinstance(value, str) and value:
                folded = extract.html_sup_sub(value)
                if folded != value:
                    changed += 1
                payload[field] = folded
        accepted = payload.get("accepted_values")
        if isinstance(accepted, list):
            payload["accepted_values"] = [extract.html_sup_sub(v) if isinstance(v, str) else v
                                         for v in accepted]
    for option in row.get("options") or []:
        value = option.get("text")
        if isinstance(value, str) and value:
            folded = extract.html_sup_sub(value)
            if folded != value:
                changed += 1
            option["text"] = folded
    return changed


def keyset(rows: list) -> set:
    return {row.get("candidate_key") for row in rows if row.get("candidate_key")}


def sha256_of(rows: list) -> str:
    import hashlib
    blob = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def rebuild_index(index: dict, rows: list) -> dict:
    """`per_paper` 的計數與各路徑的 sha 都從**當前行**重算；其餘欄位原样帶回（不發明）。"""
    by_paper = {}
    for row in rows:
        by_paper.setdefault(paper_of(row), []).append(row)
    per_paper = []
    for entry in index.get("per_paper") or []:
        paper = entry.get("paper")
        current = by_paper.get(paper)
        if not current:
            per_paper.append(dict(entry))              # 本卷不在本次列集：原样帶回並計入對帳
            continue
        refreshed = dict(entry)
        refreshed["questions"] = len(current)
        refreshed["candidates_sha256"] = sha256_of(current)
        per_paper.append(refreshed)
    known = {entry.get("paper") for entry in per_paper}
    for paper, current in by_paper.items():
        if paper not in known:
            per_paper.append({"paper": paper, "questions": len(current),
                             "candidates_sha256": sha256_of(current),
                             "category": (current[0].get("metadata") or {}).get(
                                 "normalized_category_name"),
                             "subject": (current[0].get("metadata") or {}).get(
                                 "normalized_subject_name"),
                             "year": (current[0].get("metadata") or {}).get("year"),
                             "ordinal": (current[0].get("metadata") or {}).get("exam_ordinal"),
                             "run": paper})
    index["per_paper"] = per_paper
    index["questions"] = len(rows)
    index["papers"] = len({paper_of(row) for row in rows})
    index["categories"] = sorted({(row.get("metadata") or {}).get("normalized_category_name") or ""
                                  for row in rows})
    index["subjects"] = sorted({(row.get("metadata") or {}).get("normalized_subject_name") or ""
                                for row in rows})
    index["years"] = sorted({str((row.get("metadata") or {}).get("year") or "") for row in rows},
                            reverse=True)
    index["order"] = [entry["paper"] for entry in per_paper]
    index["taxonomy"] = review_queue.taxonomy_of(per_paper)
    return index


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True, help="佇列根目錄（含 review-ui/）")
    parser.add_argument("--check", action="store_true", help="只量，不寫入")
    args = parser.parse_args(argv)

    ui = os.path.join(args.queue, "review-ui")
    candidates = os.path.join(ui, "candidates.jsonl")
    index_path = os.path.join(ui, "queue_index.json")
    events_path = os.path.join(ui, "question_review_events.jsonl")
    rows = [json.loads(line) for line in open(candidates, encoding="utf-8") if line.strip()]
    index = json.load(open(index_path, encoding="utf-8"))
    before_keys, before_count = keyset(rows), len(rows)

    changed = 0
    for row in rows:
        changed += 1 if to_markup(row) else 0
    # 爭議按卷重算（分組契約見 `qbr.disputes`）。`disputes_for_paper` 自備注 paper，會覆寫
    # row["metadata"]，故 paper 必以本檔讀得之為准（後面以它組索引，须在重算之後）。
    by_paper: dict = {}
    for row in rows:
        by_paper.setdefault(paper_of(row), []).append(row)
    for group in by_paper.values():
        review_queue.disputes_for_paper(group)           # 回傳序＝輸入序（實測）
    # `typology`（題型輪廓）在封裝時已算好，且只由文字長度決定；這個腳本只改拼法（
    # 同字數）故不重算。重算會讓 26 個鍵裡的 `*_length` 群少 1（標記比單字元長），
    # 與 golden 的對照就會假性失敗。實測：不重算時 26 鍵全等。

    after_keys = keyset(rows)
    orphans = sorted({str(json.loads(line).get("candidate_key"))
                       for line in open(events_path, encoding="utf-8") if line.strip()}
                      - after_keys)
    kinds = collections.Counter(d.get("kind") for row in rows for d in (row.get("disputes") or []))
    severities = collections.Counter(row.get("dispute_severity") for row in rows)

    # ---- 判準：列數不變、key 集合不變、orphan=0。任一条不合都不寫。----------
    problems = []
    if len(rows) != before_count or after_keys != before_keys or None in after_keys:
        problems.append("列或 candidate_key 對不攏：%d→%d" % (before_count, len(rows)))
    if orphans:
        problems.append("orphan=%d：%s" % (len(orphans), orphans[:3]))
    if problems:
        print("TABLE problems: " + "; ".join(problems))
        return 1

    markup_rows = sum(1 for row in rows if "<sup>" in json.dumps(row, ensure_ascii=False)
                      or "<sub>" in json.dumps(row, ensure_ascii=False))
    print("列 %d（改 %d 欄）| markup 列 %d | 筆 %d | orphan %d | 無 dispute 列 %d"
          % (len(rows), changed, markup_rows, sum(kinds.values()), len(orphans),
             sum(1 for row in rows if not row.get("disputes"))))
    print("kind 計 " + " ".join("%s=%d" % (k or "-", n) for k, n in sorted(kinds.items())))
    print("severity " + " ".join("%s=%d" % (k, n) for k, n in
                      sorted(severities.items(), key=lambda kv: str(kv[0]))))

    if args.check:
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup = os.path.join(ui, "candidates.jsonl.bak-%s" % stamp)
    os.replace(candidates, backup)                      # 同檔序列號（本腳本自己發給）
    with open(candidates, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.write("")
    with open(index_path, "w", encoding="utf-8") as handle:
        json.dump(rebuild_index(index, rows), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("寫入 %s（備份 %s）" % (candidates, os.path.basename(backup)))
    print("索引 %s" % index_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
