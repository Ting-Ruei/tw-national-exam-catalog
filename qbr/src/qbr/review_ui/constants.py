"""Review-UI shared constants.

Extracted verbatim from `scripts/serve_question_review_ui.py` so one file is no longer 10,700 lines.
`serve_question_review_ui` re-exports every name here; that indirection is deliberate and is why the
test files that load the server by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations

from qbr.paths import repo_root



import sys as _sys
from pathlib import Path as _Path

# The server's guarded imports fall back to `from scripts.x import ...`. `scripts/` has no
# `__init__.py`, so that is a namespace-package import and needs the *repository root* on `sys.path`
# — not the `scripts/` directory. Both are added: the root for `scripts.x`, the directory for the
# plain `import x` form that a bare `sys.path` entry would otherwise be needed for.
_REPO_ROOT = str(_Path(__file__).resolve().parents[4])
for _p in (_REPO_ROOT, _REPO_ROOT + "/scripts"):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from typing import Any
from pathlib import Path
import os
import re

PROJECT_ROOT = Path(repo_root())


ASSET_ROOT = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾")).expanduser()


DEFAULT_CANDIDATE_ROOT = ASSET_ROOT / "30_normalized_items" / "question_candidates"


MANUAL_ASSET_ROOT = ASSET_ROOT / "40_manual_assets"


MOBILE_UI_ROOT = PROJECT_ROOT / "review_ui"


WORKFLOW_UI_PATH = MOBILE_UI_ROOT / "v1-reference" / "workflow.html"


STRUCTURED_TABLE_RE = re.compile(r"<table.*?</table>", re.I | re.S)


STRUCTURED_TABLE_OPEN_RE = re.compile(r"<table\b", re.I)


VISUAL_DEPENDENCY_RE = re.compile(
    r"(下圖|附圖|圖中|圖示|如圖|圖片|影像|照片|箭頭|表中|下表|附表|心電圖|X\s*光|X光|超音波|切片圖|染色圖|鏡檢圖|尿沉渣圖|電泳圖|曲線圖|流程圖|家系圖)",
    re.I,
)


VISUAL_DEPENDENCY_SQL_RE = r"(下圖|附圖|圖中|圖示|如圖|圖片|影像|照片|箭頭|表中|下表|附表|心電圖|X\s*光|X光|超音波|切片圖|染色圖|鏡檢圖|尿沉渣圖|電泳圖|曲線圖|流程圖|家系圖)"


TABLE_DEPENDENCY_RE = re.compile(
    r"(表中|下表|附表|如下表|(?<![A-Za-z0-9_])table(?![A-Za-z0-9_]))",
    re.I,
)


TABLE_DEPENDENCY_SQL_RE = r"(表中|下表|附表|如下表|(^|[^[:alnum:]_])table([^[:alnum:]_]|$))"


LATIN_BINOMIAL_RE = re.compile(r"\b[A-Z][a-z]{3,}\s+[a-z][a-z-]{2,}\b")


ABBREVIATED_BINOMIAL_RE = re.compile(r"\b[A-Z]\.\s+[a-z][a-z-]{2,}\b")


CAPSULE_COMPOUND_RE = re.compile(
    r"(?:荧膜|荚膜|莢膜|萸膜).{0,12}(?:組織|漿菌|孢漿菌|胞漿菌|肥漿菌)"
    r"|Histoplasma\s+capsulatum",
    re.IGNORECASE,
)


CAPSULE_EXACT_REPLACEMENTS = {
    # These are exact two-character OCR/簡體 glyph repairs, not a semantic
    # rewrite of the organism name that follows.  Keeping the replacement at
    # this boundary lets Review UI offer a safe partial one-click correction
    # for variants such as ``荧膜組織漿菌`` while leaving the remaining wording
    # visible for PDF review.
    "荧膜": "莢膜",
    "荚膜": "莢膜",
}


ANSWER_ISSUE_CODES = {"missing_answer", "missing_answer_markdown", "unexpected_answer_value"}


RESET_REVIEW_ACTIONS = {"unreviewed", "reset_review"}


AI_RESET_REVIEW_ACTIONS = {"unreviewed", "reset_review", "reset_ai_review"}


AI_FEEDBACK_RATINGS = {"up", "down"}


AI_FEEDBACK_SCOPES = {"question", "group", "visual", "answer"}


GROUP_REVIEW_ACTIONS = {"confirm_not_group", "confirm_group", "reset_group_review"}


VISUAL_REVIEW_ACTIONS = {"human_review_pdf_visual"}


MOBILE_REVIEW_ACTIONS = {"mobile_defer", "mobile_resume"}


NON_QUESTION_REVIEW_ACTIONS = GROUP_REVIEW_ACTIONS | VISUAL_REVIEW_ACTIONS | MOBILE_REVIEW_ACTIONS


QUESTION_REVIEW_ACTIONS = {"accept", "correct", "needs_review", "block", "exclude", "unblock", "comment", "reviewed", *RESET_REVIEW_ACTIONS}


ANSWER_REVIEW_ACTIONS = {"accept", "correct", "needs_review", "block", "unblock", "comment", "reviewed", *RESET_REVIEW_ACTIONS}


QUESTION_READY_ACTIONS = {"accept", "unblock"}


ANSWER_READY_ACTIONS = {"accept", "unblock"}


NOTE_ACTIONS = {"comment"}


STANDING_ACTIONS = {"accept", "needs_review", "block", "exclude", "unblock", "comment", "reviewed"}


HUMAN_SUPERSEDES_AI_ACTIONS = {"accept", "unblock", "block", "needs_review", "exclude", "reviewed", "correct"}


PHARMACIST_TRACK_FILTER = "__pharmacist_track__"


CATEGORY_GROUP_FILTERS = {
    "__chinese_medicine_track__": ("中醫師", "中醫師(一)", "中醫師(二)"),
    "__physician_track__": ("醫師", "醫師(一)", "醫師(二)", "醫師(ㄧ)"),
    "__dentist_track__": ("牙醫師", "牙醫師(一)", "牙醫師(二)"),
    PHARMACIST_TRACK_FILTER: ("藥師", "藥師(一)", "藥師(二)"),
}


CATEGORY_GROUP_LABELS = {
    "__chinese_medicine_track__": "中醫師制度群組",
    "__physician_track__": "醫師制度群組",
    "__dentist_track__": "牙醫師制度群組",
    PHARMACIST_TRACK_FILTER: "藥師制度群組",
}


PHARMACIST_TRACK_CATEGORIES = CATEGORY_GROUP_FILTERS[PHARMACIST_TRACK_FILTER]


DEFAULT_AI_MODEL = os.environ.get("OPENAI_REVIEW_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"


OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")


AI_REVIEW_PROMPT_VERSION = "question_format_audit_v0.1"


REPAIR_REVIEWER_PREFIXES = (
    "repair_",
    "backfill_",
    "parser_global_refresh",
    "codex-repair",
    "codex-text-normalization-repair",
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


WORKFLOW_QUEUE_DEFINITIONS = (
    ("source", "來源／parser", "題數、題號、選項或 MinerU/parser contract 仍有阻擋。"),
    ("answer", "答案／MOD", "答案表、送分、多答案或答案證據需要人工核對。"),
    ("vision", "圖片／裁切", "先判斷是否真的有圖，再核對 MinerU asset 與裁切範圍。"),
    ("group", "題組範圍", "題組關鍵字或 range proposal 需要人工確認。"),
    ("notation", "上下標／字形", "符號、上下標或 compatibility glyph proposal 需要驗證。"),
    ("text", "文字三證據", "三個 PDF extractor 或來源文字仍有差異。"),
    ("revision", "修訂／重跑", "已有修訂或 lane stale，需要確認 revision loop。"),
    ("sample", "機器通過抽樣", "沒有開放例外；保留少量抽樣以量測漏檢。"),
)


WORKFLOW_QUEUE_LABELS = {key: label for key, label, _description in WORKFLOW_QUEUE_DEFINITIONS}


WORKFLOW_QUEUE_DESCRIPTIONS = {key: description for key, _label, description in WORKFLOW_QUEUE_DEFINITIONS}


class SqlWriteError(RuntimeError):
    """Raised when SQL-first review persistence cannot be confirmed."""


def category_matches_filter(category: str, category_filter: str) -> bool:
    normalized_category = normalize_category_name(category)
    if category_filter in CATEGORY_GROUP_FILTERS:
        return normalized_category in CATEGORY_GROUP_NORMALIZED_FILTERS[category_filter]
    return normalized_category == normalize_category_name(category_filter)


def normalize_category_name(value: Any) -> str:
    """Normalize category spelling for ReviewUI matching only.

    Official/raw names are deliberately not rewritten.  This matcher only
    removes harmless spacing and bracket-shape differences so old JSONL rows,
    SQL rows, and catalog-derived rows share one filter behavior.
    """
    text = str(value or "")
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


CATEGORY_GROUP_NORMALIZED_FILTERS = {
    key: frozenset(normalize_category_name(value) for value in values)
    for key, values in CATEGORY_GROUP_FILTERS.items()
}


def category_filter_values(category_filter: str) -> tuple[str, ...]:
    """Return SQL-safe aliases for a direct or制度群組 category filter."""
    values = CATEGORY_GROUP_FILTERS.get(category_filter, (category_filter,))
    expanded: set[str] = set()
    for value in values:
        raw = str(value or "")
        normalized = normalize_category_name(raw)
        expanded.update(
            {
                raw,
                normalized,
                normalized.replace("(", "（").replace(")", "）"),
            }
        )
    return tuple(sorted(value for value in expanded if value))


SQL_CANDIDATE_CATEGORY_EXPR = (
    "COALESCE(NULLIF(raw_candidate_json->'metadata'->>'normalized_category_name', ''), "
    "NULLIF(raw_candidate_json->'metadata'->>'group_name', ''), '')"
)


SQL_ANSWER_CATEGORY_EXPR = (
    "COALESCE(NULLIF(c.raw_candidate_json->'metadata'->>'normalized_category_name', ''), "
    "NULLIF(c.raw_candidate_json->'metadata'->>'group_name', ''), '')"
)


SQL_REVIEW_PREVIOUS_ACTION_EXPR = (
    "COALESCE(NULLIF(lq.event_json->>'previous_action', ''), "
    "NULLIF(lq.event_json->>'preserved_action', ''), "
    "NULLIF(lq.event_json->>'previous_review_action', ''), '')"
)


SQL_REVIEW_ACCEPTED_REAUDIT_EXPR = (
    "(lq.action IN ('unreviewed', 'reset_review') AND ("
    f"{SQL_REVIEW_PREVIOUS_ACTION_EXPR} IN ('accept', 'unblock') "
    "OR lower(COALESCE(lq.event_json->>'approval_ref', '')) LIKE '%%accepted%%reaudit%%' "
    "OR lower(COALESCE(lq.reviewer, '')) LIKE '%%accepted%%reaudit%%'"
    "))"
)


SQL_REVIEW_REPAIR_PENDING_EXPR = (
    "(lq.action IN ('unreviewed', 'reset_review') "
    f"AND NOT {SQL_REVIEW_ACCEPTED_REAUDIT_EXPR} AND ("
    "COALESCE(lq.event_json, '{}'::jsonb) ? 'repair_kind' "
    "OR COALESCE(lq.event_json, '{}'::jsonb) ? 'repair_type' "
    "OR COALESCE(lq.event_json, '{}'::jsonb) ? 'repair_action' "
    "OR COALESCE(lq.event_json, '{}'::jsonb) ? 'repair_scope' "
    "OR COALESCE(lq.event_json, '{}'::jsonb) ? 'source_event_id' "
    "OR COALESCE(lq.reviewer, '') LIKE 'repair_%%' "
    "OR COALESCE(lq.reviewer, '') LIKE 'backfill_%%' "
    "OR COALESCE(lq.reviewer, '') LIKE 'parser_global_refresh%%' "
    "OR COALESCE(lq.reviewer, '') LIKE 'codex-repair%%' "
    "OR COALESCE(lq.reviewer, '') LIKE 'codex-text-normalization-repair%%' "
    "OR COALESCE(lq.notes, '') ~ '(修復|正規化|待複核|需人工複核)' "
    "OR COALESCE(c.raw_candidate_json->'metadata'->>'review_block_repair', '') <> '' "
    "OR COALESCE(c.raw_candidate_json->'metadata'->>'backfill_repair', '') <> '' "
    "OR COALESCE(c.raw_candidate_json->'metadata'->>'backfill_source', '') <> ''"
    "))"
)


AI_ANSWER_DEFER_LABELS = {"answer_pair_suspect", "needs_human_review", "pass_likely"}


AI_OCR_TEXT_REPLACEMENTS = [
    *CAPSULE_EXACT_REPLACEMENTS.items(),
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


QBR_AI_FINDINGS_STREAM = "question_ai_findings.jsonl"


_TAIL_ANCHOR_BYTES = 64


PAGE_HTML = (MOBILE_UI_ROOT / "v1-reference" / "legacy.html").read_text(encoding="utf-8")
