#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic rule-hit scan（workplan 3.2）。**唯讀**：不寫佇列、不寫帳本、不呼叫模型。

為什麼要有這一支（2026-09-30）
--------------------------------
規則家的治療閉環缺「量」：規則寫好了，但沒有人量它今天在庫裡對應到多少題。
§9.11-7 的治理（命中率＋壓縮 pass）與 4.1 的存量清理都吃同一份量測——本支就是那個量測：
deterministic、可重跑、同輸入同輸出（幾分鐘級；79,090 列單次掃）。

五個鏡頭（lens）
----------------
1. ``figure-ref-missing``（驗收鏡頭）：**裁片在、refs 空**——run 目錄裡有這題（``qNNN_*``）的
   裁片檔，候選列的 ``image_refs`` 卻是空的。這是「如下圖引用缺失」的 deterministic 形狀：
   頁面證據已在磁碟上，文字層卻沒有引用它。
2. ``superscript-family``：p18（合併版）說的「大寫字母緊接數字或字母」token 出現在文字層。
   Token 集是規則的**predicate 投影**（見 ``SUPERSCRIPT_PATTERNS``，附推導來源），不是第二份
   規則；每個命中都带 token 明細，讓下一關（抽樣／紙本核對）有得查。帳本裡含
   ``<sub>/<sup>`` 的 correction＝已修紀錄，另外計（``superscript_fixed_keys``），不混進 fresh。
3. ``apostrophe-width``：拉丁字相鄰的彎/全形撇號（p5 半形/全形規則的形狀）。
4. ``rare-codepoint``：白名單外的碼點（p7/p11 的亂碼字形），逐列列碼點與次數。
5. ``corrections-not-in-text``：帳本最新 correction 的欄位文字與磁碟不符——09-27/29 重灌把
   已折進檔的修正洗掉的那一類，這裡量化它（4.1 的素材）。

負控制（self-check，隨掃印出，也寫進單元測）
--------------------------------------------
- figure 鏡頭：任何命中的列**必然** refs 為空且裁片檔存在；已知有圖有 refs 的列（1072 q051）
  不允許出現在命中裡。
- superscript 鏡頭：已修 keys 不進 fresh（它們的證據在帳本的 correction 文字裡）。
- corrections 鏡頭：欄位文字與帳本一致＝``applied``（不是命中）；欄位不存在＝``skip``（明講，
  不算命中也不算 applied）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

#: 同目錄姊妹支的折疊邏輯（``latest_revisions``／``correction_fields``／``row_field``／``spaced``）
#: 以**檔案路徑**載入，不走 ``scripts.*`` 套件名：repo 根與 qbr 根各有一個 ``scripts`` 目錄（都是
#: namespace package），名字解析會隨進入點漂移（量於 3.2 的第一次 CLI 測：pytest 時解析到 qbr 端、
#: 直接跑時解析到 repo 端而找不到本檔）。檔案路徑只認「哪一份程式」，不認呼叫者怎麼進來的。
#: 模組載入時它自己會把 repo 根放進 ``sys.path``，其 ``scripts.serve_question_review_ui`` 的
#: 匯入照常成立。
import importlib.util  # noqa: E402

_atc_path = os.path.join(HERE, "apply_text_corrections.py")
_atc_spec = importlib.util.spec_from_file_location("_qbr_apply_text_corrections", _atc_path)
assert _atc_spec and _atc_spec.loader, "apply_text_corrections.py must sit beside this script"
_atc = importlib.util.module_from_spec(_atc_spec)
sys.modules.setdefault(_atc_spec.name, _atc)
_atc_spec.loader.exec_module(_atc)

latest_revisions = _atc.latest_revisions
correction_fields = _atc.correction_fields
row_field = _atc.row_field
spaced = _atc.spaced
APPLIED_WITHDRAWN = _atc.APPLIED_WITHDRAWN

#: 鏡頭名，輸出檔與 summary 的鍵都是這些。
LENS_FIGURE = "figure-ref-missing"
LENS_SUPER = "superscript-family"
LENS_APOSTROPHE = "apostrophe-width"
LENS_RARE = "rare-codepoint"
LENS_CORRECTIONS = "corrections-not-in-text"
LENS_ALL = (LENS_FIGURE, LENS_SUPER, LENS_APOSTROPHE, LENS_RARE, LENS_CORRECTIONS)

#: 邊界用 lookaround 而非 ``\b``：CJK 字元也是 ``\w``，「用GABAA受體」裡的 token 前後沒有
#: 「non-word」可借——量測 3.2 第一次單元測（CJK 夾住的 token 全漏）。要拒的是**拉丁/數字的
#: 延伸**（GABA_Av、KM1x 這種把它讀成另一個 token 的形），不是中文鄰接。
_B = r"(?<![A-Za-z0-9])"
_E = r"(?![A-Za-z0-9])"

