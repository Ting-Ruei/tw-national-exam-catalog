# -*- coding: utf-8 -*-
"""Manifest paths must survive a move.

The defect this covers is the merge that produced this package. The sample manifest was written
with absolute paths into the sandbox (`pi_test/question_bank_rebuild/`), and the pipeline was then
moved into the catalog repository. Every path still resolved - to a directory that no longer held
the pipeline. Three tests failed on `FileNotFoundError` and 198 passed, because most tests do not
read the manifest; the failure was real and almost entirely silent.

So the assertions are: a stored path is relative, a loaded path is absolute, and the answer does not
depend on the working directory.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import manifests  # noqa: E402

MANIFEST = manifests.of_package(PKG)


def test_the_manifest_records_relative_paths():
    """A manifest row must not carry an absolute path.

    絕對路徑在沙盒裡是對的，搬走就全錯 —— 而錯的方式是「找不到檔案」，不是「讀到錯的檔案」，
    所以它不會被內容比對抓到。這條測試就是搬遷時實際踩到的坑。
    """
    if not os.path.isfile(MANIFEST):
        pytest.skip("no sample manifest in this checkout")
    with open(MANIFEST, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    assert rows
    for row in rows:
        for key in manifests.PATH_KEYS:
            value = row.get(key)
            if value:
                assert not os.path.isabs(value), f"{key} is absolute: {value}"


def test_loading_resolves_against_the_manifest_not_the_cwd(tmp_path, monkeypatch):
    """同一份 manifest，在任何工作目錄下讀到的都是同一個檔。"""
    manifest = tmp_path / "data" / "sample_manifest.jsonl"
    manifest.parent.mkdir(parents=True)
    asset = manifest.parent / "raw" / "paper.pdf"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"%PDF-1.4\n")
    manifest.write_text(json.dumps({"uid": "x", "raw_copy": "raw/paper.pdf"}) + "\n",
                        encoding="utf-8")
    for where in (str(tmp_path), os.path.dirname(str(tmp_path)), "/"):
        monkeypatch.chdir(where)
        rows = manifests.load(str(manifest))
        assert rows[0]["raw_copy"] == str(asset), where


def test_a_missing_manifest_is_empty_not_an_error():
    """「還沒建樣本」是一個合法的狀態，不是壞掉的 manifest。"""
    assert manifests.load("/nonexistent/sample_manifest.jsonl") == []


def test_an_absolute_row_is_left_alone_and_not_silently_repaired(tmp_path):
    """已經是絕對路徑的列要原樣回傳。

    不去「順手修好」是刻意的：修好會讓一份仍然寫著絕對路徑的 manifest 看起來正常，
    而那份 manifest 本身就是缺陷。讓它繼續可用但明顯不對，比悄悄改成正確更安全。
    """
    absolute = "/some/where/paper.pdf"
    row = manifests.resolve({"raw_copy": absolute}, str(tmp_path))
    assert row["raw_copy"] == absolute
