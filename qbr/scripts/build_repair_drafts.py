#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次修復草稿（workplan 4.1）——「字形類別」這一類的**deterministic** 草案產生器。

量測（2026-09-30 全庫掃描，``scan_rule_hits.py`` 的 rare-codepoint 鏡頭）說了什麼：
白名單調好之後剩下的 3,974 列裡，大頭是**康熙部首字形**（⽤/⾎/⽣… U+2F00–2FDF）、
**相容漢字**（列/不/療… U+F900–FAFF）與**修飾字母 ᵐ**（U+1D50，OCR 把 mg 的 m 排成上標）。
這三類有**逐一碼點、經 Unicode 正規化（NFKC）可逆**的標準形——修法是 deterministic 的，
不需要模型讀紙本；真正需要人工的是「這裡的 Ⅰ/℃/ᵐ 是不是紙本忠實形」那種判斷，那些**不產草案**。

草案寫到哪：``--out`` 指定的沙盒 stores/repair_drafts.jsonl（append-only，schema
``repair_agent_test/repair_drafts v1``，與 bridge 的 propose_repair 同形）。**佇列、帳本、
正式 candidates 一個位元組都不寫**；核可在沙盒 UI 由設計者按；落地＝G3，程式不提供。

身分：``run_id = compute-<日期>``、``prompt_version = deterministic-radix-map-v1``——
這些草案不是任何一次模型對話的產物，憑證明寫這一點（不冒充 agent run）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from collections import Counter
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

#: 有 deterministic 標準形的三個碼點類別：康熙部首、相容漢字、修飾的上標字母。
#: 證據：3.2 的 rare-codepoint 全庫普查 top 10（⽤ 1011 列、⾎ 795、⽣ 780、⼀ 672、
#: ⼈ 498、⼩ 452、⼦ 441、⼤ 439、⾏ 388、⾼ 380；ᵐ 1,468）——全部落在這三類。
#: **NFKC 對這些碼點是碼點對碼點的正規化**（不動字串裡的其他字元——℃→°C 那種分解
#: 不在類別裡，所以整串不會被 NFKC）。
DETERMINISTIC_RANGES = ((0x1D00, 0x1DBF), (0x2F00, 0x2FDF), (0xF900, 0xFAFF))

#: 修飾字母裡有 deterministic 上/下標形的是**字母**；數字的（U+2070 起）在白名單裡。
#: 上面第一個區間 = Phonetic Extensions（ᵐ U+1D50 在這）。
RUN_ID = ""   # 在 main() 解析（env QBR_RUN_DATE 或當下時間）；模組層只是宣告
PROMPT_VERSION = "deterministic-radix-map-v1"


def is_deterministic_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in DETERMINISTIC_RANGES)


def targeted_nfkc(text: str) -> tuple[str, list[dict]]:
    """只正規化三類碼點；回新文字與逐碼點變更字典（含 NFKC 後的形）。

    不在類別裡的字元原樣保留（一字不動），於是全串的 NFKC 副作用（℃→°C、㍿→「…」）
    都被排除。
    """
    out = []
    changes = []
    for ch in str(text or ""):
        if is_deterministic_char(ch):
            norm = unicodedata.normalize("NFKC", ch)
            if norm != ch:
                changes.append({"from": ch, "u+": "U+%04X" % ord(ch),
                                "to": norm, "class": _glyph_class(ch)})
                out.append(norm)
                continue
        out.append(ch)
    return "".join(out), changes


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _glyph_class(ch: str) -> str:
    cp = ord(ch)
    if 0x2F00 <= cp <= 0x2FDF: return "kangxi-radical"
    if 0xF900 <= cp <= 0xFAFF: return "compat-ideograph"
    return "modifier-letter"


def option_field(row: dict, key: str) -> str | None:
    """``option A`` 這種欄位名回到候選列的 options 元素（row_texts 的反函數）。"""
    letter = (key or "").replace("option ", "").strip().upper()[:1] or ""
    if not letter:
        return None
    for option in (row.get("options") or []):
        if isinstance(option, dict) and str(option.get("key") or "").strip().upper().startswith(letter):
            return str(option.get("text") or "")
    return None


