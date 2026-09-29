# -*- coding: utf-8 -*-
"""紙本上的斜體字，有沒有被收錄的文字標起來（唯讀，不寫任何東西）。

業主 2026-09-25：「管線好像開始抓『斜體字』……這部分可以加入，而且甚至檢查範圍要覆蓋到以前審過的
所有題目。」這一支就是那個檢查的**量測**那一半：斜體是紙本的性質（字型），所以它不必問模型——
判讀讀出的是**字**，字型由腳本量。

    python scripts/scan_italic_marks.py --queue <queue-root>              # 只印現況
    python scripts/scan_italic_marks.py --queue <queue-root> --json out.json
    python scripts/scan_italic_marks.py --queue <queue-root> --only q007

量到的三種狀態，逐個斜體片段：

* `marked` —— 收錄的文字把這一段包在 `<i>…</i>` 裡（平台認得這個標記，`canon` 的標記表有它）。
* `unmarked` —— 紙本斜體、收錄的文字是平的。**這是待辦**：字元沒有錯，少的只是「紙本這裡是斜體」
  這件事。
* `missing` —— 斜體片段在收錄的文字裡找不到（抽取器把它拆了或改了字）。這一種**不推論**：找不到
  就不能說它該被包起來，所以它自己一格，不混進待辦。

站上實測（2026-09-25，唯讀）：全佇列 79,090 列的收錄文字**一個 `<i>` 都沒有**，而那一本微生物學
紙本自己就有 157 個斜體片段（8 頁、1,216 個 span），字型是 `Helvetica-Oblique`，內容是學名
（`Streptococcus pyogenes`、`Bacteroides`…）。判讀端已經自己開始寫 `<i>`（`moex:105100:308:44:1:
question:q007` 的判讀記錄裡就有），所以這一支量的是**兩邊對不對得起來**，不是誰先想到。
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
sys.path.insert(0, HERE)

from qbr import extract, repair, reread  # noqa: E402

import confirm_dispute  # noqa: E402
from crop_run_figures import band_extents  # noqa: E402

#: `<i>…</i>`（平台的斜體標記）。`canon` 的標記表認得它，所以收錄的文字帶著它不算壞掉。
ITALIC_TAG = re.compile(r"<i\s*>(.*?)</i\s*>", re.IGNORECASE | re.DOTALL)
ANY_MARKUP = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
#: 一對標記**裡面的內容**（`<i>K'</i>` 的那個 `K'`、`<sub>2</sub>` 的那個 `2`）：插新標記時整段
#: 都要讓開，不是只讓開 `<i>` 這兩個字。
MARKED_CONTENT = re.compile(r"<\s*([a-zA-Z][^>]*)>[^<>]*<\s*/", re.IGNORECASE)


def _flat(text: str) -> str:
    """比對用的形狀：只把空白收乾淨，**不拿掉標記**（標記就是要檢查的東西）。"""
    return re.sub(r"[\s\u3000\u00a0]+", " ", str(text or "")).strip()


def mark_state(field_text: str, run: str) -> str:
    """這一段斜體字在這一份欄位文字裡的狀態：`marked`／`unmarked`／`missing`。

    只認「這一段字**整段**在 `<i>` 裡面」：紙本斜體的是整個學名，包住半段不是同一個主張。
    找不到就是 `missing`——抽取器可能把它拆行、改了字或拼法不同，那一種不由這一支判定。
    """
    run = _flat(run)
    if not run:
        return "missing"
    field = _flat(field_text)
    marked_spans = [_flat(match.group(1)) for match in ITALIC_TAG.finditer(field)]
    if run in marked_spans:
        return "marked"
    if run in ANY_MARKUP.sub("", field):
        return "unmarked"
    return "missing"


def italic_runs_in_band(spans_by_page: dict, band: dict) -> list:
    """這一題自己的列裡有哪些斜體片段（`{page: [span, …]}` 與 `{page: (y0, y1)}`）。

    片段與列只要有垂直重疊就算這一題的：列是**文字行**的範圍，一個斜體片段的高度可能比行高一點
    （`Helvetica-Oblique` 的字面），要求它整段落在列裡會漏掉跨行的學名。
    """
    out = []
    for page, spans in (spans_by_page or {}).items():
        extent = (band or {}).get(int(page))
        if not extent:
            continue
        low, high = float(extent[0]), float(extent[1])
        for span in spans:
            box = span.get("bbox") or (0, 0, 0, 0)
            top, bottom = float(box[1]), float(box[3])
            if bottom <= low or top >= high:
                continue
            text = _flat(span.get("text"))
            if text:
                out.append(text)
    return out


def apply_runs(field_text: str, runs: list) -> tuple:
    """把這些斜體片段在這一欄標成 `<i>…</i>`：回 `(new_text, applied, left)`。

    兩個閘門，理由是量到的，不是風格：

    * **這一段字在這一欄只出現一次**才動手。`mark_state` 是子字串比對，而 `e`／`s`／`K`、`K'`
      這種片段在題幹裡出現好幾次（站上 1,434 題的樣本裡，一個字的片段有 479 個）——插在哪一個
      上面是猜的，猜錯就是把別的字變成斜體。這一種列進 `left`，留給人。
    * 已經被某一段 `<i>…</i>` 蓋住的字不再包一層：`in situ` 已經標好時，片段 `in` 不該在裡面
      再插一個 `<i>`（那會變成 `<i><i>in</i> situ</i>`）。

    先長後短：`K'` 要在 `K` 之前處理，否則 `K` 會先被包起來、`K'` 就找不到乾淨的字串了。
    """
    text = str(field_text or "")
    applied, left = [], []
    ordered = sorted([str(run or "") for run in runs], key=lambda run: -len(_flat(run)))
    for run in ordered:
        needle = _flat(run)
        if not needle:
            continue
        if mark_state(text, run) == "marked":
            continue
        spots = bare_occurrences(text, needle)
        if len(spots) != 1:
            # 0 個＝已經被某一段 `<i>…</i>` 蓋住（或紙本有、收錄文字沒有）；2 個以上＝猜不出位置。
            left.append(needle)
            continue
        start, end = spots[0]
        text = text[:start] + "<i>" + text[start:end] + "</i>" + text[end:]
        applied.append(needle)
    return text, applied, left


def bare_occurrences(text: str, needle: str) -> list:
    """`needle` 在這一欄裡**沒有被任何標記蓋住**的出現位置：`[(start, end), …]`。

    用位置而不是 `str.count`：標好的那一段（`<i>K'</i>`）裡面也有一個 `K`，只數字面的話第二段
    片段就永遠找不到「乾淨的那一個」——而它其實就在標記外面。標記本身（`<sub>`／`<i>`）的區間先
    標掉，落在裡面的出現不算。
    """
    blocked = [match.span() for match in ANY_MARKUP.finditer(text)]
    blocked += [match.span() for match in MARKED_CONTENT.finditer(text)]
    out = []
    start = text.find(needle)
    while start != -1:
        end = start + len(needle)
        if not any(start < stop and begin < end for begin, stop in blocked):
            out.append((start, end))
        start = text.find(needle, start + 1)
    return out


def place_in_owner(owner_text: str, line: str, offset, run: str):
    """那一段字在 owner 欄裡的**確切位置**，或 `None`（不猜）。

    只用「出現幾次」（`bare_occurrences`）時，同一段字在 owner 欄出現兩次以上就放不回去——而那正是
    站上那 53 個片段的形狀（`owner` 指到 `stem` 的有 52 個），於是標記被拿掉而沒有放回任何地方。
    紙本自己有位置：片段坐在**哪一列**上（`line`）與它在那一列裡的位移（`offset`）都是量到的，
    兩者相加就是唯一的位置。找不到那一列、那一列在 owner 欄出現不只一次、或算出來的字對不上 ⇒ `None`。
    """
    if not line or offset is None or int(offset) < 0:
        return None
    start = owner_text.find(line)
    if start < 0 or owner_text.find(line, start + 1) >= 0:
        return None
    at = start + int(offset)
    return at if owner_text[at:at + len(run)] == run else None


def set_field(row: dict, field: str, text: str) -> bool:
    """把一欄寫回這一列（欄位名與事件流一致：`stem`、`option A`…）。"""
    if field == "stem":
        row["stem"] = text
        return True
    if field.startswith("option "):
        wanted = field.split(" ", 1)[1]
        for option in row.get("options") or []:
            if isinstance(option, dict) and str(option.get("key") or "") == wanted:
                option["text"] = text
                return True
    return False


def spans_in_band(spans_by_page: dict, band: dict) -> list:
    """這一題自己的列裡的斜體片段（帶位置）：`[(page, bbox, text)]`。"""
    out = []
    for page, spans in (spans_by_page or {}).items():
        extent = (band or {}).get(int(page))
        if not extent:
            continue
        low, high = float(extent[0]), float(extent[1])
        for span in spans:
            box = span.get("bbox") or (0, 0, 0, 0)
            top, bottom = float(box[1]), float(box[3])
            if bottom <= low or top >= high:
                continue
            text = _flat(span.get("text"))
            if text:
                out.append((int(page), [float(value) for value in box], text))
    return out


def line_of_span(rows: list, page: int, box: list) -> str:
    """這個片段坐在紙本的哪一列上（`reread.band_rows` 給的列）：回那一列的文字。

    這是**歸屬**那一半的證據，而不是印象：列的 y 範圍與片段重疊 ⇒ 那一列就是它所在的紙本列。
    """
    top, bottom = float(box[1]), float(box[3])
    best = None
    for row in rows or ():
        if int(row.get("page") or 0) != int(page):
            continue
        low, high = float(row.get("y0") or 0.0), float(row.get("y1") or 0.0)
        if bottom <= low or top >= high:
            continue
        if best is None or (high - low) < (float(best.get("y1")) - float(best.get("y0"))):
            best = row
    return _flat((best or {}).get("text"))


def _chunk(text: str, size: int = 12) -> str:
    """一句話裡可以用來認人的那一小段（前 `size` 個非空白字元）。"""
    packed = _flat(text).replace(" ", "")
    return packed[:size]


def line_owner(line_text: str, fields: dict, chosen: str) -> tuple:
    """這一列的文字落在哪一欄 → `(verdict, owner)`。

    `line-matches-the-field`：那一列的辨識片段就在我寫的那一欄裡（歸屬對）。
    `line-belongs-to-another-field`：**只在別欄**找得到 ⇒ 我把標記寫錯欄了。
    `line-text-not-in-any-field`：抽取文字與紙本列對不上（這一種不推論）。
    """
    chunk = _chunk(line_text)
    if not chunk:
        return "line-not-measured", None
    in_chosen = chunk in _flat(fields.get(chosen) or "").replace(" ", "")
    if in_chosen:
        return "line-matches-the-field", chosen
    for name, text in fields.items():
        if name == chosen:
            continue
        if chunk in _flat(text).replace(" ", ""):
            return "line-belongs-to-another-field", name
    return "line-text-not-in-any-field", None


def fields_of(row: dict) -> dict:
    """一列的題幹與選項（`{field: text}`，欄位名與事件流用的一致：`stem`、`option A`…）。"""
    out = {"stem": str(row.get("stem") or "")}
    for option in row.get("options") or []:
        if isinstance(option, dict):
            out["option %s" % (option.get("key") or "")] = str(option.get("text") or "")
    return out


def queue_root_of(queue: str) -> str:
    """`--queue` 給的是佇列根或 `review-ui` 目錄，兩種都接受（與其他腳本一致）。"""
    return queue if os.path.basename(queue.rstrip("/")) == "review-ui" else os.path.join(queue, "review-ui")


def field_state(fields: dict, run: str) -> tuple:
    """這一段斜體落在哪一欄、那一欄是什麼狀態 → `(state, field)`。

    逐欄問（`mark_state`），不是把所有欄位併成一份文字再問：一列的題幹與 A–D 是**四個不同的地方**，
    而套用那一半（`apply_experience_repairs`）是按欄換字的——它需要知道是哪一欄，不然同一段字出現在
    兩欄時就分不出該動哪一個。優先序：`marked` > `unmarked` > `missing`（已經標好的那一次算數，
    同一段字在別欄是平的只是還沒跟上）。
    """
    for state in ("marked", "unmarked"):
        for name, text in fields.items():
            if mark_state(text, run) == state:
                return state, name
    return "missing", None


def scan(args) -> int:
    directory = queue_root_of(args.queue)
    with open(os.path.join(directory, "candidates.jsonl"), encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    stats = collections.Counter()
    examples = collections.defaultdict(list)
    spans_by_pdf, bands_by_pdf = {}, {}
    detail = open(args.detail, "w", encoding="utf-8") if args.detail else None
    pending, left_overs = [], []
    verified, wrong, reverts = [], [], []
    scanned = 0
    started = time.time()
    for index, row in enumerate(rows, 1):
        if args.only and args.only not in json.dumps(row, ensure_ascii=False):
            continue
        if args.limit and scanned >= args.limit:
            break
        number = row.get("question_number") or row.get("number")
        pdf = confirm_dispute.paper_pdf_of(row)
        if not pdf:
            stats["no-paper"] += 1
            continue
        if pdf not in spans_by_pdf:
            spans_by_pdf[pdf] = extract.italic_spans_a(pdf)
            bands_by_pdf[pdf], _ = repair.mask_chrome(extract.extract_cells_a(pdf))
        band = band_extents(bands_by_pdf[pdf], number)
        if not band:
            stats["no-band"] += 1
            continue
        runs = italic_runs_in_band(spans_by_pdf[pdf], band)
        if not runs:
            stats["no-italic"] += 1
            continue
        scanned += 1
        stats["questions-with-italic"] += 1
        fields = fields_of(row)
        if args.verify:
            # 業主 2026-09-25：「斜體有些是誤植，你怎麼解決。」歸屬那一半用**紙本自己的列**對：
            # 這一段字坐在哪一列上，那一列的文字只在**哪一欄**裡 ⇒ 標記該寫在那一欄。
            # 兩半分工：字型說「哪幾個字是斜體」（`italic_spans_a`），列的文字說「它屬於哪一欄」。
            tags = [(name, text, match) for name, text in fields.items()
                    for match in ITALIC_TAG.finditer(text)]
            if not tags:
                continue
            rows_here = reread.band_rows(bands_by_pdf[pdf], number)
            spans = spans_in_band(spans_by_pdf[pdf], band)
            verified.append({"candidate_key": row.get("candidate_key"),
                             "question_number": number, "marks": []})
            for name, text, match in tags:
                run = _flat(match.group(1))
                box = next((item for item in spans if item[2] == run), None)
                if not box:
                    verdict, owner = "no-span-with-that-text", None
                else:
                    line = line_of_span(rows_here, box[0], box[1])
                    verdict, owner = line_owner(line, fields, name)
                verified[-1]["marks"].append({"field": name, "text": run, "verdict": verdict,
                                              "owner": owner})
                stats["verify-%s" % verdict] += 1
                if "belongs-to-another" in verdict:
                    # 那一列裡出現**不只一次**就不記位置：記了也只是取第一個，那是猜。
                    wrong.append({"row": row, "field": name, "run": run, "owner": owner,
                                  "verdict": verdict, "line": line,
                                  "offset": line.find(run) if line.count(run) == 1 else None})
        if args.apply:
            # 業主 2026-09-25：「斜體可以套用**全部**的題目。」範圍是整份候選檔（這一支本來就走
            # 每一列），方法是：紙本量到的字型告訴我們哪幾個字是斜體，哪一欄由判讀／收錄文字對出
            # 來，插入 `<i>` 之前每一段都要在這一份欄位裡**只出現一次**（做不到的列進 left）。
            by_field = collections.defaultdict(list)
            for run in runs:
                state, field = field_state(fields, run)
                if state == "unmarked" and field:
                    by_field[field].append(run)
            for field, runs_here in by_field.items():
                new_text, applied, left = apply_runs(fields[field], runs_here)
                if applied and set_field(row, field, new_text):
                    stats["apply-fields"] += 1
                    stats["apply-runs"] += len(applied)
                    pending.append({"row": row, "field": field, "before": fields[field],
                                    "after": new_text, "runs": applied})
                if left:
                    stats["apply-left"] += len(left)
                    left_overs.append((row.get("question_number"), field, left))
        marks = []
        for run in runs:
            state, field = field_state(fields, run)
            marks.append({"text": run, "state": state, "field": field})
            stats["spans"] += 1
            if state == "marked":
                stats["marked"] += 1
            elif state == "unmarked":
                stats["unmarked"] += 1
                if len(examples["unmarked"]) < 20:
                    examples["unmarked"].append((row.get("question_number"), run))
            else:
                stats["missing"] += 1
                if len(examples["missing"]) < 10:
                    examples["missing"].append((row.get("question_number"), run))
        if any(ITALIC_TAG.search(text) for text in fields.values()):
            stats["rows-carrying-i-tag"] += 1
        if detail is not None:
            # 逐列寫，不要等最後一次 dump：這一支要跑十幾分鐘，中途就能看出進度與分布；而且
            # 下游要的是「哪些題、哪一欄、幾個字」，不是二十個樣本。
            detail.write(json.dumps({
                "candidate_key": row.get("candidate_key"),
                "question_number": row.get("question_number"),
                "paper": os.path.basename(pdf), "band": band,
                "runs": marks,
                "unmarked": sum(1 for m in marks if m["state"] == "unmarked"),
                "missing": sum(1 for m in marks if m["state"] == "missing"),
                "longest_unmarked": max([len(m["text"]) for m in marks if m["state"] == "unmarked"] or [0]),
                "fields": sorted(fields),
            }, ensure_ascii=False) + "\n")
            if scanned % 500 == 0:
                detail.flush()
        # 這一支在整份候選檔上要跑十幾分鐘，而它只在最後才印——不印進度就沒有辦法分辨「還在算」與
        # 「卡住了」（站上第一次普查就是這樣，只能看 CPU 時間猜）。
        if index % 5000 == 0:
            print("  …已掃 %d／%d 列（有斜體 %d 題、片段 %d）"
                  % (index, len(rows), stats["questions-with-italic"], stats["spans"]), flush=True)
    print("掃過 %d 列（每一列都看，不分已審／未審）" % len(rows))
    for name in ("no-paper", "no-band", "no-italic"):
        print("  %-22s %d" % (name, stats[name]))
    print("有斜體的題            %d" % stats["questions-with-italic"])
    print("斜體片段              %d" % stats["spans"])
    print("  已標 <i>            %d" % stats["marked"])
    print("  未標（待辦）        %d" % stats["unmarked"])
    print("  對不上（不推論）    %d" % stats["missing"])
    print("收錄文字已經帶 <i> 的列 %d" % stats["rows-carrying-i-tag"])
    print("共 %.1f 分鐘（%.0f 毫秒／列）" % ((time.time() - started) / 60.0,
                                           (time.time() - started) * 1000.0 / max(1, len(rows))))
    for name in ("unmarked", "missing"):
        if examples[name]:
            print("  %s 樣本：" % name)
            for number, run in examples[name]:
                print("    q%-5s %s" % (number, run[:60]))
    if detail is not None:
        detail.close()
        print("逐列明細寫到 %s" % args.detail)
    if args.verify:
        print("檢查已套用的 <i>：%d 個片段" % sum(stats[key] for key in stats if key.startswith("verify-")))
        for name in sorted(key for key in stats if key.startswith("verify-")):
            print("  %-40s %d" % (name.replace("verify-", ""), stats[name]))
        if args.verify != "-":
            with open(args.verify, "w", encoding="utf-8") as handle:
                for item in verified:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            print("  逐題結果寫到 %s" % args.verify)
        if args.revert and wrong:
            for item in wrong:
                row, name, run = item["row"], item["field"], item["run"]
                fields = fields_of(row)
                text = fields.get(name) or ""
                if ("<i>%s</i>" % run) not in text:
                    continue
                owner = item["owner"]
                fixed = text.replace("<i>%s</i>" % run, run, 1)
                moved = False
                if owner and owner in fields:
                    owner_text = fields.get(owner) or ""
                    # 已經帶著這個標記的欄不要再包一層（`<i><i>X</i></i>`）：第二趟跑同一條規則時
                    # 那一段的裸字還在，位置也還算得出來，於是同一段字會被包第二次。
                    if ("<i>%s</i>" % run) not in owner_text:
                        at = place_in_owner(owner_text, item.get("line"), item.get("offset"), run)
                        if at is None:
                            # 沒有紙本位置的舊紀錄（`line`／`offset`）才退回「只出現一次」那一條。
                            spots = bare_occurrences(owner_text, run)
                            at = spots[0][0] if len(spots) == 1 else None
                        if at is not None:
                            moved = set_field(row, owner, owner_text[:at] + "<i>" + run + "</i>"
                                              + owner_text[at + len(run):])
                if not moved:
                    # **放不回去就不要拿掉。**拿掉而沒有目的地，等於弄丟一個紙本真的有的斜體：站上實測
                    # 2026-09-25 21:50（`--revert` 第一次跑），53 個片段的 `owner` 都指到別欄，而 53 個
                    # **全部只被拿掉、0 個被放回**（owner 欄裡那一段字出現不只一次 ⇒ `spots != 1` ⇒
                    # `moved` 永遠 False）——摘要卻印「歸屬錯的已修：53」。有疑慮的那一段要留給人看
                    # （`verify-line-belongs-to-another-field` 就在 `--verify` 的逐題明細裡），不是刪掉。
                    stats["revert-skipped"] += 1
                    continue
                if set_field(row, name, fixed):
                    stats["recover-fields"] += 1
                    reverts.append({"row": row, "field": name, "run": run, "owner": owner,
                                    "moved": moved})
                    pending.append({"row": row, "field": name, "before": text, "after": fixed,
                                    "runs": [run],
                                    "why": "紙本那一列的文字告訴我們這一段斜體屬於哪一欄（歸屬檢查）"})
                    if moved:
                        pending.append({"row": row, "field": owner,
                                        "before": fields.get(owner) or "",
                                        "after": fields_of(row).get(owner) or "",
                                        "runs": [run],
                                        "why": "紙本那一列的文字告訴我們這一段斜體屬於哪一欄（歸屬檢查）"})
            print("  歸屬錯的已修：%d 個片段（移到紙本那一列所在的欄：%d）"
                  % (stats["recover-fields"], sum(1 for item in reverts if item["moved"])))
            print("  放不回去所以沒動它（留在 --verify 的明細裡給人看）：%d 個片段"
                  % stats["revert-skipped"])
    if pending and not args.apply:
        print("依歸屬檢查改標記：%d 欄" % len(pending))
    if pending:
        print("套用 <i>：%d 欄／%d 片段；做不到（這一欄裡出現不只一次）%d 片段"
              % (stats["apply-fields"], stats["apply-runs"], stats["apply-left"]))
        if left_overs:
            print("  留給人（前 12 題）：")
            for number, field, left in left_overs[:12]:
                print("    q%-5s %s %s" % (number, field, "、".join(left[:6])))
        if pending:
            stamp = time.strftime("%Y%m%dT%H%M%S")
            candidates = os.path.join(directory, "candidates.jsonl")
            backup = candidates + ".before-" + stamp
            with open(candidates, encoding="utf-8") as handle:
                original = handle.read()
            with open(backup, "w", encoding="utf-8") as handle:
                handle.write(original)
            with open(candidates, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print("  候選列已寫回 %s（備份 %s）" % (candidates, backup))
            import apply_experience_repairs as ax
            events = os.path.join(directory, "question_review_events.jsonl")
            by_key = collections.OrderedDict()
            for item in pending:
                by_key.setdefault(str(item["row"].get("candidate_key") or ""), []).append(item)
            written = 0
            for key, items in by_key.items():
                row = items[0]["row"]
                subs = [{"field": item["field"], "before": item["before"], "after": item["after"],
                         "replace": True} for item in items]
                correction = ax.apply_mod.build_correction(row, subs)
                lead = next((item.get("why") for item in items if item.get("why")), None) \
                    or ax.lead_for(subs)
                event = ax.apply_mod.build_repair_event(
                    key, subs, correction, None, reviewer="repair_italic_markup",
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%S"), crop=None, applied="field",
                    lead=lead, source="qbr_italic_markup")
                event["experience"] = {
                    "form": "italic-markup", "confirmed_questions": 0, "evidence": [],
                    "why": ("紙本字型量到的斜體（全庫普查 %d 題／%d 片段）；業主 2026-09-25 決定"
                            "套用到全部的題目" % (stats["questions-with-italic"], stats["spans"])),
                }
                ax.append_review_event(events, event)
                written += 1
                print("  已記 %s：%s" % (key, "；".join("%s %s" % (item["field"],
                                                                "+".join(item["runs"]))
                                                      for item in items)))
            print("  事件寫到 %s（%d 筆 reset_review）" % (events, written))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"stats": dict(stats), "examples": {k: v for k, v in examples.items()}},
                      handle, ensure_ascii=False, indent=1)
        print("明細寫到 %s" % args.json)
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="紙本斜體字與收錄文字的標記對不對得起來（唯讀）")
    parser.add_argument("--queue", required=True, help="佇列根或它的 review-ui 目錄")
    parser.add_argument("--only", default=None, help="只掃含這個字串的列（key／題號）")
    parser.add_argument("--limit", type=int, default=0, help="最多掃幾題有斜體的")
    parser.add_argument("--json", default=None, help="把統計與樣本寫到這個檔（預設不寫任何東西）")
    parser.add_argument("--verify", default=None, metavar="JSONL",
                        help="檢查已套用的 <i> 歸屬對不對（紙本那一列的文字落在哪一欄），逐題寫到這個檔")
    parser.add_argument("--revert", action="store_true",
                        help="與 --verify 併用：把歸屬錯的標記從錯的欄位拿掉（能確定位置時移到對的欄位）")
    parser.add_argument("--apply", action="store_true",
                        help="把斜體標成 <i> 並寫回候選列（每一欄只出現一次的片段才動；寫前備份）")
    parser.add_argument("--detail", default=None,
                        help="逐列明細（JSONL：每一題有斜體的都一行，記哪一欄、什麼狀態、幾個字）")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(scan(parse_args()))
