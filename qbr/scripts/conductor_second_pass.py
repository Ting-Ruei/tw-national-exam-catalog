# -*- coding: utf-8 -*-
"""指揮者 second pass：把 A 層領導者（GLM lane）套在**既有的 finding 流**上，產生第二意見。

為什麼需要（owner 2026-10-01）
------------------------------

    領導者本地模型已經裝好（192.168.10.90:8888）＋ owner 選「全做」，其中一步是「99 題小考」。

`scan_category_principles.py` 的稽核結果是一流讀法（occamy-6bit 對紙本）；A 層的職責（
workplan 3.5、orchestrator.py 的模組文件）是判斷「這些一流讀法可不可信」。讀與判是兩個角色：
判斷的腦本來就該由設計者指派（3.5「模型設計者日後指派」），現在指派到了（`dgx-flash` lane，
GLM-5.3-Flash-EXL3，LAN，不出網——`QBR_CONDUCTOR_ENGINE` 可改派）。

**判不讀不落地**：第二意見是 advisory 記錄，只寫 `--out` 這一個輸出流；不碰 queue 的任何檔、
不寫人工事件流。輸出行的 schema 是 `conductor_second_pass v1`，上游一行以 sha8＋行號引用，
不複製題目文字——「被判斷的記錄」的正本留在來源檔，checksum 讓它移動時可以被發現。

用法
-----

    # 真判：第一個 finding 檔的前 99 列（例如 c-audit 的 bounded 樣本）
    .venv/bin/python scripts/conductor_second_pass.py \\
        --findings repair_agent_test/agent/store/scans/20260930-c-audit/question_ai_findings.jsonl \\
        --queue data/review-queues/live --limit 99 \\
        --out repair_agent_test/agent/store/scans/20261001-glm-conductor/second_pass.jsonl

    # 全檔；行流式寫入，中斷的損失上限是正在判的那一列
    .venv/bin/python scripts/conductor_second_pass.py --findings <file> --queue <root> --out <file>
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

#: The output schema. Its own label because the consumers differ from every other stream: a second
#: opinion cites its input row rather than restating it, so a row that moved in the source is
#: detectable by checksum instead of by a human diffing two files.
SCHEMA = "conductor_second_pass v1"


def candidates_path(queue: str) -> str:
    return os.path.join(queue, "review-ui", "candidates.jsonl")


def load_questions(queue: str) -> dict:
    """`candidate_key` -> row, from the queue's candidates file."""
    candidates = candidates_path(queue)
    if not os.path.exists(candidates):
        raise SystemExit("找不到 %s（--queue 要指到佇列根目錄）" % candidates)
    rows: dict[str, dict] = {}
    with open(candidates, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows[row.get("candidate_key") or ""] = row
    return rows


def sha8_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


def crop_path_of(row: dict, queue: str) -> str:
    """The finding's crop, resolved and *checked*: a missing file is not a crash, it is no crop."""
    raw = row.get("crop") or ""
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw if os.path.exists(raw) else ""
    resolved = os.path.join(queue, raw)
    return resolved if os.path.exists(resolved) else ""


def envelope(lane: str, served: str, key: str, source_sha8: str, row_no: int,
             record: dict, note: str) -> dict:
    """One output row: the second opinion plus the pointer to what it judged."""
    return {
        "schema": SCHEMA,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "lane": lane,
        "served_model": served,
        "candidate_key": key,
        "source": {"findings_sha8": source_sha8, "row": row_no},
        "record": record,
        "note": note or None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="指揮者 second pass over a findings stream")
    parser.add_argument("--findings", required=True, help="input findings jsonl (rows to rejudge)")
    parser.add_argument("--queue", default="data/review-queues/live",
                        help="queue root; candidates.jsonl lives inside review-ui/")
    parser.add_argument("--engine", help="lane name; default QBR_CONDUCTOR_ENGINE or the conductor lane")
    parser.add_argument("--out", required=True, help="output jsonl (advisory)")
    parser.add_argument("--limit", type=int, default=0, help="judge at most this many (0 = all)")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--with-evidence", action="store_true",
                        help="send the full evidence pack at once (orchestrator.judge_with_evidence) "
                             "instead of triage-only; measured 2026-10-01: triage-only rubber-stamps "
                             "audit diffs TRUST 43/43")
    args = parser.parse_args(argv)

    lane = (args.engine or os.environ.get("QBR_CONDUCTOR_ENGINE", "").strip()
            or orchestrator.CONDUCTOR_ENGINE)
    endpoint = engines.named(lane)
    served = engines.served_id(endpoint)
    if served and served != endpoint["name"]:
        # The pin does not name what the server runs — every record would describe a model that did
        # not produce the bytes. Say it and stop: one variable fixes this, replacing no code.
        print("⚠ pin %r ≠ /v1/models 回報 %r — 先用該 lane 的 _MODEL 變數更新 pin 再跑"
              % (endpoint["name"], served), file=sys.stderr)
        return 2

    questions = load_questions(args.queue)
    source_sha8 = sha8_of(args.findings)
    tally: dict[str, int] = {}
    degraded = 0
    skipped = 0
    seconds_total = 0.0

    started = time.time()
    written = 0
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.findings, encoding="utf-8") as source, \
            open(args.out, "a", encoding="utf-8") as sink:
        for row_no, line in enumerate(source, 1):
            if args.limit and row_no > args.limit:
                break
            row = json.loads(line)
            key = row.get("candidate_key") or ""
            question = questions.get(key)
            if question is None:
                skipped += 1
                record = {"degraded": True}
                note = "佇列中找不到 candidate_key，跳過不猜"
            elif args.with_evidence:
                judgement, note = orchestrator.judge_with_evidence(
                    question, row.get("finding"), endpoint=endpoint, timeout=args.timeout,
                    max_tokens=args.max_tokens, crop_path=crop_path_of(row, args.queue))
                record = judgement or {"degraded": True, "why": note}
            else:
                record, note = orchestrator.review_with_orchestrator(
                    question, row.get("finding"), endpoint=endpoint, timeout=args.timeout,
                    max_tokens=args.max_tokens, crop_path=crop_path_of(row, args.queue))
            verdict = (record or {}).get("verdict") or ""
            if verdict:
                tally[verdict] = tally.get(verdict, 0) + 1
            else:
                degraded += 1
            seconds_total += (record or {}).get("seconds") or 0.0
            sink.write(json.dumps(
                envelope(lane, served or endpoint["name"], key, source_sha8, row_no,
                         record, note), ensure_ascii=False) + "\n")
            sink.flush()
            written += 1
    elapsed = time.time() - started

    print("指揮者 second pass：%d 列 → %s" % (written, args.out))
    print("  verdicts: %s" % json.dumps(tally, ensure_ascii=False))
    print("  degraded: %d（找不到題目跳過 %d）　判讀秒數合計 %.1f s　牆上時鐘 %.1f s"
          % (degraded, skipped, seconds_total, elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())