#: p18 predicate 投影。推導：合併條文的例子（受體/酵素、藥動參數、化學/取代基、指數、物理式）
#: ＋家族各代（p8/p9/p13/p14/p15/p16/p17）union。**形狀模式**，逐 token 報告，FP 由
#: rows-per-hit 密度呈現、由抽樣關判讀——本支不猜語意。
SUPERSCRIPT_PATTERNS = [
    # 受體/酵素/亞型：GABAA、GABAB、MAOA、5-HT1A、D2、A2A（2個以上大寫字母後接數字/字母）
    re.compile(_B + r"(?:GABA[A-E]?|MAO[AB]|5-HT[0-9][A-E]?|[A-Z]{2,4}[0-9]{1,2}[A-C]?)" + _E),
    # 藥動參數（家族 union 的具名集）
    re.compile(_B + r"(?:Vmax|Cmax|Cmin|Css|Cp|AUC[0-9-]*|CLcr|CLr|CLh|CL[RH]|ERh|VD|Vd|KM|Km|K_M|"
               r"V_D|HbA1C|Ctrough|C_trough|ke|ka|kel|k21|kA|kE|t1/2|t_1/2|pKa|pKb|f_u|f_e|MWdextrose)" + _E),
    # 指數/冪次：e-0.2t、hr-1、hr^-1、10^-3（10 後不帶 ^ 的不收——「100」這種數字不是 token）
    re.compile(_B + r"(?:e-?0\.[0-9]+t?|hr\^?-1|min-?1|10\^[+-]?[0-9]+)" + _E),
    # 物理式下標：H_b、sin r_c（底線記法）
    re.compile(_B + r"(?:H_[a-z]|sin r_[a-z]|GPIIb/IIIa)" + _E),
]

#: 拉丁字相鄰的彎/全形撇號（p5：紙本半形，文字層存成這些＝嫌疑）。
APOSTROPHE_PATTERN = re.compile(r"[A-Za-z][\u2018\u2019\u02BC\uFF07][A-Za-z]|[A-Za-z][\u2018\u2019\u02BC\uFF07]\b")

#: 碼點白名單之外的才報（p7/p11 的亂碼形）。區間與理由：
#:
#: - ASCII 可印；CJK 主要面（4E00–9FFF）與擴展 A（3400–4DBF，學名用字有出現）；
#: - CJK 標點 3000–303F、全形 0xFF00–0xFFEF、表意空格；
#: - 希臘 0370–03FF（劑量單位 α/β/γ）、拉丁-1 00A0–00FF（°±×÷、重音字母）；
#: - 一般標點 2010–205F、算符 2190–22FF（≤≥→×）、上下標 2070–209F（¹²³）、Letterlike
#:   2100–214F（℃ ℉ ℓ 醫學單位字，普查後屬紙本忠實形）；
#: - 圈圈數字 2460–24FF（列舉序號 ①②③，2026-09-30 全庫普查：佔 rare 鏡頭的 90%＋，屬
#:   紙本忠實形，不是亂碼）；相容漢字 F900–FAFF（列/不/療 的相容字形，同上普查）。
CODEPOINT_WHITELIST = (
    [(0x20, 0x7E), (0x00A0, 0x00FF), (0x0370, 0x03FF), (0x2010, 0x205F),
     (0x2070, 0x209F), (0x2100, 0x214F), (0x2190, 0x22FF), (0x2460, 0x24FF), (0x3000, 0x303F),
     (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF)]
)


def rare_codepoints(text: str) -> list[str]:
    """欄位文字裡白名單外的碼點（去重、按出現序）。"""
    seen: list[str] = []
    for ch in str(text or ""):
        cp = ord(ch)
        if cp in (0x0A, 0x0D, 0x09):
            continue
        if any(lo <= cp <= hi for lo, hi in CODEPOINT_WHITELIST):
            continue
        if ch not in seen:
            seen.append(ch)
    return seen


def crop_numbers_for_run(crops_root: Path) -> dict[str, set[int]]:
    """run 目錄名 -> 該 run 中出現過的題號（int，統一 ``q7``／``q007`` 的補零差異）。

    裁片檔名是 ``qNNN_<label>.png``（量於 2.5/1.1 演練）；run 目錄名＝紙本檔名去副檔名。
    只看一層；子目錄不存在＝沒有證據，不是錯誤。
    """
    index: dict[str, set[int]] = {}
    if not crops_root.is_dir():
        return index
    for run_dir in crops_root.iterdir():
        if not run_dir.is_dir():
            continue
        numbers: set[int] = set()
        for entry in run_dir.iterdir():
            match = re.match(r"q(\d{2,4})_", entry.name)
            if match:
                numbers.add(int(match.group(1)))
        index[run_dir.name] = numbers
    return index


