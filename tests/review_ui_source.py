"""Where the review UI server's source text lives, for tests that assert on it.

Several tests prove a rule "lives in one place" by searching the server's source for a marker — e.g.
`test_no_path_still_treats_only_correct_as_needing_reaffirming` greps for a predicate, and the
repaired-text test greps for `review_queue.disputes_for_paper(`. Those assertions are about the
implementation, so they must keep reading it.

On 2026-09-23 the 10,713-line `scripts/serve_question_review_ui.py` was split: the implementation
moved to `qbr/src/qbr/review_ui/*.py` and the server became a composition root that re-exports it.
A test that reads the server file alone would now find none of the code it is checking and fail for
the wrong reason — reporting a missing rule when the rule simply moved.

`server_source()` is the one place that answers "what is the server's source". Use it instead of
reading a path directly, so the next move only has to change this file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The composition root. Kept as a name because a few tests want *only* this file (the re-export
#: contract, the argument parser).
SERVER_PATH = ROOT / "scripts" / "serve_question_review_ui.py"

#: The extracted implementation, in load order. `qbr/src/qbr/review_ui/` holds the modules the
#: server used to contain.
IMPL_DIR = ROOT / "qbr" / "src" / "qbr" / "review_ui"


def server_source() -> str:
    """The server's full source: the composition root plus every extracted module.

    This is the honest answer to "the server's code" after the split, and it is what the
    single-source assertions should search. Order is stable (server first, then modules by name) so a
    failure message is reproducible.

    A test asserting `source.count("def X(") == 1` is checking for a *duplicate implementation*, so
    this must not pick up a copy of the old file. The extractor writes
    `serve_question_review_ui.py.pre-split` next to the server; globbing `*.py` in the impl dir is
    safe, but globbing the scripts directory would not be — hence the explicit list.
    """
    parts = [SERVER_PATH.read_text(encoding="utf-8")]
    if IMPL_DIR.is_dir():
        for path in sorted(IMPL_DIR.glob("*.py")):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def server_files() -> list[Path]:
    """Every file whose text `server_source()` concatenates."""
    files = [SERVER_PATH]
    if IMPL_DIR.is_dir():
        files.extend(sorted(IMPL_DIR.glob("*.py")))
    return files


#: Linux caps a **single** `argv` string at `MAX_ARG_STRLEN`; macOS does not have that cap. Any test
#: that hands a whole script to node must stay below it, which is why the script goes in a file.
NODE_ARG_MAX = 128 * 1024


def node_binary() -> str:
    """node 的路徑；這台機器沒有 node 就 SkipTest（呼叫的測試自己決定要不要跳）。"""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node 不在這台機器上")
    return node


def run_node_script(
    script: str,
    *,
    args: Sequence[str] = (),
    cwd: str | None = None,
    timeout: float = 120.0,
) -> str:
    """把一整份 JavaScript 交給 node 執行，回傳 stdout；非零離開碼就是 AssertionError。

    **不可以用 `node -e <script>`。** Linux 對**單一 argv 字串**有 128 KiB 的上限
    （`MAX_ARG_STRLEN`），macOS 沒有這一條。實測（2026-09-29，Linux 容器、node v20.19.2）：
    `tests/test_review_ui_discuss.py::test_the_merged_bucket_is_checked_against_the_real_helpers`
    把整棵範圍樹的 JSON ＋ v2 的全部原始碼串進 `-e`，在 Ubuntu 上丟
    `OSError: [Errno 7] Argument list too long`，在 macOS 上 100% 通過 —— 也就是說這一條**只有 CI
    看得見**（GitHub Actions `unit-tests` 就是這樣紅的，本機跑幾百次都綠）。

    暫存檔放在 repo 之外、副檔名 `.cjs`（強制 CommonJS，不受任何 `package.json` 的 `type` 影響）。
    `args` 是額外的 argv 尾巴（例如 vm runner 把呼叫式從 `process.argv[1]` 收進來，避免在 JS 字串
    裡處理引號）——**小的東西**才走這裡，它跟 script 一樣受 128 KiB 的限制；`cwd` 讓相對檔名
    （`01-core.js`）能像在 `review_ui/v2/` 裡一樣被讀到。
    """
    node = node_binary()
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", encoding="utf-8", delete=False) as handle:
        handle.write(script)
        path = handle.name
    try:
        result = subprocess.run(
            [node, path, *args], capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    finally:
        os.unlink(path)
    if result.returncode != 0:
        raise AssertionError(f"node 執行失敗：{result.stderr[-2000:]}")
    return result.stdout


def run_node_expression(prefix: str, expression: str) -> object:
    """`prefix`（stub ＋ 原始碼）之後接著印出 `JSON.stringify(expression)` 的值。"""
    stdout = run_node_script(f"{prefix}\nconsole.log(JSON.stringify({expression}));")
    return json.loads(stdout.strip().splitlines()[-1])
