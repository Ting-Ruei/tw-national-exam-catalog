# -*- coding: utf-8 -*-
"""把逐題註解裡**新的**規則，整理成錯題討論區的「基本原則」。

使用者的規則（原文）：**逐題 comment 自己讀，是原本沒有的規則才加入。**

這支是那個「加入」的寫入半邊。判讀本身（哪一句是新的、為什麼）在
`qbr/reports/comment_to_principles.md`，那份文件是**給人看的**：它逐筆列出讀到的註解、
對照已有的規則，並說明為什麼只有那幾條算新（第一輪 9 筆→1 條，第二輪 79 句→4 條）。
這裡只做四件機械的事：

1. 把判讀結果（下面 `CURATED`）變成基本原則流的 `add` 事件。
2. **去重**：文字已經在作用中的原則裡，就不再加（所以重跑是 0 新增）。
3. **對帳**：每一條新原則都要指得出它是從哪一筆註記事件讀來的，而那個 key 必須真的在事件
   流裡是一筆註記。指不出來源的「原則」就是編的。
4. **撤銷**：`CURATED` 是它自己那組原則的**唯一來源**——把一段文字從表裡改掉之後，舊的那條
   要跟著被 `remove` 掉，否則流裡會同時躺著修正前與修正後兩句話，而提示詞兩句都會讀進去。
   撤銷只動 `source == comment_review` 的（介面上**人自己加**的原則不是策展人的東西）。

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
from qbr.review_ui import events as review_ui_events  # noqa: E402

#: 判讀結果。每一條都要有 `evidence`（讀到它的 candidate_key）與 `rationale`（為什麼它算新的）。
#: 第一輪只有 `q071` 的「表格要用紙本圖」通過——見 `comment_to_principles.md`；
#: 第二輪（2026-09-24，36 筆註記）再加四條，判準一樣是「既有偵測器量不到」。
#: 被排除的仍然不寫進來：上下標被壓平（`flattened-offset`）、字形／異體字（`substituted-ideograph`、
#: `lost-glyph`、`substituted-script`）、血液氣體分壓的數字下標（已量到 119 題帶對的 `<sub>2</sub>`）、
#: 以及一次性事實（「⁻ 0.23t 是上標」「Ae-αt＋Be-βt 並沒有改到」「下標」）——把它們也寫成原則，
#: 只會讓下一輪的提示詞變長而什麼都沒改變。
CURATED = (
    {
        "text": "表格的內容不要靠文字層推論或重排。紙本的表格在文字層只剩一串數字、欄位對不上；"
                "遇到表格時，表格要以紙本圖（截圖／figure）為準，不要用抽取出來的數字把它拼成文字表格。",
        "evidence": ["moex:108030:305:33:1:question:q071"],
        "rationale": "q071 的表格在文字層被壓成一行；reread.SYSTEM 規則 1 仍指示把表格文字"
                     "依序寫入，這是唯一一條沒有被寫下來的替代做法。",
    },
    # 第二輪（2026-09-24）。來源是使用者後來寫的 79 句註記（59 個題號）；判讀在
    # `qbr/reports/comment_to_principles.md` 的第二輪那節。四條的判準都一樣：**既有偵測器量不到**。
    # 這一輪的證據比第一輪硬：這幾題的 `disputes` 是空的（實測見報告），所以不是「已經有了」。
    {
        "text": "上下標的核對要看到字母，不是只看到數字。藥動與腎功能那份既有清單（Vd、KM、HbA1C、"
                "ClCr、Ctrough…）已經有人寫下來了，**清單以外**的字母下標才是沒有人管的："
                "GABA_B、GPIIb/IIIa、H_b/H_c/H_d、sin r_c 這類，在文字層和普通大寫長得一模一樣，"
                "要回紙本確認哪幾個字母是下標，再指出要改的確切位置。",
        "evidence": ["moex:103090:312:11:1:question:q054",
                     "moex:115090:305:0401:1:question:q010",
                     "moex:103090:312:22:1:question:q029",
                     "moex:103090:312:22:1:question:q007"],
        "rationale": "實測這四題的 disputes 都是空的。而且**碼的層次就不可能**：`flattened-offset` "
                     "只承載 `extract._flattened_offset` 的結果，那個判定要求整段含數字、且無法對應的"
                     "字元只能是 `.,/·×÷`——`b`／`c`／`d`／`M`／`B` 這種被字母擋住的 run 明文被排除"
                     "（`extract.py:82-89,108-110`）。VD／KM 不列為新規則：它們已在 "
                     "`subject-overrides.md:36-42` 的成文清單裡。",
    },
    {
        "text": "判斷要看紙本截圖，而且要看對那一題。截圖要涵蓋紙本圖裡被讀成文字的那串字"
                "（例如圖上的藥名標註），並且在說「這一題錯了」之前確認正在看的圖就是這一題的，"
                "不是隔壁題的。",
        "evidence": ["moex:103090:312:33:1:question:q074",
                     "moex:103090:312:33:1:question:q053",
                     "moex:115090:305:0401:1:question:q075",
                     "moex:103090:312:22:1:question:q077"],
        "rationale": "q077 是模型照著別題的圖講這一題（使用者：「圖片試78題的，你截錯了」）；"
                     "q074 是圖上的 `drug-A drug-B` 被讀成文字而截圖沒有涵蓋它；q053／q075 是人"
                     "直接要求「用截圖確認上下標位置」。",
    },
    {
        "text": "題幹的文字跑到選項或答案裡面、而且 ABCD 的順序也跟著不對時，那是版面（圖層）被拆錯，"
                "不是打字打錯；這種情形不要只提議替換某幾個字，要指出整段被搬到哪裡去了。",
        "evidence": ["moex:103090:312:33:1:question:q036",
                     "moex:103090:312:33:1:question:q055"],
        "rationale": "兩題的 disputes 都是空的（`option-shape` 只看選項數、`dangling-answer` 只看"
                     "答案指到的選項在不在，都不看「文字被搬到隔壁欄」）。使用者對這兩題的判斷"
                     "都是「被吃到」加上「可能是圖層問題」。",
    },
    {
        "text": "標點要注意全形與半形。紙本印的是半形（例如 Parkinson's 的那個 '），文字層存成"
                "全形時要指出來——它看起來很像，但在題庫裡是不同的字元。",
        "evidence": ["moex:103090:312:11:1:question:q079"],
        "rationale": "`disputes.KINDS` 有 16 種，最接近的是 `punctuation-only-option`——那是"
                     "「選項只剩標點、文字沒讀到」，不是「標點被讀成另一個標點形」，兩者不同"
                     "（實測 q079 與 q012 這兩個題號的 disputes 都是空的）。全形折半只存在於"
                     "`canon.py` 的 NFKC**比較用**正規化；NFKC 只折相容字元，實測 NFKC(⻑) != 長，"
                     "異體字原樣留著，U+2019 也折不掉——所以它不會把這種差異報出來。",
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
    """每一筆**註記**事件指到的題目。這是「這條原則讀得到來源」的判準。

    用 `review_ui.events._is_note_event`，不自己再判一次。那是「這是對一題的註記，不是對它的
    判決」的**唯一**定義，而兩份實作已經漂移了：註記大多跟著判決一起寫（`note_action: "note"`），
    所以只看 `action == "comment"` 會漏掉大部分。實測（2026-09-24，live 佇列）36 筆註記裡只有
    13 筆是純 comment，另外 23 筆掛在 block 上——只認 comment 的那一版，會把使用者大部分的
    話判成「沒有可對帳的來源」而拒絕。
    """
    return {str(event.get("candidate_key") or "").strip()
            for event in events
            if review_ui_events._is_note_event(event)
            and str(event.get("candidate_key") or "").strip()}


#: 這張表寫進去的原則帶的 `source`。介面上的「新增原則」走 `review_state.append_principle`，
#: 它**不寫** `source`（`reviewer` 預設 `local`）——所以這個欄位剛好把「策展人寫的」與
#: 「人自己寫的」分開，撤銷時只動前者。
CURATED_SOURCE = "comment_review"


def active_curated(events: list[dict], source: str = CURATED_SOURCE) -> list[dict]:
    """現在作用中、而且是**這張表**寫進去的原則。

    回傳事件而不是文字，因為「表裡已經沒有它了」要撤得掉就需要 `principle_id`。
    人手在介面上加的原則不在這裡——那不是策展人可以動的東西。
    """
    return [event for event in discuss.principles_projection(events)["principles"]
            if str(event.get("source") or "") == source]


def uncurated(active: list[dict]) -> list[dict]:
    """作用中、由這張表寫入、但**現在的 `CURATED` 裡已經沒有這段文字**的原則。

    這是「改進要用取代，不是分岔」在流的層次上的版本：`CURATED` 是這組原則的唯一來源，
    所以把一段文字從表裡改掉之後，舊的那條必須被撤掉——否則流裡會同時躺著修正前與修正後的
    兩句話，而提示詞兩句都會讀進去。實測這就是為什麼需要它：修正「字母下標」那條的範圍時，
    舊版把 VD／KM 講成沒人寫過，而它們其實在 `subject-overrides.md` 的成文清單裡。
    """
    texts = {str(item.get("text") or "").strip() for item in CURATED}
    return [event for event in active if str(event.get("text") or "").strip() not in texts]


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
    active = active_curated(discuss.load_events(args.principles))
    known = {str(event.get("text") or "").strip() for event in active}
    retired = uncurated(active)

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

    print("讀到註記事件 %d 筆，涵蓋 %d 個題號" % (
        sum(1 for e in review_events if review_ui_events._is_note_event(e)), len(keys)))
    # **核准的數字要印出來**（2026-09-24）。這一輪寫進去的策展原則在原則區按過「核准」之前
    # **不會**進提示詞（見 `ai_findings.principles_for_prompt`），所以只印「作用中 N 條」會讓
    # 一條剛寫好的原則安靜地從提示詞裡消失——下一個人看到的是模型的輸出，不是這裡的數字。
    principle_events = discuss.load_events(args.principles)
    projection = discuss.principles_projection(principle_events)
    print("作用中的原則：%d 條（已核准 %d／待核准 %d）（其中這張表寫的 %d 條）" % (
        projection["count"], projection["approved_count"], projection["pending_count"],
        len(active)))
    print("判讀候選：%d 條；已存在而略過：%d 條；指不出來源而拒絕：%d 條"
          % (len(CURATED), len(skipped), len(ungrounded)))
    for text, evidence in ungrounded:
        print("  拒絕（沒有可對帳的來源）：%s ← %s" % (text[:40], evidence))
    for item in proposed:
        print("  ＋ %s" % item["text"])
        print("     evidence=%s" % ",".join(item["evidence"]))
    for event in retired:
        print("  － 撤銷 %s（表裡已經沒有這段文字）：%s"
              % (event.get("principle_id"), str(event.get("text") or "")[:48]))

    if not args.apply:
        print("\n（dry-run；沒有寫入。加 --apply 才寫。）")
        return 0

    if retired and not CURATED:
        # 一個空表不該有「撤銷全部」的權力：那讓打錯一個字變成刪掉一整組原則。
        print("拒絕：CURATED 是空的，卻有 %d 條作用中的策展原則——先確認表是不是被改壞了"
              % len(retired), file=sys.stderr)
        return 2

    written = []
    for event in retired:
        written.append(discuss.append_event(args.principles, {
            "schema": discuss.PRINCIPLE_SCHEMA,
            "action": "remove",
            "principle_id": str(event.get("principle_id") or ""),
            "text": str(event.get("text") or ""),
            "scope": "question",
            "reviewer": args.reviewer,
            "source": CURATED_SOURCE,
            "reason": "策展表已改寫：這段文字不再在 CURATED 裡（改進用取代，不是分岔）",
        }))
    for item in proposed:
        event = discuss.append_event(args.principles, {
            "schema": discuss.PRINCIPLE_SCHEMA,
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
        "added": len(proposed),
        "retired": len(retired),
        "skipped_existing": len(skipped),
        "refused_ungrounded": len(ungrounded),
        "principle_ids": [event.get("principle_id") for event in written
                          if str(event.get("action") or "") == "add"],
        "retired_ids": [event.get("principle_id") for event in retired],
    }
    receipt_path = Path(args.principles).with_suffix(".curation-receipt.json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n寫入 %d 條（撤銷 %d 條）；收據：%s" % (len(proposed), len(retired), receipt_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
