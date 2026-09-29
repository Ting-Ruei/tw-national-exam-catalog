"""Build browser-safe copies of every official paper a browser would show blank.

Run:  qbr/.venv/bin/python qbr/scripts/build_browser_safe_papers.py --dry-run
      qbr/.venv/bin/python qbr/scripts/build_browser_safe_papers.py --apply

The official corpus is read, never written. Output goes to
`<asset_root>/10_official_pdf_browser_safe/`, mirroring the corpus tree, and every file is recorded
in a manifest with both digests. A paper that needs no change is not copied at all: the server falls
back to the original, so the derived tree stays as small as the defect.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qbr.browser_safe_pdf import (  # noqa: E402
    DERIVED_DIR_NAME,
    corpus_pages_needing_rewrite,
    derived_path_for,
    rewrite_and_verify,
)
from qbr.paths import asset_root  # noqa: E402

MANIFEST_NAME = "browser_safe_manifest.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", help="corpus root; defaults to qbr's asset_root()")
    parser.add_argument("--derived-root", help="output root; defaults to <asset_root>/%s" % DERIVED_DIR_NAME)
    parser.add_argument("--manifest", help="defaults to <derived_root>/%s" % MANIFEST_NAME)
    parser.add_argument("--apply", action="store_true", help="write the files (default is a dry run)")
    parser.add_argument("--only", help="restrict to paths containing this substring")
    parser.add_argument("--limit", type=int, help="stop after this many papers")
    args = parser.parse_args()

    root = args.asset_root or asset_root(must_exist=True)
    if not root:
        print("找不到語料根目錄；用 --asset-root 指定", file=sys.stderr)
        return 2

    derived_root = args.derived_root or os.path.join(root, DERIVED_DIR_NAME)
    manifest_path = args.manifest or os.path.join(derived_root, MANIFEST_NAME)

    print("語料根目錄 %s" % root)
    print("輸出根目錄 %s" % derived_root)
    print()

    targets = corpus_pages_needing_rewrite(root)
    if args.only:
        targets = [path for path in targets if args.only in path]
    if args.limit:
        targets = targets[: args.limit]

    print("需要轉換的 PDF: %d 份" % len(targets))
    print()

    if not args.apply:
        for path in targets:
            print("  %s" % os.path.relpath(path, root))
        print()
        print("（這是 dry-run；加上 --apply 才會寫檔）")
        return 0

    os.makedirs(derived_root, exist_ok=True)

    records = []
    failed = []
    for index, source in enumerate(targets, start=1):
        target = derived_path_for(source, root, derived_root)
        relative = os.path.relpath(source, root)
        try:
            report = rewrite_and_verify(source, target)
        except Exception as exc:  # noqa: BLE001 - a failure is recorded and the batch continues
            failed.append({"source": relative, "reason": "%s: %s" % (type(exc).__name__, exc)})
            print("  [%d/%d] 失敗 %s: %s" % (index, len(targets), relative, exc))
            continue

        record = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": "qbr.scripts.build_browser_safe_papers",
            "reason": (
                "Chrome's PDF viewer cannot decode JPEG 2000, so the scanned page images in this "
                "paper rendered blank; the image streams were re-encoded as JPEG and nothing else "
                "was changed"
            ),
            "relative_path": relative,
            "derived_path": os.path.relpath(target, root),
            **report,
        }
        records.append(record)
        print(
            "  [%d/%d] %s  %d 頁 / %d 張圖  %.2f -> %.2f MB"
            % (
                index,
                len(targets),
                relative,
                report["pages"],
                report["converted_images"],
                report["source_bytes"] / 1048576,
                report["target_bytes"] / 1048576,
            )
        )

    with open(manifest_path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print()
    print("完成: %d 份, 失敗 %d 份" % (len(records), len(failed)))
    print("manifest %s" % manifest_path)
    if failed:
        print()
        print("失敗清單:")
        for item in failed:
            print("  %s: %s" % (item["source"], item["reason"]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
