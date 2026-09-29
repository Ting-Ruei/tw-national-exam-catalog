#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回報：把代理累積的結果整理成人看得懂的摘要，一類一行。

    python3 scripts/report_repair_progress.py --queue <dir>
    python3 scripts/report_repair_progress.py --queue <dir> --json

**為什麼要有這一支。** owner（2026-09-24）：
  「彙總也可以是逐題，但是我每年自動化彙入新題目的時候，我可以開啟 UI，看到那些異常被用什麼
    方式修正過了，我可以一次看一類，快速按 A 表示我有看過，這是可以變通的」
  「事件驅動，應該說現階段還在建立期，因此在每次修理的時候就可以把同一類修正打包」

所以摘要的單位是**類**（同一個 dispute kind），不是逐題——但每一類底下要能展開看到個別題目。
這一支產生的是**給人讀的文字**；UI 讀的是同一批 finding，兩者不該各算一次（那會是兩個可以
不一致的地方）。

它不寫任何東西（除了 stdout），所以它可以在任何時候跑，包括人在看的時候。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import ai_findings, scan_state  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True, help="the queue directory (holds review-ui/)")
    parser.add_argument("--json", action="store_true", help="machine-readable instead of the summary")
    parser.add_argument("--examples", type=int, default=5,
                        help="how many questions to show per class (owner's 5 題下拉)")
    return parser.parse_args()


def load_findings(queue_dir: str, max_records: int = 200_000):
    """The newest `max_records` findings, newest first.

    **Simple forward read, bounded.** The first attempt at this streamed the file backwards in 1 MB
    chunks to avoid materialising it; that was measurably worse — 17.1 s against 1.49 s, an 11×
    regression — because decoding back-to-front forces a new bytes object per chunk while the
    forward read is one linear pass with the OS readahead behind it. Memory was never the binding
    problem at this size: 85,475 records is well under a second and a few hundred MB.

    So the fix for "the file is 500 MB and grows" is a **bound with a stated meaning**, not a
    slower reader: keep the newest `max_records`, and let the count itself say it was capped rather
    than silently reporting a subset as the total. The default is several times the largest stream
    seen (85,475 on 2026-09-24) and is reached long after the loop has been running for months.

    Returns `(rows, capped)`; `main` prints the cap so a truncated summary never reads as a total.
    """
    # `ai_findings.store_path` is the one answer for where the findings live, and it tolerates either
    # spelling of `--queue` (root or `review-ui/`). Hand-joining `queue_dir + STREAM` is what made the
    # report print "0 筆" while 517 MB of findings sat one directory away.
    path = ai_findings.store_path(queue_dir)
    if not os.path.isfile(path):
        return [], False
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.reverse()                            # newest first, which is what the summary shows
    return rows[:max_records], len(rows) > max_records


def classify(record: dict) -> str:
    """Which class a finding belongs to.

    **分類的依據是紀錄裡真的有的欄位**，不是我希望有的欄位。實際形狀（量過 3,002 筆）是
    `finding` 一個 dict，裡面有模型自己的 `what`／`verdict`；`learned` 有時是字串、有時是 null，
    而且沒有 `evidence.disputes`。第一版照著想像的形狀寫，跑起來是 `AttributeError`——那正是
    「規則寫成 parser 的輸出」的反面：這裡讀的是已經寫進檔案的紀錄，欄位要先量再寫。

    類別的優先序：
      1. `finding.what`——模型對「這是哪一種問題」的命名，最接近 owner 說的「一次看一類」。
      2. `finding.verdict`——沒有 `what` 時至少還有裁決。
      3. 讀不到時的固定字串，**不能是空字串**（空的分類會在摘要裡消失）。
    """
    finding = record.get("finding")
    if isinstance(finding, dict):
        what = str(finding.get("what") or "").strip()
        verdict = str(finding.get("verdict") or "").strip()
        if what and what.upper() != "NONE":
            return what
        if verdict:
            return "verdict:%s" % verdict
    elif isinstance(finding, str) and finding.strip():
        return finding.strip()[:80]
    # An engine that failed has no finding; that is a class too, and a useful one (owner sees which
    # lane is not answering without reading a log).
    if record.get("error"):
        return "讀不到（引擎沒回答）"
    learned = record.get("learned")
    if isinstance(learned, dict):
        return str(learned.get("confirm_dispute") or "（未分類）").strip() or "（未分類）"
    return "（未分類）"


