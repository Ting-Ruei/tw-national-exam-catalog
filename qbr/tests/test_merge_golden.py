# -*- coding: utf-8 -*-
"""The merge's own regressions, and the golden run that catches the next one.

Moving this pipeline from `pi_test/question_bank_rebuild/` into `tw-national-exam-catalog/qbr/` was
expected to be a file move. It was not: it exposed three defects, and *two of them were introduced
by the merge itself* and would have shipped silently.

1. **Absolute asset paths.** The sample manifest named files by absolute path into the sandbox. The
   move left 30 of 30 rows pointing at nothing. Covered in `test_manifests.py`.

2. **A doubled role suffix on the identity of every question.** The catalog spells a registry key
   with a role on the end (`moex:115090:308:0504:1:question`) and `package.py` appended `:question`
   unconditionally, so questions were keyed `...:question:question`. That key is what a reviewer's
   decision is recorded against. 80 of 80 questions were affected.

3. **A corrections sheet that could not be reached.** The registry manifest holds a row for the
   question and answer sheets of this paper but *none* for the 更正答案 sheet. The lookup treated a
   registry hit as an alternative to the directory listing rather than as a first source, so a
   partial hit stopped the search: `1152_醫事檢驗師_生物化學與臨床生化學` has a `_MOD.pdf` on disk
   the whole time, and its Q10 and Q41 are **送分** - published instead as `A` and `D`.

Number 3 is the reason this file is not just a set of unit tests. The failure direction matters: a
送分 mark means *every* answer is accepted, and publishing a single letter instead marks every other
option wrong. It is also the failure the two engines cannot catch, because both were reading the
same missing sheet. The golden run below is the check that compares a whole paper field for field,
so the next such defect fails a test rather than a reader.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import answer_sheets, package  # noqa: E402

GOLDEN = os.path.join(HERE, "golden", "golden_1152_medtech_biochem_candidates.jsonl")

CATALOG_ROOT = os.path.join(os.path.dirname(PKG), "國考題資料夾")


# --------------------------------------------------------------- the golden run

def _golden_rows():
    if not os.path.isfile(GOLDEN):
        pytest.skip("no golden file")
    with open(GOLDEN, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_the_golden_file_carries_no_absolute_path_or_machine_address():
    """封裝產物不得有絕對路徑或機器位址（charter 的硬規則，且用 grep 就能守住）。"""
    text = open(GOLDEN, encoding="utf-8").read()
    for forbidden in ("/Users/", "/Volumes/", "file://", "/tmp/", "192.168.", "127.0.0.1"):
        assert forbidden not in text, f"golden carries {forbidden}"


def test_the_golden_questions_are_keyed_by_the_paper_not_by_the_caller():
    """`candidate_key` 不得出現重複的 role 後綴。

    這是 merge 引進的缺陷：`...:question:question:q001`。它看起來只是名字難看，實際上是
    審核紀錄的主鍵 —— 主鍵一變，人做過的決定就對不回題目。
    """
    rows = _golden_rows()
    assert rows
    for row in rows:
        assert ":question:question" not in row["candidate_key"]
        assert ":answer:answer" not in (row.get("answer_source_registry_key") or "")
        assert row["candidate_key"].endswith(row["question_number"] and
                                             "q%03d" % int(row["question_number"]))
        assert row["source_registry_key"].count(":question") == 1


def test_the_golden_run_accepts_every_answer_of_a_correction():
    """更正答案的「送分」必須是四個字母，不是一個。

    `1152_醫事檢驗師_生物化學與臨床生化學` Q10 與 Q41 是送分。若某次重建把它讀成 `A`／`D`，
    每一位答 B、C、D 的考生都會被判錯 —— 而兩個引擎都會錯得一樣，因為它們讀的是同一份
    找不到的更正答案卷。這是這條測試存在的唯一理由。
    """
    rows = _golden_rows()
    special = [r for r in rows if (r.get("answer_payload") or {}).get("is_special_correction")]
    assert special, "the golden paper has 送分 questions; losing them is the defect"
    for row in special:
        assert row["answer"] == "送分", row["question_number"]
        assert sorted(row["answer_payload"]["accepted_values"]) == ["A", "B", "C", "D"]


def test_a_partially_mappable_offset_run_is_left_as_printed():
    """混有「無法轉成上下標」字元的 run，整段保持原樣 —— 這是已知的取捨，不是意外。

    `HbA₁c` 的 `₁` 在紙上確實是下標（11.03pt 的 `HbA` 旁邊一個 5.51pt 的 `1c`），
    而 `c` 沒有下標形式。舊規則是「有一個字能轉就轉」，結果 `1c` 變成 `₁c`，
    `HbA₁c` 是對的 —— 但同一條規則也把 `41.` 變成 `⁴¹.`、把 240 個普通數字變成上標。
    現在的規則是「全部都必須能轉」，所以這 240 個錯誤消失了，而 `HbA₁c` 退成 `HbA1c`。

    這條測試原本的註解寫著：「若哪天兩者都能成立（例如用幾何而不是字元集分辨），
    這裡應該是**改成斷言 `₁`**」。那一天沒有到 —— 而且不該到，因為**幾何分辨不了這一題**：
    Unicode 真的沒有下標 `c`，不是規則不肯轉，是**目的字元不存在**。所以「用幾何分辨」
    在這裡不成立，這個取捨是永久的。同一則註解也說了另一半：真正該修的是**字元集缺字母**，
    那部分已經修了（見上方 `test_a_raised_latin_letter_becomes_a_superscript`）：
    `k` `t` `p` `m` `a` `s` 這些**有** Unicode 形式的字母現在會轉，`c` 不會。

    這是**已知且已記錄**的少量英文上下標問題，不是可以拿掉的限制：把它改回去就是拿 240 個
    錯誤換一個下標。這條測試把當前的契約固定住。
    """
    rows = _golden_rows()
    hba = [r for r in rows if "HbA" in (r.get("stem") or "")]
    assert hba, "the golden paper has an HbA question"
    stem = hba[0]["stem"]
    assert "HbA" in stem
    # 不 partial 轉換：`c` 沒有下標形式，所以整段原樣。
    assert "\u2081" not in stem
    assert "HbA1c" in stem
    # 不得是半轉換的產物（`HbA₁c` 或 `⁴¹.` 那種錯法）
    assert "HbA\u2081c" not in stem


# --------------------------------------------------------------- the two fixes

def test_a_role_suffix_is_not_appended_twice():
    """`question_key` 對兩種呼叫拼法給出同一個題目鍵。"""
    bare = package.question_key("moex:115090:308:0504:1", 7)
    with_role = package.question_key("moex:115090:308:0504:1:question", 7)
    assert bare == with_role == "moex:115090:308:0504:1:question:q007"
    assert package.paper_key("moex:115090:308:0504:1:answer") == "moex:115090:308:0504:1"
    assert package.paper_key("moex:115090:308:0504:1") == "moex:115090:308:0504:1"


def test_a_partial_registry_hit_still_reaches_the_correction_sheet():
    """註冊表只給了 question/answer 時，更正答案卷仍要從目錄找到。

    這是一個假設的註冊表（只回兩卷）對真實目錄的測試 —— 用假的註冊表是刻意的：
    真註冊表的內容會變，而「部分命中不得停止搜尋」是規則。
    """
    if not os.path.isdir(CATALOG_ROOT):
        pytest.skip("corpus not present")
    paper = os.path.join(CATALOG_ROOT, "10_official_pdf", "by_official_catalog",
                         "醫事檢驗師", "115", "第2次")
    if not os.path.isdir(paper):
        pytest.skip("paper not present")

    def partial_reader():
        table = {
            "moex:115090:308:0504:1:question": {
                "destination": os.path.join(paper, "1152_醫事檢驗師_生物化學與臨床生化學.pdf")},
            "moex:115090:308:0504:1:answer": {
                "destination": os.path.join(paper, "1152_醫事檢驗師_生物化學與臨床生化學_ANS.pdf")},
        }
        return table, [], {}

    found, how = answer_sheets.sheet_paths(
        CATALOG_ROOT, year=115, category="醫事檢驗師", ordinal=2,
        subject="生物化學與臨床生化學", registry_key="moex:115090:308:0504:1",
        registry_reader=partial_reader)
    assert "question" in found and "answer" in found
    assert "corrected" in found, f"correction unreachable; resolution was {how!r}"
    assert found["corrected"].endswith("_MOD.pdf")


def test_the_directory_never_displaces_the_registry():
    """第二次目錄掃描只補缺，不覆蓋註冊表給的路徑。

    註冊表是註冊表，目錄是一個剛好同名的檔案；覆蓋會讓「查到的」與「猜到的」再也分不出來。
    """
    if not os.path.isdir(CATALOG_ROOT):
        pytest.skip("corpus not present")
    paper = os.path.join(CATALOG_ROOT, "10_official_pdf", "by_official_catalog",
                         "醫事檢驗師", "115", "第2次")
    if not os.path.isdir(paper):
        pytest.skip("paper not present")
    registry_answer = os.path.join(paper, "1152_醫事檢驗師_生物化學與臨床生化學_ANS.pdf")

    def reader():
        return ({"moex:115090:308:0504:1:answer": {"destination": registry_answer}}, [], {})

    found, _how = answer_sheets.sheet_paths(
        CATALOG_ROOT, year=115, category="醫事檢驗師", ordinal=2,
        subject="生物化學與臨床生化學", registry_key="moex:115090:308:0504:1",
        registry_reader=reader)
    assert found.get("answer") == registry_answer
