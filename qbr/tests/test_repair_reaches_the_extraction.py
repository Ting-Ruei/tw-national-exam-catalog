# -*- coding: utf-8 -*-
"""端到端：人 block → 紙本判讀 → 事件 → **抽取檔真的被改** → 介面看到的是改過的。

這一支存在的理由
----------------
2026-09-24 owner 的原話：「判讀 → 文字 跟 文字 →抽取檔要打通，並且改標籤送到「AI已解決」，
我才能知道有沒有改過。」在那之前，鏈路只通到一半：判讀寫了 advisory、套用寫了 append-only
事件、**抽取檔本身永遠是壞的那一份**。於是同一題每一輪都被重新讀到同一個缺陷，而「已解決」
只是一個覆蓋顯示。

所以這裡測的是兩支工具的**交界**，不是任何一支的內部（那兩支各自的單元測試在
`test_apply_dispute_repairs.py` 與 `test_apply_text_corrections.py`）：

    question_review_events.jsonl（人的 block）
    question_ai_findings.jsonl（紙本判讀：`changes` ＋ 截圖）
        -> apply_dispute_repairs.py --apply     （判讀 → 文字）
        -> apply_text_corrections.py  --apply   （文字 → 抽取檔）
        -> candidates.jsonl 的那一欄真的等於紙本讀到的字

負控制（沒有這一條，這支可以因為「什麼都沒做」而通過）
------------------------------------------------------
`q_never_reviewed` 帶著一模一樣的判讀與 `changes`，但**沒有人按過 block**：它必須**不被改**。
這正是「機器只碰人已經拒絕的題」那條閘門；把閘門拆掉，這一條會紅。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent

#: The two tools the loop runs, in order. Spelled as the daemon spells them.
APPLIER = PKG / "scripts" / "apply_dispute_repairs.py"
PRODUCER = PKG / "scripts" / "apply_text_corrections.py"

#: A defect no detector in this project fires on: the paper prints a half-width apostrophe and the
#: extraction stored a full-width one. Same length, one character - a substitution, not a rewrite.
SUB_KEY = "moex:111020:305:33:1:question:q901"
SUB_STORED = "有關 Parkinson’s disease 的敘述，下列何者正確？"
SUB_PAGE = "有關 Parkinson's disease 的敘述，下列何者正確？"

#: The same kind of defect but **not** the same length: the extraction dropped a run of text. This is
#: the shape the owner reported as 「題目被吃到選項」. It cannot be a character substitution, which is
#: exactly why it used to be dropped on the floor.
FIELD_KEY = "moex:111020:305:33:1:question:q902"
FIELD_STORED = "下列何者為臺灣物理治療學會於西元2030年的願景？"
FIELD_PAGE = "下列何者為臺灣物理治療學會於西元2030年的願景？請選出最適合的答案"

#: Identical evidence, nobody rejected it. Must be left alone.
NEVER_KEY = "moex:111020:305:33:1:question:q903"

#: The defect sits in one **option**, and the reader's whole-field correction carries the options it
#: did not edit as context. That is the shape the whole-field path emits, and on 2026-09-24 the
#: producer refused the entire row over it ("applied=field 但沒有對應的 changes"), i.e. the visible
#: defect the owner blocked stayed unfixed while a normalisation on another question went in. The
#: contract: `changes` names what changed, `correction` carries the field values the server's fold
#: needs, and a carried value that disagrees with disk is what makes an event stale - not the mere
#: absence of a `changes` entry.
OPTION_KEY = "moex:111020:305:33:1:question:q904"
OPTION_A_STORED = "具有阻斷GABAA型受體之功能"
OPTION_A_PAGE = "具有阻斷 GABA 型受體之功能"


def _row(key: str, stem: str, number: int, option_a: str = "選項A") -> dict:
    return {
        "candidate_key": key,
        "question_number": number,
        "stem": stem,
        "options": [{"key": k, "text": (option_a if k == "A" else "選項" + k)} for k in "ABCD"],
        "answer": "A",
        "metadata": {"normalized_category_name": "測試類科", "year": "115",
                     "exam_ordinal": "1", "normalized_subject_name": "測試科目"},
    }


def _finding(key: str, field: str, stored: str, page: str, verdict: str = "TRUST") -> dict:
    """One page read, shaped exactly like the resident loop writes it.

    `orchestration.verdict` is not decoration: a whole-field replacement is only written when the
    second engine called the difference mechanical (`TRUST`). A fixture without it is a reading nobody
    ever double-checked, which the applier refuses — that refusal has its own test below.
    """
    record = {
        "schema": "qbr_ai_finding_v0.1",
        "candidate_key": key,
        "created_at": "2026-09-24T17:00:00",
        "model": "test-page-reader",
        "endpoint": "http://127.0.0.1:1",
        "prompt_version": "test",
        "population": "dispute",
        "reading_sha256": "0" * 64,
        "crop": "review-ui/crops/test/q901-dispute.png",
        "changes": [{"field": field, "from": stored, "to": page, "stored": stored, "page": page}],
        "finding": {"verdict": "DEFECT", "what": "PUNCTUATION",
                    "where": "%s：紙本是「%s」，抽取成「%s」" % (field, page, stored),
                    "fix": "把 %s 從「%s」改成「%s」" % (field, stored, page),
                    "rule_worthy": False, "confidence": 0.8, "transcription": {"stem": page}},
        "error": None,
    }
    if verdict is not None:
        record["orchestration"] = {"verdict": verdict, "why": "測試用", "self_look": verdict != "TRUST"}
    return record


def _build_queue(root: Path, *, block_keys: set[str], field_verdict: str | None = "TRUST",
                 field_page: str = FIELD_PAGE) -> Path:
    ui = root / "review-ui"
    ui.mkdir(parents=True)
    rows = [
        _row(SUB_KEY, SUB_STORED, 901),
        _row(FIELD_KEY, FIELD_STORED, 902),
        _row(NEVER_KEY, FIELD_STORED, 903),
        _row(OPTION_KEY, "下列何者為 felbamate 的作用機轉？", 904, option_a=OPTION_A_STORED),
    ]
    (ui / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    findings = [
        _finding(SUB_KEY, "stem", SUB_STORED, SUB_PAGE),
        _finding(FIELD_KEY, "stem", FIELD_STORED, field_page, verdict=field_verdict),
        _finding(NEVER_KEY, "stem", FIELD_STORED, FIELD_PAGE),
        _finding(OPTION_KEY, "option A", OPTION_A_STORED, OPTION_A_PAGE),
    ]
    (ui / "question_ai_findings.jsonl").write_text(
        "".join(json.dumps(f, ensure_ascii=False) + "\n" for f in findings), encoding="utf-8")
    events = [
        {"created_at": "2026-09-24T17:01:0%d" % i, "candidate_key": key, "action": "block",
         "reviewer": "local", "notes": "紙本不是這個字"}
        for i, key in enumerate(sorted(block_keys))
    ]
    (ui / "question_review_events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return ui


def _run(script: Path, queue: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), "--queue", str(queue), *extra],
        capture_output=True, text=True, cwd=str(PKG), timeout=300)


def _rows(ui: Path) -> dict:
    out = {}
    for line in (ui / "candidates.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["candidate_key"]] = row
    return out


def _events(ui: Path) -> list:
    return [json.loads(line) for line
            in (ui / "question_review_events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repair_twice(root: Path):
    """The loop's own order: apply the reading, then let the text reach the extraction file."""
    ui = root / "review-ui"
    first = _run(APPLIER, root, "--page-read", "--apply")
    second = _run(PRODUCER, root, "--apply")
    return ui, first, second


