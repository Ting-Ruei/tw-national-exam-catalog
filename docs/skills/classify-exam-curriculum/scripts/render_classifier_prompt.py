#!/usr/bin/env python3
"""Render a self-contained, low-capability-model classification prompt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from taxonomy_support import find_subject, load_taxonomies


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--taxonomy", type=Path, action="append", help="Repeat to override the default taxonomy set.")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def render(taxonomy: dict[str, Any], subject: dict[str, Any]) -> str:
    lines = [
        "你是國考題課程章節分類器。你只做分類，不解題、不修改題目。",
        "",
        "硬性規則：",
        "1. 每次獨立閱讀一題的題幹、全部選項、答案與題組題幹。",
        "2. category、subject、question_key、input_hash 必須原樣複製。",
        "3. 只能從下列代碼選一個 domain_code 與一個 primary_chapter_code。",
        "4. 不可依題號、出題位置、預期題數或平均分配猜分類。",
        "5. evidence_terms 必須是題目中可見的 1 到 5 個短詞。",
        "6. 無法唯一判斷、需要看圖或跨領域難分時，confidence=low 且 review_status=needs_human_review。",
        "7. 只輸出一行 JSON，不要 Markdown，不要補充說明。",
        "",
        "判斷順序：先找題目真正要判斷的對象或方法，再套邊界規則，再選 domain，最後只比較該 domain 內的 chapter。",
        "",
        "邊界規則：",
    ]
    lines.extend(f"- {rule}" for rule in subject.get("boundary_rules", []))
    lines.extend(["", "允許的分類代碼："])
    for domain in subject.get("domains", []):
        lines.extend(
            [
                "",
                f"DOMAIN {domain['code']}｜{domain['label']}",
                f"定義：{domain['definition']}",
                f"常見線索：{'、'.join(domain.get('positive_cues', []))}",
            ]
        )
        if domain.get("exclusions"):
            lines.append(f"排除：{'；'.join(domain['exclusions'])}")
        for chapter in domain.get("chapters", []):
            cues = "、".join(chapter.get("positive_cues", []))
            lines.append(f"- {chapter['code']}｜{chapter['label']}：{chapter['definition']} 線索：{cues}")
    lines.extend(
        [
            "",
            "輸出格式：",
            json.dumps(
                {
                    "question_key": "照抄輸入",
                    "input_hash": "照抄輸入",
                    "taxonomy_version": taxonomy["taxonomy_version"],
                    "category": taxonomy["category"],
                    "subject": "照抄輸入",
                    "domain_code": "只能使用上列 DOMAIN 代碼",
                    "primary_chapter_code": "只能使用所選 DOMAIN 下的 chapter 代碼",
                    "secondary_chapter_codes": [],
                    "confidence": "high|medium|low",
                    "review_status": "ai_suggested|needs_human_review",
                    "evidence_terms": ["題目可見短詞"],
                    "reason": "一句話說明題目考點為何屬於此章節",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    taxonomies = load_taxonomies(args.taxonomy)
    _path, taxonomy, subject = find_subject(taxonomies, args.subject)
    prompt = render(taxonomy, subject)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(prompt, encoding="utf-8")
        print(args.output)
    else:
        print(prompt, end="")


if __name__ == "__main__":
    main()
