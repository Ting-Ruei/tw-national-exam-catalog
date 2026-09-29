# -*- coding: utf-8 -*-
"""把「模型已經答了、解析器卻整筆丟掉」的 finding 讀回來（不重新問模型）。

量到的事實（2026-09-24，`dgx-qwen3.8-flash`）：

* 一輪 166 題裡 **30 筆**被判成 `unparsed`、整筆丟掉；站上整條流最新記錄裡有 **84 題沒有答案，
  其中 83 題讀得回來**（剩下那 1 題的 `raw` 裡根本沒有 JSON）。
* 那 83 筆的 `raw` **開頭都是一個寫好的 JSON**：
  `{"verdict":"DEFECT","what":"GLYPH_DAMAGE","where":"題幹：「抗癲癇藥物」中的「癲癇」二字…`
  然後在某個欄位裡掉進複讀迴圈——「正確詞彙為「抗癲癇」->「抗癲癇」? 錯誤。正確詞彙為…」四千字，
  物件從來沒有收尾。
* 折斷落在哪個欄位（83 筆實測）：`verdict` 與 `what` **83/83 都到齊**；`where` 只有 19 筆活下來，
  `confidence` 一筆都沒有。也就是說救回來的是一句話的**判定與代碼**，位置與修法多半掉了。
* 大部分是 `GLYPH_DAMAGE`：那是「被要求指出兩個**看起來一模一樣**的字差在哪」的那一類
  （部首 vs 漢字、`癲` 的罕見異體）。模型看不出它被要求描述的那個差異，於是原地打轉。

所以：**答案是模型已經寫下來的，只有解析器弄丟了。** 這支把已經躺在 `raw` 裡的答案讀出來，
補成一筆新的記錄——**不重問**。

那重問呢？折斷不是輸入造成的，這點量過了，所以不能拿「重問也沒用」當理由：

    同樣那 6 題（最長、折得最兇的幾筆）、同樣的提示、同樣的 `--max-tokens 4000 --concurrency 4`，
    重問一次 **6/6 都解析成功**（另一組加了「不要反覆查證」那一段也是 6/6）。同一批題目在原本那一輪
    是折斷的，重問卻過了——所以複讀是**取樣意外**，不是那幾題本身的性質。

選這支而不是重問，理由是成本與證據：補一筆花 0 秒引擎時間，而且它用的是**當下那次**已經產生的
原文（重問會蓋掉同一題的另一筆記錄）；重問要排隊、要等、而且可能再折一次。兩者不互斥——
`--restale` 之後仍然可以重問，`is_answer` 會讓有 finding 的題目不再被選中。

（順帶量到一件事：重問那 6 題得到的 `verdict`／`what` 與搶救出來的一致，例如 `q011` 兩次都是
`DEFECT／GLYPH_DAMAGE`、`q078` 兩次都是 `NOT_EXTRACTION／NONE`。所以雖然 `where` 掉了，
搶救出來的**判定本身**是重現得出來的。）

為什麼是「補一筆」而不是「改那一筆」
------------------------------------

`question_ai_findings.jsonl` 是 append-only 的：記錄的價值在於它說的是**當下那次朗讀**的結果，
能被事後改寫的紀錄就不能再被信任。這裡不動舊行，附上一筆同樣 `raw`、同樣 `prompt_version`、
同樣證據的新記錄——而讀取端（`latest_by_question`）本來就是「同一個 key 取最後一筆」，
所以補上的那筆自然生效。`error` 設回 `None`：那筆記錄的 `finding` 現在讀得出來，說它「失敗」
會與同一筆裡的 `finding` 自相矛盾；折斷這件事留在 `finding.truncated` 與原封不動的 `raw` 裡。
`created_at` 是誠實的——它是這筆記錄被寫下的時間。

重跑是 0 筆：`is_answer` 讓已經有 finding 的最新記錄不再被選中。

治理
----

這裡寫的是**模型的**輸出流，不是人的審核流：不碰 `question_review_events.jsonl`、
不產生 `action`、不冒充審核者、也不改任何題目（`GOV-05`）。預設 dry-run，`--apply` 才寫。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import ai_findings  # noqa: E402


def recoverable(latest: dict) -> list:
    """每個 key 的最新記錄裡，答案讀得回來的那一些。

    「讀得回來」用 `parse_finding` 定義，不是這裡自己再寫一套：正規化（代碼對照、`what_reported`、
    `DEFECT`＋`NONE` 的矛盾）只有那一份，補進流裡的 finding 就必須與其他消費者看到的一模一樣。
    """
    out = []
    for key in sorted(latest):
        record = latest[key]
        if ai_findings.is_answer(record):
            continue        # 已經有答案，不管是這一輪答的還是上一輪補的
        raw = record.get("raw") or ""
        if not raw:
            continue        # 沒有原文就沒有可讀的東西——那不是解析問題，是呼叫沒回來
        finding = ai_findings.parse_finding(raw)
        if finding is None or not ai_findings.is_answer({"finding": finding}):
            continue
        out.append((record, finding))
    return out


def recovered_record(record: dict, finding: dict) -> dict:
    """同一筆記錄，換上讀得回來的 finding。

    除了這三個欄位，其餘（`raw`、`prompt_system`、`prompt_user`、`evidence`、`principles`、
    `prompt_version`、`crop`、`usage`、`seconds`）逐欄照抄：證明這筆記錄講的是同一次呼叫。
    """
    fresh = dict(record)
    fresh["finding"] = finding
    fresh["error"] = None
    fresh["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return fresh


def _line(record: dict, finding: dict) -> str:
    mark = "（折斷）" if finding.get("truncated") else ""
    where = (finding.get("where") or "").replace("\n", " ")[:48]
    return "  %-46s %-14s %-22s%s  %s" % (
        record.get("candidate_key"), finding.get("verdict"), finding.get("what"), mark, where)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", help="佇列根目錄（含 review-ui/），或直接給 review-ui/")
    parser.add_argument("--findings", help="直接指定 question_ai_findings.jsonl")
    parser.add_argument("--limit", type=int, default=0, help="這一輪最多補幾筆（0＝全部）")
    parser.add_argument("--apply", action="store_true", help="真的寫；沒有它就是 dry-run")
    args = parser.parse_args()

    if args.findings:
        path = args.findings
    elif args.queue:
        path = ai_findings.store_path(args.queue)
    else:
        parser.error("要給 --queue 或 --findings")
    if not os.path.exists(path):
        print("找不到 findings 流：%s" % path)
        return 2

    latest = ai_findings.latest_by_question(path)
    picked = recoverable(latest)
    print("讀 %s" % path)
    print("最新記錄 %d 題；其中沒有答案 %d 題，答案讀得回來 %d 題"
          % (len(latest), sum(1 for r in latest.values() if not ai_findings.is_answer(r)), len(picked)))
    if not picked:
        print("\n沒有要補的（重跑是 0 筆：已經有答案的記錄不會再被選中）")
        return 0

    selected = picked[:args.limit] if args.limit else picked
    broken = sum(1 for _, f in selected if f.get("truncated"))
    print("本輪 %d 筆（其中折斷後搶救 %d 筆、其餘是完整物件）\n" % (len(selected), broken))
    for record, finding in selected:
        print(_line(record, finding))

    if not args.apply:
        print("\n（dry-run；沒有寫入。加 --apply 才寫。）")
        return 0
    for record, finding in selected:
        ai_findings.append(path, recovered_record(record, finding))
    print("\n已補上 %d 筆到 %s" % (len(selected), path))
    print("這些題不再是 stale（`is_answer` 看的就是 `finding`），所以 --restale 不會再問它們一次。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
