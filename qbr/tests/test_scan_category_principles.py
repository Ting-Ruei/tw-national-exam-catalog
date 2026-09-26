# -*- coding: utf-8 -*-
"""整科掃描：選題只看考別、跳過只看讀法、寫出去的 population 是 `category-scan`。

The contract this file pins (owner 2026-09-24：「新整理出來的總規則要重新審視藥師、藥師(二)的
所有題目」):

  1. 選題由**考別**決定。沒有一筆 dispute、也沒有人按過任何按鈕的題目要被選到——那正是這個工具
     相對於 `confirm_dispute` 的全部意義（後者的工作清單來自人的 `block` 與爭議種類）。
  2. 別的考別不會被選到。
  3. 制度群組 key 與它包含的考別名稱選出**同一組**題目——拼字來源只有一個
     （`review_ui.constants.category_matches_filter`），包含 `藥師（一）` 這種全角括號的寫法。
  4. `--dry-run`（也就是預設）什麼都不寫：沒有 findings、沒有截圖、**沒有送模型**。
  5. `--skip-confirmed` 跳過同一個讀法，而讀法一變那一題自己回來（負控制：不是靠人記得清旗標）。
     一次**讀不到**不算讀過——暫時的端點故障不該讓題目永遠從清單上消失。
  6. 寫進去的 population 是 `category-scan`，不是 `dispute`（`apply_dispute_repairs` 只取後者，
     所以掃描的讀法不會被當成修復套用）。

The paper is never rendered here and the model is never called: `confirm_dispute.paper_pdf_of` and
`confirm_dispute.crop_for` are replaced (rasterising the corpus is `reread`/`extract`'s job, with its
own tests) and `ask_about_blocks.ask` — the one place a read reaches the network — is stubbed.
Everything between them is the production path: the same `confirm_one`, the same prompt assembly, the
same mechanical diff, the same append.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import ask_about_blocks  # noqa: E402
import confirm_dispute  # noqa: E402
import scan_category_principles as scan  # noqa: E402
from qbr import ai_findings, discuss  # noqa: E402
from qbr.review_ui.constants import CATEGORY_GROUP_FILTERS, PHARMACIST_TRACK_FILTER  # noqa: E402

#: The group key and the names it contains, from the app's own table — never spelled again here.
PHARM = CATEGORY_GROUP_FILTERS[PHARMACIST_TRACK_FILTER]


# ------------------------------------------------------------------ helpers

def _row(key, number, category, *, stem="下列何者錯誤？", disputes=None):
    """A queue row: category in `metadata.normalized_category_name`, which is where the app looks."""
    return {
        "candidate_key": key,
        "question_number": number,
        "stem": stem,
        "options": [{"key": letter, "text": "選項%s" % letter} for letter in "ABCD"],
        "answer": "A",
        "disputes": disputes or [],
        "metadata": {"normalized_category_name": category,
                     "normalized_subject_name": "藥理學",
                     "question_pdf_relative": "國考題資料夾/10_official_pdf/藥理學.pdf"},
    }


def _seen(row):
    """What the paper says: the row's own text, so the page agrees with the extraction."""
    return {"stem": row["stem"],
            "options": {option["key"]: option["text"] for option in row["options"]}}


