#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""被同意的機器修復 → **提案**一條可以學習的原則（核准永遠是人的動作）。

業主 2026-09-25：「修正記憶可以收斂——AI 修理之後，人工會同意或拒絕；被**重複拒絕**的題目要讓它
**再進入掃描的迴圈**，然後再修正；被**同意**的題目則成為**可以學習的原則**。」

拒絕的那一半在 `repair_loop.rejections_by_key`（同一份折疊）＋ `scan_state.question_fingerprint`
（重複被拒會再進迴圈）＋ `ai_findings.rejected_note`（提示詞知道上次被退的是哪一筆改動）。
這一支是**同意**的那一半。

它讀什麼、為什麼讀那裡
----------------------

「人同意了這一筆機器改動」這件事**已經記在審核事件流裡**：`repair_loop.confirmations_by_key`
（與拒絕、與審題者的註解共用同一個折疊）讀的是「修復站著 → 人按下 accept」那一對事件。所以這一支
不需要新的訊號來源，也不需要動伺服器的寫入路徑。

**它不寫回饋事件流（`question_correction_feedback_events.jsonl`）。** 那份流的 SQL 契約把
`source_kind` 釘在 `('human_correction', 'three_evidence_correction')` 兩個值上
（`review_state` 的 `CHECK` 條件），要加第三個值就得跑一次 schema 變更——那是另一條版本線、
另一個要 owner 核准的動作，而這一輪沒有那個核准，而且那份流的消費者目前**不存在**
（`build_ai_task` 沒有呼叫端、站上那個流只有 1 筆）。所以這裡直接讀真相（審核事件流），
用 `review_feedback.classify_change` 這個既有的判斷把每一筆確認分類，而不是先抄一份再讀。

兩步，兩個閘門
--------------

1. **重建被同意的改動**（決定性，不叫模型）。每一筆確認的 `before`／`after` 由那一筆修復自己的
   逐欄 `from`／`to` 重建（不是讀現在的列——那筆修復可能已經被撤回或被人再改過），類別由既有的
   `review_feedback.classify_change` 判定。
2. **提案一條原則**（一個類別叫模型一次）。同一個類別有 ≥`--min-confirmations` 筆**不同題目**的
   確認時，把那些例子交給模型，請它寫一條可以貼進提示詞的通則＋反例＋不該泛化的情況。
   `status == "candidate"` 且有 `principle_text` 才寫一筆 `add` 事件。`no_generalization`（例子
   撐不起一條通則）與 `already_covered`（要提的規則**現行原則裡已經有**，`effective_principles`）
   都是「不提案」，只是理由不同——兩者都只印出來，一個字都不寫。

**這一支從不核准。** 提案是 `reviewer="principle_curator"`、`source="feedback_learning"` 的 `add`
事件；`ai_findings.principles_for_prompt` 只讀已核准的原則，所以一個提案在**人按下核准之前**不會
改變任何一次讀取（畫面上它是「待你核准」）。腳本裡沒有任何地方寫 `approve`／`unapprove`，
`tests/test_propose_principles.py` 用「寫出去的事件只有 add」釘住這件事。

為什麼腳本不自己寫那句話？因為「讀出文字的意義」是提示詞的事（`qbr/AGENTS.md`）。腳本只做算術：
這一類被確認了幾次、例子是哪幾筆、要不要問模型；話由模型提、由人核准——這是「兩個做同一件事的
東西」在這條路上唯一的分工方式。

用法
----

    # 預設乾跑：算給你看，不寫任何檔案、不叫模型
    .venv/bin/python scripts/propose_principles.py --queue data/review-queues/live

    # 真的做：對過門檻的類別各問一次模型，寫下提案
    .venv/bin/python scripts/propose_principles.py --queue data/review-queues/live --apply

    # 目前為止有什麼
    .venv/bin/python scripts/propose_principles.py --queue data/review-queues/live --report
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
sys.path.insert(0, HERE)
# 學習路徑的零件在 repo 根的 `scripts/`（`review_state` 也從那裡匯入同一個模組：一份實作，
# 兩個使用者——那份判斷決定 `change_class`，而它是這一支分組的依據）。
sys.path.insert(0, os.path.dirname(PKG))

