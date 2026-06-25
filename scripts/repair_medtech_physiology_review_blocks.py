#!/usr/bin/env python3
"""
Repair active review-blocked medical technologist physiology/pathology items.

This script is deliberately annotation-driven: it uses the user's active
block / needs_review notes as the repair boundary, keeps human review states
unchanged, and appends new review events only when a correction overlay must be
updated for the Review UI.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import build_question_candidates_from_mineru as builder


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = PROJECT_ROOT / "國考題資料夾" / "30_normalized_items" / "question_candidates" / "20260620-213413"
DEFAULT_CANDIDATE_PATH = RUN_DIR / "question_candidates__20260620-213413.jsonl"
DEFAULT_ISSUE_PATH = RUN_DIR / "question_parse_issues__20260620-213413.csv"
DEFAULT_REVIEW_LOG = RUN_DIR / "question_review_events.jsonl"

SUBJECT = "臨床生理學與病理學"
CATEGORY = "醫事檢驗師"
REVIEW_ACTIONS = {"block", "needs_review"}
SCRIPT_NAME = "repair_medtech_physiology_review_blocks"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair active blocked/needs_review physiology candidates from human notes.")
    parser.add_argument("--candidate-path", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--issue-path", type=Path, default=DEFAULT_ISSUE_PATH)
    parser.add_argument("--review-log", type=Path, default=DEFAULT_REVIEW_LOG)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def make_backup(paths: list[Path]) -> Path:
    backup_dir = RUN_DIR / "_repair_backups" / f"{datetime.now():%Y%m%d-%H%M%S}-medtech-physiology-review-blocks"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def is_target(candidate: dict[str, Any]) -> bool:
    meta = candidate.get("metadata") or {}
    return meta.get("normalized_category_name") == CATEGORY and meta.get("normalized_subject_name") == SUBJECT


def latest_review_events(review_log: Path, target_keys: set[str]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not review_log.exists():
        return latest
    with review_log.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if key not in target_keys:
                continue
            if event.get("action") in {"reset_review", "unreviewed"}:
                latest.pop(key, None)
            else:
                latest[key] = event
    return latest


def normalize_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    replacements = {
        "子宫": "子宮",
        "输": "輸",
        "鳞": "鱗",
        "镁": "鎂",
        "碱": "鹼",
        "様": "樣",
        "侧": "側",
        "交换": "交換",
        "婴": "嬰",
        "顴葉": "顳葉",
        "鎘刀": "鐮刀",
        "P_{CO₂12}": "PCO₂ 12",
        "P_{CO₂↑}": "PCO₂↑",
        "P_{CO₂↓}": "PCO₂↓",
        "P_{O₂↑}": "PO₂↑",
        "P_{O₂↓}": "PO₂↓",
        "\\Delta 波": "∆波",
        "F\\\\₄\\": "F₄",
        "V\\\\₄\\": "V₄",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"([+-]?\d+(?:\.\d+)?)\s*\^\{\\circ\}?", r"\1°", text)
    text = re.sub(r"\\mathrm\{P_\{O₂\}\}", "PO₂", text)
    text = re.sub(r"\\mathrm\{P_\{CO₂\}\}", "PCO₂", text)
    text = re.sub(r"P_\{O₂\}", "PO₂", text)
    text = re.sub(r"P_\{CO₂\}", "PCO₂", text)
    text = re.sub(r"HCO3-", "HCO₃⁻", text)
    text = re.sub(r"\bDL\s+CO\b", "DLCO", text)
    text = re.sub(r"DLCO\s+\)", "DLCO)", text)
    text = text.replace("V₅ · V₆", "V₅、V₆")
    text = text.replace(">26 ~mm", ">26 mm")
    text = re.sub(r"\s+", " ", text).strip() if "\n" not in text else "\n".join(line.strip() for line in text.splitlines()).strip()
    return text


def normalize_option(option: dict[str, Any]) -> bool:
    before_key = option.get("key")
    before_text = option.get("text") or ""
    key = option.get("key")
    if isinstance(key, str) and key.lower() in {"a", "b", "c", "d", "e"}:
        option["key"] = key.upper()
    option["text"] = normalize_text(option.get("text") or "")
    changed = option.get("key") != before_key or option.get("text") != before_text
    if changed:
        option["markup"] = builder.markup_payload(option["text"])
    return changed


def normalize_candidate(candidate: dict[str, Any]) -> bool:
    before_stem = candidate.get("stem") or ""
    candidate["stem"] = normalize_text(candidate.get("stem") or "")
    changed = candidate["stem"] != before_stem
    if changed:
        candidate["stem_markup"] = builder.markup_payload(candidate["stem"])
    for option in candidate.get("options") or []:
        changed = normalize_option(option) or changed
    metadata = candidate.get("metadata") or {}
    if changed:
        metadata["review_block_repair"] = SCRIPT_NAME
        candidate["metadata"] = metadata
    return changed


def set_options(candidate: dict[str, Any], options: list[tuple[str, str]]) -> None:
    candidate["options"] = [
        {
            "key": key,
            "raw_order": index,
            "text": normalize_text(text),
            "image": None,
            "markup": builder.markup_payload(normalize_text(text)),
        }
        for index, (key, text) in enumerate(options, start=1)
    ]


def answer_payload_for(candidate: dict[str, Any], number: str) -> dict[str, Any] | None:
    answer_md = Path((candidate.get("metadata") or {}).get("answer_markdown") or "")
    if not answer_md.exists():
        return None
    answers = builder.parse_answers(answer_md.read_text(encoding="utf-8", errors="replace"))
    return answers.get(str(int(number)))


def clone_as_question(base: dict[str, Any], number: int, stem: str, options: list[tuple[str, str]]) -> dict[str, Any]:
    candidate = copy.deepcopy(base)
    source = candidate["source_registry_key"]
    key = f"{source}:q{number:03d}"
    candidate["candidate_key"] = key
    candidate["canonical_question_key"] = key
    candidate["question_number"] = str(number)
    candidate["question_number_occurrence"] = 1
    candidate["stem"] = normalize_text(stem)
    candidate["stem_markup"] = builder.markup_payload(candidate["stem"])
    set_options(candidate, options)
    payload = answer_payload_for(base, str(number))
    candidate["answer_payload"] = payload
    candidate["answer"] = payload["answer"] if payload else None
    metadata = dict(candidate.get("metadata") or {})
    metadata["raw_block"] = "\n".join([candidate["stem"]] + [f"({key}) {text}" for key, text in options])
    metadata["review_block_repair"] = SCRIPT_NAME
    candidate["metadata"] = metadata
    return candidate


def apply_targeted_repairs(candidates: list[dict[str, Any]], latest: dict[str, dict[str, Any]]) -> tuple[set[str], list[dict[str, Any]], list[str]]:
    by_key = {candidate["candidate_key"]: candidate for candidate in candidates}
    changed: set[str] = set()
    appended_events: list[dict[str, Any]] = []
    removed_keys: list[str] = []

    def touch(key: str) -> dict[str, Any] | None:
        candidate = by_key.get(key)
        if candidate:
            changed.add(key)
        return candidate

    key = "moex:111020:308:11:1:question:q011"
    candidate = touch(key)
    if candidate:
        set_options(candidate, [("A", "-60°～0°"), ("B", "-30°～90°"), ("C", "120°～150°"), ("D", "90°～120°")])

    key = "moex:111020:308:11:1:question:q017"
    candidate = touch(key)
    if candidate:
        for option in candidate.get("options") or []:
            if option.get("key") == "B":
                option["text"] = "F₄"
                option["markup"] = builder.markup_payload(option["text"])

    key = "moex:109100:308:11:1:question:q018"
    candidate = touch(key)
    if candidate:
        set_options(candidate, [("A", "pH"), ("B", "PCO₂"), ("C", "HCO₃⁻"), ("D", "PO₂")])

    key = "moex:109020:308:11:1:question:q014"
    candidate = touch(key)
    if candidate:
        for option in candidate.get("options") or []:
            if option.get("key") == "D":
                option["text"] = "延腦（medulla）"
                option["markup"] = None

    key = "moex:109020:308:11:1:question:q015"
    candidate = touch(key)
    if candidate:
        set_options(candidate, [("A", "額葉與顳葉"), ("B", "小腦區"), ("C", "頂葉及枕葉"), ("D", "腦幹區")])
    removed_keys.append("moex:109020:308:11:1:question:q015:dup02")

    key = "moex:107100:308:11:1:question:q005"
    candidate = touch(key)
    if candidate:
        for option in candidate.get("options") or []:
            if option.get("key") == "C":
                option["text"] = "V₅、V₆ 的R值 >26 mm"
                option["markup"] = builder.markup_payload(option["text"])
    removed_keys.append("moex:107100:308:11:1:question:q000")

    key = "moex:106100:308:11:1:question:q010"
    candidate = touch(key)
    if candidate:
        for option in candidate.get("options") or []:
            if option.get("key") == "D":
                option["text"] = "缺口型U波"
                option["markup"] = None
        q11 = clone_as_question(
            candidate,
            11,
            "此張心電圖的過渡帶（transitional zone）最接近：",
            [("A", "V₂"), ("B", "V₃"), ("C", "V₄"), ("D", "V₅")],
        )
        if q11["candidate_key"] not in by_key:
            insert_index = candidates.index(candidate) + 1
            candidates.insert(insert_index, q11)
            by_key[q11["candidate_key"]] = q11
            changed.add(q11["candidate_key"])

    old_key = "moex:106020:308:11:1:question:q018:dup02"
    candidate = by_key.get(old_key)
    if candidate:
        new_key = "moex:106020:308:11:1:question:q024"
        candidate["candidate_key"] = new_key
        candidate["canonical_question_key"] = new_key
        candidate["question_number"] = "24"
        candidate["question_number_occurrence"] = 1
        candidate["stem"] = "18歲糖尿病病患入急診室有不規則脈搏與用力深呼吸的症狀。其動脈血液氣體分析報告於室內空氣下，pH 7.05、PCO₂ 12 mmHg、HCO₃⁻ 5 mEq/L、鹼基超過量（base excess）-30 mEq/L、PO₂ 108 mmHg。此病患的判讀為："
        candidate["stem_markup"] = builder.markup_payload(candidate["stem"])
        payload = answer_payload_for(candidate, "24")
        candidate["answer_payload"] = payload
        candidate["answer"] = payload["answer"] if payload else None
        normalize_candidate(candidate)
        changed.add(new_key)
        event = latest.get(old_key)
        if event:
            appended_events.append(
                {
                    "action": event.get("action") or "block",
                    "candidate_key": new_key,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "notes": (event.get("notes") or "") + "\n[系統補記] 原 candidate_key 為 q018:dup02，已依人工註記移至 q024。",
                    "reviewer": SCRIPT_NAME,
                }
            )

    key = "moex:104090:308:11:1:question:q010"
    candidate = touch(key)
    if candidate:
        set_options(candidate, [("A", "4"), ("B", "6"), ("C", "8"), ("D", "10")])

    key = "moex:104020:311:11:1:question:q032"
    candidate = touch(key)
    if candidate:
        candidate["stem"] = "超音波檢查圖像中，箭頭所指為："
        candidate["stem_markup"] = None

    key = "moex:104020:311:11:1:question:q017"
    candidate = touch(key)
    if candidate:
        set_options(
            candidate,
            [
                ("A", "P_{z}, F_{z}, C_{z}, Fp_{z}, O_{z}"),
                ("B", "F_{z}, Fp_{z}, C_{z}, P_{z}, O_{z}"),
                ("C", "Fp_{z}, F_{z}, C_{z}, P_{z}, O_{z}"),
                ("D", "P_{z}, Fp_{z}, C_{z}, F_{z}, O_{z}"),
            ],
        )

    normalized_removed = set(removed_keys)
    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["candidate_key"] in normalized_removed:
            continue
        kept.append(candidate)
    candidates[:] = kept
    return changed, appended_events, removed_keys


def normalize_active_corrections(latest: dict[str, dict[str, Any]], active_keys: set[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key in sorted(active_keys):
        event = latest.get(key)
        if not event or event.get("action") not in REVIEW_ACTIONS or not isinstance(event.get("correction"), dict):
            continue
        corrected = copy.deepcopy(event["correction"])
        changed = False
        if "stem" in corrected:
            new_stem = normalize_text(corrected.get("stem") or "")
            changed = changed or new_stem != corrected.get("stem")
            corrected["stem"] = new_stem
        for option in corrected.get("options") or []:
            before = json.dumps(option, ensure_ascii=False, sort_keys=True)
            normalize_option(option)
            changed = changed or before != json.dumps(option, ensure_ascii=False, sort_keys=True)
        if not changed:
            continue
        events.append(
            {
                "action": event["action"],
                "candidate_key": key,
                "correction": corrected,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "notes": (event.get("notes") or "") + "\n[系統補記] 修正前一版 correction overlay 的 OCR 字形或科學符號；人工狀態維持不變。",
                "reviewer": SCRIPT_NAME,
            }
        )
    return events


def recompute_issues(candidates: list[dict[str, Any]]) -> list[builder.Issue]:
    issues: list[builder.Issue] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        own_issues = builder.candidate_issues(candidate)
        candidate["quality_status"] = builder.quality_status(own_issues)
        candidate["issue_count"] = len(own_issues)
        issues.extend(own_issues)
        by_source[candidate["source_registry_key"]].append(candidate)
    doc_issues: list[builder.Issue] = []
    for source, source_candidates in by_source.items():
        doc_issues.extend(builder.document_issues(source_candidates, source))
    issues.extend(doc_issues)
    by_key: dict[str, list[builder.Issue]] = defaultdict(list)
    for issue in issues:
        if issue.candidate_key:
            by_key[issue.candidate_key].append(issue)
    for candidate in candidates:
        related = by_key.get(candidate["candidate_key"], [])
        candidate["quality_status"] = builder.quality_status(related)
        candidate["issue_count"] = len(related)
    return issues


def main() -> None:
    args = parse_args()
    candidates = load_jsonl(args.candidate_path)
    target_keys = {candidate["candidate_key"] for candidate in candidates if is_target(candidate)}
    latest = latest_review_events(args.review_log, target_keys)
    active_keys = {key for key, event in latest.items() if event.get("action") in REVIEW_ACTIONS}
    changed: set[str] = set()
    for candidate in candidates:
        if candidate["candidate_key"] in active_keys and normalize_candidate(candidate):
            changed.add(candidate["candidate_key"])

    targeted_changed, moved_events, removed_keys = apply_targeted_repairs(candidates, latest)
    changed |= targeted_changed
    overlay_events = normalize_active_corrections(latest, active_keys)
    appended_events = moved_events + overlay_events

    issues = recompute_issues(candidates)
    print(f"active review items: {len(active_keys)}")
    print(f"candidate rows changed/added: {len(changed)}")
    print(f"candidate rows removed: {len(removed_keys)} {removed_keys}")
    print(f"review overlay events to append: {len(appended_events)}")
    if args.dry_run:
        print("sample changed:", sorted(changed)[:20])
        print("sample appended events:", json.dumps(appended_events[:3], ensure_ascii=False, indent=2))
        return

    backup_dir = make_backup([args.candidate_path, args.issue_path, args.review_log])
    write_jsonl(args.candidate_path, candidates)
    builder.write_issues_csv(args.issue_path, issues)
    if appended_events:
        with args.review_log.open("a", encoding="utf-8") as f:
            for event in appended_events:
                f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"backup: {backup_dir}")
    print(f"total candidates: {len(candidates)}")


if __name__ == "__main__":
    main()