def test_a_page_read_reaches_the_extraction_file():
    root = Path(tempfile.mkdtemp(prefix="qbr-e2e-repair-"))
    try:
        ui = _build_queue(root, block_keys={SUB_KEY, FIELD_KEY})
        before = _sha(ui / "candidates.jsonl")
        ui, applied, produced = _repair_twice(root)
        rows = _rows(ui)

        # 判讀 → 文字：both shapes get a repair, and both carry the evidence and the label.
        assert rows[SUB_KEY]["stem"] == SUB_PAGE, rows[SUB_KEY]["stem"]
        assert rows[FIELD_KEY]["stem"] == FIELD_PAGE, rows[FIELD_KEY]["stem"]
        repairs = [e for e in _events(ui) if e.get("reviewer") == "repair_dispute_apply"]
        assert {e["candidate_key"] for e in repairs} == {SUB_KEY, FIELD_KEY}, repairs
        for event in repairs:
            assert event["action"] == "reset_review"
            assert event["repair_kind"] == "content_change"
            assert event.get("applied"), "事件要帶 applied 標籤，介面才知道有沒有改過"
            assert event.get("crop"), "事件要帶截圖，人才能對照"
            assert event["changes"], "事件要帶它改的那兩邊"

        # 文字 → 抽取檔：the file itself changed, and the pre-repair text is still readable.
        assert _sha(ui / "candidates.jsonl") != before
        for key, stored in ((SUB_KEY, SUB_STORED), (FIELD_KEY, FIELD_STORED)):
            original = rows[key].get("parser_original") or {}
            assert original.get("stem") == stored, (key, original)
        assert produced.returncode == 0, produced.stderr

        # Idempotent: the loop runs every 30 minutes forever.
        sha_once = _sha(ui / "candidates.jsonl")
        _, applied_again, produced_again = _repair_twice(root)
        assert _sha(ui / "candidates.jsonl") == sha_once
        assert len([e for e in _events(ui) if e.get("reviewer") == "repair_dispute_apply"]) == 2
        assert applied_again.returncode == 0 and produced_again.returncode == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_reading_the_second_engine_did_not_trust_never_reaches_the_file():
    """負控制：第二個引擎說這次讀法可疑（`CARE`/`DOUBT`），或根本沒有第二次判讀 → 不准改。

    這是 2026-09-24 站上實測的形狀：86 題的整欄替換計畫裡，`TRUST` 47 題、`CARE` 24、`DOUBT` 3、
    沒有第二次判讀 12。兩個把 `▢`（模型自己說「讀不出來」）寫進題目的案例也都是 `CARE`——所以
    這道閘門不是形式，它是目前抓得住那類讀法的唯一一條線。
    """
    for verdict in ("CARE", "DOUBT", None):
        root = Path(tempfile.mkdtemp(prefix="qbr-e2e-verdict-"))
        try:
            ui = _build_queue(root, block_keys={FIELD_KEY}, field_verdict=verdict)
            before = _sha(ui / "candidates.jsonl")
            ui, applied, produced = _repair_twice(root)
            assert applied.returncode == 0 and produced.returncode == 0
            assert _rows(ui)[FIELD_KEY]["stem"] == FIELD_STORED, verdict
            assert [e for e in _events(ui)
                    if e.get("reviewer") == "repair_dispute_apply"] == [], verdict
            assert _sha(ui / "candidates.jsonl") == before, verdict
        finally:
            shutil.rmtree(root, ignore_errors=True)