def run_name_of(row: dict) -> str | None:
    """候選列的裁片 run 目錄名（紙本檔名去副檔名），或 None。"""
    metadata = row.get("metadata") or {}
    relative = metadata.get("question_pdf_relative")
    if not relative:
        return None
    return os.path.splitext(os.path.basename(str(relative)))[0]


def row_texts(row: dict) -> dict[str, str]:
    """會被規則檢查的文字欄位：stem、shared_stem（題組共用題幹）、各選項 text。"""
    texts = {"stem": str(row.get("stem") or "")}
    if row.get("shared_stem"):
        texts["shared_stem"] = str(row["shared_stem"])
    options = row.get("options") or []
    if isinstance(options, dict):
        options = list(options.items())
    for option in options:
        if isinstance(option, dict):
            key = str(option.get("key") or "").strip().upper()[:1] or "?"
            texts["option %s" % key] = str(option.get("text") or "")
    return texts


def lens_hits(row: dict, crops_by_run: dict[str, set[str]]) -> list[dict]:
    """鏡頭 1–4（每列的 deterministic 形狀檢查）；鏡頭 5 在 scan() 裡跨檔折疊。"""
    hits: list[dict] = []

    run = run_name_of(row)
    number = str(row.get("question_number") or "")
    try:
        number_int = int(number)
    except ValueError:
        number_int = None
    refs = row.get("image_refs") or []
    if (not refs) and run and number_int is not None and number_int in crops_by_run.get(run, set()):
        hits.append({
            "lens": LENS_FIGURE,
            "candidate_key": row.get("candidate_key"),
            "evidence": {"run": run, "question_number": number,
                         "crop_pattern": "q%03d_*" % number_int},
        })

    for field, text in row_texts(row).items():
        tokens: list[str] = []
        for pattern in SUPERSCRIPT_PATTERNS:
            tokens.extend(match.group(0) for match in pattern.finditer(text))
        tokens = sorted(set(tokens))
        if tokens:
            hits.append({
                "lens": LENS_SUPER,
                "candidate_key": row.get("candidate_key"),
                "evidence": {"field": field, "tokens": tokens[:12]},
            })
        apostrophes = sorted(set(APOSTROPHE_PATTERN.findall(text)))
        if apostrophes:
            hits.append({
                "lens": LENS_APOSTROPHE,
                "candidate_key": row.get("candidate_key"),
                "evidence": {"field": field, "samples": apostrophes[:6]},
            })
        oddies = rare_codepoints(text)
        if oddies:
            hits.append({
                "lens": LENS_RARE,
                "candidate_key": row.get("candidate_key"),
                "evidence": {"field": field,
                             "codepoints": [{"char": ch, "u+": "U+%04X" % ord(ch)} for ch in oddies[:8]]},
            })
    return hits


#: （裁片檔名層級的細節證據由命中後的下一關再查；掃描期不重開目錄，維持單次線性成本。）
#: 負控制不放進本支的執行路徑：它們是測試與「對已知良好鍵斷言缺席」的事後檢查（見測試檔）。


