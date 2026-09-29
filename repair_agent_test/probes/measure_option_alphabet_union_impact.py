#!/usr/bin/env python3
"""在 `census_option_alphabet_union.py` 找到的 667 篇上，量「union vs 最常用 family」的選項數。

這支探針回答：**修正到底有沒有用、有沒有副作用。**

對每一篇被誤標的卷：
- `union_zero`：union 下全卷 0 選項的篇數（預期 ~658）
- `top_zero`：最常用 family 下仍 0 選項的篇數（剩下的應是**獨立的題號偵測問題**）
- `top_full`：最常用 family 下**每一題**都拿到選項的篇數
- `top worse than union`：**負對照，必須是 0**（修正不得讓任何一篇變差）

```sh
cd tw-national-exam-catalog
qbr/.venv/bin/python repair_agent_test/probes/measure_option_alphabet_union_impact.py \
    --mislabeled /tmp/option-alphabet-union/mislabeled.json \
    --out /tmp/option-alphabet-union/impact.json
```
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "qbr", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "qbr", "scripts"))

from qbr import extract, reflow, repair  # noqa: E402
import compare_skeleton  # noqa: E402


def counts(kept, alphabet):
    table, _, used = reflow.cells_with_pages(kept, alphabet=alphabet)
    items = compare_skeleton.skeleton_items(reflow.skeleton(table), table, used)
    return sum(1 for item in items if item["options"]), len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mislabeled", default="/tmp/option-alphabet-union/mislabeled.json")
    parser.add_argument("--out", default="/tmp/option-alphabet-union/impact.json")
    args = parser.parse_args()

    papers = json.load(open(args.mislabeled))
    stats = collections.Counter()
    worse = []
    for index, paper in enumerate(papers):
        try:
            rows = extract.extract_cells_a(paper["pdf"])
            kept, _ = repair.mask_chrome(rows)
            body = "".join(row.get("text") or "" for row in kept)
            families = repair.option_alphabet_families(body)
            union = tuple(sorted({code for family in families for code in family}))
            top = tuple(families[0])
            as_union, _ = counts(kept, union)
            as_top, total = counts(kept, top)
            stats["union_zero"] += 1 if as_union == 0 else 0
            stats["top_zero"] += 1 if as_top == 0 else 0
            stats["top_full"] += 1 if as_top == total else 0
            stats["n"] += 1
            if as_top < as_union:                     # negative control: expect empty
                worse.append({"pdf": paper["pdf"], "union": as_union, "top": as_top})
        except Exception as error:                    # noqa: BLE001 - a probe counts failures
            stats["error"] += 1
            print("error", paper["pdf"], error, file=sys.stderr)
        if index % 200 == 0:
            print(" ...", index, file=sys.stderr)

    with open(args.out, "w") as handle:
        json.dump({"stats": stats, "worse": worse}, handle, ensure_ascii=False, indent=1)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print("most-used family worse than union on:", len(worse), "(negative control, expect 0)")


if __name__ == "__main__":
    main()
