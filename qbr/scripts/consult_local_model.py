# -*- coding: utf-8 -*-
"""Ask a local model to look at a problem the pipeline cannot name, and record the answer.

This is not the question-auditing path. That one asks a model to judge one extracted
question against a fixed vocabulary and is deliberately narrow. This one is the opposite:
it is the escape hatch for when the pipeline is looping - when the same class of failure
keeps coming back under different names and another rule would only bury it.

Two rules are kept, and both are about honesty rather than about model quality:

  * The model is shown the evidence, not a summary of the evidence. The failing lines are
    passed through as they were extracted, so what it reasons about is the paper and not
    this script's opinion of the paper.
  * The answer is written down next to the question, with the model's name and the exact
    prompt. A suggestion that is not recorded cannot be checked later, and an unrecorded
    suggestion is how a project acquires advice it never actually followed.

The models are addressed by environment variable, so switching between the fast one and the
slow one is a shell setting rather than an edit:

    QBR_LOCAL_MODEL_URL   http://127.0.0.1:18120     (ornith-1.5-mtplx-35b, fast, vision)
    QBR_LOCAL_MODEL_NAME  ornith-1.5-mtplx-35b
    QBR_LOCAL_MODEL_KEY   mtplx
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The two endpoints, named so a run can say which it used without being told again.
ENDPOINTS = {
    "mtplx-35b": {"url": "http://127.0.0.1:18120", "name": "ornith-1.5-mtplx-35b",
                  "key": "mtplx"},
    "mtplx-9b": {"url": "http://127.0.0.1:18121", "name": "ornith-1.5-mtplx-9b",
                 "key": "mtplx"},
    "qwen3.8-flash-next": {"url": "http://192.168.10.90:8888",
                           "name": "qwen3.8-flash-next", "key": "dgx-spark-local"},
}

SYSTEM = """你是一位 PDF 抽取流程的診斷工程師。你面前是一份官方考卷的抽取結果，其中某些行被流程誤認為「題號」，但實際上不是。

我要問你的不是「這段文字哪裡壞了」，而是更根本的問題：**這些行到底是什麼？它們為什麼會被誤認為題號？**

請這樣回答：

1. `what`：這些行實際上是什麼（例如：圖表的座標軸數字、跨頁續接的題幹、表格內容、頁碼…）。
   逐一說明，不要合併。
2. `why`：它們為什麼會被「數字 空白 內容」這個形狀的規則抓到。
3. `class`：這些是不是**同一類**問題？如果是同一類，給它一個名字；如果不是，分成幾類並各自命名。
4. `fix_at`：如果要修，最適合修在**哪一層**？（抽取層 / 行分組 / 題號偵測 / 題目分段 / 不該用規則處理）
5. `risk`：如果為這類問題寫一條規則，最可能誤傷什麼？

輸出 JSON：
{{"what":[{{"line":"原文","is":"它是什麼"}}],"why":"...","class":[{{"name":"類別名","lines":"哪些行屬於這類"}}],"fix_at":"...","risk":"..."}}

不要客套，不要重述我的問題。直接給結論。"""

USER = """科目：{subject}
世代：{generation}（{style}）

這份卷子被抽取出 {questions} 個題目，但流程判定不乾淨。

**被誤認為題號的行（這些是問題所在）**：
{anchors}

**這份卷子的前 12 行（讓你看排版）**：
{head}

**流程的說法**：
- 題號範圍：{first} 到 {last}
- 缺號：{gaps}
- 警告：{reasons}

請告訴我這些被誤認的行到底是什麼。"""


def ask(messages, *, endpoint, max_tokens=4000, timeout=1800):
    body = {"model": endpoint["name"], "messages": messages,
            "max_tokens": max_tokens, "temperature": 0}
    request = urllib.request.Request(
        endpoint["url"] + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + endpoint["key"]})
    started = time.time()
    try:
        raw = json.loads(urllib.request.urlopen(request, timeout=timeout).read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return None, "request failed: %s" % exc, {}, time.time() - started
    elapsed = time.time() - started
    message = (raw.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content") or ""
    return _parse(content), content, raw.get("usage") or {}, elapsed


def _parse(content):
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def consult(subject, generation, style, questions, anchors, head, first, last, gaps,
            reasons, *, model="mtplx-35b", max_tokens=4000):
    """One consultation. Returns a record that is safe to write to a log as it stands."""
    endpoint = ENDPOINTS[model]
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER.format(
                    subject=subject, generation=generation, style=style, questions=questions,
                    anchors="\n".join(anchors) or "（無）", head="\n".join(head) or "（無）",
                    first=first, last=last, gaps=gaps, reasons=reasons)}]
    parsed, raw, usage, seconds = ask(messages, endpoint=endpoint, max_tokens=max_tokens)
    return {"subject": subject, "model": endpoint["name"], "endpoint": endpoint["url"],
            "prompt_system": SYSTEM, "prompt_user": messages[1]["content"],
            "answer": parsed, "raw": raw if parsed is None else "",
            "usage": usage, "seconds": round(seconds, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Consult a local model about a stuck problem.")
    parser.add_argument("--report", required=True, help="batch_report.jsonl to look through")
    parser.add_argument("--paper", required=True, help="paper name (or a unique part of it)")
    parser.add_argument("--out", required=True, help="where to append the consultation")
    parser.add_argument("--model", default="mtplx-35b", choices=sorted(ENDPOINTS))
    parser.add_argument("--head-lines", type=int, default=12)
    args = parser.parse_args()

    sys.path.insert(0, os.path.join(PKG_ROOT, "src"))
    from qbr import extract, repair

    record = None
    with open(args.report, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if args.paper in row["name"]:
                record = row
                break
    if record is None:
        raise SystemExit("no paper in %s matching %r" % (args.report, args.paper))

    corpus = os.path.normpath(os.path.join(
        PKG_ROOT, "..", "..", "tw-national-exam-catalog", "國考題資料夾",
        "10_official_pdf", "by_official_catalog"))
    import glob
    hits = glob.glob(os.path.join(corpus, "*", "*", "*", record["name"]))
    if not hits:
        raise SystemExit("paper file not found for %s" % record["name"])

    rows = extract.extract_lines_a(hits[0])
    text = repair.text_from_rows(rows)
    repaired, _ = repair.normalize_pretty(text)
    records, _residual, diagnostics = repair.segment_questions(repaired)
    numbers = [int(r["number"]) for r in records]

    # The evidence is the whole thing the model needs: the lines that were taken for
    # anchors and look like they should not be. A duplicated number is shown with both of
    # its readings, because that is the ambiguity being asked about.
    import collections
    counts = collections.Counter(numbers)
    anchors = []
    for record_item in records:
        number = int(record_item["number"])
        if counts[number] > 1 or number > (record["last"] or 0):
            anchors.append("#%-3d  stem=%r  options=%d"
                           % (number, (record_item.get("stem") or "")[:88],
                              len(record_item.get("options") or {})))
    head = repaired.splitlines()[:args.head_lines]

    print("consulting %s about %s" % (args.model, record["name"]), flush=True)
    result = consult(record["subject"], record.get("generation", "?"), record.get("style") or "-",
                     record.get("questions"), anchors, head, record.get("first"),
                     record.get("last"), record.get("gaps"), record.get("reasons"),
                     model=args.model)
    with open(args.out, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(json.dumps(result.get("answer"), ensure_ascii=False, indent=2)
          if result.get("answer") else result.get("raw", "")[:2000])
    print("\n(%s, %.1fs, %s tokens)"
          % (result["model"], result["seconds"], result["usage"].get("completion_tokens")))


if __name__ == "__main__":
    main()
