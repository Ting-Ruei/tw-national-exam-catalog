#!/usr/bin/env python3
"""
Serve a local review UI for question candidates.

PostgreSQL is the primary review surface when REVIEW_UI_BACKEND=sql. The legacy
JSONL files are kept only as a transitional append-only backup while the review
workflow is migrating fully into SQL.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gc
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - optional runtime dependency in Docker
    psycopg = None
    Jsonb = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
DEFAULT_CANDIDATE_ROOT = ASSET_ROOT / "30_normalized_items" / "question_candidates"
MANUAL_ASSET_ROOT = ASSET_ROOT / "40_manual_assets"
STRUCTURED_TABLE_RE = re.compile(r"<table.*?</table>", re.I | re.S)
GROUP_CONTINUATION_RE = re.compile(r"^\s*[（(]?\s*(承上題|呈上題|上題|前述)\s*[）)]?[，,、：:]?", re.I)
GROUP_PREFIX_RANGE_RE = re.compile(r"^\s*(\d{1,3})\s*(?:-|－|~|～|至|到)\s*(\d{1,3})\s*(?=\S)")
GROUP_COUNT_RE = re.compile(r"回答下列\s*(\d{1,2})\s*題")
VISUAL_DEPENDENCY_RE = re.compile(
    r"(下圖|附圖|圖中|圖示|如圖|圖片|影像|照片|箭頭|表中|下表|附表|心電圖|X\s*光|X光|超音波|切片圖|染色圖|鏡檢圖|尿沉渣圖|電泳圖|曲線圖|流程圖|家系圖)",
    re.I,
)
VISUAL_DEPENDENCY_SQL_RE = r"(下圖|附圖|圖中|圖示|如圖|圖片|影像|照片|箭頭|表中|下表|附表|心電圖|X\s*光|X光|超音波|切片圖|染色圖|鏡檢圖|尿沉渣圖|電泳圖|曲線圖|流程圖|家系圖)"
TABLE_DEPENDENCY_SQL_RE = r"(表中|下表|附表|如下表|table)"
ANSWER_ISSUE_CODES = {"missing_answer", "missing_answer_markdown", "unexpected_answer_value"}
RESET_REVIEW_ACTIONS = {"unreviewed", "reset_review"}
AI_RESET_REVIEW_ACTIONS = {"unreviewed", "reset_review", "reset_ai_review"}
GROUP_REVIEW_ACTIONS = {"confirm_not_group", "confirm_group", "reset_group_review"}
VISUAL_REVIEW_ACTIONS = {"human_review_pdf_visual"}
NON_QUESTION_REVIEW_ACTIONS = GROUP_REVIEW_ACTIONS | VISUAL_REVIEW_ACTIONS
QUESTION_REVIEW_ACTIONS = {"accept", "correct", "needs_review", "block", "exclude", "unblock", "comment", "reviewed", *RESET_REVIEW_ACTIONS}
ANSWER_REVIEW_ACTIONS = {"accept", "correct", "needs_review", "block", "unblock", "comment", "reviewed", *RESET_REVIEW_ACTIONS}
QUESTION_READY_ACTIONS = {"accept", "unblock"}
ANSWER_READY_ACTIONS = {"accept", "unblock"}
DEFAULT_AI_MODEL = os.environ.get("OPENAI_REVIEW_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
AI_REVIEW_PROMPT_VERSION = "question_format_audit_v0.1"
REPAIR_REVIEWER_PREFIXES = (
    "repair_",
    "backfill_",
    "parser_global_refresh",
    "codex-repair",
)
AI_REVIEW_ACTIONS_WITH_WORK = {
    "needs_review",
    "block",
    "manual_correction",
    "human_review",
    "parser_fix",
    "manual_image_check",
    "human_review_text",
    "human_review_pdf_visual",
    "fix_parser_rule",
    "add_manual_asset",
}


class SqlWriteError(RuntimeError):
    """Raised when SQL-first review persistence cannot be confirmed."""

AI_ANSWER_DEFER_LABELS = {"answer_pair_suspect", "needs_human_review", "pass_likely"}
AI_OCR_TEXT_REPLACEMENTS = [
    ("麸胺", "麩胺"),
    ("麃胺", "麩胺"),
    ("麗胺酸（Glutamic acid）", "麩胺酸（Glutamic acid）"),
    ("麗胺酸（glutamic acid）", "麩胺酸（glutamic acid）"),
    ("繊胺酸 (Valine)", "纈胺酸 (Valine)"),
    ("厥氧", "厭氧"),
    ("鶥鵡熱", "鸚鵡熱"),
    ("恶臭", "惡臭"),
    ("辅酶", "輔酶"),
    ("辅因子", "輔因子"),
    ("转胺", "轉胺"),
    ("转移酶", "轉移酶"),
    ("还原酶", "還原酶"),
    ("氧化还原", "氧化還原"),
    ("羟化", "羥化"),
    ("胰岛", "胰島"),
    ("肾功能", "腎功能"),
    ("去氢", "去氫"),
    ("乳酸去氢", "乳酸去氫"),
    ("将 ", "將 "),
]
AI_OCR_CHAR_REPLACEMENTS = str.maketrans(
    {
        "麸": "麩",
        "黄": "黃",
        "氢": "氫",
        "脱": "脫",
        "铵": "銨",
        "巯": "巰",
        "羟": "羥",
        "钠": "鈉",
        "钾": "鉀",
        "钙": "鈣",
        "镁": "鎂",
        "铁": "鐵",
        "锌": "鋅",
        "锰": "錳",
        "铜": "銅",
        "铅": "鉛",
        "肾": "腎",
        "岛": "島",
        "恶": "惡",
        "鉯": "鈀",
        "将": "將",
        "转": "轉",
        "还": "還",
        "辅": "輔",
        "递": "遞",
        "剂": "劑",
        "体": "體",
        "质": "質",
        "酰": "醯",
    }
)


def issue_quality_status(issues: list[dict[str, Any]]) -> str:
    severities = {issue.get("severity") for issue in issues}
    if "blocked" in severities or "error" in severities:
        return "blocked"
    if "warning" in severities:
        return "needs_review"
    return "pass"


def int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def candidate_visual_profile(candidate: dict[str, Any]) -> dict[str, Any]:
    visual_review_status = str(candidate.get("visual_review") or "").strip()
    no_visual_required = visual_review_status == "no_visual_required"
    image_refs = [
        ref for ref in (candidate.get("image_refs") or [])
        if isinstance(ref, dict)
    ]
    stem_image = candidate.get("stem_image") if isinstance(candidate.get("stem_image"), dict) else None
    option_images = [
        option.get("image")
        for option in (candidate.get("options") or [])
        if isinstance(option, dict) and isinstance(option.get("image"), dict)
    ]
    existing_refs = [ref for ref in image_refs if ref.get("exists") is not False]
    if stem_image and stem_image.get("exists") is not False:
        existing_refs.append(stem_image)
    existing_refs.extend(ref for ref in option_images if ref and ref.get("exists") is not False)
    text = "\n".join(
        [
            str(candidate.get("stem") or ""),
            str((candidate.get("metadata") or {}).get("raw_block") or ""),
        ]
    )
    has_visual_dependency = bool(VISUAL_DEPENDENCY_RE.search(text)) and not no_visual_required
    roles = sorted(
        {
            str(ref.get("asset_role") or ref.get("role") or "image")
            for ref in existing_refs
            if isinstance(ref, dict)
        }
    )
    has_manual_asset = any(
        str(ref.get("manual_asset") or "").lower() in {"true", "1"}
        or "manual" in str(ref.get("asset_role") or ref.get("role") or "").lower()
        or str(ref.get("asset_role") or ref.get("role") or "") == "table_manual_screenshot"
        for ref in existing_refs
        if isinstance(ref, dict)
    )
    return {
        "has_visual_asset": bool(existing_refs),
        "visual_asset_count": len(existing_refs),
        "has_visual_dependency": has_visual_dependency,
        "needs_visual_asset_review": has_visual_dependency and not existing_refs,
        "visual_asset_roles": roles,
        "has_manual_asset": has_manual_asset,
        "no_visual_required": no_visual_required,
        "visual_review_status": visual_review_status,
        "visual_reviewed": visual_review_status in {"no_visual_required", "visual_asset_ok", "visual_asset_problem"},
    }


def answer_payload_values(answer_payload: Any, answer: Any) -> list[str]:
    values: list[str] = []
    if isinstance(answer_payload, dict):
        accepted = answer_payload.get("accepted_values")
        if isinstance(accepted, list):
            values.extend(str(value).strip() for value in accepted if str(value).strip())
        for key in ["answer", "raw_answer"]:
            value = str(answer_payload.get(key) or "").strip()
            if value:
                values.append(value)
    answer_value = str(answer or "").strip()
    if answer_value:
        values.append(answer_value)
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value not in seen:
            unique_values.append(value)
            seen.add(value)
    return unique_values


def answer_choice_letters(value: str) -> list[str]:
    return re.findall(r"[A-D]", value.upper())


def answer_review_hint(role: str, answer: Any, answer_payload: Any) -> dict[str, Any]:
    values = answer_payload_values(answer_payload, answer)
    raw_answer = ""
    is_special_correction = False
    if isinstance(answer_payload, dict):
        raw_answer = str(answer_payload.get("raw_answer") or "").strip()
        is_special_correction = bool(answer_payload.get("is_special_correction"))
    answer_text = str(answer or "").strip()
    unresolved_marker = any(value == "#" for value in values)
    multi_choice = any("|" in value or "+" in value or "/" in value for value in values)
    accepted_count = len([value for value in values if answer_choice_letters(value)])
    is_ans_single = role == "answer" and bool(re.fullmatch(r"[A-D]", answer_text.upper()))
    is_mod = role == "correction"
    needs_manual_choice = is_mod and (unresolved_marker or multi_choice or is_special_correction or accepted_count > 1)
    flags: list[str] = []
    message = ""
    severity = ""
    if is_ans_single:
        flags.append("ans_single_choice_trusted")
        message = "ANS 單選答案，若無其他疑點可沿用 parser 結果。"
        severity = "info"
    if is_mod and unresolved_marker:
        flags.append("mod_unresolved_marker")
        message = "MOD 答案仍含 #，需看答案 PDF 後點選正確答案。"
        severity = "warning"
    elif is_mod and needs_manual_choice:
        flags.append("mod_multi_answer")
        message = "MOD 多答案或特殊更正，建議看答案 PDF 後用點選確認。"
        severity = "warning"
    return {
        "flags": flags,
        "message": message,
        "severity": severity,
        "trusted_single": is_ans_single,
        "needs_manual_choice": needs_manual_choice,
        "unresolved_marker": unresolved_marker,
        "raw_answer": raw_answer,
        "values": values,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a local human review UI for question candidates.")
    parser.add_argument("--candidate-jsonl", type=Path, default=None)
    parser.add_argument("--issue-csv", type=Path, default=None)
    parser.add_argument("--review-log", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--auto-reload-candidates",
        action="store_true",
        help="Automatically reload large candidate/issue files when they change. Disabled by default to avoid memory spikes during review.",
    )
    parser.add_argument(
        "--review-backend",
        choices=["jsonl", "sql"],
        default=os.environ.get("REVIEW_UI_BACKEND", "sql"),
        help="Use JSONL files or PostgreSQL review staging for candidate list queries.",
    )
    return parser.parse_args()


def latest_path(pattern: str) -> Path:
    paths = sorted(DEFAULT_CANDIDATE_ROOT.glob(pattern))
    if not paths:
        raise SystemExit(f"No candidate output found: {DEFAULT_CANDIDATE_ROOT}/{pattern}")
    return paths[-1]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
                return rows


def load_issues(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}
    issues: dict[str, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("candidate_key") or ""
            if row.get("issue_json"):
                try:
                    row["issue_json"] = json.loads(row["issue_json"])
                except json.JSONDecodeError:
                    pass
            issues.setdefault(key, []).append(row)
    return issues


def project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        parts = path.parts
        if "tw-national-exam-catalog" in parts:
            index = parts.index("tw-national-exam-catalog")
            return PROJECT_ROOT.joinpath(*parts[index + 1 :])
        return path
    if value.startswith("國考題資料夾/"):
        return PROJECT_ROOT / value
    return ASSET_ROOT / value


def safe_file_path(value: str) -> Path | None:
    if not value:
        return None
    path = project_path(value)
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return None
    allowed_roots = [PROJECT_ROOT.resolve(), ASSET_ROOT.resolve()]
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return resolved
    return None


def display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def sibling_pdf(markdown_value: str, suffix: str) -> str | None:
    if not markdown_value:
        return None
    markdown_path = project_path(markdown_value)
    candidate = markdown_path.with_name(f"{markdown_path.stem}{suffix}.pdf")
    if candidate.exists():
        return display_path(candidate)
    return None


def safe_path_segment(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:\*\?\"<>\|\s]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:80] or fallback


def data_url_to_bytes(data_url: str) -> tuple[bytes, str, str]:
    match = re.fullmatch(r"data:(image/(?:png|jpeg|jpg|webp));base64,(.+)", data_url.strip(), re.S)
    if not match:
        raise ValueError("Only png, jpeg, or webp image data URLs are supported.")
    mime_type = "image/jpeg" if match.group(1) == "image/jpg" else match.group(1)
    extension = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }[mime_type]
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise ValueError("Image data is not valid base64.") from exc
    if not data:
        raise ValueError("Image is empty.")
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("Image is larger than 15 MB.")
    return data, mime_type, extension


def load_review_events(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    latest_reset: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest, counts, latest_reset
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            if event.get("action") in GROUP_REVIEW_ACTIONS:
                counts[key] = counts.get(key, 0) + 1
                continue
            if event.get("action") in RESET_REVIEW_ACTIONS:
                counts[key] = counts.get(key, 0) + 1
                latest.pop(key, None)
                latest_reset[key] = event
                continue
            if "correction" not in event and key in latest and latest[key].get("correction"):
                event["correction"] = latest[key]["correction"]
            counts[key] = counts.get(key, 0) + 1
            latest[key] = event
            latest_reset.pop(key, None)
    return latest, counts, latest_reset


def load_group_review_events(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key or event.get("action") not in GROUP_REVIEW_ACTIONS:
                continue
            latest[str(key)] = event
    return latest


def load_latest_events(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    latest_reset: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest, counts, latest_reset
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get("candidate_key")
            if not key:
                continue
            if event.get("action") in RESET_REVIEW_ACTIONS:
                counts[key] = counts.get(key, 0) + 1
                latest.pop(key, None)
                latest_reset[key] = event
                continue
            counts[key] = counts.get(key, 0) + 1
            latest[key] = event
            latest_reset.pop(key, None)
    return latest, counts, latest_reset


def load_keyed_events(
    path: Path,
    key_field: str = "candidate_key",
    reset_actions: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    if not path.exists():
        return latest, counts
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = event.get(key_field)
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            if reset_actions and event.get("action") in reset_actions:
                latest.pop(key, None)
                continue
            latest[key] = event
    return latest, counts


def file_signature(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def repair_event_info(
    latest_review: dict[str, Any] | None,
    latest_reset_review: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    latest_action = str((latest_review or {}).get("action") or "")
    if latest_action in {"accept", "unblock"}:
        return {"active": False}

    event = latest_review or latest_reset_review
    reviewer = str((event or {}).get("reviewer") or "")
    metadata_sources = [
        str(metadata.get(key) or "")
        for key in ("review_block_repair", "backfill_repair", "backfill_source")
        if metadata.get(key)
    ]
    is_repair_event = reviewer.startswith(REPAIR_REVIEWER_PREFIXES)
    is_reset_waiting = bool(latest_reset_review and not latest_review)
    if not (is_repair_event or is_reset_waiting or metadata_sources):
        return {"active": False}

    notes = str((event or {}).get("reset_notes") or (event or {}).get("notes") or "")
    return {
        "active": True,
        "label": "已修待複核",
        "reviewer": reviewer,
        "action": latest_action or str((latest_reset_review or {}).get("action") or "reset_review"),
        "notes": notes,
        "sources": metadata_sources,
        "updated_at": (event or {}).get("created_at"),
    }


def compact_candidate_for_ai(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata") or {}
    return {
        "candidate_key": candidate.get("candidate_key"),
        "category": metadata.get("normalized_category_name") or metadata.get("group_name"),
        "subject": metadata.get("normalized_subject_name"),
        "year": metadata.get("year"),
        "exam_ordinal": metadata.get("exam_ordinal"),
        "question_number": candidate.get("question_number"),
        "stem": candidate.get("stem"),
        "options": [
            {
                "key": option.get("key"),
                "text": option.get("text"),
                "has_image": bool(isinstance(option.get("image"), dict) and option["image"].get("exists")),
            }
            for option in (candidate.get("options") or [])
            if isinstance(option, dict)
        ],
        "answer": candidate.get("answer"),
        "group_ref": candidate.get("group_ref"),
        "image_count": len(candidate.get("non_option_image_refs") or candidate.get("image_refs") or []),
        "question_issues": [
            {
                "issue_code": issue.get("issue_code"),
                "severity": issue.get("severity"),
                "message": issue.get("message"),
            }
            for issue in (candidate.get("question_issues") or candidate.get("issues") or [])
        ],
    }


def local_question_ai_audit(candidate: dict[str, Any]) -> dict[str, Any]:
    payload = compact_candidate_for_ai(candidate)
    text_parts = [str(payload.get("stem") or "")]
    text_parts.extend(str(option.get("text") or "") for option in payload.get("options") or [])
    combined = "\n".join(text_parts)
    findings: list[dict[str, Any]] = []

    def add(code: str, severity: str, field: str, message: str, evidence: str = "", suggestion: str = "") -> None:
        findings.append(
            {
                "code": code,
                "severity": severity,
                "field": field,
                "message": message,
                "evidence": evidence[:160],
                "suggestion": suggestion,
            }
        )

    suspicious_variant_map = {
        "黄": "黃",
        "氢": "氫",
        "脱": "脫",
        "铵": "銨",
        "巯": "巰",
        "题": "題",
        "临": "臨",
        "验": "驗",
        "药": "藥",
        "麸": "麩",
        "羟": "羥",
        "钠": "鈉",
        "钾": "鉀",
        "钙": "鈣",
        "镁": "鎂",
        "铁": "鐵",
        "锌": "鋅",
        "铜": "銅",
        "铅": "鉛",
    }
    simplified_hits = sorted({char for char in combined if char in suspicious_variant_map})
    if simplified_hits:
        evidence = "、".join(f"{char}→{suspicious_variant_map[char]}" for char in simplified_hits)
        add(
            "possible_simplified_or_ocr_char",
            "warning",
            "text",
            "偵測到疑似簡化字或 OCR 字形，建議人工比對 PDF。",
            evidence,
            "若 PDF 原文為繁體，請修 parser 正規化或人工校正；若原文即如此，請保留。",
        )
    if re.search(r"\\[A-Za-z]+|_\{|<sub>|<sup>|\^\{", combined):
        add(
            "science_markup_present",
            "info",
            "text",
            "題文含公式、上下標或 LaTeX/HTML markup，前端顯示與入庫時需確認。",
            re.search(r"\\[A-Za-z]+|_\{|<sub>|<sup>|\^\{", combined).group(0),
            "確認畫面是否已正確渲染希臘字母、上下標與科學符號。",
        )
    bracket_pairs = [("(", ")"), ("（", "）"), ("[", "]"), ("【", "】")]
    for left, right in bracket_pairs:
        if combined.count(left) != combined.count(right):
            add("unbalanced_bracket", "warning", "text", f"括號數量不一致：{left}{right}", f"{left}:{combined.count(left)} {right}:{combined.count(right)}")
    options = payload.get("options") or []
    option_keys = [option.get("key") for option in options]
    if len(options) not in {0, 4, 5}:
        add("unexpected_option_count", "warning", "options", "選項數量不是常見的 4 或 5 個。", str(option_keys), "檢查是否串題、漏選項或題組文字被切進選項。")
    if len(option_keys) != len(set(option_keys)):
        add("duplicate_option_key", "error", "options", "選項代號重複。", str(option_keys), "需要修 parser 或人工校正。")
    if re.search(r"(下列圖|附圖|圖中|表中|下表|附表)", combined) and not payload.get("image_count") and not any(option.get("has_image") for option in options):
        add("image_or_table_cue_without_asset", "warning", "assets", "題文提到圖表，但 candidate 沒有圖片或表格資產。", "圖表 cue", "比對 MinerU layout；必要時用 manual asset 掛圖。")
    if "<table" in combined.lower():
        add("structured_table_markup", "info", "stem", "題幹含結構化 table markup。", "<table>", "若網頁顯示不完整，改用 manual table image 並保留 raw table 追溯。")
    status = "pass"
    if any(item["severity"] == "error" for item in findings):
        status = "block"
    elif any(item["severity"] == "warning" for item in findings):
        status = "needs_review"
    return {
        "status": status,
        "confidence": 0.45 if findings else 0.55,
        "summary": "本機規則稽核完成；未使用 OpenAI API。" if findings else "本機規則未發現明顯格式疑點；未使用 OpenAI API。",
        "findings": findings,
        "recommended_action": "needs_review" if status != "pass" else "no_action",
    }


def extract_response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks).strip()


def openai_question_ai_audit(candidate: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        audit = local_question_ai_audit(candidate)
        audit["provider"] = "local"
        audit["model"] = "heuristic"
        return audit
    payload = compact_candidate_for_ai(candidate)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["pass", "needs_review", "block"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "summary": {"type": "string"},
            "recommended_action": {"type": "string", "enum": ["no_action", "needs_review", "block", "manual_correction"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "code": {"type": "string"},
                        "severity": {"type": "string", "enum": ["info", "warning", "error"]},
                        "field": {"type": "string"},
                        "message": {"type": "string"},
                        "evidence": {"type": "string"},
                        "suggestion": {"type": "string"},
                    },
                    "required": ["code", "severity", "field", "message", "evidence", "suggestion"],
                },
            },
        },
        "required": ["status", "confidence", "summary", "recommended_action", "findings"],
    }
    request_body = {
        "model": DEFAULT_AI_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "你是台灣國考題 OCR/parser 審核助理。只做格式與字形稽核，不判斷學科答案正確性。"
                    "請檢查疑似 OCR 字形錯誤、簡繁混用、希臘字母/上下標/科學符號、選項數量、題組/圖表線索、表格或圖片引用是否可能缺漏。"
                    "不要自動改題，不要宣稱一定錯；用繁體中文回覆 JSON。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "question_format_audit",
                "strict": True,
                "schema": schema,
            }
        },
    }
    data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{OPENAI_API_BASE}/responses",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=int(os.environ.get("OPENAI_REVIEW_TIMEOUT", "45"))) as response:
        raw_response = json.loads(response.read().decode("utf-8"))
    text = extract_response_text(raw_response)
    try:
        audit = json.loads(text)
    except json.JSONDecodeError:
        audit = {
            "status": "needs_review",
            "confidence": 0,
            "summary": "OpenAI 回傳不是有效 JSON，已保留原始文字供排查。",
            "recommended_action": "needs_review",
            "findings": [
                {
                    "code": "invalid_model_json",
                    "severity": "error",
                    "field": "model_output",
                    "message": "模型回傳無法解析。",
                    "evidence": text[:500],
                    "suggestion": "檢查模型與 response format 支援度。",
                }
            ],
        }
    audit["provider"] = "openai"
    audit["model"] = DEFAULT_AI_MODEL
    audit["response_id"] = raw_response.get("id")
    audit["usage"] = raw_response.get("usage")
    return audit


def normalized_correction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    correction: dict[str, Any] = {}
    for key in ("stem", "answer", "group_ref", "visual_review"):
        if key in value:
            correction[key] = "" if value[key] is None else str(value[key])
    if "group_sequence_no" in value:
        try:
            correction["group_sequence_no"] = int(value["group_sequence_no"])
        except (TypeError, ValueError):
            correction["group_sequence_no"] = None
    if "stem_image" in value and value.get("stem_image") is None:
        correction["stem_image"] = None
    elif isinstance(value.get("stem_image"), dict):
        stem_image = normalized_asset_ref(value["stem_image"])
        if stem_image:
            correction["stem_image"] = stem_image
    if isinstance(value.get("image_refs"), list):
        image_refs = []
        for ref in value["image_refs"]:
            if not isinstance(ref, dict):
                continue
            normalized = normalized_asset_ref(ref)
            if normalized:
                image_refs.append(normalized)
        correction["image_refs"] = image_refs
    if isinstance(value.get("answer_image_refs"), list):
        answer_image_refs = []
        for ref in value["answer_image_refs"]:
            if not isinstance(ref, dict):
                continue
            normalized = normalized_asset_ref(ref)
            if normalized:
                answer_image_refs.append(normalized)
        correction["answer_image_refs"] = answer_image_refs
    if isinstance(value.get("options"), list):
        options = []
        for option in value["options"]:
            if not isinstance(option, dict):
                continue
            label = str(option.get("key") or "").strip().upper()
            if not label:
                continue
            image = option.get("image")
            normalized_image = normalized_asset_ref(image) if isinstance(image, dict) else None
            options.append(
                {
                    "key": label[:1],
                    "text": "" if option.get("text") is None else str(option.get("text")),
                    "image": normalized_image,
                    "markup": option.get("markup"),
                }
            )
        correction["options"] = options
    return correction


def normalized_asset_ref(value: dict[str, Any]) -> dict[str, Any]:
    path_value = str(value.get("path") or value.get("path_relative") or "").strip()
    path = safe_file_path(path_value)
    if path is None:
        return {}
    normalized: dict[str, Any] = {
        "raw_ref": str(value.get("raw_ref") or value.get("label") or path.name),
        "path": display_path(path),
        "path_relative": display_path(path),
        "exists": path.exists(),
    }
    for key in ("asset_key", "asset_role", "source", "caption", "description", "manual_asset", "mime_type", "sha256", "bytes", "placement", "target_option"):
        if key in value:
            normalized[key] = value[key]
    return normalized


def apply_ai_ocr_replacements(value: str) -> tuple[str, list[str]]:
    if not value:
        return value, []
    updated = value
    changes: list[str] = []
    for source, target in AI_OCR_TEXT_REPLACEMENTS:
        if source in updated:
            updated = updated.replace(source, target)
            changes.append(f"{source} -> {target}")
    translated = updated.translate(AI_OCR_CHAR_REPLACEMENTS)
    if translated != updated:
        for old, new in zip(updated, translated):
            if old != new:
                changes.append(f"{old} -> {new}")
        updated = translated
    return updated, sorted(set(changes))


def ai_audit_has_work(audit: dict[str, Any], suggested_correction: dict[str, Any] | None = None) -> bool:
    if ai_audit_is_answer_deferred_only(audit):
        return False
    recommended_action = str(audit.get("recommended_action") or audit.get("skill_recommended_action") or "")
    findings = audit.get("findings") or []
    labels = set(audit.get("labels") or [])
    if suggested_correction:
        return True
    if recommended_action in AI_REVIEW_ACTIONS_WITH_WORK:
        return True
    if findings:
        return True
    return bool(labels - {"pass_likely"})


def ai_audit_is_answer_deferred_only(audit: dict[str, Any]) -> bool:
    recommended_actions = {
        str(audit.get("recommended_action") or ""),
        str(audit.get("skill_recommended_action") or ""),
    }
    labels = set(audit.get("labels") or [])
    findings = audit.get("findings") or []
    has_answer_defer = "defer_to_answer_audit" in recommended_actions
    if not has_answer_defer:
        has_answer_defer = any(
            str(finding.get("suggestion") or finding.get("recommended_action") or "") == "defer_to_answer_audit"
            for finding in findings
            if isinstance(finding, dict)
        )
    if not has_answer_defer:
        return False
    if labels - AI_ANSWER_DEFER_LABELS:
        return False
    non_answer_findings = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_text = " ".join(
            str(finding.get(field) or "")
            for field in ("field", "code", "message", "evidence", "suggestion", "recommended_action")
        )
        if "answer" not in finding_text.lower() and "答案" not in finding_text:
            non_answer_findings.append(finding)
    return not non_answer_findings


def effective_ai_audit_status(audit: dict[str, Any] | None, suggested_correction: dict[str, Any] | None = None) -> str | None:
    if not isinstance(audit, dict):
        return None
    status = str(audit.get("status") or "")
    if status == "blocked":
        status = "block"
    if ai_audit_is_answer_deferred_only(audit):
        return "pass"
    if status == "pass" and ai_audit_has_work(audit, suggested_correction):
        return "needs_review"
    return status or None


def ai_suggested_correction(candidate: dict[str, Any], audit: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(audit, dict):
        return None, []
    if isinstance(audit.get("suggested_correction"), dict):
        normalized = normalized_correction(audit["suggested_correction"])
        explicit_changes = audit.get("suggested_changes")
        if isinstance(explicit_changes, list):
            changes = [str(item) for item in explicit_changes if str(item).strip()]
        else:
            changes = ["AI 提供 explicit suggested_correction。"] if normalized else []
        return (normalized or None), changes

    status = effective_ai_audit_status(audit)
    audit_text = " ".join(
        str(part or "")
        for part in [
            audit.get("summary"),
            audit.get("reason"),
            audit.get("recommended_action"),
            json.dumps(audit.get("labels") or [], ensure_ascii=False),
            json.dumps(audit.get("findings") or [], ensure_ascii=False),
        ]
    )
    if status == "pass" and not re.search(r"(ocr|字形|簡|麸|麩|胰岛|肾|氢|去氢|麗胺|麃胺)", audit_text, re.I):
        return None, []

    correction: dict[str, Any] = {}
    changes: list[str] = []
    stem, stem_changes = apply_ai_ocr_replacements(str(candidate.get("stem") or ""))
    if stem_changes and stem != candidate.get("stem"):
        correction["stem"] = stem
        changes.extend(f"題幹：{change}" for change in stem_changes)

    option_rows = []
    option_changed = False
    for option in candidate.get("options") or []:
        if not isinstance(option, dict):
            continue
        copy = dict(option)
        text, option_changes = apply_ai_ocr_replacements(str(copy.get("text") or ""))
        if option_changes and text != copy.get("text"):
            copy["text"] = text
            option_changed = True
            changes.extend(f"選項 {copy.get('key')}: {change}" for change in option_changes)
        option_rows.append(copy)
    if option_changed:
        correction["options"] = option_rows

    return (correction or None), sorted(set(changes))


def strip_structured_tables(value: str) -> tuple[str, bool]:
    if not value:
        return value, False
    stripped = STRUCTURED_TABLE_RE.sub("", value)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return stripped, stripped != value


def html_page() -> bytes:
    return PAGE_HTML.encode("utf-8")


class ReviewState:
    def __init__(
        self,
        candidate_path: Path,
        issue_path: Path | None,
        review_log: Path,
        *,
        auto_reload_candidates: bool = False,
        review_backend: str = "sql",
    ) -> None:
        self.candidate_path = candidate_path
        self.issue_path = issue_path
        self.review_log = review_log
        self.auto_reload_candidates = auto_reload_candidates
        self.review_backend = review_backend
        self._candidate_reload_lock = threading.Lock()
        self._candidate_reload_status: dict[str, Any] = {
            "ok": True,
            "auto_reload_candidates": auto_reload_candidates,
            "reloaded": False,
            "busy": False,
            "message": "candidate data loaded at startup",
        }
        self.database_url = os.environ.get("DATABASE_URL")
        self.sql_review_enabled = self.review_backend == "sql" and psycopg is not None and bool(self.database_url)
        self.legacy_jsonl_backup_enabled = os.environ.get("REVIEW_UI_WRITE_LEGACY_JSONL", "1").lower() not in {"0", "false", "no"}
        self._sql_local = threading.local()
        self._sql_facets_cache: dict[str, dict[str, list[str]]] = {}
        if self.sql_review_enabled:
            self.candidates = []
            self.candidate_by_key = {}
            self.issues = {}
        else:
            self.candidates = load_jsonl(candidate_path)
            self.candidate_by_key = {str(item.get("candidate_key")): item for item in self.candidates if item.get("candidate_key")}
            self.issues = load_issues(issue_path)
        self.review_log.parent.mkdir(parents=True, exist_ok=True)
        self.answer_review_log = self.review_log.parent / "answer_review_events.jsonl"
        self.ai_review_log = self.review_log.parent / "question_ai_review_events.jsonl"
        self.preference_path = self.review_log.parent / "review_ui_preferences.json"
        if self.sql_review_enabled:
            self.latest_reviews, self.review_counts, self.latest_reset_reviews = {}, {}, {}
            self.latest_group_reviews = {}
            self.latest_answer_reviews, self.answer_review_counts, self.latest_answer_reset_reviews = {}, {}, {}
            self.latest_ai_reviews, self.ai_review_counts = {}, {}
        else:
            self.latest_reviews, self.review_counts, self.latest_reset_reviews = load_review_events(review_log)
            self.latest_group_reviews = load_group_review_events(review_log)
            self.latest_answer_reviews, self.answer_review_counts, self.latest_answer_reset_reviews = load_latest_events(self.answer_review_log)
            self.latest_ai_reviews, self.ai_review_counts = load_keyed_events(
                self.ai_review_log,
                reset_actions=AI_RESET_REVIEW_ACTIONS,
            )
        self._candidate_signature = file_signature(self.candidate_path)
        self._issue_signature = file_signature(self.issue_path) if self.issue_path else None
        self._review_log_signature = file_signature(self.review_log)
        self._answer_review_log_signature = file_signature(self.answer_review_log)
        self._ai_review_log_signature = file_signature(self.ai_review_log)

    def candidate_data_status(self) -> dict[str, Any]:
        current_candidate_signature = file_signature(self.candidate_path)
        current_issue_signature = file_signature(self.issue_path) if self.issue_path else None
        return {
            **self._candidate_reload_status,
            "auto_reload_candidates": self.auto_reload_candidates,
            "candidate_stale": current_candidate_signature != self._candidate_signature,
            "issue_stale": current_issue_signature != self._issue_signature,
            "candidate_signature": self._candidate_signature,
            "current_candidate_signature": current_candidate_signature,
            "issue_signature": self._issue_signature,
            "current_issue_signature": current_issue_signature,
            "candidate_count": len(self.candidates),
            "review_backend": "sql" if self.sql_review_enabled else "jsonl",
            "sql_primary": bool(self.sql_review_enabled),
            "legacy_jsonl_backup": bool(self.legacy_jsonl_backup_enabled),
            "jsonl_status": "legacy_backup" if self.sql_review_enabled else "primary",
        }

    def reload_candidate_data(self, force: bool = False, block: bool = True) -> dict[str, Any]:
        if self.sql_review_enabled:
            self._candidate_reload_status = {
                "ok": True,
                "auto_reload_candidates": self.auto_reload_candidates,
                "busy": False,
                "reloaded": False,
                "message": "SQL backend active; JSONL candidate reload skipped",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            return self.candidate_data_status()
        acquired = self._candidate_reload_lock.acquire(blocking=block)
        if not acquired:
            self._candidate_reload_status = {
                **self._candidate_reload_status,
                "ok": False,
                "busy": True,
                "reloaded": False,
                "message": "candidate reload already running",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            return self.candidate_data_status()
        try:
            candidate_signature = file_signature(self.candidate_path)
            issue_signature = file_signature(self.issue_path) if self.issue_path else None
            candidate_stale = candidate_signature != self._candidate_signature
            issue_stale = issue_signature != self._issue_signature
            if not force and not candidate_stale and not issue_stale:
                self._candidate_reload_status = {
                    "ok": True,
                    "auto_reload_candidates": self.auto_reload_candidates,
                    "busy": False,
                    "reloaded": False,
                    "message": "candidate data already current",
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                }
                return self.candidate_data_status()
            if candidate_stale or force:
                new_candidates = load_jsonl(self.candidate_path)
                new_candidate_by_key = {
                    str(item.get("candidate_key")): item
                    for item in new_candidates
                    if item.get("candidate_key")
                }
                self.candidates = new_candidates
                self.candidate_by_key = new_candidate_by_key
                self._candidate_signature = candidate_signature
            if issue_stale or force:
                self.issues = load_issues(self.issue_path)
                self._issue_signature = issue_signature
            gc.collect()
            self._candidate_reload_status = {
                "ok": True,
                "auto_reload_candidates": self.auto_reload_candidates,
                "busy": False,
                "reloaded": bool(candidate_stale or issue_stale or force),
                "message": "candidate data reloaded",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            return self.candidate_data_status()
        except Exception as exc:
            self._candidate_reload_status = {
                "ok": False,
                "auto_reload_candidates": self.auto_reload_candidates,
                "busy": False,
                "reloaded": False,
                "message": f"candidate reload failed: {exc}",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            return self.candidate_data_status()
        finally:
            self._candidate_reload_lock.release()

    def refresh_event_logs(self) -> None:
        if self.sql_review_enabled:
            return
        if self.auto_reload_candidates:
            self.reload_candidate_data(force=False, block=False)

        review_signature = file_signature(self.review_log)
        if review_signature != self._review_log_signature:
            self.latest_reviews, self.review_counts, self.latest_reset_reviews = load_review_events(self.review_log)
            self.latest_group_reviews = load_group_review_events(self.review_log)
            self._review_log_signature = review_signature

        answer_signature = file_signature(self.answer_review_log)
        if answer_signature != self._answer_review_log_signature:
            self.latest_answer_reviews, self.answer_review_counts, self.latest_answer_reset_reviews = load_latest_events(self.answer_review_log)
            self._answer_review_log_signature = answer_signature

        ai_signature = file_signature(self.ai_review_log)
        if ai_signature != self._ai_review_log_signature:
            self.latest_ai_reviews, self.ai_review_counts = load_keyed_events(
                self.ai_review_log,
                reset_actions=AI_RESET_REVIEW_ACTIONS,
            )
            self._ai_review_log_signature = ai_signature

    def _sql_connection(self):
        if not self.sql_review_enabled or psycopg is None or not self.database_url:
            raise RuntimeError("SQL review backend is not available.")
        conn = getattr(self._sql_local, "conn", None)
        if conn is None or getattr(conn, "closed", True):
            conn = psycopg.connect(self.database_url, connect_timeout=5)
            self._sql_local.conn = conn
        return conn

    def _discard_sql_connection(self) -> None:
        conn = getattr(self._sql_local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._sql_local.conn = None

    @contextmanager
    def _sql_connect(self):
        conn = self._sql_connection()
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                self._discard_sql_connection()
            raise
        finally:
            if not getattr(conn, "closed", True):
                try:
                    conn.rollback()
                except Exception:
                    self._discard_sql_connection()

    def _candidate_by_key_sql(self, candidate_key: str) -> dict[str, Any] | None:
        if not candidate_key:
            return None
        return self._candidates_by_key_sql([candidate_key]).get(candidate_key)

    def _candidates_by_key_sql(self, candidate_keys: list[str]) -> dict[str, dict[str, Any]]:
        keys = [str(key or "") for key in candidate_keys if str(key or "")]
        if not keys:
            return {}
        results: dict[str, dict[str, Any]] = {}
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT candidate_key, raw_candidate_json
                    FROM exam.question_candidates
                    WHERE candidate_key = ANY(%s)
                    """,
                    (keys,),
                )
                for key, raw_candidate in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        results[str(key)] = raw_candidate
                    elif isinstance(raw_candidate, str):
                        results[str(key)] = json.loads(raw_candidate)
        return results

    def _sql_candidate_where(self, params: dict[str, str], ignore: str = "") -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        review_status = params.get("reviewStatus") or ""
        if review_status == "exclude":
            clauses.append("COALESCE(review_status, '') = 'excluded'")
        else:
            clauses.append("COALESCE(review_status, '') <> 'excluded'")
        filters = {
            "category": ("raw_candidate_json->'metadata'->>'normalized_category_name'", params.get("category") or ""),
            "subject": ("raw_candidate_json->'metadata'->>'normalized_subject_name'", params.get("subject") or ""),
            "year": ("raw_candidate_json->'metadata'->>'year'", params.get("year") or ""),
            "ordinal": ("raw_candidate_json->'metadata'->>'exam_ordinal'", params.get("ordinal") or ""),
        }
        for key, (expr, value) in filters.items():
            if key == ignore or not value:
                continue
            clauses.append(f"COALESCE({expr}, '') = %s")
            values.append(value)
        return clauses, values

    def sql_facets(self, params: dict[str, str] | None = None) -> dict[str, list[str]]:
        params = params or {}
        cache_key = json.dumps(
            {
                key: params.get(key) or ""
                for key in ("category", "subject", "year", "ordinal")
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if cache_key in self._sql_facets_cache:
            return self._sql_facets_cache[cache_key]
        facet_defs = {
            "categories": ("category", "raw_candidate_json->'metadata'->>'normalized_category_name'"),
            "subjects": ("subject", "raw_candidate_json->'metadata'->>'normalized_subject_name'"),
            "years": ("year", "raw_candidate_json->'metadata'->>'year'"),
            "ordinals": ("ordinal", "raw_candidate_json->'metadata'->>'exam_ordinal'"),
        }
        result: dict[str, list[str]] = {}
        try:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    for output_key, (filter_key, expr) in facet_defs.items():
                        clauses, values = self._sql_candidate_where(params, ignore=filter_key)
                        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                        cur.execute(
                            f"""
                            SELECT DISTINCT COALESCE({expr}, '')
                            FROM exam.question_candidates
                            {where}
                            """,
                            values,
                        )
                        rows = [str(row[0] or "") for row in cur.fetchall() if str(row[0] or "")]
                        if output_key in {"years", "ordinals"}:
                            rows.sort(key=lambda item: (int(item) if item.isdigit() else 9999, item))
                        else:
                            rows.sort()
                        result[output_key] = rows
        except Exception:
            return self.facets(params)
        self._sql_facets_cache[cache_key] = result
        return result

    def _sql_candidate_rows(self, params: dict[str, str], *, limit: int | None = None) -> list[dict[str, Any]]:
        clauses, values = self._sql_candidate_where(params)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = ""
        query_values = list(values)
        if limit is not None:
            limit_sql = "LIMIT %s"
            query_values.append(max(1, int(limit)))
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT raw_candidate_json
                    FROM exam.question_candidates
                    {where}
                    ORDER BY
                        COALESCE(raw_candidate_json->'metadata'->>'normalized_category_name', ''),
                        COALESCE(raw_candidate_json->'metadata'->>'normalized_subject_name', ''),
                        CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
                            THEN (raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END DESC,
                        CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
                            THEN (raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END DESC,
                        CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END,
                        candidate_key
                    {limit_sql}
                    """,
                    query_values,
                )
                rows = []
                for (raw_candidate,) in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                return rows

    def _sql_group_candidate_rows(self, params: dict[str, str]) -> list[dict[str, Any]]:
        clauses, values = self._sql_candidate_where(params)
        group_review_status = params.get("groupReviewStatus") or ""
        try:
            requested_limit = max(1, min(int(params.get("limit") or "200"), 500))
        except ValueError:
            requested_limit = 200
        row_limit = requested_limit * 20
        group_status_clause = ""
        if group_review_status == "unreviewed":
            group_status_clause = "AND (lg.action IS NULL OR lg.action = 'reset_group_review')"
        elif group_review_status == "reviewed":
            group_status_clause = "AND lg.action IN ('confirm_group', 'confirm_not_group')"
        elif group_review_status == "confirmed_group":
            group_status_clause = "AND lg.action = 'confirm_group'"
        elif group_review_status == "confirmed_not_group":
            group_status_clause = "AND lg.action = 'confirm_not_group'"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH latest_group AS (
                        SELECT DISTINCT ON (candidate_key)
                            candidate_key,
                            action
                        FROM exam.question_review_events
                        WHERE action IN ('confirm_not_group', 'confirm_group', 'reset_group_review')
                        ORDER BY candidate_key, id DESC
                    ),
                    filtered AS (
                        SELECT
                            candidate_key,
                            source_registry_key,
                            question_number,
                            raw_candidate_json,
                            COALESCE(raw_candidate_json->'metadata'->>'normalized_category_name', raw_candidate_json->'metadata'->>'group_name', '') AS category,
                            COALESCE(raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
                            COALESCE(raw_candidate_json->'metadata'->>'year', '') AS year,
                            COALESCE(raw_candidate_json->'metadata'->>'exam_ordinal', '') AS ordinal,
                            COALESCE(raw_candidate_json->>'stem', '') AS stem,
                            COALESCE(raw_candidate_json->>'group_ref', '') AS group_ref,
                            COALESCE(lg.action, '') AS group_action,
                            CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END AS qn
                        FROM exam.question_candidates
                        LEFT JOIN latest_group lg USING (candidate_key)
                        {where}
                        {group_status_clause}
                    ),
                    keyed AS (
                        SELECT
                            *,
                            concat_ws('|', category, subject, year, ordinal, source_registry_key) AS session_key
                        FROM filtered
                    ),
                    suspects AS (
                        SELECT *
                        FROM keyed
                        WHERE group_ref <> ''
                           OR group_action IN ('confirm_group', 'confirm_not_group', 'reset_group_review')
                           OR stem ~ '回答下列[[:space:]]*[0-9]{{1,2}}[[:space:]]*題'
                           OR stem ~ '^[[:space:]]*[（(]?[[:space:]]*(承上題|呈上題|上題|前述)'
                           OR stem ~ '^[[:space:]]*[0-9]{{1,3}}[[:space:]]*(-|－|~|～|至|到)[[:space:]]*[0-9]{{1,3}}'
                    ),
                    wanted AS (
                        SELECT DISTINCT
                            f.raw_candidate_json,
                            f.category,
                            f.subject,
                            CASE WHEN f.year ~ '^[0-9]+$' THEN f.year::integer ELSE 0 END AS year_sort,
                            CASE WHEN f.ordinal ~ '^[0-9]+$' THEN f.ordinal::integer ELSE 0 END AS ordinal_sort,
                            f.qn,
                            f.candidate_key
                        FROM keyed f
                        JOIN suspects s ON s.session_key = f.session_key
                        WHERE
                            (s.group_action IN ('confirm_group', 'confirm_not_group', 'reset_group_review') AND f.candidate_key = s.candidate_key)
                            OR (s.group_ref <> '' AND f.group_ref = s.group_ref)
                            OR (
                                s.stem ~ '回答下列[[:space:]]*[0-9]{{1,2}}[[:space:]]*題'
                                AND f.qn BETWEEN s.qn AND s.qn + COALESCE(NULLIF(substring(s.stem from '回答下列[[:space:]]*([0-9]{{1,2}})[[:space:]]*題'), '')::integer, 1) - 1
                            )
                            OR (
                                s.stem ~ '^[[:space:]]*[（(]?[[:space:]]*(承上題|呈上題|上題|前述)'
                                AND f.qn BETWEEN s.qn - 1 AND s.qn + 5
                            )
                            OR (
                                s.stem ~ '^[[:space:]]*[0-9]{{1,3}}[[:space:]]*(-|－|~|～|至|到)[[:space:]]*[0-9]{{1,3}}'
                                AND f.qn BETWEEN s.qn AND s.qn + 10
                            )
                    )
                    SELECT raw_candidate_json
                    FROM wanted
                    ORDER BY category, subject, year_sort DESC, ordinal_sort DESC, qn, candidate_key
                    LIMIT %s
                    """,
                    [*values, row_limit],
                )
                rows = []
                for (raw_candidate,) in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                return rows

    def _sql_candidate_filter_parts(self, params: dict[str, str]) -> tuple[str, list[Any]]:
        clauses, values = self._sql_candidate_where(params)
        q = (params.get("q") or "").strip().lower()
        status = params.get("status") or ""
        review_status = params.get("reviewStatus") or ""
        ai_review_status = params.get("aiReviewStatus") or ""
        visual_status = params.get("visualStatus") or ""
        if review_status != "exclude":
            clauses.append("COALESCE(review_action, '') <> 'exclude'")
        if status:
            clauses.append("question_quality_status = %s")
            values.append(status)
        if review_status == "not_accept":
            clauses.append(
                """
                (
                    (is_reviewed AND review_action NOT IN ('accept', 'correct', 'unblock', 'exclude'))
                    OR review_action IN ('unreviewed', 'reset_review')
                )
                """
            )
        elif review_status == "formal_drift":
            clauses.append(
                """
                physical_in_formal
                AND NOT ready_for_formal
                """
            )
        elif review_status == "formal":
            clauses.append("ready_for_formal")
        elif review_status == "answer_stage":
            clauses.append("review_action IN ('accept', 'unblock')")
        elif review_status == "correct":
            clauses.append(
                """
                is_reviewed
                AND (
                    corrected_candidate_json IS NOT NULL
                    OR COALESCE(review_event_json, '{}'::jsonb) ? 'correction'
                )
                """
            )
        elif review_status == "reset_review":
            clauses.append("review_action IN ('unreviewed', 'reset_review')")
        elif review_status == "unreviewed":
            clauses.append("NOT is_reviewed")
        elif review_status == "reviewed":
            clauses.append("is_reviewed")
        elif review_status:
            clauses.append("review_action = %s")
            values.append(review_status)
        if ai_review_status == "unreviewed":
            clauses.append("NOT ai_reviewed")
        elif ai_review_status == "reviewed":
            clauses.append("ai_reviewed")
        elif ai_review_status == "suggested_correction":
            clauses.append("ai_has_suggestion")
        elif ai_review_status == "needs_review":
            clauses.append("ai_effective_status IN ('needs_review', 'block', 'blocked')")
        elif ai_review_status:
            clauses.append("ai_effective_status = %s")
            values.append(ai_review_status)
        if visual_status == "visual":
            clauses.append("visual_review_status NOT IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem') AND NOT has_manual_asset AND (has_visual_asset OR has_visual_dependency OR has_structured_table)")
        elif visual_status == "visual_asset":
            clauses.append("visual_review_status NOT IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem') AND NOT has_manual_asset AND has_visual_asset")
        elif visual_status == "visual_missing_asset":
            clauses.append("visual_review_status = '' AND has_visual_dependency AND NOT has_visual_asset AND NOT has_structured_table")
        elif visual_status == "table":
            clauses.append("visual_review_status NOT IN ('no_visual_required', 'visual_asset_ok', 'visual_asset_problem') AND NOT has_manual_asset AND has_structured_table")
        elif visual_status == "manual_asset":
            clauses.append("has_manual_asset")
        elif visual_status == "visual_ok":
            clauses.append("(visual_review_status = 'visual_asset_ok' OR has_manual_asset)")
        elif visual_status == "no_visual":
            clauses.append("visual_review_status = 'no_visual_required'")
        elif visual_status == "visual_problem":
            clauses.append("visual_review_status = 'visual_asset_problem'")
        if q:
            clauses.append(
                """
                lower(concat_ws(
                    ' ',
                    candidate_key,
                    question_number,
                    stem_text,
                    category,
                    subject,
                    review_action,
                    review_notes,
                    review_event_json::text,
                    ai_provider,
                    ai_model_name,
                    ai_effective_status,
                    ai_audit_json::text
                )) LIKE %s
                """
            )
            values.append(f"%{q}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cte = f"""
WITH latest_question AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        corrected_candidate_json,
        event_json,
        notes,
        created_at,
        id
    FROM exam.question_review_events
    WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
    ORDER BY candidate_key, id DESC
),
latest_visual AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        corrected_candidate_json,
        event_json,
        notes,
        created_at,
        id
    FROM exam.question_review_events
    WHERE action = 'human_review_pdf_visual'
    ORDER BY candidate_key, id DESC
),
latest_answer AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        created_at,
        id
    FROM exam.answer_review_events
    ORDER BY candidate_key, id DESC
),
latest_ai AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        provider,
        model_name,
        audit_status,
        recommended_action,
        audit_json,
        event_json,
        created_at,
        id
    FROM exam.question_ai_review_events
    ORDER BY candidate_key, id DESC
),
issue_flags AS (
    SELECT
        candidate_key,
        bool_or(severity IN ('blocked', 'error')) AS has_blocking_issue,
        bool_or(severity = 'warning') AS has_warning_issue
    FROM exam.question_parse_issues
    WHERE issue_code NOT IN ('missing_answer', 'missing_answer_markdown', 'unexpected_answer_value')
    GROUP BY candidate_key
),
base AS (
    SELECT
        c.candidate_key,
        c.question_number,
        c.stem_text,
        c.raw_candidate_json || jsonb_strip_nulls(jsonb_build_object(
            'visual_review',
            NULLIF(COALESCE(lv.corrected_candidate_json->>'visual_review', lq.corrected_candidate_json->>'visual_review', ''), '')
        )) AS raw_candidate_json,
        c.review_status,
        (fq.question_key IS NOT NULL) AS physical_in_formal,
        (lq.action IN ('accept', 'unblock') AND la.action IN ('accept', 'unblock')) AS ready_for_formal,
        (fq.question_key IS NOT NULL) AS in_formal,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') AS category,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
        COALESCE(c.raw_candidate_json->'metadata'->>'year', '') AS year,
        COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') AS ordinal,
        CASE
            WHEN COALESCE(i.has_blocking_issue, false) THEN 'blocked'
            WHEN COALESCE(i.has_warning_issue, false) THEN 'needs_review'
            ELSE 'pass'
        END AS question_quality_status,
        lq.action AS review_action,
        lq.corrected_candidate_json,
        lq.event_json AS review_event_json,
        lq.notes AS review_notes,
        la.action AS answer_review_action,
        (lq.action IS NOT NULL AND lq.action NOT IN ('unreviewed', 'reset_review')) AS is_reviewed,
        lai.action AS ai_action,
        lai.provider AS ai_provider,
        lai.model_name AS ai_model_name,
        lai.audit_json AS ai_audit_json,
        (
            COALESCE(lv.corrected_candidate_json->>'visual_review', lq.corrected_candidate_json->>'visual_review', '') = 'no_visual_required'
        ) AS no_visual_required,
        COALESCE(lv.corrected_candidate_json->>'visual_review', lq.corrected_candidate_json->>'visual_review', '') AS visual_review_status,
        (
            CASE
                WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'image_refs') = 'array'
                    THEN jsonb_array_length((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'image_refs')
                ELSE 0
            END > 0
            OR CASE
                WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs') = 'array'
                    THEN jsonb_array_length((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs')
                ELSE 0
            END > 0
            OR jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'stem_image') = 'object'
            OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'options') = 'array'
                            THEN (c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'options'
                        ELSE '[]'::jsonb
                    END
                ) AS option_row(value)
                WHERE jsonb_typeof(option_row.value->'image') = 'object'
            )
        ) AS has_visual_asset,
        (
            concat_ws(
                ' ',
                c.stem_text,
                c.raw_candidate_json->>'stem',
                c.raw_candidate_json->'metadata'->>'raw_block',
                lq.corrected_candidate_json->>'stem'
            ) ~* '{VISUAL_DEPENDENCY_SQL_RE}'
        ) AS has_visual_dependency,
        (
            position('<table' in lower(concat_ws(' ', c.raw_candidate_json->>'stem', lq.corrected_candidate_json->>'stem'))) > 0
            OR concat_ws(
                ' ',
                c.stem_text,
                c.raw_candidate_json->>'stem',
                c.raw_candidate_json->'metadata'->>'raw_block',
                lq.corrected_candidate_json->>'stem'
            ) ~* '{TABLE_DEPENDENCY_SQL_RE}'
        ) AS has_structured_table,
        EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'image_refs') = 'array'
                        THEN (c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'image_refs'
                    ELSE '[]'::jsonb
                END
            ) AS ref_row(value)
            WHERE ref_row.value->>'manual_asset' IN ('true', '1')
               OR COALESCE(ref_row.value->>'asset_role', ref_row.value->>'role', '') LIKE '%%manual%%'
               OR COALESCE(ref_row.value->>'asset_role', ref_row.value->>'role', '') = 'table_manual_screenshot'
        )
        OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs') = 'array'
                        THEN (c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'answer_image_refs'
                    ELSE '[]'::jsonb
                END
            ) AS answer_ref_row(value)
            WHERE answer_ref_row.value->>'manual_asset' IN ('true', '1')
               OR COALESCE(answer_ref_row.value->>'asset_role', answer_ref_row.value->>'role', '') LIKE '%%manual%%'
        )
        OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof((c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'options') = 'array'
                        THEN (c.raw_candidate_json || COALESCE(lq.corrected_candidate_json, '{{}}'::jsonb))->'options'
                    ELSE '[]'::jsonb
                END
            ) AS option_manual_row(value)
            WHERE option_manual_row.value->'image'->>'manual_asset' IN ('true', '1')
        ) AS has_manual_asset,
        (lai.action IS NOT NULL AND lai.action NOT IN ('unreviewed', 'reset_review', 'reset_ai_review')) AS ai_reviewed,
        (
            COALESCE(lai.audit_json, '{{}}'::jsonb) ? 'suggested_correction'
            OR COALESCE(lai.event_json->'audit', '{{}}'::jsonb) ? 'suggested_correction'
            OR COALESCE(lai.event_json, '{{}}'::jsonb) ? 'suggested_correction'
        ) AS ai_has_suggestion,
        CASE
            WHEN lai.action IN ('unreviewed', 'reset_review', 'reset_ai_review') OR lai.action IS NULL THEN NULL
            WHEN lai.audit_status IN ('block', 'blocked') THEN 'block'
            WHEN lai.audit_status = 'needs_review' THEN 'needs_review'
            WHEN jsonb_typeof(COALESCE(lai.audit_json->'findings', '[]'::jsonb)) = 'array'
                AND jsonb_array_length(COALESCE(lai.audit_json->'findings', '[]'::jsonb)) > 0 THEN 'needs_review'
            WHEN COALESCE(lai.recommended_action, '') NOT IN ('', 'no_action') THEN 'needs_review'
            WHEN COALESCE(lai.audit_json, '{{}}'::jsonb) ? 'suggested_correction' THEN 'needs_review'
            ELSE COALESCE(lai.audit_status, 'pass')
        END AS ai_effective_status,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END AS year_sort,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END AS ordinal_sort,
        CASE WHEN c.question_number ~ '^[0-9]+$' THEN c.question_number::integer ELSE 0 END AS question_sort
    FROM exam.question_candidates c
    LEFT JOIN exam.questions fq ON fq.question_key = c.candidate_key
    LEFT JOIN issue_flags i ON i.candidate_key = c.candidate_key
    LEFT JOIN latest_question lq ON lq.candidate_key = c.candidate_key
    LEFT JOIN latest_visual lv ON lv.candidate_key = c.candidate_key
    LEFT JOIN latest_answer la ON la.candidate_key = c.candidate_key
    LEFT JOIN latest_ai lai ON lai.candidate_key = c.candidate_key
),
filtered AS (
    SELECT *
    FROM base
    {where}
)
"""
        return cte, values

    def _sql_can_use_light_candidate_query(self, params: dict[str, str]) -> bool:
        return (
            not (params.get("q") or "").strip()
            and not (params.get("status") or "")
            and not (params.get("aiReviewStatus") or "")
            and not (params.get("visualStatus") or "")
        )

    def _sql_light_candidate_filter_parts(self, params: dict[str, str]) -> tuple[str, list[Any]]:
        clauses, values = self._sql_candidate_where(params)
        review_status = params.get("reviewStatus") or ""
        if review_status != "exclude":
            clauses.append("COALESCE(review_action, '') <> 'exclude'")
        if review_status == "not_accept":
            clauses.append(
                """
                (
                    (is_reviewed AND review_action NOT IN ('accept', 'correct', 'unblock', 'exclude'))
                    OR review_action IN ('unreviewed', 'reset_review')
                    OR review_action IS NULL
                )
                """
            )
        elif review_status == "formal_drift":
            clauses.append(
                """
                physical_in_formal
                AND NOT ready_for_formal
                """
            )
        elif review_status == "formal":
            clauses.append("ready_for_formal")
        elif review_status == "answer_stage":
            clauses.append("review_action IN ('accept', 'unblock')")
        elif review_status == "correct":
            clauses.append(
                """
                is_reviewed
                AND (
                    corrected_candidate_json IS NOT NULL
                    OR COALESCE(review_event_json, '{}'::jsonb) ? 'correction'
                )
                """
            )
        elif review_status == "reset_review":
            clauses.append("review_action IN ('unreviewed', 'reset_review')")
        elif review_status == "unreviewed":
            clauses.append("NOT is_reviewed")
        elif review_status == "reviewed":
            clauses.append("is_reviewed")
        elif review_status:
            clauses.append("review_action = %s")
            values.append(review_status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cte = f"""
WITH latest_question AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        corrected_candidate_json,
        event_json,
        notes,
        created_at,
        id
    FROM exam.question_review_events
    WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
    ORDER BY candidate_key, id DESC
),
latest_answer AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        created_at,
        id
    FROM exam.answer_review_events
    ORDER BY candidate_key, id DESC
),
base AS (
    SELECT
        c.candidate_key,
        c.question_number,
        c.raw_candidate_json,
        c.review_status,
        (fq.question_key IS NOT NULL) AS physical_in_formal,
        (lq.action IN ('accept', 'unblock') AND la.action IN ('accept', 'unblock')) AS ready_for_formal,
        (fq.question_key IS NOT NULL) AS in_formal,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') AS category,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
        COALESCE(c.raw_candidate_json->'metadata'->>'year', '') AS year,
        COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') AS ordinal,
        lq.action AS review_action,
        lq.corrected_candidate_json,
        lq.event_json AS review_event_json,
        la.action AS answer_review_action,
        (lq.action IS NOT NULL AND lq.action NOT IN ('unreviewed', 'reset_review')) AS is_reviewed,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END AS year_sort,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END AS ordinal_sort,
        CASE WHEN c.question_number ~ '^[0-9]+$' THEN c.question_number::integer ELSE 0 END AS question_sort
    FROM exam.question_candidates c
    LEFT JOIN exam.questions fq ON fq.question_key = c.candidate_key
    LEFT JOIN latest_question lq ON lq.candidate_key = c.candidate_key
    LEFT JOIN latest_answer la ON la.candidate_key = c.candidate_key
),
filtered AS (
    SELECT *
    FROM base
    {where}
)
"""
        return cte, values

    def _sql_plain_candidate_rows_and_counts(
        self,
        params: dict[str, str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        clauses, values = self._sql_candidate_where(params)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        scoped = bool(clauses)
        order_clause = """
            COALESCE(raw_candidate_json->'metadata'->>'normalized_category_name', raw_candidate_json->'metadata'->>'group_name', ''),
            COALESCE(raw_candidate_json->'metadata'->>'normalized_subject_name', ''),
            CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
                THEN (raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END DESC,
            CASE WHEN COALESCE(raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
                THEN (raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END DESC,
            CASE WHEN question_number ~ '^[0-9]+$' THEN question_number::integer ELSE 0 END,
            candidate_key
        """ if scoped else "id"
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT raw_candidate_json
                    FROM exam.question_candidates
                    {where}
                    ORDER BY {order_clause}
                    LIMIT %s
                    """,
                    [*values, limit],
                )
                rows: list[dict[str, Any]] = []
                for (raw_candidate,) in cur.fetchall():
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                cur.execute(f"SELECT count(*) FROM exam.question_candidates {where}", values)
                filtered_count = int(cur.fetchone()[0])
                if (params.get("reviewStatus") or "") == "exclude":
                    cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') = 'excluded'")
                else:
                    cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') <> 'excluded'")
                total_count = int(cur.fetchone()[0])
                reviewed_count = 0
                if scoped:
                    cur.execute(
                        f"""
                        WITH latest_question AS (
                            SELECT DISTINCT ON (candidate_key) candidate_key, action, created_at, id
                            FROM exam.question_review_events
                            WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
                            ORDER BY candidate_key, id DESC
                        )
                        SELECT count(*)
                        FROM exam.question_candidates c
                        JOIN latest_question lq ON lq.candidate_key = c.candidate_key
                        {where}
                          AND lq.action NOT IN ('unreviewed', 'reset_review')
                        """,
                        values,
                    )
                    reviewed_count = int(cur.fetchone()[0])
                return rows, filtered_count, reviewed_count, total_count

    def _sql_filtered_candidate_rows_and_counts(
        self,
        params: dict[str, str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        if self._sql_can_use_light_candidate_query(params) and not (params.get("reviewStatus") or ""):
            return self._sql_plain_candidate_rows_and_counts(params, limit)
        if self._sql_can_use_light_candidate_query(params):
            cte, values = self._sql_light_candidate_filter_parts(params)
        else:
            cte, values = self._sql_candidate_filter_parts(params)
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    {cte}
                    SELECT
                        raw_candidate_json,
                        count(*) OVER () AS filtered_count,
                        count(*) FILTER (WHERE is_reviewed) OVER () AS reviewed_count
                    FROM filtered
                    ORDER BY category, subject, year_sort DESC, ordinal_sort DESC, question_sort, candidate_key
                    LIMIT %s
                    """,
                    [*values, limit],
                )
                rows: list[dict[str, Any]] = []
                filtered_count = 0
                reviewed_count = 0
                for raw_candidate, row_filtered_count, row_reviewed_count in cur.fetchall():
                    filtered_count = int(row_filtered_count or 0)
                    reviewed_count = int(row_reviewed_count or 0)
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                if not rows:
                    cur.execute(
                        f"""
                        {cte}
                        SELECT
                            count(*) AS filtered_count,
                            count(*) FILTER (WHERE is_reviewed) AS reviewed_count
                        FROM filtered
                        """,
                        values,
                    )
                    count_row = cur.fetchone()
                    filtered_count = int(count_row[0] or 0) if count_row else 0
                    reviewed_count = int(count_row[1] or 0) if count_row else 0
                if (params.get("reviewStatus") or "") == "exclude":
                    cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') = 'excluded'")
                else:
                    cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') <> 'excluded'")
                total_count = int(cur.fetchone()[0])
                return rows, filtered_count, reviewed_count, total_count

    def _sql_issue_map(self, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not keys:
            return {}
        issues: dict[str, list[dict[str, Any]]] = {}
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT candidate_key, issue_code, severity, message, issue_json
                    FROM exam.question_parse_issues
                    WHERE candidate_key = ANY(%s)
                    ORDER BY id
                    """,
                    (keys,),
                )
                for key, issue_code, severity, message, issue_json in cur.fetchall():
                    issues.setdefault(key, []).append(
                        {
                            "candidate_key": key,
                            "issue_code": issue_code,
                            "severity": severity,
                            "message": message,
                            "issue_json": issue_json or {},
                        }
                    )
        return issues

    def _sql_formal_question_map(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        if not keys:
            return {}
        formal: dict[str, dict[str, Any]] = {}
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT question_key, id, review_status, created_at
                    FROM exam.questions
                    WHERE question_key = ANY(%s)
                    """,
                    (keys,),
                )
                for question_key, question_id, review_status, created_at in cur.fetchall():
                    formal[str(question_key)] = {
                        "in_formal": True,
                        "question_id": int(question_id),
                        "review_status": review_status,
                        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                    }
        return formal

    def _db_event_value(self, event_json: Any, fallback: dict[str, Any]) -> dict[str, Any]:
        if isinstance(event_json, dict):
            event = dict(event_json)
        elif isinstance(event_json, str) and event_json.strip():
            try:
                event = json.loads(event_json)
            except json.JSONDecodeError:
                event = {}
        else:
            event = {}
        merged = dict(fallback)
        merged.update(event)
        return merged

    def _legacy_jsonl_storage(self, path: Path, event: dict[str, Any]) -> dict[str, Any]:
        if not self.legacy_jsonl_backup_enabled and self.sql_review_enabled:
            return {"ok": True, "enabled": False, "path": str(path)}
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            return {"ok": True, "enabled": True, "path": str(path)}
        except Exception as exc:
            if self.sql_review_enabled:
                return {"ok": False, "enabled": True, "path": str(path), "error": str(exc)}
            raise

    def _sql_latest_question_review_event(self, candidate_key: str) -> dict[str, Any] | None:
        if not self.sql_review_enabled or not candidate_key:
            return None
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT action, corrected_candidate_json, event_json, notes, reviewer, created_at
                    FROM exam.question_review_events
                    WHERE candidate_key = %s
                      AND action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (candidate_key,),
                )
                row = cur.fetchone()
        if not row:
            return None
        action, correction, event_json, notes, reviewer, created_at = row
        return self._db_event_value(
            event_json,
            {
                "candidate_key": candidate_key,
                "action": action,
                "correction": correction,
                "notes": notes,
                "reviewer": reviewer,
                "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
            },
        )

    def current_question_review(self, candidate_key: str) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self._sql_latest_question_review_event(candidate_key) or {}
        return self.latest_reviews.get(candidate_key) or {}

    def _sql_question_review_maps(
        self,
        keys: list[str],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
        latest: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = {}
        latest_reset: dict[str, dict[str, Any]] = {}
        if not keys:
            return latest, counts, latest_reset
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT candidate_key, action, corrected_candidate_json, event_json, notes, reviewer, created_at
                    FROM exam.question_review_events
                    WHERE candidate_key = ANY(%s)
                    ORDER BY candidate_key, id
                    """,
                    (keys,),
                )
                for key, action, correction, event_json, notes, reviewer, created_at in cur.fetchall():
                    if action in GROUP_REVIEW_ACTIONS:
                        counts[key] = counts.get(key, 0) + 1
                        continue
                    event = self._db_event_value(
                        event_json,
                        {
                            "candidate_key": key,
                            "action": action,
                            "correction": correction,
                            "notes": notes,
                            "reviewer": reviewer,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                        },
                    )
                    counts[key] = counts.get(key, 0) + 1
                    if event.get("action") in RESET_REVIEW_ACTIONS:
                        latest.pop(key, None)
                        latest_reset[key] = event
                        continue
                    if "correction" not in event and key in latest and latest[key].get("correction"):
                        event["correction"] = latest[key]["correction"]
                    latest[key] = event
                    latest_reset.pop(key, None)
        return latest, counts, latest_reset

    def _sql_group_review_maps(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        if not keys:
            return latest
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (candidate_key)
                        candidate_key,
                        action,
                        event_json,
                        notes,
                        reviewer,
                        created_at
                    FROM exam.question_review_events
                    WHERE candidate_key = ANY(%s)
                      AND action IN ('confirm_not_group', 'confirm_group', 'reset_group_review')
                    ORDER BY candidate_key, id DESC
                    """,
                    (keys,),
                )
                for key, action, event_json, notes, reviewer, created_at in cur.fetchall():
                    event = self._db_event_value(
                        event_json,
                        {
                            "candidate_key": key,
                            "action": action,
                            "notes": notes,
                            "reviewer": reviewer,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                        },
                    )
                    latest[key] = event
        return latest

    def _sql_latest_event_maps(
        self,
        table: str,
        keys: list[str],
        *,
        reset_actions: set[str],
        ai: bool = False,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, dict[str, Any]]]:
        latest: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = {}
        latest_reset: dict[str, dict[str, Any]] = {}
        if not keys:
            return latest, counts, latest_reset
        if table not in {"exam.answer_review_events", "exam.question_ai_review_events"}:
            raise ValueError(table)
        if ai:
            sql = """
                SELECT candidate_key, action, audit_json, event_json, notes, reviewer, provider, model_name, prompt_version, input_hash, created_at
                FROM exam.question_ai_review_events
                WHERE candidate_key = ANY(%s)
                ORDER BY candidate_key, id
            """
        else:
            sql = """
                SELECT candidate_key, action, reviewed_answer_json, corrected_answer_json, event_json, notes, reviewer, answer_source_registry_key, created_at
                FROM exam.answer_review_events
                WHERE candidate_key = ANY(%s)
                ORDER BY candidate_key, id
            """
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (keys,))
                for row in cur.fetchall():
                    if ai:
                        key, action, audit_json, event_json, notes, reviewer, provider, model_name, prompt_version, input_hash, created_at = row
                        fallback = {
                            "candidate_key": key,
                            "action": action,
                            "audit": audit_json or {},
                            "notes": notes,
                            "reviewer": reviewer,
                            "provider": provider,
                            "model": model_name,
                            "prompt_version": prompt_version,
                            "input_hash": input_hash,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                        }
                    else:
                        key, action, reviewed_answer, corrected_answer, event_json, notes, reviewer, answer_source_registry_key, created_at = row
                        fallback = {
                            "candidate_key": key,
                            "answer_source_registry_key": answer_source_registry_key,
                            "action": action,
                            "reviewed_answer": reviewed_answer,
                            "corrected_answer": corrected_answer,
                            "notes": notes,
                            "reviewer": reviewer,
                            "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                        }
                    event = self._db_event_value(event_json, fallback)
                    counts[key] = counts.get(key, 0) + 1
                    if event.get("action") in reset_actions:
                        latest.pop(key, None)
                        latest_reset[key] = event
                        continue
                    latest[key] = event
                    latest_reset.pop(key, None)
        return latest, counts, latest_reset

    def batch_accept_questions(self, candidate_keys: list[str], reviewer: str = "local", notes: str = "") -> dict[str, Any]:
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()
        ordered_keys = []
        for raw_key in candidate_keys:
            key = str(raw_key or "")
            if key and key not in seen:
                seen.add(key)
                ordered_keys.append(key)
        seen.clear()
        sql_items = self._candidates_by_key_sql(ordered_keys) if self.sql_review_enabled else {}
        sql_issues = self._sql_issue_map(ordered_keys) if self.sql_review_enabled else {}
        sql_latest_ai, _sql_ai_counts, _sql_ai_reset = self._sql_latest_event_maps(
            "exam.question_ai_review_events",
            ordered_keys,
            reset_actions=AI_RESET_REVIEW_ACTIONS,
            ai=True,
        ) if self.sql_review_enabled else ({}, {}, {})
        for raw_key in candidate_keys:
            key = str(raw_key or "")
            if not key or key in seen:
                continue
            seen.add(key)
            item = sql_items.get(key) if self.sql_review_enabled else self.candidate_by_key.get(key)
            if not item:
                skipped.append({"candidate_key": key, "reason": "not_found"})
                continue
            latest = self.current_question_review(key)
            latest_action = latest.get("action") if latest else None
            if latest_action in {"block", "exclude", "needs_review"}:
                skipped.append({"candidate_key": key, "reason": f"manual_{latest_action}"})
                continue
            if latest_action in {"accept", "unblock"}:
                skipped.append({"candidate_key": key, "reason": "already_accepted"})
                continue
            latest_ai = sql_latest_ai.get(key) if self.sql_review_enabled else self.latest_ai_reviews.get(key)
            latest_ai_audit = latest_ai.get("audit") if latest_ai else None
            ai_suggestion, _ai_suggestion_changes = ai_suggested_correction(item, latest_ai_audit)
            ai_status = effective_ai_audit_status(latest_ai_audit, ai_suggestion)
            if ai_status in {"needs_review", "block"}:
                skipped.append({"candidate_key": key, "reason": f"ai_{ai_status}"})
                continue
            issue_map = sql_issues if self.sql_review_enabled else self.issues
            question_issues = [issue for issue in issue_map.get(key, []) if issue.get("issue_code") not in ANSWER_ISSUE_CODES]
            quality = issue_quality_status(question_issues)
            if quality != "pass":
                skipped.append({"candidate_key": key, "reason": f"quality_{quality}"})
                continue
            event = {
                "candidate_key": key,
                "action": "accept",
                "notes": notes,
                "reviewer": reviewer,
                "batch_action": "accept_visible_pass",
            }
            correction = normalized_correction(latest.get("correction") if latest else None)
            if correction:
                event["correction"] = correction
            try:
                saved.append(self.append_review(event))
            except SqlWriteError as exc:
                skipped.append({"candidate_key": key, "reason": f"sql_write_failed: {exc}"})
        return {"saved": saved, "skipped": skipped}

    def confirm_not_group(self, candidate_keys: list[str], reviewer: str = "local", notes: str = "", group_sheet_key: str = "") -> dict[str, Any]:
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_key in candidate_keys:
            key = str(raw_key or "")
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in self.candidate_by_key and not self.sql_candidate_exists(key):
                skipped.append({"candidate_key": key, "reason": "not_found"})
                continue
            event = {
                "candidate_key": key,
                "action": "confirm_not_group",
                "reviewer": reviewer,
                "notes": notes or "題組審核：人工確認此候選不是題組，不應再出現在題組待審清單。",
                "group_sheet_key": group_sheet_key,
                "review_layer": "group",
            }
            try:
                saved.append(self.append_review(event))
            except SqlWriteError as exc:
                skipped.append({"candidate_key": key, "reason": f"sql_write_failed: {exc}"})
        return {"saved": saved, "skipped": skipped}

    def reset_group_review(self, candidate_keys: list[str], reviewer: str = "local", notes: str = "", group_sheet_key: str = "") -> dict[str, Any]:
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_key in candidate_keys:
            key = str(raw_key or "")
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in self.candidate_by_key and not self.sql_candidate_exists(key):
                skipped.append({"candidate_key": key, "reason": "not_found"})
                continue
            event = {
                "candidate_key": key,
                "action": "reset_group_review",
                "reviewer": reviewer,
                "notes": notes or "題組審核：人工退回題組層未審，不改變審題與答案狀態。",
                "group_sheet_key": group_sheet_key,
                "review_layer": "group",
            }
            try:
                saved.append(self.append_review(event))
            except SqlWriteError as exc:
                skipped.append({"candidate_key": key, "reason": f"sql_write_failed: {exc}"})
        if self.sql_review_enabled and seen:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE exam.questions
                        SET question_group_id = NULL,
                            group_sequence_no = NULL
                        WHERE question_key = ANY(%s)
                        """,
                        (list(seen),),
                    )
                conn.commit()
        return {"saved": saved, "skipped": skipped}

    def _candidate_rows_for_group(self, candidate_keys: list[str]) -> list[dict[str, Any]]:
        candidate_keys = list(dict.fromkeys(str(key) for key in candidate_keys if str(key or "").strip()))
        if not candidate_keys:
            return []
        if self.sql_review_enabled:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT candidate_key, source_registry_key, source_document_id, question_number, raw_candidate_json
                        FROM exam.question_candidates
                        WHERE candidate_key = ANY(%s)
                        """,
                        (candidate_keys,),
                    )
                    rows = []
                    for key, source_registry_key, source_document_id, question_number, raw_candidate in cur.fetchall():
                        payload = raw_candidate if isinstance(raw_candidate, dict) else json.loads(raw_candidate)
                        rows.append(
                            {
                                "candidate_key": key,
                                "source_registry_key": source_registry_key,
                                "source_document_id": source_document_id,
                                "question_number": question_number,
                                "raw_candidate_json": payload,
                            }
                        )
                    return rows
        rows = []
        for key in candidate_keys:
            item = self.candidate_by_key.get(key)
            if item:
                rows.append(
                    {
                        "candidate_key": key,
                        "source_registry_key": item.get("source_registry_key"),
                        "source_document_id": None,
                        "question_number": item.get("question_number"),
                        "raw_candidate_json": item,
                    }
                )
        return rows

    def _upsert_sql_question_group(
        self,
        candidate_keys: list[str],
        *,
        group_ref: str,
        group_type: str,
        shared_stem: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False}
        rows = self._candidate_rows_for_group(candidate_keys)
        if not rows:
            return {"ok": False, "error": "no candidate rows for group"}
        rows.sort(key=lambda row: int_or_zero(row.get("question_number")))
        first = rows[0]
        source_registry_key = str(first.get("source_registry_key") or "")
        group_key = f"{source_registry_key}:{group_ref}"
        official_document_id = first.get("source_document_id")
        question_numbers = [str(row.get("question_number") or "") for row in rows]
        range_label = f"q{int_or_zero(question_numbers[0]):03d}-q{int_or_zero(question_numbers[-1]):03d}" if question_numbers else group_ref
        metadata_payload = {
            **metadata,
            "candidate_keys": candidate_keys,
            "question_numbers": question_numbers,
            "source_registry_key": source_registry_key,
            "group_ref": group_ref,
        }
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_groups (
                        official_document_id,
                        group_key,
                        group_type,
                        shared_stem_text,
                        stem,
                        shared_stem_json,
                        metadata,
                        group_question_range,
                        review_status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'accepted')
                    ON CONFLICT (group_key) DO UPDATE
                    SET group_type = EXCLUDED.group_type,
                        shared_stem_text = EXCLUDED.shared_stem_text,
                        stem = EXCLUDED.stem,
                        shared_stem_json = EXCLUDED.shared_stem_json,
                        metadata = EXCLUDED.metadata,
                        group_question_range = EXCLUDED.group_question_range,
                        review_status = 'accepted'
                    RETURNING id
                    """,
                    (
                        official_document_id,
                        group_key,
                        group_type,
                        shared_stem or None,
                        shared_stem or None,
                        Jsonb({"text": shared_stem}) if Jsonb is not None and shared_stem else None,
                        Jsonb(metadata_payload) if Jsonb is not None else json.dumps(metadata_payload, ensure_ascii=False),
                        range_label,
                    ),
                )
                group_id = int(cur.fetchone()[0])
                for sequence_no, key in enumerate(candidate_keys, start=1):
                    cur.execute(
                        """
                        UPDATE exam.questions
                        SET question_group_id = %s,
                            group_sequence_no = %s
                        WHERE question_key = %s
                        """,
                        (group_id, sequence_no, key),
                    )
                cur.execute(
                    """
                    UPDATE exam.question_groups g
                    SET anchor_question_id = q.id
                    FROM exam.questions q
                    WHERE g.id = %s
                      AND q.question_key = %s
                    """,
                    (group_id, candidate_keys[0]),
                )
            conn.commit()
        return {"ok": True, "sql_primary": True, "group_id": group_id, "group_key": group_key}

    def confirm_group(
        self,
        candidate_keys: list[str],
        *,
        reviewer: str = "local",
        notes: str = "",
        group_ref: str = "",
        group_type: str = "shared_stem",
        shared_stem: str = "",
        group_sheet_key: str = "",
    ) -> dict[str, Any]:
        unique_keys = list(dict.fromkeys(str(key) for key in candidate_keys if str(key or "").strip()))
        rows = self._candidate_rows_for_group(unique_keys)
        rows.sort(key=lambda row: int_or_zero(row.get("question_number")))
        ordered_keys = [str(row["candidate_key"]) for row in rows]
        if not ordered_keys:
            return {"saved": [], "skipped": [{"candidate_key": "", "reason": "no_valid_candidates"}], "group": {}}
        if group_type not in {"shared_stem", "chained_context", "manual_range", "unknown"}:
            group_type = "shared_stem"
        if not group_ref:
            first_no = int_or_zero(rows[0].get("question_number"))
            last_no = int_or_zero(rows[-1].get("question_number"))
            group_ref = f"q{first_no:03d}-q{last_no:03d}" if first_no and last_no else group_sheet_key or "manual_group"
        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        member_summary = [
            {"candidate_key": key, "group_sequence_no": index + 1}
            for index, key in enumerate(ordered_keys)
        ]
        for index, key in enumerate(ordered_keys, start=1):
            event = {
                "candidate_key": key,
                "action": "confirm_group",
                "reviewer": reviewer,
                "notes": notes or f"題組審核：人工確認屬於 {group_ref}。",
                "group_sheet_key": group_sheet_key,
                "review_layer": "group",
                "group_ref": group_ref,
                "group_type": group_type,
                "group_sequence_no": index,
                "group_members": member_summary,
                "shared_stem": shared_stem,
            }
            try:
                saved.append(self.append_review(event))
            except SqlWriteError as exc:
                skipped.append({"candidate_key": key, "reason": f"sql_write_failed: {exc}"})
        group_result = self._upsert_sql_question_group(
            ordered_keys,
            group_ref=group_ref,
            group_type=group_type,
            shared_stem=shared_stem,
            metadata={"group_sheet_key": group_sheet_key, "reviewer": reviewer},
        )
        return {"saved": saved, "skipped": skipped, "group": group_result}

    def candidate_keys_for_manual_group_range(self, seed_candidate_key: str, range_text: str) -> list[str]:
        match = re.search(r"(\d{1,3})\s*(?:-|－|~|～|至|到)\s*(\d{1,3})", str(range_text or ""))
        if not match:
            return []
        start, end = int(match.group(1)), int(match.group(2))
        if start <= 0 or end < start or end > start + 30:
            return []
        seed_source = ""
        seed_item = self.candidate_by_key.get(seed_candidate_key)
        if seed_item:
            seed_source = str(seed_item.get("source_registry_key") or "")
        if self.sql_review_enabled:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    if not seed_source:
                        cur.execute(
                            "SELECT source_registry_key FROM exam.question_candidates WHERE candidate_key = %s",
                            (seed_candidate_key,),
                        )
                        row = cur.fetchone()
                        seed_source = str(row[0] or "") if row else ""
                    if not seed_source:
                        return []
                    cur.execute(
                        """
                        SELECT candidate_key
                        FROM exam.question_candidates
                        WHERE source_registry_key = %s
                          AND question_number ~ '^[0-9]+$'
                          AND question_number::integer BETWEEN %s AND %s
                        ORDER BY question_number::integer, candidate_key
                        """,
                        (seed_source, start, end),
                    )
                    return [str(row[0]) for row in cur.fetchall()]
        if not seed_source:
            return []
        rows = [
            item for item in self.candidates
            if str(item.get("source_registry_key") or "") == seed_source
            and start <= int_or_zero(item.get("question_number")) <= end
        ]
        rows.sort(key=lambda item: int_or_zero(item.get("question_number")))
        return [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]

    def sql_candidate_exists(self, candidate_key: str) -> bool:
        if not self.sql_review_enabled:
            return False
        try:
            with self._sql_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM exam.question_candidates WHERE candidate_key = %s LIMIT 1",
                        (candidate_key,),
                    )
                    return cur.fetchone() is not None
        except Exception:
            return False

    def save_manual_image_asset(
        self,
        candidate_key: str,
        data_url: str,
        *,
        reviewer: str = "local",
        notes: str = "",
        caption: str = "",
        asset_role: str = "manual_question_image",
        placement: str = "stem",
        target_option: str = "",
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        candidate = self.candidate_by_key.get(candidate_key)
        if not candidate and self.sql_review_enabled:
            candidate = self._candidate_by_key_sql(candidate_key)
        if not candidate:
            raise KeyError(candidate_key)
        image_bytes, mime_type, extension = data_url_to_bytes(data_url)
        full_hash = hashlib.sha256(image_bytes).hexdigest()
        digest = full_hash[:16]
        metadata = candidate.get("metadata") or {}
        category = safe_path_segment(metadata.get("normalized_category_name") or metadata.get("group_name"))
        subject = safe_path_segment(metadata.get("normalized_subject_name"))
        year = safe_path_segment(metadata.get("year"))
        ordinal = safe_path_segment(metadata.get("exam_ordinal"))
        question_number = safe_path_segment(candidate.get("question_number"), "q")
        target_dir = MANUAL_ASSET_ROOT / "question_images" / category / subject / f"{year}_{ordinal}"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{year}{ordinal}_q{question_number}_{digest}{extension}"
        target = target_dir / filename
        if not target.exists():
            target.write_bytes(image_bytes)

        asset_ref = {
            "asset_key": f"{candidate_key}:manual_image:{digest}",
            "raw_ref": filename,
            "path": display_path(target),
            "path_relative": display_path(target),
            "exists": True,
            "asset_role": asset_role or "manual_question_image",
            "placement": placement or "stem",
            "target_option": target_option or "",
            "source": "human_paste",
            "caption": caption or "人工貼上修正圖片",
            "description": notes or "",
            "manual_asset": True,
            "mime_type": mime_type,
            "sha256": full_hash,
            "bytes": len(image_bytes),
        }
        previous = self.current_question_review(candidate_key)
        existing_correction = normalized_correction(previous.get("correction"))
        correction = dict(existing_correction)
        placement = str(placement or "stem").strip()
        target_option = str(target_option or "").strip().upper()[:1]
        if placement == "option":
            if target_option not in {"A", "B", "C", "D", "E"}:
                raise ValueError("option placement requires target_option A-E.")
            asset_ref["asset_role"] = "option_image"
            asset_ref["placement"] = "option"
            asset_ref["target_option"] = target_option
            option_rows = list(existing_correction.get("options") or candidate.get("options") or [])
            next_options = []
            touched = False
            for option in option_rows:
                if not isinstance(option, dict):
                    continue
                row = dict(option)
                if str(row.get("key") or "").strip().upper()[:1] == target_option:
                    row["image"] = asset_ref
                    touched = True
                next_options.append(row)
            if not touched:
                next_options.append({"key": target_option, "text": "", "image": asset_ref})
            correction["options"] = next_options
        elif placement == "answer":
            asset_ref["asset_role"] = "manual_answer_image"
            asset_ref["placement"] = "answer"
            existing_refs = [] if replace_existing else list(existing_correction.get("answer_image_refs") or candidate.get("answer_image_refs") or [])
            existing_paths = {
                str(ref.get("path") or ref.get("path_relative") or "")
                for ref in existing_refs
                if isinstance(ref, dict)
            }
            if str(asset_ref["path"]) not in existing_paths:
                existing_refs.append(asset_ref)
            correction["answer_image_refs"] = existing_refs
        else:
            if placement == "table":
                asset_ref["asset_role"] = "table_manual_screenshot"
            elif placement == "group":
                asset_ref["asset_role"] = "group_shared_asset"
            else:
                asset_ref["asset_role"] = asset_role or "manual_question_image"
                placement = "stem"
            asset_ref["placement"] = placement
            existing_refs = [] if replace_existing else list(existing_correction.get("image_refs") or candidate.get("image_refs") or [])
            existing_paths = {
                str(ref.get("path") or ref.get("path_relative") or "")
                for ref in existing_refs
                if isinstance(ref, dict)
            }
            if str(asset_ref["path"]) not in existing_paths:
                existing_refs.append(asset_ref)
            correction["image_refs"] = existing_refs
        correction["visual_review"] = "visual_asset_ok"
        event = {
            "candidate_key": candidate_key,
            "action": "correct",
            "reviewer": reviewer or "local",
            "notes": notes or ("人工貼上修正圖片並取代既有圖片；圖片審核視為已確認，題目是否通過仍依審題狀態。" if replace_existing else f"人工貼上修正圖片至{placement}；圖片審核視為已確認，題目是否通過仍依審題狀態。"),
            "correction": correction,
            "manual_asset": asset_ref,
            "correction_action": "replace_manual_asset" if replace_existing else "add_manual_asset",
        }
        saved_event = self.append_review(event)
        return {"asset": asset_ref, "event": saved_event}

    def candidate_payload(
        self,
        item: dict[str, Any],
        *,
        issues_by_key: dict[str, list[dict[str, Any]]] | None = None,
        latest_reviews: dict[str, dict[str, Any]] | None = None,
        review_counts: dict[str, int] | None = None,
        latest_reset_reviews: dict[str, dict[str, Any]] | None = None,
        latest_answer_reviews: dict[str, dict[str, Any]] | None = None,
        answer_review_counts: dict[str, int] | None = None,
        latest_ai_reviews: dict[str, dict[str, Any]] | None = None,
        ai_review_counts: dict[str, int] | None = None,
        formal_question_map: dict[str, dict[str, Any]] | None = None,
        latest_group_reviews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        key = item["candidate_key"]
        issues_by_key = self.issues if issues_by_key is None else issues_by_key
        latest_reviews = self.latest_reviews if latest_reviews is None else latest_reviews
        review_counts = self.review_counts if review_counts is None else review_counts
        latest_reset_reviews = self.latest_reset_reviews if latest_reset_reviews is None else latest_reset_reviews
        latest_answer_reviews = self.latest_answer_reviews if latest_answer_reviews is None else latest_answer_reviews
        answer_review_counts = self.answer_review_counts if answer_review_counts is None else answer_review_counts
        latest_ai_reviews = self.latest_ai_reviews if latest_ai_reviews is None else latest_ai_reviews
        ai_review_counts = self.ai_review_counts if ai_review_counts is None else ai_review_counts
        formal_question_map = {} if formal_question_map is None else formal_question_map
        latest_group_reviews = self.latest_group_reviews if latest_group_reviews is None else latest_group_reviews
        copy = dict(item)
        metadata = copy.get("metadata") or {}
        issues = issues_by_key.get(key, [])
        question_issues = [issue for issue in issues if issue.get("issue_code") not in ANSWER_ISSUE_CODES]
        answer_issues = [issue for issue in issues if issue.get("issue_code") in ANSWER_ISSUE_CODES]
        copy["raw_quality_status"] = copy.get("quality_status")
        copy["question_quality_status"] = issue_quality_status(question_issues)
        copy["answer_gate_status"] = issue_quality_status(answer_issues)
        copy["issues"] = question_issues
        copy["question_issues"] = question_issues
        copy["answer_issues"] = answer_issues
        copy["question_issue_count"] = len(question_issues)
        copy["answer_issue_count"] = len(answer_issues)
        latest_review = latest_reviews.get(key)
        latest_reset_review = latest_reset_reviews.get(key)
        copy["repair_status"] = repair_event_info(latest_review, latest_reset_review, metadata)
        correction = normalized_correction(latest_review.get("correction") if latest_review else None)
        if correction:
            copy["parser_original"] = {
                "stem": item.get("stem"),
                "options": item.get("options"),
                "answer": item.get("answer"),
                "group_ref": item.get("group_ref"),
                "group_sequence_no": item.get("group_sequence_no"),
                "image_refs": item.get("image_refs"),
                "stem_image": item.get("stem_image"),
                "answer_image_refs": item.get("answer_image_refs"),
                "visual_review": item.get("visual_review"),
            }
            for field in ("stem", "answer", "group_ref", "group_sequence_no", "image_refs", "stem_image", "answer_image_refs", "visual_review"):
                if field in correction:
                    copy[field] = correction[field]
            if "options" in correction:
                copy["options"] = correction["options"]
        display_stem, table_suppressed = strip_structured_tables(str(copy.get("stem") or ""))
        if table_suppressed:
            copy["stem_with_tables"] = copy.get("stem")
            copy["stem"] = display_stem
            copy["table_markup_suppressed"] = True
        option_image_paths = {
            str((option.get("image") or {}).get("path") or "")
            for option in (copy.get("options") or [])
            if isinstance(option, dict) and isinstance(option.get("image"), dict)
        }
        copy["non_option_image_refs"] = [
            ref
            for ref in (copy.get("image_refs") or [])
            if isinstance(ref, dict) and str(ref.get("path") or "") not in option_image_paths
        ]
        copy["visual_profile"] = candidate_visual_profile(copy)
        copy["is_visual_question"] = bool(
            copy["visual_profile"]["has_visual_asset"] or copy["visual_profile"]["has_visual_dependency"]
        )
        group_review = latest_group_reviews.get(key)
        if group_review and group_review.get("action") == "confirm_group":
            if group_review.get("group_ref"):
                copy["group_ref"] = group_review.get("group_ref")
            if group_review.get("group_sequence_no") is not None:
                copy["group_sequence_no"] = group_review.get("group_sequence_no")
            if group_review.get("group_type"):
                copy["group_type"] = group_review.get("group_type")
        copy["group_review"] = group_review
        copy["review"] = {
            "status": "reviewed" if latest_review else "unreviewed",
            "action": latest_review.get("action") if latest_review else None,
            "notes": latest_review.get("notes") if latest_review else None,
            "updated_at": latest_review.get("created_at") if latest_review else None,
            "event_count": review_counts.get(key, 0),
            "has_correction": bool(correction),
            "correction": correction or None,
            "reset": latest_reset_review,
            "is_reset_unreviewed": bool(latest_reset_review and not latest_review),
        }
        latest_action = latest_review.get("action") if latest_review else None
        formal = dict(formal_question_map.get(key) or {"in_formal": False})
        physical_in_formal = bool(formal.get("in_formal"))
        latest_answer_review = latest_answer_reviews.get(key)
        latest_answer_action = latest_answer_review.get("action") if latest_answer_review else None
        copy["answer_review"] = {
            "status": "reviewed" if latest_answer_review else "unreviewed",
            "action": latest_answer_action,
            "notes": latest_answer_review.get("notes") if latest_answer_review else None,
            "updated_at": latest_answer_review.get("created_at") if latest_answer_review else None,
            "event_count": answer_review_counts.get(key, 0),
            "correction": latest_answer_review.get("corrected_answer") if latest_answer_review else None,
        }
        question_ready = latest_action in QUESTION_READY_ACTIONS
        answer_ready = latest_answer_action in ANSWER_READY_ACTIONS
        ready_for_formal = bool(question_ready and answer_ready)
        formal.update(
            {
                "physical_in_formal": physical_in_formal,
                "question_ready": question_ready,
                "answer_ready": answer_ready,
                "ready_for_formal": ready_for_formal,
                "pending_promotion": bool(ready_for_formal and not physical_in_formal),
                "review_drift": bool(physical_in_formal and not ready_for_formal),
                "in_formal": ready_for_formal,
            }
        )
        copy["formal"] = formal
        latest_ai_review = latest_ai_reviews.get(key)
        ai_audit = latest_ai_review.get("audit") if latest_ai_review else None
        ai_suggestion, ai_suggestion_changes = ai_suggested_correction(copy, ai_audit)
        copy["ai_review"] = {
            "status": "reviewed" if latest_ai_review else "unreviewed",
            "audit_status": effective_ai_audit_status(ai_audit, ai_suggestion),
            "raw_audit_status": ai_audit.get("status") if isinstance(ai_audit, dict) else None,
            "recommended_action": ai_audit.get("recommended_action") if isinstance(ai_audit, dict) else None,
            "summary": ai_audit.get("summary") if isinstance(ai_audit, dict) else None,
            "findings": ai_audit.get("findings") if isinstance(ai_audit, dict) else [],
            "labels": ai_audit.get("labels") if isinstance(ai_audit, dict) else [],
            "suggested_correction": ai_suggestion,
            "suggested_changes": ai_suggestion_changes,
            "provider": latest_ai_review.get("provider") if latest_ai_review else None,
            "model": latest_ai_review.get("model") if latest_ai_review else None,
            "updated_at": latest_ai_review.get("created_at") if latest_ai_review else None,
            "event_count": ai_review_counts.get(key, 0),
        }
        copy["source_files"] = {
            "official_pdf": metadata.get("question_pdf_relative") or metadata.get("question_pdf"),
            "mineru_layout_pdf": sibling_pdf(metadata.get("question_markdown_relative") or metadata.get("question_markdown") or "", "_layout"),
            "mineru_origin_pdf": sibling_pdf(metadata.get("question_markdown_relative") or metadata.get("question_markdown") or "", "_origin"),
            "question_markdown": metadata.get("question_markdown_relative") or metadata.get("question_markdown"),
        }
        copy["answer_source_files"] = {
            "official_pdf": metadata.get("answer_pdf_primary_relative") or metadata.get("answer_pdf_primary"),
            "mineru_layout_pdf": sibling_pdf(metadata.get("answer_markdown_relative") or metadata.get("answer_markdown") or "", "_layout"),
            "mineru_origin_pdf": sibling_pdf(metadata.get("answer_markdown_relative") or metadata.get("answer_markdown") or "", "_origin"),
            "answer_markdown": metadata.get("answer_markdown_relative") or metadata.get("answer_markdown"),
        }
        return copy

    def answer_sheet_key(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") or {}
        return "|".join(
            str(value or "")
            for value in [
                item.get("answer_source_registry_key"),
                metadata.get("answer_pdf_primary_relative") or metadata.get("answer_pdf_primary"),
                metadata.get("exam_code"),
                metadata.get("category_code"),
                metadata.get("subject_code"),
                metadata.get("year"),
                metadata.get("exam_ordinal"),
            ]
        )

    def answer_sheet_payload(
        self,
        items: list[dict[str, Any]],
        *,
        issues_by_key: dict[str, list[dict[str, Any]]] | None = None,
        latest_reviews: dict[str, dict[str, Any]] | None = None,
        review_counts: dict[str, int] | None = None,
        latest_reset_reviews: dict[str, dict[str, Any]] | None = None,
        latest_answer_reviews: dict[str, dict[str, Any]] | None = None,
        answer_review_counts: dict[str, int] | None = None,
        latest_ai_reviews: dict[str, dict[str, Any]] | None = None,
        ai_review_counts: dict[str, int] | None = None,
        latest_group_reviews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload_items = [
            self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            for item in sorted(items, key=lambda row: int_or_zero(row.get("question_number")))
        ]
        first = payload_items[0]
        metadata = first.get("metadata") or {}
        role = metadata.get("answer_role_primary") or ""
        rows = []
        reviewed_count = 0
        accepted_count = 0
        blocked_count = 0
        needs_review_count = 0
        corrected_count = 0
        answer_issue_count = 0
        answer_attention_count = 0
        concrete_rows: list[dict[str, Any]] = []
        for item in payload_items:
            review = item.get("answer_review") or {}
            hint = answer_review_hint(role, item.get("answer"), item.get("answer_payload"))
            action = review.get("action")
            if review.get("status") == "reviewed":
                reviewed_count += 1
            if action in {"accept", "unblock"}:
                accepted_count += 1
            elif action == "block":
                blocked_count += 1
            elif action == "needs_review":
                needs_review_count += 1
            if review.get("correction"):
                corrected_count += 1
            answer_issue_count += int(item.get("answer_issue_count") or 0)
            if hint.get("severity") == "warning":
                answer_attention_count += 1
            concrete_rows.append(
                {
                    "candidate_key": item.get("candidate_key"),
                    "question_number": item.get("question_number"),
                    "question_number_occurrence": item.get("question_number_occurrence"),
                    "stem": item.get("stem"),
                    "options": item.get("options") or [],
                    "answer": item.get("answer"),
                    "answer_image_refs": item.get("answer_image_refs") or [],
                    "answer_payload": item.get("answer_payload"),
                    "answer_review": review,
                    "answer_hint": hint,
                    "answer_issues": item.get("answer_issues") or [],
                    "question_review": item.get("review") or {},
                }
            )
        rows_by_number = {int_or_zero(row.get("question_number")): row for row in concrete_rows if int_or_zero(row.get("question_number")) > 0}
        max_question_number = max(rows_by_number) if rows_by_number else len(concrete_rows)
        for number in range(1, max_question_number + 1):
            if number in rows_by_number:
                rows.append(rows_by_number[number])
                continue
            rows.append(
                {
                    "candidate_key": "",
                    "question_number": str(number),
                    "question_number_occurrence": None,
                    "stem": "",
                    "answer": None,
                    "answer_payload": None,
                    "answer_review": {"status": "unreviewed", "action": None},
                    "answer_hint": {"flags": [], "severity": "", "message": "", "needs_manual_choice": False},
                    "answer_issues": [],
                    "question_review": {"status": "not_accepted", "action": "not_accepted"},
                    "is_placeholder": True,
                    "placeholder_reason": "題目尚未通過審核",
                }
            )
        if blocked_count:
            sheet_action = "block"
        elif needs_review_count:
            sheet_action = "needs_review"
        elif accepted_count == len(concrete_rows) and concrete_rows:
            sheet_action = "accept"
        elif reviewed_count:
            sheet_action = "reviewed"
        else:
            sheet_action = None
        return {
            "candidate_key": self.answer_sheet_key(items[0]),
            "sheet_key": self.answer_sheet_key(items[0]),
            "sheet_type": "answer_sheet",
            "question_count": len(rows),
            "reviewable_question_count": len(concrete_rows),
            "placeholder_count": len(rows) - len(concrete_rows),
            "reviewed_count": reviewed_count,
            "accepted_count": accepted_count,
            "blocked_count": blocked_count,
            "needs_review_count": needs_review_count,
            "corrected_count": corrected_count,
            "answer_issue_count": answer_issue_count,
            "answer_attention_count": answer_attention_count,
            "answer_gate_status": "blocked" if answer_issue_count else "needs_review" if answer_attention_count else "pass",
            "answer_review": {
                "status": "reviewed" if reviewed_count else "unreviewed",
                "action": sheet_action,
            },
            "answer_role_primary": role,
            "answer_role_label": "MOD" if role == "correction" else "ANS" if role == "answer" else role or "unknown",
            "metadata": metadata,
            "source_files": {
                "official_pdf": metadata.get("answer_pdf_primary_relative") or metadata.get("answer_pdf_primary"),
                "mineru_layout_pdf": sibling_pdf(metadata.get("answer_markdown_relative") or metadata.get("answer_markdown") or "", "_layout"),
                "mineru_origin_pdf": sibling_pdf(metadata.get("answer_markdown_relative") or metadata.get("answer_markdown") or "", "_origin"),
                "answer_markdown": metadata.get("answer_markdown_relative") or metadata.get("answer_markdown"),
            },
            "question_source_files": first.get("source_files") or {},
            "rows": rows,
        }

    def group_sheet_key(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") or {}
        group_ref = str(item.get("group_ref") or "").strip()
        inferred_group_ref = str(item.get("inferred_group_ref") or "").strip()
        group_review = item.get("group_review") or {}
        reviewed_sheet_key = str(group_review.get("group_sheet_key") or "").strip()
        if group_review.get("action") in {"confirm_group", "confirm_not_group"} and reviewed_sheet_key:
            return reviewed_sheet_key
        session_key = "|".join(
            str(value or "")
            for value in [
                metadata.get("normalized_category_name") or metadata.get("group_name"),
                metadata.get("normalized_subject_name"),
                metadata.get("year"),
                metadata.get("exam_ordinal"),
                item.get("source_registry_key"),
            ]
        )
        if inferred_group_ref:
            return f"inferred_continuation|{session_key}|{inferred_group_ref}"
        return f"group_ref|{session_key}|{group_ref}" if group_ref else f"unbound_suspect|{session_key}"

    def group_suspect_reasons(self, item: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if (item.get("group_review") or {}).get("action") == "confirm_not_group":
            return reasons
        group_ref = str(item.get("group_ref") or "").strip()
        if group_ref:
            reasons.append("已有 group_ref")
        inferred_group_ref = str(item.get("inferred_group_ref") or "").strip()
        inferred_group_kind = str(item.get("inferred_group_kind") or "").strip()
        if inferred_group_ref and inferred_group_kind != "explicit_count":
            reasons.append("承上題連續關聯")
        if inferred_group_kind == "explicit_count":
            reasons.append("明示範圍題組")
        text_parts = [
            str(item.get("stem") or ""),
            str(((item.get("metadata") or {}).get("raw_block")) or ""),
        ]
        combined = "\n".join(text_parts)
        if GROUP_CONTINUATION_RE.search(combined):
            reasons.append("題幹開頭承上題")
        if GROUP_PREFIX_RANGE_RE.search(combined) or GROUP_COUNT_RE.search(combined):
            reasons.append("題幹明示回答多題")
        return sorted(set(reasons))

    def group_session_key(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") or {}
        return "|".join(
            str(value or "")
            for value in [
                metadata.get("normalized_category_name") or metadata.get("group_name"),
                metadata.get("normalized_subject_name"),
                metadata.get("year"),
                metadata.get("exam_ordinal"),
                item.get("source_registry_key"),
            ]
        )

    def inferred_continuation_groups(self, payloads: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        by_session: dict[str, dict[int, dict[str, Any]]] = {}
        for payload in payloads:
            number = int_or_zero(payload.get("question_number"))
            if number <= 0:
                continue
            by_session.setdefault(self.group_session_key(payload), {})[number] = payload

        groups_by_key: dict[str, list[dict[str, Any]]] = {}
        for session_key, by_number in by_session.items():
            for number, payload in sorted(by_number.items()):
                if (payload.get("group_review") or {}).get("action") == "confirm_not_group":
                    continue
                stem = str(payload.get("stem") or "")
                count_match = GROUP_COUNT_RE.search(stem)
                prefix_match = GROUP_PREFIX_RANGE_RE.search(stem)
                start = number
                end = 0
                if prefix_match:
                    start = int(prefix_match.group(1))
                    end = int(prefix_match.group(2))
                elif count_match:
                    end = start + int(count_match.group(1)) - 1
                if not (start <= number <= end <= start + 20):
                    continue
                group_ref = f"q{start:03d}-q{end:03d}"
                group_key = f"{session_key}|{group_ref}"
                group_items = []
                for group_number in range(start, end + 1):
                    item = by_number.get(group_number)
                    if item and (item.get("group_review") or {}).get("action") == "confirm_not_group":
                        item = None
                    if item:
                        item = dict(item)
                        item["inferred_group_ref"] = group_ref
                        item["inferred_group_kind"] = "explicit_count"
                        group_items.append(item)
                if len(group_items) >= 2:
                    groups_by_key[group_key] = group_items
            continuation_numbers = {
                number
                for number, payload in by_number.items()
                if GROUP_CONTINUATION_RE.search(str(payload.get("stem") or ""))
                and (payload.get("group_review") or {}).get("action") != "confirm_not_group"
            }
            for number in sorted(continuation_numbers):
                start = number - 1
                while start in continuation_numbers:
                    start -= 1
                if start not in by_number:
                    continue
                end = number
                while end + 1 in continuation_numbers:
                    end += 1
                group_ref = f"q{start:03d}-q{end:03d}"
                group_key = f"{session_key}|{group_ref}"
                group_items = []
                for group_number in range(start, end + 1):
                    item = by_number.get(group_number)
                    if item and (item.get("group_review") or {}).get("action") == "confirm_not_group":
                        item = None
                    if item:
                        item = dict(item)
                        item["inferred_group_ref"] = group_ref
                        group_items.append(item)
                if len(group_items) >= 2:
                    groups_by_key[group_key] = group_items
        return list(groups_by_key.values())

    def group_sheet_payload(
        self,
        items: list[dict[str, Any]],
        *,
        issues_by_key: dict[str, list[dict[str, Any]]] | None = None,
        latest_reviews: dict[str, dict[str, Any]] | None = None,
        review_counts: dict[str, int] | None = None,
        latest_reset_reviews: dict[str, dict[str, Any]] | None = None,
        latest_answer_reviews: dict[str, dict[str, Any]] | None = None,
        answer_review_counts: dict[str, int] | None = None,
        latest_ai_reviews: dict[str, dict[str, Any]] | None = None,
        ai_review_counts: dict[str, int] | None = None,
        latest_group_reviews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        unique_items = list({str(item.get("candidate_key") or ""): item for item in items if item.get("candidate_key")}.values())
        payload_items = [
            self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            for item in sorted(unique_items, key=lambda row: (int_or_zero(row.get("question_number")), str(row.get("candidate_key") or "")))
        ]
        if not payload_items:
            raise ValueError("group sheet requires at least one candidate")
        first = payload_items[0]
        metadata = first.get("metadata") or {}
        group_ref = str(first.get("group_ref") or "").strip()
        inferred_group_ref = str(first.get("inferred_group_ref") or "").strip()
        inferred_group_kind = str(first.get("inferred_group_kind") or "").strip()
        rows = []
        reason_counts: dict[str, int] = {}
        accepted_count = 0
        blocked_count = 0
        needs_review_count = 0
        group_review_actions: list[str] = []
        for item in payload_items:
            reasons = self.group_suspect_reasons(item)
            for reason in reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            group_action = (item.get("group_review") or {}).get("action") or ""
            if group_action in {"confirm_group", "confirm_not_group"}:
                group_review_actions.append(str(group_action))
            action = (item.get("review") or {}).get("action")
            if action in {"accept", "unblock"}:
                accepted_count += 1
            elif action in {"block", "exclude"}:
                blocked_count += 1
            elif action == "needs_review":
                needs_review_count += 1
            rows.append(
                {
                    "candidate_key": item.get("candidate_key"),
                    "question_number": item.get("question_number"),
                    "question_number_occurrence": item.get("question_number_occurrence"),
                    "stem": item.get("stem"),
                    "group_ref": item.get("group_ref") or "",
                    "inferred_group_ref": item.get("inferred_group_ref") or "",
                    "inferred_group_kind": item.get("inferred_group_kind") or "",
                    "review": item.get("review") or {},
                    "group_review": item.get("group_review") or {},
                    "ai_review": item.get("ai_review") or {},
                    "visual_profile": item.get("visual_profile") or {},
                    "is_visual_question": bool(item.get("is_visual_question")),
                    "reasons": reasons,
                }
            )
        group_review_status = "unreviewed"
        if group_review_actions and all(action == "confirm_group" for action in group_review_actions):
            group_review_status = "confirmed_group"
        elif group_review_actions and all(action == "confirm_not_group" for action in group_review_actions):
            group_review_status = "confirmed_not_group"
        elif group_review_actions:
            group_review_status = "reviewed"
        return {
            "sheet_type": "group_sheet",
            "candidate_key": self.group_sheet_key(first),
            "group_sheet_key": self.group_sheet_key(first),
            "group_review_status": group_review_status,
            "group_ref": group_ref,
            "inferred_group_ref": inferred_group_ref,
            "inferred_group_kind": inferred_group_kind,
            "group_label": group_ref or (
                f"明示範圍 {inferred_group_ref}"
                if inferred_group_kind == "explicit_count"
                else f"承上題 {inferred_group_ref}"
                if inferred_group_ref
                else "未綁疑似題組"
            ),
            "metadata": metadata,
            "source_files": first.get("source_files") or {},
            "rows": rows,
            "question_count": len(rows),
            "accepted_count": accepted_count,
            "blocked_count": blocked_count,
            "needs_review_count": needs_review_count,
            "reason_counts": reason_counts,
            "gate_status": "linked" if group_ref else "inferred_continuation" if inferred_group_ref else "unbound_suspect",
        }

    def facets(self, params: dict[str, str] | None = None) -> dict[str, list[str]]:
        params = params or {}
        values: dict[str, set[str]] = {"categories": set(), "subjects": set(), "years": set(), "ordinals": set()}
        for item in self.candidates:
            metadata = item.get("metadata") or {}
            category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
            subject = metadata.get("normalized_subject_name") or ""
            year = str(metadata.get("year") or "")
            ordinal = str(metadata.get("exam_ordinal") or "")
            # The category selector is the top-level navigation rail. Keep it
            # global so selecting one category never hides the other categories
            # and traps the reviewer inside the current choice.
            if category:
                values["categories"].add(category)
            if self._facet_match(category, subject, year, ordinal, params, ignore="subject") and subject:
                values["subjects"].add(subject)
            if self._facet_match(category, subject, year, ordinal, params, ignore="year") and year:
                values["years"].add(year)
            if self._facet_match(category, subject, year, ordinal, params, ignore="ordinal") and ordinal:
                values["ordinals"].add(ordinal)
        return {
            key: sorted(value, key=lambda item: (int(item) if item.isdigit() else 9999, item))
            if key in {"years", "ordinals"}
            else sorted(value)
            for key, value in values.items()
        }

    def _facet_match(
        self,
        category: str,
        subject: str,
        year: str,
        ordinal: str,
        params: dict[str, str],
        ignore: str,
    ) -> bool:
        checks = {
            "category": (category, params.get("category") or ""),
            "subject": (subject, params.get("subject") or ""),
            "year": (year, params.get("year") or ""),
            "ordinal": (ordinal, params.get("ordinal") or ""),
        }
        for key, (value, expected) in checks.items():
            if key == ignore or not expected:
                continue
            if value != expected:
                return False
        return True

    def filtered_candidate_payloads(self, params: dict[str, str]) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self.filtered_candidate_payloads_sql(params)
        q = (params.get("q") or "").strip().lower()
        status = params.get("status") or ""
        review_status = params.get("reviewStatus") or ""
        ai_review_status = params.get("aiReviewStatus") or ""
        category_filter = params.get("category") or ""
        subject_filter = params.get("subject") or ""
        year_filter = params.get("year") or ""
        ordinal_filter = params.get("ordinal") or ""
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500

        payloads: list[dict[str, Any]] = []
        filtered_count = 0
        reviewed_count = 0
        for item in self.candidates:
            key = item["candidate_key"]
            latest_review = self.latest_reviews.get(key)
            latest_reset_review = self.latest_reset_reviews.get(key)
            review = {
                "status": "reviewed" if latest_review else "unreviewed",
                "action": latest_review.get("action") if latest_review else None,
                "notes": latest_review.get("notes") if latest_review else None,
                "reset": latest_reset_review,
                "is_reset_unreviewed": bool(latest_reset_review and not latest_review),
            }
            metadata = item.get("metadata") or {}
            category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
            subject = metadata.get("normalized_subject_name") or ""
            latest_ai_review = self.latest_ai_reviews.get(key)
            latest_ai_audit = latest_ai_review.get("audit") if latest_ai_review else None
            ai_suggestion, _ai_suggestion_changes = ai_suggested_correction(item, latest_ai_audit)
            ai_audit_status = effective_ai_audit_status(latest_ai_audit, ai_suggestion) or ""
            if review_status == "not_accept":
                review_match = (
                    (review["status"] == "reviewed" and review["action"] not in {"accept", "correct", "unblock", "exclude"})
                    or bool(latest_reset_review and not latest_review)
                )
            elif review_status == "correct":
                review_match = bool(latest_review and normalized_correction(latest_review.get("correction")))
            elif review_status == "reset_review":
                review_match = bool(latest_reset_review and not latest_review)
            elif review_status == "exclude":
                review_match = review["action"] == "exclude"
            else:
                review_match = (
                    review["action"] != "exclude"
                    and (not review_status or review["status"] == review_status or review["action"] == review_status)
                )
            item_issues = [issue for issue in self.issues.get(key, []) if issue.get("issue_code") not in ANSWER_ISSUE_CODES]
            question_quality_status = issue_quality_status(item_issues)
            if status and question_quality_status != status:
                continue
            if ai_review_status:
                if ai_review_status == "unreviewed":
                    ai_match = not latest_ai_review
                elif ai_review_status == "reviewed":
                    ai_match = bool(latest_ai_review)
                elif ai_review_status == "suggested_correction":
                    ai_match = bool(ai_suggestion)
                elif ai_review_status == "needs_review":
                    ai_match = ai_audit_status in {"needs_review", "block", "blocked"}
                else:
                    ai_match = ai_audit_status == ai_review_status
                if not ai_match:
                    continue
            if not review_match:
                continue
            if category_filter and category != category_filter:
                continue
            if subject_filter and subject != subject_filter:
                continue
            if year_filter and str(metadata.get("year") or "") != year_filter:
                continue
            if ordinal_filter and str(metadata.get("exam_ordinal") or "") != ordinal_filter:
                continue
            if q:
                reset_review = latest_reset_review or {}
                ai_audit = latest_ai_audit if isinstance(latest_ai_audit, dict) else {}
                haystack = " ".join(
                    str(value or "")
                    for value in [
                        item.get("candidate_key"),
                        item.get("question_number"),
                        item.get("stem"),
                        category,
                        subject,
                        review.get("action"),
                        review.get("notes"),
                        reset_review.get("action"),
                        reset_review.get("notes"),
                        reset_review.get("previous_action"),
                        reset_review.get("previous_notes"),
                        reset_review.get("reset_notes"),
                        latest_ai_review.get("provider") if latest_ai_review else "",
                        latest_ai_review.get("model") if latest_ai_review else "",
                        ai_audit.get("status"),
                        ai_audit.get("summary"),
                        ai_audit.get("reason"),
                        ai_audit.get("recommended_action"),
                        json.dumps(ai_audit.get("labels") or [], ensure_ascii=False),
                        json.dumps(ai_audit.get("suggested_changes") or [], ensure_ascii=False),
                    ]
                ).lower()
                if q not in haystack:
                    continue
            filtered_count += 1
            if review["status"] == "reviewed":
                reviewed_count += 1
            if len(payloads) < limit:
                payloads.append(self.candidate_payload(item))
        return {
            "candidates": payloads,
            "total_count": len(self.candidates),
            "filtered_count": filtered_count,
            "returned_count": len(payloads),
            "reviewed_count": reviewed_count,
            "facets": self.facets(params),
            "candidate_data": self.candidate_data_status(),
        }

    def filtered_candidate_payloads_sql(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500

        rows, filtered_count, reviewed_count, total_count = self._sql_filtered_candidate_rows_and_counts(params, limit)
        keys = [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]
        issues_by_key = self._sql_issue_map(keys)
        formal_question_map = self._sql_formal_question_map(keys)
        latest_reviews, review_counts, latest_reset_reviews = self._sql_question_review_maps(keys)
        latest_group_reviews = self._sql_group_review_maps(keys)
        latest_answer_reviews, answer_review_counts, _answer_reset_reviews = self._sql_latest_event_maps(
            "exam.answer_review_events",
            keys,
            reset_actions=RESET_REVIEW_ACTIONS,
        )
        latest_ai_reviews, ai_review_counts, _ai_reset_reviews = self._sql_latest_event_maps(
            "exam.question_ai_review_events",
            keys,
            reset_actions=AI_RESET_REVIEW_ACTIONS,
            ai=True,
        )

        payloads: list[dict[str, Any]] = []
        for item in rows:
            payloads.append(
                self.candidate_payload(
                    item,
                    issues_by_key=issues_by_key,
                    latest_reviews=latest_reviews,
                    review_counts=review_counts,
                    latest_reset_reviews=latest_reset_reviews,
                    latest_answer_reviews=latest_answer_reviews,
                    answer_review_counts=answer_review_counts,
                    latest_ai_reviews=latest_ai_reviews,
                    ai_review_counts=ai_review_counts,
                    formal_question_map=formal_question_map,
                    latest_group_reviews=latest_group_reviews,
                )
            )
        return {
            "candidates": payloads,
            "total_count": total_count,
            "filtered_count": filtered_count,
            "returned_count": len(payloads),
            "reviewed_count": reviewed_count,
            "facets": self.sql_facets(params),
            "candidate_data": self.candidate_data_status(),
        }

    def filtered_answer_payloads(self, params: dict[str, str]) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self.filtered_answer_payloads_sql(params)
        q = (params.get("q") or "").strip().lower()
        review_status = params.get("answerReviewStatus") or ""
        category_filter = params.get("category") or ""
        subject_filter = params.get("subject") or ""
        year_filter = params.get("year") or ""
        ordinal_filter = params.get("ordinal") or ""
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500

        sheet_items: dict[str, list[dict[str, Any]]] = {}
        reviewed_count = 0
        eligible_count = 0
        for item in self.candidates:
            key = item["candidate_key"]
            question_review = self.latest_reviews.get(key)
            if not question_review or question_review.get("action") not in {"accept", "unblock"}:
                continue
            latest_answer_review = self.latest_answer_reviews.get(key)
            answer_review = {
                "status": "reviewed" if latest_answer_review else "unreviewed",
                "action": latest_answer_review.get("action") if latest_answer_review else None,
                "notes": latest_answer_review.get("notes") if latest_answer_review else None,
            }
            metadata = item.get("metadata") or {}
            category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
            subject = metadata.get("normalized_subject_name") or ""
            if category_filter and category != category_filter:
                continue
            if subject_filter and subject != subject_filter:
                continue
            if year_filter and str(metadata.get("year") or "") != year_filter:
                continue
            if ordinal_filter and str(metadata.get("exam_ordinal") or "") != ordinal_filter:
                continue
            eligible_count += 1
            if answer_review["status"] == "reviewed":
                reviewed_count += 1
            sheet_items.setdefault(self.answer_sheet_key(item), []).append(item)
        candidate_sheets = [self.answer_sheet_payload(items) for items in sheet_items.values()]

        def sheet_matches_query(sheet: dict[str, Any]) -> bool:
            if not q:
                return True
            metadata = sheet.get("metadata") or {}
            row_values = []
            for row in sheet.get("rows") or []:
                answer_review = row.get("answer_review") or {}
                row_values.extend(
                    [
                        row.get("candidate_key"),
                        row.get("question_number"),
                        row.get("stem"),
                        row.get("answer"),
                        answer_review.get("action"),
                        answer_review.get("notes"),
                    ]
                )
            haystack = " ".join(
                str(value or "")
                for value in [
                    sheet.get("sheet_key"),
                    metadata.get("normalized_category_name") or metadata.get("group_name"),
                    metadata.get("normalized_subject_name"),
                    metadata.get("year"),
                    metadata.get("exam_ordinal"),
                    sheet.get("answer_role_label"),
                    *row_values,
                ]
            ).lower()
            return q in haystack

        def sheet_matches_review(sheet: dict[str, Any]) -> bool:
            if not review_status:
                return True
            question_count = int(sheet.get("question_count") or 0)
            reviewable_count = int(sheet.get("reviewable_question_count") or question_count)
            reviewed = int(sheet.get("reviewed_count") or 0)
            accepted = int(sheet.get("accepted_count") or 0)
            blocked = int(sheet.get("blocked_count") or 0)
            needs_review = int(sheet.get("needs_review_count") or 0)
            corrected = int(sheet.get("corrected_count") or 0)
            if review_status == "unreviewed":
                return reviewed < reviewable_count
            if review_status == "reviewed":
                return reviewed > 0
            if review_status == "not_accept":
                return reviewed > 0 and accepted < reviewable_count
            if review_status == "accept":
                return reviewable_count > 0 and accepted == reviewable_count
            if review_status == "block":
                return blocked > 0
            if review_status == "needs_review":
                return needs_review > 0
            if review_status == "correct":
                return corrected > 0
            if review_status == "comment":
                return any((row.get("answer_review") or {}).get("action") == "comment" for row in sheet.get("rows") or [])
            return True

        sheets = [sheet for sheet in candidate_sheets if sheet_matches_query(sheet) and sheet_matches_review(sheet)]
        filtered_count = sum(int(sheet.get("question_count") or 0) for sheet in sheets)
        sheets.sort(
            key=lambda sheet: (
                str((sheet.get("metadata") or {}).get("normalized_category_name") or ""),
                str((sheet.get("metadata") or {}).get("normalized_subject_name") or ""),
                int_or_zero((sheet.get("metadata") or {}).get("year")),
                int_or_zero((sheet.get("metadata") or {}).get("exam_ordinal")),
                sheet.get("answer_role_label") or "",
            )
        )
        return {
            "candidates": sheets[:limit],
            "eligible_count": eligible_count,
            "filtered_count": filtered_count,
            "sheet_count": len(candidate_sheets),
            "returned_count": len(sheets[:limit]),
            "reviewed_count": reviewed_count,
            "facets": self.facets(params),
            "candidate_data": self.candidate_data_status(),
        }

    def _sql_answer_filter_values(self, params: dict[str, str]) -> tuple[list[str], list[Any]]:
        clauses = ["COALESCE(c.review_status, '') <> 'excluded'"]
        values: list[Any] = []
        filters = {
            "category": ("COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '')", params.get("category") or ""),
            "subject": ("c.raw_candidate_json->'metadata'->>'normalized_subject_name'", params.get("subject") or ""),
            "year": ("c.raw_candidate_json->'metadata'->>'year'", params.get("year") or ""),
            "ordinal": ("c.raw_candidate_json->'metadata'->>'exam_ordinal'", params.get("ordinal") or ""),
        }
        for expr, value in filters.values():
            if not value:
                continue
            clauses.append(f"COALESCE({expr}, '') = %s")
            values.append(value)
        return clauses, values

    def _sql_answer_sheet_cte(self, params: dict[str, str]) -> tuple[str, list[Any]]:
        clauses, values = self._sql_answer_filter_values(params)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        q = (params.get("q") or "").strip().lower()
        answer_review_status = params.get("answerReviewStatus") or ""
        answer_filter_sql = ""
        if answer_review_status == "unreviewed":
            answer_filter_sql = "WHERE reviewed_count < question_count"
        elif answer_review_status == "reviewed":
            answer_filter_sql = "WHERE reviewed_count > 0"
        elif answer_review_status == "not_accept":
            answer_filter_sql = "WHERE reviewed_count > 0 AND accepted_count < question_count"
        elif answer_review_status == "accept":
            answer_filter_sql = "WHERE question_count > 0 AND accepted_count = question_count"
        elif answer_review_status == "block":
            answer_filter_sql = "WHERE blocked_count > 0"
        elif answer_review_status == "needs_review":
            answer_filter_sql = "WHERE needs_review_count > 0"
        elif answer_review_status == "correct":
            answer_filter_sql = "WHERE corrected_count > 0"
        elif answer_review_status == "comment":
            answer_filter_sql = "WHERE comment_count > 0"
        cte = f"""
WITH latest_question AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        corrected_candidate_json,
        event_json,
        notes,
        created_at,
        id
    FROM exam.question_review_events
    WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
    ORDER BY candidate_key, id DESC
),
latest_answer AS (
    SELECT DISTINCT ON (candidate_key)
        candidate_key,
        action,
        corrected_answer_json,
        event_json,
        notes,
        created_at,
        id
    FROM exam.answer_review_events
    ORDER BY candidate_key, id DESC
),
eligible AS (
    SELECT
        c.candidate_key,
        c.question_number,
        c.raw_candidate_json,
        array_to_string(ARRAY[
            COALESCE(c.raw_candidate_json->>'answer_source_registry_key', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'answer_pdf_primary_relative', c.raw_candidate_json->'metadata'->>'answer_pdf_primary', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'exam_code', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'category_code', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'subject_code', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'year', ''),
            COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '')
        ], '|') AS sheet_key,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', '') AS category,
        COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', '') AS subject,
        COALESCE(c.raw_candidate_json->'metadata'->>'answer_role_primary', '') AS answer_role,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'year', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'year')::integer ELSE 0 END AS year_sort,
        CASE WHEN COALESCE(c.raw_candidate_json->'metadata'->>'exam_ordinal', '') ~ '^[0-9]+$'
            THEN (c.raw_candidate_json->'metadata'->>'exam_ordinal')::integer ELSE 0 END AS ordinal_sort,
        CASE WHEN c.question_number ~ '^[0-9]+$' THEN c.question_number::integer ELSE 0 END AS question_sort,
        la.action AS answer_action,
        la.corrected_answer_json,
        la.event_json AS answer_event_json,
        (
            %s = ''
            OR lower(concat_ws(
                ' ',
                c.candidate_key,
                c.question_number,
                c.stem_text,
                c.raw_candidate_json->>'stem',
                c.raw_candidate_json->>'answer',
                COALESCE(c.raw_candidate_json->'metadata'->>'normalized_category_name', c.raw_candidate_json->'metadata'->>'group_name', ''),
                COALESCE(c.raw_candidate_json->'metadata'->>'normalized_subject_name', ''),
                c.raw_candidate_json->'metadata'->>'year',
                c.raw_candidate_json->'metadata'->>'exam_ordinal',
                la.action,
                la.notes,
                la.event_json::text
            )) LIKE %s
        ) AS query_match
    FROM exam.question_candidates c
    JOIN latest_question lq ON lq.candidate_key = c.candidate_key
    LEFT JOIN latest_answer la ON la.candidate_key = c.candidate_key
    {where}
      AND lq.action IN ('accept', 'unblock')
),
sheet_stats AS (
    SELECT
        sheet_key,
        min(category) AS category,
        min(subject) AS subject,
        min(year_sort) AS year_sort,
        min(ordinal_sort) AS ordinal_sort,
        min(answer_role) AS answer_role,
        count(*) AS question_count,
        count(*) FILTER (WHERE answer_action IS NOT NULL AND answer_action NOT IN ('unreviewed', 'reset_review')) AS reviewed_count,
        count(*) FILTER (WHERE answer_action IN ('accept', 'unblock')) AS accepted_count,
        count(*) FILTER (WHERE answer_action = 'block') AS blocked_count,
        count(*) FILTER (WHERE answer_action = 'needs_review') AS needs_review_count,
        count(*) FILTER (
            WHERE corrected_answer_json IS NOT NULL
               OR COALESCE(answer_event_json, '{{}}'::jsonb) ? 'correction'
        ) AS corrected_count,
        count(*) FILTER (WHERE answer_action = 'comment') AS comment_count,
        bool_or(query_match) AS query_match
    FROM eligible
    GROUP BY sheet_key
),
query_sheets AS (
    SELECT *
    FROM sheet_stats
    WHERE query_match
),
filtered_sheets AS (
    SELECT *
    FROM query_sheets
    {answer_filter_sql}
)
"""
        return cte, [q, f"%{q}%", *values]

    def _sql_answer_sheet_rows_and_counts(
        self,
        params: dict[str, str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, int, int, int]:
        cte, values = self._sql_answer_sheet_cte(params)
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    {cte}
                    , selected_sheets AS (
                        SELECT
                            sheet_key,
                            (SELECT COALESCE(sum(question_count), 0)::integer FROM query_sheets) AS eligible_count,
                            (SELECT COALESCE(sum(reviewed_count), 0)::integer FROM query_sheets) AS reviewed_count,
                            (SELECT count(*)::integer FROM query_sheets) AS sheet_count,
                            (SELECT COALESCE(sum(question_count), 0)::integer FROM filtered_sheets) AS filtered_count
                        FROM filtered_sheets
                        ORDER BY category, subject, year_sort DESC, ordinal_sort DESC, answer_role, sheet_key
                        LIMIT %s
                    )
                    SELECT
                        e.raw_candidate_json,
                        s.eligible_count,
                        s.reviewed_count,
                        s.sheet_count,
                        s.filtered_count
                    FROM eligible e
                    JOIN selected_sheets s ON s.sheet_key = e.sheet_key
                    ORDER BY e.category, e.subject, e.year_sort DESC, e.ordinal_sort DESC, e.answer_role, e.sheet_key, e.question_sort, e.candidate_key
                    """,
                    [*values, limit],
                )
                rows: list[dict[str, Any]] = []
                eligible_count = 0
                reviewed_count = 0
                sheet_count = 0
                filtered_count = 0
                for raw_candidate, row_eligible_count, row_reviewed_count, row_sheet_count, row_filtered_count in cur.fetchall():
                    eligible_count = int(row_eligible_count or 0)
                    reviewed_count = int(row_reviewed_count or 0)
                    sheet_count = int(row_sheet_count or 0)
                    filtered_count = int(row_filtered_count or 0)
                    if isinstance(raw_candidate, dict):
                        rows.append(raw_candidate)
                    elif isinstance(raw_candidate, str):
                        rows.append(json.loads(raw_candidate))
                if not rows:
                    cur.execute(
                        f"""
                        {cte}
                        SELECT
                            COALESCE(sum(question_count), 0)::integer AS eligible_count,
                            COALESCE(sum(reviewed_count), 0)::integer AS reviewed_count,
                            count(*)::integer AS sheet_count,
                            (SELECT COALESCE(sum(question_count), 0)::integer FROM filtered_sheets) AS filtered_count
                        FROM query_sheets
                        """,
                        values,
                    )
                    count_row = cur.fetchone()
                    if count_row:
                        eligible_count, reviewed_count, sheet_count, filtered_count = [int(value or 0) for value in count_row]
        return rows, eligible_count, reviewed_count, sheet_count, filtered_count

    def filtered_answer_payloads_sql(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500

        rows, eligible_count, reviewed_count, sheet_count, filtered_count = self._sql_answer_sheet_rows_and_counts(params, limit)
        keys = [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]
        issues_by_key = self._sql_issue_map(keys)
        latest_reviews, review_counts, latest_reset_reviews = self._sql_question_review_maps(keys)
        latest_group_reviews = self._sql_group_review_maps(keys)
        latest_answer_reviews, answer_review_counts, _answer_reset_reviews = self._sql_latest_event_maps(
            "exam.answer_review_events",
            keys,
            reset_actions=RESET_REVIEW_ACTIONS,
        )
        latest_ai_reviews, ai_review_counts, _ai_reset_reviews = self._sql_latest_event_maps(
            "exam.question_ai_review_events",
            keys,
            reset_actions=AI_RESET_REVIEW_ACTIONS,
            ai=True,
        )

        sheet_items: dict[str, list[dict[str, Any]]] = {}
        for item in rows:
            key = item["candidate_key"]
            question_review = latest_reviews.get(key)
            if not question_review or question_review.get("action") not in {"accept", "unblock"}:
                continue
            sheet_items.setdefault(self.answer_sheet_key(item), []).append(item)
        sheets = [
            self.answer_sheet_payload(
                items,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            for items in sheet_items.values()
        ]
        sheets.sort(
            key=lambda sheet: (
                str((sheet.get("metadata") or {}).get("normalized_category_name") or ""),
                str((sheet.get("metadata") or {}).get("normalized_subject_name") or ""),
                int_or_zero((sheet.get("metadata") or {}).get("year")),
                int_or_zero((sheet.get("metadata") or {}).get("exam_ordinal")),
                sheet.get("answer_role_label") or "",
            )
        )
        return {
            "candidates": sheets[:limit],
            "eligible_count": eligible_count,
            "filtered_count": filtered_count,
            "sheet_count": sheet_count,
            "returned_count": len(sheets[:limit]),
            "reviewed_count": reviewed_count,
            "facets": self.sql_facets(params),
            "candidate_data": self.candidate_data_status(),
        }

    def filtered_group_payloads(self, params: dict[str, str]) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self.filtered_group_payloads_sql(params)
        return self.filtered_group_payloads_from_rows(params, self.candidates, self.facets(params))

    def filtered_group_payloads_sql(self, params: dict[str, str]) -> dict[str, Any]:
        rows = self._sql_group_candidate_rows(params)
        keys = [str(item.get("candidate_key")) for item in rows if item.get("candidate_key")]
        issues_by_key = self._sql_issue_map(keys)
        latest_reviews, review_counts, latest_reset_reviews = self._sql_question_review_maps(keys)
        latest_group_reviews = self._sql_group_review_maps(keys)
        latest_answer_reviews, answer_review_counts, _answer_reset_reviews = self._sql_latest_event_maps(
            "exam.answer_review_events",
            keys,
            reset_actions=RESET_REVIEW_ACTIONS,
        )
        latest_ai_reviews, ai_review_counts, _ai_reset_reviews = self._sql_latest_event_maps(
            "exam.question_ai_review_events",
            keys,
            reset_actions=AI_RESET_REVIEW_ACTIONS,
            ai=True,
        )
        return self.filtered_group_payloads_from_rows(
            params,
            rows,
            self.sql_facets(params),
            issues_by_key=issues_by_key,
            latest_reviews=latest_reviews,
            review_counts=review_counts,
            latest_reset_reviews=latest_reset_reviews,
            latest_answer_reviews=latest_answer_reviews,
            answer_review_counts=answer_review_counts,
            latest_ai_reviews=latest_ai_reviews,
            ai_review_counts=ai_review_counts,
            latest_group_reviews=latest_group_reviews,
        )

    def filtered_group_payloads_from_rows(
        self,
        params: dict[str, str],
        rows: list[dict[str, Any]],
        facets: dict[str, list[str]],
        *,
        issues_by_key: dict[str, list[dict[str, Any]]] | None = None,
        latest_reviews: dict[str, dict[str, Any]] | None = None,
        review_counts: dict[str, int] | None = None,
        latest_reset_reviews: dict[str, dict[str, Any]] | None = None,
        latest_answer_reviews: dict[str, dict[str, Any]] | None = None,
        answer_review_counts: dict[str, int] | None = None,
        latest_ai_reviews: dict[str, dict[str, Any]] | None = None,
        ai_review_counts: dict[str, int] | None = None,
        latest_group_reviews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        q = (params.get("q") or "").strip().lower()
        try:
            limit = max(1, min(int(params.get("limit") or "500"), 1000))
        except ValueError:
            limit = 500
        payloads_by_key: dict[str, list[dict[str, Any]]] = {}
        inferred_candidate_keys: set[str] = set()
        suspect_count = 0
        payloads: list[dict[str, Any]] = []
        for item in rows:
            payload = self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            payloads.append(payload)
        for group_items in self.inferred_continuation_groups(payloads):
            suspect_count += len(group_items)
            inferred_candidate_keys.update(
                str(item.get("candidate_key") or "")
                for item in group_items
                if item.get("candidate_key")
            )
            payloads_by_key[self.group_sheet_key(group_items[0])] = group_items
        for payload in payloads:
            if str(payload.get("candidate_key") or "") in inferred_candidate_keys and not str(payload.get("group_ref") or "").strip():
                continue
            reasons = self.group_suspect_reasons(payload)
            group_review_action = (payload.get("group_review") or {}).get("action")
            if group_review_action in GROUP_REVIEW_ACTIONS:
                payloads_by_key.setdefault(self.group_sheet_key(payload), []).append(payload)
                continue
            if not reasons:
                continue
            if GROUP_CONTINUATION_RE.search(str(payload.get("stem") or "")) and not str(payload.get("group_ref") or "").strip():
                continue
            if str(payload.get("inferred_group_ref") or "").strip() and not str(payload.get("group_ref") or "").strip():
                continue
            payloads_by_key.setdefault(self.group_sheet_key(payload), []).append(payload)
        sheets = [
            self.group_sheet_payload(
                items,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
                latest_group_reviews=latest_group_reviews,
            )
            for items in payloads_by_key.values()
        ]
        group_review_status = params.get("groupReviewStatus") or ""
        if group_review_status:
            if group_review_status == "reviewed":
                sheets = [sheet for sheet in sheets if sheet.get("group_review_status") != "unreviewed"]
            else:
                sheets = [sheet for sheet in sheets if sheet.get("group_review_status") == group_review_status]
        if q:
            sheets = [
                sheet for sheet in sheets
                if q in " ".join(
                    str(value or "")
                    for value in [
                        sheet.get("group_sheet_key"),
                        sheet.get("group_ref"),
                        sheet.get("group_label"),
                        (sheet.get("metadata") or {}).get("normalized_category_name"),
                        (sheet.get("metadata") or {}).get("normalized_subject_name"),
                        (sheet.get("metadata") or {}).get("year"),
                        (sheet.get("metadata") or {}).get("exam_ordinal"),
                        json.dumps(sheet.get("reason_counts") or {}, ensure_ascii=False),
                        *[
                            " ".join(
                                str(row.get(field) or "")
                                for field in ("candidate_key", "question_number", "stem", "group_ref")
                            )
                            for row in sheet.get("rows") or []
                        ],
                    ]
                ).lower()
            ]
        sheets.sort(
            key=lambda sheet: (
                str((sheet.get("metadata") or {}).get("normalized_category_name") or ""),
                str((sheet.get("metadata") or {}).get("normalized_subject_name") or ""),
                -int_or_zero((sheet.get("metadata") or {}).get("year")),
                -int_or_zero((sheet.get("metadata") or {}).get("exam_ordinal")),
                0 if sheet.get("group_ref") else 1,
                str(sheet.get("group_label") or ""),
            )
        )
        return {
            "candidates": sheets[:limit],
            "total_count": len(rows),
            "filtered_count": len(sheets),
            "returned_count": len(sheets[:limit]),
            "group_count": len(sheets),
            "suspect_question_count": suspect_count,
            "facets": facets,
            "candidate_data": self.candidate_data_status(),
        }

    def load_preferences(self, reviewer: str) -> dict[str, Any]:
        preferences = self._load_file_preferences().get(reviewer, {})
        db_preferences = self._load_db_preferences(reviewer)
        if db_preferences:
            preferences.update(db_preferences)
        return preferences

    def save_preferences(self, reviewer: str, preferences: dict[str, Any]) -> None:
        preferences = dict(preferences)
        file_preferences = self._load_file_preferences()
        file_preferences[reviewer] = preferences
        with self.preference_path.open("w", encoding="utf-8") as f:
            json.dump(file_preferences, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        self._save_db_preferences(reviewer, preferences)

    def _load_file_preferences(self) -> dict[str, dict[str, Any]]:
        if not self.preference_path.exists():
            return {}
        try:
            with self.preference_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
        return {}

    def _load_db_preferences(self, reviewer: str) -> dict[str, Any]:
        if psycopg is None or not self.database_url:
            return {}
        try:
            connection_factory = self._sql_connect if self.sql_review_enabled else lambda: psycopg.connect(self.database_url, connect_timeout=2)
            with connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT preferences_json FROM exam.review_ui_preferences WHERE reviewer = %s",
                        (reviewer,),
                    )
                    row = cur.fetchone()
                    if row and isinstance(row[0], dict):
                        return row[0]
        except Exception:
            return {}
        return {}

    def _save_db_preferences(self, reviewer: str, preferences: dict[str, Any]) -> None:
        if psycopg is None or not self.database_url:
            return
        try:
            connection_factory = self._sql_connect if self.sql_review_enabled else lambda: psycopg.connect(self.database_url, connect_timeout=2)
            with connection_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO exam.review_ui_preferences (reviewer, preferences_json, updated_at)
                        VALUES (%s, %s::jsonb, now())
                        ON CONFLICT (reviewer) DO UPDATE
                        SET preferences_json = EXCLUDED.preferences_json,
                            updated_at = now()
                        """,
                        (reviewer, json.dumps(preferences, ensure_ascii=False)),
                    )
                conn.commit()
        except Exception:
            return

    def _insert_sql_question_review_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.question_review_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        action = str(event.get("action") or "")
        is_non_question_review_action = action in NON_QUESTION_REVIEW_ACTIONS
        status = {
            "accept": "accepted",
            "correct": "corrected",
            "needs_review": "needs_review",
            "block": "blocked",
            "reviewed": "accepted",
            "unblock": "accepted",
            "comment": "needs_review",
            "exclude": "excluded",
            "unreviewed": "unreviewed",
            "reset_review": "unreviewed",
        }.get(action, "unreviewed")
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_review_events (
                        candidate_id,
                        candidate_key,
                        reviewer,
                        action,
                        corrected_candidate_json,
                        event_json,
                        notes,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("reviewer"),
                        event.get("action"),
                        Jsonb(event.get("correction")) if event.get("correction") is not None else None,
                        Jsonb(event),
                        event.get("notes") or "",
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                event_id = int(row[0])
                if not is_non_question_review_action:
                    cur.execute(
                        """
                        UPDATE exam.question_candidates
                        SET review_status = %s,
                            updated_at = now()
                        WHERE candidate_key = %s
                        """,
                        (status, event.get("candidate_key")),
                    )
            conn.commit()
        self._sql_facets_cache.clear()
        return {"ok": True, "sql_primary": True, "table": "exam.question_review_events", "event_id": event_id}

    def _insert_sql_answer_review_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.answer_review_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.answer_review_events (
                        candidate_id,
                        candidate_key,
                        answer_source_registry_key,
                        reviewer,
                        action,
                        reviewed_answer_json,
                        corrected_answer_json,
                        event_json,
                        notes,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("answer_source_registry_key") or None,
                        event.get("reviewer"),
                        event.get("action"),
                        Jsonb(event.get("reviewed_answer")) if event.get("reviewed_answer") is not None else None,
                        Jsonb(event.get("corrected_answer")) if event.get("corrected_answer") is not None else None,
                        Jsonb(event),
                        event.get("notes") or "",
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                event_id = int(row[0])
            conn.commit()
        self._sql_facets_cache.clear()
        return {"ok": True, "sql_primary": True, "table": "exam.answer_review_events", "event_id": event_id}

    def _insert_sql_answer_review_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []
        if not self.sql_review_enabled:
            return [{"ok": True, "sql_primary": False, "table": "exam.answer_review_events"} for _event in events]
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        storage_rows: list[dict[str, Any]] = []
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                for event in events:
                    cur.execute(
                        """
                        INSERT INTO exam.answer_review_events (
                            candidate_id,
                            candidate_key,
                            answer_source_registry_key,
                            reviewer,
                            action,
                            reviewed_answer_json,
                            corrected_answer_json,
                            event_json,
                            notes,
                            created_at
                        )
                        SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                        FROM exam.question_candidates
                        WHERE candidate_key = %s
                        RETURNING id
                        """,
                        (
                            event.get("candidate_key"),
                            event.get("answer_source_registry_key") or None,
                            event.get("reviewer"),
                            event.get("action"),
                            Jsonb(event.get("reviewed_answer")) if event.get("reviewed_answer") is not None else None,
                            Jsonb(event.get("corrected_answer")) if event.get("corrected_answer") is not None else None,
                            Jsonb(event),
                            event.get("notes") or "",
                            event.get("created_at"),
                            event.get("candidate_key"),
                        ),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                    storage_rows.append({"ok": True, "sql_primary": True, "table": "exam.answer_review_events", "event_id": int(row[0])})
            conn.commit()
        self._sql_facets_cache.clear()
        return storage_rows

    def _insert_sql_ai_review_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.sql_review_enabled:
            return {"ok": True, "sql_primary": False, "table": "exam.question_ai_review_events"}
        if Jsonb is None:
            raise SqlWriteError("SQL JSONB adapter is not available.")
        audit = event.get("audit") if isinstance(event.get("audit"), dict) else {"status": "pass"}
        audit_status = str(audit.get("status") or "pass")
        if audit_status in {"blocked", "block"}:
            audit_status = "block"
        elif audit_status != "needs_review":
            audit_status = "pass"
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exam.question_ai_review_events (
                        candidate_id,
                        candidate_key,
                        action,
                        reviewer,
                        provider,
                        model_name,
                        prompt_version,
                        input_hash,
                        audit_status,
                        recommended_action,
                        audit_json,
                        event_json,
                        notes,
                        created_at
                    )
                    SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())
                    FROM exam.question_candidates
                    WHERE candidate_key = %s
                    RETURNING id
                    """,
                    (
                        event.get("candidate_key"),
                        event.get("action") or "ai_audit",
                        event.get("reviewer"),
                        event.get("provider") or audit.get("provider") or "local",
                        event.get("model") or audit.get("model"),
                        event.get("prompt_version"),
                        event.get("input_hash"),
                        audit_status,
                        audit.get("recommended_action"),
                        Jsonb(audit),
                        Jsonb(event),
                        event.get("notes") or "",
                        event.get("created_at"),
                        event.get("candidate_key"),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise SqlWriteError(f"candidate_key not found in SQL: {event.get('candidate_key')}")
                event_id = int(row[0])
            conn.commit()
        self._sql_facets_cache.clear()
        return {"ok": True, "sql_primary": True, "table": "exam.question_ai_review_events", "event_id": event_id}

    def append_review(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        key = event.get("candidate_key")
        if event.get("action") == "correct":
            previous = self.current_question_review(str(key or ""))
            previous_action = previous.get("action") if previous else None
            event.setdefault("correction_action", "save")
            event["action"] = previous_action if previous_action in {"accept", "needs_review", "block", "exclude", "unblock", "comment", "reviewed"} else "reviewed"
        event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        sql_storage = self._insert_sql_question_review_event(event)
        jsonl_storage = self._legacy_jsonl_storage(self.review_log, event)
        self._review_log_signature = file_signature(self.review_log)
        storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
        if key:
            self.review_counts[key] = self.review_counts.get(key, 0) + 1
            if event.get("action") in GROUP_REVIEW_ACTIONS:
                self.latest_group_reviews[key] = event
            elif event.get("action") in RESET_REVIEW_ACTIONS:
                self.latest_reviews.pop(key, None)
                self.latest_reset_reviews[key] = event
            else:
                self.latest_reviews[key] = event
                self.latest_reset_reviews.pop(key, None)
        return {**event, "storage": storage}

    def append_answer_review(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        key = event.get("candidate_key")
        if event.get("action") == "correct":
            previous = self.latest_answer_reviews.get(key or "")
            previous_action = previous.get("action") if previous else None
            event["correction_action"] = "save"
            event["action"] = previous_action if previous_action in {"accept", "needs_review", "block", "unblock", "comment", "reviewed"} else "reviewed"
        event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        sql_storage = self._insert_sql_answer_review_event(event)
        jsonl_storage = self._legacy_jsonl_storage(self.answer_review_log, event)
        self._answer_review_log_signature = file_signature(self.answer_review_log)
        storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
        if key:
            self.answer_review_counts[key] = self.answer_review_counts.get(key, 0) + 1
            if event.get("action") in RESET_REVIEW_ACTIONS:
                self.latest_answer_reviews.pop(key, None)
                self.latest_answer_reset_reviews[key] = event
            else:
                self.latest_answer_reviews[key] = event
                self.latest_answer_reset_reviews.pop(key, None)
        return {**event, "storage": storage}

    def append_answer_reviews_batch(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_events: list[dict[str, Any]] = []
        latest_for_correct: dict[str, dict[str, Any]] = {}
        if any(event.get("action") == "correct" for event in events):
            keys = [str(event.get("candidate_key") or "") for event in events if event.get("candidate_key")]
            if self.sql_review_enabled:
                latest_for_correct, _counts, _reset = self._sql_latest_event_maps(
                    "exam.answer_review_events",
                    keys,
                    reset_actions=RESET_REVIEW_ACTIONS,
                )
            else:
                latest_for_correct = self.latest_answer_reviews
        created_at = datetime.now().isoformat(timespec="seconds")
        for raw_event in events:
            event = dict(raw_event)
            key = str(event.get("candidate_key") or "")
            if not key:
                continue
            if event.get("action") == "correct":
                previous = latest_for_correct.get(key) or {}
                previous_action = previous.get("action") if previous else None
                event["correction_action"] = "save"
                event["action"] = previous_action if previous_action in {"accept", "needs_review", "block", "unblock", "comment", "reviewed"} else "reviewed"
            event.setdefault("created_at", created_at)
            normalized_events.append(event)
        if not normalized_events:
            return []
        sql_storages = self._insert_sql_answer_review_events(normalized_events)
        if self.legacy_jsonl_backup_enabled or not self.sql_review_enabled:
            try:
                with self.answer_review_log.open("a", encoding="utf-8") as f:
                    for event in normalized_events:
                        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                jsonl_storage = {"ok": True, "enabled": True, "path": str(self.answer_review_log)}
            except Exception as exc:
                if self.sql_review_enabled:
                    jsonl_storage = {"ok": False, "enabled": True, "path": str(self.answer_review_log), "error": str(exc)}
                else:
                    raise
        else:
            jsonl_storage = {"ok": True, "enabled": False, "path": str(self.answer_review_log)}
        self._answer_review_log_signature = file_signature(self.answer_review_log)
        saved: list[dict[str, Any]] = []
        for event, sql_storage in zip(normalized_events, sql_storages):
            key = str(event.get("candidate_key") or "")
            storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
            if key:
                self.answer_review_counts[key] = self.answer_review_counts.get(key, 0) + 1
                if event.get("action") in RESET_REVIEW_ACTIONS:
                    self.latest_answer_reviews.pop(key, None)
                    self.latest_answer_reset_reviews[key] = event
                else:
                    self.latest_answer_reviews[key] = event
                    self.latest_answer_reset_reviews.pop(key, None)
            saved.append({**event, "storage": storage})
        return saved

    def append_ai_review(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        key = event.get("candidate_key")
        event.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        sql_storage = self._insert_sql_ai_review_event(event)
        jsonl_storage = self._legacy_jsonl_storage(self.ai_review_log, event)
        self._ai_review_log_signature = file_signature(self.ai_review_log)
        storage = {**sql_storage, "legacy_jsonl_backup": jsonl_storage}
        if key:
            self.ai_review_counts[key] = self.ai_review_counts.get(key, 0) + 1
            if event.get("action") in AI_RESET_REVIEW_ACTIONS:
                self.latest_ai_reviews.pop(key, None)
            else:
                self.latest_ai_reviews[key] = event
        return {**event, "storage": storage}

    def reset_ai_review(self, candidate_key: str, reviewer: str = "local", notes: str = "") -> dict[str, Any]:
        if self.sql_review_enabled:
            exists = self.sql_candidate_exists(candidate_key)
        else:
            exists = candidate_key in self.candidate_by_key
        if not exists:
            raise KeyError(candidate_key)
        event = {
            "candidate_key": candidate_key,
            "reviewer": reviewer,
            "action": "reset_ai_review",
            "notes": notes or "撤回 AI 格式稽核；保留歷史事件但目前視為未稽核。",
        }
        return self.append_ai_review(event)

    def run_question_ai_audit(self, candidate_key: str, reviewer: str = "local", notes: str = "") -> dict[str, Any]:
        item = self._candidate_by_key_sql(candidate_key) if self.sql_review_enabled else self.candidate_by_key.get(candidate_key)
        if not item:
            raise KeyError(candidate_key)
        if self.sql_review_enabled:
            issues_by_key = self._sql_issue_map([candidate_key])
            latest_reviews, review_counts, latest_reset_reviews = self._sql_question_review_maps([candidate_key])
            latest_answer_reviews, answer_review_counts, _answer_reset_reviews = self._sql_latest_event_maps(
                "exam.answer_review_events",
                [candidate_key],
                reset_actions=RESET_REVIEW_ACTIONS,
            )
            latest_ai_reviews, ai_review_counts, _ai_reset_reviews = self._sql_latest_event_maps(
                "exam.question_ai_review_events",
                [candidate_key],
                reset_actions=AI_RESET_REVIEW_ACTIONS,
                ai=True,
            )
            candidate = self.candidate_payload(
                item,
                issues_by_key=issues_by_key,
                latest_reviews=latest_reviews,
                review_counts=review_counts,
                latest_reset_reviews=latest_reset_reviews,
                latest_answer_reviews=latest_answer_reviews,
                answer_review_counts=answer_review_counts,
                latest_ai_reviews=latest_ai_reviews,
                ai_review_counts=ai_review_counts,
            )
        else:
            candidate = self.candidate_payload(item)
        audit = openai_question_ai_audit(candidate)
        audit_input = compact_candidate_for_ai(candidate)
        event = {
            "candidate_key": candidate_key,
            "reviewer": reviewer,
            "action": "ai_audit",
            "prompt_version": AI_REVIEW_PROMPT_VERSION,
            "provider": audit.get("provider"),
            "model": audit.get("model"),
            "input_hash": hashlib.sha256(json.dumps(audit_input, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "notes": notes,
            "audit": audit,
        }
        return self.append_ai_review(event)

    def question_review_action(self, candidate_key: str) -> str | None:
        latest = self.current_question_review(candidate_key)
        if not latest:
            return None
        return latest.get("action")

    def question_is_answer_eligible(self, candidate_key: str) -> bool:
        return self.question_review_action(candidate_key) in {"accept", "unblock"}

    def question_answer_eligibility_map(self, candidate_keys: list[str]) -> dict[str, str]:
        keys = [str(key or "") for key in candidate_keys if str(key or "")]
        if not keys:
            return {}
        if self.sql_review_enabled:
            latest_reviews, _counts, _reset = self._sql_question_review_maps(keys)
            return {key: str((latest_reviews.get(key) or {}).get("action") or "") for key in keys}
        return {key: str((self.latest_reviews.get(key) or {}).get("action") or "") for key in keys}

    def _sql_pipeline_payload(self) -> dict[str, Any]:
        counts = {
            "official_documents": 0,
            "question_candidates": 0,
            "question_parse_issues": 0,
            "question_reviewed": 0,
            "question_accepted": 0,
            "question_needs_review": 0,
            "question_blocked": 0,
            "answer_reviewed": 0,
            "answer_accepted": 0,
            "answer_blocked": 0,
            "answer_ready": 0,
            "question_accepted_answer_pending": 0,
            "ready_for_formal": 0,
            "formal_pending_promotion": 0,
            "ai_reviewed": 0,
            "ai_needs_review": 0,
            "ai_blocked": 0,
            "formal_questions": 0,
            "formal_review_drift": 0,
        }
        with self._sql_connect() as conn:
            with conn.cursor() as cur:
                for table_name, key in [
                    ("exam.official_documents", "official_documents"),
                    ("exam.question_candidates", "question_candidates"),
                    ("exam.question_parse_issues", "question_parse_issues"),
                ]:
                    try:
                        if table_name == "exam.question_candidates":
                            cur.execute("SELECT count(*) FROM exam.question_candidates WHERE COALESCE(review_status, '') <> 'excluded'")
                        else:
                            cur.execute(f"SELECT count(*) FROM {table_name}")
                        counts[key] = int(cur.fetchone()[0] or 0)
                    except Exception:
                        conn.rollback()
                cur.execute(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (candidate_key) candidate_key, action
                        FROM exam.question_review_events
                        WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
                        ORDER BY candidate_key, id DESC
                    )
                    SELECT
                        count(*),
                        count(*) FILTER (WHERE action IN ('accept', 'unblock')),
                        count(*) FILTER (WHERE action = 'needs_review'),
                        count(*) FILTER (WHERE action = 'block')
                    FROM latest l
                    JOIN exam.question_candidates c ON c.candidate_key = l.candidate_key
                    WHERE COALESCE(c.review_status, '') <> 'excluded'
                    """
                )
                row = cur.fetchone()
                if row:
                    counts["question_reviewed"] = int(row[0] or 0)
                    counts["question_accepted"] = int(row[1] or 0)
                    counts["question_needs_review"] = int(row[2] or 0)
                    counts["question_blocked"] = int(row[3] or 0)
                cur.execute(
                    """
                    SELECT count(*)
                    FROM exam.question_candidates
                    WHERE NULLIF(raw_candidate_json->>'answer', '') IS NOT NULL
                      AND COALESCE(review_status, '') <> 'excluded'
                    """
                )
                counts["answer_ready"] = int(cur.fetchone()[0] or 0)
                cur.execute(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (candidate_key) candidate_key, action
                        FROM exam.question_review_events
                        WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
                        ORDER BY candidate_key, id DESC
                    )
                    SELECT count(*)
                    FROM exam.question_candidates c
                    JOIN latest l ON l.candidate_key = c.candidate_key
                    WHERE l.action IN ('accept', 'unblock')
                      AND COALESCE(c.review_status, '') <> 'excluded'
                      AND NULLIF(c.raw_candidate_json->>'answer', '') IS NOT NULL
                    """
                )
                counts["question_accepted_answer_pending"] = int(cur.fetchone()[0] or 0)
                cur.execute(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (candidate_key) candidate_key, action
                        FROM exam.answer_review_events
                        ORDER BY candidate_key, id DESC
                    )
                    SELECT
                        count(*),
                        count(*) FILTER (WHERE action IN ('accept', 'unblock')),
                        count(*) FILTER (WHERE action = 'block')
                    FROM latest l
                    JOIN exam.question_candidates c ON c.candidate_key = l.candidate_key
                    WHERE COALESCE(c.review_status, '') <> 'excluded'
                    """
                )
                row = cur.fetchone()
                if row:
                    counts["answer_reviewed"] = int(row[0] or 0)
                    counts["answer_accepted"] = int(row[1] or 0)
                    counts["answer_blocked"] = int(row[2] or 0)
                cur.execute(
                    """
                    WITH latest_question AS (
                        SELECT DISTINCT ON (candidate_key) candidate_key, action
                        FROM exam.question_review_events
                        WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
                        ORDER BY candidate_key, id DESC
                    ),
                    latest_answer AS (
                        SELECT DISTINCT ON (candidate_key) candidate_key, action
                        FROM exam.answer_review_events
                        ORDER BY candidate_key, id DESC
                    ),
                    ready AS (
                        SELECT c.candidate_key
                        FROM exam.question_candidates c
                        JOIN latest_question q ON q.candidate_key = c.candidate_key
                        JOIN latest_answer a ON a.candidate_key = c.candidate_key
                        WHERE q.action IN ('accept', 'unblock')
                          AND a.action IN ('accept', 'unblock')
                          AND COALESCE(c.review_status, '') <> 'excluded'
                    )
                    SELECT
                        count(*),
                        count(*) FILTER (WHERE fq.question_key IS NULL)
                    FROM ready r
                    LEFT JOIN exam.questions fq ON fq.question_key = r.candidate_key
                    """
                )
                row = cur.fetchone()
                if row:
                    counts["ready_for_formal"] = int(row[0] or 0)
                    counts["formal_pending_promotion"] = int(row[1] or 0)
                cur.execute(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (candidate_key) candidate_key, audit_status
                        FROM exam.question_ai_review_events
                        ORDER BY candidate_key, id DESC
                    )
                    SELECT
                        count(*),
                        count(*) FILTER (WHERE audit_status = 'needs_review'),
                        count(*) FILTER (WHERE audit_status IN ('block', 'blocked'))
                    FROM latest l
                    JOIN exam.question_candidates c ON c.candidate_key = l.candidate_key
                    WHERE COALESCE(c.review_status, '') <> 'excluded'
                    """
                )
                row = cur.fetchone()
                if row:
                    counts["ai_reviewed"] = int(row[0] or 0)
                    counts["ai_needs_review"] = int(row[1] or 0)
                    counts["ai_blocked"] = int(row[2] or 0)
                cur.execute("SELECT count(*) FROM exam.questions")
                counts["formal_questions"] = int(cur.fetchone()[0] or 0)
                cur.execute(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (candidate_key) candidate_key, action
                        FROM exam.question_review_events
                        WHERE action NOT IN ('confirm_not_group', 'confirm_group', 'reset_group_review', 'human_review_pdf_visual')
                        ORDER BY candidate_key, id DESC
                    ),
                    latest_answer AS (
                        SELECT DISTINCT ON (candidate_key) candidate_key, action
                        FROM exam.answer_review_events
                        ORDER BY candidate_key, id DESC
                    )
                    SELECT count(*)
                    FROM exam.questions q
                    LEFT JOIN latest l ON l.candidate_key = q.question_key
                    LEFT JOIN latest_answer a ON a.candidate_key = q.question_key
                    WHERE NOT (
                        COALESCE(l.action, '') IN ('accept', 'unblock')
                        AND COALESCE(a.action, '') IN ('accept', 'unblock')
                    )
                    """
                )
                counts["formal_review_drift"] = int(cur.fetchone()[0] or 0)
        return self._pipeline_payload_from_counts(counts)

    def pipeline_payload(self) -> dict[str, Any]:
        if self.sql_review_enabled:
            return self._sql_pipeline_payload()
        reviewed = []
        accepted = []
        blocked = []
        needs_review = []
        answer_ready_count = 0
        question_accepted_answer_pending_count = 0
        answer_reviewed = []
        answer_accepted = []
        answer_blocked = []
        ai_reviewed = []
        ai_needs_review = []
        ai_blocked = []
        for item in self.candidates:
            latest = self.latest_reviews.get(item["candidate_key"])
            if latest:
                reviewed.append(item)
                if latest.get("action") == "accept":
                    accepted.append(item)
                    if item.get("answer") not in (None, ""):
                        question_accepted_answer_pending_count += 1
                elif latest.get("action") in {"block", "exclude"}:
                    blocked.append(item)
                elif latest.get("action") == "needs_review":
                    needs_review.append(item)
            if item.get("answer") not in (None, ""):
                answer_ready_count += 1
            latest_answer = self.latest_answer_reviews.get(item["candidate_key"])
            if latest_answer:
                answer_reviewed.append(item)
                if latest_answer.get("action") == "accept":
                    answer_accepted.append(item)
                elif latest_answer.get("action") == "block":
                    answer_blocked.append(item)
            latest_ai = self.latest_ai_reviews.get(item["candidate_key"])
            if latest_ai:
                ai_reviewed.append(item)
                audit = latest_ai.get("audit") if isinstance(latest_ai.get("audit"), dict) else {}
                ai_suggestion, _ai_suggestion_changes = ai_suggested_correction(item, audit)
                effective_status = effective_ai_audit_status(audit, ai_suggestion)
                if effective_status == "block":
                    ai_blocked.append(item)
                elif effective_status == "needs_review":
                    ai_needs_review.append(item)
        issue_count = sum(len(value) for value in self.issues.values())
        counts = {
            "official_documents": len({item.get("source_registry_key") for item in self.candidates}),
            "question_candidates": len(self.candidates),
            "question_parse_issues": issue_count,
            "question_reviewed": len(reviewed),
            "question_accepted": len(accepted),
            "question_needs_review": len(needs_review),
            "question_blocked": len(blocked),
            "answer_reviewed": len(answer_reviewed),
            "answer_accepted": len(answer_accepted),
            "answer_blocked": len(answer_blocked),
            "question_accepted_answer_pending": question_accepted_answer_pending_count,
            "answer_ready": answer_ready_count,
            "ai_reviewed": len(ai_reviewed),
            "ai_needs_review": len(ai_needs_review),
            "ai_blocked": len(ai_blocked),
        }
        return self._pipeline_payload_from_counts(counts)

    def _pipeline_payload_from_counts(self, counts: dict[str, int]) -> dict[str, Any]:
        return {
            "storage": {
                "review_backend": "sql" if self.sql_review_enabled else "jsonl",
                "sql_primary": bool(self.sql_review_enabled),
                "legacy_jsonl_backup": bool(self.legacy_jsonl_backup_enabled),
                "jsonl_status": "legacy_backup" if self.sql_review_enabled else "primary",
            },
            "candidate_source_jsonl": str(self.candidate_path),
            "issue_source_csv": str(self.issue_path) if self.issue_path else None,
            "legacy_review_log": str(self.review_log),
            "layers": [
                {
                    "name": "官方 PDF / MinerU raw",
                    "tables": ["exam.official_documents", "exam.assets", "exam.document_assets", "exam.mineru_runs"],
                    "status": "source",
                    "count": counts.get("official_documents", 0),
                    "description": "官方 PDF、MinerU markdown、圖片與 layout PDF。這一層只追溯來源，不代表題目已可入庫。",
                },
                {
                    "name": "題目 candidate",
                    "tables": ["exam.question_candidates"],
                    "status": "pre_ingestion",
                    "count": counts.get("question_candidates", 0),
                    "description": "parser 從 MinerU markdown 切出的候選題目，目前仍需人工審核。",
                },
                {
                    "name": "QA flags",
                    "tables": ["exam.question_parse_issues"],
                    "status": "pre_ingestion",
                    "count": counts.get("question_parse_issues", 0),
                    "description": "機械檢查疑點，例如題號重複、選項不足、圖片提示但未偵測圖片。",
                },
                {
                    "name": "題目人工審核",
                    "tables": ["exam.question_review_events"],
                    "status": "human_review",
                    "count": counts.get("question_reviewed", 0),
                    "description": "你在 Review UI 按下通過、保留疑問、阻擋入庫、註記後產生的事件。",
                    "breakdown": {
                        "accepted": counts.get("question_accepted", 0),
                        "needs_review": counts.get("question_needs_review", 0),
                        "blocked": counts.get("question_blocked", 0),
                    },
                },
                {
                    "name": "AI 格式稽核",
                    "tables": ["exam.question_ai_review_events", "exam.model_runs"],
                    "status": "ai_review",
                    "count": counts.get("ai_reviewed", 0),
                    "description": "模型或本機規則對候選題做字形、格式、圖片/表格線索與 parser 結構稽核；只提供疑點，不自動改變人工審核狀態。",
                    "breakdown": {
                        "needs_review": counts.get("ai_needs_review", 0),
                        "blocked": counts.get("ai_blocked", 0),
                    },
                },
                {
                    "name": "答案核對",
                    "tables": ["exam.answer_review_events"],
                    "status": "planned",
                    "count": counts.get("question_accepted_answer_pending", 0),
                    "description": "獨立於題目結構審核。題目通過後，再集中核對答案、MOD/ANS 優先序與答案表解析。",
                    "breakdown": {
                        "candidates_with_answer": counts.get("answer_ready", 0),
                        "question_accepted_answer_pending": counts.get("question_accepted_answer_pending", 0),
                        "answer_reviewed": counts.get("answer_reviewed", 0),
                        "answer_accepted": counts.get("answer_accepted", 0),
                        "answer_blocked": counts.get("answer_blocked", 0),
                    },
                },
                {
                    "name": "正式題庫",
                    "tables": ["exam.question_groups", "exam.questions", "exam.question_options", "exam.answers", "exam.question_assets"],
                    "status": "usable_bank",
                    "count": counts.get("ready_for_formal", 0),
                    "description": "最新題目審核與答案核對都通過的目前可用題目。實體正式表可稍後用 promotion 同步，題組審核是額外結構標籤，不阻擋可用狀態。",
                    "breakdown": {
                        "ready_for_formal": counts.get("ready_for_formal", 0),
                        "formal_questions_physical": counts.get("formal_questions", 0),
                        "pending_promotion": counts.get("formal_pending_promotion", 0),
                        "physical_review_drift": counts.get("formal_review_drift", 0),
                    },
                },
            ],
        }


class Handler(BaseHTTPRequestHandler):
    state: ReviewState

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), format % args))

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            return

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            data = html_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return
        if parsed.path == "/file":
            query = urllib.parse.parse_qs(parsed.query)
            path = safe_file_path(query.get("path", [""])[0])
            if path is None or not path.exists() or not path.is_file():
                self.send_error(404, "File not found or not allowed")
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.end_headers()
            return
        else:
            self.send_error(404, "Not found")
            return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            data = html_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/candidates":
            self.state.refresh_event_logs()
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            payload = self.state.filtered_candidate_payloads(params)
            self.send_json(
                {
                    "candidate_jsonl": str(self.state.candidate_path),
                    "candidate_source_jsonl": str(self.state.candidate_path),
                    "issue_csv": str(self.state.issue_path) if self.state.issue_path else None,
                    "review_log": str(self.state.review_log),
                    "legacy_review_log": str(self.state.review_log),
                    "storage": self.state.candidate_data_status(),
                    **payload,
                }
            )
            return
        if parsed.path == "/api/answer-candidates":
            self.state.refresh_event_logs()
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            payload = self.state.filtered_answer_payloads(params)
            self.send_json(
                {
                    "candidate_jsonl": str(self.state.candidate_path),
                    "candidate_source_jsonl": str(self.state.candidate_path),
                    "review_log": str(self.state.review_log),
                    "legacy_review_log": str(self.state.review_log),
                    "answer_review_log": str(self.state.answer_review_log),
                    "storage": self.state.candidate_data_status(),
                    **payload,
                }
            )
            return
        if parsed.path == "/api/group-candidates":
            self.state.refresh_event_logs()
            query = urllib.parse.parse_qs(parsed.query)
            params = {key: values[0] for key, values in query.items() if values}
            payload = self.state.filtered_group_payloads(params)
            self.send_json(
                {
                    "candidate_jsonl": str(self.state.candidate_path),
                    "candidate_source_jsonl": str(self.state.candidate_path),
                    "review_log": str(self.state.review_log),
                    "legacy_review_log": str(self.state.review_log),
                    "storage": self.state.candidate_data_status(),
                    **payload,
                }
            )
            return
        if parsed.path == "/api/pipeline":
            self.state.refresh_event_logs()
            self.send_json(self.state.pipeline_payload())
            return
        if parsed.path == "/api/preferences":
            query = urllib.parse.parse_qs(parsed.query)
            reviewer = query.get("reviewer", ["local"])[0] or "local"
            self.send_json({"ok": True, "reviewer": reviewer, "preferences": self.state.load_preferences(reviewer)})
            return
        if parsed.path == "/api/reload-status":
            self.state.refresh_event_logs()
            self.send_json(self.state.candidate_data_status())
            return
        if parsed.path == "/file":
            query = urllib.parse.parse_qs(parsed.query)
            path = safe_file_path(query.get("path", [""])[0])
            if path is None or not path.exists() or not path.is_file():
                self.send_error(404, "File not found or not allowed")
                return
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in {"/api/review", "/api/review-batch-accept", "/api/group-confirm-not-group", "/api/group-confirm-group", "/api/group-reset-review", "/api/manual-asset", "/api/answer-review", "/api/answer-review-batch", "/api/ai-question-audit", "/api/ai-question-audit-reset", "/api/preferences", "/api/reload-candidates"}:
            self.send_error(404, "Not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
            return
        self.state.refresh_event_logs()
        if parsed.path == "/api/reload-candidates":
            force = bool(payload.get("force"))
            status = self.state.reload_candidate_data(force=force, block=False)
            status_code = 409 if status.get("busy") else (500 if not status.get("ok") else 200)
            self.send_json(status, status=status_code)
            return
        if parsed.path == "/api/preferences":
            reviewer = payload.get("reviewer") or "local"
            preferences = payload.get("preferences")
            if not isinstance(preferences, dict):
                self.send_json({"ok": False, "error": "preferences must be an object"}, status=400)
                return
            self.state.save_preferences(reviewer, preferences)
            self.send_json({"ok": True, "reviewer": reviewer, "preferences": preferences})
            return
        if parsed.path == "/api/review-batch-accept":
            keys = payload.get("candidate_keys")
            if not isinstance(keys, list) or not keys:
                self.send_json({"ok": False, "error": "candidate_keys must be a non-empty list"}, status=400)
                return
            result = self.state.batch_accept_questions(
                [str(key) for key in keys],
                reviewer=payload.get("reviewer") or "local",
                notes=payload.get("notes") or "批次通過：人工快速瀏覽目前畫面，parser pass 且未被標記 block / needs_review。",
            )
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "saved_count": len(result["saved"]),
                    "skipped_count": len(result["skipped"]),
                    "skipped": result["skipped"][:50],
                }
            )
            return
        if parsed.path == "/api/group-confirm-not-group":
            keys = payload.get("candidate_keys")
            if not isinstance(keys, list) or not keys:
                self.send_json({"ok": False, "error": "candidate_keys must be a non-empty list"}, status=400)
                return
            result = self.state.confirm_not_group(
                [str(key) for key in keys],
                reviewer=payload.get("reviewer") or "local",
                notes=payload.get("notes") or "",
                group_sheet_key=payload.get("group_sheet_key") or "",
            )
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "saved_count": len(result["saved"]),
                    "skipped_count": len(result["skipped"]),
                    "skipped": result["skipped"][:50],
                    "events": result["saved"],
                }
            )
            return
        if parsed.path == "/api/group-reset-review":
            keys = payload.get("candidate_keys")
            if not isinstance(keys, list) or not keys:
                self.send_json({"ok": False, "error": "candidate_keys must be a non-empty list"}, status=400)
                return
            result = self.state.reset_group_review(
                [str(key) for key in keys],
                reviewer=payload.get("reviewer") or "local",
                notes=payload.get("notes") or "",
                group_sheet_key=payload.get("group_sheet_key") or "",
            )
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "saved_count": len(result["saved"]),
                    "skipped_count": len(result["skipped"]),
                    "skipped": result["skipped"][:50],
                    "events": result["saved"],
                }
            )
            return
        if parsed.path == "/api/group-confirm-group":
            keys = payload.get("candidate_keys")
            if not isinstance(keys, list):
                keys = []
            if not keys and payload.get("seed_candidate_key") and payload.get("range"):
                keys = self.state.candidate_keys_for_manual_group_range(
                    str(payload.get("seed_candidate_key") or ""),
                    str(payload.get("range") or ""),
                )
            if not keys:
                self.send_json({"ok": False, "error": "candidate_keys or a valid seed_candidate_key/range is required"}, status=400)
                return
            result = self.state.confirm_group(
                [str(key) for key in keys],
                reviewer=payload.get("reviewer") or "local",
                notes=payload.get("notes") or "",
                group_ref=payload.get("group_ref") or "",
                group_type=payload.get("group_type") or "shared_stem",
                shared_stem=payload.get("shared_stem") or "",
                group_sheet_key=payload.get("group_sheet_key") or "",
            )
            self.send_json(
                {
                    "ok": True,
                    "review_log": str(self.state.review_log),
                    "saved_count": len(result["saved"]),
                    "skipped_count": len(result["skipped"]),
                    "skipped": result["skipped"][:50],
                    "events": result["saved"],
                    "group": result.get("group") or {},
                }
            )
            return
        if parsed.path == "/api/manual-asset":
            candidate_key = str(payload.get("candidate_key") or "")
            data_url = str(payload.get("data_url") or "")
            if not candidate_key:
                self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
                return
            if not data_url:
                self.send_json({"ok": False, "error": "data_url is required"}, status=400)
                return
            try:
                result = self.state.save_manual_image_asset(
                    candidate_key,
                    data_url,
                    reviewer=payload.get("reviewer") or "local",
                    notes=payload.get("notes") or "",
                    caption=payload.get("caption") or "",
                    asset_role=payload.get("asset_role") or "manual_question_image",
                    placement=payload.get("placement") or "stem",
                    target_option=payload.get("target_option") or "",
                    replace_existing=bool(payload.get("replace_existing")),
                )
            except KeyError:
                self.send_json({"ok": False, "error": "candidate_key not found"}, status=404)
                return
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except Exception as exc:
                self.send_json({"ok": False, "error": f"manual asset save failed: {exc}"}, status=500)
                return
            self.send_json({"ok": True, **result})
            return
        if parsed.path == "/api/ai-question-audit":
            candidate_key = str(payload.get("candidate_key") or "")
            if not candidate_key:
                self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
                return
            try:
                event = self.state.run_question_ai_audit(
                    candidate_key,
                    reviewer=payload.get("reviewer") or "local",
                    notes=payload.get("notes") or "",
                )
            except KeyError:
                self.send_json({"ok": False, "error": "candidate_key not found"}, status=404)
                return
            except Exception as exc:
                self.send_json({"ok": False, "error": f"AI audit failed: {exc}"}, status=502)
                return
            self.send_json({"ok": True, "ai_review_log": str(self.state.ai_review_log), "event": event})
            return
        if parsed.path == "/api/ai-question-audit-reset":
            candidate_key = str(payload.get("candidate_key") or "")
            if not candidate_key:
                self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
                return
            try:
                event = self.state.reset_ai_review(
                    candidate_key,
                    reviewer=payload.get("reviewer") or "local",
                    notes=payload.get("notes") or "",
                )
            except KeyError:
                self.send_json({"ok": False, "error": "candidate_key not found"}, status=404)
                return
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            self.send_json({"ok": True, "ai_review_log": str(self.state.ai_review_log), "event": event})
            return
        if parsed.path == "/api/answer-review":
            action = payload.get("action")
            if action not in ANSWER_REVIEW_ACTIONS:
                self.send_json({"ok": False, "error": "Invalid action"}, status=400)
                return
            if not payload.get("candidate_key"):
                self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
                return
            if action in {"accept", "unblock"} and not self.state.question_is_answer_eligible(payload.get("candidate_key")):
                self.send_json(
                    {
                        "ok": False,
                        "error": "Question review is not accepted; answer review cannot pass this item.",
                        "question_review_action": self.state.question_review_action(payload.get("candidate_key")),
                    },
                    status=409,
                )
                return
            if "corrected_answer" in payload:
                payload["corrected_answer"] = "" if payload["corrected_answer"] is None else str(payload["corrected_answer"])
            try:
                saved_event = self.state.append_answer_review(payload)
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
                return
            self.send_json({"ok": True, "answer_review_log": str(self.state.answer_review_log), "event": saved_event})
            return
        if parsed.path == "/api/answer-review-batch":
            action = payload.get("action")
            entries = payload.get("entries")
            if action not in ANSWER_REVIEW_ACTIONS:
                self.send_json({"ok": False, "error": "Invalid action"}, status=400)
                return
            if not isinstance(entries, list) or not entries:
                self.send_json({"ok": False, "error": "entries must be a non-empty list"}, status=400)
                return
            entry_keys = [
                str(entry.get("candidate_key") or "")
                for entry in entries
                if isinstance(entry, dict) and entry.get("candidate_key")
            ]
            eligibility = self.state.question_answer_eligibility_map(entry_keys)
            ineligible = [
                {
                    "candidate_key": entry.get("candidate_key"),
                    "question_review_action": eligibility.get(str(entry.get("candidate_key") or ""), ""),
                }
                for entry in entries
                if isinstance(entry, dict)
                and entry.get("candidate_key")
                and eligibility.get(str(entry.get("candidate_key") or ""), "") not in {"accept", "unblock"}
            ]
            if action in {"accept", "unblock"} and ineligible:
                self.send_json(
                    {
                        "ok": False,
                        "error": "Some questions are not accepted in question review; answer review batch was not saved.",
                        "ineligible": ineligible,
                    },
                    status=409,
                )
                return
            unresolved_mod_entries = [
                {
                    "candidate_key": entry.get("candidate_key"),
                    "corrected_answer": entry.get("corrected_answer"),
                }
                for entry in entries
                if isinstance(entry, dict)
                and entry.get("needs_manual_answer_review")
                and str(entry.get("corrected_answer") or "").strip() in {"", "#"}
            ]
            if action in {"accept", "unblock"} and unresolved_mod_entries:
                self.send_json(
                    {
                        "ok": False,
                        "error": "MOD answers with # or blank values must be resolved before the answer sheet can pass.",
                        "unresolved_mod_entries": unresolved_mod_entries[:50],
                    },
                    status=409,
                )
                return
            events = []
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("candidate_key"):
                    continue
                event = {
                    "candidate_key": entry.get("candidate_key"),
                    "answer_source_registry_key": entry.get("answer_source_registry_key") or "",
                    "action": action,
                    "notes": payload.get("notes") or entry.get("notes") or "",
                    "reviewer": payload.get("reviewer") or "local",
                    "reviewed_answer": entry.get("reviewed_answer") or {"answer": entry.get("answer")},
                    "corrected_answer": "" if entry.get("corrected_answer") is None else str(entry.get("corrected_answer")),
                    "sheet_key": payload.get("sheet_key") or "",
                    "sheet_action": payload.get("sheet_action") or action,
                }
                if payload.get("ai_requested"):
                    event["ai_requested"] = True
                events.append(event)
            try:
                saved_events = self.state.append_answer_reviews_batch(events)
            except SqlWriteError as exc:
                self.send_json({"ok": False, "error": f"SQL write failed: {exc}", "saved_count": 0}, status=500)
                return
            self.send_json(
                {
                    "ok": True,
                    "answer_review_log": str(self.state.answer_review_log),
                    "saved_count": len(saved_events),
                    "events": saved_events,
                }
            )
            return
        action = payload.get("action")
        if action not in QUESTION_REVIEW_ACTIONS:
            self.send_json({"ok": False, "error": "Invalid action"}, status=400)
            return
        if not payload.get("candidate_key"):
            self.send_json({"ok": False, "error": "candidate_key is required"}, status=400)
            return
        if "correction" in payload:
            correction = normalized_correction(payload.get("correction"))
            if not correction:
                payload.pop("correction", None)
            else:
                payload["correction"] = correction
        try:
            saved_event = self.state.append_review(payload)
        except SqlWriteError as exc:
            self.send_json({"ok": False, "error": f"SQL write failed: {exc}"}, status=500)
            return
        self.send_json({"ok": True, "review_log": str(self.state.review_log), "event": saved_event})


PAGE_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>國考題候選審核</title>
  <style>
    :root { color-scheme: light; --line:#d9dee8; --muted:#687385; --bg:#f7f8fb; --ink:#172033; --ok:#0b7a4b; --warn:#9a5b00; --bad:#b42318; --blue:#175cd3; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Noto Sans TC", "Segoe UI", sans-serif; color:var(--ink); background:var(--bg); }
    header { min-height:76px; display:flex; align-items:center; gap:10px; padding:10px 16px; border-bottom:1px solid var(--line); background:white; flex-wrap:wrap; }
    header strong { font-size:16px; }
    header input, header select { height:32px; border:1px solid var(--line); border-radius:6px; padding:0 8px; background:white; max-width:180px; }
    main { display:grid; grid-template-columns: 320px minmax(360px, 1fr) minmax(420px, 1.2fr); height:calc(100vh - 76px); }
    aside { border-right:1px solid var(--line); overflow:auto; background:white; }
    .list-item { width:100%; text-align:left; border:0; border-bottom:1px solid var(--line); background:white; padding:10px 12px; cursor:pointer; }
    .list-item:hover, .list-item.active { background:#eef4ff; }
    .meta { color:var(--muted); font-size:12px; line-height:1.35; }
    .badge { display:inline-block; min-width:44px; text-align:center; padding:2px 6px; border-radius:999px; font-size:12px; background:#edf0f5; color:#334155; }
    .badge.pass { background:#dff7ea; color:var(--ok); }
    .badge.accept { background:#dff7ea; color:var(--ok); }
    .badge.needs_review { background:#fff1cf; color:var(--warn); }
    .badge.blocked { background:#fee4e2; color:var(--bad); }
    .badge.block { background:#fee4e2; color:var(--bad); }
    .badge.exclude { background:#e4e7ec; color:#344054; border:1px solid #98a2b3; }
    .badge.comment { background:#f3e8ff; color:#6941c6; }
    .badge.not_group { background:#eef4ff; color:#175cd3; border:1px solid #b2ccff; }
    .badge.linked { background:#dff7ea; color:var(--ok); border:1px solid #8bd9b1; }
    .badge.visual { background:#fff7ed; color:#c2410c; border:1px solid #fed7aa; }
    .badge.ai { background:#ecfdf3; color:#027a48; border:1px solid #abefc6; }
    .badge.ai-warning { background:#fff1cf; color:var(--warn); border:1px solid #f2c94c; }
    .ai-list-note { margin-top:4px; color:#9a5b00; }
    .badge.reviewed { background:#dbeafe; color:var(--blue); }
    .badge.unreviewed { background:#edf0f5; color:#475467; }
    .badge.reset_review { background:#fff1cf; color:#9a5b00; border:1px solid #f2c94c; }
    .badge.repaired { background:#e0f2fe; color:#0369a1; border:1px solid #7dd3fc; }
    .badge.formal { background:#eef4ff; color:#175cd3; border:1px solid #b2ccff; }
    .badge.formal_drift { background:#fff1cf; color:#9a5b00; border:1px solid #f2c94c; }
    section { overflow:auto; padding:14px; }
    .panel { background:white; border:1px solid var(--line); border-radius:8px; margin-bottom:12px; overflow:hidden; }
    .panel h2 { margin:0; padding:10px 12px; font-size:14px; border-bottom:1px solid var(--line); background:#fbfcff; }
    .panel .body { padding:12px; }
    .stem { white-space:pre-wrap; line-height:1.55; }
    .stem table { white-space:normal; width:100%; border-collapse:collapse; margin:10px 0; font-size:12px; }
    .stem th, .stem td { border:1px solid var(--line); padding:5px 6px; vertical-align:top; }
    .stem tr:nth-child(even) { background:#f8fafc; }
    .math { white-space:nowrap; font-family: "Times New Roman", "Noto Serif", serif; }
    sub, sup { line-height:0; }
    .option { display:grid; grid-template-columns:34px 1fr; gap:8px; margin:8px 0; line-height:1.5; }
    .option b { color:#243b64; }
    .issue { border-left:4px solid #b8c1d1; padding:7px 9px; margin:7px 0; background:#f8fafc; }
    .issue.warning { border-color:#e2a100; }
    .issue.error, .issue.blocked { border-color:#d92d20; }
    .issue.info { border-color:#6b9bff; }
    .toolbar { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
    button.action { border:1px solid var(--line); border-radius:6px; padding:8px 10px; background:white; cursor:pointer; }
    button.action.accept { border-color:#8bd9b1; color:var(--ok); }
    button.action.block { border-color:#f2a19b; color:var(--bad); }
    button.action.primary-accept, button.action.primary-block { min-height:46px; min-width:132px; font-size:16px; font-weight:700; color:white; border:0; }
    button.action.primary-accept { background:var(--ok); }
    button.action.primary-block { background:var(--bad); }
    button.action.active { background:#eef4ff; border-color:#9ab8ff; color:var(--blue); }
    button.nav { border:1px solid var(--line); border-radius:6px; height:32px; padding:0 10px; background:white; cursor:pointer; }
    button.batch-accept { border-color:#0b7a4b; background:#e8fff4; color:#065f46; font-weight:800; }
    button.visual-review { border-color:#fb923c; background:#fff7ed; color:#c2410c; font-weight:800; }
    textarea { width:100%; min-height:72px; resize:vertical; border:1px solid var(--line); border-radius:6px; padding:8px; }
    input.edit-field, textarea.edit-field { width:100%; border:1px solid var(--line); border-radius:6px; padding:8px; background:white; }
    textarea.edit-field { min-height:58px; }
    .edit-grid { display:grid; gap:8px; }
    .edit-option { display:grid; grid-template-columns:34px 1fr; gap:8px; align-items:start; }
    .manual-correction { border-left:4px solid var(--blue); background:#f5f8ff; padding:8px 10px; margin:8px 0; }
    .correction-tools { display:grid; gap:8px; padding:10px; border:1px solid #bfd0ff; border-radius:8px; background:#fbfcff; margin-bottom:10px; }
    .symbol-toolbar { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
    .symbol-toolbar button { border:1px solid var(--line); border-radius:6px; min-width:34px; min-height:30px; padding:5px 8px; background:white; cursor:pointer; font-weight:700; }
    .symbol-toolbar button:hover { border-color:var(--blue); background:#eef4ff; color:var(--blue); }
    .symbol-toolbar .tool-group-label { color:var(--muted); font-size:12px; margin-right:2px; }
    .correction-preview { display:grid; gap:6px; grid-template-columns:1fr; }
    .preview-box { border:1px dashed #b8c1d1; border-radius:6px; background:white; padding:8px; min-height:38px; white-space:pre-wrap; line-height:1.55; }
    .preview-box.empty { color:var(--muted); }
    .quick-actions { display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:10px; border:1px solid var(--line); border-radius:8px; background:#fbfcff; margin:10px 0; }
    iframe { width:100%; height:calc(100vh - 162px); border:1px solid var(--line); border-radius:8px; background:white; }
    .viewer-toolbar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
    .viewer-toolbar button { border:1px solid var(--line); border-radius:6px; padding:7px 9px; background:white; cursor:pointer; }
    .viewer-toolbar button.active { background:#eef4ff; border-color:#9ab8ff; color:var(--blue); }
    .asset-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap:8px; }
    .asset-grid img { width:100%; max-height:160px; object-fit:contain; border:1px solid var(--line); border-radius:6px; background:white; }
    .inline-images { display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:10px; margin:12px 0; }
    .inline-images figure { margin:0; border:1px solid var(--line); border-radius:8px; background:#fbfcff; padding:8px; }
    .inline-images img { width:100%; max-height:280px; object-fit:contain; display:block; background:white; }
    .inline-images figcaption { margin-top:6px; color:var(--muted); font-size:12px; word-break:break-all; }
    .paste-zone { border:2px dashed #9ab8ff; border-radius:8px; background:#f5f8ff; padding:14px; min-height:92px; display:flex; flex-direction:column; gap:8px; justify-content:center; margin:10px 0; outline:none; }
    .paste-zone:focus { border-color:var(--blue); box-shadow:0 0 0 3px #dbeafe; }
    .paste-preview { max-width:100%; max-height:260px; object-fit:contain; border:1px solid var(--line); border-radius:8px; background:white; display:none; }
    .manual-asset-controls { display:grid; gap:8px; margin-top:10px; }
    .manual-asset-controls input, .manual-asset-controls select { border:1px solid var(--line); border-radius:6px; padding:8px; background:white; }
    .asset-placement-buttons { display:grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap:6px; }
    .asset-placement-buttons button { border:1px solid var(--line); border-radius:6px; padding:8px 6px; background:white; cursor:pointer; font-weight:700; }
    .asset-placement-buttons button.active { border-color:var(--blue); background:#eef4ff; color:var(--blue); box-shadow:0 0 0 2px #dbeafe inset; }
    @media (max-width: 900px) { .asset-placement-buttons { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
    .layer { border-left:4px solid #b8c1d1; background:#f8fafc; margin:8px 0; padding:9px 10px; }
    .layer.human_review { border-color:#175cd3; }
    .layer.ai_review { border-color:#027a48; }
    .layer.planned { border-color:#9a5b00; }
    .layer.usable_bank { border-color:#175cd3; }
    .layer.not_bulk_ingested { border-color:#b42318; }
    .kv { display:grid; grid-template-columns:120px 1fr; gap:6px 10px; }
    .question-number { display:inline-flex; align-items:baseline; gap:6px; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:#f8fafc; margin:8px 0; }
    .question-number b { font-size:16px; color:#0f2f5f; }
    .filter-label { display:flex; align-items:center; gap:4px; }
    .mode-tabs { display:flex; gap:4px; }
    .mode-tabs button { height:32px; border:1px solid var(--line); border-radius:6px; padding:0 10px; background:white; cursor:pointer; }
    .mode-tabs button.active { background:#eef4ff; border-color:#9ab8ff; color:var(--blue); font-weight:700; }
    .answer-only, .group-only, .visual-only { display:none; }
    body.answer-mode .answer-only { display:initial; }
    body.answer-mode .question-only, body.answer-mode .group-only, body.answer-mode .visual-only { display:none; }
    body.group-mode .group-only { display:initial; }
    body.group-mode .question-only, body.group-mode .answer-only, body.group-mode .visual-only { display:none; }
    body.visual-mode .visual-only { display:initial; }
    body.visual-mode .question-only, body.visual-mode .answer-only, body.visual-mode .group-only { display:none; }
    body.visual-mode .question-correction-panel { display:none; }
    .answer-sheet-summary { display:grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap:8px; margin:10px 0; }
    .answer-sheet-summary div { border:1px solid var(--line); border-radius:8px; background:#fbfcff; padding:8px; }
    .answer-sheet-summary b { display:block; font-size:18px; }
    .answer-table { width:100%; border-collapse:collapse; font-size:14px; }
    .answer-table th, .answer-table td { border-bottom:1px solid var(--line); padding:7px 6px; vertical-align:top; text-align:left; }
    .answer-table th { position:sticky; top:0; background:#fbfcff; z-index:1; }
    .answer-table input { width:104px; border:1px solid var(--line); border-radius:6px; padding:7px 8px; font-size:18px; font-weight:700; letter-spacing:0; text-align:center; }
    .answer-table .stem-cell { max-width:360px; color:#344054; font-size:12px; line-height:1.4; }
    .answer-cell { display:flex; flex-direction:column; gap:8px; align-items:flex-start; }
    .answer-current { min-width:48px; font-size:22px; font-weight:800; color:#0f2f5f; line-height:1; }
    .answer-choice-panel { display:flex; flex-direction:column; gap:6px; align-items:flex-start; }
    .answer-choice-row, .answer-mode-row { display:flex; gap:4px; flex-wrap:wrap; align-items:center; }
    .answer-choice, .answer-mode, .answer-clear, .answer-select-all { border:1px solid var(--line); border-radius:6px; min-width:34px; height:30px; padding:0 8px; background:white; cursor:pointer; font-weight:700; }
    .answer-choice.active { background:#0f5fb8; border-color:#0f5fb8; color:white; }
    .answer-mode { font-size:12px; font-weight:600; color:#344054; }
    .answer-mode.active { background:#e8fff4; border-color:#0b7a4b; color:#065f46; }
    .answer-clear { color:#b42318; border-color:#f2a19b; }
    .danger-small { margin-top:6px; padding:3px 7px; font-size:12px; color:#b42318; border-color:#f2a19b; background:#fff6f5; }
    .answer-warning { border-left:4px solid #f79009; background:#fff8e6; padding:6px 8px; color:#7a4a00; font-size:12px; }
    .answer-info { border-left:4px solid #6b9bff; background:#f5f8ff; padding:6px 8px; color:#0f2f5f; font-size:12px; }
    .answer-table .option-strip { grid-template-columns: repeat(4, minmax(88px, 1fr)); gap:8px; margin-top:10px; }
    .answer-table .option-strip img { max-height:120px; }
    .answer-table .option-strip figcaption { text-align:center; font-size:13px; color:#344054; }
    .answer-table .blocked-row { background:#fff6f5; }
    .answer-table .mod-warning-row { background:#fffaf0; }
    .answer-table .not-eligible-row { background:#fff1cf; }
    .answer-table .placeholder-row { background:#fff8e6; }
    .answer-table .placeholder-row input { background:#f2f4f7; color:#98a2b3; }
    .answer-table .answer-group-spacer td { height:16px; padding:0; background:#f4f6fa; border-bottom:1px solid #cfd6e3; }
    .answer-format { border-left:4px solid var(--blue); background:#f5f8ff; padding:9px 10px; margin:10px 0; }
    .group-summary { display:grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin:10px 0; }
    .group-summary div { border:1px solid var(--line); border-radius:8px; background:#fbfcff; padding:8px; }
    .group-summary b { display:block; font-size:18px; }
    .group-table { width:100%; border-collapse:collapse; font-size:14px; }
    .group-table th, .group-table td { border-bottom:1px solid var(--line); padding:8px 6px; vertical-align:top; text-align:left; }
    .group-table th { position:sticky; top:0; background:#fbfcff; z-index:1; }
    .group-table .stem-cell { max-width:520px; color:#344054; line-height:1.45; }
    .group-warning { border-left:4px solid #f79009; background:#fff8e6; padding:8px 10px; color:#7a4a00; }
    code { word-break:break-all; }
  </style>
</head>
<body>
  <header>
    <strong>國考題候選審核</strong>
    <div class="mode-tabs">
      <button id="modeQuestion" onclick="setMode('question')">審題</button>
      <button id="modeAnswer" onclick="setMode('answer')">答案</button>
      <button id="modeGroup" onclick="setMode('group')">題組</button>
      <button id="modeVisual" onclick="setMode('visual')">圖片</button>
    </div>
    <input id="search" placeholder="搜尋類科、科目、題號、疑點">
    <select id="status" class="question-only">
      <option value="">全部狀態</option>
      <option value="blocked">系統阻擋</option>
      <option value="needs_review">系統提醒</option>
      <option value="pass">系統通過</option>
    </select>
    <select id="reviewStatus" class="question-only">
      <option value="">全部審核</option>
      <option value="unreviewed" selected>未看過</option>
      <option value="reset_review">退回未審</option>
      <option value="not_accept">未通過</option>
      <option value="exclude">非題目/已排除</option>
      <option value="formal">已入正式庫</option>
      <option value="answer_stage">已進答案關卡</option>
      <option value="formal_drift">入庫後有修正</option>
      <option value="reviewed">已看過</option>
      <option value="accept">已通過</option>
      <option value="correct">有人工校正</option>
      <option value="block">阻擋入庫</option>
      <option value="needs_review">保留疑問</option>
      <option value="comment">有註記</option>
    </select>
    <select id="aiReviewStatus" class="question-only">
      <option value="">全部 AI 稽核</option>
      <option value="unreviewed">AI 未稽核</option>
      <option value="reviewed">AI 已稽核</option>
      <option value="needs_review">AI 有疑點</option>
      <option value="suggested_correction">AI 有建議校正</option>
      <option value="pass">AI 通過</option>
      <option value="block">AI 阻擋</option>
    </select>
    <select id="visualStatus" class="visual-only">
      <option value="">全部圖表狀態</option>
      <option value="visual">圖表待審核</option>
      <option value="visual_asset">待核：已有 MinerU 圖片</option>
      <option value="table">待核：表格/表中資料</option>
      <option value="visual_missing_asset">待核：明確提到圖但目前無圖</option>
      <option value="visual_problem">圖片有問題/待補圖</option>
      <option value="visual_ok">圖片題/已確認</option>
      <option value="no_visual">已確認不需要圖片</option>
      <option value="manual_asset">已有人工補圖(已處理)</option>
    </select>
    <select id="answerReviewStatus" class="answer-only">
      <option value="">全部答案審核</option>
      <option value="unreviewed" selected>答案未看過</option>
      <option value="not_accept">答案未通過</option>
      <option value="reviewed">答案已看過</option>
      <option value="accept">答案已通過</option>
      <option value="block">答案阻擋</option>
      <option value="needs_review">答案保留疑問</option>
      <option value="comment">答案有註記</option>
    </select>
    <select id="groupReviewStatus" class="group-only">
      <option value="">全部題組審核</option>
      <option value="unreviewed" selected>題組未審核</option>
      <option value="reviewed">題組已審核</option>
      <option value="confirmed_group">已確認題組</option>
      <option value="confirmed_not_group">已確認非題組</option>
    </select>
    <label class="filter-label meta">考別<select id="categoryFilter"><option value="">全部</option></select></label>
    <label class="filter-label meta">科目<select id="subjectFilter"><option value="">全部</option></select></label>
    <label class="filter-label meta">年份<select id="yearFilter"><option value="">全部</option></select></label>
    <label class="filter-label meta">考次<select id="ordinalFilter"><option value="">全部</option></select></label>
    <span id="count" class="meta"></span>
    <span id="progress" class="meta"></span>
    <button class="nav question-only batch-accept" onclick="batchAcceptVisiblePass()">批次通過本頁 pass</button>
    <button class="nav" onclick="reloadCandidateData()">重載資料</button>
    <button class="nav" onclick="showPipeline()">資料庫層級</button>
    <span id="dataStatus" class="meta"></span>
    <span id="batchStatus" class="meta"></span>
  </header>
  <main>
    <aside id="list"></aside>
    <section id="detail"></section>
    <section>
      <div class="viewer-toolbar">
        <button id="pdfOfficial" onclick="setPdfKind('official_pdf')">官方 PDF</button>
        <button id="pdfLayout" onclick="setPdfKind('mineru_layout_pdf')">MinerU layout</button>
        <button id="pdfOrigin" onclick="setPdfKind('mineru_origin_pdf')">MinerU origin</button>
        <a id="pdfOpen" class="meta" target="_blank">另開</a>
      </div>
      <iframe id="pdf"></iframe>
      <p id="pdfPath" class="meta"></p>
    </section>
  </main>
<script>
let candidates = [];
let filtered = [];
let current = null;
let currentPdfKind = 'mineru_layout_pdf';
let lastPdfUrl = '';
let pdfUpdateTimer = null;
let preferences = {};
let savePreferenceTimer = null;
const reviewer = 'local';
let totalCount = 0;
let filteredCount = 0;
let reviewedCount = 0;
let sheetCount = 0;
let pendingPreferenceFilters = null;
let mode = 'question';
let fetchSequence = 0;
let fetchAbortController = null;
let candidateDataStatus = null;
let refillTimer = null;
let modeRefreshTimer = null;
const modeDataCache = new Map();
const modeSnapshots = new Map();
const MODE_CACHE_MAX_AGE_MS = 120000;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[m]));
const jsArg = (s) => JSON.stringify(String(s ?? '')).replace(/</g, '\\u003c');
const fileUrl = (path) => path ? `/file?path=${encodeURIComponent(path)}` : '';
let pendingManualAssetDataUrl = '';

const STATUS_LABELS = {
  pass: '系統通過',
  blocked: '系統阻擋',
  block: '阻擋入庫',
  needs_review: '保留疑問',
  accept: '審題通過',
  unblock: '審題通過',
  exclude: '非題目',
  comment: '有註記',
  reviewed: '已看過',
  unreviewed: '未看過',
  reset_review: '退回未審',
  formal: '已入正式庫',
  formal_drift: '正式庫待同步',
  visual: '圖片題',
  visual_asset_ok: '圖片題',
  visual_asset_problem: '圖片有問題',
  no_visual_required: '不需要圖片',
  ai_pass: 'AI 通過',
  ai_needs_review: 'AI 有疑點',
  ai_block: 'AI 阻擋',
  source: '來源層',
  pre_ingestion: '解析暫存',
  human_review: '人工審核',
  ai_review: 'AI 稽核',
  planned: '下一關',
  usable_bank: '正式題庫'
};

function statusLabel(value, fallback = '') {
  const key = String(value ?? '').trim();
  return STATUS_LABELS[key] || fallback || key || '';
}

function aiStatusLabel(status) {
  const normalized = String(status || '').trim();
  if (!normalized) return 'AI 未稽核';
  return STATUS_LABELS[`ai_${normalized}`] || `AI ${normalized}`;
}
function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('file read failed'));
    reader.readAsDataURL(file);
  });
}
async function setManualAssetFile(file) {
  const status = document.getElementById('manualAssetStatus');
  if (!file) return;
  if (!file.type || !file.type.startsWith('image/')) {
    if (status) status.textContent = '剪貼簿或檔案不是圖片。';
    return;
  }
  pendingManualAssetDataUrl = await fileToDataUrl(file);
  const preview = document.getElementById('manualAssetPreview');
  if (preview) {
    preview.src = pendingManualAssetDataUrl;
    preview.style.display = 'block';
  }
  if (status) status.textContent = `已載入圖片：${file.name || 'clipboard image'}，確認後按「儲存補圖」。`;
}
async function handleManualAssetPaste(event) {
  const items = Array.from(event.clipboardData?.items || []);
  const imageItem = items.find(item => item.type && item.type.startsWith('image/'));
  if (!imageItem) return;
  event.preventDefault();
  await setManualAssetFile(imageItem.getAsFile());
}
async function handleAnswerImagePaste(event) {
  const items = Array.from(event.clipboardData?.items || []);
  const imageItem = items.find(item => item.type && item.type.startsWith('image/'));
  if (!imageItem) return;
  event.preventDefault();
  const caption = document.getElementById('manualAssetCaption');
  setManualAssetPlacement('stem');
  if (caption && !caption.value) caption.value = `第 ${current?.question_number || ''} 題補圖`.trim();
  await setManualAssetFile(imageItem.getAsFile());
  const status = document.getElementById('manualAssetStatus');
  if (status) status.textContent = '已讀取截圖；請用上方按鈕選擇題幹、選項或題組共用後儲存。';
}
async function handleManualAssetFile(input) {
  await setManualAssetFile(input.files && input.files[0]);
  input.value = '';
}

function setManualAssetPlacement(placement, targetOption = '') {
  const placementInput = document.getElementById('manualAssetPlacement');
  const targetInput = document.getElementById('manualAssetTargetOption');
  if (placementInput) placementInput.value = placement || 'stem';
  if (targetInput) targetInput.value = targetOption || '';
  updateManualAssetPlacement();
}

function updateManualAssetPlacement() {
  const placement = document.getElementById('manualAssetPlacement')?.value || 'stem';
  const role = document.getElementById('manualAssetRole');
  const targetOption = document.getElementById('manualAssetTargetOption')?.value || '';
  document.querySelectorAll('[data-manual-placement]').forEach(button => {
    const buttonPlacement = button.dataset.manualPlacement || 'stem';
    const buttonOption = button.dataset.manualOption || '';
    button.classList.toggle('active', buttonPlacement === placement && buttonOption === targetOption);
  });
  if (!role) return;
  if (placement === 'table') role.value = 'table_manual_screenshot';
  else if (placement === 'option') role.value = 'option_image';
  else if (placement === 'group') role.value = 'group_shared_asset';
  else role.value = 'manual_question_image';
}

async function saveManualAsset(options = {}) {
  if (!current || !pendingManualAssetDataUrl) {
    const status = document.getElementById('manualAssetStatus');
    if (status) status.textContent = '請先貼上或選擇一張圖片。';
    return;
  }
  const acceptAfterSave = Boolean(options.acceptAfterSave);
  const reviewedKey = current.candidate_key;
  const reviewedIndex = filtered.findIndex(item => item.candidate_key === reviewedKey);
  const status = document.getElementById('manualAssetStatus');
  const notes = document.getElementById('manualAssetNotes')?.value || document.getElementById('notes')?.value || '';
  const caption = document.getElementById('manualAssetCaption')?.value || '';
  const assetRole = document.getElementById('manualAssetRole')?.value || 'manual_question_image';
  const placement = document.getElementById('manualAssetPlacement')?.value || 'stem';
  const targetOption = document.getElementById('manualAssetTargetOption')?.value || '';
  const replaceExisting = Boolean(document.getElementById('manualAssetReplace')?.checked);
  if (placement === 'option' && !targetOption) {
    if (status) status.textContent = '請先選擇要補到哪一個選項。';
    return;
  }
  if (status) status.textContent = '補圖寫入中...';
  const res = await fetch('/api/manual-asset', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      candidate_key: current.candidate_key,
      data_url: pendingManualAssetDataUrl,
      reviewer,
      notes,
      caption,
      asset_role: assetRole,
      placement,
      target_option: targetOption,
      replace_existing: replaceExisting
    })
  });
  const data = await res.json();
  if (!data.ok) {
    if (status) status.textContent = `補圖失敗：${data.error}`;
    return;
  }
  clearCandidateCache();
  pendingManualAssetDataUrl = '';
  if (data.event?.correction) {
    const action = data.event.action || current.review?.action || 'reviewed';
    current.review = {
      ...(current.review || {}),
      status: 'reviewed',
      action,
      notes: data.event.notes || notes,
      updated_at: data.event.created_at,
      has_correction: true,
      correction: data.event.correction,
      event_count: (current.review?.event_count || 0) + 1
    };
    applyCorrectionToCurrent(data.event.correction);
  }
  if (acceptAfterSave) {
    if (mode === 'visual') {
      advanceAfterVisualReview(reviewedKey, reviewedIndex);
      return;
    }
    await review('accept', current.review?.correction || data.event?.correction || null, {stayOnCurrent: true});
    return;
  }
  renderList();
  renderDetail();
  const saved = document.getElementById('saved');
  if (saved) saved.textContent = `已寫入人工補圖；${storageLabel(data.event?.storage)}。請確認畫面後再通過或保留疑問。`;
}

function itemMatchesVisualFilter(item) {
  const visualStatus = filterValue('visualStatus') || 'visual';
  if (!visualStatus) return true;
  const profile = item.visual_profile || {};
  const reviewStatus = profile.visual_review_status || item.visual_review || '';
  const hasAsset = Boolean(profile.has_visual_asset);
  const hasDependency = Boolean(profile.has_visual_dependency);
  const hasTable = Boolean(item.table_markup_suppressed || profile.has_structured_table);
  const hasManual = Boolean(profile.has_manual_asset) || Boolean(profile.visual_asset_roles || item.image_refs || item.stem_image || item.options)
    && JSON.stringify({
      image_refs: item.image_refs || [],
      stem_image: item.stem_image || null,
      options: item.options || []
    }).includes('manual');
  if (visualStatus === 'visual') return !['no_visual_required', 'visual_asset_ok', 'visual_asset_problem'].includes(reviewStatus) && !hasManual && (hasAsset || hasDependency || hasTable);
  if (visualStatus === 'visual_asset') return !['no_visual_required', 'visual_asset_ok', 'visual_asset_problem'].includes(reviewStatus) && !hasManual && hasAsset;
  if (visualStatus === 'visual_missing_asset') return !reviewStatus && hasDependency && !hasAsset && !hasTable;
  if (visualStatus === 'table') return !['no_visual_required', 'visual_asset_ok', 'visual_asset_problem'].includes(reviewStatus) && !hasManual && hasTable;
  if (visualStatus === 'manual_asset') return hasManual;
  if (visualStatus === 'visual_ok') return reviewStatus === 'visual_asset_ok' || hasManual;
  if (visualStatus === 'no_visual') return reviewStatus === 'no_visual_required';
  if (visualStatus === 'visual_problem') return reviewStatus === 'visual_asset_problem';
  return true;
}

function advanceAfterVisualReview(reviewedKey, reviewedIndex) {
  const stillVisible = itemMatchesVisualFilter(current) && itemMatchesCurrentReviewFilter(current);
  if (stillVisible && reviewedIndex >= 0) {
    filtered[reviewedIndex] = current;
    candidates = candidates.map(item => item.candidate_key === reviewedKey ? current : item);
  } else if (reviewedIndex >= 0) {
    filtered.splice(reviewedIndex, 1);
    candidates = candidates.filter(item => item.candidate_key !== reviewedKey);
    if (filteredCount > 0) filteredCount -= 1;
  }
  chooseNextLocal(reviewedKey, Math.max(reviewedIndex, 0));
}

async function saveVisualReviewStatus(status, note) {
  if (!current || mode !== 'visual') return;
  const reviewedKey = current.candidate_key;
  const reviewedIndex = filtered.findIndex(item => item.candidate_key === reviewedKey);
  const existingCorrection = current.review?.correction || collectCorrection();
  const correction = mergeCorrection(existingCorrection, {visual_review: status});
  const noteBox = document.getElementById('notes');
  if (noteBox && !noteBox.value.includes(note)) {
    noteBox.value = [noteBox.value.trim(), note].filter(Boolean).join('\n');
  }
  const result = await review('human_review_pdf_visual', correction, {stayOnCurrent: true, notes: note});
  if (result?.ok) advanceAfterVisualReview(reviewedKey, reviewedIndex);
}

async function markNoVisualRequired() {
  await saveVisualReviewStatus('no_visual_required', '圖片審核：人工確認此題不需要圖片或表格資產，取消疑似需圖標籤。');
}

async function markVisualAssetOk() {
  await saveVisualReviewStatus('visual_asset_ok', '圖片審核：人工確認目前圖片或表格資產正確。');
}

async function markVisualAssetProblem() {
  await saveVisualReviewStatus('visual_asset_problem', '圖片審核：人工確認圖片或表格資產有問題，需要補圖、換圖或重新綁定。');
}

function imageRefPath(ref) {
  if (!ref || typeof ref !== 'object') return '';
  return String(ref.path || ref.path_relative || '');
}

function canonicalImagePath(value) {
  let path = String(value || '').trim();
  if (!path) return '';
  try { path = decodeURIComponent(path); } catch {}
  path = path.replace(/^file:\/\//, '');
  path = path.replace(/\\/g, '/');
  const marker = '/國考題資料夾/';
  const markerIndex = path.indexOf(marker);
  if (markerIndex >= 0) path = `國考題資料夾/${path.slice(markerIndex + marker.length)}`;
  path = path.replace(/^\/Users\/[^/]+\/tw-national-exam-catalog\//, '');
  path = path.replace(/^\.?\//, '');
  return path;
}

function imagePathBasename(value) {
  const path = canonicalImagePath(value);
  return path.split('/').filter(Boolean).pop() || '';
}

function imageRefMatches(ref, path) {
  const refPath = canonicalImagePath(imageRefPath(ref));
  const targetPath = canonicalImagePath(path);
  if (!refPath || !targetPath) return false;
  if (refPath === targetPath) return true;
  if (refPath.endsWith(`/${targetPath}`) || targetPath.endsWith(`/${refPath}`)) return true;
  const refBase = imagePathBasename(refPath);
  const targetBase = imagePathBasename(targetPath);
  return Boolean(refBase && targetBase && refBase === targetBase);
}

function assetFigure(ref, label, options = {}) {
  const path = ref?.path || ref?.path_relative || '';
  if (!ref || !ref.exists || !path) return '';
  const caption = ref.caption || ref.raw_ref || label || '圖片';
  const removeButton = options.allowRemove === false ? '' : `<button class="action danger-small" data-image-path="${esc(path)}" onclick="removeImageRef(this.dataset.imagePath)">解除綁定</button>`;
  return `<figure>
    <a href="${fileUrl(path)}" target="_blank"><img src="${fileUrl(path)}" alt="${esc(label || caption)}"></a>
    <figcaption>${esc(caption)}</figcaption>
    ${removeButton}
  </figure>`;
}

function recomputeCurrentVisualState() {
  if (!current) return;
  const optionImagePaths = new Set(
    (current.options || [])
      .map(option => option?.image?.path || option?.image?.path_relative || '')
      .map(path => canonicalImagePath(path))
      .filter(Boolean)
  );
  current.non_option_image_refs = (current.image_refs || []).filter(ref => !optionImagePaths.has(canonicalImagePath(ref.path || ref.path_relative || '')));
  const refs = [
    ...(current.image_refs || []),
    ...(current.answer_image_refs || []),
    ...(current.stem_image ? [current.stem_image] : []),
    ...((current.options || []).map(option => option?.image).filter(Boolean))
  ].filter(ref => ref && ref.exists !== false);
  const text = `${current.stem || ''}\n${current.metadata?.raw_block || ''}`;
  const hasVisualDependency = current.visual_review !== 'no_visual_required' && /(下圖|附圖|圖中|圖示|如圖|圖片|影像|照片|箭頭|表中|下表|附表|心電圖|X\s*光|X光|超音波|切片圖|染色圖|鏡檢圖|尿沉渣圖|電泳圖|曲線圖|流程圖|家系圖)/i.test(text);
  current.visual_profile = {
    ...(current.visual_profile || {}),
    has_visual_asset: refs.length > 0,
    visual_asset_count: refs.length,
    has_visual_dependency: hasVisualDependency,
    needs_visual_asset_review: hasVisualDependency && refs.length === 0,
    has_manual_asset: JSON.stringify(refs).includes('manual'),
    no_visual_required: current.visual_review === 'no_visual_required',
    visual_review_status: current.visual_review || '',
    visual_reviewed: ['no_visual_required', 'visual_asset_ok', 'visual_asset_problem'].includes(current.visual_review || '')
  };
  current.is_visual_question = Boolean(current.visual_profile.has_visual_asset || current.visual_profile.has_visual_dependency);
}

async function removeImageRef(path) {
  const saved = document.getElementById('saved') || document.getElementById('manualAssetStatus');
  if (!current || !path) {
    if (saved) saved.textContent = '解除綁定失敗：找不到目前題目或圖片路徑。';
    return;
  }
  const existingCorrection = current.review?.correction || {};
  const currentOptions = Array.isArray(current.options) ? current.options.map(option => ({...option})) : [];
  const nextOptions = currentOptions.map(option => {
    if (option.image && imageRefMatches(option.image, path)) {
      const copy = {...option};
      delete copy.image;
      return copy;
    }
    return option;
  });
  const nextImageRefs = (current.image_refs || []).filter(ref => !imageRefMatches(ref, path));
  const nextAnswerImageRefs = (current.answer_image_refs || []).filter(ref => !imageRefMatches(ref, path));
  const correction = mergeCorrection(existingCorrection, {
    image_refs: nextImageRefs,
    answer_image_refs: nextAnswerImageRefs,
    stem_image: current.stem_image && imageRefMatches(current.stem_image, path) ? null : current.stem_image,
    options: nextOptions
  });
  const noteBox = document.getElementById('notes');
  const note = `解除圖片綁定：${path}`;
  if (noteBox && !noteBox.value.includes(note)) {
    noteBox.value = [noteBox.value.trim(), note].filter(Boolean).join('\n');
  }
  if (saved) saved.textContent = '解除圖片綁定中...';
  await review('correct', correction, {stayOnCurrent: true});
}
const compactJson = (value) => {
  if (!value || (typeof value === 'object' && Object.keys(value).length === 0)) return '';
  try { return JSON.stringify(value); } catch { return String(value); }
};
function storageLabel(storage) {
  if (!storage) return '';
  if (storage.sql_primary) {
    return `SQL 已寫入 ${storage.table || ''}${storage.event_id ? ` #${storage.event_id}` : ''}`;
  }
  if (storage.sql_primary === false) return 'JSONL primary 已寫入';
  return '';
}
const greekMap = {
  alpha:'α', beta:'β', gamma:'γ', delta:'δ', epsilon:'ε', zeta:'ζ', eta:'η', theta:'θ',
  iota:'ι', kappa:'κ', lambda:'λ', mu:'μ', nu:'ν', xi:'ξ', omicron:'ο', pi:'π',
  rho:'ρ', sigma:'σ', tau:'τ', upsilon:'υ', phi:'φ', chi:'χ', psi:'ψ', omega:'ω',
  Alpha:'Α', Beta:'Β', Gamma:'Γ', Delta:'Δ', Theta:'Θ', Lambda:'Λ', Xi:'Ξ', Pi:'Π',
  Sigma:'Σ', Phi:'Φ', Psi:'Ψ', Omega:'Ω'
};
function renderMath(value) {
  return esc(value)
    .replace(/\\rightarrow/g, '→')
    .replace(/\\to/g, '→')
    .replace(/\\([A-Za-z]+)/g, (match, name) => greekMap[name] || match)
    .replace(/_\{([^{}]+)\}/g, '<sub>$1</sub>')
    .replace(/\^\{([^{}]+)\}/g, '<sup>$1</sup>')
    .replace(/_([A-Za-z0-9+-])/g, '<sub>$1</sub>')
    .replace(/\^([A-Za-z0-9+-])/g, '<sup>$1</sup>');
}

function normalizeInlineScienceText(value) {
  return String(value ?? '')
    .replace(/核黄素/g, '核黃素')
    .replace(/萘鹼酸/g, '菸鹼酸')
    .replace(/脱氢酶/g, '脫氫酶')
    .replace(/過氧化氢/g, '過氧化氫')
    .replace(/鉷（Co）/g, '鈷（Co）')
    .replace(/鉷/g, '鈷')
    .replace(/麗胺基硫還原酶/g, '麩胺基硫還原酶')
    .replace(/\\uparrow/g, '↑')
    .replace(/\\downarrow/g, '↓')
    .replace(/\\kappa/g, 'κ')
    .replace(/\\lambda/g, 'λ')
    .replace(/\{\}\s*(?=[⁰¹²³⁴⁵⁶⁷⁸⁹]|\d+\s*[A-Z][a-z]?)/g, '')
    .replace(/\b(3|14|32|35|51|57|59|75|99|111|123|125|131)\s*(H|C|Cr|Co|Fe|Se|Tc|In|I|P|S)\b/g, (_, number, element) => `${toSuperscript(number)}${element}`)
    .replace(/([⁰¹²³⁴⁵⁶⁷⁸⁹]+)\s+(H|C|Cr|Co|Fe|Se|Tc|In|I|P|S)\b/g, '$1$2')
    .replace(/\b(LD|ID)\\*([₀₁₂₃₄₅₆₇₈₉0-9]+)\\*/g, (_, prefix, number) => `${prefix}${toSubscript(number)}`)
    .replace(/\b(LD|ID)\s*50\b/g, '$1₅₀')
    .replace(/\b(CD\d+)\s*([+-])(?=\s|$|[，。,；;、）)])/g, (_, marker, sign) => `${marker}${toSuperscript(sign)}`)
    .replace(/\bPrP(sc|c)\b/g, (_, suffix) => `PrP${String(suffix).split('').map(toSuperscript).join('')}`)
    .replace(/\\mathrm\{([^{}]+)\}/g, '$1')
    .replace(/\bFP([₁₂])/g, 'Fp$1')
    .replace(/\b2₂S\b/g, '2₂s')
    .replace(/\b10x\b/g, '10ₓ');
}

function toSuperscript(value) {
  const map = {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹','+':'⁺','-':'⁻','s':'ˢ','c':'ᶜ','S':'ˢ','C':'ᶜ'};
  return String(value ?? '').replace(/[0-9+\-scSC]/g, char => map[char] || char);
}

function toSubscript(value) {
  const map = {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉','+':'₊','-':'₋'};
  return String(value ?? '').replace(/[0-9+\-]/g, char => map[char] || char);
}

function renderInlineMarkupEscaped(value) {
  return value
    .replace(/([A-Za-zΑ-ω]+)_\{([^{}<>]+)\}/g, '$1<sub>$2</sub>')
    .replace(/([A-Za-zΑ-ω]+)\^\{([^{}<>]+)\}/g, '$1<sup>$2</sup>')
    .replace(/([A-Za-zΑ-ω]+)_([A-Za-z0-9+\-₀-₉]+)/g, '$1<sub>$2</sub>')
    .replace(/([A-Za-zΑ-ω]+)\^([A-Za-z0-9+\-₀-₉]+)/g, '$1<sup>$2</sup>');
}

function renderText(value) {
  const raw = String(value ?? '');
  const parts = raw.split(/(\$[^$]+\$)/g);
  return parts.map(part => {
    if (part.startsWith('$') && part.endsWith('$')) {
      return `<span class="math">${renderMath(part.slice(1, -1).trim())}</span>`;
    }
    return renderInlineMarkupEscaped(esc(normalizeInlineScienceText(part)))
      .replace(/&lt;(table|thead|tbody|tfoot|tr|td|th)(?:\s+[^<>]*?)?&gt;/g, '<$1>')
      .replace(/&lt;\/(table|thead|tbody|tfoot|tr|td|th)&gt;/g, '</$1>')
      .replace(/&lt;sub&gt;(.+?)&lt;\/sub&gt;/g, '<sub>$1</sub>')
      .replace(/&lt;sup&gt;(.+?)&lt;\/sup&gt;/g, '<sup>$1</sup>');
  }).join('');
}

function editableText(value) {
  return esc(String(value ?? ''));
}

function correctionFields() {
  return Array.from(document.querySelectorAll('#editStem, #editAnswer, #editGroupRef, .edit-option-text'));
}

function activeCorrectionField() {
  const active = document.activeElement;
  if (active && correctionFields().includes(active)) return active;
  return document.getElementById('editStem') || correctionFields()[0] || null;
}

function insertIntoCorrectionField(text) {
  const field = activeCorrectionField();
  if (!field) return;
  const start = field.selectionStart ?? String(field.value || '').length;
  const end = field.selectionEnd ?? start;
  const value = String(field.value || '');
  field.value = value.slice(0, start) + text + value.slice(end);
  const next = start + text.length;
  field.focus();
  if (field.setSelectionRange) field.setSelectionRange(next, next);
  field.dispatchEvent(new Event('input', {bubbles: true}));
}

function wrapCorrectionSelection(tag) {
  const field = activeCorrectionField();
  if (!field) return;
  const start = field.selectionStart ?? 0;
  const end = field.selectionEnd ?? start;
  const value = String(field.value || '');
  const selected = value.slice(start, end) || (tag === 'sub' ? '2' : '1');
  const replacement = `<${tag}>${selected}</${tag}>`;
  field.value = value.slice(0, start) + replacement + value.slice(end);
  field.focus();
  if (field.setSelectionRange) {
    const innerStart = start + tag.length + 2;
    field.setSelectionRange(innerStart, innerStart + selected.length);
  }
  field.dispatchEvent(new Event('input', {bubbles: true}));
}

function normalizeCorrectionNotation(value) {
  return String(value ?? '')
    .replace(/\\([A-Za-z]+)/g, (match, name) => greekMap[name] || match)
    .replace(/\^\{\\circ\}\s*C/g, '°C')
    .replace(/\^\{o\}\s*C/g, '°C')
    .replace(/\^\{0\}\s*C/g, '°C')
    .replace(/℃/g, '°C')
    .replace(/°\s+C/g, '°C')
    .replace(/([Α-ωA-Za-z])\s*([₀₁₂₃₄₅₆₇₈₉])\b/g, '$1$2')
    .replace(/([αβγδκλμθφω])\s+([0-9]+)\b/g, '$1<sub>$2</sub>')
    .replace(/\b(HCO|PCO|PO|CO|O)([0-9]+)([+\-])?(?=\s|$|[，。,；;、）)\]])/g, (_, prefix, number, charge) => `${prefix}<sub>${number}</sub>${charge ? `<sup>${charge}</sup>` : ''}`)
    .replace(/\b(O|CO|COO|HCO|PCO|PO|H|NADP|NAD|FAD|IgG|IgM|IgA|CD|T|B)_\{([^{}<>]+)\}/g, '$1<sub>$2</sub>')
    .replace(/\b(O|CO|COO|HCO|PCO|PO|H|NADP|NAD|FAD|IgG|IgM|IgA|CD|T|B)\^([+\-0-9]+)\b/g, '$1<sup>$2</sup>');
}

function normalizeActiveCorrectionField() {
  const field = activeCorrectionField();
  if (!field) return;
  const start = field.selectionStart ?? 0;
  const end = field.selectionEnd ?? start;
  const value = String(field.value || '');
  if (end > start) {
    const replacement = normalizeCorrectionNotation(value.slice(start, end));
    field.value = value.slice(0, start) + replacement + value.slice(end);
    field.focus();
    if (field.setSelectionRange) field.setSelectionRange(start, start + replacement.length);
  } else {
    field.value = normalizeCorrectionNotation(value);
    field.focus();
  }
  field.dispatchEvent(new Event('input', {bubbles: true}));
}

function updateCorrectionPreview() {
  const previewStem = document.getElementById('correctionPreviewStem');
  const previewAnswer = document.getElementById('correctionPreviewAnswer');
  if (!previewStem && !previewAnswer) return;
  const stem = document.getElementById('editStem')?.value || '';
  const answer = document.getElementById('editAnswer')?.value || '';
  if (previewStem) {
    previewStem.innerHTML = stem.trim() ? renderText(stem) : '題幹預覽';
    previewStem.classList.toggle('empty', !stem.trim());
  }
  if (previewAnswer) {
    previewAnswer.innerHTML = answer.trim() ? renderText(answer) : '答案預覽';
    previewAnswer.classList.toggle('empty', !answer.trim());
  }
}

function initCorrectionTools() {
  correctionFields().forEach(field => {
    field.addEventListener('input', updateCorrectionPreview);
    field.addEventListener('focus', () => {
      field.dataset.activeCorrectionField = '1';
    });
  });
  updateCorrectionPreview();
}

async function load() {
  const preferenceRes = await fetch(`/api/preferences?reviewer=${encodeURIComponent(reviewer)}`);
  const preferenceData = await preferenceRes.json();
  preferences = preferenceData.preferences || {};
  pendingPreferenceFilters = preferences.filters || {};
  restorePreferences();
  mode = preferences.mode || mode;
  await fetchCandidates(preferences.currentKey || null);
}

function uniqueSorted(values, numeric = false) {
  const items = [...new Set(values.filter(v => v !== undefined && v !== null && String(v).trim() !== '').map(String))];
  return items.sort((a, b) => numeric ? Number(a) - Number(b) : a.localeCompare(b, 'zh-Hant'));
}

function populateSelect(id, values, numeric = false) {
  const select = document.getElementById(id);
  const currentValue = select.value;
  select.innerHTML = '<option value="">全部</option>' + uniqueSorted(values, numeric).map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
  if ([...select.options].some(option => option.value === currentValue)) select.value = currentValue;
}

function populateFilters() {
  return;
}

function populateFiltersFromFacets(facets) {
  populateSelect('categoryFilter', facets.categories || []);
  populateSelect('subjectFilter', facets.subjects || []);
  populateSelect('yearFilter', facets.years || [], true);
  populateSelect('ordinalFilter', facets.ordinals || [], true);
  if (pendingPreferenceFilters) {
    restorePreferences();
    pendingPreferenceFilters = null;
  }
}

function restorePreferences() {
  const filters = preferences.filters || {};
  for (const [id, value] of Object.entries(filters)) {
    const element = document.getElementById(id);
    if (element && Array.from(element.options || []).some(option => option.value === value)) {
      element.value = value;
    } else if (element && element.tagName === 'INPUT') {
      element.value = value || '';
    }
  }
  currentPdfKind = preferences.pdfKind || currentPdfKind;
}

function collectPreferences() {
  return {
    filters: {
      search: document.getElementById('search').value,
      status: document.getElementById('status').value,
      reviewStatus: document.getElementById('reviewStatus').value,
      aiReviewStatus: document.getElementById('aiReviewStatus').value,
      visualStatus: document.getElementById('visualStatus').value,
      groupReviewStatus: document.getElementById('groupReviewStatus').value,
      categoryFilter: document.getElementById('categoryFilter').value,
      subjectFilter: document.getElementById('subjectFilter').value,
      yearFilter: document.getElementById('yearFilter').value,
      ordinalFilter: document.getElementById('ordinalFilter').value
    },
    currentKey: current ? current.candidate_key : preferences.currentKey || '',
    pdfKind: currentPdfKind,
    mode,
    updatedAt: new Date().toISOString()
  };
}

function filterValue(id) {
  if (pendingPreferenceFilters && Object.prototype.hasOwnProperty.call(pendingPreferenceFilters, id)) {
    return pendingPreferenceFilters[id] || '';
  }
  return document.getElementById(id).value;
}

function savePreferencesSoon() {
  preferences = collectPreferences();
  clearTimeout(savePreferenceTimer);
  savePreferenceTimer = setTimeout(() => {
    fetch('/api/preferences', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({reviewer, preferences})
    }).catch(() => {});
  }, 900);
}

function updateModeControls() {
  document.body.className = mode === 'answer' ? 'answer-mode' : mode === 'group' ? 'group-mode' : mode === 'visual' ? 'visual-mode' : '';
  document.getElementById('modeQuestion').className = mode === 'question' ? 'active' : '';
  document.getElementById('modeAnswer').className = mode === 'answer' ? 'active' : '';
  document.getElementById('modeGroup').className = mode === 'group' ? 'active' : '';
  document.getElementById('modeVisual').className = mode === 'visual' ? 'active' : '';
}

function setMode(nextMode) {
  const normalizedMode = ['answer', 'group', 'visual'].includes(nextMode) ? nextMode : 'question';
  if (mode === normalizedMode) return;
  mode = normalizedMode;
  if (mode === 'answer') currentPdfKind = 'official_pdf';
  if (mode === 'visual') {
    const visualSelect = document.getElementById('visualStatus');
    if (visualSelect && !visualSelect.value) visualSelect.value = 'visual';
  }
  updateModeControls();
  const showedSnapshot = renderModeSnapshotIfAvailable();
  if (showedSnapshot) scheduleModeBackgroundRefresh();
  else applyFilter(null, null, null, {useCache: true, showLoading: true});
}

function startVisualReview() {
  mode = 'visual';
  document.getElementById('visualStatus').value = 'visual';
  updateModeControls();
  const showedSnapshot = renderModeSnapshotIfAvailable();
  if (showedSnapshot) scheduleModeBackgroundRefresh();
  else applyFilter(null, null, null, {useCache: true, showLoading: true});
}

async function showPipeline() {
  const res = await fetch('/api/pipeline');
  const data = await res.json();
  current = null;
  renderList();
  document.getElementById('pdf').src = '';
  lastPdfUrl = '';
  document.getElementById('pdfOpen').removeAttribute('href');
  document.getElementById('pdfPath').textContent = '';
  const layers = data.layers.map(layer => `
    <div class="layer ${esc(layer.status)}">
      <h3>${esc(layer.name)} <span class="badge">${esc(layer.count)}</span></h3>
      <p>${esc(layer.description)}</p>
      <div class="kv">
        <span class="meta">狀態</span><code>${esc(statusLabel(layer.status))}</code>
        <span class="meta">表格</span><code>${esc((layer.tables || []).join(', '))}</code>
        ${layer.breakdown ? `<span class="meta">細項</span><code>${esc(JSON.stringify(layer.breakdown))}</code>` : ''}
      </div>
    </div>
  `).join('');
  const storage = data.storage || {};
  document.getElementById('detail').innerHTML = `
    <div class="panel"><h2>資料庫入庫層級</h2><div class="body">
      <p><span class="badge ${storage.sql_primary ? 'accept' : 'needs_review'}">${storage.sql_primary ? 'SQL primary' : 'JSONL primary'}</span> <span class="meta">JSONL 狀態：${esc(storage.jsonl_status || '')}</span></p>
      <p class="meta">Candidate source snapshot: <code>${esc(data.candidate_source_jsonl || data.candidate_jsonl || '')}</code></p>
      <p class="meta">Issue source snapshot: <code>${esc(data.issue_source_csv || data.issue_csv || '')}</code></p>
      <p class="meta">Legacy review backup: <code>${esc(data.legacy_review_log || data.review_log || '')}</code></p>
      ${layers}
    </div></div>`;
}

function queryParams() {
  const params = new URLSearchParams();
  params.set('q', filterValue('search').trim());
  params.set('status', mode === 'question' ? filterValue('status') : '');
  params.set('reviewStatus', mode === 'question' ? filterValue('reviewStatus') : '');
  params.set('aiReviewStatus', mode === 'question' ? filterValue('aiReviewStatus') : '');
  params.set('visualStatus', mode === 'visual' ? (filterValue('visualStatus') || 'visual') : '');
  params.set('answerReviewStatus', mode === 'answer' ? filterValue('answerReviewStatus') : '');
  params.set('groupReviewStatus', mode === 'group' ? filterValue('groupReviewStatus') : '');
  params.set('category', filterValue('categoryFilter'));
  params.set('subject', filterValue('subjectFilter'));
  params.set('year', filterValue('yearFilter'));
  params.set('ordinal', filterValue('ordinalFilter'));
  params.set('limit', mode === 'group' ? '200' : '500');
  return params;
}

function endpointForCurrentMode() {
  return mode === 'answer' ? '/api/answer-candidates' : mode === 'group' ? '/api/group-candidates' : '/api/candidates';
}

function requestKeyForCurrentMode(endpoint, params) {
  return `${mode} ${endpoint}?${params.toString()}`;
}

function clearCandidateCache() {
  modeDataCache.clear();
  modeSnapshots.clear();
}

function cacheCandidateData(key, data) {
  modeDataCache.set(key, {data, timestamp: Date.now()});
  modeSnapshots.set(mode, {data, timestamp: Date.now()});
  if (modeDataCache.size > 16) {
    const oldestKey = [...modeDataCache.entries()].sort((a, b) => a[1].timestamp - b[1].timestamp)[0]?.[0];
    if (oldestKey) modeDataCache.delete(oldestKey);
  }
}

function renderModeSnapshotIfAvailable() {
  const snapshot = modeSnapshots.get(mode);
  if (!snapshot) return false;
  applyCandidateData(snapshot.data, current?.candidate_key || null, null, null);
  const status = document.getElementById('dataStatus');
  if (status) status.textContent = '背景更新中';
  return true;
}

function scheduleModeBackgroundRefresh() {
  if (modeRefreshTimer) clearTimeout(modeRefreshTimer);
  const refreshMode = mode;
  savePreferencesSoon();
  modeRefreshTimer = setTimeout(() => {
    modeRefreshTimer = null;
    if (mode !== refreshMode) return;
    fetchCandidates(current?.candidate_key || null, null, null, {useCache: false, showLoading: false, allowStaleModeCache: false});
  }, 1200);
}

function latestModeCache() {
  const prefix = `${mode} `;
  const matches = [...modeDataCache.entries()]
    .filter(([key]) => key.startsWith(prefix))
    .sort((a, b) => b[1].timestamp - a[1].timestamp);
  return matches[0]?.[1] || null;
}

function applyCandidateData(data, preferredKey = null, preferredIndex = null, skipKey = null) {
  candidates = data.candidates || [];
  filtered = candidates;
  totalCount = mode === 'answer' ? (data.eligible_count || candidates.length) : (data.total_count || candidates.length);
  filteredCount = data.filtered_count || candidates.length;
  sheetCount = data.sheet_count || candidates.length;
  reviewedCount = data.reviewed_count || 0;
  candidateDataStatus = data.candidate_data || null;
  updateCandidateDataStatus();
  if (data.facets) populateFiltersFromFacets(data.facets);
  chooseCurrent(preferredKey, preferredIndex, skipKey);
}

function renderCandidateLoadingState() {
  document.getElementById('count').textContent = '讀取中...';
  document.getElementById('progress').textContent = '';
  const status = document.getElementById('dataStatus');
  if (status) status.textContent = '切換中';
  const detail = document.getElementById('detail');
  if (detail) {
    detail.innerHTML = `<div class="panel"><h2>讀取中</h2><div class="body"><p class="meta">正在切換審核關卡...</p></div></div>`;
  }
}

async function fetchCandidates(preferredKey = null, preferredIndex = null, skipKey = null, options = {}) {
  updateModeControls();
  const useCache = options.useCache !== false;
  const showLoading = options.showLoading !== false;
  const params = queryParams();
  const endpoint = endpointForCurrentMode();
  const requestKey = requestKeyForCurrentMode(endpoint, params);
  const cached = useCache ? modeDataCache.get(requestKey) : null;
  if (cached && Date.now() - cached.timestamp < MODE_CACHE_MAX_AGE_MS) {
    applyCandidateData(cached.data, preferredKey, preferredIndex, skipKey);
    return;
  }
  const staleModeCache = useCache ? latestModeCache() : null;
  if (staleModeCache && options.allowStaleModeCache !== false) {
    applyCandidateData(staleModeCache.data, preferredKey, preferredIndex, skipKey);
    const status = document.getElementById('dataStatus');
    if (status) status.textContent = '背景更新中';
  }
  const requestId = ++fetchSequence;
  if (fetchAbortController) {
    fetchAbortController.abort();
  }
  fetchAbortController = new AbortController();
  const signal = fetchAbortController.signal;
  if (showLoading && !staleModeCache) renderCandidateLoadingState();
  try {
    const res = await fetch(`${endpoint}?${params.toString()}`, {signal});
    const data = await res.json();
    if (signal.aborted || requestId !== fetchSequence) return;
    cacheCandidateData(requestKey, data);
    applyCandidateData(data, preferredKey, preferredIndex, skipKey);
  } catch (err) {
    if (err && err.name === 'AbortError') return;
    const detail = document.getElementById('detail');
    if (detail && requestId === fetchSequence) {
      detail.innerHTML = `<div class="panel"><h2>讀取失敗</h2><div class="body"><p class="error">${esc(err?.message || err)}</p></div></div>`;
    }
  } finally {
    if (fetchAbortController && fetchAbortController.signal === signal) {
      fetchAbortController = null;
    }
  }
}

function updateCandidateDataStatus() {
  const element = document.getElementById('dataStatus');
  if (!element) return;
  if (!candidateDataStatus) {
    element.textContent = '';
    return;
  }
  if (candidateDataStatus.busy) {
    element.textContent = '資料重載中';
  } else if (candidateDataStatus.candidate_stale || candidateDataStatus.issue_stale) {
    element.textContent = '候選資料已更新，請按重載資料';
  } else {
    element.textContent = '';
  }
}

async function reloadCandidateData() {
  clearCandidateCache();
  const element = document.getElementById('dataStatus');
  if (element) element.textContent = '資料重載中...';
  const res = await fetch('/api/reload-candidates', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({force: false})
  });
  const data = await res.json();
  candidateDataStatus = data;
  updateCandidateDataStatus();
  if (!res.ok) return;
  await fetchCandidates(current?.candidate_key || null);
}

function applyFilter(preferredKey = null, preferredIndex = null, skipKey = null, options = {}) {
  if (modeRefreshTimer) {
    clearTimeout(modeRefreshTimer);
    modeRefreshTimer = null;
  }
  if (preferredKey && typeof preferredKey === 'object' && Object.prototype.hasOwnProperty.call(preferredKey, 'target')) {
    preferredKey = null;
    preferredIndex = null;
    skipKey = null;
  }
  pendingPreferenceFilters = null;
  savePreferencesSoon();
  fetchCandidates(preferredKey, preferredIndex, skipKey, options);
}

function itemMatchesCurrentReviewFilter(item) {
  if (mode !== 'question') return true;
  const reviewStatus = filterValue('reviewStatus');
  const review = item.review || {};
  const status = review.status || 'unreviewed';
  const action = review.action || '';
  if (reviewStatus !== 'exclude' && action === 'exclude') return false;
  if (!reviewStatus) return true;
  if (reviewStatus === 'not_accept') {
    return (status === 'reviewed' && !['accept', 'correct', 'unblock', 'exclude'].includes(action)) || Boolean(review.is_reset_unreviewed);
  }
  if (reviewStatus === 'exclude') {
    return action === 'exclude';
  }
  if (reviewStatus === 'correct') {
    return Boolean(review.correction);
  }
  if (reviewStatus === 'reset_review') {
    return Boolean(review.is_reset_unreviewed);
  }
  if (reviewStatus === 'formal') {
    return Boolean(item.formal?.ready_for_formal);
  }
  if (reviewStatus === 'formal_drift') {
    return Boolean(item.formal?.review_drift);
  }
  if (reviewStatus === 'answer_stage') {
    return ['accept', 'unblock'].includes(action);
  }
  return status === reviewStatus || action === reviewStatus;
}

function updateProgressCountsAfterLocalReview(wasReviewed, stillVisible) {
  if (!wasReviewed) reviewedCount += 1;
  if (!stillVisible && filteredCount > 0) filteredCount -= 1;
}

function updateCountLabels() {
  if (mode === 'answer') {
    document.getElementById('count').textContent = `顯示 ${filtered.length} 張答案表 / 符合 ${filteredCount} 題 / 可核答案 ${totalCount} 題`;
    document.getElementById('progress').textContent = `答案已看 ${reviewedCount} 題，未看 ${Math.max(totalCount - reviewedCount, 0)} 題`;
  } else if (mode === 'group') {
    document.getElementById('count').textContent = `顯示 ${filtered.length} 組 / 疑似題組 ${filteredCount} 組 / 題目 ${totalCount}`;
    const groupStatus = filterValue('groupReviewStatus');
    document.getElementById('progress').textContent = groupStatus
      ? `題組篩選：${document.getElementById('groupReviewStatus')?.selectedOptions?.[0]?.textContent || groupStatus}`
      : `題組層先做結構檢查，正式通過仍回到審題與答案核對`;
  } else if (mode === 'visual') {
    document.getElementById('count').textContent = `顯示 ${filtered.length} / 圖片條件 ${filteredCount} / 全部 ${totalCount}`;
    const visualStatus = filterValue('visualStatus') || 'visual';
    document.getElementById('progress').textContent = `圖片篩選：${document.getElementById('visualStatus')?.selectedOptions?.[0]?.textContent || visualStatus}`;
  } else {
    document.getElementById('count').textContent = `顯示 ${filtered.length} / 符合 ${filteredCount} / 全部 ${totalCount}`;
    document.getElementById('progress').textContent = `已看 ${reviewedCount}，未看 ${Math.max(filteredCount - reviewedCount, 0)}`;
  }
}

function chooseNextLocal(reviewedKey, reviewedIndex) {
  const next = filtered.find((item, index) => index >= reviewedIndex && item.candidate_key !== reviewedKey)
    || [...filtered].reverse().find((item, index) => filtered.length - 1 - index < reviewedIndex && item.candidate_key !== reviewedKey)
    || null;
  current = next;
  updateCountLabels();
  renderList();
  renderDetail();
  savePreferencesSoon();
  scheduleCandidateRefill();
}

function scheduleCandidateRefill() {
  if (!['question', 'visual'].includes(mode)) return;
  if (refillTimer) clearTimeout(refillTimer);
  if (filtered.length > 35 || filteredCount <= filtered.length) return;
  const preferredKey = current?.candidate_key || null;
  refillTimer = setTimeout(() => {
    refillTimer = null;
    fetchCandidates(preferredKey);
  }, 450);
}

function chooseCurrent(preferredKey = null, preferredIndex = null, skipKey = null) {
  const q = document.getElementById('search').value.trim().toLowerCase();
  if (mode === 'answer') {
    updateCountLabels();
  } else {
    updateCountLabels();
  }

  let next = null;
  if (preferredKey) {
    next = filtered.find(item => item.candidate_key === preferredKey) || null;
  }
  if (!next && Number.isInteger(preferredIndex) && preferredIndex !== null && filtered.length) {
    const forward = filtered[Math.min(preferredIndex, filtered.length - 1)] || null;
    if (forward && forward.candidate_key !== skipKey) {
      next = forward;
    } else {
      next = filtered.find((item, index) => index > preferredIndex && item.candidate_key !== skipKey) || null;
      next = next || [...filtered].reverse().find((item, index) => filtered.length - 1 - index < preferredIndex && item.candidate_key !== skipKey) || null;
    }
  }
  if (!next && current && current.candidate_key !== skipKey) {
    next = filtered.find(item => item.candidate_key === current.candidate_key) || null;
  }
  if (!next && filtered.length) {
    next = filtered.find(item => item.candidate_key !== skipKey) || null;
  }
  current = next;
  renderList();
  renderDetail();
  savePreferencesSoon();
}

function renderList() {
  const list = document.getElementById('list');
  list.innerHTML = filtered.map(item => {
    const meta = item.metadata || {};
    if (mode === 'group' && item.sheet_type === 'group_sheet') {
      const status = item.gate_status || 'group';
      const groupReviewStatus = item.group_review_status || 'unreviewed';
      const groupReviewBadge = groupReviewStatus === 'confirmed_group'
        ? '<span class="badge linked">已審題組</span>'
        : groupReviewStatus === 'confirmed_not_group'
          ? '<span class="badge not_group">已審非題組</span>'
          : groupReviewStatus === 'reviewed'
            ? '<span class="badge reviewed">已審</span>'
            : '<span class="badge unreviewed">未審</span>';
      const groupKind = item.group_ref ? '已綁題組' : item.inferred_group_kind === 'explicit_count' ? '明示範圍題組' : item.inferred_group_ref ? '承上題關聯' : '疑似未綁';
      const reasons = Object.entries(item.reason_counts || {}).map(([key, value]) => `${key} ${value}`).join('、');
      return `<button class="list-item ${current && current.candidate_key === item.candidate_key ? 'active' : ''}" data-key="${esc(item.candidate_key)}" onclick="selectCandidate('${esc(item.candidate_key)}')">
        <div>${groupReviewBadge} <span class="badge ${esc(status)}">${esc(groupKind)}</span> ${esc(item.group_label || '題組候選')}</div>
        <div class="meta">${esc(meta.normalized_category_name || meta.group_name)} ${esc(meta.year)}-${esc(meta.exam_ordinal)} ${esc(meta.normalized_subject_name)}</div>
        <div class="meta">${esc(item.question_count || 0)} 題；${esc(reasons || '題組線索')}</div>
      </button>`;
    }
    if (mode === 'answer' && item.sheet_type === 'answer_sheet') {
      const review = item.answer_review || {};
      const reviewBadge = review.is_reset_unreviewed ? 'reset_review' : (review.action || review.status || 'unreviewed');
      const reviewLabel = review.is_reset_unreviewed ? '退回未審' : statusLabel(reviewBadge);
      const role = item.answer_role_label || 'unknown';
      return `<button class="list-item ${current && current.candidate_key === item.candidate_key ? 'active' : ''}" data-key="${esc(item.candidate_key)}" onclick="selectCandidate('${esc(item.candidate_key)}')">
        <div><span class="badge ${esc(item.answer_gate_status || 'pass')}">${esc(statusLabel(item.answer_gate_status || 'pass'))}</span> <span class="badge ${esc(reviewBadge)}">${esc(reviewLabel)}</span> ${esc(role)} 答案表</div>
        <div class="meta">${esc(meta.normalized_category_name || meta.group_name)} ${esc(meta.year)}-${esc(meta.exam_ordinal)} ${esc(meta.normalized_subject_name)}</div>
        <div class="meta">${esc(item.reviewed_count || 0)} / ${esc(item.question_count || 0)} 題已核對，${esc(item.answer_issue_count || 0)} 個答案疑點</div>
      </button>`;
    }
    const review = mode === 'answer' ? (item.answer_review || {}) : (item.review || {});
    const reviewBadge = review.is_reset_unreviewed ? 'reset_review' : (review.action || review.status || 'unreviewed');
    const reviewLabel = review.is_reset_unreviewed ? '退回未審' : statusLabel(reviewBadge);
    const aiReview = item.ai_review || {};
    const aiBadge = aiReview.audit_status && aiReview.audit_status !== 'pass'
      ? `<span class="badge ai-warning">${esc(aiStatusLabel(aiReview.audit_status))}</span>`
      : '';
    const formal = item.formal || {};
    const formalBadge = formal.review_drift
      ? '<span class="badge formal_drift">正式庫待同步</span>'
      : formal.ready_for_formal
        ? '<span class="badge formal">已入正式庫</span>'
        : formal.physical_in_formal
          ? '<span class="badge formal_drift">正式表舊資料</span>'
        : '';
    const firstAiFinding = (aiReview.findings || [])[0];
    const aiSummary = firstAiFinding
      ? `<div class="meta ai-list-note">AI 建議：${esc(firstAiFinding.message || firstAiFinding.suggestion || firstAiFinding.code || '')}</div>`
      : aiReview.summary && aiReview.audit_status && aiReview.audit_status !== 'pass'
        ? `<div class="meta ai-list-note">AI 摘要：${esc(aiReview.summary)}</div>`
        : '';
    const statusText = mode === 'answer' ? (item.answer_gate_status || 'pass') : (item.question_quality_status || item.quality_status);
    const statusBadge = statusText && statusText !== 'pass'
      ? `<span class="badge ${esc(statusText)}">${esc(statusLabel(statusText))}</span>`
      : '';
    const visualStatus = item.visual_profile?.visual_review_status || item.visual_review || '';
    const isManualVisual = Boolean(item.visual_profile?.has_manual_asset);
    const visualBadge = visualStatus === 'visual_asset_ok' || isManualVisual
      ? '<span class="badge visual">圖片題</span>'
      : visualStatus === 'visual_asset_problem'
        ? '<span class="badge needs_review">圖待修</span>'
        : visualStatus === 'no_visual_required'
          ? '<span class="badge reviewed">不需圖</span>'
          : item.is_visual_question
            ? `<span class="badge visual">${item.visual_profile?.has_visual_asset ? '圖片待核' : '疑似需圖'}</span>`
            : '';
    return `<button class="list-item ${current && current.candidate_key === item.candidate_key ? 'active' : ''}" data-key="${esc(item.candidate_key)}" onclick="selectCandidate('${esc(item.candidate_key)}')">
      <div>${statusBadge} ${formalBadge} ${visualBadge} ${aiBadge} <span class="badge ${esc(reviewBadge)}">${esc(reviewLabel)}</span> 第 ${esc(item.question_number)} 題</div>
      <div class="meta">${esc(meta.group_name)} ${esc(meta.year)}-${esc(meta.exam_ordinal)} ${esc(meta.normalized_subject_name)}</div>
      <div class="meta">${mode === 'answer' ? esc(item.answer_issue_count || 0) + ' 個答案疑點' : esc(item.question_issue_count ?? item.issue_count ?? 0) + ' 個題目疑點'}</div>
      ${aiSummary}
    </button>`;
  }).join('');
  scrollCurrentListItemIntoView();
}

function selectCandidate(key) {
  current = candidates.find(item => item.candidate_key === key);
  renderList();
  renderDetail();
  savePreferencesSoon();
}

async function openQuestionCandidate(key) {
  mode = 'question';
  document.getElementById('search').value = key;
  document.getElementById('reviewStatus').value = '';
  document.getElementById('aiReviewStatus').value = '';
  document.getElementById('status').value = '';
  updateModeControls();
  await fetchCandidates(key);
}

function scrollCurrentListItemIntoView() {
  if (!current) return;
  const active = document.querySelector(`.list-item[data-key="${CSS.escape(current.candidate_key)}"]`);
  if (active) active.scrollIntoView({block: 'nearest'});
}

function moveSelection(delta) {
  if (!filtered.length) return;
  const currentIndex = current ? filtered.findIndex(item => item.candidate_key === current.candidate_key) : -1;
  const nextIndex = Math.max(0, Math.min(filtered.length - 1, currentIndex + delta));
  const next = filtered[nextIndex];
  if (!next || next.candidate_key === current?.candidate_key) return;
  current = next;
  renderList();
  renderDetail();
  savePreferencesSoon();
}

function pdfPathFor(kind) {
  const files = current && current.source_files ? current.source_files : {};
  return files[kind] || files.mineru_layout_pdf || files.official_pdf || files.mineru_origin_pdf || '';
}

function setPdfKind(kind) {
  currentPdfKind = kind;
  updatePdfViewer();
  savePreferencesSoon();
}

function updatePdfViewer() {
  if (!current) return;
  const path = pdfPathFor(currentPdfKind);
  const url = fileUrl(path);
  if (url !== lastPdfUrl) {
    lastPdfUrl = url;
    if (pdfUpdateTimer) clearTimeout(pdfUpdateTimer);
    pdfUpdateTimer = setTimeout(() => {
      pdfUpdateTimer = null;
      if (lastPdfUrl === url) document.getElementById('pdf').src = url;
    }, 0);
  }
  document.getElementById('pdfOpen').href = url;
  document.getElementById('pdfPath').innerHTML = path ? `<code>${esc(path)}</code>` : '找不到可顯示的 PDF';
  for (const [kind, id] of [['official_pdf', 'pdfOfficial'], ['mineru_layout_pdf', 'pdfLayout'], ['mineru_origin_pdf', 'pdfOrigin']]) {
    const btn = document.getElementById(id);
    const exists = Boolean(pdfPathFor(kind));
    btn.disabled = !exists;
    btn.className = currentPdfKind === kind ? 'active' : '';
  }
}

function parseAnswerSelection(value, preferredMode = '') {
  const text = String(value ?? '').trim().toUpperCase();
  const letters = [];
  for (const letter of text.match(/[A-D]/g) || []) {
    if (!letters.includes(letter)) letters.push(letter);
  }
  let mode = preferredMode || 'single';
  if (text.includes('+')) mode = 'all';
  else if (letters.length > 1 && letters.some(letter => text.includes(letters.join('')))) mode = 'any_combo';
  else if (text.includes('|') || letters.length > 1) mode = 'any';
  return {letters, mode, raw: text};
}

function formatAnswerSelection(letters, mode) {
  const unique = [];
  for (const letter of letters || []) {
    const normalized = String(letter || '').toUpperCase();
    if (/^[A-D]$/.test(normalized) && !unique.includes(normalized)) unique.push(normalized);
  }
  if (!unique.length) return '';
  if (mode === 'all') return unique.join('+');
  if (mode === 'any_combo' && unique.length > 1) return answerCombinationValues(unique).join('|');
  if (mode === 'any') return unique.join('|');
  return unique[unique.length - 1];
}

function answerCombinationValues(letters) {
  const unique = [];
  for (const letter of letters || []) {
    const normalized = String(letter || '').toUpperCase();
    if (/^[A-D]$/.test(normalized) && !unique.includes(normalized)) unique.push(normalized);
  }
  const combinations = [];
  function collect(start, size, current) {
    if (current.length === size) {
      combinations.push(current.join(''));
      return;
    }
    for (let index = start; index < unique.length; index += 1) {
      collect(index + 1, size, [...current, unique[index]]);
    }
  }
  for (let size = 1; size <= unique.length; size += 1) {
    collect(0, size, []);
  }
  return combinations;
}

function findAnswerInput(key) {
  return Array.from(document.querySelectorAll('.answer-edit')).find(input => input.dataset.key === key) || null;
}

function findAnswerPanel(key) {
  return Array.from(document.querySelectorAll('.answer-choice-panel')).find(panel => panel.dataset.key === key) || null;
}

function updateAnswerChoicePanel(key) {
  const input = findAnswerInput(key);
  const panel = findAnswerPanel(key);
  if (!input || !panel) return;
  const parsed = parseAnswerSelection(input.value, input.dataset.mode || panel.dataset.mode || '');
  input.dataset.mode = parsed.mode;
  panel.dataset.mode = parsed.mode;
  const display = panel.querySelector('[data-answer-display]');
  if (display) display.textContent = input.value.trim() || '空白';
  for (const button of panel.querySelectorAll('.answer-choice')) {
    button.classList.toggle('active', parsed.letters.includes(button.dataset.letter));
  }
  for (const button of panel.querySelectorAll('.answer-mode')) {
    button.classList.toggle('active', button.dataset.mode === parsed.mode);
  }
}

function setAnswerMode(key, mode) {
  const input = findAnswerInput(key);
  if (!input) return;
  input.dataset.mode = mode;
  const parsed = parseAnswerSelection(input.value, mode);
  input.value = formatAnswerSelection(parsed.letters, mode);
  updateAnswerChoicePanel(key);
}

function toggleAnswerChoice(key, letter) {
  const input = findAnswerInput(key);
  if (!input) return;
  const parsed = parseAnswerSelection(input.value, input.dataset.mode || '');
  input.dataset.mode = parsed.mode;
  let letters = [...parsed.letters];
  const normalized = String(letter || '').toUpperCase();
  if (parsed.mode === 'single') {
    letters = [normalized];
  } else if (letters.includes(normalized)) {
    letters = letters.filter(item => item !== normalized);
  } else {
    letters.push(normalized);
  }
  input.value = formatAnswerSelection(letters, parsed.mode);
  updateAnswerChoicePanel(key);
}

function clearAnswerChoice(key) {
  const input = findAnswerInput(key);
  if (!input) return;
  input.value = '';
  updateAnswerChoicePanel(key);
}

function selectAllAnswerChoices(key) {
  const input = findAnswerInput(key);
  if (!input) return;
  const parsed = parseAnswerSelection(input.value, input.dataset.mode || '');
  const mode = parsed.mode === 'single' ? 'any_combo' : parsed.mode;
  input.dataset.mode = mode;
  input.value = formatAnswerSelection(['A', 'B', 'C', 'D'], mode);
  updateAnswerChoicePanel(key);
}

function unresolvedAnswerValue(value) {
  const text = String(value ?? '').trim();
  return !text || text === '#';
}

function answerChoiceControl(row, corrected, options = {}) {
  const key = String(row.candidate_key || '');
  const disabled = options.disabled || !key;
  const hint = row.answer_hint || {};
  const parsed = parseAnswerSelection(corrected);
  if (disabled) {
    return `<div class="answer-cell">
      <div class="answer-current" data-answer-display>${corrected ? esc(corrected) : '<span class="meta">空白</span>'}</div>
      <span class="meta">此列不可在答案關卡修改</span>
    </div>`;
  }
  const buttons = ['A', 'B', 'C', 'D'].map(letter =>
    `<button type="button" class="answer-choice ${parsed.letters.includes(letter) ? 'active' : ''}" data-answer-action="choice" data-key="${esc(key)}" data-letter="${letter}">${letter}</button>`
  ).join('');
  const modeButtons = [
    ['single', '單選'],
    ['any', '任一'],
    ['any_combo', '任一+複選'],
    ['all', '複選']
  ].map(([modeValue, label]) =>
    `<button type="button" class="answer-mode ${parsed.mode === modeValue ? 'active' : ''}" data-answer-action="mode" data-key="${esc(key)}" data-mode="${modeValue}">${label}</button>`
  ).join('');
  const hintHtml = hint.severity === 'warning' && hint.message ? `<div class="answer-warning">${esc(hint.message)}</div>` : '';
  return `<div class="answer-cell">
      <div class="answer-choice-panel" data-key="${esc(key)}" data-mode="${esc(parsed.mode)}">
        <div class="answer-current" data-answer-display>${corrected ? esc(corrected) : '<span class="meta">空白</span>'}</div>
        <input type="hidden" class="answer-edit" data-key="${esc(key)}" data-mode="${esc(parsed.mode)}" value="${editableText(corrected)}">
        <div class="answer-choice-row">${buttons}<button type="button" class="answer-clear" data-answer-action="clear" data-key="${esc(key)}">清空</button><button type="button" class="answer-select-all" data-answer-action="select_all" data-key="${esc(key)}">全選</button></div>
        <div class="answer-mode-row">${modeButtons}</div>
        ${hintHtml}
      </div>
    </div>`;
}

document.addEventListener('click', event => {
  const button = event.target.closest('[data-answer-action]');
  if (!button) return;
  const key = button.dataset.key || '';
  if (!key) return;
  event.preventDefault();
  if (button.dataset.answerAction === 'choice') {
    toggleAnswerChoice(key, button.dataset.letter || '');
  } else if (button.dataset.answerAction === 'mode') {
    setAnswerMode(key, button.dataset.mode || 'single');
  } else if (button.dataset.answerAction === 'clear') {
    clearAnswerChoice(key);
  } else if (button.dataset.answerAction === 'select_all') {
    selectAllAnswerChoices(key);
  }
});

function fixedOptionRows(options) {
  const rows = Array.isArray(options) ? options : [];
  const byKey = new Map();
  for (const option of rows) {
    const key = String(option?.key || '').trim().toUpperCase();
    if (!key) continue;
    byKey.set(key, {...option, key});
  }
  const fixedKeys = ['A', 'B', 'C', 'D'];
  const extraKeys = [...byKey.keys()]
    .filter(key => !fixedKeys.includes(key))
    .sort((a, b) => a.localeCompare(b));
  return [...fixedKeys, ...extraKeys].map(key => byKey.get(key) || {key, text: ''});
}

function renderDetail() {
  if (!current) {
    document.getElementById('detail').innerHTML = `<div class="panel"><h2>目前沒有符合條件的題目</h2><div class="body"><p class="meta">可以切換審核篩選，或開始產生下一批 candidate。</p></div></div>`;
    document.getElementById('pdf').src = '';
    lastPdfUrl = '';
    document.getElementById('pdfOpen').removeAttribute('href');
    document.getElementById('pdfPath').textContent = '';
    return;
  }
  const meta = current.metadata || {};
  const reviewState = current.review || {};
  const reviewBadge = reviewState.is_reset_unreviewed ? 'reset_review' : (reviewState.action || reviewState.status || 'unreviewed');
  const reviewLabel = reviewState.is_reset_unreviewed ? '退回未審' : statusLabel(reviewBadge);
  const hasCorrection = Boolean(reviewState.has_correction);
  updatePdfViewer();
  if (mode === 'answer') {
    renderAnswerDetail();
    return;
  }
  if (mode === 'group') {
    renderGroupDetail();
    return;
  }
  const isVisualMode = mode === 'visual';
  const images = (current.non_option_image_refs || []).filter(ref => ref.exists).map((ref, index) => assetFigure(ref, ref.raw_ref || `圖 ${index + 1}`)).join('');
  const inlineImages = (current.non_option_image_refs || []).filter(ref => ref.exists).map((ref, index) => assetFigure(ref, `圖 ${index + 1}`)).join('');
  const issues = (current.issues || []).map(issue => {
    const detail = compactJson(issue.issue_json);
    return `<div class="issue ${esc(issue.severity)}"><b>${esc(issue.severity)} / ${esc(issue.issue_code)}</b><br>${esc(issue.message)}${detail ? `<br><code>${esc(detail)}</code>` : ''}</div>`;
  }).join('') || '<div class="meta">目前沒有 QA flag。</div>';
  const optionRows = fixedOptionRows(current.options);
  const options = optionRows.map(opt =>
    `<div class="option"><b>(${esc(opt.key)})</b><div>${renderText(opt.text)}${opt.image && opt.image.exists ? `<div class="inline-images">${assetFigure(opt.image, `選項 ${opt.key}`)}</div>` : ''}</div></div>`
  ).join('');
  const editOptions = optionRows.map(opt =>
    `<div class="edit-option"><b>(${esc(opt.key)})</b><textarea class="edit-field edit-option-text" data-key="${esc(opt.key)}">${editableText(opt.text)}</textarea></div>`
  ).join('');
  const answerText = current.answer !== undefined && current.answer !== null && String(current.answer).trim() !== ''
    ? renderText(current.answer)
    : '<span class="meta">目前 candidate 未抓到答案，後續答案核對關卡會集中排查。</span>';
  const answerImages = (current.answer_image_refs || []).filter(ref => ref.exists).map((ref, index) => assetFigure(ref, `答案補圖 ${index + 1}`)).join('');
  const original = current.parser_original || null;
  const visualTableRefs = (current.image_refs || []).filter(ref => ref.exists);
  const tableNote = current.table_markup_suppressed ? `
    <div class="issue warning">
      <b>題幹表格已隱藏</b><br>
      結構化表格文字不作為審核主畫面內容，請以附圖或右側 PDF 為準。
      ${visualTableRefs.length ? '' : '<br><span class="meta">目前尚未補上表格截圖，建議先補圖再通過。</span>'}
    </div>` : '';
  const reset = reviewState.reset || {};
  const repairStatus = current.repair_status || {};
  const repairBadge = repairStatus.active ? '<span class="badge repaired">已修待複核</span>' : '';
  const repairNote = repairStatus.active ? `
    <div class="issue info">
      <b>已修待複核</b><br>
      <span class="meta">來源：${esc(repairStatus.reviewer || (repairStatus.sources || []).join(', ') || '系統修整')} ${esc(repairStatus.updated_at || '')}</span>
      ${repairStatus.notes ? `<div class="stem">${renderText(repairStatus.notes)}</div>` : ''}
    </div>` : '';
  const previousNotes = reset.previous_notes || '';
  const resetNotes = reset.reset_notes || reset.notes || '';
  const resetNote = reviewState.is_reset_unreviewed ? `
    <div class="issue warning">
      <b>退回未審</b><br>
      <span class="meta">上一個狀態：${esc(reset.previous_action || '未知')} ${esc(reset.previous_reviewed_at || '')}</span>
      ${previousNotes ? `<hr><b>原人工註記</b><div class="stem">${renderText(previousNotes)}</div>` : ''}
      ${resetNotes ? `<hr><b>本次修整說明</b><div class="stem">${renderText(resetNotes)}</div>` : ''}
    </div>` : '';
  const notePrefill = reviewState.is_reset_unreviewed
    ? [previousNotes ? `原人工註記：\n${previousNotes}` : '', resetNotes ? `本次修整：\n${resetNotes}` : ''].filter(Boolean).join('\n\n')
    : (reviewState.notes || '');
  const originalNote = original ? `
    <div class="manual-correction">
      <b>已使用人工校正版顯示</b>
      <p class="meta">parser 原始內容仍保留於 candidate，正式入庫時可比對人工校正版與 parser 原始版。</p>
    </div>` : '';
  const aiReview = current.ai_review || {};
  const aiBadge = aiReview.audit_status && aiReview.audit_status !== 'pass'
    ? `<span class="badge ai-warning">${esc(aiStatusLabel(aiReview.audit_status))}</span>`
    : '';
  const aiRawNote = aiReview.raw_audit_status && aiReview.raw_audit_status !== aiReview.audit_status
    ? `<span class="meta">raw: ${esc(aiReview.raw_audit_status)} → 顯示為 ${esc(aiReview.audit_status)}</span>`
    : '';
  const aiFindings = (aiReview.findings || []).map(finding => `
    <div class="issue ${esc(finding.severity || 'info')}">
      <b>${esc(finding.severity || 'info')} / ${esc(finding.code || '')}</b>
      <br>${esc(finding.message || '')}
      ${finding.evidence ? `<br><span class="meta">證據：${esc(finding.evidence)}</span>` : ''}
      ${finding.suggestion ? `<br><span class="meta">建議：${esc(finding.suggestion)}</span>` : ''}
    </div>
  `).join('');
  const aiSuggestedChanges = (aiReview.suggested_changes || []).map(change => `<li>${esc(change)}</li>`).join('');
  const aiCorrectionPanel = aiReview.suggested_correction ? `
    <div class="manual-correction">
      <b>AI 幫你標出的建議校正</b>
      ${aiSuggestedChanges ? `<ul>${aiSuggestedChanges}</ul>` : '<p class="meta">AI 提供了校正內容，請人工確認後套用。</p>'}
      <button class="action" onclick="applyAiSuggestedCorrection()">套用 AI 建議校正</button>
      <span class="meta">套用後會保留在需人工複核狀態，不會自動通過。</span>
    </div>` : '';
  const aiPanel = `
    <div class="panel"><h2>AI 格式稽核</h2><div class="body">
      <p class="meta">${aiReview.status === 'reviewed' ? `上次：${esc(aiReview.provider || '')} ${esc(aiReview.model || '')} / ${esc(aiReview.audit_status || '')} ${esc(aiReview.updated_at || '')}` : '尚未稽核'}</p>
      <p>${aiBadge} ${aiRawNote}</p>
      ${aiReview.summary ? `<p>${esc(aiReview.summary)}</p>` : '<p class="meta">AI 稽核只檢查字形、格式、選項、圖表與 parser 結構疑點，不會修改人工審核狀態。</p>'}
      ${aiCorrectionPanel}
      ${aiFindings || '<div class="meta">目前沒有 AI 稽核疑點。</div>'}
    </div></div>`;
  const statusText = current.question_quality_status || current.quality_status || 'pass';
  const statusBadge = statusText && statusText !== 'pass'
    ? `<span class="badge ${esc(statusText)}">${esc(statusLabel(statusText))}</span>`
    : '';
  const formal = current.formal || {};
  const formalBadge = formal.review_drift
    ? '<span class="badge formal_drift">正式庫待同步</span>'
    : formal.ready_for_formal
      ? '<span class="badge formal">已入正式庫</span>'
      : formal.physical_in_formal
        ? '<span class="badge formal_drift">正式表舊資料</span>'
      : '';
  const formalNote = formal.review_drift ? `
    <div class="issue warning">
      <b>正式題庫與審核層不同步</b><br>
      此題曾寫入正式表，但最新題目審核或答案核對已不再同時通過；目前不應視為可用題，需回審後重新同步正式庫。
      <br><span class="meta">formal question id: ${esc(formal.question_id || '')}</span>
    </div>` : formal.pending_promotion ? `
    <div class="issue info">
      <b>已達可用狀態，待同步正式表</b><br>
      最新題目審核與答案核對都已通過，系統已把此題視為正式可用；下一次 promotion 會寫入 <code>exam.questions</code> / <code>exam.answers</code>。
    </div>` : '';
  const visualProfile = current.visual_profile || {};
  const visualReviewStatus = visualProfile.visual_review_status || current.visual_review || '';
  const isManualVisual = Boolean(visualProfile.has_manual_asset);
  const visualBadge = visualReviewStatus === 'visual_asset_ok' || isManualVisual
    ? `<span class="badge visual">${esc(statusLabel('visual_asset_ok'))}</span>`
    : visualReviewStatus === 'visual_asset_problem'
      ? `<span class="badge needs_review">${esc(statusLabel('visual_asset_problem'))}</span>`
      : visualReviewStatus === 'no_visual_required'
        ? `<span class="badge reviewed">${esc(statusLabel('no_visual_required'))}</span>`
        : current.is_visual_question
          ? `<span class="badge visual">${visualProfile.has_visual_asset ? `圖片待核 ${esc(visualProfile.visual_asset_count || 0)}` : '疑似需圖'}</span>`
          : '';
  const visualReviewPanel = isVisualMode ? `
    <div class="issue info">
      <b>圖片/表格審核</b><br>
      <span class="meta">AI 與 parser 標籤只提示疑點；這裡只判斷圖片或表格資產是否正確、是否需要補圖。</span>
      <div class="toolbar">
        <button class="action primary-accept" onclick="markVisualAssetOk()">圖片正確</button>
        <button class="action block" onclick="markVisualAssetProblem()">圖片有問題/待補圖</button>
        <button class="action" onclick="markNoVisualRequired()">確認不需要圖片</button>
      </div>
    </div>` : '';
  const imageReviewAssetPanel = isVisualMode ? `
    <div class="panel"><h2>圖片</h2><div class="body">
      <div class="asset-grid">${images || '<span class="meta">未偵測到圖片引用。</span>'}</div>
      <div class="manual-asset-controls">
        <div id="manualAssetPasteZone" class="paste-zone" tabindex="0" onpaste="handleManualAssetPaste(event)">
          <b>貼上人工修正圖片</b>
          <span class="meta">先在 PDF 或截圖工具框選正確範圍，點這裡後按 ⌘V；或選擇圖片檔。若要取代 MinerU 錯圖，勾選「取代既有圖片」。</span>
          <img id="manualAssetPreview" class="paste-preview" alt="manual asset preview">
        </div>
        <input type="file" accept="image/png,image/jpeg,image/webp" onchange="handleManualAssetFile(this)">
        <div class="asset-placement-buttons" aria-label="補圖放置位置">
          <button type="button" class="active" data-manual-placement="stem" onclick="setManualAssetPlacement('stem')">題幹</button>
          <button type="button" data-manual-placement="option" data-manual-option="A" onclick="setManualAssetPlacement('option', 'A')">A</button>
          <button type="button" data-manual-placement="option" data-manual-option="B" onclick="setManualAssetPlacement('option', 'B')">B</button>
          <button type="button" data-manual-placement="option" data-manual-option="C" onclick="setManualAssetPlacement('option', 'C')">C</button>
          <button type="button" data-manual-placement="option" data-manual-option="D" onclick="setManualAssetPlacement('option', 'D')">D</button>
          <button type="button" data-manual-placement="table" onclick="setManualAssetPlacement('table')">表格</button>
          <button type="button" data-manual-placement="group" onclick="setManualAssetPlacement('group')">題組共用</button>
        </div>
        <input id="manualAssetPlacement" type="hidden" value="stem">
        <input id="manualAssetTargetOption" type="hidden" value="">
        <input id="manualAssetRole" type="hidden" value="manual_question_image">
        <label class="meta"><input type="checkbox" id="manualAssetReplace"> 取代既有圖片</label>
        <input id="manualAssetCaption" placeholder="圖片說明，例如：第 7 題表格人工截圖">
        <textarea id="manualAssetNotes" placeholder="補圖註記，例如：MinerU 原圖裁切不完整，人工截圖補正。">${esc(reviewState.notes || '')}</textarea>
        <div class="toolbar">
          <button class="action" onclick="saveManualAsset()">儲存補圖</button>
          <button class="action accept" onclick="saveManualAsset({acceptAfterSave: true})">儲存補圖並通過</button>
          <span id="manualAssetStatus" class="meta"></span>
        </div>
      </div>
    </div></div>` : '';
  const symbolToolsPanel = `
    <div class="panel question-correction-panel"><h2>符號模板</h2><div class="body">
      <div class="symbol-toolbar" aria-label="常用符號模板">
        <span class="tool-group-label">希臘</span>
        ${['α','β','γ','δ','ε','κ','λ','μ','θ','φ','ω','Δ','Σ'].map(symbol => `<button type="button" title="插入 ${symbol}" onclick="insertIntoCorrectionField('${symbol}')">${symbol}</button>`).join('')}
        <span class="tool-group-label">格式</span>
        <button type="button" title="下標：選中文字後套用" onclick="wrapCorrectionSelection('sub')">x<sub>2</sub></button>
        <button type="button" title="上標：選中文字後套用" onclick="wrapCorrectionSelection('sup')">x<sup>2</sup></button>
        <button type="button" title="攝氏溫度" onclick="insertIntoCorrectionField('°C')">°C</button>
        <button type="button" title="正負號" onclick="insertIntoCorrectionField('±')">±</button>
        <button type="button" title="乘號" onclick="insertIntoCorrectionField('×')">×</button>
        <button type="button" title="箭頭" onclick="insertIntoCorrectionField('→')">→</button>
        <button type="button" title="可逆反應" onclick="insertIntoCorrectionField('↔')">↔</button>
        <span class="tool-group-label">模板</span>
        <button type="button" title="CO2 下標" onclick="insertIntoCorrectionField('CO\\u003csub\\u003e2\\u003c/sub\\u003e')">CO<sub>2</sub></button>
        <button type="button" title="O2 下標" onclick="insertIntoCorrectionField('O\\u003csub\\u003e2\\u003c/sub\\u003e')">O<sub>2</sub></button>
        <button type="button" title="HCO3-" onclick="insertIntoCorrectionField('HCO\\u003csub\\u003e3\\u003c/sub\\u003e\\u003csup\\u003e-\\u003c/sup\\u003e')">HCO<sub>3</sub><sup>-</sup></button>
        <button type="button" title="PCO2" onclick="insertIntoCorrectionField('PCO\\u003csub\\u003e2\\u003c/sub\\u003e')">PCO<sub>2</sub></button>
        <button type="button" title="alpha 1" onclick="insertIntoCorrectionField('α\\u003csub\\u003e1\\u003c/sub\\u003e')">α<sub>1</sub></button>
        <button type="button" title="套用安全正規化到目前欄位或選取文字" onclick="normalizeActiveCorrectionField()">正規化</button>
      </div>
      <p class="meta">先點人工校正裡的題幹或選項欄位，再按符號；這裡只負責產生常用符號，不改變審核狀態。</p>
    </div></div>`;
  const questionQuickActions = isVisualMode ? '' : `
      <div class="quick-actions">
        <button class="action primary-accept" onclick="review('accept')">通過</button>
        <button class="action primary-block" onclick="review('block')">阻擋入庫</button>
        <button class="action" onclick="review('exclude')">非題目</button>
        <button class="action batch-accept" onclick="batchAcceptVisiblePass()">批次通過本頁 pass</button>
        <span class="meta">快速瀏覽可直接按；需要修正時用下方人工校正。</span>
      </div>`;
  const manualReviewPanel = isVisualMode ? '' : `
    <div class="panel"><h2>人工審核</h2><div class="body">
      <textarea id="notes" placeholder="審核註記或修正摘要">${esc(notePrefill)}</textarea>
      <div class="toolbar">
        <button class="action accept" onclick="review('accept')">通過</button>
        <button class="action" onclick="review('reviewed')">標記已看過</button>
        <button class="action" onclick="review('needs_review')">保留疑問</button>
        <button class="action block" onclick="review('block')">阻擋入庫</button>
        <button class="action" onclick="review('exclude')">非題目</button>
        <button class="action" onclick="review('comment')">只加註記</button>
      </div>
      <p id="saved" class="meta"></p>
    </div></div>`;
  document.getElementById('detail').innerHTML = `
    <div class="panel"><h2>題目</h2><div class="body">
      <div class="meta"><code>${esc(current.candidate_key)}</code></div>
      <p class="meta">Canonical: <code>${esc(current.canonical_question_key || current.candidate_key)}</code> / occurrence ${esc(current.question_number_occurrence || 1)}</p>
      <p class="meta">${esc(meta.normalized_category_name)} / ${esc(meta.normalized_subject_name)} / ${esc(meta.year)} 年第 ${esc(meta.exam_ordinal)} 次</p>
      <div class="question-number"><span>資料庫題號</span><b>第 ${esc(current.question_number)} 題</b><span class="meta">occurrence ${esc(current.question_number_occurrence || 1)}</span></div>
      <p>${statusBadge} ${formalBadge} ${visualBadge} ${aiBadge} <span class="badge ${esc(reviewBadge)}">${esc(reviewLabel)}</span> ${repairBadge} ${hasCorrection ? '<span class="badge reviewed">人工校正</span>' : ''} <span class="meta">${esc(reviewState.updated_at || '')}</span></p>
      ${formalNote}
      ${resetNote}
      ${repairNote}
      ${visualReviewPanel}
      ${tableNote}
      ${(current.answer_issues || []).length ? `<p class="meta">答案相關疑點已移到「答案核對」關卡：${esc((current.answer_issues || []).map(issue => issue.issue_code).join(', '))}</p>` : ''}
      ${questionQuickActions}
      ${originalNote}
      <div class="stem">${renderText(current.stem)}</div>
      ${inlineImages ? `<div class="inline-images">${inlineImages}</div>` : ''}
      <hr>${options}
      <p><b>答案：</b>${answerText} <span class="meta">此處顯示目前解析結果；正式判定會在下一個「答案核對」關卡統一檢查。</span></p>
      ${answerImages ? `<div class="inline-images answer-images">${answerImages}</div>` : ''}
      <p><b>題組：</b>${esc(current.group_ref ?? '無')}${current.group_sequence_no ? ` <span class="meta">序號 ${esc(current.group_sequence_no)}</span>` : ''}</p>
    </div></div>
    <div class="panel"><h2>疑點</h2><div class="body">${issues}</div></div>
    ${aiPanel}
    ${imageReviewAssetPanel}
    ${manualReviewPanel}
    <div class="panel question-correction-panel"><h2>人工校正</h2><div class="body">
      <div class="correction-tools">
        <div class="correction-preview">
          <div class="meta">即時顯示預覽（與審題畫面使用同一個 renderer）</div>
          <div id="correctionPreviewStem" class="preview-box empty"></div>
          <div id="correctionPreviewAnswer" class="preview-box empty"></div>
        </div>
      </div>
      <div class="edit-grid">
        <label class="meta">題幹<textarea id="editStem" class="edit-field">${editableText(current.stem)}</textarea></label>
        <div>
          <div class="meta">選項</div>
          ${editOptions}
        </div>
        <label class="meta">答案<input id="editAnswer" class="edit-field" value="${editableText(current.answer ?? '')}"><span class="meta">可補文字答案，例如 A、AC 或 A|C；圖片請用上方補圖按鈕放到題幹、選項或題組共用。</span></label>
        <label class="meta">題組<input id="editGroupRef" class="edit-field" value="${editableText(current.group_ref ?? '')}"></label>
      </div>
      <div class="toolbar">
        <button class="action" onclick="saveCorrection(false)">儲存人工校正</button>
        <button class="action accept" onclick="saveCorrection(true)">儲存並通過</button>
      </div>
      <p class="meta">人工校正會寫入 review event，不會覆蓋 parser 原始輸出；單純儲存校正會保留原本通過、阻擋或疑問狀態。</p>
    </div></div>
    ${symbolToolsPanel}
    <div class="panel"><h2>來源</h2><div class="body">
      <p class="meta">官方 PDF: <code>${esc((current.source_files || {}).official_pdf || '')}</code></p>
      <p class="meta">MinerU layout: <code>${esc((current.source_files || {}).mineru_layout_pdf || '')}</code></p>
      <p class="meta">MinerU origin: <code>${esc((current.source_files || {}).mineru_origin_pdf || '')}</code></p>
      <p class="meta">Markdown: <code>${esc((current.source_files || {}).question_markdown || '')}</code></p>
    </div></div>`;
  initCorrectionTools();
}

function renderAnswerDetail() {
  const meta = current.metadata || {};
  const rows = current.rows || [];
  const sheetReview = current.answer_review || {};
  const answerBadge = sheetReview.action || sheetReview.status || 'unreviewed';
  const roleLabel = current.answer_role_label || 'unknown';
  const roleText = roleLabel === 'MOD'
    ? '這批答案採用 MOD 修改答案，正式入庫時會優先於 ANS。'
    : roleLabel === 'ANS'
      ? '這批答案採用 ANS 原始答案。'
      : '這批答案來源尚未明確標成 ANS 或 MOD，請核對來源檔名。';
  const rowsHtml = rows.map((row, index) => {
    const answerReview = row.answer_review || {};
    const questionReview = row.question_review || {};
    const questionAction = questionReview.action || questionReview.status || 'unreviewed';
    const answerAction = answerReview.action || answerReview.status || 'unreviewed';
    const isPlaceholder = Boolean(row.is_placeholder);
    const eligible = !isPlaceholder && ['accept', 'unblock'].includes(questionAction);
    const corrected = answerReview.correction ?? row.answer ?? '';
    const answerDisplay = String(corrected || '').trim();
    const answerHint = row.answer_hint || {};
    const issueText = (row.answer_issues || []).map(issue => `${issue.severity}/${issue.issue_code}`).join(', ');
    const rowClass = [
      isPlaceholder ? 'placeholder-row' : '',
      !isPlaceholder && !eligible ? 'not-eligible-row' : '',
      answerAction === 'block' ? 'blocked-row' : '',
      answerHint.severity === 'warning' ? 'mod-warning-row' : ''
    ].filter(Boolean).join(' ');
    const optionFigures = !isPlaceholder
      ? (row.options || []).filter(opt => opt.image && opt.image.exists).map(opt =>
          `<figure><a href="${fileUrl(opt.image.path)}" target="_blank"><img src="${fileUrl(opt.image.path)}" alt="option ${esc(opt.key)}"></a><figcaption>${esc(opt.key)}</figcaption></figure>`
        ).join('')
      : '';
    const answerFigures = !isPlaceholder
      ? (row.answer_image_refs || []).filter(ref => ref.exists).map((ref, figIndex) => assetFigure(ref, `答案補圖 ${figIndex + 1}`, {allowRemove: false})).join('')
      : '';
    const rowHtml = `<tr class="${rowClass}">
      <td>第 ${esc(row.question_number)} 題${row.question_number_occurrence && row.question_number_occurrence !== 1 ? ` <span class="meta">occ ${esc(row.question_number_occurrence)}</span>` : ''}</td>
      <td>${answerChoiceControl(row, answerDisplay, {disabled: isPlaceholder || !eligible})}${answerFigures ? `<div class="inline-images answer-images">${answerFigures}</div>` : ''}</td>
      <td><span class="badge ${esc(questionAction)}">${esc(isPlaceholder ? '未通過' : questionAction)}</span>${eligible ? '' : `<div class="meta">${esc(row.placeholder_reason || '題目未審核通過，答案不可入庫。')}</div>`}</td>
      <td>${isPlaceholder ? '<span class="meta">空白保留排序</span>' : `<span class="badge ${esc(answerAction)}">${esc(answerAction)}</span>`}</td>
      <td class="stem-cell">${isPlaceholder ? '<span class="meta">題目尚未通過審核</span>' : `${renderText(String(row.stem || '').slice(0, 180))}${String(row.stem || '').length > 180 ? '...' : ''}${optionFigures ? `<div class="inline-images option-strip">${optionFigures}</div>` : ''}`}</td>
      <td class="meta">${esc(issueText || '')}</td>
    </tr>`;
    return (index + 1) % 5 === 0 && index + 1 < rows.length
      ? `${rowHtml}<tr class="answer-group-spacer" aria-hidden="true"><td colspan="6"></td></tr>`
      : rowHtml;
  }).join('');
  const ineligibleCount = rows.filter(row => {
    const action = (row.question_review || {}).action || (row.question_review || {}).status || 'unreviewed';
    return !['accept', 'unblock'].includes(action);
  }).length;
  const answerActionsHtml = `
      <div class="quick-actions">
        <button class="action primary-accept answer-review-action" onclick="answerSheetReviewAction('accept')">整份通過</button>
        <button class="action primary-block answer-review-action" onclick="answerSheetReviewAction('block')">整份阻擋</button>
        <button class="action answer-review-action" onclick="answerSheetReviewAction('correct')">儲存答案修正</button>
        <button class="action answer-review-action" onclick="answerSheetReviewAction('needs_review')">保留疑問</button>
        <span class="meta">這一關只判定答案表；前一關未通過的題目不能因答案核對而入庫。</span>
      </div>`;
  document.getElementById('detail').innerHTML = `
    <div class="panel"><h2>答案核對</h2><div class="body">
      <div class="meta"><code>${esc(current.sheet_key || current.candidate_key)}</code></div>
      <p class="meta">${esc(meta.normalized_category_name)} / ${esc(meta.normalized_subject_name)} / ${esc(meta.year)} 年第 ${esc(meta.exam_ordinal)} 次</p>
      <p><span class="badge ${esc(roleLabel)}">${esc(roleLabel)}</span> ${esc(roleText)}</p>
      <p><span class="badge ${esc(answerBadge)}">答案表 ${esc(answerBadge)}</span>${ineligibleCount ? ` <span class="badge needs_review">${esc(ineligibleCount)} 題題目未通過</span>` : ''}</p>
      <div class="answer-sheet-summary">
        <div><span class="meta">總題數</span><b>${esc(current.question_count || rows.length)}</b></div>
        <div><span class="meta">可核題數</span><b>${esc(current.reviewable_question_count || rows.length)}</b></div>
        <div><span class="meta">缺題保留</span><b>${esc(current.placeholder_count || 0)}</b></div>
        <div><span class="meta">已核答案</span><b>${esc(current.reviewed_count || 0)}</b></div>
        <div><span class="meta">答案通過</span><b>${esc(current.accepted_count || 0)}</b></div>
        <div><span class="meta">人工修正</span><b>${esc(current.corrected_count || 0)}</b></div>
        <div><span class="meta">MOD 需確認</span><b>${esc(current.answer_attention_count || 0)}</b></div>
      </div>
      ${answerActionsHtml}
      <div class="answer-format">
        <b>多答案點選規則</b>
        <p class="meta">ANS 單選答案若無其他疑點可沿用 parser 結果。MOD 多答案請看右側答案 PDF 後點選 A-D；「任一」會存成 <code>A|C</code>，「任一+複選」會展開所有可能組合，例如 ABC 會存成 <code>A|B|C|AB|AC|BC|ABC</code>，「複選」會存成 <code>A+C</code>。若 MOD 仍是 <code>#</code> 或空白，整份答案不可通過。</p>
      </div>
    </div></div>
    <div class="panel"><h2>整份題號與答案對應</h2><div class="body">
      <table class="answer-table">
        <thead><tr><th>題號</th><th>解析答案 / 人工答案</th><th>審題狀態</th><th>答案狀態</th><th>題幹摘要</th><th>疑點</th></tr></thead>
        <tbody>${rowsHtml || '<tr><td colspan="6" class="meta">這份答案表目前沒有可核對的題目。</td></tr>'}</tbody>
      </table>
    </div></div>
    <div class="panel"><h2>答案審核註記</h2><div class="body">
      <div class="quick-actions">
        <button class="action primary-accept answer-review-action" onclick="answerSheetReviewAction('accept')">整份通過</button>
        <button class="action primary-block answer-review-action" onclick="answerSheetReviewAction('block')">整份阻擋</button>
        <button class="action answer-review-action" onclick="answerSheetReviewAction('correct')">儲存答案修正</button>
        <button class="action answer-review-action" onclick="answerSheetReviewAction('needs_review')">保留疑問</button>
        <span class="meta">看完整份答案表後，可直接在這裡寫註記並送出。</span>
      </div>
      <textarea id="answerNotes" placeholder="答案核對註記。可寫下可疑題號、MOD 判定或人工修正理由。"></textarea>
      <p id="saved" class="meta"></p>
    </div></div>
    <div class="panel"><h2>來源</h2><div class="body">
      <p class="meta">答案 PDF: <code>${esc((current.source_files || {}).official_pdf || '')}</code></p>
      <p class="meta">答案 MinerU layout: <code>${esc((current.source_files || {}).mineru_layout_pdf || '')}</code></p>
      <p class="meta">答案 Markdown: <code>${esc((current.source_files || {}).answer_markdown || '')}</code></p>
    </div></div>`;
}

function renderGroupDetail() {
  const meta = current.metadata || {};
  const rows = uniqueRowsByKey(current.rows || []);
  current.rows = rows;
  const reasonCounts = Object.entries(current.reason_counts || {}).map(([key, value]) =>
    `<span class="badge needs_review">${esc(key)} ${esc(value)}</span>`
  ).join(' ');
  const rowsHtml = rows.map(row => {
    const review = row.review || {};
    const reviewBadge = review.is_reset_unreviewed ? 'reset_review' : (review.action || review.status || 'unreviewed');
    const groupReviewBadge = row.group_review?.action === 'confirm_group'
      ? ' <span class="badge linked">確認題組</span>'
      : row.group_review?.action === 'confirm_not_group'
        ? ' <span class="badge not_group">確認非題組</span>'
        : '';
    const aiStatus = row.ai_review?.audit_status || '';
    const visualBadge = row.is_visual_question ? ` <span class="badge visual">${row.visual_profile?.has_visual_asset ? '圖片題' : '疑似需圖'}</span>` : '';
    const reasons = (row.reasons || []).map(reason => `<span class="badge needs_review">${esc(reason)}</span>`).join(' ');
    const rowGroupRef = row.group_ref || row.inferred_group_ref || '無';
    const rowGroupKind = row.group_ref ? 'group_ref' : row.inferred_group_kind === 'explicit_count' ? 'explicit inferred' : row.inferred_group_ref ? 'inferred' : 'group_ref';
    return `<tr>
      <td><b>第 ${esc(row.question_number)} 題</b>${row.question_number_occurrence && row.question_number_occurrence !== 1 ? ` <span class="meta">occ ${esc(row.question_number_occurrence)}</span>` : ''}</td>
      <td><code>${esc(row.candidate_key)}</code><div class="meta">${esc(rowGroupKind)}: ${esc(rowGroupRef)}</div></td>
      <td><span class="badge ${esc(reviewBadge)}">${esc(reviewBadge)}</span>${groupReviewBadge}${visualBadge}${aiStatus ? ` <span class="badge ${aiStatus === 'pass' ? 'ai' : 'ai-warning'}">AI ${esc(aiStatus)}</span>` : ''}</td>
      <td>${reasons || '<span class="meta">題組線索</span>'}</td>
      <td class="stem-cell">${renderText(String(row.stem || '').slice(0, 260))}${String(row.stem || '').length > 260 ? '...' : ''}</td>
      <td><button class="action" data-key="${esc(row.candidate_key)}" onclick="openQuestionCandidate(this.dataset.key)">回審此題</button></td>
    </tr>`;
  }).join('');
  const unboundWarning = current.group_ref || current.inferred_group_ref ? '' : `
    <div class="group-warning">
      這一組是「未綁疑似題組」。若右側 PDF 與題幹確認它們共享共同情境，請在本頁使用「確認為題組」或「人工範圍建立題組」。
    </div>`;
  document.getElementById('detail').innerHTML = `
    <div class="panel"><h2>題組審核</h2><div class="body">
      <div class="meta"><code>${esc(current.group_sheet_key || current.candidate_key)}</code></div>
      <p class="meta">${esc(meta.normalized_category_name || meta.group_name)} / ${esc(meta.normalized_subject_name)} / ${esc(meta.year)} 年第 ${esc(meta.exam_ordinal)} 次</p>
      <p><span class="badge ${esc(current.gate_status || 'group')}">${esc(current.group_label || '題組候選')}</span> ${reasonCounts}</p>
      ${unboundWarning}
      <div class="group-summary">
        <div><span class="meta">候選題數</span><b>${esc(current.question_count || rows.length)}</b></div>
        <div><span class="meta">題目已通過</span><b>${esc(current.accepted_count || 0)}</b></div>
        <div><span class="meta">阻擋/非題</span><b>${esc(current.blocked_count || 0)}</b></div>
        <div><span class="meta">保留疑問</span><b>${esc(current.needs_review_count || 0)}</b></div>
      </div>
      <div class="toolbar">
        <button class="action primary-accept" onclick="confirmCurrentSheetGroup()">確認為題組</button>
        <button class="action" onclick="confirmCurrentSheetNotGroup()">確認非題組</button>
        <button class="action danger-small" onclick="resetCurrentSheetGroupReview()">退回題組未審</button>
        <select id="groupConfirmType" class="edit-field" style="max-width:170px">
          <option value="shared_stem">共同題幹</option>
          <option value="chained_context">承上題脈絡</option>
          <option value="manual_range">人工範圍</option>
          <option value="unknown">待定</option>
        </select>
        <input id="groupConfirmRef" class="edit-field" style="max-width:140px" value="${esc(current.group_ref || current.inferred_group_ref || '')}" placeholder="q011-q012">
        <span id="groupSaved" class="meta">確認題組只寫題組層；確認非題組會從題組待審清單排除。</span>
      </div>
      <div class="manual-correction">
        <b>人工範圍建立題組</b>
        <p class="meta">如果系統沒抓到題組，先用上方篩選到同一考別、科目、年份與考次，再輸入範圍，例如 <code>7-9</code> 或 <code>11-12</code>。系統會以目前題組候選中的第一題作為同份試題定位點，尋找連續題號並寫入題組層。</p>
        <div class="toolbar">
          <input id="manualGroupRange" class="edit-field" style="max-width:120px" placeholder="7-9">
          <select id="manualGroupType" class="edit-field" style="max-width:170px">
            <option value="shared_stem">共同題幹</option>
            <option value="chained_context">承上題脈絡</option>
            <option value="manual_range">人工範圍</option>
            <option value="unknown">待定</option>
          </select>
          <button class="action" onclick="confirmManualGroupFromCurrent()">建立題組</button>
          <button class="action primary-accept" onclick="adjustCurrentGroupToManualRange()">調整目前題組為此範圍</button>
          <span id="manualGroupSaved" class="meta"></span>
        </div>
      </div>
      <p class="meta">題組層目前只做結構檢查與導流；真正是否可入庫仍取決於每題審題通過、題組綁定正確，以及後續答案核對通過。</p>
    </div></div>
    <div class="panel"><h2>題組題目</h2><div class="body">
      <table class="group-table">
        <thead><tr><th>題號</th><th>候選鍵 / 題組</th><th>狀態</th><th>題組線索</th><th>題幹摘要</th><th>操作</th></tr></thead>
        <tbody>${rowsHtml || '<tr><td colspan="6" class="meta">目前沒有題組候選。</td></tr>'}</tbody>
      </table>
    </div></div>`;
}

function uniqueRowsByKey(rows) {
  const seen = new Set();
  const unique = [];
  for (const row of rows || []) {
    const key = row?.candidate_key || '';
    if (!key || seen.has(key)) continue;
    seen.add(key);
    unique.push(row);
  }
  return unique;
}

function uniqueCandidateKeys(rows) {
  return uniqueRowsByKey(rows).map(row => row.candidate_key).filter(Boolean);
}

function collectCorrection() {
  const options = Array.from(document.querySelectorAll('.edit-option-text')).map(textarea => ({
    key: textarea.dataset.key,
    text: textarea.value,
    image: (current?.options || []).find(option => String(option.key || '').toUpperCase() === String(textarea.dataset.key || '').toUpperCase())?.image || undefined
  }));
  return {
    stem: document.getElementById('editStem')?.value ?? current?.stem ?? '',
    options,
    answer: document.getElementById('editAnswer')?.value ?? current?.answer ?? '',
    group_ref: document.getElementById('editGroupRef')?.value ?? current?.group_ref ?? '',
    image_refs: current?.image_refs || [],
    stem_image: current?.stem_image || null,
    answer_image_refs: current?.answer_image_refs || [],
    visual_review: current?.visual_review || ''
  };
}

function mergeCorrection(base, patch) {
  const merged = {...(base || {})};
  for (const [key, value] of Object.entries(patch || {})) {
    if (key !== 'options') merged[key] = value;
  }
  if (Array.isArray(patch?.options)) {
    const rows = Array.isArray(merged.options) && merged.options.length
      ? merged.options
      : (current?.options || []).map(option => ({...option}));
    const byKey = new Map(rows.map(option => [String(option.key || '').toUpperCase(), {...option}]));
    for (const option of patch.options) {
      const key = String(option.key || '').toUpperCase();
      if (!key) continue;
      byKey.set(key, {...(byKey.get(key) || {key}), ...option, key});
    }
    merged.options = Array.from(byKey.values()).sort((a, b) => String(a.key).localeCompare(String(b.key)));
  }
  return merged;
}

function applyCorrectionToCurrent(correction) {
  if (!current || !correction) return;
  if (!current.parser_original) {
    current.parser_original = {
      stem: current.stem,
      options: Array.isArray(current.options) ? current.options.map(option => ({...option})) : [],
      answer: current.answer,
      group_ref: current.group_ref,
      image_refs: current.image_refs,
      stem_image: current.stem_image,
      answer_image_refs: current.answer_image_refs,
      visual_review: current.visual_review
    };
  }
  for (const field of ['stem', 'answer', 'group_ref', 'image_refs', 'stem_image', 'answer_image_refs', 'visual_review']) {
    if (Object.prototype.hasOwnProperty.call(correction, field)) {
      current[field] = correction[field];
    }
  }
  if (Array.isArray(correction.options)) {
    current.options = correction.options.map(option => ({...option}));
  }
  recomputeCurrentVisualState();
}

async function applyAiSuggestedCorrection() {
  if (!current || mode !== 'question') return;
  const suggestion = current.ai_review?.suggested_correction;
  if (!suggestion || Object.keys(suggestion).length === 0) return;
  const existingCorrection = current.review?.correction || collectCorrection();
  const correction = mergeCorrection(existingCorrection, suggestion);
  const changes = current.ai_review?.suggested_changes || [];
  const noteBox = document.getElementById('notes');
  const aiNote = [
    'AI 建議校正已套用，需人工複核後才能通過。',
    changes.length ? `AI 建議變更：${changes.join('；')}` : ''
  ].filter(Boolean).join('\n');
  if (noteBox && !noteBox.value.includes('AI 建議校正已套用')) {
    noteBox.value = [noteBox.value.trim(), aiNote].filter(Boolean).join('\n\n');
  }
  const currentAction = current.review?.action || current.review?.status || '';
  const action = ['block', 'exclude'].includes(currentAction) ? currentAction : 'needs_review';
  await review(action, correction, {stayOnCurrent: true});
}

async function saveCorrection(acceptAfterSave) {
  if (!current) return;
  await review(acceptAfterSave ? 'accept' : 'correct', collectCorrection(), {stayOnCurrent: !acceptAfterSave});
}

async function batchAcceptVisiblePass() {
  if (mode !== 'question') return;
  const eligible = filtered.filter(item => {
    const review = item.review || {};
    const action = review.action || review.status || 'unreviewed';
    const quality = item.question_quality_status || item.quality_status;
    const aiStatus = item.ai_review?.audit_status || '';
    return quality === 'pass' && (!aiStatus || aiStatus === 'pass') && !['block', 'exclude', 'needs_review', 'accept', 'unblock'].includes(action);
  });
  const status = document.getElementById('batchStatus');
  if (!eligible.length) {
    status.textContent = '本頁沒有可批次通過的 pass 題目。';
    return;
  }
  const ok = window.confirm(`將目前畫面 ${eligible.length} 題 parser pass 且未被 block / needs_review 的題目批次標記為通過？`);
  if (!ok) return;
  status.textContent = '批次寫入中...';
  const res = await fetch('/api/review-batch-accept', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      candidate_keys: eligible.map(item => item.candidate_key),
      reviewer,
      notes: '批次通過：人工快速瀏覽目前畫面，parser pass 且未被標記 block / needs_review。'
    })
  });
  const data = await res.json();
  if (!data.ok) {
    status.textContent = `批次通過失敗：${data.error}`;
    return;
  }
  clearCandidateCache();
  status.textContent = `批次通過 ${data.saved_count} 題，略過 ${data.skipped_count} 題。`;
  await fetchCandidates(null, null, current?.candidate_key || null);
}

async function confirmCurrentSheetNotGroup() {
  if (!current || mode !== 'group') return;
  const keys = uniqueCandidateKeys(current.rows || []);
  const status = document.getElementById('groupSaved');
  if (!keys.length) {
    if (status) status.textContent = '這組沒有可寫入的題目。';
    return;
  }
  const ok = window.confirm(`確認這 ${keys.length} 題不是題組，並從題組待審清單排除？這不會改變題目本身的通過/阻擋狀態。`);
  if (!ok) return;
  if (status) status.textContent = '寫入確認非題組中...';
  const res = await fetch('/api/group-confirm-not-group', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      candidate_keys: keys,
      group_sheet_key: current.group_sheet_key || current.candidate_key || '',
      reviewer,
      notes: '題組審核：人工確認此候選不是題組。'
    })
  });
  const data = await res.json();
  if (!data.ok) {
    if (status) status.textContent = `確認非題組失敗：${data.error}`;
    return;
  }
  clearCandidateCache();
  if (status) status.textContent = `已確認非題組 ${data.saved_count} 題，略過 ${data.skipped_count} 題。`;
  await advanceAfterGroupReview(current.candidate_key, 'confirmed_not_group', data.events || []);
}

function currentGroupMatchesFilter(status) {
  const groupStatus = filterValue('groupReviewStatus');
  if (!groupStatus) return true;
  if (groupStatus === 'unreviewed') return status === 'unreviewed';
  if (groupStatus === 'reviewed') return status && status !== 'unreviewed';
  return groupStatus === status;
}

function applyGroupReviewEventsToCurrent(status, events) {
  if (!current || mode !== 'group') return;
  const byKey = new Map((events || []).filter(event => event.candidate_key).map(event => [event.candidate_key, event]));
  current.rows = uniqueRowsByKey(current.rows || []).map(row => {
    const event = byKey.get(row.candidate_key);
    if (!event) return row;
    return {
      ...row,
      group_review: {
        ...(row.group_review || {}),
        ...event,
        action: event.action || (status === 'confirmed_group' ? 'confirm_group' : status === 'confirmed_not_group' ? 'confirm_not_group' : 'reset_group_review')
      }
    };
  });
  current.group_review_status = status;
  if (status === 'confirmed_group') {
    current.gate_status = 'linked';
    current.group_ref = document.getElementById('groupConfirmRef')?.value || current.group_ref || current.inferred_group_ref || '';
    current.group_label = current.group_ref || current.group_label;
  }
  const index = filtered.findIndex(item => item.candidate_key === current.candidate_key);
  if (index >= 0) filtered[index] = current;
}

async function advanceAfterGroupReview(reviewedKey, confirmedStatus, events = []) {
  if (currentGroupMatchesFilter(confirmedStatus)) {
    const reviewedIndex = filtered.findIndex(item => item.candidate_key === reviewedKey);
    applyGroupReviewEventsToCurrent(confirmedStatus, events);
    const next = filtered.find((item, index) => index > reviewedIndex && item.candidate_key !== reviewedKey)
      || filtered.find((item, index) => index < reviewedIndex && item.candidate_key !== reviewedKey)
      || current;
    current = next;
    updateCountLabels();
    renderList();
    renderDetail();
    savePreferencesSoon();
    return;
  }
  await fetchCandidates(null, null, reviewedKey);
}

async function confirmCurrentSheetGroup() {
  if (!current || mode !== 'group') return;
  const keys = uniqueCandidateKeys(current.rows || []);
  const status = document.getElementById('groupSaved');
  const groupRef = document.getElementById('groupConfirmRef')?.value || current.group_ref || current.inferred_group_ref || '';
  const groupType = document.getElementById('groupConfirmType')?.value || 'shared_stem';
  if (!keys.length) {
    if (status) status.textContent = '這組沒有可寫入的題目。';
    return;
  }
  if (!groupRef) {
    if (status) status.textContent = '請先填入題組標籤，例如 q011-q012。';
    return;
  }
  if (status) status.textContent = '寫入確認題組中...';
  const res = await fetch('/api/group-confirm-group', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      candidate_keys: keys,
      group_sheet_key: current.group_sheet_key || current.candidate_key || '',
      group_ref: groupRef,
      group_type: groupType,
      reviewer,
      notes: `題組審核：人工確認為 ${groupRef}。`
    })
  });
  const data = await res.json();
  if (!data.ok) {
    if (status) status.textContent = `確認題組失敗：${data.error}`;
    return;
  }
  clearCandidateCache();
  if (status) status.textContent = `已確認題組 ${data.saved_count} 題；${data.group?.group_key ? 'SQL group 已建立' : '已寫入題組事件'}。`;
  await advanceAfterGroupReview(current.candidate_key, 'confirmed_group', data.events || []);
}

async function resetCurrentSheetGroupReview() {
  if (!current || mode !== 'group') return;
  const keys = uniqueCandidateKeys(current.rows || []);
  const status = document.getElementById('groupSaved');
  if (!keys.length) {
    if (status) status.textContent = '這組沒有可退回的題目。';
    return;
  }
  const ok = window.confirm(`將目前 ${keys.length} 題的題組審核退回未審？審題與答案核對狀態不會改變。`);
  if (!ok) return;
  if (status) status.textContent = '題組退回未審中...';
  const res = await fetch('/api/group-reset-review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      candidate_keys: keys,
      group_sheet_key: current.group_sheet_key || current.candidate_key || '',
      reviewer,
      notes: '題組審核：人工退回題組層未審，等待重新判斷。'
    })
  });
  const data = await res.json();
  if (!data.ok) {
    if (status) status.textContent = `退回題組未審失敗：${data.error}`;
    return;
  }
  clearCandidateCache();
  if (status) status.textContent = `已退回題組未審 ${data.saved_count} 題，略過 ${data.skipped_count} 題。`;
  await advanceAfterGroupReview(current.candidate_key, 'unreviewed', data.events || []);
}

async function confirmManualGroupFromCurrent() {
  if (!current || mode !== 'group') return;
  const range = document.getElementById('manualGroupRange')?.value || '';
  const groupType = document.getElementById('manualGroupType')?.value || 'shared_stem';
  const status = document.getElementById('manualGroupSaved');
  const seedKey = (current.rows || []).map(row => row.candidate_key).filter(Boolean)[0] || '';
  if (!range.trim()) {
    if (status) status.textContent = '請輸入題組範圍，例如 7-9。';
    return;
  }
  if (!seedKey) {
    if (status) status.textContent = '目前題組頁沒有可定位同份試題的候選題，請先縮小篩選到有候選題的考次。';
    return;
  }
  if (status) status.textContent = '尋找同份試題題號並寫入題組...';
  const match = range.match(/(\d{1,3})\s*(?:-|－|~|～|至|到)\s*(\d{1,3})/);
  const groupRef = match ? `q${String(Number(match[1])).padStart(3, '0')}-q${String(Number(match[2])).padStart(3, '0')}` : '';
  const res = await fetch('/api/group-confirm-group', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      seed_candidate_key: seedKey,
      range,
      group_ref: groupRef,
      group_type: groupType,
      reviewer,
      notes: `題組審核：人工從題組審核頁手動建立 ${groupRef || range}。`
    })
  });
  const data = await res.json();
  if (!data.ok) {
    if (status) status.textContent = `建立題組失敗：${data.error}`;
    return;
  }
  if (status) status.textContent = `已建立題組 ${data.saved_count} 題；${data.group?.group_key ? 'SQL group 已建立' : '已寫入題組事件'}。`;
  await fetchCandidates(current.candidate_key, null, null);
}

async function adjustCurrentGroupToManualRange() {
  if (!current || mode !== 'group') return;
  const range = document.getElementById('manualGroupRange')?.value || '';
  const status = document.getElementById('manualGroupSaved');
  if (!range.trim()) {
    if (status) status.textContent = '請先輸入要調整成的範圍，例如 7-9。';
    return;
  }
  const existingKeys = uniqueCandidateKeys(current.rows || []);
  if (!existingKeys.length) {
    if (status) status.textContent = '目前題組沒有可調整的題目。';
    return;
  }
  const ok = window.confirm(`將目前題組改成 ${range}？系統會先退回目前這組的題組審核，再用指定範圍重新確認。`);
  if (!ok) return;
  if (status) status.textContent = '調整題組範圍中...';
  const resetRes = await fetch('/api/group-reset-review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      candidate_keys: existingKeys,
      group_sheet_key: current.group_sheet_key || current.candidate_key || '',
      reviewer,
      notes: `題組審核：人工調整題組範圍前，退回原候選 ${current.group_label || current.group_sheet_key || ''}。`
    })
  });
  const resetData = await resetRes.json();
  if (!resetData.ok) {
    if (status) status.textContent = `調整失敗：退回原題組失敗：${resetData.error}`;
    return;
  }
  await confirmManualGroupFromCurrent();
}

async function answerSheetReviewAction(action, aiRequested = false) {
  if (!current) return;
  const reviewedKey = current.candidate_key;
  const currentIndex = filtered.findIndex(item => item.candidate_key === reviewedKey);
  const notes = document.getElementById('answerNotes')?.value || '';
  const savedStatus = document.getElementById('saved');
  const actionButtons = Array.from(document.querySelectorAll('.answer-review-action'));
  actionButtons.forEach(button => { button.disabled = true; });
  if (savedStatus) savedStatus.textContent = '答案審核寫入中...';
  const answerInputs = new Map(Array.from(document.querySelectorAll('.answer-edit')).filter(input => input.dataset.key).map(input => [input.dataset.key, input.value]));
  const entries = (current.rows || []).filter(row => row.candidate_key && !row.is_placeholder).map(row => ({
    candidate_key: row.candidate_key,
    answer_source_registry_key: current.sheet_key || '',
    answer: row.answer,
    reviewed_answer: row.answer_payload || {answer: row.answer},
    corrected_answer: answerInputs.has(row.candidate_key) ? answerInputs.get(row.candidate_key) : (row.answer_review?.correction ?? row.answer ?? ''),
    needs_manual_answer_review: Boolean(row.answer_hint?.needs_manual_choice)
  }));
  const unresolvedRows = action === 'accept'
    ? (current.rows || []).filter(row => row.candidate_key && !row.is_placeholder && row.answer_hint?.needs_manual_choice).filter(row => {
        const value = answerInputs.has(row.candidate_key) ? answerInputs.get(row.candidate_key) : (row.answer_review?.correction ?? row.answer ?? '');
        return unresolvedAnswerValue(value);
      })
    : [];
  if (unresolvedRows.length) {
    const numbers = unresolvedRows.slice(0, 12).map(row => `第 ${row.question_number} 題`).join('、');
    if (savedStatus) savedStatus.textContent = `MOD 多答案仍有 # 或空白：${numbers}${unresolvedRows.length > 12 ? '...' : ''}。請先看答案 PDF 點選答案。`;
    actionButtons.forEach(button => { button.disabled = false; });
    return;
  }
  const res = await fetch('/api/answer-review-batch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      sheet_key: current.sheet_key || current.candidate_key,
      sheet_action: action,
      action,
      answer_role_label: current.answer_role_label || '',
      notes,
      reviewer: 'local',
      ai_requested: aiRequested,
      entries
    })
  });
  const data = await res.json();
  if (data.ok) {
    clearCandidateCache();
    const storageText = storageLabel((data.events || [])[0]?.storage);
    const reviewedAction = (data.events || [])[0]?.action || action;
    const answerEventsByKey = new Map((data.events || []).filter(event => event.candidate_key).map(event => [event.candidate_key, event]));
    if (current) {
      current.rows = (current.rows || []).map(row => {
        const event = answerEventsByKey.get(row.candidate_key);
        if (!event) return row;
        return {
          ...row,
          answer_review: {
            status: 'reviewed',
            action: event.action || reviewedAction,
            notes: event.notes || notes,
            updated_at: event.created_at || '',
            event_count: (row.answer_review?.event_count || 0) + 1,
            correction: event.corrected_answer ?? row.answer_review?.correction ?? row.answer ?? ''
          }
        };
      });
      current.answer_review = {
        status: 'reviewed',
        action: reviewedAction,
        notes,
        updated_at: (data.events || [])[0]?.created_at || '',
        event_count: (current.answer_review?.event_count || 0) + (data.saved_count || entries.length || 1)
      };
      current.reviewed_count = current.reviewable_question_count || current.question_count || current.reviewed_count || 0;
      current.accepted_count = ['accept', 'unblock'].includes(reviewedAction) ? current.reviewed_count : current.accepted_count;
    }
    const answerStatus = filterValue('answerReviewStatus');
    const shouldLeaveCurrent =
      (answerStatus === 'unreviewed' && ['accept', 'unblock', 'block', 'needs_review', 'reviewed', 'comment'].includes(reviewedAction))
      || (answerStatus === 'not_accept' && ['accept', 'unblock'].includes(reviewedAction))
      || (answerStatus === 'accept' && !['accept', 'unblock'].includes(reviewedAction))
      || (answerStatus === 'block' && reviewedAction !== 'block')
      || (answerStatus === 'needs_review' && reviewedAction !== 'needs_review');
    if (shouldLeaveCurrent && currentIndex >= 0) {
      filtered.splice(currentIndex, 1);
      candidates = filtered;
      chooseCurrent(null, Math.min(currentIndex, Math.max(filtered.length - 1, 0)), reviewedKey);
    } else {
      renderList();
      renderDetail();
    }
    const saved = document.getElementById('saved');
    if (saved) saved.textContent = `已寫入 ${data.saved_count || entries.length} 題${storageText ? `：${storageText}` : ''}`;
  } else {
    if (savedStatus) savedStatus.textContent = `寫入失敗：${data.error}${data.ineligible ? '；有題目未審核通過' : ''}${data.unresolved_mod_entries ? '；有 MOD # 或空白未處理' : ''}`;
    actionButtons.forEach(button => { button.disabled = false; });
  }
}

async function answerReviewAction(action) {
  if (!current) return;
  const reviewedKey = current.candidate_key;
  const currentIndex = filtered.findIndex(item => item.candidate_key === reviewedKey);
  const notes = document.getElementById('answerNotes')?.value || '';
  const correctedAnswer = document.getElementById('editReviewedAnswer')?.value ?? current.answer ?? '';
  const body = {
    candidate_key: current.candidate_key,
    answer_source_registry_key: current.answer_source_registry_key || '',
    action,
    notes,
    reviewer: 'local',
    reviewed_answer: current.answer_payload || {answer: current.answer},
    corrected_answer: correctedAnswer
  };
  const res = await fetch('/api/answer-review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const data = await res.json();
  if (data.ok) {
    clearCandidateCache();
    current.answer_review = {
      status: 'reviewed',
      action: data.event.action,
      notes,
      updated_at: data.event.created_at,
      correction: data.event.corrected_answer || null,
      event_count: (current.answer_review?.event_count || 0) + 1
    };
    const storageText = storageLabel(data.event?.storage);
    await fetchCandidates(null, currentIndex >= 0 ? currentIndex : null, reviewedKey);
    const saved = document.getElementById('saved');
    if (saved && storageText) saved.textContent = `已寫入：${storageText}`;
  } else {
    document.getElementById('saved').textContent = `寫入失敗：${data.error}`;
  }
}

async function review(action, correction = null, options = {}) {
  if (!current) return;
  const stayOnCurrent = Boolean(options.stayOnCurrent);
  const reviewedKey = current.candidate_key;
  const currentIndex = filtered.findIndex(item => item.candidate_key === reviewedKey);
  const wasReviewed = (current.review?.status || '') === 'reviewed';
  const notes = options.notes ?? document.getElementById('notes')?.value ?? document.getElementById('manualAssetNotes')?.value ?? '';
  const body = {candidate_key: current.candidate_key, action, notes, reviewer: 'local'};
  if (correction) {
    body.correction = correction;
  } else if (current.review?.correction) {
    body.correction = current.review.correction;
  }
  const res = await fetch('/api/review', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const data = await res.json();
  if (data.ok) {
    clearCandidateCache();
    const savedAction = data.event.action || action;
    const savedCorrection = data.event.correction || current.review?.correction || null;
    const isVisualReviewAction = savedAction === 'human_review_pdf_visual';
    if (savedCorrection) {
      applyCorrectionToCurrent(savedCorrection);
    }
    if (isVisualReviewAction) {
      if (currentIndex >= 0) {
        filtered[currentIndex] = current;
        candidates = candidates.map(item => item.candidate_key === reviewedKey ? current : item);
      }
      savePreferencesSoon();
      return data;
    }
    current.review = {
      status: 'reviewed',
      action: savedAction,
      notes: data.event.notes ?? notes,
      updated_at: data.event.created_at,
      has_correction: Boolean(savedCorrection),
      correction: savedCorrection,
      event_count: (current.review?.event_count || 0) + 1
    };
    const savedTarget = document.getElementById('saved') || document.getElementById('manualAssetStatus');
    if (savedTarget) savedTarget.textContent = `已寫入：${savedAction}${storageLabel(data.event?.storage) ? `；${storageLabel(data.event.storage)}` : ''}`;
    if (stayOnCurrent) {
      if (!wasReviewed) reviewedCount += 1;
      if (currentIndex >= 0) {
        filtered[currentIndex] = current;
        candidates[currentIndex] = current;
      }
      updateCountLabels();
      renderList();
      renderDetail();
      const saved = document.getElementById('saved') || document.getElementById('manualAssetStatus');
      if (saved) saved.textContent = `已儲存人工校正；${storageLabel(data.event?.storage)}。畫面已更新，尚未自動通過。`;
      savePreferencesSoon();
      return;
    }
    const stillVisible = itemMatchesCurrentReviewFilter(current);
    updateProgressCountsAfterLocalReview(wasReviewed, stillVisible);
    if (stillVisible && currentIndex >= 0) {
      filtered[currentIndex] = current;
      candidates[currentIndex] = current;
    } else if (currentIndex >= 0) {
      filtered.splice(currentIndex, 1);
      candidates = candidates.filter(item => item.candidate_key !== reviewedKey);
    }
    chooseNextLocal(reviewedKey, Math.max(currentIndex, 0));
    return data;
  } else {
    const saved = document.getElementById('saved') || document.getElementById('manualAssetStatus');
    if (saved) saved.textContent = `寫入失敗：${data.error}`;
    return null;
  }
}

document.getElementById('search').addEventListener('input', applyFilter);
document.getElementById('status').addEventListener('change', applyFilter);
document.getElementById('reviewStatus').addEventListener('change', applyFilter);
document.getElementById('aiReviewStatus').addEventListener('change', applyFilter);
document.getElementById('visualStatus').addEventListener('change', applyFilter);
document.getElementById('answerReviewStatus').addEventListener('change', applyFilter);
document.getElementById('groupReviewStatus').addEventListener('change', applyFilter);
document.getElementById('categoryFilter').addEventListener('change', () => {
  document.getElementById('subjectFilter').value = '';
  document.getElementById('yearFilter').value = '';
  document.getElementById('ordinalFilter').value = '';
  applyFilter();
});
document.getElementById('subjectFilter').addEventListener('change', () => {
  document.getElementById('yearFilter').value = '';
  document.getElementById('ordinalFilter').value = '';
  applyFilter();
});
document.getElementById('yearFilter').addEventListener('change', () => {
  document.getElementById('ordinalFilter').value = '';
  applyFilter();
});
document.getElementById('ordinalFilter').addEventListener('change', applyFilter);
document.addEventListener('keydown', event => {
  const target = event.target;
  const tag = target && target.tagName ? target.tagName.toLowerCase() : '';
  const isEditing = target && (target.isContentEditable || ['input', 'textarea', 'select'].includes(tag));
  if (isEditing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    moveSelection(1);
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    moveSelection(-1);
  }
});
load();
</script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    candidate_path = args.candidate_jsonl or latest_path("*/question_candidates__*.jsonl")
    issue_path = args.issue_csv or latest_path("*/question_parse_issues__*.csv")
    review_log = args.review_log or candidate_path.parent / "question_review_events.jsonl"
    state = ReviewState(
        candidate_path,
        issue_path,
        review_log,
        auto_reload_candidates=args.auto_reload_candidates,
        review_backend=args.review_backend,
    )
    Handler.state = state
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Review UI: http://{args.host}:{args.port}/")
    print(f"Candidate JSONL: {candidate_path}")
    print(f"Issue CSV: {issue_path}")
    print(f"Review log: {review_log}")
    print(f"Review backend: {'sql' if state.sql_review_enabled else 'jsonl'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
