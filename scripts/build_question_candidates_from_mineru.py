#!/usr/bin/env python3
"""
Build question candidate JSONL and QA flags from completed MinerU markdown.

This is an ingestion preflight tool. It does not write to PostgreSQL and does
not mutate official PDFs or MinerU output. The output is intended for the
human-in-the-loop review UI and later formal ingestion.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
PAIR_INDEX_DIR = ASSET_ROOT / "Registry" / "paired_indexes"
OUTPUT_ROOT = ASSET_ROOT / "30_normalized_items"
MINERU_ROOT = ASSET_ROOT / "20_mineru_output"
PARSER_VERSION = "moex_mineru_candidate_v0.8"

OPTION_RE = re.compile(r"(?m)^\s*(?:[（(]([A-E])[\)）]|([A-E])[\.\、．·]|([A-E])-(?=[a-z]))\s*")
INLINE_OPTION_RE = re.compile(
    r"(?m)(^|\s+)(?:\$\s*)?(?:"
    r"(?:\\(?:mathrm|mathbf|text)\{)?[（(]([A-E])[\)）]"
    r"|\\(?:mathrm|mathbf|text)\{([A-E])\}[\.\、．·]"
    r"|(?:\\(?:mathrm|mathbf|text)\{)?([A-E])[\.\、．·]"
    r"|(?:\\(?:mathrm|mathbf|text)\{)?([A-E])-(?=[a-z])"
    r")\s*"
)
# A question number may be written as `33.` or legacy `33 題幹`, but
# ultrasound/text-image OCR often starts lines with decimals such as `1.7 3.4`.
# Do not treat decimal values as question starts.
QUESTION_START_RE_MODERN = re.compile(r"(?m)^(\d{1,3})(?:[\.．](?!\d)|、)\s*(\S.*)$")
QUESTION_START_RE_LEGACY = re.compile(r"(?m)^(\d{1,3})(?:[\.．](?!\d)\s*|、\s*|\s+)(\S.*)$")
IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HTML_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
DETAILS_BLOCK_RE = re.compile(r"<details\b.*?</details>", re.S | re.I)
STANDALONE_IMAGE_RE = re.compile(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*$")
GROUP_RANGE_RE = re.compile(r"第\s*(\d{1,3})\s*(?:至|到|~|～|-|－)\s*(\d{1,3})\s*題")
GROUP_PREFIX_RANGE_RE = re.compile(r"^\s*(\d{1,3})\s*(?:-|－|~|～|至|到)\s*(\d{1,3})\s*(?=\S)")
GROUP_COUNT_RE = re.compile(r"回答下列\s*(\d{1,2})\s*題")
IMAGE_HINT_RE = re.compile(r"(下列圖|如圖|如附圖|附圖|圖示|圖中|圖片|照片|影像如下|X光片|x光片|切片圖|表中|下表|附表|如下表)")
EXAM_HEADER_HINT_RE = re.compile(r"(代號|類科名稱|科目名稱|考試時間|座號|本試題|禁止使用電子計算器|單一選擇題)")
SUSPICIOUS_RE = re.compile(r"(�|□|▯|_{3,}|\.{6,}|。{3,})")
MARKUP_HINT_RE = re.compile(r"(<sub>|<sup>|\\[a-zA-Z]+|[α-ωΑ-ΩⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|[A-Za-z][0-9][A-Za-z0-9]*|\^[0-9+-]+)")
OCR_CHAR_MAP = str.maketrans(
    {
        "锌": "鋅",
        "羟": "羥",
        "钙": "鈣",
        "锰": "錳",
        "减": "減",
        "内": "內",
        "麦": "麩",
        "麸": "麩",
        "黄": "黃",
        "状": "狀",
        "肠": "腸",
        "岛": "島",
        "题": "題",
        "脱": "脫",
        "氢": "氫",
        "铵": "銨",
        "巯": "巰",
        "则": "則",
        "恶": "惡",
        "溃": "潰",
        "疡": "瘍",
        "鳞": "鱗",
        "静": "靜",
        "婴": "嬰",
        "脏": "臟",
        "肾": "腎",
        "鉯": "鈀",
    }
)
OCR_PHRASE_MAP = {
    "鎌": "鎂",
    "罪固酮": "睪固酮",
    "罁固酮": "睪固酮",
    "二氢": "二氫",
    "萘鹼酸": "菸鹼酸",
    "蘸鹼酸": "菸鹼酸",
    "核黄素": "核黃素",
    "鉴胺素": "鈷胺素",
    "厥氧": "厭氧",
    "鶥鵡熱": "鸚鵡熱",
    "麗胺酸（glutamic acid）": "麩胺酸（glutamic acid）",
    "麗胺酸 (glutamic acid)": "麩胺酸 (glutamic acid)",
    "繳胺酸": "纈胺酸",
    "繊胺酸": "纈胺酸",
    "参考": "參考",
    "stansard": "standard",
    "欽": "鈥",
    "鉝": "銫",
    "鉷（Co）": "鈷（Co）",
    "鉷": "鈷",
    "麗胺基硫還原酶": "麩胺基硫還原酶",
    "釔 (Gadolinium, Gd)": "釓 (Gadolinium, Gd)",
    "釔（Gadolinium, Gd）": "釓（Gadolinium, Gd）",
    "氩離子": "氫離子",
    "氩離子": "氫離子",
    "氩离子": "氫離子",
    "氩離": "氫離",
    "上腔静脈": "上腔靜脈",
}

AMINO_ACID_ANCHORS = [
    (re.compile(r"\bglycine\b", re.I), ["甘胺酸"]),
    (re.compile(r"\balanine\b", re.I), ["丙胺酸"]),
    (re.compile(r"\bvaline\b", re.I), ["纈胺酸"]),
    (re.compile(r"\bleucine\b", re.I), ["白胺酸", "亮胺酸"]),
    (re.compile(r"\bisoleucine\b", re.I), ["異白胺酸", "異亮胺酸"]),
    (re.compile(r"\bserine\b", re.I), ["絲胺酸"]),
    (re.compile(r"\bthreonine\b", re.I), ["蘇胺酸"]),
    (re.compile(r"\bcysteine\b", re.I), ["半胱胺酸"]),
    (re.compile(r"\bmethionine\b", re.I), ["甲硫胺酸"]),
    (re.compile(r"\baspart(?:ic acid|ate)\b", re.I), ["天門冬胺酸"]),
    (re.compile(r"\bglutam(?:ic acid|ate)\b", re.I), ["麩胺酸", "穀胺酸", "谷胺酸"]),
    (re.compile(r"\basparagine\b", re.I), ["天門冬醯胺"]),
    (re.compile(r"\bglutamine\b", re.I), ["麩醯胺", "麩胺醯胺", "谷氨醯胺"]),
    (re.compile(r"\blysine\b", re.I), ["離胺酸", "賴胺酸"]),
    (re.compile(r"\barginine\b", re.I), ["精胺酸"]),
    (re.compile(r"\bhistidine\b", re.I), ["組胺酸"]),
    (re.compile(r"\bphenylalanine\b", re.I), ["苯丙胺酸"]),
    (re.compile(r"\btyrosine\b", re.I), ["酪胺酸"]),
    (re.compile(r"\btryptophan\b", re.I), ["色胺酸"]),
    (re.compile(r"\bproline\b", re.I), ["脯胺酸"]),
]

LATEX_SYMBOL_MAP = {
    "alpha": "α",
    "beta": "β",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "gamma": "γ",
    "kappa": "κ",
    "lambda": "λ",
    "theta": "θ",
    "chi": "χ",
    "mu": "μ",
    "zeta": "ζ",
}

SUBSCRIPT_DIGITS = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")
SUPERSCRIPT_DIGITS = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
SUPERSCRIPT_LETTERS = str.maketrans({"a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "k": "ᵏ", "m": "ᵐ", "n": "ⁿ", "s": "ˢ", "u": "ᵘ", "w": "ʷ"})
SUBSCRIPT_LETTERS = str.maketrans({"a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ"})
ORDINAL_SUPERSCRIPTS = {
    "st": "ˢᵗ",
    "nd": "ⁿᵈ",
    "rd": "ʳᵈ",
    "th": "ᵗʰ",
}

BLOOD_GROUP_SUPERSCRIPT_RE = re.compile(
    r"(?<![A-Za-z])(?P<prefix>(?:[Aa]nti|ANTI|Anti)\s*[- ]\s*)?"
    r"(?P<system>Fy|Jk|JK|Le|Lu|Di|Mi|Kp|C)"
    r"\s*(?P<suffix>[abcw])(?![A-Za-z])"
)
ABO_SUBTYPE_RE = re.compile(r"\b(?P<abo>[ABO])\s*(?:_\{?(?P<braced>h|el|end|m)\}?|(?P<plain>h|el|end|m))\b", re.I)


@dataclass
class Issue:
    candidate_key: str
    source_registry_key: str
    question_number: str
    issue_code: str
    severity: str
    message: str
    issue_json: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build question candidate JSONL and QA flags from MinerU output.")
    parser.add_argument("--pair-index", type=Path, default=latest_path(PAIR_INDEX_DIR, "question_answer_pairs_detail__*.csv"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--limit", type=int, default=0, help="Limit paired documents for smoke tests. 0 means no limit.")
    parser.add_argument("--registry-key", action="append", default=[], help="Only process selected question registry key(s).")
    parser.add_argument("--group-name", action="append", default=[], help="Only process selected group_name values.")
    parser.add_argument("--include-needs-review", action="store_true", help="Keep candidates even when they have warning issues.")
    return parser.parse_args()


def latest_path(directory: Path, pattern: str) -> Path:
    paths = sorted(directory.glob(pattern))
    if not paths:
        raise SystemExit(f"No file found: {directory}/{pattern}")
    return paths[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if value.startswith("國考題資料夾/"):
        return PROJECT_ROOT / value
    return ASSET_ROOT / value


def relative_to_project(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def mineru_md_for_pdf(pdf_value: str) -> Path | None:
    if not pdf_value:
        return None
    pdf_path = project_path(pdf_value)
    try:
        rel = pdf_path.resolve().relative_to((ASSET_ROOT / "10_official_pdf").resolve())
    except ValueError:
        rel = Path(str(pdf_value).replace("10_official_pdf/", "", 1))
    parent = MINERU_ROOT / rel.with_suffix("")
    stem = pdf_path.stem
    for mode in ("vlm", "hybrid_auto", "ocr"):
        candidate = parent / mode / f"{stem}.md"
        if candidate.exists():
            return candidate
    matches = sorted(parent.glob(f"**/{stem}.md"))
    return matches[0] if matches else None


def normalize_text(value: str) -> str:
    value = DETAILS_BLOCK_RE.sub("", value)
    value = STANDALONE_IMAGE_RE.sub("", value)
    for source, target in OCR_PHRASE_MAP.items():
        value = value.replace(source, target)
    value = value.translate(OCR_CHAR_MAP)
    value = re.sub(r"\\,\s*", " ", value)
    value = normalize_science_markup(value)
    lines = [line.strip() for line in value.replace("\u3000", " ").splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def ordinal_suffix(value: str) -> str:
    return ORDINAL_SUPERSCRIPTS.get(value, value)


def normalize_science_markup(value: str) -> str:
    """Render common MinerU/LaTeX biomedical notation as review-friendly text."""
    value = re.sub(r"\$?\\mu\$?\s*([a-zA-Z])", r"μ\1", value)
    value = re.sub(r"\\mu\s+([a-zA-Z])", r"μ\1", value)
    symbol_pattern = "|".join(sorted(LATEX_SYMBOL_MAP, key=len, reverse=True))
    value = re.sub(rf"\$?\\({symbol_pattern})\$?\s*_\{{?(\d+)\}}?", lambda m: f"{LATEX_SYMBOL_MAP[m.group(1)]}{m.group(2)}", value)
    value = re.sub(rf"\$?\\({symbol_pattern})\$?\s+(\d+)", lambda m: f"{LATEX_SYMBOL_MAP[m.group(1)]}{m.group(2)}", value)
    value = re.sub(rf"\$?\\({symbol_pattern})\$?", lambda m: LATEX_SYMBOL_MAP[m.group(1)], value)
    value = re.sub(r"\\(?:mathbf|mathrm|mathit|text)\{([^{}]+)\}", r"\1", value)
    value = value.replace(r"\triangle", "△")
    value = value.replace(r"\%", "%")
    value = re.sub(r"(\d+(?:\.\d+)?)\s*\$?\s*\^\{(?:\\mathrm\{)?(st|nd|rd|th)(?:\})?\}", lambda m: f"{m.group(1)}{ordinal_suffix(m.group(2))}", value)
    value = re.sub(r"\$?\s*(-?\d+(?:\.\d+)?)\s*\$?\s*\^\s*\{?\\circ\}?\s*\$?\s*(?:\\mathrm\{C\}|C)", r"\1℃", value)
    value = re.sub(r"\$?\s*(-?\d+(?:\.\d+)?)\s*(?:°|˚)\s*C\b", r"\1℃", value)
    value = re.sub(r"\^\s*\{?\\circ\}?\s*(?:\\mathrm\{C\}|C)", "℃", value)
    value = re.sub(r"(?:°|˚)\s*C\b", "℃", value)
    value = re.sub(r"\^\s*\{?\\circ\}?", "°", value)
    value = re.sub(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)℃", r"\1-\2℃", value)
    value = value.replace(r"\rightarrow", "→").replace(r"\to", "→")
    value = value.replace(r"\uparrow", "↑").replace(r"\downarrow", "↓")
    value = value.replace(r"\left", "").replace(r"\right", "")
    value = value.replace(r"\times", "×").replace(r"\cdot", "·")
    value = value.replace(r"\log", "log")
    value = value.replace(r"\sim", "～").replace("∼", "～")
    value = re.sub(r"\\mathrm\{([^{}]+)\}", r"\1", value)
    value = value.replace(r"\geq", "≥").replace(r"\leq", "≤")
    # MinerU/Markdown may escape biomedical allele and variant names such as
    # HLA-B\*5801, CYP2D6\*2, or delE746\_A750. These backslashes are display
    # artifacts, not part of the official notation.
    value = re.sub(r"(?<=[A-Za-z0-9])\\([*_])(?=[A-Za-z0-9])", r"\1", value)
    value = re.sub(r"\{\}\s*(?=[⁰¹²³⁴⁵⁶⁷⁸⁹]|\d+\s*[A-Z][a-z]?)", "", value)
    value = re.sub(r"\b(3|14|32|35|51|57|59|75|99|111|123|125|131)\s*(H|C|Cr|Co|Fe|Se|Tc|In|I|P|S)\b", lambda m: f"{m.group(1).translate(SUPERSCRIPT_DIGITS)}{m.group(2)}", value)
    value = re.sub(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]+)\s+(H|C|Cr|Co|Fe|Se|Tc|In|I|P|S)\b", r"\1\2", value)
    value = re.sub(r"\b(LD|ID)\\*([₀₁₂₃₄₅₆₇₈₉0-9]+)\\*", lambda m: f"{m.group(1)}{m.group(2).translate(SUBSCRIPT_DIGITS)}", value)
    value = re.sub(r"\b(LD|ID)\s*50\b", r"\1₅₀", value)
    value = re.sub(r"\b(CD\d+)\s*([+-])(?=\s|$|[，。,；;、）)])", lambda m: f"{m.group(1)}{m.group(2).translate(SUPERSCRIPT_DIGITS)}", value)
    value = re.sub(r"\bPrP(sc|c)\b", lambda m: "PrP" + "".join(ch.translate(SUPERSCRIPT_LETTERS) for ch in m.group(1)), value)
    value = value.replace("T_{1/2}", "T₁/₂")
    value = value.replace("t_{1/2}", "t₁/₂")
    value = value.replace(r"V_{\max}", "Vmax").replace(r"V_{max}", "Vmax")
    value = re.sub(r"\^\{\s*([0-9]+)\s*([+-])\s*\}", lambda m: f"{m.group(1)}{m.group(2)}".translate(SUPERSCRIPT_DIGITS), value)
    value = re.sub(r"_\{([0-9A-Za-z+-.]+)\}", lambda m: m.group(1).translate(SUBSCRIPT_DIGITS), value)
    value = re.sub(r"_\{([A-Za-z]+[₀₁₂₃₄₅₆₇₈₉₊₋]+)\}", r"\1", value)
    value = re.sub(r"\^\{([0-9A-Za-z+-]+)\}", lambda m: m.group(1).translate(SUPERSCRIPT_DIGITS), value)
    value = re.sub(r"_([0-9+-])", lambda m: m.group(1).translate(SUBSCRIPT_DIGITS), value)
    value = re.sub(r"\^([0-9+-])", lambda m: m.group(1).translate(SUPERSCRIPT_DIGITS), value)
    value = re.sub(r"\b2₂S\b", "2₂s", value)
    value = value.replace("R₄S", "R₄s")
    value = re.sub(r"\b4₁S\b", "4₁s", value)
    value = re.sub(r"\b10[Xx]\b", "10ₓ", value)
    value = re.sub(r"\\mathrm\{([^{}]+)\}", r"\1", value)
    value = re.sub(r"(?<=\d)\\\s+(?=[A-Za-zμ])", " ", value)
    value = re.sub(r"\$([^$]+)\$", r"\1", value)
    value = value.replace("$", "")
    value = re.sub(r"(?<!\\)\}\s*$", "", value)
    value = re.sub(r"([α-ωΑ-Ω]\d*)\s*-\s*", r"\1-", value)
    value = re.sub(r"\(\s*([α-ωΑ-Ω]\d*)\s*-\s*", r"(\1-", value)
    value = re.sub(r"([α-ωΑ-Ω])\s+([\u4e00-\u9fff])", r"\1\2", value)
    value = re.sub(r"([A-Za-z])\s+([₀₁₂₃₄₅₆₇₈₉])", r"\1\2", value)
    value = re.sub(r"（\s*([^（）]+?)\s*）", r"（\1）", value)
    value = re.sub(r"(?<=[₀₁₂₃₄₅₆₇₈₉])\s+(?=[A-Z])", "", value)
    value = re.sub(r"\b([μmunp])\s+g\b", r"\1g", value)
    value = re.sub(r"μ\s*m\b", "μm", value)
    value = re.sub(r"\s+([μmunp]?mol|[μmunp]?g|mL|dL|L|cm|m)\b", r" \1", value)
    value = re.sub(r"\b([μmunp]?g)\s*/\s*(dL|mL|L)\b", r"\1/\2", value)
    value = re.sub(r"\b(MΩ)\s*/\s*(cm)\b", r"\1/\2", value)
    value = re.sub(r"\b([pP]CO)2\b", r"\1₂", value)
    value = re.sub(r"\b([pP])\s*O₂\b", r"\1O₂", value)
    value = re.sub(r"\b([pP])\s*CO₂\b", r"\1CO₂", value)
    value = re.sub(r"\bDL\s+CO\b", "DLCO", value, flags=re.I)
    value = re.sub(r"\bSO\s*₂\b", "SO₂", value)
    value = re.sub(r"\bFEV\s*₁(?:\.₀)?\s*%\b", "FEV₁%", value)
    value = re.sub(r"\bFEV\s*₁(?:\.₀)?\s*/\s*FVC\b", "FEV₁/FVC", value)
    value = re.sub(r"\s+([，。；：、])", r"\1", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = normalize_blood_group_markup(value)
    value = value.replace("R₄S", "R₄s")
    value = value.replace(r"\~", "～")
    value = re.sub(r"\s*[~～]\s*(?=mmHg\b)", " ", value)
    return value


def normalize_blood_group_markup(value: str) -> str:
    """Normalize frequent hematology/blood-bank superscript and subtype OCR forms."""
    if not value:
        return value

    def superscript(match: re.Match[str]) -> str:
        prefix = re.sub(r"\s+", "", match.group("prefix") or "")
        if prefix:
            prefix = prefix.replace("ANTI", "Anti").replace("anti", "anti")
            prefix = re.sub(r"(?i)^anti", "Anti", prefix)
            prefix = "Anti-"
        system = match.group("system")
        if system == "JK":
            system = "Jk"
        suffix = match.group("suffix").lower().translate(SUPERSCRIPT_LETTERS)
        return f"{prefix}{system}{suffix}"

    value = BLOOD_GROUP_SUPERSCRIPT_RE.sub(superscript, value)
    value = re.sub(r"\b([Aa]nti)\s*-\s*P\s*₁\b", lambda m: f"{m.group(1).capitalize()}-P₁", value)
    value = re.sub(r"\b([Aa]nti)-PP₁P\s*k\b", lambda m: f"{m.group(1).capitalize()}-PP₁Pᵏ", value)
    value = re.sub(r"\bweak\s+D\s*\(\s*D\s*u\s*\)", "weak D (Dᵘ)", value, flags=re.I)
    value = re.sub(r"\bD\s+u\b", "Dᵘ", value)
    value = re.sub(r"\bRh\s*null\b", r"Rh_{null}", value, flags=re.I)
    value = re.sub(r"\b([ABO])\s*_\s*h\b", lambda m: f"{m.group(1)}ₕ", value, flags=re.I)
    value = re.sub(r"\b([AO])\s*([12])\b(?=\s*(?:cells?|cell|亞型|血型|抗原|subgroup|subtype))", lambda m: f"{m.group(1)}{m.group(2).translate(SUBSCRIPT_DIGITS)}", value, flags=re.I)

    def abo_subtype(match: re.Match[str]) -> str:
        abo = match.group("abo").upper()
        suffix = (match.group("braced") or match.group("plain") or "").lower()
        if suffix == "h":
            return f"{abo}ₕ"
        if suffix == "m":
            return f"{abo}ₘ"
        return f"{abo}_{{{suffix}}}"

    value = ABO_SUBTYPE_RE.sub(abo_subtype, value)
    value = re.sub(r"\b(BFU|CFU)\s*E\b", r"\1_{E}", value)
    value = re.sub(r"\blate\s+BFU\s*E\b", r"late BFU_{E}", value, flags=re.I)
    value = re.sub(r"\bFactor\s+V_\{\\text\{Leiden\}\}", "Factor V_{Leiden}", value)
    value = re.sub(r"\bPFA-100\s*\^\{®\}", "PFA-100®", value)
    value = re.sub(r"\bSe\s*w(?=[⁰¹²³⁴⁵⁶⁷⁸⁹0-9])", "Seʷ", value)
    return value


def amino_acid_anchor_suspects(text: str) -> list[dict[str, Any]]:
    """Find bilingual amino-acid terms whose Chinese side may be OCR-damaged."""
    suspects: list[dict[str, Any]] = []
    if not text:
        return suspects
    for pattern, expected_terms in AMINO_ACID_ANCHORS:
        for match in pattern.finditer(text):
            if any(term in text for term in expected_terms):
                continue
            start = max(0, match.start() - 24)
            end = min(len(text), match.end() + 24)
            window = text[start:end]
            before_anchor = text[max(0, match.start() - 16) : match.start()]
            has_translation_like_prefix = re.search(r"(胺酸|氨酸|醯胺|酰胺|胺|氨|酸|醯|酰|硫|苯|羥|羟|色|酪|纈|缬|離|离|賴|赖|組|组|精|脯|絲|丝|蘇|苏|白|亮|丙|甘)", before_anchor)
            if not has_translation_like_prefix:
                continue
            suspects.append(
                {
                    "english_anchor": match.group(0),
                    "expected_chinese": expected_terms,
                    "context": window,
                }
            )
    return suspects


def collect_image_refs(text: str, md_path: Path) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for raw in IMAGE_REF_RE.findall(text) + HTML_IMG_RE.findall(text):
        image_path = (md_path.parent / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        refs.append(
            {
                "raw_ref": raw,
                "path": str(image_path),
                "relative_path": relative_to_project(image_path),
                "exists": image_path.exists(),
                "bytes": image_path.stat().st_size if image_path.exists() else None,
            }
        )
    return refs


def option_markers(body: str) -> list[re.Match[str]]:
    markers = list(INLINE_OPTION_RE.finditer(body))
    if not markers:
        return []
    best: list[re.Match[str]] = []
    best_score: tuple[int, int, int] = (-1, -1, -1)
    for start_index, start_marker in enumerate(markers):
        seen: set[str] = set()
        candidate: list[re.Match[str]] = []
        for marker in markers[start_index:]:
            label = option_marker_label(marker)
            if label == "E" and re.match(r"\s*coli\b", body[marker.end() :], re.I):
                continue
            if label not in "ABCDE":
                continue
            if label in seen:
                if option_marker_is_hyphen_word(marker) or option_marker_is_inline_list_item(marker):
                    continue
                break
            seen.add(label)
            candidate.append(marker)
        labels = [option_marker_label(marker) for marker in candidate]
        has_abcd = all(label in labels for label in "ABCD")
        starts_with_a = labels[:1] == ["A"]
        # Prefer the real A-D sequence over a leading organism name such as
        # "E. coli" that can look like an option marker at the start of a stem.
        score = (1 if has_abcd else 0, 1 if starts_with_a else 0, len(candidate))
        if score > best_score:
            best_score = score
            best = candidate
    return best if len(best) >= 2 else list(OPTION_RE.finditer(body))


def match_group(marker: re.Match[str], index: int) -> str | None:
    try:
        return marker.group(index)
    except IndexError:
        return None


def option_marker_label(marker: re.Match[str]) -> str:
    if marker.re is INLINE_OPTION_RE:
        return match_group(marker, 2) or match_group(marker, 3) or match_group(marker, 4) or match_group(marker, 5) or ""
    return match_group(marker, 1) or match_group(marker, 2) or match_group(marker, 3) or ""


def option_marker_is_hyphen_word(marker: re.Match[str]) -> bool:
    return marker.re is INLINE_OPTION_RE and bool(match_group(marker, 5))


def option_marker_is_inline_list_item(marker: re.Match[str]) -> bool:
    text = marker.group(0).strip()
    return bool(re.fullmatch(r"[A-E]、", text))


def option_marker_start(marker: re.Match[str]) -> int:
    return marker.start(0)


def strip_leading_question_number_marker(text: str, number: str) -> str:
    try:
        n = str(int(number))
    except ValueError:
        return text
    superscript_n = n.translate(SUPERSCRIPT_DIGITS)
    patterns = [
        rf"^\s*{re.escape(n)}\s*(?:[\.．、·\-－]\s*)",
        rf"^\s*{re.escape(superscript_n)}\s*(?:[\.．、·\-－]\s*)",
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, count=1)
    return cleaned.strip()


def parse_question_block(number: str, body: str, md_path: Path) -> dict[str, Any]:
    markers = option_markers(body)
    stem = normalize_text(body[: option_marker_start(markers[0])] if markers else body)
    stem = strip_leading_question_number_marker(stem, number)
    options: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        label = option_marker_label(marker)
        start = marker.end()
        end = option_marker_start(markers[index + 1]) if index + 1 < len(markers) else len(body)
        option_text = normalize_text(body[start:end])
        options.append(
            {
                "key": label,
                "raw_order": index + 1,
                "text": option_text,
                "image": None,
                "markup": markup_payload(option_text),
            }
        )
    options.sort(key=lambda item: "ABCDE".find(item["key"]) if item["key"] in "ABCDE" else 99)
    image_refs = collect_image_refs(body, md_path)
    if image_refs and options and len(image_refs) >= len(options):
        if sum(1 for option in options if not option["text"]) >= len(options) - 1:
            for option, image_ref in zip(options, image_refs):
                option["image"] = image_ref
    group_ref = infer_group_ref(stem, number)
    return {
        "question_number": number,
        "stem": stem,
        "stem_markup": markup_payload(stem),
        "options": options,
        "image_refs": image_refs,
        "question_type": "multiple_choice" if options else "unknown",
        "group_ref": group_ref,
        "raw_block": body.strip(),
    }


def is_exam_header_block(body: str) -> bool:
    normalized = normalize_text(body)
    if not normalized:
        return False
    header_hits = len(EXAM_HEADER_HINT_RE.findall(normalized))
    has_options = bool(OPTION_RE.search(body))
    return header_hits >= 3 and not has_options


def split_merged_unnumbered_questions(number: str, body: str) -> list[tuple[str, str]]:
    """Split blocks where MinerU omitted the next question number but kept a second A-D option set."""
    markers = list(INLINE_OPTION_RE.finditer(body)) or list(OPTION_RE.finditer(body))
    labels = [option_marker_label(marker) for marker in markers]
    if len(labels) < 8 or labels[:4] != ["A", "B", "C", "D"] or labels[4:8] != ["A", "B", "C", "D"]:
        return [(number, body)]
    try:
        next_number = str(int(number) + 1)
    except ValueError:
        return [(number, body)]

    between = body[markers[3].end() : option_marker_start(markers[4])]
    line_matches = list(re.finditer(r"(?m)\S.*$", between))
    if len(line_matches) < 2:
        return [(number, body)]
    second_stem = line_matches[-1]
    second_stem_text = second_stem.group(0).strip()
    if not re.search(r"[？?]\s*$", second_stem_text):
        return [(number, body)]

    split_at = markers[3].end() + second_stem.start()
    return [(number, body[:split_at]), (next_number, body[split_at:])]


def split_merged_inline_numbered_question(number: str, body: str) -> list[tuple[str, str]]:
    """Split blocks where the next question number is glued to the previous option text."""
    markers = list(INLINE_OPTION_RE.finditer(body)) or list(OPTION_RE.finditer(body))
    labels = [option_marker_label(marker) for marker in markers]
    if len(labels) < 4 or labels[:4] != ["A", "B", "C", "D"]:
        return [(number, body)]
    try:
        current_number = int(number)
    except ValueError:
        return [(number, body)]
    next_number = current_number + 1
    if not (1 <= next_number <= 200):
        return [(number, body)]

    search_start = markers[3].end()
    pattern = re.compile(rf"(?<!\d)({next_number})(?:[\.．、](?!\d)\s*|\s+)(\S.*)", re.S)
    match = pattern.search(body, search_start)
    if not match:
        return [(number, body)]
    next_stem = match.group(2).strip()
    if not re.search(r"(下列|何者|何種|何項|哪一|為何|？|\?)", next_stem[:80]):
        return [(number, body)]
    return [(number, body[: match.start()]), (str(next_number), body[match.start(2) :])]


def split_merged_questions(number: str, body: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    for split_number, split_body in split_merged_inline_numbered_question(number, body):
        parts.extend(split_merged_unnumbered_questions(split_number, split_body))
    return parts


def question_start_re_for_year(year: str | None) -> re.Pattern[str]:
    try:
        roc_year = int(year or "")
    except ValueError:
        roc_year = 999
    if roc_year <= 105:
        return QUESTION_START_RE_LEGACY
    return QUESTION_START_RE_MODERN


def is_spurious_legacy_numeric_start(match: re.Match[str], year: str | None) -> bool:
    """Ignore chart-axis tick labels that look like legacy question starts."""
    try:
        roc_year = int(year or "")
    except ValueError:
        return False
    if roc_year > 105:
        return False
    tail = normalize_text(match.group(2) or "")
    if not re.fullmatch(r"\d{1,4}", tail):
        return False
    number = normalize_question_number(match.group(1))
    if number is None:
        return False
    value = int(tail)
    return value == 0 or value % 10 == 0 or tail in {"1", "2", "3", "4", "5"}


def is_spurious_legacy_markup_start(match: re.Match[str], year: str | None) -> bool:
    try:
        roc_year = int(year or "")
    except ValueError:
        return False
    if roc_year > 105:
        return False
    tail = normalize_text(match.group(2) or "").lower()
    return tail in {"</details>", "<details>", "</summary>", "text_image"}


def parse_questions(markdown: str, md_path: Path, year: str | None = None) -> list[dict[str, Any]]:
    starts = [
        match
        for match in question_start_re_for_year(year).finditer(markdown)
        if normalize_question_number(match.group(1)) != "0"
        and not is_spurious_legacy_numeric_start(match, year)
        and not is_spurious_legacy_markup_start(match, year)
    ]
    questions: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        number = start.group(1)
        body_start = start.start(2)
        body_end = starts[index + 1].start() if index + 1 < len(starts) else len(markdown)
        body = markdown[body_start:body_end]
        try:
            int(number)
        except ValueError:
            continue
        if is_exam_header_block(body):
            continue
        for split_number, split_body in split_merged_questions(number, body):
            if is_exam_header_block(split_body):
                continue
            questions.append(parse_question_block(split_number, split_body, md_path))
    return questions


def table_cells(row_html: str) -> list[str]:
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.S | re.I)
    clean_cells: list[str] = []
    for cell in cells:
        clean = re.sub(r"<[^>]+>", "", cell)
        clean_cells.append(normalize_text(clean))
    return clean_cells


def parse_correction_notes(markdown: str) -> dict[str, list[str]]:
    notes: dict[str, list[str]] = {}
    for number, values in re.findall(r"第\s*(\d+)\s*題答\s*([A-E/或、]+)\s*者均給分", markdown):
        notes[str(int(number))] = [item for item in re.split(r"或|/|、", values) if item]
    return notes


def normalize_question_number(value: str) -> str | None:
    match = re.search(r"\d{1,3}", str(value))
    if not match:
        return None
    return str(int(match.group(0)))


def parse_answers(markdown: str) -> dict[str, dict[str, Any]]:
    corrections = parse_correction_notes(markdown)
    answers: dict[str, dict[str, Any]] = {}
    for table in re.findall(r"<table.*?>(.*?)</table>", markdown, flags=re.S | re.I):
        rows = re.findall(r"<tr.*?>(.*?)</tr>", table, flags=re.S | re.I)
        parsed_rows = [table_cells(row) for row in rows]
        question_numbers: list[str] | None = None
        answer_values: list[str] | None = None
        for row in parsed_rows:
            if not row:
                continue
            if row[0] in {"題號", "題序"}:
                question_numbers = [cell for cell in row[1:] if cell]
            elif row[0] == "答案":
                answer_values = [cell for cell in row[1:] if cell]
        if not question_numbers or not answer_values:
            continue
        for number, answer in zip(question_numbers, answer_values):
            number_key = normalize_question_number(number)
            if number_key is None:
                continue
            if answer == "#" and number_key in corrections:
                accepted = corrections[number_key]
                answers[number_key] = {
                    "answer": "|".join(accepted),
                    "accepted_values": accepted,
                    "raw_answer": answer,
                    "is_special_correction": True,
                }
            else:
                answers[number_key] = {
                    "answer": answer,
                    "accepted_values": [answer] if answer else [],
                    "raw_answer": answer,
                    "is_special_correction": False,
                }
    return answers


def infer_group_ref(stem: str, number: str) -> str | None:
    for start, end in GROUP_RANGE_RE.findall(stem):
        try:
            n = int(number)
            a = int(start)
            b = int(end)
        except ValueError:
            continue
        if a <= n <= b:
            return f"q{a:03d}-q{b:03d}"
    prefix = GROUP_PREFIX_RANGE_RE.search(stem)
    if prefix:
        try:
            n = int(number)
            a = int(prefix.group(1))
            b = int(prefix.group(2))
        except ValueError:
            return None
        if a <= n <= b:
            return f"q{a:03d}-q{b:03d}"
    count = GROUP_COUNT_RE.search(stem)
    if count:
        try:
            a = int(number)
            b = a + int(count.group(1)) - 1
        except ValueError:
            return None
        if b >= a:
            return f"q{a:03d}-q{b:03d}"
    return None


def propagate_group_refs(parsed_questions: list[dict[str, Any]]) -> None:
    by_number: dict[int, dict[str, Any]] = {}
    for parsed in parsed_questions:
        try:
            by_number[int(parsed["question_number"])] = parsed
        except (KeyError, ValueError):
            continue
    for parsed in list(parsed_questions):
        group_ref = parsed.get("group_ref")
        if not group_ref:
            continue
        match = re.fullmatch(r"q(\d{3})-q(\d{3})", str(group_ref))
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2))
        if not (start <= end <= start + 20):
            continue
        for number in range(start, end + 1):
            if number in by_number:
                by_number[number]["group_ref"] = group_ref


def markup_payload(text: str) -> dict[str, Any] | None:
    if not text or not MARKUP_HINT_RE.search(text):
        return None
    return {
        "plain": text,
        "markup": text,
        "format": "plain-or-mineru-markdown",
        "needs_review": bool(re.search(r"(<sub>|<sup>|\\[a-zA-Z]+|\^[0-9+-]+)", text)),
    }


def add_issue(
    issues: list[Issue],
    candidate_key: str,
    source_registry_key: str,
    question_number: str,
    issue_code: str,
    severity: str,
    message: str,
    issue_json: dict[str, Any] | None = None,
) -> None:
    issues.append(
        Issue(
            candidate_key=candidate_key,
            source_registry_key=source_registry_key,
            question_number=question_number,
            issue_code=issue_code,
            severity=severity,
            message=message,
            issue_json=issue_json or {},
        )
    )


def candidate_issues(candidate: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    key = candidate["candidate_key"]
    source = candidate["source_registry_key"]
    number = str(candidate["question_number"])
    stem = candidate["stem"]
    options = candidate["options"]
    option_text = "\n".join(str(option.get("text") or "") for option in options)
    visible_text = f"{stem}\n{option_text}"
    if not stem:
        add_issue(issues, key, source, number, "empty_stem", "error", "題幹為空。")
    elif len(stem) < 8:
        add_issue(issues, key, source, number, "short_stem", "warning", "題幹過短，可能切題錯誤。", {"length": len(stem)})
    if SUSPICIOUS_RE.search(stem):
        add_issue(issues, key, source, number, "suspicious_ocr_chars", "warning", "題幹含疑似 OCR 亂碼或佔位符。")
    labels = [item["key"] for item in options]
    raw_labels = [item["key"] for item in sorted(options, key=lambda item: item.get("raw_order", 0))]
    doubled_ad = raw_labels[:8] == ["A", "B", "C", "D", "A", "B", "C", "D"] or labels == ["A", "A", "B", "B", "C", "C", "D", "D"]
    if len(options) < 4:
        add_issue(issues, key, source, number, "too_few_options", "error", "選項少於 4 個。", {"option_labels": labels})
    if len(labels) != len(set(labels)):
        add_issue(issues, key, source, number, "duplicate_option_label", "error", "選項標籤重複。", {"option_labels": labels})
        if doubled_ad:
            try:
                next_number = int(number) + 1
            except ValueError:
                next_number = None
            add_issue(
                issues,
                key,
                source,
                number,
                "merged_next_question_suspect",
                "blocked",
                "選項呈現 A-D 後又出現 A-D，應優先判定為下一題黏在本題；修補時必須同時切出/新增下一題，不可只整理本題選項。",
                {
                    "option_labels": labels,
                    "raw_option_labels": raw_labels,
                    "expected_next_question_number": next_number,
                    "repair_policy": "split_current_and_create_next_candidate",
                    "source_of_truth": "official_pdf",
                },
            )
    expected_labels = list("ABCDE"[: len(labels)])
    if len(labels) >= 4 and raw_labels != expected_labels:
        add_issue(
            issues,
            key,
            source,
            number,
            "option_order_unusual",
            "warning",
            "原始 Markdown 選項順序不是 A/B/C/D，可能是雙欄 PDF 或 OCR 閱讀順序造成；parser 已依選項標籤排序，仍建議人工確認。",
            {"raw_option_labels": raw_labels, "normalized_option_labels": labels},
        )
    for option in options:
        if not option["text"] and not option.get("image"):
            add_issue(issues, key, source, number, "empty_option", "error", f"選項 {option['key']} 為空。")
    answer = candidate.get("answer")
    if answer is None:
        add_issue(issues, key, source, number, "missing_answer", "info", "答案 PDF 未找到對應題號；留待答案核對關卡集中排查。")
    elif isinstance(answer, str) and answer and not re.fullmatch(r"[A-E#|/或、]+", answer):
        add_issue(issues, key, source, number, "unexpected_answer_value", "warning", "答案值格式不常見。", {"answer": answer})
    image_refs = candidate.get("image_refs", [])
    has_structured_table = "<table" in stem.lower() and "</table>" in stem.lower()
    if IMAGE_HINT_RE.search(stem) and not image_refs and not has_structured_table:
        add_issue(issues, key, source, number, "image_hint_without_asset", "warning", "題幹提到圖表或影像，但未偵測到圖片引用。")
    for ref in image_refs:
        if not ref["exists"]:
            add_issue(issues, key, source, number, "missing_image_asset", "error", "Markdown 引用的圖片不存在。", ref)
        elif ref.get("bytes") == 0:
            add_issue(issues, key, source, number, "empty_image_asset", "error", "圖片檔案大小為 0。", ref)
    stem_markup = candidate.get("stem_markup") or {}
    if stem_markup.get("needs_review"):
        add_issue(issues, key, source, number, "markup_needs_review", "warning", "題幹含公式、上下標或 markup，建議人工預覽。")
    amino_suspects = amino_acid_anchor_suspects(visible_text)
    if amino_suspects:
        add_issue(
            issues,
            key,
            source,
            number,
            "amino_acid_translation_suspect",
            "warning",
            "題目含胺基酸英文錨點，但附近中文譯名未符合常見對照，可能是 OCR 誤字。",
            {"suspects": amino_suspects},
        )
    return issues


def expected_question_numbers_for_document(candidates: list[dict[str, Any]]) -> set[int]:
    if not candidates:
        return set()
    metadata = candidates[0].get("metadata") or {}
    category = str(metadata.get("normalized_category_name") or metadata.get("group_name") or "")
    if category == "醫事檢驗師":
        return set(range(1, 81))
    return set()


def document_issues(candidates: list[dict[str, Any]], source_registry_key: str) -> list[Issue]:
    issues: list[Issue] = []
    numbers: list[int] = []
    by_number: dict[int, list[str]] = {}
    for candidate in candidates:
        try:
            number = int(candidate["question_number"])
        except ValueError:
            continue
        numbers.append(number)
        by_number.setdefault(number, []).append(candidate["candidate_key"])
    if not numbers:
        return issues
    for number, keys in by_number.items():
        if len(keys) > 1:
            for key in keys:
                add_issue(issues, key, source_registry_key, str(number), "duplicate_question_number", "error", "同一份考卷內題號重複。", {"candidate_keys": keys})
    expected = set(range(min(numbers), max(numbers) + 1))
    missing = sorted(expected - set(numbers))
    if missing:
        key = candidates[0]["candidate_key"]
        add_issue(issues, key, source_registry_key, "", "question_number_gap", "warning", "題號不連續，可能有缺題或 parser 未切到。", {"missing_numbers": missing[:50]})
    fixed_expected = expected_question_numbers_for_document(candidates)
    if fixed_expected:
        key = candidates[0]["candidate_key"]
        actual = {number for number in numbers if number in fixed_expected}
        fixed_missing = sorted(fixed_expected - actual)
        out_of_range = sorted({number for number in numbers if number not in fixed_expected})
        if fixed_missing:
            add_issue(
                issues,
                key,
                source_registry_key,
                "",
                "fixed_exam_question_count_missing",
                "blocked",
                "此類科每份試題應有固定 80 題，但 parser 未切出完整題號。",
                {
                    "expected_range": [1, 80],
                    "expected_count": 80,
                    "actual_distinct_in_range": len(actual),
                    "missing_numbers": fixed_missing[:80],
                },
            )
        if out_of_range:
            add_issue(
                issues,
                key,
                source_registry_key,
                "",
                "fixed_exam_question_count_out_of_range",
                "error",
                "此類科每份試題應只含 1-80 題，parser 切出範圍外題號，可能誤抓 PDF 表頭或頁碼。",
                {
                    "expected_range": [1, 80],
                    "out_of_range_numbers": out_of_range[:80],
                },
            )
    return issues


def quality_status(issues: list[Issue]) -> str:
    severities = {issue.severity for issue in issues}
    if "blocked" in severities or "error" in severities:
        return "blocked"
    if "warning" in severities:
        return "needs_review"
    return "pass"


def build_candidates_for_pair(row: dict[str, str]) -> tuple[list[dict[str, Any]], list[Issue], dict[str, Any]]:
    q_md = mineru_md_for_pdf(row.get("question_pdf") or row.get("question_pdf_relative", ""))
    a_md = mineru_md_for_pdf(row.get("answer_pdf_primary") or row.get("answer_pdf_primary_relative", ""))
    source_registry_key = row["question_registry_key"]
    meta = {
        "pair_key": row["pair_key"],
        "source_registry_key": source_registry_key,
        "question_markdown": str(q_md) if q_md else None,
        "answer_markdown": str(a_md) if a_md else None,
        "status": "planned",
    }
    if q_md is None:
        issue = Issue("", source_registry_key, "", "missing_question_markdown", "blocked", "找不到題目 MinerU markdown。", {})
        meta["status"] = "missing_question_markdown"
        return [], [issue], meta
    if a_md is None:
        issue = Issue("", source_registry_key, "", "missing_answer_markdown", "blocked", "找不到 primary answer MinerU markdown。", {})
        meta["status"] = "missing_answer_markdown"
        return [], [issue], meta
    q_text = q_md.read_text(encoding="utf-8", errors="replace")
    a_text = a_md.read_text(encoding="utf-8", errors="replace")
    parsed_questions = parse_questions(q_text, q_md, row.get("year"))
    propagate_group_refs(parsed_questions)
    answers = parse_answers(a_text)
    candidates: list[dict[str, Any]] = []
    issues: list[Issue] = []
    number_occurrences: dict[str, int] = {}
    for parsed in parsed_questions:
        number = str(int(parsed["question_number"]))
        number_occurrences[number] = number_occurrences.get(number, 0) + 1
        candidate_key = f"{source_registry_key}:q{int(number):03d}"
        if number_occurrences[number] > 1:
            candidate_key = f"{candidate_key}:dup{number_occurrences[number]:02d}"
        answer_payload = answers.get(number)
        candidate = {
            "candidate_key": candidate_key,
            "source_registry_key": source_registry_key,
            "canonical_question_key": f"{source_registry_key}:q{int(number):03d}",
            "question_number_occurrence": number_occurrences[number],
            "answer_source_registry_key": row.get("answer_registry_key_primary") or None,
            "question_number": number,
            "stem": parsed["stem"],
            "stem_markup": parsed["stem_markup"],
            "stem_image": None,
            "options": parsed["options"],
            "answer": answer_payload["answer"] if answer_payload else None,
            "answer_payload": answer_payload,
            "explanation": None,
            "question_type": parsed["question_type"],
            "group_ref": parsed["group_ref"],
            "image_refs": parsed["image_refs"],
            "metadata": {
                "parser_version": PARSER_VERSION,
                "group_name": row.get("group_name"),
                "year": row.get("year"),
                "exam_ordinal": row.get("exam_ordinal"),
                "exam_code": row.get("exam_code"),
                "category_code": row.get("category_code"),
                "subject_code": row.get("subject_code"),
                "official_category_name": row.get("official_category_name"),
                "normalized_category_name": row.get("normalized_category_name"),
                "official_subject_name": row.get("official_subject_name"),
                "normalized_subject_name": row.get("normalized_subject_name"),
                "question_pdf": row.get("question_pdf"),
                "question_pdf_relative": row.get("question_pdf_relative"),
                "answer_pdf_primary": row.get("answer_pdf_primary"),
                "answer_pdf_primary_relative": row.get("answer_pdf_primary_relative"),
                "answer_role_primary": row.get("answer_role_primary"),
                "question_markdown": str(q_md),
                "question_markdown_relative": relative_to_project(q_md),
                "answer_markdown": str(a_md),
                "answer_markdown_relative": relative_to_project(a_md),
                "raw_block": parsed["raw_block"],
            },
        }
        own_issues = candidate_issues(candidate)
        candidate["quality_status"] = quality_status(own_issues)
        candidate["issue_count"] = len(own_issues)
        candidates.append(candidate)
        issues.extend(own_issues)
    if not candidates:
        issues.append(Issue("", source_registry_key, "", "no_questions_parsed", "blocked", "題目 markdown 未解析出任何題目。", {"markdown": str(q_md)}))
    doc_issues = document_issues(candidates, source_registry_key)
    issues.extend(doc_issues)
    issues_by_key: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.candidate_key:
            issues_by_key.setdefault(issue.candidate_key, []).append(issue)
    for candidate in candidates:
        related = issues_by_key.get(candidate["candidate_key"], [])
        candidate["quality_status"] = quality_status(related)
        candidate["issue_count"] = len(related)
    meta["status"] = "ok"
    meta["candidate_count"] = len(candidates)
    meta["issue_count"] = len(issues)
    return candidates, issues, meta


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_issues_csv(path: Path, issues: list[Issue]) -> None:
    fields = ["candidate_key", "source_registry_key", "question_number", "issue_code", "severity", "message", "issue_json"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for issue in issues:
            writer.writerow(
                {
                    "candidate_key": issue.candidate_key,
                    "source_registry_key": issue.source_registry_key,
                    "question_number": issue.question_number,
                    "issue_code": issue.issue_code,
                    "severity": issue.severity,
                    "message": issue.message,
                    "issue_json": json.dumps(issue.issue_json, ensure_ascii=False, sort_keys=True),
                }
            )


def main() -> None:
    args = parse_args()
    rows = read_csv(args.pair_index)
    if args.registry_key:
        wanted = set(args.registry_key)
        rows = [row for row in rows if row.get("question_registry_key") in wanted]
    if args.group_name:
        wanted_groups = set(args.group_name)
        rows = [row for row in rows if row.get("group_name") in wanted_groups]
    rows = [row for row in rows if row.get("pair_status") in {"paired_ans_only", "paired_mod_primary"}]
    if args.limit > 0:
        rows = rows[: args.limit]

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir / "question_candidates" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = run_dir / f"question_candidates__{timestamp}.jsonl"
    issue_path = run_dir / f"question_parse_issues__{timestamp}.csv"
    summary_path = run_dir / f"question_candidate_summary__{timestamp}.json"

    all_candidates: list[dict[str, Any]] = []
    all_issues: list[Issue] = []
    document_summaries: list[dict[str, Any]] = []
    for row in rows:
        candidates, issues, meta = build_candidates_for_pair(row)
        all_candidates.extend(candidates)
        all_issues.extend(issues)
        document_summaries.append(meta)

    write_jsonl(candidate_path, all_candidates)
    write_issues_csv(issue_path, all_issues)
    summary = {
        "parser_version": PARSER_VERSION,
        "pair_index": str(args.pair_index),
        "run_dir": str(run_dir),
        "candidate_jsonl": str(candidate_path),
        "issue_csv": str(issue_path),
        "paired_documents_seen": len(rows),
        "candidate_count": len(all_candidates),
        "issue_count": len(all_issues),
        "quality_status_counts": {
            status: sum(1 for item in all_candidates if item.get("quality_status") == status)
            for status in ("pass", "needs_review", "blocked")
        },
        "document_status_counts": {
            status: sum(1 for item in document_summaries if item.get("status") == status)
            for status in sorted({item.get("status") for item in document_summaries})
        },
        "documents": document_summaries,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