def summarize(records, examples: int) -> dict:
    """Group the findings into classes, newest question first within each class.

    `records` is walked **newest first** (see `load_findings`), which is what makes the per-class
    question lists newest-first. Reversing each class's list instead is *not* equivalent: a question
    seen in several records is kept at its first sighting of the walk, and the direction decides
    which sighting that is (measured: class `verdict:DEFECT` led with `q002` vs `q053`, both holding
    155 questions).

    Takes any iterable of records and walks it once.
    """
    by_class = collections.OrderedDict()
    total = 0
    # Both accumulations in one pass: `records` may be a generator, so it cannot be walked twice.
    verdicts = collections.Counter()
    degraded = 0
    disagreements = []
    #: verdicts per judging model, because "which engine judged" is the one thing the owner needs to
    #: see while the driver is being moved onto the on-prem box (2026-09-25: 「才能逐漸評估地端的做事
    #: 能力」). One line per engine, over the same population as `verdicts`, so the two can be compared
    #: without re-reading the stream.
    by_model = collections.defaultdict(collections.Counter)
    for record in records:
        total += 1
        name = classify(record)
        entry = by_class.setdefault(name, {"kind": name, "count": 0, "questions": [], "seen": set(),
                                           "last_at": ""})
        entry["count"] += 1
        entry["last_at"] = entry["last_at"] or str(record.get("at") or record.get("created_at") or "")
        key = str(record.get("candidate_key") or "")
        # **A set, not `key not in list`.** The list grows to 81,151 keys in the biggest class, and
        # membership in a list is a linear scan — so the dedup was O(n²): measured 20.8 s of the
        # 22.6 s total *inside this loop*, while parsing the whole 500 MB was 0.96 s. The set makes
        # it O(1) and the loop becomes dominated by JSON, which is what it should be.
        if key and key not in entry["seen"]:
            entry["seen"].add(key)
            entry["questions"].append(key)
        block = record.get("orchestration")
        if isinstance(block, dict):
            if block.get("degraded"):
                degraded += 1
            else:
                verdict = str(block.get("verdict") or "")
                if verdict:
                    verdicts[verdict] += 1
                    # `model` is the judging engine's own name, so a record judged before the lane
                    # moved (`deepseek-v4.1-flash`) and one judged after it (`qwen3.8-flash-next`)
                    # stay apart instead of averaging into one number.
                    by_model[str(block.get("model") or "（未記引擎）")][verdict] += 1
                second = block.get("second_look")
                if isinstance(second, dict) and second.get("disagrees_with_local"):
                    disagreements.append({"candidate_key": record.get("candidate_key"),
                                          "why": second.get("why")})
    classes = list(by_class.values())
    for entry in classes:
        # The dedup set is scaffolding, not output: dropping it here keeps the summary's shape
        # unchanged (`render` and `--json` consumers must not see it).
        entry.pop("seen", None)
        entry["shown"] = entry["questions"][:examples]
        entry["hidden"] = max(0, len(entry["questions"]) - examples)
    classes.sort(key=lambda item: -item["count"])
    return {"classes": classes,
            "orchestration": {"verdicts": dict(verdicts), "degraded": degraded,
                              "by_model": {model: dict(counts) for model, counts in by_model.items()},
                              "disagreements": disagreements},
            "total_findings": total,
            "total_classes": len(classes)}

