#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a `compare_models.py` run as a **one-screen-per-question** comparison page (A2).

The designer asked for the models' actions "一字排開" - laid out in a row, so the eye can see two
models disagree about the same question without scrolling between tabs. This renders exactly that,
and it follows the three conclusions the industry comparison tools share (LLM Comparator, LMArena,
Argilla):

1. **The scored column is present.** Each question shows the human's text next to the model's, so a
   reader can see the right answer rather than trusting the verdict chip.
2. **The unit of comparison is the *difference*, not the whole document.** Only the fields the person
   touched are shown in full; the untouched ones collapse to a line. A comparison of two identical
   stems is noise with a header.
3. **A person can disagree, in words.** Every question has a "partly right / wrong reason" free-text
   box. It is stored in `browser` localStorage only - it never enters `question_review_events.jsonl`,
   because the person's authority over the corpus is G4 and this page is a sandbox.

This writes a **static** page with the run embedded, so it opens by double-click with no server and no
network. That is deliberate: the run is the record, and a page that needed the run re-fetched could
show a different file than the one that was scored.

Usage:
    python3 build_compare_page.py runs/20260928T...jsonl --out /tmp/a2/compare.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import probe_text_sufficiency as probe  # noqa: E402

VERDICT_CLASS = {
    "exact": "v-exact", "near": "v-near", "wrong": "v-wrong",
    "missed": "v-missed", "over": "v-over", "same": "v-same",
}
VERDICT_LABEL = {
    "exact": "✓ 與人相同", "near": "~ 只差標記", "wrong": "✗ 改錯", "missed": "－ 漏改",
    "over": "＋ 多改", "same": "· 未動",
}


def load_dataset() -> dict:
    with open(os.path.join(HERE, "dataset.jsonl"), encoding="utf-8") as handle:
        return {json.loads(line)["candidate_key"]: json.loads(line)
                for line in handle if line.strip()}


def option_text(question: dict, key: str) -> str:
    return probe.option_text(question, key)


