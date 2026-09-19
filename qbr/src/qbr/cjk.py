"""CJK source-fidelity counters.

Detector only. Per Protocol §26 a simplified/traditional change is a REVIEW-required
normalization, so nothing here rewrites text: the legacy pipeline's mistake was to keep
adding character maps (`OCR_CHAR_MAP`, `text_normalization_rules.json`) that silently
rewrite candidates, which is exactly how a legit glyph (e.g. the herb name starting with
the same codepoint as the character for "medicine") can be corrupted.
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_ROOT, "data")
SIMPLIFIED_TABLE = os.path.join(DATA_DIR, "simplified_only_chars.txt")

# Conservative seed set (observed in this project's own repair history) used only when
# the generated CC-CEDICT derived table is missing. Report coverage limits are printed
# in that case instead of pretending full coverage.
_SEED_SIMPLIFIED_ONLY = "题数黄麦麸铂钙氧气钠钾镁锰须谐什么检验药剂溶液浓度体积"

_REPLACEMENT = "\ufffd"
_PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u20000-\u2ebef]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
_QUESTION_ANCHOR = re.compile(r"(?:^|\n)[ \t]*(\d{1,3})[ \t]*[.、．]")
_OPTION_MARKER = re.compile(r"(?:^|[\s。.、．])([（(]?[A-Da-d][)）.、．])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A private-use character standing *inside* a word is a glyph the font could not map. One
# standing at a boundary is the label of the thing after it - the typesetter drew its option
# markers from a private-use font rather than printing `A` `B` `C` `D`, which is not damage at
# all. The neighbourhood is what tells them apart, so no list of code points is needed.
_HAN_BEFORE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaffA-Za-z0-9\u00c0-\u024f]")


def load_simplified_only():
    """Return the simplified-only codepoint set plus a coverage note."""
    if os.path.isfile(SIMPLIFIED_TABLE):
        with open(SIMPLIFIED_TABLE, encoding="utf-8") as handle:
            chars = handle.read()
        return set(chars), "generated table"
    return set(_SEED_SIMPLIFIED_ONLY), "seed set only (regenerate with scripts/build_reference_tables.py)"


def is_pua(char):
    code = ord(char)
    for low, high in _PUA_RANGES:
        if low <= code <= high:
            return True
    return False


def audit_text(text, max_samples=12):
    """Measure how trustworthy a PDF text layer is. Returns a plain dict.

    Two private-use figures are reported, not one, because the two mean opposite things.
    `pua_marker_characters` are option labels the typesetter drew from a private-use font -
    present in 1,134 of the corpus's 3,516 papers and entirely benign. `lost_glyph_characters`
    are characters the font could not map, standing inside a word; rendered on the page they
    read correctly (`轉胺\ue2c6` is 轉胺酶) but the text layer has no character for them.

    Only the second is damage. Counting both made `damaged_characters` exceed the triage bound
    on every generation-1 paper, 40 of which were then refused at the gate for `damaged-codepoints`
    - a complaint about the print form's font choice rather than about the paper.
    """
    simplified_only, coverage = load_simplified_only()
    cjk_hits = len(_CJK.findall(text))
    samples = []
    seen = set()
    simplified_total = 0
    marker_total = 0
    lost_total = 0
    for position, char in enumerate(text):
        if char in simplified_only:
            simplified_total += 1
            if char not in seen and len(seen) < max_samples:
                seen.add(char)
                samples.append(
                    "U+%04X %s ctx=%s"
                    % (ord(char), char, text[max(0, position - 8):position + 8].replace("\n", " "))
                )
        elif is_pua(char):
            if position > 0 and _HAN_BEFORE.match(text[position - 1]):
                lost_total += 1
            else:
                marker_total += 1
    control_total = len(_CONTROL.findall(text))
    damaged = text.count(_REPLACEMENT) + lost_total + control_total
    return {
        "characters": len(text),
        "cjk_characters": cjk_hits,
        "replacement_characters": text.count(_REPLACEMENT),
        "pua_characters": marker_total + lost_total,
        "pua_marker_characters": marker_total,
        "lost_glyph_characters": lost_total,
        "control_characters": control_total,
        "damaged_characters": damaged,
        "simplified_only_hits": simplified_total,
        "simplified_only_types": len(seen),
        "simplified_samples": samples,
        "simplified_ratio": (simplified_total / cjk_hits) if cjk_hits else 0.0,
        "damage_ratio": (damaged / len(text)) if text else 0.0,
        "latin_words": len(_LATIN_WORD.findall(text)),
        "question_anchors": [int(x) for x in _QUESTION_ANCHOR.findall(text)],
        "option_markers": len(_OPTION_MARKER.findall(text)),
        "reference_coverage": coverage,
    }
