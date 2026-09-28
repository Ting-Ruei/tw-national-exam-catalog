#!/usr/bin/env python3
"""Compare a GLM-OCR transcription against a reference text layer.

Only paper properties: character multiset, order, line breaks. No semantics.

Usage:
  compare_ocr_to_reference.py --ocr FILE --reference FILE [--json OUT]
"""
import argparse, json, re, unicodedata
from collections import Counter
from difflib import SequenceMatcher

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
PUNCT = "，。、；：？！（）「」『』【】《》〈〉─—－／％﹪…·"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    out = []
    for ch in s:
        if ch.isspace():
            continue
        out.append(ch)
    return "".join(out)


def cjk_only(s: str) -> str:
    return "".join(ch for ch in norm(s) if CJK.match(ch))


def char_report(ocr: str, ref: str) -> dict:
    a, b = Counter(ocr), Counter(ref)
    extra = {c: n for c, n in (a - b).items()}
    missing = {c: n for c, n in (b - a).items()}
    return {
        "ocr_chars": sum(a.values()),
        "ref_chars": sum(b.values()),
        "extra": dict(sorted(extra.items(), key=lambda kv: -kv[1])[:40]),
        "missing": dict(sorted(missing.items(), key=lambda kv: -kv[1])[:40]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--json")
    args = ap.parse_args()

    ocr_raw = open(args.ocr, encoding="utf-8").read()
    ref_raw = open(args.reference, encoding="utf-8").read()
    ocr, ref = norm(ocr_raw), norm(ref_raw)

    r = {
        "files": {"ocr": args.ocr, "reference": args.reference},
        "raw": {"ocr_lines": len(ocr_raw.splitlines()), "ref_lines": len(ref_raw.splitlines())},
        "normalized_length": {"ocr": len(ocr), "ref": len(ref)},
        "similarity": round(SequenceMatcher(None, ocr, ref).ratio(), 4),
        "all_chars": char_report(ocr, ref),
        "han_only": char_report(cjk_only(ocr_raw), cjk_only(ref_raw)),
    }

    # Simplified-only codepoints are a hard error for a Traditional corpus.
    simp = sorted({c for c in ocr if c in "试验剂药体国际发时间问题医学术学"})
    r["suspected_simplified_chars"] = simp
    # Simplified chars that the reference does NOT contain
    refset = set(ref)
    r["simplified_not_in_reference"] = sorted({c for c in ocr if c in "试验剂药体" and c not in refset})

    print(json.dumps(r, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(r, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
