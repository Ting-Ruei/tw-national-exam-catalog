# -*- coding: utf-8 -*-
"""修題證據包生產者：一列派工工單（人類 block）一次呼叫產一列文字草案（L2）。

契約（REPAIR_LOOP_PLAN.md §4 L2，owner 2026-10-03）：
- 輸入是派工工單列（`agent/dispatch.mjs` 寫的，或手寫）：`{key, human_block_reason,
  return_reasons, lessons, crops, fields}`——本腳本不重掃帳本、不重讀學習檔；
- 每列：queue 找不到 candidate_key ⇒ skipped；**裁片不存在於磁碟 ⇒ degraded**（鐵律 5：
  生產者只引用真實存在的證據）；模型回答不可用（fix 空／basis 空／insert 認不得／自報
  degraded）⇒ degraded，**不硬湊**；
- fix 與現值相同（或像片段：與原題幹相似度過低）⇒ degraded——沒有東西可 ✅ 的草案是雜訊；
- **queue 一個 byte 都不動**：草案走 bridge `propose` 進 `store/repair_drafts.jsonl`
  （status proposed），落地的核准在人（✅）；機器永不寫人審帳本；
- pin 與 `/v1/models` 不符 ⇒ 拒跑（exit 2）；
- lane 預設 `occamy-6bit`（做事模型，owner 2026-10-03：指揮者 GLM 只給對話子進程）。
  `QBR_REPAIR_WORKER_ENGINE` 換 lane。

  python3 qbr/scripts/repair_second_pass.py --workorder wo.jsonl --queue data/review-queues/live \
      --out store/scans/20261003-repair/repair_runA.jsonl
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

# bridge 是「草案寫入器」——與 UI/agent 同一份 do_propose（同一 schema、同一 provenance），
# 不在這裡重寫一份 append。以路徑載入（它自己也把 qbr/src 放上 sys.path）。
_spec_path = os.path.join(os.path.dirname(PKG), "repair_agent_test", "agent", "bridge.py")
_spec = None
bridge = None
if os.path.exists(_spec_path):
    import importlib.util
    _spec = importlib.util.spec_from_file_location("repair_agent_bridge", _spec_path)
    bridge = importlib.util.module_from_spec(_spec)

#: 一次送幾張裁片影像（與 figure 生產者同值；多了 prompt 失控，少了看不到關鍵圖）。
MAX_IMAGES = 4

#: 草案行自己的 schema 標籤：與 figure 生產者同形（decisions 流，不是判分）。
SCHEMA = "repair_second_pass v1"


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


def existing_crops(row: dict) -> tuple[list[str], list[str]]:
    """工單上的裁片路徑 →（存在的絕對路徑, 送影像用的清單）。虛構路徑在這裡就消失。"""
    paths = [str(p) for p in (row.get("crops") or []) if str(p).strip()]
    real = [p for p in paths if os.path.isfile(p)]
    return real, real[:MAX_IMAGES]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="修題證據包生產者（advisory）")
    parser.add_argument("--workorder", help="派工工單 jsonl（dispatch.mjs 或手寫）")
    parser.add_argument("--queue", default="data/review-queues/live")
    parser.add_argument("--out", help="本輪 run 記錄（audit trail；草案另走 bridge propose）")
    parser.add_argument("--limit", type=int, default=0, help="最多跑幾列（0 = 全部）")
    parser.add_argument("--tag", default="A", help="跑別 A/B，戳進每一列")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=800)
    args = parser.parse_args(argv)

    if not (args.workorder and args.out):
        parser.error("需要 --workorder 與 --out")

    lane = (os.environ.get("QBR_REPAIR_WORKER_ENGINE", "").strip() or "occamy-6bit")
    endpoint = engines.named(lane)
    served = engines.served_id(endpoint)
    if served and served != endpoint["name"]:
        print("⚠ pin %r ≠ /v1/models 回報 %r — 先用該 lane 的 _MODEL 變數更新 pin 再跑"
              % (endpoint["name"], served), file=sys.stderr)
        return 2

    if bridge is None:
        print("找不到 bridge.py（%s 不存在）——草案沒有寫入器，拒跑" % _spec_path, file=sys.stderr)
        return 2
    _spec.loader.exec_module(bridge)

    questions = load_questions(args.queue)
    source_sha8 = sha8_of(args.workorder)

    tally: dict[str, int] = {"drafted": 0, "degraded": 0, "skipped": 0}
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
            crops, images_sent = existing_crops(row)
            record: dict = {}
            note = None
            if question is None:
                tally["skipped"] += 1
                record, note = {"degraded": True}, "佇列中找不到 candidate_key，跳過不猜"
            elif not crops:
                tally["degraded"] += 1
                record, note = {"degraded": True}, "裁片不存在於磁碟（虛構路徑已剔除），跳過不猜"
            else:
                decision, note = orchestrator.propose_text_fix(
                    question,
                    block_reason=row.get("human_block_reason") or "",
                    return_reasons=row.get("return_reasons") or [],
                    lessons=row.get("lessons") or [],
                    crop_paths=images_sent, endpoint=endpoint,
                    timeout=args.timeout, max_tokens=args.max_tokens)
                if decision is None:
                    tally["degraded"] += 1
                    record = {"degraded": True, "decision_note": note}
                if decision is not None:
                    fix = bridge.strip_option_prefix(decision["fix"])
                    try:
                        field, current = bridge.resolve_text_target(question, decision["insert"])
                    except ValueError as exc:
                        tally["degraded"] += 1
                        record = {"degraded": True, "decision_note": str(exc)}
                        decision = None
                if decision is not None:
                    # refs ⊆ 磁碟 的對應物：fix 不得等於現值、不得像片段——生產者自己先擋，
                    # 不要把註定被 ✅ 拒絕的草案推給設計者。
                    if field == "stem" and not bridge.is_full_replacement(current, fix):
                        tally["degraded"] += 1
                        record = {"degraded": True, "decision_note":
                                  "fix 與原題幹差太多（像是建議或片段），不是整欄替換"}
                    elif fix == current:
                        tally["degraded"] += 1
                        record = {"degraded": True, "decision_note": "fix 與現值相同"}
                    else:
                        # `_die` 以 SystemExit 拒絕（欄位缺漏／crop 不存在）；一列的失敗是
                        # degraded 記錄，不是整輪 run 的死因。
                        try:
                            bridge.do_propose(argparse.Namespace(
                                key=key, fix=fix, insert=decision["insert"],
                                basis=decision["basis"],
                                crop=crops[0] if crops else None))
                        except SystemExit as exc:
                            tally["degraded"] += 1
                            record = {"degraded": True, "decision_note":
                                      "bridge propose 拒絕（%s）" % (exc.code or "")}
                        else:
                            # 草案行的 sha 由消費端（UI `_sha256`／pending）以其**檔案行**為準
                            # 計算——這裡不重算，json 序列化差一個空白就對不上。
                            tally["drafted"] += 1
                            record = dict(decision)
                            seconds_total += decision.get("seconds") or 0.0
            sink.write(json.dumps(
                envelope(lane, served or endpoint["name"], key, source_sha8, row_no,
                         args.tag, images_sent and [os.path.basename(p) for p in images_sent],
                         record, note),
                ensure_ascii=False) + "\n")
            sink.flush()
            written += 1
    elapsed = time.time() - started

    print("repair 生產者（%s）：%d 列 → %s" % (args.tag, written, args.out))
    print("  outcomes: %s" % json.dumps(tally, ensure_ascii=False))
    print("  判讀秒數合計 %.1f s　牆上時鐘 %.1f s" % (seconds_total, elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
