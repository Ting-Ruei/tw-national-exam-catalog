# -*- coding: utf-8 -*-
"""The corpus is found, not counted to.

This covers the defect that a fresh clone exposed. Eight places used to decide the corpus location
themselves, with two incompatible rules - `dirname(dirname(PKG)) + "/tw-national-exam-catalog/..."`,
`dirname(PKG)/../ + the same`, and in one file a **hard-coded absolute path**. All of them were
correct in this workspace and none of them was correct for the wrong reason: they were statements
about where the code happened to sit, wearing the clothes of statements about the repository.

Measured on a clean checkout of the catalog repository alone (one directory shallower than this
workspace), the counted path leaves the repository entirely, the corpus is not found, and two tests
report a **pipeline defect** - `AssertionError: assert ('moex:105020:305:33:1' and None)` - when the
actual cause is that a test could not find a directory. That is the failure mode worth a test: not a
crash, a wrong accusation, pointing the reader at the wrong file.
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import paths  # noqa: E402


def test_the_repository_is_identified_by_its_contents_not_by_its_depth():
    """`repo_root()` 找的是「有 catalogs/ 的那一層」，不是「往上第幾層」。

    本樹的父目錄就有 `catalogs/`，所以答案必須是 catalog repo，不是 `ai_learning_platform`。
    """
    root = paths.repo_root()
    assert os.path.isdir(os.path.join(root, paths.REPO_MARKER)), root
    # 這一條是重點：舊寫法會在「獨立 clone」時指到 repo 外面。
    assert root == os.path.dirname(PKG), (root, os.path.dirname(PKG))


def test_a_standalone_checkout_resolves_inside_itself():
    """獨立 clone（corpus 在 repo 根、沒有 `tw-national-exam-catalog/` 那一層）要能解析。

    這是舊規則真正失效的佈局，也是這次發現缺陷的地方。用一個假的樹來量，因為真的 clone
    不可能出現在測試裡。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "standalone")
        qbr = os.path.join(repo, "qbr")
        os.makedirs(os.path.join(repo, "catalogs"))
        os.makedirs(os.path.join(repo, "國考題資料夾", "10_official_pdf"))
        os.makedirs(qbr)
        got = paths.asset_root(qbr)
        assert got == os.path.join(repo, "國考題資料夾"), got
        # 而「往上數兩層」的舊寫法在這個佈局會指到 tmp，也就是 repo 外面。
        assert os.path.dirname(os.path.dirname(qbr)) != repo


def test_a_workspace_layout_still_resolves():
    """工作區佈局（corpus 在 `tw-national-exam-catalog/` 底下）要解到那個位置，不得退步。

    用**假的樹**，不依賴這台機器上有沒有 corpus。第一版直接 assert 真的 corpus 存在，
    結果這個「全新 clone 必須 0 failed」的測試本身在全新 clone 裡失敗 —— 正是它要防的那種錯。
    一個測試若把「這台機器上剛好有資料」當成前提，它就把環境的巧合寫成了規格。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        workspace = os.path.join(tmp, "ai_learning_platform")
        # 工作區佈局：corpus 在 `tw-national-exam-catalog/` 之下，package 也在同一層之下。
        repo = os.path.join(workspace, "tw-national-exam-catalog")
        qbr = os.path.join(repo, "qbr")
        os.makedirs(os.path.join(repo, "catalogs"))
        os.makedirs(os.path.join(repo, "國考題資料夾", "10_official_pdf"))
        os.makedirs(qbr)
        got = paths.asset_root(qbr)
        assert got == os.path.join(repo, "國考題資料夾"), got
        # 而「往上數兩層」的舊寫法在 *沙盒* 佈局才會對，在這裡會指到 workspace 上一層。
        assert os.path.dirname(os.path.dirname(qbr)) == workspace


def test_the_environment_wins():
    """操作者指定 corpus 時，操作者是權威。"""
    try:
        os.environ[paths.ENV_VAR] = "/tmp"
        assert paths.asset_root() == "/tmp"
    finally:
        os.environ.pop(paths.ENV_VAR, None)


def test_a_missing_corpus_is_reported_as_a_path_not_as_none():
    """corpus 不在時，預設回傳「正規的預期路徑」，讓錯誤訊息能說出它找過哪裡。

    `must_exist=True` 才回傳 `None`：兩種呼叫者的需求不同，而把「不在」與「沒有」混成同一個
    答案是這整類缺陷的來源。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "nowhere", "qbr")
        os.makedirs(repo)
        canonical = paths.asset_root(repo)
        assert canonical.endswith(paths.CORPUS_DIR_NAME)
        assert paths.asset_root(repo, must_exist=True) is None


def test_no_module_hard_codes_an_absolute_home_directory():
    """不得有寫死的 `/Users/...` 路徑留在可執行的程式碼裡。

    `where_is_the_text.py` 曾經有一條，charter 明文禁止（資產只用相對路徑）。
    用 **AST 掃字串常值**，不是 grep：註解與 docstring 可以（也應該）提到舊路徑 ——
    那是在記錄歷史，而這條規則管的是會真正被解析成路徑的字串。
    第一版用 grep 寫，結果 6 個真註解被當成違規；一個會誤報的檢查會很快被人忽略，
    那比沒有檢查更糟。

    掃描範圍**包含 `tests/`**。第一版只掃 `scripts/` 與 `src/`，於是錯過了
    `tests/test_text_repair.py` 裡一條寫死的路徑 —— 而那個檔案正是這條規則要防的缺陷。
    檢查器漏掉自己的測試，比漏掉產品程式碼更嚴重：它讓「檢查通過」變成一句不成立的話。
    """
    import ast
    offenders = []
    for base in ("scripts", "src", "tests"):
        for dirpath, _dirs, files in os.walk(os.path.join(PKG, base)):
            if "__pycache__" in dirpath:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as handle:
                    source = handle.read()
                # Parse **once**. The first version of this test called `ast.parse` twice, so the
                # `id()` of the docstring nodes from the first tree never matched the nodes in the
                # second, every docstring counted as a string literal, and the test reported prose
                # as a hard-coded path. Two parses of the same text are two different sets of nodes;
                # identity only means something within one tree.
                tree = ast.parse(source)
                docstrings = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        body = getattr(node, "body", None)
                        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                                and isinstance(body[0].value.value, str):
                            docstrings.add(id(body[0].value))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                            and id(node) not in docstrings:
                        for marker in ("/Users/", "/Volumes/", "file://"):
                            # A literal that **is** the bare marker is a detection pattern - this very
                            # test and `test_merge_golden.py` hold them in tuples. What is forbidden is
                            # a literal that *contains* the marker plus real path content, i.e. an
                            # actual absolute path. Without this distinction the scanner reports
                            # itself, which is how a check gets switched off.
                            if marker in node.value and node.value.strip() != marker:
                                offenders.append(f"{path}:{node.lineno}: {node.value[:60]!r}")
    assert not offenders, offenders