import repair_loop  # noqa: E402
import ask_about_blocks  # noqa: E402
from qbr import ai_findings, discuss, engines  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(PKG), "scripts"))
from review_feedback import classify_change, diff_snapshots  # noqa: E402

#: 提案的署名與來源。**不是人**，而且畫面上要看得出差別：核准是人按下的（`/api/principles`）。
CURATOR_REVIEWER = "principle_curator"
CURATOR_SOURCE = "feedback_learning"

#: 策展的提示詞。放在這裡而不是 `qbr/prompts/`，因為它是這一支唯一的 prompt，而且它與送出去的
#: 那個封包（下面 `curation_packet`）必須一起讀才讀得懂。
#:
#: 其中兩條規則是**量到的**缺陷，不是想像的：
#:   * `already_covered`：站上 p8（機器提的）與 p6（人寫的）講的是同一件事（都是上下標），因為封包
#:     沒有帶「已經生效的原則」，策展員就把人的話重寫了一遍——提案要花一次呼叫、還要人再讀一次。
#:   * 範圍：p9 是策展員自己提的通則，它從一組 subject 不全是藥學的例子泛化出去，於是那句通則裡
#:     就帶著「不要將此規則應用於非藥學/藥理學領域的題目」這句但書，owner 最後仍把它拿掉——一條
#:     要自己附但書的通則，就是超出量測範圍的通則。這一支的分組只看 `review_feedback.classify_change`，
#:     所以在補上 `subject`／`category`／`paper` 之前，策展員看不到例子的範圍。
CURATOR_SYSTEM = """你是國考題庫抽取品管的原則策展員。

使用者會給你**同一類**的修正紀錄：那些修正是機器做的，而且**已經被人一題一題確認過**。
請你從這些已確認的例子裡，提出**一條**可以貼進提示詞的通則，讓下一次讀紙本時不再犯同一個錯。

規則：
  * 通則要說的是**紙本上看得見的東西**（哪個符號、哪個位置、什麼形狀），不要說「更仔細一點」
    這種沒有內容的話。
  * 你只能提出**候選**：不得宣告已生效、不得說人已經核准、不得修改任何題目、不得寫檔。
  * 你必須同時寫出**反例**（什麼情況下這條通則不適用）與**不該泛化的地方**。提不出反例，
    代表你看見的可能只是這一題的巧合，那就回 no_generalization。
  * 例子只有一兩筆時，傾向 no_generalization；寧可少一條原則，不要多一條錯的法則。
  * 封包裡的 `effective_principles` 是**現在已經生效**的原則（人寫的、人已核准的）。如果你要提的
    通則已經被其中一條涵蓋——同一件事，即使字不一樣——就回 already_covered，並在 `rationale` 裡
    指名你重複的是哪一條。**不要**提出近似重複的原則：把人的話重寫一遍不是新知識。
  * 每個例子的 `subject`／`category`／`paper` 是那一題**量到這個缺陷時的範圍**（科目、考別、那一份
    試卷）。一條通則只能在**這些例子共有的範圍**內成立：例子之間不共享 subject 或 paper 時，或這條
    通則只有在其中一個 subject 上站得住時，這件事一定要寫進 `do_not_generalize`（或直接回
    no_generalization）。超出量測範圍的泛化是錯的，即使通則本身在這個範圍內是對的。
  * 每個例子的 `figure_facts` 是那一題的圖**在紙本上量到的事實**（幾張、屬於哪個選項、歸屬有沒有
    量過），和 `before`/`after` 一樣是證據而不是評語。文字前後看不出圖的問題時，看它。

只回一個 JSON 物件：
{
  "status": "candidate" | "no_generalization" | "already_covered" | "unclear",
  "principle_text": "一句可以貼進提示詞的通則，或 null",
  "negative_controls": ["什麼情況下不適用"],
  "do_not_generalize": ["不該從這些例子推出來的事"],
  "rationale": "一段簡短的、指出可見證據的說明"
}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; the streams live in its review-ui/ subdirectory")
    parser.add_argument("--model", default="splash", choices=sorted(ask_about_blocks.ENDPOINTS))
    parser.add_argument("--min-confirmations", type=int, default=2,
                        help="how many confirmed questions of one class before a proposal is asked "
                             "for (default 2: one example is an anecdote, not a rule)")
    parser.add_argument("--apply", action="store_true",
                        help="call the model and write the proposals; without it nothing is written "
                             "and no model is called (dry run is the default)")
    parser.add_argument("--report", action="store_true",
                        help="print what is confirmed and what is proposed, and change nothing")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--timeout", type=int, default=1800)
    return parser.parse_args()


def queue_dir_of(queue: str) -> str:
    """`<queue>/review-ui`, tolerating the subdirectory itself (same rule as `repair_loop`)."""
    return repair_loop.review_ui_dir(queue)


def events_path(queue_dir: str) -> str:
    return os.path.join(queue_dir, "question_review_events.jsonl")


def principles_path(queue_dir: str) -> str:
    return os.path.join(queue_dir, discuss.PRINCIPLES_STREAM)


def load_rows(queue_dir: str) -> dict:
    return {row.get("candidate_key"): row
            for row in repair_loop.load_candidates(os.path.join(queue_dir, "candidates.jsonl"))
            if row.get("candidate_key")}


def with_field(row: dict, field: str, value) -> dict:
    """A copy of `row` with one field set to `value`, for the same two spellings as `field_value`."""
    out = dict(row)
    if field.startswith("option "):
        key = field[len("option "):].strip().upper()
        options = [dict(option or {}) for option in (row.get("options") or [])]
        for option in options:
            if str(option.get("key") or "").upper() == key:
                option["text"] = value
        out["options"] = options
        return out
    out[field] = value
    return out


def snapshots_for(row: dict, changes: list, applied: str = "") -> tuple:
    """`(before, after)` of one confirmed repair, rebuilt from its own `changes[]`.

    Rebuilt from the repair's text rather than from what the row holds today, because **today's row is
    not evidence about what was accepted**: the repair may have been withdrawn (the row holds the
    parser's text again) or followed by other edits. The repair's own `from`/`to` is the record of what
    was on screen when the person said yes, so that is what the two snapshots are built from.

    Two `applied` forms, and they mean different things by `from`/`to` (`apply_dispute_repairs`):
    `field` replaces the whole field, `substitution` replaces one character inside it. Assigning a
    substitution's single character would replace the whole stem with one glyph, so the form is read
    rather than guessed (`repair_loop.confirmations_by_key` carries it).
    """
    before, after = dict(row), dict(row)
    for change in changes:
        field = str(change.get("field") or "")
        if not field:
            continue
        if applied == "substitution":
            source, target = str(change.get("from") or ""), str(change.get("to") or "")
            text = str(row.get(field) or "")
            before = with_field(before, field, text.replace(target, source))
            after = with_field(after, field, text.replace(source, target))
            continue
        before = with_field(before, field, change.get("from"))
        after = with_field(after, field, change.get("to"))
    return before, after


def confirmed_changes(queue_dir: str, rows: dict) -> list:
    """`[{candidate_key, question_number, change_class, before, after, diff, changes, confirmed_at}]`.

    One row per confirmed question, already classified. The `change_class` comes from
    `review_feedback.classify_change` - the same function the correction-feedback path uses - so
    "what kind of change is this" has one answer in the project.
    """
    out = []
    confirmations = repair_loop.confirmations_by_key(events_path(queue_dir))
    for key, confirmed in sorted(confirmations.items()):
        row = rows.get(key)
        changes = [change for change in (confirmed.get("changes") or []) if change.get("field")]
        if not row or not changes:
            continue
        before, after = snapshots_for(row, changes, str(confirmed.get("applied") or ""))
        diff = diff_snapshots(before, after)
        if not diff:
            # 一場空的改變沒有可學的東西：修復本身沒變（`from == to`），或它動的欄位不在可見欄位裡。
            continue
        out.append({"candidate_key": key,
                    "question_number": row.get("question_number"),
                    "change_class": classify_change(before, after, diff),
                    "before": before, "after": after, "diff": diff,
                    "changes": changes, "confirmed_at": confirmed.get("confirmed_at"),
                    # **這一題的範圍**：這一筆缺陷是在哪個科目、哪個考別、哪一份試卷上量到的。
                    # 三個值各用既有的一個讀者（`ai_findings.subject_of`／`category_of` 是判讀提示詞
                    # 用的那兩個，`repair_loop.paper_of` 是區塊報告用的那一個），不是這裡再發明第二種
                    # 拼法。沒有它，`classify_change` 是唯一的分組依據，於是策展員會從一組不同科目的
                    # 例子裡提出一條通則——站上的 p9 就是這樣生出來、又被 owner 拿掉的。
                    "subject": ai_findings.subject_of(row),
                    "category": ai_findings.category_of(row),
                    "paper": repair_loop.paper_of(key),
                    # **這一題的圖在紙本上的實測事實**，用畫面同一個詞彙（`ai_findings.figures_note`）。
                    # 沒有它，策展員只看得到文字的前後，於是「這一題有沒有圖、圖屬於哪個選項、歸屬量過
                    # 沒有」在提案時是隱形的——而 2026-09-25 owner 回報的正是圖被貼錯：一條關於圖的
                    # 通則不可能從看不見圖的例子裡生出來。
                    "figure_facts": ai_findings.figures_note(row)})
    return out


def classes_of(confirmations: list) -> dict:
    """`{change_class: [confirmation rows]}` — the groups a proposal may be asked for."""
    groups = collections.defaultdict(list)
    for row in confirmations:
        groups[str(row.get("change_class") or "unknown")].append(row)
    return groups


def curation_packet(change_class: str, rows: list, principles: list) -> dict:
    """What the curator is shown: the confirmed examples of one class, verbatim and bounded.

    The per-example field names are `review_feedback`'s (`before`/`after`/`diff`/`evidence`), so that
    "the evidence for a rule" is described once in the project; the task framing and the answer shape
    live in `CURATOR_SYSTEM`, because asking a model the same thing once per example would be N copies
    of one instruction.

    Two things the packet carries besides the examples, both because of a measured failure:

    `effective_principles` is `ai_findings.principles_for_prompt`'s list - **one renderer**, the same
    one the reading prompt gets its 基本原則 from, so "what is already in force" cannot differ between
    the reader and the curator. Without it the curator cannot tell a new rule from a paraphrase of the
    person's own: the machine's p8 and the person's p6 were both about sub/superscripts.

    `subject`/`category`/`paper` per example is the scope the defect was measured in. Grouping is by
    `review_feedback.classify_change` alone, so two examples of one class may come from papers that
    share nothing else; the curator cannot say "this holds for 藥學, not elsewhere" about a defect it
    never saw the subject of - and p9, the rule the owner removed, was exactly that generalisation.

    `figure_facts` is the same rendering the reading prompt gets (`ai_findings.figures_note`), not a
    second summary of it: a rule about pictures has to be proposed from the picture state, and two
    renderings of "what this question's images are" would be two things that can disagree.
    """
    return {
        "task_type": "principle_candidate",
        "change_class": change_class,
        "confirmed_questions": len(rows),
        "effective_principles": list(principles or []),
        "examples": [
            {"candidate_key": row["candidate_key"],
             "question_number": row.get("question_number"),
             # 人的確認時間就是這一筆例子的出處：它是審核事件流裡的一筆事件，可以回去查。
             "confirmed_at": row.get("confirmed_at"),
             # 這一筆缺陷量到的範圍，以及這個範圍用哪三個既有的名字表達（`confirmed_changes`）。
             "subject": row.get("subject") or "",
             "category": row.get("category") or "",
             "paper": row.get("paper") or "",
             "before_stem": row["before"].get("stem"),
             "after_stem": row["after"].get("stem"),
             "diff": row["diff"],
             "figure_facts": row.get("figure_facts") or None}
            for row in rows
        ],
    }


def existing_proposals(queue_dir: str) -> set:
    """`{(change_class, (candidate_key, …))}` for this script's **still-active** proposals.

    A second `--apply` would otherwise propose the same rule again - the confirmation stream is
    append-only and `classify_change` is deterministic, so nothing about a re-run is new. Skipping by
    "same class, same confirmed questions" keeps a re-run a no-op while still letting a **new**
    confirmation (same class, one more question) become a new proposal: the evidence set is the key, so
    it changes exactly when the evidence does. Removed or rejected proposals are not in
    `principles_projection`'s active list, so a rule that was taken off the board can come back.
    """
    events = discuss.load_events(principles_path(queue_dir))
    active = discuss.principles_projection(events)["principles"]
    return {(str(event.get("change_class") or ""), tuple(sorted(event.get("evidence") or [])))
            for event in active
            if str(event.get("source") or "") == CURATOR_SOURCE}


def ask_curator(packet: dict, *, endpoint, args) -> tuple:
    """One call. Returns `(parsed_or_None, raw, error, seconds)`.

    Through `ask_about_blocks.ask`, like every other pass in this repo, so the engine-specific thinking
    switch and the egress gate stay in `qbr.engines` (`QBR_ALLOW_EXTERNAL_LLM` keeps an off-network
    provider off unless someone turns it on; `splash`/`dgx-*` are on-network).

    The answer is read with `ai_findings.json_object` - the same object reader `parse_finding` uses -
    so a fenced or prose-wrapped answer is read the same way in both places.
    """
    messages = [
        {"role": "system", "content": CURATOR_SYSTEM},
        {"role": "user", "content": json.dumps(packet, ensure_ascii=False, indent=1, sort_keys=True)},
    ]
    _parsed, raw, complaint, _usage, seconds = ask_about_blocks.ask(
        messages, endpoint=endpoint, max_tokens=args.max_tokens, timeout=args.timeout)
    return ai_findings.json_object(raw), raw, complaint, seconds


def proposal_event(queue_dir: str, *, change_class: str, text: str, keys: list, rationale: str) -> dict:
    """The `add` event a person then approves or rejects. Shape is `discuss.append_event`'s.

    `evidence` is the list of candidate keys the proposal came from, the same field name the curated
    and hand-written principles use, so the 原則區 can call up the original paper for any proposal.
    `reviewer`/`source` say a machine proposed it; the `approve` event that would make it effective is
    written by `/api/principles`, with a person behind it.
    """
    return {
        "schema": discuss.PRINCIPLE_SCHEMA,
        "action": "add",
        "principle_id": discuss.next_id(discuss.load_events(principles_path(queue_dir)), "p"),
        "text": text,
        "scope": "question",
        "reviewer": CURATOR_REVIEWER,
        "source": CURATOR_SOURCE,
        "evidence": sorted(keys),
        "change_class": change_class,
        "rationale": rationale,
    }


def print_state(queue_dir: str, confirmations: list, args) -> None:
    groups = classes_of(confirmations)
    principles = discuss.load_events(principles_path(queue_dir))
    projection = discuss.principles_projection(principles)
    print("被同意的機器修復 %d 題（讀 %s 的同一個折疊）"
          % (len(confirmations), events_path(queue_dir)))
    for change_class, rows in sorted(groups.items()):
        mark = "" if len(rows) >= args.min_confirmations else "（未達門檻 %d）" % args.min_confirmations
        print("  %-14s %d 題%s" % (change_class, len(rows), mark))
    proposals = [event for event in principles
                 if str(event.get("source") or "") == CURATOR_SOURCE]
    print("原則流 %d 筆事件、有效 %d 條、已核准 %d 條、其中這一支提案的 %d 條（%s）"
          % (projection["event_count"], projection["count"], projection["approved_count"],
             len(proposals), principles_path(queue_dir)))
    # 提案與核准分得開，是這一支最要緊的一件事：印出來，讓讀 log 的人不必相信我的話。
    print("（這一支只寫 add；核准是人在 /api/principles 按下的，永遠不是這裡。）")


def report(queue_dir: str, args) -> int:
    print_state(queue_dir, confirmed_changes(queue_dir, load_rows(queue_dir)), args)
    return 0


def main() -> int:
    args = parse_args()
    queue_dir = queue_dir_of(args.queue)
    if not os.path.isdir(queue_dir):
        print("找不到 %s——這個佇列還沒建好，不做任何事。" % queue_dir, file=sys.stderr)
        return 1
    if args.report:
        return report(queue_dir, args)

    rows = load_rows(queue_dir)
    confirmations = confirmed_changes(queue_dir, rows)
    print_state(queue_dir, confirmations, args)
    if not args.apply:
        print("（乾跑：不寫任何檔案、不呼叫任何模型。要真的做請加 --apply。）")
        return 0

    endpoint = ask_about_blocks.ENDPOINTS[args.model]
    refused = engines.egress_refusal(endpoint)
    if refused:
        print("不呼叫 %s：%s" % (args.model, refused))
        return 0

    proposed = 0
    already = existing_proposals(queue_dir)
    # 已核准的原則：策展員要先看得見現在生效的是什麼，才不會把人寫過的話重寫一遍（p8 與 p6）。
    # 讀的是同一個渲染（`ai_findings.principles_for_prompt`），所以「已經生效」在這裡與在判讀提示詞
    # 裡是同一個集合，不會有第二種說法。
    effective_principles = ai_findings.principles_for_prompt(
        discuss.load_events(principles_path(queue_dir)))
    print("（封包的 `effective_principles` 是已核准的 %d 條——策展員看得見已經生效的原則，才不會"
          "把人寫過的話重寫一遍。）" % len(effective_principles))
    for change_class, group in sorted(classes_of(confirmations).items()):
        if len(group) < args.min_confirmations:
            continue
        keys = sorted(row["candidate_key"] for row in group)
        if (change_class, tuple(keys)) in already:
            print("  %-14s %d 題：同一批例子已經提案過了，不重複提。" % (change_class, len(group)))
            continue
        packet = curation_packet(change_class, group, effective_principles)
        print("  %-14s %d 題：問 %s 一次…" % (change_class, len(group), args.model), flush=True)
        parsed, _raw, error, seconds = ask_curator(packet, endpoint=endpoint, args=args)
        if parsed is None:
            print("    讀不到回應（%s），這一類不提案。" % (error or "unparsed"))
            continue
        status = str(parsed.get("status") or "").strip()
        text = str(parsed.get("principle_text") or "").strip()
        print("    %s（%.1fs）" % (status or "（沒有 status）", seconds))
        if status == "already_covered":
            # 與 `no_generalization` 一樣是「不提案」，只是理由不同：這條規則在**現行原則**裡已經
            # 有了。寫下去的話，人的話會被機器重寫一遍，而人還要再讀一次同一件事。
            print("    不寫提案：已經被現行原則涵蓋——%s"
                  % (parsed.get("rationale") or "模型沒有指名是哪一條"))
            continue
        if status != "candidate" or not text:
            print("    不寫提案：%s" % (parsed.get("rationale") or "模型沒有提出通則"))
            continue
        event = proposal_event(queue_dir, change_class=change_class, text=text,
                               keys=[row["candidate_key"] for row in group],
                               rationale=str(parsed.get("rationale") or ""))
        # 共用的寫者（`discuss.append_event`）：一份流一個寫法，`created_at` 也只有一個來源。
        discuss.append_event(principles_path(queue_dir), event)
        proposed += 1
        print("    提案 %s：%s" % (event["principle_id"], text))
        print("      反例：%s" % ("；".join(parsed.get("negative_controls") or []) or "（沒寫）"))
        print("      不該泛化：%s" % ("；".join(parsed.get("do_not_generalize") or []) or "（沒寫）"))
    print()
    print("寫了 %d 筆**提案**。提案在畫面上是「待你核准」，核准之前不會進任何一次讀取的提示詞。"
          % proposed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
