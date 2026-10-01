# -*- coding: utf-8 -*-
"""figure-missing 證據包生產者：為 image_refs 為空、裁片在磁碟的題目，一次決策一列草案。

契約（owner 2026-10-01 裁決「停 5A/5B，改證據包式生產者」）：
- 輸入是 deterministic 掃描產生的工單列（`wo-4.1-figure-missing.jsonl`）——本腳本不重掃；
- 每列：queue 找不到 candidate_key ⇒ skipped；裁片清單空 ⇒ degraded；
  指揮者回答不可用 ⇒ degraded，**不預設 insert**（orchestrator.parse_figure_decision 的契約）；
- **refs ⊆ 磁碟清單** 由本腳本重驗（不是信模型文字）——虛構檔名被剔除並記 `ref_unverified`；
- **queue 一個 byte 都不動**：輸出是 advisory 草案流，落地的核准在人工；
- pin 與 `/v1/models` 不符 ⇒ 拒跑（exit 2）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import engines, orchestrator  # noqa: E402

#: 草案行自己的 schema 標籤：消費者與 verdict 流不同（decisions，不是判分）。
SCHEMA = "figure_missing_drafts v1"

CROPS_SUBDIR = os.path.join("review-ui", "crops")


def candidates_path(queue: str) -> str:
    return os.path.join(queue, "review-ui", "candidates.jsonl")


def load_questions(queue: str) -> dict:
    """`candidate_key` -> row，讀一次全部（79k 列，線性一遍）。"""
    rows: dict[str, dict] = {}
    with open(candidates_path(queue), encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("candidate_key"):
                rows[row["candidate_key"]] = row
    return rows


def sha8_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


def crop_hits_of(note: str) -> list[dict]:
    """工單 note 裡的 deterministic 掃描命中（逐欄）清單；解析不了就是沒有，不猜。"""
    text = note or ""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        hits = json.loads(text[start:end + 1])
        return hits if isinstance(hits, list) else []
    except json.JSONDecodeError:
        return []


def crop_listing(crops_root: str, run: str, pattern: str) -> tuple[list[str], list[str]]:
    """`（清單, 絕對路徑們）`：只收符合掃描 pattern 的檔名；目錄或 pattern 不成立就是空。"""
    directory = os.path.join(crops_root, run)
    if not pattern or not os.path.isdir(directory):
        return [], []
    import fnmatch
    names = sorted(fn for fn in os.listdir(directory)
                   if fnmatch.fnmatch(fn, pattern) and os.path.isfile(os.path.join(directory, fn)))
    return names, [os.path.join(directory, fn) for fn in names]


def envelope(lane: str, served: str, key: str, source_sha8: str, row_no: int, tag: str,
             crop_files: list[str], record: dict, note: str | None) -> dict:
    return {
        "schema": SCHEMA,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "lane": lane,
        "served_model": served,
        "tag": tag,
        "candidate_key": key,
        "source": {"workorder_sha8": source_sha8, "row": row_no},
        "crop_files": crop_files,
        "record": record,
        "note": note or None,
    }


def compare_files(path_a: str, path_b: str) -> dict:
    """A/B 兩輪草案的重決一致性：同 (decision, refs集合) 的比例，母體＝兩輪都有可用記錄的交集。"""
    def index(path):
        rows: dict[str, dict] = {}
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                record = row.get("record") or {}
                if row.get("candidate_key") and not record.get("degraded"):
                    rows[row["candidate_key"]] = record
        return rows

    a, b = index(path_a), index(path_b)
    both = sorted(set(a) & set(b))
    same = [k for k in both
            if (a[k].get("decision"), sorted(a[k].get("refs") or []))
            == (b[k].get("decision"), sorted(b[k].get("refs") or []))]
    rate = round(len(same) / len(both), 3) if both else None
    return {"both": len(both), "same": len(same), "rate": rate}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="figure-missing 證據包生產者（advisory）")
    parser.add_argument("--workorder", help="deterministic scan 工單 jsonl")
    parser.add_argument("--queue", default="data/review-queues/live")
    parser.add_argument("--out")
    parser.add_argument("--limit", type=int, default=0, help="最多跑幾列（0 = 全部）")
    parser.add_argument("--tag", default="A", help="跑別 A/B，戳進每一列")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--compare", nargs=2, metavar=("OUT_A", "OUT_B"),
                        help="比兩個輸出檔的重決一致性後離開")
    args = parser.parse_args(argv)

    if args.compare:
        print(json.dumps(compare_files(args.compare[0], args.compare[1]), ensure_ascii=False))
        return 0
    if not (args.workorder and args.out):
        parser.error("需要 --workorder 與 --out（--compare 模式除外）")

    lane = (os.environ.get("QBR_CONDUCTOR_ENGINE", "").strip() or orchestrator.CONDUCTOR_ENGINE)
    endpoint = engines.named(lane)
    served = engines.served_id(endpoint)
    if served and served != endpoint["name"]:
        print("⚠ pin %r ≠ /v1/models 回報 %r — 先用該 lane 的 _MODEL 變數更新 pin 再跑"
              % (endpoint["name"], served), file=sys.stderr)
        return 2

    questions = load_questions(args.queue)
    source_sha8 = sha8_of(args.workorder)
    crops_root = os.path.join(args.queue, CROPS_SUBDIR)

    tally: dict[str, int] = {}
    degraded = skipped = 0
    seconds_total = 0.0
    started = time.time()
    written = 0
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.workorder, encoding="utf-8") as source, \
            open(args.out, "a", encoding="utf-8") as sink:
        for row_no, line in enumerate(source, 1):
            if args.limit and row_no > args.limit:
                break
            row = json.loads(line)
            key = row.get("key") or ""
            question = questions.get(key)
            hits = crop_hits_of(row.get("note"))
            hit = hits[0] if hits else {}
            names, paths = crop_listing(crops_root, hit.get("run") or "",
                                        (hit.get("crop_pattern") or "").strip())
            images_sent = paths[:4]
            if question is None:
                skipped += 1
                record, note = {"degraded": True}, "佇列中找不到 candidate_key，跳過不猜"
            elif not names:
                skipped += 1
                record, note = {"degraded": True}, "磁碟上沒有符合掃描 pattern 的裁片，跳過不猜"
            else:
                decision, note = orchestrator.propose_figure_refs(
                    question, names, images_sent, endpoint=endpoint,
                    timeout=args.timeout, max_tokens=args.max_tokens)
                if decision is None:
                    degraded += 1
                    record = {"degraded": True, "decision_note": note}
                else:
                    verified = [r for r in decision.get("refs") or [] if r in names]
                    invented = len(decision.get("refs") or []) - len(verified)
                    if invented or (decision.get("decision") == "insert" and not verified):
                        decision["refs"] = verified
                        decision["ref_unverified"] = invented
                        if decision.get("decision") == "insert" and not verified:
                            note = "insert 沒有可用檔名（虛構者 %d 個已剔除）" % invented
                    if decision.get("decision") in ("none", "uncertain"):
                        decision["refs"] = []
                    record = decision
                    seconds_total += decision.get("seconds") or 0.0
            decision_kind = (record or {}).get("decision")
            if decision_kind:
                tally[decision_kind] = tally.get(decision_kind, 0) + 1
            sink.write(json.dumps(
                envelope(lane, served or endpoint["name"], key, source_sha8, row_no,
                         args.tag, images_sent and names[:4], record, note),
                ensure_ascii=False) + "\n")
            sink.flush()
            written += 1
    elapsed = time.time() - started

    print("figure-missing 生產者（%s）：%d 列 → %s" % (args.tag, written, args.out))
    print("  decisions: %s" % json.dumps(tally, ensure_ascii=False))
    print("  degraded: %d（找不到題目／裁片跳過 %d）　判讀秒數合計 %.1f s　牆上時鐘 %.1f s"
          % (degraded, skipped, seconds_total, elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())