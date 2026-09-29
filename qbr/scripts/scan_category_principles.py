# -*- coding: utf-8 -*-
"""整科掃描：把審題者的基本原則套在**一個考別的每一題**上，不只讀有爭議或被人標記的題目。

為什麼需要這支腳本（owner 2026-09-24）
--------------------------------------

    「這次我要求新整理出來的總規則要重新審視藥師、藥師(二)的所有題目（屬於測試將原則拓展跟
      科目全掃描的概念）」

新整理好的基本原則是一句**通則**，它的適用範圍不是「有人標記過的題」，而是那個考別的**每一題**
——沒被標記的題目正是還沒有人看過的題目。而 `confirm_dispute.py` 的工作清單只有兩種來源：人的
standing `block`（`--blocked-only`／`--pending-only`）與爭議種類（`--kind`）。兩者都是「有人先
說話」的清單，剛好排除了整科掃描要讀的那些題，所以那個清單不可能從它們推導出來——這支腳本就是
那個入口。

讀法沒有第二份
--------------

選題、跳過、批次是新的；**讀**沒有理由重寫。`confirm_dispute.confirm_one` 是唯一的讀法：同一張
截圖（`crop_for` → `reread.band_rows` 的「這一題自己的列」）、同一段提示詞（`transcribe_system`
＝ `reread.SYSTEM` ＋ 基本原則 ＋ 審題者的回答 ＋ 這一題的註解）、同一個 append（
`ai_findings.append`）。一個讀法有兩份實作，就是兩個可以各自漂移的量測，而記錄上分不出來——
這正是這個專案反覆量到最貴的一種錯。

寫進去的 `population` 是 `category-scan`，而且那不是裝飾
--------------------------------------------------------

  * `apply_dispute_repairs.py` 只取 `population == "dispute"` 的紀錄（見該檔 `main()` 的
    `latest_by_question` 迴圈），所以整科掃描的讀法**不會**被套用那一步拿去改題目文字。掃描說
    「紙本與抽取哪裡不一樣」；要不要改是人與那一步的事（GOV-05：這一支不寫 review event、不動
    題目）。
  * `prompt_version` 把 population 一起雜湊（`ai_findings.POPULATIONS`），所以「有人標記過的題」
    與「整科掃到、沒有人標記過」不會共用一個版本號——它們是兩個不同的量測，而記錄必須說得出
    自己是哪一個。

可中斷、可續跑
--------------

一題讀完就 append 一行（`ai_findings.append` 每筆開關一次檔案），所以中途被殺掉的損失上限是
正在讀的那一題。續跑用 `--skip-confirmed`：跳過的條件是**這一題現在的讀法**（
`ai_findings.reading_fingerprint`）已經有一筆 `category-scan` 紀錄。讀法改了（例如有人真的修過
文字）指紋就變了，那一題自己會回來，不必有人記得去清一個旗標。

用法
-----

    # 先看會讀哪些題。**預設就是 dry run**：不送模型、不寫任何檔案、不拍截圖。
    .venv/bin/python scripts/scan_category_principles.py --queue data/review-queues/live \\
        --category __pharmacist_track__

    # 真的讀，一批 2000 題；中斷後同一條指令再跑一次，讀過的會跳過
    .venv/bin/python scripts/scan_category_principles.py --queue data/review-queues/live \\
        --category __pharmacist_track__ --limit 2000 --skip-confirmed --apply

    # 目前為止讀了什麼（各考別一列）
    .venv/bin/python scripts/scan_category_principles.py --queue data/review-queues/live --report
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import ai_findings, discuss  # noqa: E402
from scripts.serve_question_review_ui import CATEGORY_GROUP_FILTERS, category_matches_filter  # noqa: E402
import ask_about_blocks  # noqa: E402
import confirm_dispute  # noqa: E402
import repair_loop  # noqa: E402

#: The label every record this script writes carries.
#:
#: `dispute` would be a lie in two directions at once: it would tell the applier (`population ==
#: "dispute"`) that these readings may be applied as repairs, and it would file a reading of a question
#: nobody flagged under the same prompt generation as a reading of one a person did. A scan is its own
#: population, so it gets its own label - and its own framing in `ai_findings.POPULATIONS`, because the
#: model is handed a crop for a question that was selected by 考別 rather than by a person or a detector.
POPULATION = "category-scan"

#: How many questions a dry run lists by name. A dry run over a whole track is 17,790 lines if it lists
#: everything, which buries the number that matters (how many will be read); the counts are the answer,
#: the lines are the spot check.
SHOW = 20

#: What one round selected, and what the filter did to the population on the way.
Selection = collections.namedtuple("Selection", "rows matched skipped by_category")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True,
                        help="queue root; candidates and crops live under its review-ui/")
    # `--category` 選題時必填，**但 `--report` 不用**：報告讀的是整份 findings，各考別都在裡面，
    # 這時候要一個考別只是形式。argparse 的 `required=True` 表達不了這種條件，所以在下面明講一次，
    # 而不是讓 `--report` 在一個它用不到的參數上停下來。
    parser.add_argument("--category", nargs="+",
                        help="考別名稱，或制度群組 key（%s）；可給多個，取聯集。"
                             "選題時必填（只有 --report 可以省略）"
                             % "、".join(sorted(CATEGORY_GROUP_FILTERS)))
    parser.add_argument("--limit", type=int, default=0,
                        help="這一輪最多讀幾題（0 = 全部）。批次大小是續跑的工具：中斷後同一條"
                             "指令會從還沒讀過的題目接著讀")
    parser.add_argument("--skip-confirmed", action="store_true",
                        help="同一個讀法已經有 category-scan 紀錄的題目跳過（續跑用；沒有它，"
                             "跑第二次就是把整批重拍重問一次）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只印出選到的題目就結束；**這本來就是預設**，這個旗標是把它寫出來")
    parser.add_argument("--apply", action="store_true",
                        help="真的拍紙本、送模型、寫 finding（沒有這個旗標就什麼都不做）")
    parser.add_argument("--out", help="findings 路徑；預設 <queue>/review-ui/"
                                        + ai_findings.STREAM)
    parser.add_argument("--report", action="store_true",
                        help="只讀回已寫的 category-scan 紀錄，印各考別統計後結束")
    # Default only to an approved loopback endpoint. A remote inference host needs a fresh owner-
    # approved contract and must not remain a convenience default when it is unavailable.
    parser.add_argument("--model", default="mtplx-35b", choices=sorted(ask_about_blocks.ENDPOINTS),
                        help="本機引擎名稱（見 engines.py），不是 URL")
    parser.add_argument("--principles", metavar="PATH",
                        help="審題者的基本原則流（預設：這一條 queue 自己的 "
                             + discuss.PRINCIPLES_STREAM + "）；只有人核准過的原則會進提示詞")
    parser.add_argument("--dpi", type=int, default=200, help="截圖解析度")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-tokens", type=int, default=2000,
                        help="一次轉錄只有幾百個 token；留餘裕是為了不要讓 JSON 被切斷"
                             "（切斷會失去整份答案）")
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.category and not args.report:
        parser.error("--category 是必填的（只有 --report 可以省略）")
    return args


def selected_questions(queue_dir, categories, limit=0, *, already=None):
    """這一輪要讀的題目：**只看考別**，不看爭議種類，也不看任何人的決定。

    That is the whole point of this tool and the one thing it must not inherit from the dispute loop:
    a question carrying no dispute and no human decision is exactly the question nobody has looked at,
    and the newly curated 基本原則 are general - their scope is the 考別, not a list of flags. So the
    two gates of `confirm_dispute.disputed_questions` (a standing `block`, a confirmable dispute kind)
    are deliberately absent here. Category selection goes through the server composition root's public
    re-export of the canonical `category_matches_filter`, rather than a local spelling of the same idea:
    `__pharmacist_track__` and the names it contains must select the same set as the app's own filter,
    including the full-width bracket spelling some rows carry (`藥師（一）`), which is folded by the UI
    matcher and by nothing else.

    `already` is `{candidate_key: reading_sha256}` — the readings this batch has already been asked
    about. A key is skipped only while its **current** reading still hashes to the recorded one, so a
    repair re-opens the question by itself.

    Returns `Selection(rows, matched, skipped, by_category)`: `matched` counts the questions in the
    named categories before the skip (that is the population the report is about), `skipped` counts
    the ones `already` took out, and `by_category` maps each `--category` value to what it matched.
    `--limit` applies **after** the skip, so a resumed batch is the next N unread questions rather than
    N questions of which most are skipped.
    """
    candidates = os.path.join(queue_dir, "candidates.jsonl")
    if not os.path.isfile(candidates):
        print("找不到 candidates.jsonl：%s" % queue_dir, file=sys.stderr)
        return Selection([], 0, 0, {})
    already = {} if already is None else already
    by_category = collections.OrderedDict((want, 0) for want in categories)
    rows = []
    matched = 0
    skipped = 0
    for question in repair_loop.load_candidates(candidates):
        category = ai_findings.category_of(question)
        wanted = next((want for want in categories if category_matches_filter(category, want)), None)
        if wanted is None:
            continue
        matched += 1
        by_category[wanted] += 1
        key = question.get("candidate_key")
        if key in already and already[key] == ai_findings.reading_fingerprint(question):
            skipped += 1
            continue
        rows.append(question)
    if limit:
        rows = rows[:limit]
    return Selection(rows, matched, skipped, by_category)


def confirmed_keys(store_path):
    """`{candidate_key: reading_sha256}` — the current readings this pass has already read.

    Deliberately not `confirm_dispute.confirmed_keys`, which asks the identical question of the same
    store and **is pinned to `population == "dispute"`** (it exists for the resident dispute loop).
    A scan record carries `category-scan`, so calling it would answer "nothing has been read" forever
    and `--skip-confirmed` would re-render the whole batch on every resume — the flag would look
    present and do nothing. The *rule* is the same one, including the half that took a defect to
    learn: a record whose page read **failed** does not count, because every error path still writes
    a record (`finding_from` builds one with `error` set) and a transient endpoint outage must not
    silently drop a question out of the work list forever.
    """
    if not store_path or not os.path.isfile(store_path):
        return {}
    confirmed = {}
    for key, record in ai_findings.latest_by_question(store_path).items():
        if record.get("population") != POPULATION:
            continue
        if record.get("error") or (record.get("finding") or {}).get("error"):
            continue
        # A reading was actually obtained when the model returned one. An empty `changes` list is a
        # legitimate "the page agrees", so emptiness is not the test — the presence of a reading is,
        # which is exactly how an unreadable crop stays out of this map.
        if (record.get("finding") or {}).get("transcription") is None:
            continue
        confirmed[key] = record.get("reading_sha256")
    return confirmed


def append_finding(out, question, result, endpoint):
    """One finding, in the shared schema, carrying this pass's own population.

    `confirm_dispute._append_finding` cannot be called for this, and the reason is the label: the
    population is a literal inside it (`"dispute"`), and that literal is what keeps
    `apply_dispute_repairs.py` from applying a reading as a repair. Everything else is the same shape
    on purpose — same `make_record`, same stored prompt (the one that was *sent*, from
    `transcribe_system`, not one rebuilt from a template), same evidence beside the note.

    A result with no readable page is still recorded: `讀不到` is the most useful record there is
    (it is the set of questions a later round still owes), and crashing on the first unreadable crop
    would take the whole round down.
    """
    kinds = result.get("kinds") or []
    learned = {"detector": "；".join(kinds)} if kinds else None
    finding = result.get("finding") or {}
    principles = result.get("principles") or None
    answers = result.get("answers") or None
    notes = result.get("notes") or None
    system = confirm_dispute.transcribe_system(principles, answers, notes)
    _template_system, user = ai_findings.build_prompt(question, learned=learned,
                                                      population=POPULATION, principles=principles,
                                                      notes=notes)
    record = ai_findings.make_record(
        question=question, finding=finding, model=endpoint["name"], endpoint=endpoint["url"],
        prompt_system=system, prompt_user=user, raw=result.get("raw") or "",
        usage=result.get("usage") or {}, seconds=result.get("seconds") or 0.0,
        error=None if result.get("error") is None else result["error"],
        learned=learned, population=POPULATION, crop=result.get("crop"),
        changes=result.get("changes"), principles=principles, answers=answers, notes=notes)
    # The row's own disputes (usually none — that is the point of a category scan) and the crop's size
    # are the evidence this note is about, beside the note rather than inside it: a reader has to be
    # able to see the claim and the picture.
    record["evidence"] = {**(record.get("evidence") or {}),
                          "disputes": confirm_dispute.dispute_reasons(question, []),
                          "rows": result.get("rows"),
                          "crop_bytes": (os.path.getsize(result["crop_png"])
                                         if result.get("crop_png")
                                         and os.path.exists(result["crop_png"]) else None)}
    ai_findings.append(out, record)


def report(path):
    """Per-category counts of what this pass has read so far."""
    records = ai_findings.load(path)
    scans = [r for r in records if r.get("population") == POPULATION]
    if not scans:
        print("這個紀錄裡還沒有整科掃描（population=%s）。" % POPULATION)
        return 0
    by_category = {}
    for record in scans:
        bucket = by_category.setdefault(record.get("category") or "（未知）",
                                        {"total": 0, "differed": 0, "agreed": 0, "failed": 0})
        bucket["total"] += 1
        if record.get("error"):
            bucket["failed"] += 1
        elif record.get("changes"):
            bucket["differed"] += 1
        else:
            bucket["agreed"] += 1
    print("整科掃描 %d 筆（population=%s）：" % (len(scans), POPULATION))
    for category, bucket in sorted(by_category.items(), key=lambda item: -item[1]["total"]):
        print("  %-12s %6d 題：紙本與抽取不一致 %6d、一致 %6d、讀不到 %4d"
              % (category, bucket["total"], bucket["differed"], bucket["agreed"], bucket["failed"]))
    # **這批是用哪一版的原則讀的。** 基本原則會被編輯，而一個換過原則的提示詞是另一次量測
    # （`prompt_version` 就是為了這件事存在）；不印出來的話，「掃過了」與「用舊原則掃過了」在
    # 畫面上長得一模一樣。
    print("prompt_version：%s"
          % "、".join(sorted({record.get("prompt_version") or "?" for record in scans})))
    return 0


def main() -> int:
    args = parse_args()
    if args.dry_run and args.apply:
        print("--dry-run 與 --apply 只能選一個（沒給 --apply 時本來就是 dry run）。",
              file=sys.stderr)
        return 2
    queue = args.queue
    queue_dir = repair_loop.review_ui_dir(queue)
    out = args.out or ai_findings.store_path(queue)

    if args.report:
        return report(out)

    already = confirmed_keys(out) if args.skip_confirmed else None
    selection = selected_questions(queue_dir, args.category, args.limit, already=already)
    print("考別：%s" % "、".join(args.category))
    for wanted, count in selection.by_category.items():
        print("  %-12s %6d 題" % (wanted, count))
    print("符合 %d 題；其中 %d 題的這個讀法已經有紀錄（跳過）；這一批 %d 題。"
          % (selection.matched, selection.skipped, len(selection.rows)))
    for question in selection.rows[:SHOW]:
        print("  q%-5s %-12s %s" % (str(question.get("question_number")),
                                    ai_findings.category_of(question),
                                    question.get("candidate_key")))
    if len(selection.rows) > SHOW:
        print("  …（其餘 %d 題）" % (len(selection.rows) - SHOW))
    if not selection.rows:
        print("這一輪沒有要讀的題目。")
        return 0
    # **預設是 dry run。** 送模型是貴的那一半（見 `repair_daemon.sh` 開頭：掃描便宜、修復貴），而
    # 一個只是看錯參數的執行不該把整個考別送去模型。
    if not args.apply:
        print("dry run（預設）：沒有送模型、沒有寫任何檔案、沒有拍截圖。要真的讀請加 --apply。")
        return 0

    endpoint = ask_about_blocks.ENDPOINTS[args.model]
    # The crops live in the queue's own `review-ui/crops/`, so a screenshot the model was shown is
    # served by the same route as every figure crop and a rebuild carries it with the queue (`crop_for`
    # writes it there; `confirm_one` stores the queue-relative path).
    crops_root = os.path.join(queue_dir, "crops")
    queue_root = os.path.dirname(queue_dir) if os.path.basename(queue_dir) == "review-ui" \
        else queue_dir
    print("對 %d 題看紙本並轉錄（%s @ %s）" % (len(selection.rows), endpoint["name"],
                                              endpoint["url"]))
    print("寫到：%s" % out)

    # 一輪讀一次的兩個頻道（`confirm_dispute.main` 讀的是同一份，同一種理由）：原則與回答是這一輪
    # 的**約束**，中途有人改一條原則不該只對後面幾題生效——那會讓同一批量測聲稱兩個提示詞版本。
    principles_path = args.principles or os.path.join(queue_dir, discuss.PRINCIPLES_STREAM)
    principle_events = discuss.load_events(principles_path)
    projection = discuss.principles_projection(principle_events)
    # 只有人核准過的原則進提示詞：一條沒被核准的原則若進了提示詞，模型就會照著一句人還沒看過的
    # 話改題目文字，而畫面上那個「待你核准」的標記會變成一句謊（見 `principles_for_prompt`）。
    principles = ai_findings.principles_for_prompt(principle_events)
    print("基本原則 %d 條（已核准 %d／待核准 %d）（%s）"
          % (len(principles), projection["approved_count"], projection["pending_count"],
             principles_path))
    # **這一輪的存續理由就是讓新整理的原則生效，所以「一條都沒核准」不能只是靜靜地印一個 0。**
    # 實測（2026-09-24，本機鏡射）：原則流裡有 5 條有效、**0 條已核准**——現在按下 --apply，
    # 整科 17,790 題會用一個沒有任何原則的提示詞讀完（十幾個小時），而畫面上看起來一切正常。
    # 不擋下來（讀紙本本身仍有價值），但要把話說清楚。
    if projection["pending_count"] and not principles:
        print("⚠ 沒有任何已核准的原則：待核准的 %d 條**不會**進提示詞。"
              "要先在錯題討論區按下核准，這一輪才讀得到它們。" % projection["pending_count"])
    questions_path = os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM)
    # `repair_questions_projection` also contains machine-authored answers; the loop's existing
    # human-event whitelist is the canonical filter, so these reports cannot pose as reviewer guidance.
    answers_by_key = repair_loop.human_answers_by_key(questions_path)
    answered = [row for group in answers_by_key.values() for row in group["questions"]]
    answers = {"questions": answered} if answered else None
    # **這一輪到底帶了什麼進提示詞，要印出來。** 三個頻道都會被存進紀錄（`principles`／`answers`／
    # `notes`），但一個讀 log 的人不該為了知道「這次有沒有在回答的基礎上讀」而去翻 17,790 筆紀錄。
    print("審題者的回答 %d 筆（%s）" % (len(answered), questions_path))

    # 審題者寫在某一題上的註解：一輪折一次（`repair_loop.notes_by_key` 是同一個折疊規則的第二次
    # 使用，第一次是工作清單），每題只拿自己的那一份——模型手上是這一題的截圖，別題的註解只會讓它
    # 去找不存在的東西。
    events_path = os.path.join(queue_dir, "question_review_events.jsonl")
    notes_by_key = repair_loop.notes_by_key(events_path)
    annotated = [question for question in selection.rows
                 if notes_by_key.get(question.get("candidate_key"))]
    print("審題者的註解 %d 題（%s）" % (len(annotated), events_path))
    print()

    differed = 0
    agreed = 0
    failures = 0
    total = len(selection.rows)
    for index, question in enumerate(selection.rows, 1):
        note = notes_by_key.get(question.get("candidate_key"))
        result = confirm_dispute.confirm_one(question, endpoint=endpoint, args=args,
                                             crops_root=crops_root, queue_root=queue_root,
                                             principles=principles, answers=answers, notes=note)
        category = ai_findings.category_of(question) or "?"
        number = str(result.get("question_number"))
        verdict = (result.get("finding") or {}).get("verdict") or "?"
        if result.get("error"):
            failures += 1
            outcome = "讀不到（%s）" % result["error"]
        elif result.get("changes"):
            differed += 1
            outcome = "%s：紙本與抽取不一致 %d 處" % (verdict, len(result["changes"]))
        else:
            agreed += 1
            outcome = "%s：紙本與抽取一致" % verdict
        # `flush=True` 不是美觀問題：這一支可能跑十幾個小時，被 tee 到檔案裡，而一個緩衝住的
        # 進度輸出在「現在讀到第幾題」這個問題上等於沒有輸出。
        print("[%d/%d] %-12s q%-5s %s" % (index, total, category, number, outcome), flush=True)
        # 一題一筆，立刻落地。被殺掉的損失上限就是正在讀的這一題——`--skip-confirmed` 續跑時看到
        # 的就是這裡已經寫下去的那些。
        append_finding(out, question, result, endpoint)

    print()
    print("讀完 %d 題：不一致 %d、一致 %d、讀不到 %d。" % (total, differed, agreed, failures))
    print("符合 %d 題、跳過已讀 %d 題（--skip-confirmed）；寫到 %s。"
          % (selection.matched, selection.skipped, out))
    print("以上是**讀**的結果，advisory：讀不改題目文字、不寫人為判決"
          "（population=%s；套用程式只取 population=dispute）。" % POPULATION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
