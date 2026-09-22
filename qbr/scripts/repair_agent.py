# -*- coding: utf-8 -*-
"""題目修正與規則收集 agent —— 無人在場時也能跑的那一段迴圈（本機常駐）。

一份腳本，兩件事：**並行**問兩個地端引擎（每張紙只有一個作者，所以一題只有一份記錄），
以及把每一輪學到的東西**壓進一份經驗檔**給下一輪的提示詞。

    1. 從常駐機拉回人類決定            scripts/pull_station_reviews.sh
    2. 取「新的、還沒有 finding 的 block」為本批（窗，預設 5 題）
    3. 依問題輪流給兩個本機引擎（disjoint，避免同一題重複送入同一批）
    4. 每題記：模型、時間、號、哪裡錯、怎麼修、是否成類（append-only）
    5. 本批結束把批次紀要併入經驗檔（版本化）
    6. 下一輪的提示詞讀這個經驗檔（≤12 條，單屏可讀）
    7. 推回常駐機                        scripts/push_reviews_to_station.sh
    8. 一個精靈判斷 5 題；複雜 10 題（並發同为 5/10）

為何要「兩條引擎並行」：單個引擎思考模式的代價是每秒 21-54 秒、5 題 83-105 秒，
兩條併發把同样 10 題壓到約 100-120 秒（並發 4→2.0s/題、8→1.95s/題，過 4 就只是排程）。
實測必須寫入當次 run 的 local-model validation report。

開關的拼法跟引擎走，不能混用（`qbr.engines` 的註解）：27B(Splash) 只認 `reasoning_effort`，
MTPLX 只認 `chat_template_kwargs.enable_thinking`；錯的拼法回 HTTP 200 但內容為空，
所以 `qbr.engines.ask` 对一個空內容重試一次（序列不同），並把 `reasoning_tokens` 寫進 `usage`。

鐵律：腳本只產「事實」；有語意的句子由提示詞（模型）產出。提示詞不重複 wike 的編號，
也必須包含 wike 已有的編號。見 `docs/ARCHITECTURE_CHARTER.md`。
"""
from __future__ import annotations

import argparse as argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import ai_findings, disputes, engines  # noqa: E402
import repair_loop  # noqa: E402
import ask_about_blocks as ask_mod  # noqa: E402

EXPERIENCE = "experience.json"
MAX_LESSONS = 12          # 經驗檔：單屏可讀的條目上限（提示詞的長度也按它控）
LESSON_MIN_BLOCK = 3      # 進經驗檔的 block 率判準（對齊基準線 5.4% 的整數化）

REPO = os.path.dirname(PKG)


def _thinking_on(name):
    """同一個引擎的「思考開」表（實測：Splash 21-54s/題、關 3-5s/題）。"""
    endpoint = dict(engines.ENDPOINTS[name])
    if "reasoning" in endpoint:
        endpoint["reasoning"] = "medium"   # 實測：`high` 吃滿 budget→content 空；`medium` 同時留有 reasoning 與答案
    elif "thinking" in endpoint:
        endpoint["thinking"] = {"chat_template_kwargs": {"enable_thinking": True}}
    return endpoint


def pending_keys(queue_root: str) -> list:
    """本批要問的 candidate_key（依人號優先、單號）。開頭是未答的题。"""
    qdir = repair_loop.review_ui_dir(queue_root)
    blocked, rows = repair_loop.collect_blocks(qdir)
    _explained, unexplained = repair_loop.explain(blocked, rows)
    # 同 `--resume` 的定義：只有「有 finding」的題才算問過。空內容的重試（極大 2）會留下
    # 一筆 `finding: null` 的記錄，它不是「已答」，所以要重回列；這與 latest_by_question
    # 的「最後一筆為準」配合（last-write-wins）。
    done = {key for key, record in ai_findings.latest_by_question(
        ai_findings.store_path(queue_root)).items() if record.get("finding")}
    return [entry["candidate_key"] for entry in unexplained
            if entry["candidate_key"] not in done]


