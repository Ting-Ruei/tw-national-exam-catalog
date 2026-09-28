#!/usr/bin/env python3
"""全語料盤點：`option_alphabet` 的 union 會把「選項家族」標成非 `A` 的卷有幾份。

這支探針回答一個**紙張性質**的問題，與語意無關：

   一份卷印了兩個以上的 private-use family 時，
   把「所有 family 的聯集」丟給 `canon.LABELS = (A,B,C,D,E,F)` 逐碼指派，
   真正的「選項家族」會不會被排到 `A` 以外的位置？

若是，`reflow.skeleton`（找 `opt:A`）就找不到選項 → **整卷每一題 0 選項**。

**判準不是語意，是位置**：選項家族＝總使用次數最多的 family
（選項標籤每題印一次，子項標籤只在題目問到時才印）。

**負對照（要能證明檢查自己會說謊）**：
- 「union 比最常用 family 拿到更多選項」的卷數必須是 0（`union_better`）。若 > 0，此判準錯。
- 沒有 private-use font 的卷（`no_family`）必須遠多於 `MISLABELED`（本機量到 6,729 vs 667）。

用 `qbr/.venv/bin/python`（有 PyMuPDF）。

```sh
cd tw-national-exam-catalog
qbr/.venv/bin/python repair_agent_test/probes/census_option_alphabet_union.py \
    --root 國考題資料夾/10_official_pdf/by_official_catalog \
    --out-dir /tmp/option-alphabet-union
```

輸出：`census.json`（統計）、`mislabeled.json`（667 篇逐篇）、`union_better.json`（應為空）。
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "qbr", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "qbr", "scripts"))

from qbr import canon, extract, reflow, repair  # noqa: E402
import compare_skeleton  # noqa: E402


def option_count(kept, alphabet):
    """這份卷在這個 alphabet 下解析出幾個有選項的題。"""
    table, _, used = reflow.cells_with_pages(kept, alphabet=alphabet)
    items = compare_skeleton.skeleton_items(reflow.skeleton(table), table, used)
    return sum(1 for item in items if item["options"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="國考題資料夾/10_official_pdf/by_official_catalog")
    parser.add_argument("--out-dir", default="/tmp/option-alphabet-union")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(args.root, "*", "*", "*", "*.pdf")))
    print("papers", len(pdfs), file=sys.stderr)

    stats = collections.Counter()
    mislabeled = []
    union_better = []
    for index, pdf in enumerate(pdfs):
        try:
            rows = extract.extract_cells_a(pdf)
            kept, _ = repair.mask_chrome(rows)
            body = "".join(row.get("text") or "" for row in kept)
            families = repair.option_alphabet_families(body)
            if not families:
                stats["no_family"] += 1
                continue
            union = tuple(sorted({code for family in families for code in family}))
            labels = {code: canon.LABELS[i] for i, code in enumerate(union)
                      if i < len(canon.LABELS)}
            top = tuple(families[0])
            if labels.get(top[0]) != "A":
                stats["MISLABELED"] += 1
                mislabeled.append({"pdf": pdf, "n_fam": len(families), "top": hex(top[0]),
                                   "got": labels.get(top[0]),
                                   "union": [hex(code) for code in union]})
            else:
                stats["ok"] += 1
            if len(families) >= 2:
                as_union = option_count(kept, union)
                as_top = option_count(kept, top)
                if as_union > as_top:               # negative control: must stay empty
                    union_better.append({"pdf": pdf, "union": as_union, "top": as_top})
        except Exception as error:                   # noqa: BLE001 - a probe counts failures
            stats["error"] += 1
            print("error", pdf, error, file=sys.stderr)
        if index % 1000 == 0:
            print("  ...", index, file=sys.stderr)

    with open(os.path.join(args.out_dir, "census.json"), "w") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out_dir, "mislabeled.json"), "w") as handle:
        json.dump(mislabeled, handle, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out_dir, "union_better.json"), "w") as handle:
        json.dump(union_better, handle, ensure_ascii=False, indent=1)

    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print("union better than most-used family on:", len(union_better), "(negative control, expect 0)")


if __name__ == "__main__":
    main()
