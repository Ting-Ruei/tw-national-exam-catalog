#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""掃描：把「上次檢查之後狀態有變」的題放進 pending。不呼叫模型，不改題。

    python3 scripts/scan_for_repairs.py --queue <dir>
    python3 scripts/scan_for_repairs.py --queue <dir> --dry-run     # 只算命中幾題，不寫狀態

**為什麼與修復分開。** owner（2026-09-24）：「我認為掃描到跟做完了是兩回事」，以及
「掃描如果依照規則觸發，可能會反覆吃算力」。掃描便宜（比對指紋），修復貴（呼叫模型），
所以它們的頻率該分開，而且掃描不該有模型預算。

**為什麼是狀態變化，不是條件。** 舊版的工作清單是「沒有任何偵測器能解釋的 block」，
三條規則上線後 304 題全部都有偵測器 → 每輪零題（`repair_daemon.sh:12-23` 自己記著這件事）。
條件式清單會縮到零；「有什麼變了」不會。

**閘門：只看人已 block 的題。**（owner 2026-09-24，兩次都同一句話：「我才說後面的agent循環是
僅針對block去看」、「你有動過的就放入AI已解決，我認為不合理自然會打回block並且寫註解，這樣人機
交互才有效率」）。這一條不是風格，是實測出來的兩個方向都錯：

  本機鏡射（`data/review-queues/live`，2026-09-24）：
    有人 standing `block` 的題                 279
    有爭議種類（有偵測器解釋）的題               813
    其中真的是人 block 的                        105
    **人 block 了、但一個爭議種類都沒有的**       174

  1. **大部分被問的題沒有人看過。** 813 題裡只有 105 題是人擋的，所以舊閘門把 708 題
     「沒有任何人標記過」的題排進工作清單——機器在替沒人看過的題花算力，而人真正標記的題排在
     後面。
  2. **人標記的題有一大半看不到。** 279 題被 block 的裡面有 174 題沒有任何偵測器種類，於是
     `wanted_kinds ∩ dispute_kinds` 永遠是空的：**審題者說「這題有問題」而他標記的那題，正是
     管線唯一能替他讀紙本的題——卻因為偵測器沒說話而從清單上消失。** 一個被 block、又沒有種類
     的題，是「機器的讀法是唯一能說出哪裡錯的東西」的那種題，不是可以跳過的題。

  所以閘門是 standing `block`（`repair_loop` 的折疊），爭議種類只留在指紋裡（一個已經被 block
  的題上，偵測器改變心意仍然是新工作），不再是選題的條件。「沒有任何偵測器解釋的 block」那條
  舊清單之所以會縮到零，正是因為它把種類當條件；新的閘門不談種類，所以不會再縮到零。

`--pending-only` 的存在是給 `confirm_dispute.py` 用的，不是這裡——這裡只負責產生 pending。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import ai_findings, discuss, scan_state  # noqa: E402
import confirm_dispute  # noqa: E402
import repair_loop  # noqa: E402
from qbr.dispute_apply.withdrawals import MAX_ATTEMPTS  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True, help="the queue directory (holds review-ui/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="count what would be selected and write nothing (owner's 乾跑試算)")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap how many to select this round (0 = all)")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                        help="stop selecting a question once this many of its machine repairs were "
                             "rejected and nobody has accepted since (default %d; 0 = no cap). "
                             "業主 2026-09-25：循環三次之後才送入「AI無法判斷」。" % MAX_ATTEMPTS)
    return parser.parse_args()


def queue_root(queue_dir: str) -> str:
    """Where the state lives: the **queue root**, i.e. the parent of `review-ui/`.

    This must not be "whatever `--queue` said". `confirm_dispute.py` resolves the same flag through
    `repair_loop.review_ui_dir` to the subdirectory, so a scan that wrote its state at whatever path
    it was handed would put `scan_state.json` one level away from the reader - and the repair pass
    would report an empty pending list while the scan had just queued 813 questions. Two answers for
    "where is this queue" is the defect, so both go through `review_ui_dir` and the root is its parent.
    """
    resolved = repair_loop.review_ui_dir(os.path.abspath(queue_dir))
    return os.path.dirname(resolved) if os.path.basename(resolved) == "review-ui" else resolved


def review_actions(queue_dir: str) -> dict:
    """每題目前的人為決定，用來當指紋的一部分。

    讀的是審核紀錄的折疊結果，而不是最後一筆事件——`reset_review` 不該讓一題看起來像
    「沒被決定過」（那是 `events.py` 的 `_is_repair_reset` 在處理的事）。這裡直接用它，
    免得兩處對「這題的狀態是什麼」有兩種答案。
    """
    from pathlib import Path
    from scripts.serve_question_review_ui import load_review_events

    path = Path(queue_dir) / "review-ui" / "question_review_events.jsonl"
    if not path.exists():
        return {}
    latest, _counts, reset = load_review_events(path)
    actions = {key: str(event.get("action") or "") for key, event in latest.items()}
    for key, event in reset.items():
        actions.setdefault(key, str(event.get("action") or ""))
    return actions