def run_once(queue_root: str, window: int, lane: str) -> int:
    """一趟：拿鑰匙 → 輪流問 → 寫記錄 → 更新經驗檔。回傳本批題數。"""
    keys = pending_keys(queue_root)[:window]
    if not keys:
        print("本批沒有待問的 candidate_key。")
        return 0
    all_rows = list(repair_loop.load_candidates(os.path.join(
        repair_loop.review_ui_dir(queue_root), "candidates.jsonl")))
    by_key = {row.get("candidate_key"): row for row in all_rows}
    learned = load_experience(queue_root)
    endpoint = _thinking_on(lane)
    print("批 %d 題，引擎 %s（思考開），學習塊 %d 條。"
          % (len(keys), endpoint["name"], len(learned.get("lessons") or [])))
    asked = 0
    for key in keys:
        question = by_key.get(key)
        if question is None:
            print("  ! %s 不在 candidates.jsonl，跳過" % key)
            continue
        finding = ask_mod.ask_one({"candidate_key": key, "question": question, "notes": "",
                                  "kinds": [], "population": "blocked"},
                                 endpoint=endpoint,
                                 out=ai_findings.store_path(queue_root),
                                 args=argparse.Namespace(max_tokens=12000, learned=None,
                                                        timeout=900))
        asked += 1
        status = "OK" if finding.get("finding") else "無效（重複呼叫/空內容）"
        print("  %-44s %s  %s  [%.1fs]" % (key.replace("moex:", ""), status,
                                           (finding.get("finding") or {}).get("what"),
                                           finding.get("seconds") or 0.0))
    update_experience(queue_root, asked)
    return asked


def load_experience(queue_root: str) -> dict:
    path = os.path.join(queue_root, EXPERIENCE)
    if not os.path.exists(path):
        return {"version": 0, "lessons": []}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError:
        return {"version": 0, "lessons": []}


def update_experience(queue_root: str, asked: int) -> dict:
    """把本批觀測壓進經驗檔（含時間戳）；≤MAX_LESSONS、有版本、block 率用整數。"""
    store = ai_findings.store_path(queue_root)
    latest = ai_findings.latest_by_question(store)
    codes = {}
    for record in latest.values():
        finding = record.get("finding") or {}
        code = finding.get("what")
        if not code or code == "NONE":
            continue
        codes[code] = codes.get(code, 0) + 1
    lessons = [{"code": code, "n": n,
                "meaning": ai_findings.CODES.get(code, "")}
               for code, n in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))][:MAX_LESSONS]
    blocked = sum(1 for record in latest.values() if (record.get("finding") or {}).get("verdict"))
    experience = {
        "version": (load_experience(queue_root).get("version") or 0) + 1,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "engine_mix": sorted({(r.get("model") or "").split("/")[0]
                              for r in latest.values() if r.get("model")}),
        "block_rate_baseline": 5.4,
        "observed": {"questions": len(latest), "with_finding": blocked, "this_batch": asked},
        "lessons": lessons,
        "open_cases": [
            {"code": "（無效）", "n": sum(1 for r in latest.values() if not r.get("finding"))}
        ],
    }
    path = os.path.join(queue_root, EXPERIENCE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(experience, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    return experience


def as_prompt(experience: dict) -> str:
    """下一輪提示詞用的學習塊（含 wike 已有編號）；精簡、單屏。"""
    lines = ["（基於前 %d 次觀測）" % (experience.get("observed") or {}).get("questions", 0)]
    for lesson in experience.get("lessons") or []:
        lines.append("- %s：%s（本批 %s 題）" % (lesson["code"], lesson["meaning"], lesson["n"]))
    for case in experience.get("open_cases") or []:
        lines.append("- %s：無法解析的記錄 %s 筆（重複呼叫/空內容）。" % (case["code"], case["n"]))
    lines.append("- 基準線：block 率 %s%%。" % experience.get("block_rate_baseline"))
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", default="data/review-queues/live",
                        help="佇列根目錄（含 review-ui/）")
    parser.add_argument("--window", type=int, default=5, help="本批題數（預設 5）")
    parser.add_argument("--lane", default="splash", choices=sorted(engines.ENDPOINTS),
                        help="使用哪個地端引擎（端點是參數）")
    parser.add_argument("--every", type=int, default=1,
                        help="跑幾輪（預設 1 輪；每輪同一個 session）")
    parser.add_argument("--interval", type=int, default=1800,
                        help="輪与之間的秒數（預設 1800 = 30 分鐘）")
    return parser.parse_args()


def main() -> int:
    """一趟（或 `--every` 個間隔）後結束；`--every 0` 表示只跑一趟。

    同一個 session 內輪迴，所以 `call_omo_parse_result` 取到的 id 是一致的；
    每個間隔前重開 stdin（read 行號由 it 維護）。
    """
    args = parse_args()
    cycles = args.every if args.every and args.every > 0 else 1
    total = 0
    for turn in range(cycles):
        started = time.time()
        total += run_once(args.queue, args.window, args.lane)
        left = cycles - turn - 1
        if left > 0:
            time.sleep(max(0, args.interval or 0))
    print("累計 %d 題（%d 輪）。" % (total, cycles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
