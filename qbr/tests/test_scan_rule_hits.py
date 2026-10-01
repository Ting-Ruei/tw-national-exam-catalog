# -*- coding: utf-8 -*-
"""scan_rule_hits：deterministic 規則命中的形（workplan 3.2）。

每個鏡頭帶負控制——「必須命中」的旁邊都放一個「在舊行為下會被算進去」的形，
以及「已知良好」不得出現在命中的斷言。全部用合成的小佇列（不碰 live、不碰帳本）。
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))

#: 與 `scan_rule_hits.py` 載姊妹支同一著：repo 根與 qbr 根各有 `scripts` namespace，
#: 名字解析隨進入點漂移；以檔案路徑載入＝只認「哪一份程式」。
_scan_path = os.path.join(PKG, "scripts", "scan_rule_hits.py")
_scan_spec = importlib.util.spec_from_file_location("_qbr_scan_rule_hits", _scan_path)
scan_mod = importlib.util.module_from_spec(_scan_spec)
sys.modules.setdefault(_scan_spec.name, scan_mod)
_scan_spec.loader.exec_module(scan_mod)


def row(key, *, number=1, refs=None, stem="", options=None, pdf_relative=None, **extra):
    # options=[] 要保持真的空（不併回預設 A/B）——skip 案例靠它驗「欄位不存在」。
    if options is None:
        options = [("A", "無"), ("B", "無")]
    base = {
        "candidate_key": key,
        "question_number": number,
        "stem": stem,
        "options": [{"key": k, "text": t} for k, t in options],
        "image_refs": refs if refs is not None else [],
        "metadata": ({"question_pdf_relative": pdf_relative} if pdf_relative else {}),
    }
    base.update(extra)
    return base


def event(key, *, reviewer="local", correction=None, changes=None, applied=None):
    body = {"candidate_key": key, "reviewer": reviewer, "action": "comment"}
    if correction is not None:
        body["correction"] = correction
    if changes is not None:
        body["changes"] = changes
    if applied is not None:
        body["applied"] = applied
    return body


@pytest.fixture
def mini(tmp_path):
    """合成佇列＋裁片樹＋帳本；回 (candidates, events, crops_root) 三個路徑。"""
    crops = tmp_path / "crops"
    run = "1072_藥師(一)_藥劑學與生物藥劑學"
    run_dir = crops / run
    run_dir.mkdir(parents=True)
    (run_dir / "q051_embedded-image.png").write_bytes(b"\x89PNG fake")
    return run, crops


def write_events(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


# ------------------------------------------------------------------ figure-ref-missing

def test_figure_lens_hits_crops_without_refs_and_spares_refs_present(mini):
    run, crops = mini
    rows = [
        # 裁片在、refs 空 → 命中
        row("moex:x:q50", number=51, pdf_relative="國考題資料夾/10_official_pdf/by_official_catalog/藥師(一)/107/第2次/%s.pdf" % run),
        # 裁片在、refs 有（09-30 修好的形，如 q051 本尊）→ 不得命中
        row("moex:x:q51", number=51,
            refs=[{"asset_role": "figure-crop", "exists": True,
                   "path": "review-ui/crops/%s/q051_embedded-image.png" % run}],
            pdf_relative="….pdf".replace("….pdf", "國考題資料夾/…/%s.pdf" % run)),
        # refs 空、裁片不在 → 不得命中（無圖是常態，不是缺失）
        row("moex:x:q09", number=9,
            pdf_relative="國考題資料夾/…/%s.pdf" % run),
    ]
    crops_by_run = scan_mod.crop_numbers_for_run(crops)
    assert crops_by_run[run] == {51}

    hits = []
    for one in rows:
        hits.extend(h for h in scan_mod.lens_hits(one, crops_by_run) if h["lens"] == scan_mod.LENS_FIGURE)
    assert [h["candidate_key"] for h in hits] == ["moex:x:q50"], "只有「裁片在、refs 空」命中"
    assert hits[0]["evidence"]["crop_pattern"] == "q051_*"


def test_figure_lens_negative_control_on_old_behaviour(mini):
    """舊行為的形：把「refs 存在」也算缺失，q051 會被誤報——現行實作必須拒。"""
    run, crops = mini
    good = row("moex:x:q51", number=51,
               refs=[{"exists": True}],
               pdf_relative="國考題資料夾/…/%s.pdf" % run)
    crops_by_run = scan_mod.crop_numbers_for_run(crops)
    figure_hits = [h for h in scan_mod.lens_hits(good, crops_by_run) if h["lens"] == scan_mod.LENS_FIGURE]
    assert figure_hits == [], "refs 在的列（1.1 剛修好的形）絕不能進 figure 命中"


# ------------------------------------------------------------------ superscript-family

def test_superscript_lens_reports_tokens_by_field_and_classifies_ledger_fixes(mini, tmp_path):
    run, crops = mini
    candidates = tmp_path / "candidates.jsonl"
    events = tmp_path / "events.jsonl"
    # fresh：stem 帶家族 token；ledger 已修：correction 文字含 <sub>
    rows = [
        row("moex:y:q10", number=10, stem="使用GABAA受體致效劑，其親和力較高。",
            pdf_relative="國考題資料夾/…/%s.pdf" % run),
        row("moex:y:q11", number=11, stem="半衰期 t1/2 為 4 小時，其 VD 值約為 40 L。",
            pdf_relative="國考題資料夾/…/%s.pdf" % run),
        # 負控制：普通數字「100」「1000」是數量，不是 10 的指數記法
        row("moex:y:q13", number=13, options=[("C", "濃度為 100 mg/mL，體積 1000 mL")],
            pdf_relative="國考題資料夾/…/%s.pdf" % run),
        row("moex:y:q12", number=12, stem="下列何者正確？",
            pdf_relative="國考題資料夾/…/%s.pdf" % run),
    ]
    write_events(events, [
        event("moex:y:q11", reviewer="local",
              correction={"stem": "半衰期 t<sub>1/2</sub> 為 4 小時，其 V<sub>D</sub> 值約為 40 L。"}),
    ])
    candidates.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    result = scan_mod.scan(candidates, events, None, lenses=(scan_mod.LENS_SUPER,))
    # 掃描回報**磁碟的真**：q10 全新命中、q11 文字層仍是未修文字（帳本有 correction 但沒折進
    # 檔）也算命中——分類欄 `ledger_has_correction` 讓下游知道這題要「重折」而非「新修」。
    by_key = {h["candidate_key"]: h for h in result["hits"]}
    assert set(by_key) == {"moex:y:q10", "moex:y:q11"}, "無 token 與普通數字（q12/q13）都不命中"
    assert by_key["moex:y:q10"]["ledger_has_correction"] is False
    assert by_key["moex:y:q11"]["ledger_has_correction"] is True
    all_tokens = {t for h in result["hits"] for t in h["evidence"]["tokens"]}
    assert "GABAA" in all_tokens
    assert any(("t1/2" in t or "VD" in t) for t in all_tokens)
    assert result["summary"]["superscript_fixed_keys_in_ledger"] == 1


# ------------------------------------------------------------------ apostrophe-width

def test_apostrophe_lens_flags_curly_not_halfwidth(mini):
    run, crops = mini
    crops_by_run = scan_mod.crop_numbers_for_run(crops)
    rows = [
        row("moex:z:q1", number=1, stem="Parkinson’s disease 的治療"),  # 彎撇號 → 命中
        row("moex:z:q2", number=2, stem="Kjeldahl's method 的步驟"),    # 半形 → 不命中
    ]
    for one in rows:
        hits = [h for h in scan_mod.lens_hits(one, crops_by_run) if h["lens"] == scan_mod.LENS_APOSTROPHE]
        if one["candidate_key"] == "moex:z:q1":
            assert hits and hits[0]["evidence"]["field"] == "stem"
        else:
            assert hits == [], "半形撇號是紙本忠實形，不是命中"


# ------------------------------------------------------------------ rare-codepoint

def test_rare_codepoint_lens_flags_lookalike_glyphs(mini):
    run, crops = mini
    crops_by_run = scan_mod.crop_numbers_for_run(crops)
    bad = row("moex:w:q1", number=1, stem="微常數՝列於下方，數值Ә為…)")  # ՝ U+055D、Ә U+04D9
    ok = row("moex:w:q2", number=2, stem="微常數列於下方，數值為 0.5。")
    hits_bad = [h for h in scan_mod.lens_hits(bad, crops_by_run) if h["lens"] == scan_mod.LENS_RARE]
    assert hits_bad, "亂碼形（՝、Ә）必須命中"
    chars = {item["char"] for h in hits_bad for item in h["evidence"]["codepoints"]}
    assert chars == {"՝", "Ә"}
    assert not [h for h in scan_mod.lens_hits(ok, crops_by_run) if h["lens"] == scan_mod.LENS_RARE], \
        "正常中文列不得命中"


# ------------------------------------------------------------------ corrections-not-in-text

def test_corrections_lens_quantifies_what_the_rebuild_lost(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    events = tmp_path / "events.jsonl"
    rows = [
        # applied：磁碟文字 == 帳本 correction 文字 → 不是命中
        row("moex:a:q1", number=1, stem="固定的正確文字。"),
        # lost：磁碟還是修前原文 → 命中（重灌洗掉）
        row("moex:a:q2", number=2, stem="GABAA受體的敘述，錯的那一份。"),
        # 欄位不存在 → skip（明講）：磁碟列沒有 A 欄，帳本卻要寫它
        row("moex:a:q3", number=3, stem="只有題幹。", options=[]),
    ]
    write_events(events, [
        event("moex:a:q1", reviewer="local", correction={"stem": "固定的正確文字。"}),
        event("moex:a:q2", reviewer="local", correction={"stem": "GABA<sub>A</sub>受體的敘述，對的那一份。"}),
        event("moex:a:q3", reviewer="local", correction={"stem": "只有題幹。", "options": [{"key": "A", "text": "磁碟上沒有這一欄"}],
                                            }),  # 帳本帶 A 欄、磁碟無 A 欄 → skip
    ])
    candidates.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    result = scan_mod.scan(candidates, events, None)
    summary = result["summary"]
    # q1 整欄套用、q3 的 stem 也與帳本一致（第三列只是少了帳本要寫的 A 欄→skip 記兩種狀態）
    assert summary["corrections_applied_in_text"] == 2
    assert summary["corrections_not_in_text"] == 1
    assert summary["correction_fields_without_column"] == 1
    lost_keys = {h["candidate_key"] for h in result["hits"] if h["lens"] == scan_mod.LENS_CORRECTIONS}
    assert lost_keys == {"moex:a:q2"}, "套用中的 q1 與 skip 的 q3 都不是命中"
    lost_hit = [h for h in result["hits"] if h["candidate_key"] == "moex:a:q2"][0]
    assert lost_hit["ledger_reviewer"] == "local", "hit 帶事件來源（誰寫的 correction）"
    assert lost_hit["ledger_kind"] == "correction"


# ------------------------------------------------------------------ CLI + 已知良好鍵的缺席

def test_cli_writes_per_lens_files_and_refuses_nothing_on_empty(tmp_path):
    import subprocess

    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(json.dumps(
        row("moex:cli:q1", number=1, stem="GABAA受體。",
            pdf_relative="國考題資料夾/…/1072_藥師(一)_藥劑學與生物藥劑學.pdf")
    ) + "\n", encoding="utf-8")
    out = tmp_path / "scans"
    proc = subprocess.run(
        [sys.executable,  # this suite's interpreter; a relative `.venv` made the subprocess checkout-bound
         os.path.join(PKG, "scripts", "scan_rule_hits.py"),
         "--candidates", str(candidates), "--events", str(tmp_path / "no-events.jsonl"),
         "--crops", str(tmp_path / "no-crops"), "--out", str(out)],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert (out / "superscript-family.jsonl").exists()
    assert (out / "superscript_fixed_keys_in_ledger.txt").exists()
    lines = (out / "superscript-family.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0])["candidate_key"] == "moex:cli:q1"


def test_live_scan_known_good_key_absent_from_figure_hits():
    """對 1.1 已修好的 1072 q051 斷言缺席——用真 live 佇列（存在才跑）。"""
    live = Path(PKG) / "data/review-queues/live/review-ui"
    candidates = live / "candidates.jsonl"
    if not candidates.is_file():
        pytest.skip("no live snapshot on this machine")
    result = scan_mod.scan(candidates, live / "question_review_events.jsonl", live / "crops",
                           lenses=(scan_mod.LENS_FIGURE,))
    figure_hits = {h["candidate_key"] for h in result["hits"]}
    assert "moex:107100:305:33:1:question:q051" not in figure_hits, \
        "refs 已修復的 1072 q051（裁片在、refs 在）不得出現在 figure 缺失清單"
    assert result["summary"]["hits_per_lens"][scan_mod.LENS_FIGURE] >= 0