def rows_by_key(candidates_path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with candidates_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("candidate_key"):
                rows[str(row["candidate_key"])] = row
    return rows


def build(hits_path: Path, candidates_path: Path) -> tuple[list[dict], Counter]:
    """rare-codepoint 命中 → 草案列（每列帶逐碼點證據）；回（草案列，類別統計）。"""
    rows = rows_by_key(candidates_path)
    drafts: list[dict] = []
    stats: Counter = Counter()
    seen_fields: set[tuple[str, str]] = set()

    with hits_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            hit = json.loads(line)
            key = str(hit.get("candidate_key") or "")
            evidence = hit.get("evidence") or {}
            field = str(evidence.get("field") or "")
            if (key, field) in seen_fields:   # 同題同欄的多個碼點 => 一份草案打包全部
                continue
            row = rows.get(key)
            if row is None:
                stats["row-missing"] += 1     # 帳本與佇列錯位：明講，不猜
                continue
            current = _field_value(row, field)
            if current is None:
                stats["field-missing"] += 1
                continue
            fixed, changes = targeted_nfkc(current)
            if not changes:
                stats["no-deterministic-changes"] += 1
                continue
            seen_fields.add((key, field))
            stats.update(change["class"] for change in changes)
            drafts.append({
                "schema": "repair_agent_test/repair_drafts v1",
                "action": "repair_draft",
                "status": "proposed",
                "candidate_key": row.get("candidate_key"),
                "question_number": row.get("question_number"),
                "subject": (row.get("metadata") or {}).get("normalized_subject_name"),
                "fix": fixed,
                "insert": field,
                "basis": "deterministic 字形正規化（NFKC 逐碼點）：" +
                         ", ".join("%s(%s)→%s" % (c["from"], c["u+"], c["to"]) for c in changes[:8]),
                "crop": None,
                "at": _utc_now(),
                "run_id": RUN_ID,
                "session_id": "",
                "prompt_version": PROMPT_VERSION,
                "changes": changes,
            })
    return drafts, stats


def _field_value(row: dict, field: str) -> str | None:
    if field == "stem":
        return str(row.get("stem") or "")
    if field == "shared_stem":
        return str(row.get("shared_stem") or "") if row.get("shared_stem") is not None else None
    if field.startswith("option "):
        return option_field(row, field)
    return None


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hits", required=True, type=Path,
                        help="3.2 掃描輸出的 rare-codepoint.jsonl")
    parser.add_argument("--candidates", required=True, type=Path, help="候選列 JSONL（只讀）")
    parser.add_argument("--out", required=False, type=Path, default=None,
                        help="草案 JSONL 路徑（沙盒 store 的 repair_drafts.jsonl）；不給只印統計")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    global RUN_ID  # noqa: PLW0603
    # 計算草的身分：沒有 QBR_RUN_DATE 就用當下時間（一次產生一份，重跑＝identical 內容＋新時間，
    # 同（key,field,fix）行會被冪等跳過）。
    RUN_ID = "compute-" + (os.environ.get("QBR_RUN_DATE") or
                           __import__("datetime").datetime.now().strftime("%Y%m%dT%H%M%S"))
    if args.out is None:
        drafts, stats = build(args.hits, args.candidates)
        print(json.dumps({"drafts": len(drafts), "class_counts": dict(stats)}, ensure_ascii=False, indent=1))
        return 0
    drafts, stats = build(args.hits, args.candidates)
    # 草案檔是 **append-only 共享檔**（bridge 的行、消費者的行都在裡面）——tmp+move 會把
    # 別人剛剛追加的行洗掉；這裡用追加寫入，同（key,field,fix）重跑＝跳過（run 級冪等）。
    existing = set()
    if args.out.exists():
        for line in args.out.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            try:
                prior = json.loads(line)
                existing.add((str(prior.get("candidate_key") or ""), str(prior.get("insert") or ""),
                              str(prior.get("fix") or "")))
            except json.JSONDecodeError:
                continue
    args.out.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    with args.out.open("a", encoding="utf-8") as handle:
        for draft in drafts:
            ident = (str(draft["candidate_key"]), str(draft["insert"]), str(draft["fix"]))
            if ident in existing:
                stats["already-present"] += 1
                continue
            handle.write(json.dumps(draft, ensure_ascii=False) + "\n")
            appended += 1
    print(json.dumps({"drafts_seen": len(drafts), "drafts_appended": appended,
                      "class_counts": dict(stats), "out": str(args.out)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())