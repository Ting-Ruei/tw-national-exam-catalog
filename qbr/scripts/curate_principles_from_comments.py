# -*- coding: utf-8 -*-
"""把逐題註解裡**新的**規則，整理成錯題討論區的「基本原則」。

使用者的規則（原文）：**逐題 comment 自己讀，是原本沒有的規則才加入。**

這支是那個「加入」的寫入半邊。判讀本身（哪一句是新的、為什麼）在
`qbr/reports/comment_to_principles.md`，那份文件是**給人看的**：它列出讀到的 9 筆註解、
逐筆對照已有的規則，並說明為什麼只有一條算新。這裡只做三件機械的事：

1. 把判讀結果（下面 `CURATED`）變成基本原則流的 `add` 事件。
2. **去重**：文字已經在作用中的原則裡，就不再加（所以重跑是 0 新增）。
3. **對帳**：每一條新原則都要指得出它是從哪一筆 `comment` 事件讀來的，而那個 key
   必須真的在事件流裡有一筆 `comment`。指不出來源的「原則」就是編的。

為什麼是「人寫的一句，不是一條規則」
------------------------------------

專案的最高規範說，「讀出文字的意義」是提示詞，不是腳本。這裡要寫進去的東西正好是那個：
一句交給模型看的約束。所以這一支不是新的偵測器，它把一句話放進
`question_review_principles.jsonl`——`confirm_dispute` 每輪讀它、原封不動放進轉錄提示詞，
`ai_findings.build_prompt` 也讀它。它**不是** `if`。

治理
----

`reviewer` 誠實標成代理（`principle_curator`），不是 `local`（那會被讀成人），
也不是任何人的名字（治理禁止代理冒充人類審核者）。這是一條**人給的約束**被代理整理進流裡，
來源（`source`）與證據（`evidence`）都寫得出來。預設 dry-run，`--apply` 才寫；
寫入沿用與介面按鈕**同一個** `qbr.discuss.append_event`，所以行格式不可能不一致。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import discuss  # noqa: E402

#: 判讀結果。每一條都要有 `evidence`（讀到它的 candidate_key）與 `rationale`（為什麼它算新的）。
#: 只有 `q071` 的「表格要用紙本圖」通過了判讀——見 `comment_to_principles.md` 的對照表。
#: 另外 8 筆要嘛已被既有規則涵蓋（上下標 → `SUPERSCRIPT_FLATTENED`；字形 → `lost-glyph`），
#: 要嘛是一次性事實（「⁻ 0.23t 是上標」「Ae-αt＋Be-βt 並沒有改到」），把它們也寫成原則只會
#: 讓下一輪的提示詞變長而什麼都沒改變。
CURATED = (
    {
        "text": "表格的內容不要靠文字層推論或重排。紙本的表格在文字層只剩一串數字、欄位對不上；"
                "遇到表格時，表格要以紙本圖（截圖／figure）為準，不要用抽取出來的數字把它拼成文字表格。",
        "evidence": ["moex:108030:305:33:1:question:q071"],
        "rationale": "q071 的表格在文字層被壓成一行；reread.SYSTEM 規則 1 仍指示把表格文字"
                     "依序寫入，這是唯一一條沒有被寫下來的替代做法。",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", required=True,
                        help="the append-only review-event log (讀 comment 的來源)")
    parser.add_argument("--principles", required=True,
                        help="question_review_principles.jsonl（寫入目標）")
    parser.add_argument("--reviewer", default="principle_curator",
                        help="代理身分；不得使用 local 或任何人的名字（治理：不得冒充審核者）")
    parser.add_argument("--apply", action="store_true", help="真的寫；沒有它就是 dry-run")
    return parser.parse_args()


def read_events(path: str) -> list[dict]:
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                rows.append(record)
    return rows


def comment_keys(events: list[dict]) -> set[str]:
    """每一筆 `comment` 事件指到的題目。這是「這條原則讀得到來源」的判準。"""
    return {str(event.get("candidate_key") or "").strip()
            for event in events
            if str(event.get("action") or "").strip().lower() == "comment"
            and str(event.get("candidate_key") or "").strip()}


def existing_texts(events: list[dict]) -> set[str]:
    return {text.strip() for text in discuss.active_principles(events) if text.strip()}


def main() -> int:
    """Read the comments, propose only the new rules, and (with `--apply`) append them."""
    args = parse_args()
    if args.reviewer in {"local", "human", "reviewer", ""}:
        print("拒絕：reviewer 不可以是 %r——那是人的名字（治理：代理不得冒充審核者）"
              % args.reviewer, file=sys.stderr)
        return 2

    review_events = read_events(args.events)
    if not review_events:
        print("拒絕：讀不到事件流 %s（沒有來源就不可能有證據）" % args.events, file=sys.stderr)
        return 2
    keys = comment_keys(review_events)
    known = existing_texts(discuss.load_events(args.principles))

    proposed, skipped, ungrounded = [], [], []
    for item in CURATED:
        text = str(item.get("text") or "").strip()
        evidence = [str(key) for key in item.get("evidence") or []]
        if not text:
            continue
        if text in known:
            skipped.append(text)          # 已經在作用中：重跑是 0 新增
            continue
        if not evidence or any(key not in keys for key in evidence):
            # 指不出來源（或來源不是一筆 comment）＝編的原則，寧可不寫。
            ungrounded.append((text, evidence))
            continue
        proposed.append({**item, "text": text, "evidence": evidence})

    print("讀到 comment 事件 %d 筆，涵蓋 %d 個題號" % (
        sum(1 for e in review_events
            if str(e.get("action") or "").lower() == "comment"), len(keys)))
    print("作用中的原則：%d 條" % len(known))
    print("判讀候選：%d 條；已存在而略過：%d 條；指不出來源而拒絕：%d 條"
          % (len(CURATED), len(skipped), len(ungrounded)))
    for text, evidence in ungrounded:
        print("  拒絕（沒有可對帳的來源）：%s ← %s" % (text[:40], evidence))
    for item in proposed:
        print("  ＋ %s" % item["text"])
        print("     evidence=%s" % ",".join(item["evidence"]))

    if not args.apply:
        print("\n（dry-run；沒有寫入。加 --apply 才寫。）")
        return 0

    written = []
    for item in proposed:
        event = discuss.append_event(args.principles, {
            "schema": "qbr_review_principle_v0.1",
            "action": "add",
            "principle_id": discuss.next_id(discuss.load_events(args.principles), "p"),
            "text": item["text"],
            "scope": "question",
            "reviewer": args.reviewer,
            "source": "comment_review",
            "evidence": item["evidence"],
            "rationale": item.get("rationale") or "",
        })
        written.append(event)

    receipt = {
        "written_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "principles_file": args.principles,
        "source_events": args.events,
        "reviewer": args.reviewer,
        "appended": len(written),
        "skipped_existing": len(skipped),
        "refused_ungrounded": len(ungrounded),
        "principle_ids": [event.get("principle_id") for event in written],
    }
    receipt_path = Path(args.principles).with_suffix(".curation-receipt.json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n寫入 %d 條；收據：%s" % (len(written), receipt_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