def _queue(root, rows, *, principles=None):
    """A queue root with `review-ui/candidates.jsonl` — and **no review events at all** unless given."""
    ui = os.path.join(str(root), "review-ui")
    os.makedirs(ui, exist_ok=True)
    with open(os.path.join(ui, "candidates.jsonl"), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    for event in principles or []:
        discuss.append_event(os.path.join(ui, discuss.PRINCIPLES_STREAM), event)
    return ui


def _stub_paper(monkeypatch, tmp_path):
    """The two places a read touches the corpus, so a test needs no paper and no renderer."""
    pdf = tmp_path / "藥理學.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(confirm_dispute, "paper_pdf_of", lambda question: str(pdf))

    def fake_crop(pdf_path, number, *, dpi, out_png=None, boxes=()):
        png = b"\x89PNG stub crop"
        if out_png:
            os.makedirs(os.path.dirname(out_png), exist_ok=True)
            with open(out_png, "wb") as handle:
                handle.write(png)
        return png, 3, None

    monkeypatch.setattr(confirm_dispute, "crop_for", fake_crop)
    # 判讀有**兩個**會 rasterise 紙本的接縫：這一題自己的截圖（`crop_for`）與讀不出結論時的
    # 鄰題參考圖（`reference_read`）。本檔要驗的是寫出什麼、不是 rasterise（那是 `reread`／
    # `extract` 的職責，各自有測試），所以兩個都要換掉。
    monkeypatch.setattr(confirm_dispute, "reference_read", lambda *a, **k: None)


def _stub_page_read(monkeypatch, seen):
    """The endpoint, stubbed — and returns the list of calls, so a test can count them.

    `ask_about_blocks.ask` is exactly where `confirm_dispute.transcribe` reaches the network; the
    transcription's own parse (`reread.parse`) stays real, so what the model "said" travels the
    production path from the reply to the diff.
    """
    calls = []

    def fake_ask(messages, *, endpoint, max_tokens, timeout):
        calls.append(messages)
        return None, json.dumps(seen, ensure_ascii=False), None, {"total_tokens": 7}, 2.1

    monkeypatch.setattr(ask_about_blocks, "ask", fake_ask)
    return calls


def _run(monkeypatch, *argv):
    """One round of the script, exactly as the station runs it (same `main`, same argv)."""
    monkeypatch.setattr(sys, "argv", ["scan_category_principles.py", *argv])
    return scan.main()


# ------------------------------------------------------------------ 1 + 2: the selection is the category

def test_a_question_no_one_flagged_is_still_read(tmp_path):
    # 這是與 dispute 迴圈的差別，也是這個工具存在的理由：沒有 dispute、沒有人按過任何按鈕的題目
    # 正是還沒有人看過的題目。舊清單（人的 block ∩ 爭議種類）對它是空集合。
    row = _row("moex:1082:308:0504:1:question:q001", 1, "藥師(二)")
    ui = _queue(tmp_path / "queue", [row])
    assert not os.path.exists(os.path.join(ui, "question_review_events.jsonl")), \
        "這一題沒有人下過任何決定——那正是要被選到的情況"
    selection = scan.selected_questions(ui, ["藥師(二)"])
    assert [r["candidate_key"] for r in selection.rows] == [row["candidate_key"]]
    assert selection.matched == 1 and selection.skipped == 0


def test_another_category_is_not_read(tmp_path):
    ui = _queue(tmp_path / "queue", [_row("k-pharm", 1, "藥師(二)"), _row("k-phys", 2, "醫師(二)")])
    selection = scan.selected_questions(ui, ["藥師(二)"])
    assert [r["candidate_key"] for r in selection.rows] == ["k-pharm"]


def test_the_group_key_and_the_names_inside_it_select_the_same_questions(tmp_path):
    # 一個拼字來源：群組 key 展開成的集合，與那些名稱一個一個寫出來，必須選出同一組題目——包括
    # 全角括號的 `藥師（一）`，它是 `normalize_category_name` 折掉的，而不是這一支自己比對出來的。
    rows = [_row("k-1", 1, "藥師"), _row("k-2", 2, "藥師(一)"), _row("k-3", 3, "藥師(二)"),
            _row("k-4", 4, "藥師（一）"), _row("k-5", 5, "醫師(二)")]
    ui = _queue(tmp_path / "queue", rows)
    by_group = scan.selected_questions(ui, [PHARMACIST_TRACK_FILTER]).rows
    by_names = scan.selected_questions(ui, list(PHARM)).rows
    assert [r["candidate_key"] for r in by_group] == [r["candidate_key"] for r in by_names] \
        == ["k-1", "k-2", "k-3", "k-4"]


# ------------------------------------------------------------------ 4: a dry run writes nothing

def test_a_dry_run_writes_nothing_and_does_not_reach_the_endpoint(monkeypatch, tmp_path, capsys):
    root = tmp_path / "queue"
    ui = _queue(root, [_row("k-pharm", 1, "藥師(二)")])
    store = os.path.join(ui, ai_findings.STREAM)

    def refuse(*_args, **_kwargs):
        raise AssertionError("dry run 不該送模型")

    monkeypatch.setattr(ask_about_blocks, "ask", refuse)

    assert _run(monkeypatch, "--queue", str(root), "--category", PHARMACIST_TRACK_FILTER) == 0
    out = capsys.readouterr().out
    assert "符合 1 題" in out, "dry run 要印出選到幾題（這是它唯一的回報）"
    assert not os.path.exists(store), "預設是 dry run：findings 一個位元組都不該被寫出來"
    assert not os.path.exists(os.path.join(ui, "crops")), "dry run 不拍截圖"

    # 已經有紀錄的 queue 也一樣：不是「沒有東西可寫」，是「不寫」——所以比位元組，不是比存在。
    ai_findings.append(store, {"candidate_key": "k-pharm", "population": "dispute",
                               "reading_sha256": "x"})
    before = open(store, "rb").read()
    assert _run(monkeypatch, "--queue", str(root), "--category", "藥師(二)", "--dry-run") == 0
    assert open(store, "rb").read() == before, "dry run 要與執行前位元組完全相同"


# ------------------------------------------------------------------ 5: resume

def test_skip_confirmed_resumes_where_the_batch_stopped(monkeypatch, tmp_path, capsys):
    # 一批 2000 題跑十幾個小時，會被中斷（換裝置、重開機、額度用完）。續跑的工具是
    # `--skip-confirmed`，而它跳過的條件是**現在的讀法**已經有紀錄：讀法一變（有人真的改過文字）
    # 那一題自己回來，不必有人記得去清一個旗標——這是不會安靜吃掉題目的那一半。
    root = tmp_path / "queue"
    rows = [_row("k-1", 1, "藥師"), _row("k-2", 2, "藥師(一)")]
    ui = _queue(root, rows)
    store = os.path.join(ui, ai_findings.STREAM)
    _stub_paper(monkeypatch, tmp_path)
    calls = _stub_page_read(monkeypatch, _seen(rows[0]))

    batch = ["--queue", str(root), "--category", PHARMACIST_TRACK_FILTER, "--apply",
             "--skip-confirmed", "--limit", "1"]
    assert _run(monkeypatch, *batch) == 0
    assert [r["candidate_key"] for r in ai_findings.load(store)] == ["k-1"]

    # 第二批：`--limit` 是在跳過之後才切的，所以它選到的是下一題還沒讀的題目。
    assert _run(monkeypatch, *batch) == 0
    assert [r["candidate_key"] for r in ai_findings.load(store)] == ["k-1", "k-2"]
    assert len(calls) == 2, "同一個讀法不該被問第二次"

    # 負控制：文字變了，指紋就不再符合，那一題重新被讀（第三筆紀錄）。
    rows[0]["stem"] = rows[0]["stem"] + "（重新抽取過）"
    _queue(root, rows)
    assert _run(monkeypatch, *batch) == 0
    assert [r["candidate_key"] for r in ai_findings.load(store)] == ["k-1", "k-2", "k-1"]
    assert len(calls) == 3

    out = capsys.readouterr().out
    assert "符合 2 題" in out and "跳過已讀 1 題" in out and "讀完 1 題" in out, \
        "每一輪要印出 matched／skipped／read，否則中斷後沒人知道剩下多少：%r" % out[-400:]


def test_a_failed_page_read_is_not_a_skip(tmp_path):
    """**負控制：一筆失敗的紀錄不是「讀過」。**

    每一條錯誤路徑都還是寫一筆 finding（`finding_from` 的 `error` 分支），所以「有紀錄」與
    「有讀到」是兩個問題；用前者的話，一次端點故障會讓那幾題永遠不再被問——`confirmed_keys`
    的註解記著這個實測（五題讀不到的題目曾被標成完成）。
    """
    row = _row("k-1", 1, "藥師")
    ui = _queue(tmp_path / "queue", [row])
    store = os.path.join(ui, ai_findings.STREAM)
    fingerprint = ai_findings.reading_fingerprint(row)
    ai_findings.append(store, {"candidate_key": "k-1", "population": scan.POPULATION,
                               "reading_sha256": fingerprint, "error": "request failed",
                               "finding": {"verdict": None, "error": "request failed",
                                           "transcription": None}})
    assert scan.confirmed_keys(store) == {}, "讀不到不是讀過"

    # 另一半：真的讀到的（即使紙本與抽取一致，`changes` 是空的）就算讀過。
    ai_findings.append(store, {"candidate_key": "k-1", "population": scan.POPULATION,
                               "reading_sha256": fingerprint, "error": None,
                               "finding": {"verdict": "OK", "transcription": {"stem": row["stem"]}}})
    assert scan.confirmed_keys(store) == {"k-1": fingerprint}


# ------------------------------------------------------------------ 6: the label, and one read path

def test_what_is_written_is_a_category_scan_and_not_a_dispute(monkeypatch, tmp_path):
    root = tmp_path / "queue"
    row = _row("k-1", 1, "藥師(二)")
    ui = _queue(root, [row])
    store = os.path.join(ui, ai_findings.STREAM)
    _stub_paper(monkeypatch, tmp_path)
    _stub_page_read(monkeypatch, _seen(row))
    assert _run(monkeypatch, "--queue", str(root), "--category", "藥師(二)", "--apply") == 0

    records = ai_findings.load(store)
    assert len(records) == 1
    record = records[0]
    assert record["population"] == scan.POPULATION == "category-scan"
    # `apply_dispute_repairs.py` 只取 population == "dispute"；掃描的讀法是 advisory，套用與否是
    # 人與那一步的事（GOV-05：這一支不寫 review event、不改題目）。
    assert record["population"] != "dispute"
    assert "action" not in record and "reviewer" not in record
    assert record["reading_sha256"] == ai_findings.reading_fingerprint(row)
    # 同一條讀法的證據一起留下來：截圖（model 看的那一張）與機械差異（`changes` 是空的＝紙本與
    # 抽取一致，不是「沒讀到」——後者會是 `error`）。
    assert record["crop"].replace(os.sep, "/") == "review-ui/crops/藥理學/q001-dispute.png"
    assert not record["changes"] and record["error"] is None
    assert record["finding"]["transcription"] is not None
    # 送出去的那一段提示詞就是 `transcribe_system` 那一段——同一個函式，不是第二份組裝。
    assert record["prompt_system"] == confirm_dispute.transcribe_system()


def test_the_reviewers_approved_principles_reach_the_scan(monkeypatch, tmp_path):
    # 這個工具的存續理由：新整理好的基本原則要作用在**整科**上。原則進提示詞的路只有一條
    # （`transcribe_system` 接在 `reread.SYSTEM` 後面），而沒被核准的不進去——畫面上那個「待你
    # 核准」的標記若同時被送出去，它就是一句謊。
    root = tmp_path / "queue"
    row = _row("k-1", 1, "藥師(二)")
    approved = "分數的上標要看紙本，不要自己排版"
    pending = "還在等人點頭的原則"
    _queue(root, [row], principles=[
        {"schema": "qbr_review_principle_v0.1", "action": "add", "principle_id": "p1",
         "text": approved},
        {"action": "approve", "principle_id": "p1", "reviewer": "local"},
        {"schema": "qbr_review_principle_v0.1", "action": "add", "principle_id": "p2",
         "text": pending}])
    store = os.path.join(os.path.join(str(root), "review-ui"), ai_findings.STREAM)
    _stub_paper(monkeypatch, tmp_path)
    _stub_page_read(monkeypatch, _seen(row))
    assert _run(monkeypatch, "--queue", str(root), "--category", "藥師(二)", "--apply") == 0

    record = ai_findings.load(store)[0]
    assert approved in record["prompt_system"]
    assert pending not in record["prompt_system"]
    assert record["principles"] == [approved]



def test_machine_authored_answers_do_not_reach_category_scan_prompt(monkeypatch, tmp_path, capsys):
    root = tmp_path / "queue"
    row = _row("k-1", 1, "藥師(二)")
    ui = _queue(root, [row])
    questions_path = os.path.join(ui, discuss.REPAIR_QUESTIONS_STREAM)
    for event in (
        {"action": "ask", "question_id": "rq-human", "candidate_key": "k-1",
         "question": "人的問題"},
        {"action": "answer", "question_id": "rq-human", "candidate_key": "k-1",
         "answer": "人的回答只作參考", "reviewer": "local"},
        {"action": "ask", "question_id": "rq-machine", "candidate_key": "k-2",
         "question": "代理的問題"},
        {"action": "answer", "question_id": "rq-machine", "candidate_key": "k-2",
         "answer": "機器整理的答案", "reviewer": "repair_experience_apply"},
    ):
        discuss.append_event(questions_path, event)
    store = os.path.join(ui, ai_findings.STREAM)
    _stub_paper(monkeypatch, tmp_path)
    _stub_page_read(monkeypatch, _seen(row))
    assert _run(monkeypatch, "--queue", str(root), "--category", "藥師(二)", "--apply") == 0

    record = ai_findings.load(store)[0]
    assert "人的回答只作參考" in record["prompt_system"]
    assert "機器整理的答案" not in record["prompt_system"]
    assert "審題者的回答 1 筆" in capsys.readouterr().out


# ------------------------------------------------------------------ the flags the station uses

def test_the_report_counts_per_category_and_only_this_pass(monkeypatch, tmp_path, capsys):
    # 中斷之後要判斷「還剩多少」，看的就是這份報告。它數的是**整科掃描**的紀錄，而且各考別分開數
    # ——爭議確認寫在同一條流裡（`population=dispute`），把它算進來會讓進度看起來比實際多。
    # 這個模式也不需要 `--category`：報告讀的是整份紀錄（那個參數是選題用的）。
    root = tmp_path / "queue"
    ui = _queue(root, [_row("k-1", 1, "藥師(二)")])
    store = os.path.join(ui, ai_findings.STREAM)
    for key, category, changes, error in (("k-1", "藥師(二)", [], None),
                                          ("k-2", "藥師(二)", [{"field": "stem"}], None),
                                          ("k-3", "藥師", [], "request failed")):
        ai_findings.append(store, {"candidate_key": key, "population": scan.POPULATION,
                                   "category": category, "changes": changes or None,
                                   "error": error, "prompt_version": "abc123"})
    ai_findings.append(store, {"candidate_key": "k-9", "population": "dispute", "category": "藥師"})

    assert _run(monkeypatch, "--queue", str(root), "--report") == 0
    out = capsys.readouterr().out
    assert "整科掃描 3 筆" in out and "dispute" not in out
    assert "藥師(二)" in out and "不一致" in out and "讀不到" in out
    assert "abc123" in out, "報告要說得出這批是用哪一版的原則讀的"


def test_the_help_lists_the_flags_the_station_command_uses(tmp_path):
    # 一支只有人手跑得動的腳本等於沒有腳本：`--help` 就是它的介面，而這些旗標是站上那條指令的
    # 全部。旗標被改名或拿掉時，這條測試要失敗，而不是等到那條指令在站上跑不起來。
    finished = subprocess.run(
        [sys.executable, os.path.join(PKG, "scripts", "scan_category_principles.py"), "--help"],
        capture_output=True, text=True)
    assert finished.returncode == 0, finished.stderr
    for flag in ("--queue", "--category", "--limit", "--skip-confirmed", "--dry-run", "--apply",
                 "--out", "--report"):
        assert flag in finished.stdout, "%s 不在 --help 裡" % flag