def candidates_path(queue_dir: str) -> str:
    return os.path.join(queue_dir, "review-ui", "candidates.jsonl")


def main() -> int:
    args = parse_args()
    # `state_dir` is where the fingerprints live (the queue root); `path` is the candidates file
    # (inside `review-ui/`). Both come from the one resolver above.
    state_dir = queue_root(args.queue)
    path = candidates_path(state_dir)
    if not os.path.isfile(path):
        print("找不到 %s——這個佇列還沒建好，掃描不做任何事。" % path, file=sys.stderr)
        return 1

    questions = []
    # **選題的閘門是「人把它擋掉了」**（見檔頭）。讀同一份事件流的折疊，不自己再折一次，
    # 也不看爭議種類：一個被 block、沒有任何種類的題正是要送去看紙本的題。
    blocked = confirm_dispute.blocked_keys(os.path.dirname(path))
    skipped = 0
    for question in repair_loop.load_candidates(path):
        if question.get("candidate_key") not in blocked:
            skipped += 1
            continue
        row = dict(question)
        # 種類只進指紋，不當閘門：同一個 block 上偵測器改變心意仍然是新工作。
        row["kinds"] = sorted(confirm_dispute.dispute_kinds(question))
        questions.append(row)

    state = scan_state.load_state(state_dir)
    actions = review_actions(state_dir)
    # 審題者的註解也是「狀態有沒有變」的一半，所以它必須進指紋：一題如果唯一的改變是審題者新寫了
    # 一句「檢查上下標」，掃描舊版會說「沒變」——那句話就永遠不會被送去模型眼前（而它正是模型
    # 缺的東西）。讀法用 `repair_loop.notes_by_key`，與確認迴圈同一個折疊：一份事件流一個折疊規則。
    notes = repair_loop.notes_by_key(
        os.path.join(state_dir, "review-ui", "question_review_events.jsonl"))
    # 機器改過、被人打回的次數（同一份折疊，第三個維度）。**重複被拒是新的工作**：第二次打回時，
    # 動作、文字、註解都可能與第一次一字不差（站上 62 題正是如此），指紋若不含這個計數就會說
    # 「沒變」。業主的方向：「被重複拒絕的題目要讓它再進入掃描的迴圈，然後再修正」。
    rejections = repair_loop.rejections_by_key(
        os.path.join(state_dir, "review-ui", "question_review_events.jsonl"))
    answers = repair_loop.human_answers_by_key(
        os.path.join(state_dir, "review-ui", discuss.REPAIR_QUESTIONS_STREAM))
    principles = ai_findings.principles_for_prompt(
        discuss.load_events(os.path.join(state_dir, "review-ui", discuss.PRINCIPLES_STREAM)))
    prompt_contexts = {question.get("candidate_key"): ai_findings.figures_note(question)
                       for question in questions if question.get("candidate_key")}
    # **上限**（業主 2026-09-25：循環三次之後才送入「AI無法判斷」）。次數是同一份折疊算的
    # （`rejections`，也就是 `dispute_apply.withdrawals` 的 `ATTEMPT_REJECTING_ACTIONS` 那一條），
    # 上限只在這裡用到：滿了就**不選題**，所以也不會花一次判讀去問（貴的那一步在②）。
    # 不寫指紋（指紋＝「看過且沒變」，寫了等於永久靜默丟掉），人一按接受或放回（`accept`／`unblock`）
    # 次數歸零、指紋跟著變，這一題就自己回到工作清單。
    attempts = {key: int((row or {}).get("count") or 0) for key, row in rejections.items()}
    exhausted_keys = ({key for key, count in attempts.items() if count >= args.max_attempts}
                      if args.max_attempts else set())
    exhausted = [question for question in questions
                 if question.get("candidate_key") in exhausted_keys]
    live = [question for question in questions
            if question.get("candidate_key") not in exhausted_keys]
    changed, updated = scan_state.new_work(
        live, state, actions, notes, rejections, answers, principles, prompt_contexts)

    # ---- the second kind of work: a blocked question with no usable dispute reading ------------
    #
    # 「狀態有變」不是唯一一種工作。**一個站得住的人類 block，而管線還沒有一份它可以據以改題的
    # 紙本判讀，是欠著的工作**——不管狀態有沒有變。實測（常駐機 2026-09-25）：355 題站得住的
    # block 裡，210 題沒有可用的 `dispute` 判讀（166 題只有整庫掃描的 `category-scan`、43 題的
    # 判讀本身失敗、其餘從來沒被判讀過），而套用端**一條都不取**非 `dispute` 的判讀
    # （`apply_dispute_repairs.py` 只吃 `population == "dispute"`，見 `ai_findings.POPULATIONS`）。
    # 症狀正是業主說的那一句：「你明明很多題目都有自己寫應該怎麼改，但為什麼沒有按照你寫的去修改」
    # ——機器寫了，寫在一個套用端明文不採信的人口裡，而且沒有任何人會再問它一次。
    #
    # 這一條用的是**同一個問題、同一個答案**：`confirm_dispute.confirmed_keys` 是②拿來決定
    # 「這一題已經讀過、不用再問」的那個函式。判準是「有沒有可用（有人讀出來、沒有 error）的
    # `dispute` 判讀」，不是「有沒有任何判讀」，所以整庫掃描的紀錄不算數——那正是它不算數的理由。
    # 不進指紋：判讀是**這一輪的產物**，放進指紋會讓處理完的題看起來又變了一次
    # （見 `scan_state.question_fingerprint` 的說明）。這裡每一次掃描都從事實重算，所以它自己會
    # 收斂：②一寫下 dispute 判讀，這一題就離開這份名單。
    store = os.path.join(os.path.dirname(path), ai_findings.STREAM)
    confirmed = confirm_dispute.confirmed_keys(store)
    current_confirmed = {
        question.get("candidate_key") for question in live
        if confirm_dispute.confirmation_matches_prompt(
            question, confirmed.get(question.get("candidate_key")), principles=principles,
            answers=answers.get(question.get("candidate_key")),
            notes=notes.get(question.get("candidate_key")),
            rejected=rejections.get(question.get("candidate_key")))
    }
    owed = [question for question in live
            if question.get("candidate_key") not in current_confirmed]

    # `changed` 在前：它帶著「為什麼它入選」的順序。兩邊重疊的題只算一次。
    queued, seen = [], set()
    for question in list(changed) + owed:
        key = question.get("candidate_key")
        if key and key not in seen:
            seen.add(key)
            queued.append(question)

    previous_pending = scan_state.pending_keys(state_dir)
    pending = [key for key in previous_pending
               if key in blocked and key not in exhausted_keys and key not in current_confirmed]

    # `--limit` caps what this round **does**, and it must not also record the fingerprints of the
    # questions it leaves out. `new_work` has already stored one for every question it selected; a
    # question kept there without entering `pending` is "seen, unchanged" on the next scan, so it is
    # in no pending list and no future selection - it is lost without ever being reported as an error.
    # The cap is a budget for one round, not evidence that the rest were read, so drop those
    # fingerprints and let the next scan select them again.
    selected = queued[:args.limit] if args.limit else queued
    for question in queued[len(selected):]:
        updated.pop(question.get("candidate_key"), None)
    # 欠著的工作也要有自己的指紋，不然下一輪它會以「狀態有變」的身分再印一次（數字會說謊）。
    for question in selected:
        key = question.get("candidate_key")
        if key and key not in updated:
            updated[key] = scan_state.question_fingerprint(
                question, actions.get(key, ""), notes.get(key),
                int((rejections.get(key) or {}).get("count") or 0),
                answers.get(key), principles, prompt_contexts.get(key, ""))

    print("掃描 %s" % state_dir)
    print("  人已 block 的題：%d（略過沒有 block 的 %d 題）" % (len(questions), skipped))
    print("  狀態有變、要處理：%d" % len(changed))
    # 只因為「又被退了一次」而入選的：把指紋裡的計數拿掉之後，狀態與上一輪一模一樣。這是這條規則
    # 存在的證據（它同時是負對照本身），不是推論——見 `scan_state.question_fingerprint`。
    repeat_only = [q for q in changed if state.get(q.get("candidate_key")) ==
                   scan_state.question_fingerprint(
                       q, actions.get(q.get("candidate_key"), ""),
                       notes.get(q.get("candidate_key")), 0,
                       answers.get(q.get("candidate_key")), principles,
                       prompt_contexts.get(q.get("candidate_key"), ""))]
    print("  其中「機器改過、又被打回」而重新入選：%d 題" % len(repeat_only))
    print("  人 block 但沒有可用的 dispute 判讀（欠②一次讀紙本）：%d 題" % len(owed))
    if exhausted:
        print("  已退滿 %d 次、不再讀也不再改（交給人：接受或放回會重新開門）：%d 題"
              % (args.max_attempts, len(exhausted)))
    if len(selected) != len(queued):
        print("  本輪名額 %d 題，其餘 %d 題留給下一輪" % (len(selected), len(queued) - len(selected)))
    print("  上一輪還沒做完：%d" % len(pending))
    print("  已看過且沒變（不重掃）：%d" % (len(questions) - len(changed)))

    if args.dry_run:
        print("  （--dry-run：不寫入狀態）")
        return 0

    # `updated` carries fingerprints only; `pending` is a separate key **in the same file**. Merge
    # before writing, or the 813 questions queued by an earlier round would be dropped by a scan that
    # concluded "nothing changed" — the exact silent loss this design exists to prevent.
    merged = sorted(set(pending) | {q["candidate_key"] for q in selected if q.get("candidate_key")})
    updated["pending"] = merged
    scan_state.save_state(state_dir, updated)
    print("  pending 現在有 %d 題" % len(merged))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
