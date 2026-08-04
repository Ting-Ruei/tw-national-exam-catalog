#!/usr/bin/env python3
"""Run compact, advisory-only initial question audits.

The model protocol is intentionally smaller than the Review UI event schema.
Each lane reads every question but returns only exceptions. Local code validates
the exceptions, materializes safe OCR replacements, and builds Review UI
compatible result previews. This script never writes review or AI-review events.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import http.client
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


PROMPT_VERSION = "compact_initial_question_audit_v9"
LANES = ("ocr_text", "meaning", "visual", "group")
LANE_LABELS = {
    "ocr_text": "OCR 文字",
    "meaning": "題義",
    "visual": "圖片題",
    "group": "題組題",
}
LANE_CODES = {
    "ocr_text": {
        "simplified_character",
        "ocr_character",
        "notation_markup",
        "punctuation_spacing",
    },
    "meaning": {
        "broken_sentence",
        "duplicated_fragment",
        "missing_fragment",
        "boundary_merge",
        "boundary_missing",
        "option_structure",
    },
    "visual": {
        "missing_visual_asset",
        "unnecessary_visual_asset",
        "unreadable_visual_asset",
        "cropped_visual_asset",
        "mismatched_visual_asset",
        "visual_dependency_uncertain",
    },
    "group": {
        "missing_group_link",
        "wrong_group_range",
        "missing_shared_stem",
        "orphan_continuation",
        "group_sequence_error",
        "group_dependency_uncertain",
    },
}
LANE_LOCATIONS = {
    "ocr_text": re.compile(r"^(stem|option_[A-H])$"),
    "meaning": re.compile(r"^(stem|options|option_[A-H])$"),
    "visual": re.compile(r"^(stem|options|images|option_[A-H])$"),
    "group": re.compile(r"^(stem|group|neighbors)$"),
}
LANE_MAX_TOKENS = {
    "ocr_text": 1024,
    "meaning": 1024,
    "visual": 768,
    "group": 768,
}
LANE_MESSAGES = {
    "simplified_character": "疑似簡體字或繁簡混用。",
    "ocr_character": "疑似 OCR 字形辨認錯誤。",
    "notation_markup": "疑似符號、公式或上下標轉寫錯誤。",
    "punctuation_spacing": "疑似影響閱讀的標點或空格轉寫錯誤。",
    "broken_sentence": "題文疑似因轉寫而出現明顯斷裂。",
    "duplicated_fragment": "題文疑似重複轉寫片段。",
    "missing_fragment": "題文疑似缺少必要片段，需比對原卷。",
    "boundary_merge": "疑似題幹或選項邊界黏合。",
    "boundary_missing": "疑似題幹或選項邊界遺失。",
    "option_structure": "選項結構疑似不完整或錯位。",
    "missing_visual_asset": "題目需要圖表，但目前資產疑似缺漏。",
    "unnecessary_visual_asset": "目前圖片可能不屬於這一題。",
    "unreadable_visual_asset": "圖片內容疑似無法辨讀。",
    "cropped_visual_asset": "圖片疑似裁切不完整。",
    "mismatched_visual_asset": "圖片內容與題文線索疑似不相符。",
    "visual_dependency_uncertain": "是否需要或是否配對正確仍需人工看原卷。",
    "missing_group_link": "題組線索存在，但題組連結疑似缺漏。",
    "wrong_group_range": "題組範圍疑似錯誤。",
    "missing_shared_stem": "題組共同題幹疑似缺漏。",
    "orphan_continuation": "承接語句找不到前題或共同題幹。",
    "group_sequence_error": "題組內題序疑似錯誤。",
    "group_dependency_uncertain": "題組關係仍需人工確認。",
}
VISUAL_CUE_RE = re.compile(
    r"(下圖|附圖|右圖|左圖|圖中|如圖|如下圖|依圖|箭頭所指|表中|下表|附表|"
    r"影像如下|照片如下|心電圖如下|圖示如下|所提供之影像)"
)
GROUP_CUE_RE = re.compile(
    r"(第\s*\d{1,3}\s*(?:至|到|~|～|-|－)\s*\d{1,3}\s*題|"
    r"(?:回答|作答)\s*(?:下列|以下).{0,8}題|承上題|呈上題|^\s*[（(]?\s*上題)",
    re.I,
)
WELL_FORMED_DISPLAY_TAG_RE = re.compile(
    r"<(sub|sup)>[^<>]+</\1>",
    re.I,
)
MEANING_DOMAIN_JUDGMENT_RE = re.compile(
    r"(通常|醫學上|藥理|藥效|作用機轉|治療原則|臨床上|生理上|病理上|"
    r"不應發生|正確答案|答案應為|專業上)",
    re.I,
)
VALID_EXAM_WORDING = {"何者", "那一種", "那一項", "下列何者"}
OCR_STYLE_EQUIVALENT_REPLACEMENTS = {
    ("投與", "投予"),
    ("投予", "投與"),
}
OCR_NON_DEFECT_NOTE_RE = re.compile(
    r"(字面.{0,8}(?:無錯|沒有錯)|無錯別字|語意不明|指代不清|"
    r"非\s*OCR|題義問題|事實錯誤|文字無.{0,8}錯|標記完整|"
    r"無實質錯誤|正確繁體|無需修改|不構成錯誤|若視為.{0,12}無問題|"
    r"無(?:明顯)?\s*(?:OCR\s*)?(?:錯誤|問題)|"
    r"不(?:構成|視為).{0,12}(?:錯誤|問題)|非必須回報|不必回報|"
    r"排版細節|僅供參考)",
    re.I,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def task_content(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("content")
    return value if isinstance(value, dict) else {}


def task_exam(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("exam")
    return value if isinstance(value, dict) else {}


def task_signals(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("signals")
    return value if isinstance(value, dict) else {}


def compact_options(task: dict[str, Any]) -> list[dict[str, str]]:
    rows = task_content(task).get("options")
    if not isinstance(rows, list):
        return []
    return [
        {
            "key": str(row.get("key") or "").strip().upper(),
            "text": str(row.get("text") or ""),
        }
        for row in rows
        if isinstance(row, dict) and str(row.get("key") or "").strip()
    ]


def parser_issue_codes(task: dict[str, Any]) -> list[str]:
    rows = task_signals(task).get("parser_issues")
    if not isinstance(rows, list):
        return []
    return [
        str(row.get("code") or row.get("issue_code") or "")
        for row in rows
        if isinstance(row, dict) and (row.get("code") or row.get("issue_code"))
    ][:8]


def lane_applicable(task: dict[str, Any], lane: str) -> bool:
    if lane in {"ocr_text", "meaning"}:
        return True
    content = task_content(task)
    combined = "\n".join(
        [
            str(content.get("stem") or ""),
            *[option["text"] for option in compact_options(task)],
        ]
    )
    issue_codes = parser_issue_codes(task)
    if lane == "visual":
        return bool(
            usable_image_paths(task)
            or VISUAL_CUE_RE.search(combined)
            or any(
                token in code.lower()
                for code in issue_codes
                for token in ("image", "visual", "table")
            )
        )
    if lane == "group":
        return bool(
            content.get("group_ref")
            or content.get("group_sequence_no") is not None
            or GROUP_CUE_RE.search(str(content.get("stem") or ""))
            or any("group" in code.lower() for code in issue_codes)
        )
    raise ValueError(f"unsupported lane: {lane}")


def select_lane_tasks(tasks: list[dict[str, Any]], lane: str) -> list[dict[str, Any]]:
    return [task for task in tasks if lane_applicable(task, lane)]


def base_question(task: dict[str, Any], index: int) -> dict[str, Any]:
    content = task_content(task)
    exam = task_exam(task)
    return {
        "q": index,
        "question_number": exam.get("question_number"),
        "stem": str(content.get("stem") or ""),
        "options": compact_options(task),
    }


def usable_image_paths(task: dict[str, Any]) -> list[Path]:
    content = task_content(task)
    refs: list[Any] = []
    if isinstance(content.get("image_refs"), list):
        refs.extend(content["image_refs"])
    if isinstance(content.get("stem_image"), dict):
        refs.append(content["stem_image"])
    for option in content.get("options") or []:
        if isinstance(option, dict) and isinstance(option.get("image"), dict):
            refs.append(option["image"])
    paths: list[Path] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        raw = ref.get("path") or ref.get("path_relative") or ref.get("relative_path")
        if not raw:
            continue
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        resolved = str(path.resolve())
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        paths.append(path.resolve())
    return paths


def lane_questions(
    tasks: list[dict[str, Any]],
    lane: str,
) -> tuple[list[dict[str, Any]], list[Path]]:
    questions: list[dict[str, Any]] = []
    image_paths: list[Path] = []
    for index, task in enumerate(tasks, start=1):
        row = base_question(task, index)
        if lane == "ocr_text":
            pass
        elif lane == "meaning":
            row["parser_issues"] = parser_issue_codes(task)
            row["visual_asset_count"] = len(usable_image_paths(task))
        elif lane == "visual":
            slots: list[int] = []
            for path in usable_image_paths(task):
                image_paths.append(path)
                slots.append(len(image_paths))
            row["image_slots"] = slots
            row["declared_asset_count"] = len(
                task_content(task).get("image_refs")
                if isinstance(task_content(task).get("image_refs"), list)
                else []
            )
        elif lane == "group":
            content = task_content(task)
            neighbors = task.get("neighbors") if isinstance(task.get("neighbors"), dict) else {}
            previous = neighbors.get("previous") if isinstance(neighbors.get("previous"), dict) else None
            next_item = neighbors.get("next") if isinstance(neighbors.get("next"), dict) else None
            row = {
                "q": index,
                "question_number": task_exam(task).get("question_number"),
                "stem": str(content.get("stem") or ""),
                "group_ref": content.get("group_ref"),
                "group_sequence_no": content.get("group_sequence_no"),
                "previous": (
                    {
                        "n": previous.get("question_number"),
                        "stem": str(previous.get("stem") or ""),
                    }
                    if previous
                    else None
                ),
                "next": (
                    {
                        "n": next_item.get("question_number"),
                        "stem": str(next_item.get("stem") or ""),
                    }
                    if next_item
                    else None
                ),
            }
        else:
            raise ValueError(f"unsupported lane: {lane}")
        questions.append(row)
    return questions, image_paths


def lane_prompt(lane: str) -> str:
    common = (
        "你是臺灣國家考試題目的初步抓漏員。這不是答案審查，也不是詳解工作。"
        "你必須讀完本批所有題目，但正常題不要逐題輸出。"
        "不得更改答案、不得用專業知識改寫題目、不得因個人文風偏好提出修改。"
        "只輸出 JSON schema 指定的單一根物件，不得輸出 Markdown、解釋文字或第二份 JSON。"
        "issues 只列真正需要人工注意的少數題目；每個 finding 都要填輸入中的短索引 q，"
        "每題最多三項。若超過三項，只保留信心最高且最影響原文的三項。"
    )
    specific = {
        "ocr_text": (
            "本通道只檢查 OCR／轉寫後文字：簡體字、明顯錯別字、字形誤辨、"
            "公式或上下標標記、足以影響閱讀的標點空格。observed 必須是輸入欄位中逐字存在的"
            "最小片段；能安全改字才填最小 replacement，否則填 null。replacement 必須是單字、"
            "單一字元或最小符號替換，不得重寫完整醫學名詞。選項黏合、選項標號或邊界問題留給"
            "題義通道，本通道不要回報。完整成對的 <sub>...</sub>、<sup>...</sup> 是 Review UI "
            "可顯示的合法上下標，不得只因看見 HTML tag 就回報；只有標記破損、不成對或內容間距"
            "確實錯誤時才可回報並提供最小 replacement。『何者』『那一種』『那一項』是來源卷"
            "常見且有效的考題措辭，不得依文風改成『哪』或宣稱指代不清。若 note 聲稱『應為』"
            "某字，replacement 必須填同一個最小替換；無法安全改字時不得在 note 暗示確定答案。"
            "『投與』與『投予』都是臺灣醫藥題可用的給藥措辭，不得互相改寫。"
            "如果檢查後認為『合法』『無錯』『非 OCR 問題』，必須完全省略該 finding，不得把"
            "自我否定說明留在 issues。note 通常留空。"
        ),
        "meaning": (
            "本通道只檢查轉寫造成的題意不通：漏掉片段、重複片段、句子斷裂、選項黏合或邊界遺失。"
            "醫學與藥學題可以簡短、艱深或使用中英混寫，這些都不是錯誤；也不得判斷選項內容真假。"
            "只要句子文法與題目結構完整，即使專業前提反直覺或你認為醫學內容矛盾，也必須視為正常。"
            "禁止用『通常』『臨床上』『作用機轉』『不應發生』等專業知識質疑題目前提。"
            "observed 必須是可核對的原文片段，replacement 一律填 null，note 只用一句短語說明斷點。"
        ),
        "visual": (
            "本通道只檢查圖片／表格依賴與資產品質：題文需要圖但缺圖、圖片不屬於本題、"
            "圖片模糊或裁切、圖片與題文線索不相符。image_slots 依附圖順序從 1 開始。"
            "你必須實際查看每一張附圖；若圖片本身含 A、B、C、D 分圖且文字 options 為空，"
            "圖片很可能就是本題的選項，不能標成 unnecessary_visual_asset。"
            "不要解題、不要判斷正確選項。沒有圖片且題文沒有圖像線索時是正常題，絕對不要輸出"
            " unnecessary_visual_asset。只有實際附了不相關圖片才可使用該代碼。"
            "location=images 時，observed 要填例如「image_slots=1：可見 A-D 血液抹片」的短證據，"
            "不可留空。replacement 一律填 null，note 只描述可見的資產問題。"
        ),
        "group": (
            "本通道只檢查題組關係：明示範圍、共同題幹、承上題、group_ref 與題序。"
            "不可只因相鄰題目主題相似就推定為題組；獨立題沒有共同題幹是正常情形，絕對不要"
            "回報 missing_shared_stem。replacement 一律填 null，"
            "note 只描述缺少或矛盾的連結。"
        ),
    }
    codes = "|".join(sorted(LANE_CODES[lane]))
    locations = {
        "ocr_text": "stem|option_A|option_B|option_C|option_D",
        "meaning": "stem|options|option_A|option_B|option_C|option_D",
        "visual": "stem|options|images|option_A|option_B|option_C|option_D",
        "group": "stem|group|neighbors",
    }[lane]
    contract = (
        "輸出結構必須完全如下，欄位一個都不能省略："
        '{"issues":['
        '{"q":題目短索引q,"code":"代碼","location":"位置",'
        '"observed":"原文最小片段","replacement":null,"confidence":0.9,"note":"短句或空字串"}'
        ']}。正常題不要輸出 finding；沒有問題時輸出 {"issues":[]}。'
        f"code 只能是：{codes}。location 只能是：{locations}。"
        "issues 裡的 q 只能使用輸入中的 q，嚴禁改用 question_number、n、"
        "candidate_key 或 image_slots。"
        "observed 只能抄題幹或選項的實際文字片段；不得包含 JSON 的 key、text、引號或跳脫語法。"
    )
    return common + specific[lane] + contract


def response_schema(lane: str, task_count: int) -> dict[str, Any]:
    issue_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "q": {"type": "integer", "minimum": 1, "maximum": task_count},
            "code": {"type": "string", "enum": sorted(LANE_CODES[lane])},
            "location": {
                "type": "string",
                "enum": {
                    "ocr_text": ["stem", *[f"option_{key}" for key in "ABCDEFGH"]],
                    "meaning": [
                        "stem",
                        "options",
                        *[f"option_{key}" for key in "ABCDEFGH"],
                    ],
                    "visual": [
                        "stem",
                        "options",
                        "images",
                        *[f"option_{key}" for key in "ABCDEFGH"],
                    ],
                    "group": ["stem", "group", "neighbors"],
                }[lane],
            },
            "observed": {"type": "string", "minLength": 1, "maxLength": 240},
            "replacement": (
                {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 240}, {"type": "null"}]}
                if lane == "ocr_text"
                else {"type": "null"}
            ),
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "note": {"type": "string", "maxLength": 240},
        },
        "required": [
            "q",
            "code",
            "location",
            "observed",
            "replacement",
            "confidence",
            "note",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "issues": {
                "type": "array",
                "maxItems": max(task_count * 3, 1),
                "items": issue_schema,
            },
        },
        "required": ["issues"],
    }


def build_lane_packet(tasks: list[dict[str, Any]], lane: str) -> dict[str, Any]:
    questions, image_paths = lane_questions(tasks, lane)
    identity = {
        "prompt_version": PROMPT_VERSION,
        "lane": lane,
        "candidate_keys": [str(task.get("candidate_key") or "") for task in tasks],
        "content_hashes": [
            task.get("effective_content_hash") or stable_hash(task_content(task))
            for task in tasks
        ],
    }
    batch_id = f"{lane}-{stable_hash(identity)[:16]}"
    model_input = {"questions": questions}
    return {
        "prompt_version": PROMPT_VERSION,
        "lane": lane,
        "lane_label": LANE_LABELS[lane],
        "batch_id": batch_id,
        "task_count": len(tasks),
        "candidate_keys": identity["candidate_keys"],
        "system": lane_prompt(lane),
        "prompt": canonical_json(model_input),
        "response_schema": response_schema(lane, len(tasks)),
        "image_paths": [str(path) for path in image_paths],
    }


def extract_json_response(text: str) -> tuple[dict[str, Any], list[str]]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?[ \t]*\r?\n?", "", candidate, count=1, flags=re.I)
        candidate = re.sub(r"\r?\n?```[ \t]*$", "", candidate, count=1)
        candidate = candidate.strip()
    decoder = json.JSONDecoder()
    value, end = decoder.raw_decode(candidate)
    if not isinstance(value, dict):
        raise ValueError("response root must be a JSON object")
    warnings: list[str] = []
    tail = candidate[end:].strip()
    duplicate_count = 0
    while tail:
        if tail.startswith("```"):
            tail = re.sub(r"^```(?:json)?[ \t]*\r?\n?", "", tail, count=1, flags=re.I).strip()
            if not tail:
                break
        try:
            extra, extra_end = decoder.raw_decode(tail)
        except json.JSONDecodeError:
            warnings.append("trailing_text_removed")
            break
        if extra != value:
            raise ValueError("conflicting_json_tail")
        duplicate_count += 1
        tail = tail[extra_end:].strip()
    if duplicate_count:
        warnings.append(f"duplicate_root_json_removed:{duplicate_count}")
    return value, warnings


def extract_json(text: str) -> dict[str, Any]:
    value, _warnings = extract_json_response(text)
    return value


def field_text(task: dict[str, Any], location: str) -> str | None:
    content = task_content(task)
    if location == "stem":
        return str(content.get("stem") or "")
    match = re.fullmatch(r"option_([A-H])", location)
    if match:
        target = match.group(1)
        for option in content.get("options") or []:
            if isinstance(option, dict) and str(option.get("key") or "").upper() == target:
                return str(option.get("text") or "")
    if location == "options":
        return "\n".join(option["text"] for option in compact_options(task))
    return None


def safe_ocr_replacement(code: str, observed: str, replacement: str) -> bool:
    if len(observed) > 80 or len(replacement) > 80:
        return False
    if code in {"simplified_character", "ocr_character"}:
        return (
            abs(len(observed) - len(replacement)) <= 1
            and SequenceMatcher(None, observed, replacement).ratio() >= 0.45
        )
    if code == "punctuation_spacing":
        return abs(len(observed) - len(replacement)) <= 4
    if code == "notation_markup":
        return abs(len(observed) - len(replacement)) <= 16
    return False


def validate_lane_response(
    packet: dict[str, Any],
    tasks: list[dict[str, Any]],
    raw_text: str,
    done_reason: str | None,
) -> dict[str, Any]:
    lane = str(packet["lane"])
    errors: list[str] = []
    warnings: list[str] = []
    normalized: list[dict[str, Any]] = []
    dropped_issue_count = 0
    filtered_non_issue_count = 0
    try:
        value, parse_warnings = extract_json_response(raw_text)
    except (ValueError, json.JSONDecodeError) as exc:
        return {
            "complete": False,
            "errors": [f"invalid_json:{exc}"],
            "warnings": [],
            "issues": [],
            "dropped_issue_count": 0,
            "filtered_non_issue_count": 0,
            "selected_candidate_keys": [
                str(task.get("candidate_key") or "")
                for task in tasks
            ],
        }
    warnings.extend(parse_warnings)
    raw_issues: list[dict[str, Any]] = []
    issues_by_q = value.get("issues_by_q")
    if isinstance(issues_by_q, dict):
        for raw_q, per_question_issues in issues_by_q.items():
            try:
                q = int(raw_q)
            except (TypeError, ValueError):
                warnings.append(f"issues_by_q:{raw_q}:invalid_q_dropped")
                dropped_issue_count += 1
                continue
            if not 1 <= q <= len(tasks):
                warnings.append(f"issues_by_q:{raw_q}:q_out_of_range_dropped")
                dropped_issue_count += 1
                continue
            if isinstance(per_question_issues, dict) and {
                "code",
                "location",
                "observed",
                "replacement",
                "confidence",
                "note",
            }.issubset(per_question_issues):
                warnings.append(f"issues_by_q:{raw_q}:single_finding_wrapped")
                per_question_issues = [per_question_issues]
            if not isinstance(per_question_issues, list):
                warnings.append(f"issues_by_q:{raw_q}:not_list_dropped")
                dropped_issue_count += 1
                continue
            if len(per_question_issues) > 3:
                warnings.append(
                    f"issues_by_q:{raw_q}:excess_findings_received:{len(per_question_issues)}"
                )
            for issue in per_question_issues:
                if isinstance(issue, dict):
                    raw_issues.append({**issue, "q": q})
                else:
                    raw_issues.append({"q": q, "_invalid_issue_value": issue})
    else:
        # Keep legacy v1 responses readable so prior medical-technology runs and
        # saved external responses remain auditable after the v2 protocol ships.
        legacy_issues = value.get("issues")
        if not isinstance(legacy_issues, list):
            errors.append("issues_by_q_not_object")
        else:
            raw_issues = legacy_issues
            if "batch_id" in value or "checked_count" in value:
                warnings.append("legacy_flat_issues_response")
                if "batch_id" in value and value.get("batch_id") != packet["batch_id"]:
                    errors.append("batch_id_mismatch")
                if "checked_count" in value and value.get("checked_count") != len(tasks):
                    errors.append("checked_count_mismatch")
    seen: Counter[tuple[Any, ...]] = Counter()
    for position, issue in enumerate(raw_issues, start=1):
        prefix = f"issue_{position}"
        if not isinstance(issue, dict) or "_invalid_issue_value" in issue:
            warnings.append(f"{prefix}:not_object_dropped")
            dropped_issue_count += 1
            continue
        try:
            q = int(issue.get("q"))
        except (TypeError, ValueError):
            warnings.append(f"{prefix}:invalid_q_dropped")
            dropped_issue_count += 1
            continue
        if not 1 <= q <= len(tasks):
            warnings.append(f"{prefix}:q_out_of_range_dropped")
            dropped_issue_count += 1
            continue
        code = str(issue.get("code") or "")
        location = str(issue.get("location") or "")
        observed = str(issue.get("observed") or "")
        replacement = issue.get("replacement")
        note = str(issue.get("note") or "")
        try:
            confidence = float(issue.get("confidence"))
        except (TypeError, ValueError):
            confidence = -1
        issue_errors: list[str] = []
        issue_warnings: list[str] = []
        if lane == "ocr_text" and (
            (
                code == "ocr_character"
                and observed.strip() in VALID_EXAM_WORDING
            )
            or OCR_NON_DEFECT_NOTE_RE.search(note)
        ):
            warnings.append(f"{prefix}:non_defect_ocr_finding_dropped")
            filtered_non_issue_count += 1
            continue
        if (
            lane == "ocr_text"
            and isinstance(replacement, str)
            and (observed.strip(), replacement.strip())
            in OCR_STYLE_EQUIVALENT_REPLACEMENTS
        ):
            warnings.append(f"{prefix}:style_equivalent_replacement_dropped")
            filtered_non_issue_count += 1
            continue
        if (
            lane == "ocr_text"
            and replacement is None
            and WELL_FORMED_DISPLAY_TAG_RE.search(observed)
        ):
            warnings.append(f"{prefix}:valid_display_markup_finding_dropped")
            filtered_non_issue_count += 1
            continue
        if lane == "meaning" and MEANING_DOMAIN_JUDGMENT_RE.search(note):
            warnings.append(f"{prefix}:domain_judgment_finding_dropped")
            filtered_non_issue_count += 1
            continue
        if code not in LANE_CODES[lane]:
            issue_errors.append("invalid_code")
        if not LANE_LOCATIONS[lane].fullmatch(location):
            issue_errors.append("invalid_location")
        if not observed and lane == "visual":
            slots = list(range(1, len(usable_image_paths(tasks[q - 1])) + 1))
            observed = (
                f"image_slots={','.join(str(slot) for slot in slots)}"
                if slots
                else "no_attached_image"
            )
            issue_warnings.append("observed_synthesized_from_visual_input")
        elif not observed and lane == "group":
            observed = str(task_content(tasks[q - 1]).get("stem") or "")[:120]
            issue_warnings.append("observed_synthesized_from_group_input")
        if not observed or len(observed) > 240:
            issue_errors.append("invalid_observed")
        if len(note) > 240:
            note = note[:240]
            issue_warnings.append("note_truncated")
        if not 0 <= confidence <= 1:
            issue_errors.append("invalid_confidence")
        if lane in {"ocr_text", "meaning"}:
            source = field_text(tasks[q - 1], location)
            if source is None or observed not in source:
                issue_warnings.append("observed_not_in_field")
        if lane == "ocr_text":
            if replacement is not None:
                if not isinstance(replacement, str) or not replacement or replacement == observed:
                    issue_warnings.append("invalid_replacement_removed")
                    replacement = None
                elif len(replacement) > 240:
                    issue_warnings.append("replacement_too_long_removed")
                    replacement = None
                elif not safe_ocr_replacement(code, observed, replacement):
                    issue_warnings.append("unsafe_replacement_removed")
                    replacement = None
        elif replacement is not None:
            issue_warnings.append("replacement_not_allowed_for_lane_removed")
            replacement = None
        if issue_errors:
            warnings.append(
                f"{prefix}:malformed_finding_dropped:{','.join(issue_errors)}"
            )
            dropped_issue_count += 1
            continue
        warnings.extend(f"{prefix}:{warning}" for warning in issue_warnings)
        signature = (q, code, location, observed, replacement)
        seen[signature] += 1
        if seen[signature] > 1:
            warnings.append(f"{prefix}:duplicate_finding_dropped")
            dropped_issue_count += 1
            continue
        normalized.append(
            {
                "q": q,
                "candidate_key": str(tasks[q - 1].get("candidate_key") or ""),
                "lane": lane,
                "code": code,
                "location": location,
                "observed": observed,
                "replacement": replacement,
                "confidence": confidence,
                "note": note,
                "evidence_validated": not any(
                    warning.startswith("observed_")
                    for warning in issue_warnings
                ),
                "_position": position,
            }
        )
    capped: list[dict[str, Any]] = []
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for issue in normalized:
        grouped[int(issue["q"])].append(issue)
    for q, question_issues in grouped.items():
        if len(question_issues) > 3:
            excess = len(question_issues) - 3
            warnings.append(
                f"q_{q}:excess_findings_dropped:{excess}"
            )
            dropped_issue_count += excess
            question_issues = sorted(
                question_issues,
                key=lambda issue: (
                    -float(issue["confidence"]),
                    int(issue["_position"]),
                ),
            )[:3]
        capped.extend(question_issues)
    normalized = sorted(capped, key=lambda issue: int(issue["_position"]))
    for issue in normalized:
        issue.pop("_position", None)
    if done_reason != "stop":
        errors.append(f"done_reason:{done_reason or 'missing'}")
    return {
        "complete": not errors,
        "batch_id": packet["batch_id"],
        "checked_count": len(tasks),
        "issue_count": len(normalized),
        "dropped_issue_count": dropped_issue_count,
        "filtered_non_issue_count": filtered_non_issue_count,
        "errors": errors,
        "warnings": warnings,
        "issues": normalized,
        "selected_candidate_keys": [
            str(task.get("candidate_key") or "")
            for task in tasks
        ],
    }


def skipped_lane_validation(reason: str = "no_applicable_candidates") -> dict[str, Any]:
    return {
        "complete": True,
        "skipped": True,
        "skip_reason": reason,
        "selected_candidate_keys": [],
        "checked_count": 0,
        "issue_count": 0,
        "dropped_issue_count": 0,
        "filtered_non_issue_count": 0,
        "errors": [],
        "warnings": [],
        "issues": [],
    }


def unavailable_visual_validation(
    tasks: list[dict[str, Any]],
    note: str,
) -> dict[str, Any]:
    issues = [
        {
            "q": index,
            "candidate_key": str(task.get("candidate_key") or ""),
            "lane": "visual",
            "code": "visual_dependency_uncertain",
            "location": "images",
            "observed": f"attached_image_count={len(usable_image_paths(task))}",
            "replacement": None,
            "confidence": 0.0,
            "note": note,
            "evidence_validated": True,
        }
        for index, task in enumerate(tasks, start=1)
    ]
    return {
        "complete": True,
        "unavailable": True,
        "skip_reason": "vision_model_unavailable",
        "selected_candidate_keys": [
            str(task.get("candidate_key") or "")
            for task in tasks
        ],
        "checked_count": 0,
        "issue_count": len(issues),
        "dropped_issue_count": 0,
        "filtered_non_issue_count": 0,
        "errors": [],
        "warnings": ["vision_model_unavailable"],
        "issues": issues,
    }


def safe_option_rows(
    task: dict[str, Any],
    updates: dict[str, str],
    markup_updates: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    markup_updates = markup_updates or {}
    rows: list[dict[str, Any]] = []
    for option in task_content(task).get("options") or []:
        if not isinstance(option, dict):
            continue
        key = str(option.get("key") or "").strip().upper()
        row: dict[str, Any] = {
            "key": key,
            "text": updates.get(key, str(option.get("text") or "")),
        }
        if "image" in option:
            row["image"] = option.get("image")
        if "markup" in option:
            row["markup"] = markup_updates.get(key, option.get("markup"))
        rows.append(row)
    return rows


def replace_option_markup(
    markup: Any,
    observed: str,
    replacement: str,
) -> Any:
    """Keep correction text and its embedded display markup synchronized."""
    updated = copy.deepcopy(markup)
    if not isinstance(updated, dict):
        return updated
    for field in ("plain", "markup"):
        value = updated.get(field)
        if isinstance(value, str) and value.count(observed) == 1:
            updated[field] = value.replace(observed, replacement, 1)
    return updated


def materialize_ocr_correction(
    task: dict[str, Any],
    issues: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    content = task_content(task)
    stem = str(content.get("stem") or "")
    original_stem = stem
    option_text = {
        str(option.get("key") or "").upper(): str(option.get("text") or "")
        for option in content.get("options") or []
        if isinstance(option, dict)
    }
    option_markup = {
        str(option.get("key") or "").upper(): copy.deepcopy(option.get("markup"))
        for option in content.get("options") or []
        if isinstance(option, dict)
    }
    original_options = dict(option_text)
    changes: list[str] = []
    rejected: list[str] = []
    for issue in issues:
        replacement = issue.get("replacement")
        if not isinstance(replacement, str):
            continue
        observed = str(issue.get("observed") or "")
        location = str(issue.get("location") or "")
        if location == "stem":
            if stem.count(observed) != 1:
                rejected.append(f"stem:{observed}:ambiguous_or_missing")
                continue
            stem = stem.replace(observed, replacement, 1)
            changes.append(f"題幹：{observed} → {replacement}")
            continue
        match = re.fullmatch(r"option_([A-H])", location)
        if not match:
            rejected.append(f"{location}:{observed}:unsupported_location")
            continue
        key = match.group(1)
        before = option_text.get(key)
        if before is None or before.count(observed) != 1:
            rejected.append(f"option_{key}:{observed}:ambiguous_or_missing")
            continue
        option_text[key] = before.replace(observed, replacement, 1)
        option_markup[key] = replace_option_markup(
            option_markup.get(key),
            observed,
            replacement,
        )
        changes.append(f"選項 {key}：{observed} → {replacement}")
    correction: dict[str, Any] = {}
    if stem != original_stem:
        correction["stem"] = stem
    if option_text != original_options:
        correction["options"] = safe_option_rows(task, option_text, option_markup)
    return (correction or None), changes, rejected


def issue_finding(issue: dict[str, Any]) -> dict[str, Any]:
    lane = str(issue["lane"])
    replacement = issue.get("replacement")
    evidence = str(issue.get("observed") or "")
    if isinstance(replacement, str):
        evidence += f" → {replacement}"
    if not issue.get("evidence_validated", True):
        evidence = f"模型片段（未通過逐字定位）：{evidence}"
    suggestion = {
        "ocr_text": "人工比對原卷；可使用建議校正按鈕預覽。",
        "meaning": "人工比對原卷文字與斷句。",
        "visual": "在圖片題流程中核對圖片與官方 PDF。",
        "group": "在題組題流程中核對共同題幹與範圍。",
    }[lane]
    return {
        "code": str(issue["code"]),
        "severity": "warning",
        "field": str(issue["location"]),
        "message": str(issue.get("note") or LANE_MESSAGES[str(issue["code"])]),
        "evidence": evidence,
        "suggestion": suggestion,
        "audit_lane": lane,
        "confidence": issue["confidence"],
        "evidence_validated": bool(issue.get("evidence_validated", True)),
    }


def review_ui_results(
    tasks: list[dict[str, Any]],
    validations: dict[str, dict[str, Any]],
    model: str,
    provider: str = "ollama",
) -> list[dict[str, Any]]:
    incomplete = [lane for lane in LANES if not validations.get(lane, {}).get("complete")]
    if incomplete:
        raise ValueError(f"cannot materialize incomplete lanes: {incomplete}")
    issues_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lane in LANES:
        for issue in validations[lane]["issues"]:
            issues_by_key[str(issue["candidate_key"])].append(issue)
    rows: list[dict[str, Any]] = []
    for task in tasks:
        key = str(task.get("candidate_key") or "")
        issues = issues_by_key.get(key, [])
        by_lane: dict[str, list[dict[str, Any]]] = {
            lane: [issue for issue in issues if issue["lane"] == lane]
            for lane in LANES
        }
        correction, changes, rejected = materialize_ocr_correction(
            task,
            by_lane["ocr_text"],
        )
        findings = [issue_finding(issue) for issue in issues]
        checks = {
            lane: {
                "label": LANE_LABELS[lane],
                "status": (
                    "not_applicable"
                    if key not in set(validations[lane].get("selected_candidate_keys") or [])
                    else "unavailable"
                    if validations[lane].get("unavailable")
                    else "needs_review"
                    if by_lane[lane]
                    else "pass"
                ),
                "issue_count": len(by_lane[lane]),
                "model": model,
                "prompt_version": PROMPT_VERSION,
            }
            for lane in LANES
        }
        if by_lane["visual"]:
            action = "human_review_pdf_visual"
        elif by_lane["group"]:
            action = "review_group"
        elif issues:
            action = "human_review_text"
        else:
            action = "human_can_quick_accept"
        counts = "、".join(
            f"{LANE_LABELS[lane]} {len(by_lane[lane])}"
            for lane in LANES
        )
        status = "needs_review" if issues else "pass"
        confidence = (
            max(float(issue["confidence"]) for issue in issues)
            if issues
            else 0.5
        )
        row: dict[str, Any] = {
            "candidate_key": key,
            "provider": provider,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "status": status,
            "confidence": confidence,
            "confidence_basis": "max_flag_confidence" if issues else "workflow_default_for_no_finding",
            "reason": f"四路初審完成：{counts}。",
            "summary": f"四路初審完成：{counts}。",
            "labels": sorted({str(issue["code"]) for issue in issues}) or ["pass_likely"],
            "recommended_action": action,
            "findings": findings,
            "checks": checks,
            "channel_results": {
                lane: by_lane[lane]
                for lane in LANES
            },
            "suggested_correction": correction,
            "suggested_changes": changes,
            "materialization_warnings": rejected,
        }
        rows.append(row)
    return rows


@dataclass
class OllamaMetrics:
    lane: str
    batch_id: str
    http_status: int | None
    total_latency_ms: float
    first_byte_ms: float | None
    ttft_ms: float | None
    prompt_bytes: int
    response_bytes: int
    thinking_bytes: int
    prompt_eval_count: int | None
    eval_count: int | None
    prompt_eval_duration_ns: int | None
    eval_duration_ns: int | None
    total_duration_ns: int | None
    load_duration_ns: int | None
    done_reason: str | None
    error: str | None


def integer_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def ollama_format_value(
    format_mode: str,
    response_schema_value: dict[str, Any],
) -> dict[str, Any] | str | None:
    if format_mode == "schema":
        return response_schema_value
    if format_mode == "json":
        return "json"
    if format_mode == "prompt-only":
        return None
    raise ValueError(f"unsupported Ollama format mode: {format_mode}")


def ollama_request(
    packet: dict[str, Any],
    model: str,
    num_ctx: int,
    keep_alive: str,
    timeout_seconds: int,
    format_mode: str,
    max_output_tokens: int | None = None,
) -> tuple[str, str, OllamaMetrics]:
    images = [
        base64.b64encode(Path(path).read_bytes()).decode("ascii")
        for path in packet["image_paths"]
    ]
    body: dict[str, Any] = {
        "model": model,
        "system": packet["system"],
        "prompt": packet["prompt"],
        # Compact structured output is easier to delimit as one response. This
        # avoids transport-side concatenation while Ollama's schema constrains
        # the model to one JSON root.
        "stream": False,
        "think": False,
        "keep_alive": keep_alive,
        "options": {
            "temperature": 0,
            "num_ctx": num_ctx,
            "num_predict": (
                max_output_tokens
                if max_output_tokens is not None
                else LANE_MAX_TOKENS[str(packet["lane"])]
            ),
        },
    }
    format_value = ollama_format_value(format_mode, packet["response_schema"])
    if format_value is not None:
        body["format"] = format_value
    if images:
        body["images"] = images
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    started = time.perf_counter()
    first_byte_at: float | None = None
    first_token_at: float | None = None
    response_parts: list[str] = []
    thinking_parts: list[str] = []
    final: dict[str, Any] = {}
    status: int | None = None
    error: str | None = None
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            11434,
            timeout=timeout_seconds,
        )
        connection.request(
            "POST",
            "/api/generate",
            body=encoded,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        first_byte_at = time.perf_counter()
        status = response.status
        if status != 200:
            error = response.read().decode("utf-8", errors="replace")[:2000]
        else:
            payload = response.read().decode("utf-8", errors="replace")
            try:
                event = json.loads(payload)
            except json.JSONDecodeError as exc:
                error = f"invalid_ollama_response:{exc}"
            else:
                if event.get("error"):
                    error = str(event["error"])
                fragment = event.get("response")
                if isinstance(fragment, str) and fragment:
                    response_parts.append(fragment)
                thinking = event.get("thinking")
                if isinstance(thinking, str) and thinking:
                    thinking_parts.append(thinking)
                if event.get("done"):
                    final = event
    except (OSError, http.client.HTTPException) as exc:
        error = str(exc)
    finally:
        if connection is not None:
            connection.close()
    finished = time.perf_counter()
    raw = "".join(response_parts)
    thinking_raw = "".join(thinking_parts)
    metrics = OllamaMetrics(
        lane=str(packet["lane"]),
        batch_id=str(packet["batch_id"]),
        http_status=status,
        total_latency_ms=round((finished - started) * 1000, 3),
        first_byte_ms=(
            round((first_byte_at - started) * 1000, 3)
            if first_byte_at is not None
            else None
        ),
        ttft_ms=(
            round((first_token_at - started) * 1000, 3)
            if first_token_at is not None
            else None
        ),
        prompt_bytes=len(encoded),
        response_bytes=len(raw.encode("utf-8")),
        thinking_bytes=len(thinking_raw.encode("utf-8")),
        prompt_eval_count=integer_or_none(final.get("prompt_eval_count")),
        eval_count=integer_or_none(final.get("eval_count")),
        prompt_eval_duration_ns=integer_or_none(final.get("prompt_eval_duration")),
        eval_duration_ns=integer_or_none(final.get("eval_duration")),
        total_duration_ns=integer_or_none(final.get("total_duration")),
        load_duration_ns=integer_or_none(final.get("load_duration")),
        done_reason=(
            str(final.get("done_reason"))
            if final.get("done_reason") is not None
            else None
        ),
        error=error,
    )
    return raw, thinking_raw, metrics


def packet_for_storage(
    packet: dict[str, Any],
    model: str,
    format_mode: str = "schema",
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    return {
        **packet,
        "model": model,
        "image_count": len(packet["image_paths"]),
        "request_options": {
            "think": False,
            "format_mode": format_mode,
            "stream": False,
            "temperature": 0,
            "num_predict": (
                max_output_tokens
                if max_output_tokens is not None
                else LANE_MAX_TOKENS[str(packet["lane"])]
            ),
        },
    }


def select_task_window(
    tasks: list[dict[str, Any]],
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    if offset < 0:
        raise ValueError("offset must be non-negative")
    if limit < 0:
        raise ValueError("limit must be non-negative")
    selected = tasks[offset:]
    return selected[:limit] if limit > 0 else selected


def run_local(args: argparse.Namespace) -> dict[str, Any]:
    source_tasks = read_jsonl(args.tasks)
    tasks = select_task_window(source_tasks, args.offset, args.limit)
    if not tasks:
        raise ValueError("no tasks selected")
    keys = [str(task.get("candidate_key") or "") for task in tasks]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("tasks require unique non-empty candidate_key values")
    if args.output_dir.exists():
        raise FileExistsError(f"output directory already exists: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    write_jsonl(args.output_dir / "input_frozen.jsonl", tasks)
    validations: dict[str, dict[str, Any]] = {}
    metrics_rows: list[dict[str, Any]] = []
    for lane in LANES:
        selected_tasks = select_lane_tasks(tasks, lane)
        if not selected_tasks:
            validations[lane] = skipped_lane_validation()
            print(f"{lane}: skipped (no applicable candidates)", flush=True)
            continue
        if lane == "visual" and args.visual_mode == "unavailable":
            validations[lane] = unavailable_visual_validation(
                selected_tasks,
                (
                    "本地模型的 vision 傳遞 probe 失敗；圖片內容尚未經模型檢查，"
                    "請沿用 Review UI 圖片題流程人工核對。"
                ),
            )
            print(
                f"{lane}: unavailable (routed {len(selected_tasks)} candidates to human review)",
                flush=True,
            )
            continue
        packet = build_lane_packet(selected_tasks, lane)
        if args.retry_hint:
            hint = str(args.retry_hint)[:500]
            packet["system"] += (
                "這是結構修復重試。前次驗證錯誤為："
                f"{hint}。只修正輸出結構；仍須重新讀完本批題目。"
            )
            packet["retry_hint"] = hint
        lane_dir = args.output_dir / lane
        write_json(
            lane_dir / "packet.json",
            packet_for_storage(
                packet,
                args.model,
                args.ollama_format,
                args.max_output_tokens,
            ),
        )
        raw, thinking_raw, metrics = ollama_request(
            packet,
            args.model,
            args.num_ctx,
            args.keep_alive,
            args.timeout_seconds,
            args.ollama_format,
            args.max_output_tokens,
        )
        lane_dir.mkdir(parents=True, exist_ok=True)
        (lane_dir / "response.txt").write_text(raw, encoding="utf-8")
        (lane_dir / "thinking.txt").write_text(thinking_raw, encoding="utf-8")
        validation = validate_lane_response(
            packet,
            selected_tasks,
            raw,
            metrics.done_reason,
        )
        if metrics.error:
            validation["complete"] = False
            validation["errors"].append(f"transport:{metrics.error}")
        write_json(lane_dir / "validation.json", validation)
        write_json(lane_dir / "metrics.json", asdict(metrics))
        validations[lane] = validation
        metrics_rows.append(asdict(metrics))
        print(
            f"{lane}: {metrics.total_latency_ms:.0f} ms, "
            f"eval={metrics.eval_count}, issues={validation.get('issue_count', 0)}, "
            f"complete={validation['complete']}",
            flush=True,
        )
    all_complete = all(validations[lane]["complete"] for lane in LANES)
    result_path: str | None = None
    if all_complete:
        results = review_ui_results(tasks, validations, args.model)
        output = args.output_dir / "review_ui_results_preview.jsonl"
        write_jsonl(output, results)
        result_path = str(output)
    summary = {
        "schema_version": "compact_initial_question_audit_run_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "advisory_only": True,
        "writes_review_events": False,
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "source_task_count": len(source_tasks),
        "source_offset": args.offset,
        "requested_limit": args.limit,
        "task_count": len(tasks),
        "candidate_keys": keys,
        "all_lanes_complete": all_complete,
        "lane_validation": {
            lane: {
                "complete": validations[lane]["complete"],
                "issue_count": validations[lane].get("issue_count", 0),
                "dropped_issue_count": validations[lane].get(
                    "dropped_issue_count",
                    0,
                ),
                "filtered_non_issue_count": validations[lane].get(
                    "filtered_non_issue_count",
                    0,
                ),
                "errors": validations[lane]["errors"],
                "warnings": validations[lane].get("warnings", []),
                "selected_count": len(
                    validations[lane].get("selected_candidate_keys") or []
                ),
                "skipped": bool(validations[lane].get("skipped")),
                "unavailable": bool(validations[lane].get("unavailable")),
            }
            for lane in LANES
        },
        "metrics": metrics_rows,
        "review_ui_results_preview": result_path,
        "import_gate": (
            "Preview only. Human approval is still required before any AI event import "
            "or reset_review action."
        ),
    }
    write_json(args.output_dir / "summary.json", summary)
    return summary


def materialize_external(args: argparse.Namespace) -> dict[str, Any]:
    tasks = read_jsonl(args.tasks)
    if args.limit > 0:
        tasks = tasks[: args.limit]
    if not tasks:
        raise ValueError("no tasks selected")
    if args.output_dir.exists():
        raise FileExistsError(f"output directory already exists: {args.output_dir}")
    combined = extract_json(args.response.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True)
    write_jsonl(args.output_dir / "input_frozen.jsonl", tasks)
    validations: dict[str, dict[str, Any]] = {}
    for lane in ("ocr_text", "meaning"):
        selected_tasks = select_lane_tasks(tasks, lane)
        value = combined.get(lane)
        if not isinstance(value, dict):
            validations[lane] = {
                "complete": False,
                "selected_candidate_keys": [
                    str(task.get("candidate_key") or "")
                    for task in selected_tasks
                ],
                "errors": [f"missing_{lane}_object"],
                "warnings": [],
                "issues": [],
            }
            continue
        packet = build_lane_packet(selected_tasks, lane)
        packet["batch_id"] = f"{lane}-external-{len(selected_tasks)}"
        validations[lane] = validate_lane_response(
            packet,
            selected_tasks,
            canonical_json(value),
            "stop",
        )
    visual_tasks = select_lane_tasks(tasks, "visual")
    validations["visual"] = (
        unavailable_visual_validation(
            visual_tasks,
            (
                "LLM Share 文字 connector 未傳送圖片；圖片內容尚未經模型檢查，"
                "請沿用 Review UI 圖片題流程人工核對。"
            ),
        )
        if visual_tasks
        else skipped_lane_validation()
    )
    group_tasks = select_lane_tasks(tasks, "group")
    validations["group"] = (
        {
            "complete": False,
            "selected_candidate_keys": [
                str(task.get("candidate_key") or "")
                for task in group_tasks
            ],
            "errors": ["group_candidates_require_a_group_response"],
            "warnings": [],
            "issues": [],
        }
        if group_tasks
        else skipped_lane_validation()
    )
    write_json(args.output_dir / "validations.json", validations)
    complete = all(validations[lane].get("complete") for lane in LANES)
    preview_path: str | None = None
    if complete:
        preview = review_ui_results(
            tasks,
            validations,
            args.model,
            provider="llmshare",
        )
        output = args.output_dir / "review_ui_results_preview.jsonl"
        write_jsonl(output, preview)
        preview_path = str(output)
    summary = {
        "schema_version": "compact_external_materialization_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "advisory_only": True,
        "writes_review_events": False,
        "model": args.model,
        "task_count": len(tasks),
        "all_lanes_complete": complete,
        "validations": {
            lane: {
                "complete": bool(validations[lane].get("complete")),
                "issue_count": len(validations[lane].get("issues") or []),
                "errors": validations[lane].get("errors") or [],
                "warnings": validations[lane].get("warnings") or [],
                "unavailable": bool(validations[lane].get("unavailable")),
                "skipped": bool(validations[lane].get("skipped")),
            }
            for lane in LANES
        },
        "review_ui_results_preview": preview_path,
        "imported_to_review_ui": False,
    }
    write_json(args.output_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compact four-lane initial question audit; never writes review events."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser(
        "run-local",
        help="Run all four lanes sequentially through local Ollama.",
    )
    local.add_argument("--tasks", type=Path, required=True)
    local.add_argument("--output-dir", type=Path, required=True)
    local.add_argument("--model", default="qwen3.6:35b-mlx")
    local.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many source tasks before applying --limit.",
    )
    local.add_argument("--limit", type=int, default=8)
    local.add_argument("--num-ctx", type=int, default=32768)
    local.add_argument("--keep-alive", default="30m")
    local.add_argument("--timeout-seconds", type=int, default=600)
    local.add_argument(
        "--max-output-tokens",
        type=int,
        help=(
            "Override the per-lane generation limit. Defaults are 1024 for "
            "OCR/meaning and 768 for visual/group."
        ),
    )
    local.add_argument(
        "--retry-hint",
        help=(
            "Concise validator feedback added only on a structural retry. "
            "The runner records it in the frozen packet."
        ),
    )
    local.add_argument(
        "--ollama-format",
        choices=["schema", "json", "prompt-only"],
        default="schema",
        help=(
            "Ollama structured-output transport. prompt-only omits the Ollama "
            "format option but keeps the same prompt and local response validation."
        ),
    )
    local.add_argument(
        "--visual-mode",
        choices=["vision", "unavailable"],
        default="vision",
        help=(
            "Use vision inference, or explicitly route visual candidates to human review "
            "when the selected model's image transport probe failed."
        ),
    )
    external = commands.add_parser(
        "materialize-external",
        help="Validate a saved compact OCR/meaning response and build a Review UI preview.",
    )
    external.add_argument("--tasks", type=Path, required=True)
    external.add_argument("--response", type=Path, required=True)
    external.add_argument("--output-dir", type=Path, required=True)
    external.add_argument("--model", required=True)
    external.add_argument("--limit", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "run-local":
        if args.max_output_tokens is not None and args.max_output_tokens <= 0:
            raise ValueError("max-output-tokens must be positive")
        summary = run_local(args)
    elif args.command == "materialize-external":
        summary = materialize_external(args)
    else:
        raise ValueError(args.command)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