def scan(candidates_path: Path, events_path: Path | None, crops_root: Path | None,
         lenses: tuple[str, ...] = LENS_ALL) -> dict:
    """掃全庫：回 summary（純資料，可印可存）。**不寫任何檔。**"""
    revisions = latest_revisions(events_path) if (events_path and events_path.exists()) else {}
    crops_by_run = crop_numbers_for_run(crops_root) if (crops_root and crops_root.exists()) else {}

    hits: list[dict] = []
    applied = 0
    lost = 0
    partial = 0
    skipped_fields = 0
    rare_census: Counter = Counter()
    fixed_keys: set[str] = set()
    row_count = 0
    lens_rows = defaultdict(set)

    with candidates_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            row_count += 1
            key = row.get("candidate_key") or ""

            revision = revisions.get(str(key))
            has_correction = bool(revision and revision.get("kind") != APPLIED_WITHDRAWN)

            for hit in lens_hits(row, crops_by_run):
                if lenses and hit["lens"] not in lenses:
                    continue
                # 帳本狀態跟著每一個 hit 走：這一列有沒有 correction（kind 非 withdrawn），
                # 下游（草稿階段）用它分「重折舊修正」與「全新命中」——掃描期不替任何一關判斷。
                hit["ledger_has_correction"] = has_correction
                if revision:
                    hit["ledger_kind"] = revision.get("kind")
                    hit["ledger_reviewer"] = str(revision["event"].get("reviewer") or "")
                hits.append(hit)
                lens_rows[hit["lens"]].add(key)
                if hit["lens"] == LENS_RARE:
                    rare_census.update(item["char"] for item in hit["evidence"]["codepoints"])

            if revision and revision.get("kind") != APPLIED_WITHDRAWN:
                fields = correction_fields(revision["event"].get("correction") or {})
                if not fields:
                    continue
                blob = json.dumps(revision["event"].get("correction") or {}, ensure_ascii=False)
                if "<sub>" in blob or "<sup>" in blob:
                    fixed_keys.add(str(key))
                if LENS_CORRECTIONS in lenses:
                    for field, corrected in fields.items():
                        current = row_field(row, field)
                        if current is None:
                            skipped_fields += 1
                            continue
                        if spaced(current) == spaced(corrected):
                            applied += 1
                            continue
                        short, long = sorted([spaced(current), spaced(corrected)], key=len)
                        if short and long and short in long:
                            partial += 1  # 同一行的兩種讀法（長短夾），算未套用但非走樣
                        else:
                            lost += 1
                            hits.append({
                                "lens": LENS_CORRECTIONS,
                                "candidate_key": str(key),
                                "ledger_has_correction": True,
                                "ledger_kind": revision.get("kind"),
                                "ledger_reviewer": str(revision["event"].get("reviewer") or ""),
                                "evidence": {"field": field,
                                             "disk_head": excerpt(current),
                                             "event_head": excerpt(corrected)},
                            })
                            lens_rows[LENS_CORRECTIONS].add(str(key))

    summary = {
        "rows_scanned": row_count,
        "hits_per_lens": {name: len(lens_rows.get(name, set())) for name in (lenses or LENS_ALL)},
        "rows_per_hit": {name: (round(row_count / v, 1) if v else None)
                         for name, v in ((n, len(lens_rows.get(n, set()))) for n in (lenses or LENS_ALL))},
        "corrections_applied_in_text": applied,
        "corrections_partial_in_text": partial,
        "corrections_not_in_text": lost,
        "correction_fields_without_column": skipped_fields,
        "superscript_fixed_keys_in_ledger": len(fixed_keys),
        "top_rare_codepoints": [
            {"char": ch, "u+": "U+%04X" % ord(ch), "rows": rows}
            for ch, rows in rare_census.most_common(12)
        ],
        "sample_keys": {name: sorted(lens_rows.get(name, set()))[:5] for name in (lenses or LENS_ALL)},
    }
    return {"summary": summary, "hits": hits, "fixed_keys": sorted(fixed_keys)}


def excerpt(text: str, limit: int = 40) -> str:
    value = str(text or "").strip().replace("\n", "⏎")
    return value if len(value) <= limit else value[:limit] + "…"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path,
                        default=Path(ROOT) / "qbr/data/review-queues/live/review-ui/candidates.jsonl",
                        help="候選列 JSONL（預設 live 快照）")
    parser.add_argument("--events", type=Path,
                        default=Path(ROOT) / "qbr/data/review-queues/live/review-ui/question_review_events.jsonl",
                        help="人工＋機器決定流（correction 折疊來源）")
    parser.add_argument("--crops", type=Path,
                        default=Path(ROOT) / "qbr/data/review-queues/live/review-ui/crops",
                        help="裁片根（run 目錄一層）")
    parser.add_argument("--lens", action="append", default=[], choices=sorted(LENS_ALL),
                        help="只跑指定鏡頭（可重複）；預設全部")
    parser.add_argument("--out", type=Path, default=None,
                        help="hit 明細輸出目錄（預設不寫檔，只印 summary）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    lenses = tuple(args.lens) if args.lens else LENS_ALL
    result = scan(args.candidates, args.events, args.crops, lenses)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=1))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for lens in LENS_ALL:
            lens_hits_list = [h for h in result["hits"] if h["lens"] == lens]
            target = args.out / ("%s.jsonl" % lens)
            with target.open("w", encoding="utf-8") as handle:
                for hit in lens_hits_list:
                    handle.write(json.dumps(hit, ensure_ascii=False) + "\n")
            print("wrote %s: %d hits" % (target, len(lens_hits_list)))
        fixed = args.out / "superscript_fixed_keys_in_ledger.txt"
        fixed.write_text("\n".join(result["fixed_keys"]) + "\n", encoding="utf-8")
        print("wrote %s: %d keys" % (fixed, len(result["fixed_keys"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())