def field_text(question: dict, field: str) -> str:
    if field == "stem":
        return question.get("stem") or ""
    if field == "answer":
        return str(question.get("answer") or "")
    return option_text(question, field.split()[1])


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def side_html(shipped: str, human: str, model, verdict: str) -> str:
    """The three-line block for one field: machine, person, model.

    The person's line is only drawn when it differs from the machine's; showing it always would make
    the common case (identical) look like a correction. The model's line is the model's raw text, not
    a diff - the reader's eye does the diff, and a machine diff here would hide a whole-sentence
    rewrite behind a small highlight.
    """
    rows = ['<div class="line machine"><span class="tag">機器</span><code>%s</code></div>' % esc(shipped)]
    if human != shipped:
        rows.append('<div class="line human"><span class="tag">人工</span><code>%s</code></div>' % esc(human))
    if model is None:
        rows.append('<div class="line model missing"><span class="tag">模型</span><em>（沒有回傳這個欄位）</em></div>')
    else:
        rows.append('<div class="line model"><span class="tag">模型</span><code>%s</code></div>' % esc(model))
    return ('<div class="field %s">' % VERDICT_CLASS.get(verdict, "v-same")) + "\n".join(rows) + "</div>"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run")
    parser.add_argument("--out", default=os.path.join(HERE, "compare.html"))
    parser.add_argument("--sufficiency", default=os.path.join(HERE, "text-sufficiency.json"))
    args = parser.parse_args(argv)

    dataset = load_dataset()
    runs = []
    header = {}
    with open(args.run, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("kind") == "qbr_a2_model_run":
                header = record
            elif record.get("model"):
                runs.append(record)
    if not runs:
        print("no model records in %s" % args.run, file=sys.stderr)
        return 2

    kinds = {}
    for key, row in dataset.items():
        for field in row["fields_touched"]:
            shipped = field_text(row["shipped"], field)
            human = field_text(row["human"], field)
            kinds[(key, field)] = probe.classify(shipped, human)

    # Every scored question once, with each model's reply beside it.
    by_question = {}
    for record in runs:
        for row in record["rows"]:
            by_question.setdefault(row["candidate_key"], {})[record["model"]] = row

    blocks = []
    for key in sorted(by_question, key=lambda k: (dataset.get(k, {}).get("subject") or "", k)):
        data = dataset.get(key)
        if not data:
            continue
        replies = by_question[key]
        first = next(iter(replies.values()))
        touched = data["fields_touched"]
        parts = ['<section class="q" id="%s">' % esc(key),
                 '<h2>%s <span class="qn">%s</span></h2>' % (
                     esc(data.get("subject") or "（無科目）"), esc(data.get("question_number") or "")),
                 '<div class="meta">%s ・ %s</div>' % (esc(data.get("category") or ""), esc(key[:80]))]
        for field in touched:
            shipped = field_text(data["shipped"], field)
            human = field_text(data["human"], field)
            kind = kinds.get((key, field), "?")
            reach = "可從文字推出" if kind not in probe.NEEDS_SOURCE else "文字裡沒有，需看原卷"
            parts.append('<h3>%s <span class="chip %s">%s</span> <span class="chip %s">%s</span></h3>'
                         % (esc(field), VERDICT_CLASS.get("same" if kind not in probe.NEEDS_SOURCE else "over", "v-same"),
                            esc(reach), "", ""))
            for record in runs:
                name = record["model"]
                row = replies.get(name)
                if row is None:
                    continue
                verdict = row["verdicts"].get(field, "same")
                model_value = None
                if row.get("parsed"):
                    import compare_models as compare  # local: only needed for the reply map
                    reply = compare.parse_answer(row.get("reply") or "")
                    if reply:
                        model_value = compare.field_map({
                            "stem": reply.get("stem"), "answer": reply.get("answer"),
                            "options": [{"key": k, "text": v} for k, v in
                                        (reply.get("options") or {}).items()]}) .get(field)
                parts.append('<div class="model-block"><div class="mhead">%s <span class="chip %s">%s</span></div>%s</div>'
                             % (esc(name), VERDICT_CLASS.get(verdict, "v-same"),
                                esc(VERDICT_LABEL.get(verdict, verdict)),
                                side_html(shipped, human, model_value, verdict)))
        parts.append('<label class="note">我的判讀（只存在這台瀏覽器）：'
                     '<textarea data-note="%s" rows="2"></textarea></label>' % esc(key))
        parts.append("</section>")
        blocks.append("\n".join(parts))

    summaries = {record["model"]: record.get("summary") for record in runs}
    page = PAGE.replace("__TITLE__", esc(os.path.basename(args.run)))
    page = page.replace("__HEADER__", esc(json.dumps(header, ensure_ascii=False)))
    page = page.replace("__SUMMARIES__", json.dumps(summaries, ensure_ascii=False, indent=2))
    page = page.replace("__BODY__", "\n".join(blocks))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(page)
    print("wrote %s (%d questions, %d models)" % (args.out, len(blocks), len(runs)))
    return 0


PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>A2 模型比對 — __TITLE__</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.6 -apple-system, "PingFang TC", sans-serif; margin: 0; padding: 24px 32px 96px;
         background: #faf9f7; color: #23201c; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .lede { color: #6b6459; max-width: 900px; }
  pre.scores { background: #fff; border: 1px solid #e3ded6; border-radius: 8px; padding: 12px 16px;
               overflow: auto; max-height: 260px; font-size: 12.5px; }
  .q { background: #fff; border: 1px solid #e3ded6; border-radius: 10px; padding: 16px 20px;
       margin: 18px 0; }
  .q h2 { font-size: 16px; margin: 0 0 2px; }
  .qn { color: #8a8275; font-weight: 400; }
  .meta { color: #8a8275; font-size: 12.5px; margin-bottom: 12px; }
  h3 { font-size: 14px; margin: 16px 0 6px; display: flex; gap: 8px; align-items: center; }
  .chip { font-size: 11.5px; font-weight: 500; padding: 1px 8px; border-radius: 999px;
          background: #efe9e0; color: #57504a; }
  .model-block { border-left: 3px solid #e3ded6; padding-left: 12px; margin: 8px 0; }
  .mhead { font-size: 12.5px; color: #57504a; margin-bottom: 4px; display: flex; gap: 8px; }
  .line { display: flex; gap: 10px; padding: 3px 8px; border-radius: 6px; }
  .line .tag { flex: 0 0 44px; font-size: 11.5px; color: #8a8275; }
  .line code { white-space: pre-wrap; word-break: break-word; }
  .line.machine { background: #f6f3ee; }
  .line.human   { background: #e9f3ea; }
  .line.model   { background: #eef1f7; }
  .field.v-exact .line.model { background: #d9efdc; }
  .field.v-wrong .line.model { background: #f7e0dd; }
  .field.v-missed .line.model { background: #fbf3d6; }
  .field.v-over  .line.model { background: #f0e2f7; }
  .v-exact { background: #cfe9d4; } .v-near { background: #e6eecb; } .v-wrong { background: #f0c8c3; }
  .v-missed { background: #f3e6b8; } .v-over { background: #e2cdf0; } .v-same { background: #eceae5; }
  .note { display: block; margin-top: 14px; font-size: 13px; color: #6b6459; }
  textarea { width: 100%; box-sizing: border-box; font: inherit; font-size: 13px; padding: 6px 8px;
             border: 1px solid #d8d2c8; border-radius: 6px; background: #fdfcfa; }
</style>
</head>
<body>
<h1>模型比對（A2）— <code>__TITLE__</code></h1>
<p class="lede">每個欄位排成三行：<b>機器</b>（送進模型的原文）、<b>人工</b>（設計者審完的版本，即正解）、
<b>模型</b>（模型回傳的）。綠＝與人相同、黃＝漏改、紅＝改錯、紫＝多改。每題的「我的判讀」只留在這台瀏覽器。</p>
<p class="lede">本頁的資料是：<code>__HEADER__</code></p>
<pre class="scores">__SUMMARIES__</pre>
__BODY__
<script>
(function () {
  var KEY = "a2-notes:" + location.pathname;
  var store = {};
  try { store = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { store = {}; }
  document.querySelectorAll("textarea[data-note]").forEach(function (area) {
    var id = area.getAttribute("data-note");
    if (store[id]) { area.value = store[id]; }
    area.addEventListener("input", function () {
      store[id] = area.value;
      try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) {}
    });
  });
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