def test_a_reading_that_could_not_read_the_character_is_refused():
    """判讀說「這一格讀不出來」（`▢`）比現在的內容更差，不可以用它蓋掉讀得出來的字。"""
    root = Path(tempfile.mkdtemp(prefix="qbr-e2e-placeholder-"))
    try:
        ui = _build_queue(root, block_keys={FIELD_KEY}, field_page=FIELD_PAGE + "▢")
        before = _sha(ui / "candidates.jsonl")
        ui, applied, produced = _repair_twice(root)
        assert applied.returncode == 0 and produced.returncode == 0
        assert "▢" not in _rows(ui)[FIELD_KEY]["stem"]
        assert _sha(ui / "candidates.jsonl") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_humans_rejection_is_what_opens_the_gate():
    """負控制：一模一樣的判讀與 `changes`，但沒有人按過 block → 一個字都不准動。

    這一條是「機器只碰人已經拒絕的題」那條閘門的守門人。把閘門拆掉的人會在這裡看到紅燈，
    而不是在幾千題的抽取檔裡看到。
    """
    root = Path(tempfile.mkdtemp(prefix="qbr-e2e-gate-"))
    try:
        ui = _build_queue(root, block_keys=set())
        before = _sha(ui / "candidates.jsonl")
        ui, applied, produced = _repair_twice(root)
        rows = _rows(ui)
        assert rows[NEVER_KEY]["stem"] == FIELD_STORED
        assert [e for e in _events(ui) if e.get("reviewer") == "repair_dispute_apply"] == []
        assert _sha(ui / "candidates.jsonl") == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_person_own_fix_also_reaches_the_extraction_file():
    """人自己在討論區改的字，也要落到抽取檔——否則那一欄永遠是壞的，只是螢幕上看不出來。"""
    root = Path(tempfile.mkdtemp(prefix="qbr-e2e-human-"))
    try:
        ui = _build_queue(root, block_keys=set())
        events = _events(ui)
        events.append({
            "created_at": "2026-09-24T17:02:00", "candidate_key": NEVER_KEY, "action": "correct",
            "reviewer": "local", "notes": "紙本沒有那半句",
            "correction": {"stem": FIELD_STORED},
            "changes": [{"field": "stem", "from": FIELD_STORED, "to": FIELD_STORED}],
        })
        # The stored row is the *page* text here, so the person's edit is the one that must land.
        rows = _rows(ui)
        rows[NEVER_KEY]["stem"] = FIELD_PAGE
        (ui / "candidates.jsonl").write_text(
            "".join(json.dumps(rows[k], ensure_ascii=False) + "\n"
                    for k in (SUB_KEY, FIELD_KEY, NEVER_KEY, OPTION_KEY)), encoding="utf-8")
        (ui / "question_review_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")

        produced = _run(PRODUCER, root, "--apply")
        assert produced.returncode == 0, produced.stderr
        assert _rows(ui)[NEVER_KEY]["stem"] == FIELD_STORED
        assert (_rows(ui)[NEVER_KEY].get("parser_original") or {}).get("stem") == FIELD_PAGE
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_whole_field_reading_lands_in_one_option_and_leaves_the_others():
    """整欄替換只動它宣稱的那一欄；判讀一起帶回來的其他選項是上下文，不是宣稱。

    這是 2026-09-24 真的擋掉了一次修復的形狀（`115090:305:0401:q037`）：判讀換掉 `option A`，
    事件把讀到的 B/C/D 一起帶在 `correction` 裡（伺服器折疊要用整份清單），而 `changes` 只列 A。
    producer 一開始要求「`correction` 裡的每一欄都要有自己的 `changes`」，於是整列被拒絕——看得出來
    的缺陷沒被修，而看不見的正規化照樣進去了。正確的規則：沒被 `changes` 指名的欄位只要**與磁碟
    相符**就是上下文（跳過），與磁碟不符才是過期事件（拒絕）。
    """
    root = Path(tempfile.mkdtemp(prefix="qbr-e2e-option-"))
    try:
        ui = _build_queue(root, block_keys={OPTION_KEY})
        ui, applied, produced = _repair_twice(root)
        assert applied.returncode == 0 and produced.returncode == 0, (applied.stderr, produced.stderr)
        row = _rows(ui)[OPTION_KEY]
        options = {o["key"]: o["text"] for o in row["options"]}
        assert options["A"] == OPTION_A_PAGE, options["A"]
        assert options["B"] == "選項B" and options["C"] == "選項C" and options["D"] == "選項D"
        original = (row.get("parser_original") or {}).get("options")
        assert original and {o["key"]: o["text"] for o in original}["A"] == OPTION_A_STORED, original
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_person_edit_that_no_longer_matches_the_file_is_refused():
    """事件的 `from` 與檔案現在的字不符（文字後來又動過）→ 拒絕，不是改錯地方。"""
    root = Path(tempfile.mkdtemp(prefix="qbr-e2e-stale-"))
    try:
        ui = _build_queue(root, block_keys=set())
        events = _events(ui)
        events.append({
            "created_at": "2026-09-24T17:03:00", "candidate_key": NEVER_KEY, "action": "correct",
            "reviewer": "local",
            "correction": {"stem": "這是事件以為的那段字"},
            "changes": [{"field": "stem", "from": "這是事件以為的那段字", "to": "這是事件以為的那段字"}],
        })
        (ui / "question_review_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        before = _sha(ui / "candidates.jsonl")
        produced = _run(PRODUCER, root, "--apply")
        assert produced.returncode == 0, produced.stderr
        assert _sha(ui / "candidates.jsonl") == before, "過期的事件不可以改到檔案"
    finally:
        shutil.rmtree(root, ignore_errors=True)