def render(summary: dict, examples: int) -> str:
    lines = []
    lines.append("修理進度彙總")
    lines.append("  累積 %d 筆 advisory，分成 %d 類"
                 % (summary["total_findings"], summary["total_classes"]))
    # The orchestrator's line comes first, and before the classes on purpose: 「指揮者放行了哪些」
    # is what decides how much of the class list below actually has to be read. Owner's rule is
    # 「我可以一次看一類，快速按 A」—— the classes tell them what to press; this tells them where
    # pressing is enough.
    orch = summary.get("orchestration") or {}
    verdicts = orch.get("verdicts") or {}
    if verdicts or orch.get("degraded"):
        parts = "、".join("%s %d" % (name, verdicts[name])
                          for name in ("TRUST", "CARE", "DOUBT") if verdicts.get(name))
        lines.append("  指揮者：%s%s" % (parts or "沒有判斷",
                                       "；未分流 %d（指揮者沒回答，這題沒有被看過）"
                                       % orch["degraded"] if orch.get("degraded") else ""))
        # Which engine said it, when more than one has ever judged. This is the readout for the owner's
        # 2026-09-25 decision to move the driver onto the on-prem box (「才能逐漸評估地端的做事能力」):
        # one line per judging model, so the two engines' verdict distributions can be compared without
        # re-reading the stream, and a distribution that changed after the switch is visible here first.
        models = orch.get("by_model") or {}
        if len(models) > 1:
            lines.append("  判讀來源：" + "；".join(
                "%s %s" % (model, "、".join("%s %d" % (name, counts[name])
                                            for name in ("TRUST", "CARE", "DOUBT") if counts.get(name)))
                for model, counts in sorted(models.items(), key=lambda item: -sum(item[1].values()))))
        if orch.get("disagreements"):
            lines.append("  ⚠ 指揮者與地端不一致 %d 題（兩個模型讀法不同，優先看）"
                         % len(orch["disagreements"]))
            for item in orch["disagreements"][:5]:
                lines.append("      %s：%s" % (item.get("candidate_key") or "?",
                                               (item.get("why") or "")[:70]))
    lines.append("")
    if not summary["classes"]:
        lines.append("  還沒有任何 finding。")
        return "\n".join(lines)
    for entry in summary["classes"]:
        lines.append("【%s】%d 題" % (entry["kind"], entry["count"]))
        for key in entry["shown"]:
            lines.append("    %s" % key)
        if entry["hidden"]:
            lines.append("    …還有 %d 題（展開看全部；每一類都完整記錄，這裡只畫前 %d）"
                         % (entry["hidden"], examples))
        lines.append("")
    lines.append("以上是 advisory 的差異紀錄：它不改題目文字，也不改寫人為判決。"
                 "內容真的被修好時，套用那一步（③ `apply_dispute_repairs.py`）會以機器身分"
                 "把那些題退回未審並保留先前的註解。")
    return "\n".join(lines)


def figure_section(queue_dir: str, examples: int = 6) -> str:
    """圖版那一趟做了什麼：移除幾張、重切幾張，以及**人說過的話**。

    業主 2026-09-25：「我對於截圖能力還是很不滿，為什麼我已經強調有些題目沒有圖、有些題目截圖範圍錯了，
    但他還是不改?」量到的原因是那支 pass 從來不在迴圈裡跑，而它的決定只寫進 `figure_ownership.json`
    ——一份人不會打開的檔案。這一節把那一趟的結果搬到人會讀的那張紙上：`stats` 的幾個數字，加上
    逐題印出**他的原話**（移除的每一張圖都帶著那句話）。
    """
    path = os.path.join(queue_dir, "figure_ownership.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except ValueError:
        return "  圖版：figure_ownership.json 讀不動（不是 JSON）"
    stats = payload.get("stats") or {}
    lines = ["  圖版（最近一趟 %s）：人說沒有圖 ⇒ 不放圖 %d   重切／加寬 %d   只剩一小片移除 %d"
             "   整題縫一張 %d   本來就在自己列裡 %d"
             % (payload.get("stamp") or "?", stats.get("dropped-no-figure", 0),
                stats.get("widened", 0), stats.get("sliver", 0),
                stats.get("whole-question", 0), stats.get("inside", 0))]
    removed = [record for record in payload.get("records") or []
               if record.get("dropped") == "human-flagged-and-no-picture-measured"]
    for record in removed[:examples]:
        lines.append("    %s q%s 的圖被移除（他的話：%s）"
                     % (str(record.get("key") or "")[:34], record.get("number"),
                        (record.get("note") or "")[:70]))
    if len(removed) > examples:
        lines.append("    …還有 %d 題同類（明細 review-ui/figure_ownership.json 的 records）"
                     % (len(removed) - examples))
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    records, capped = load_findings(args.queue)
    # The pending list lives at the queue root; `--queue` may be either spelling.
    resolved = os.path.abspath(args.queue)
    root = os.path.dirname(resolved) if os.path.basename(resolved) == "review-ui" else resolved
    pending = scan_state.pending_keys(root)
    summary = summarize(records, args.examples)
    summary["pending"] = len(pending)
    summary["capped"] = capped
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    print(render(summary, args.examples))
    if capped:
        # Said out loud: a capped summary that reads as a total is the defect this flag exists to
        # prevent. It names the number it kept so the reader can tell "the stream is short" from
        # "the stream was cut".
        print("  （紀錄很長，只讀最新 %d 筆）" % len(records))
    print("  pending（掃到但還沒做完）：%d 題" % len(pending))
    review_ui = resolved if os.path.basename(resolved) == "review-ui" else os.path.join(root, "review-ui")
    section = figure_section(review_ui, args.examples)
    if section:
        print(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
