#!/usr/bin/env python3
"""Run a bounded visual-only model probe over blind balanced-set packets.

The runner reads only ``lane=visual`` packet fields and contact sheets.  It
does not load holdouts, answers, review events, or write candidate state.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from probe_multimodal_model import run_probe


VISION_PROMPT = """你是視覺比對器，只看接觸表像素，不解題、不辨認化合物名稱、不判斷資料庫綁定或審核狀態。OFFICIAL_PDF_REGION 是官方題目範圍，CURRENT 是目前資產。只判斷三件事：CURRENT 是否涵蓋本題全部必要圖形、圖形像素/主要標示是否對應、是否明顯抓到別題圖片。多個化學結構只需計數並比對輪廓與標示，不要逐一描述。只回 JSON：{"vision_received":true,"official_graphics_count":整數或null,"current_graphics_count":整數或null,"completeness":"complete|missing|extra|unclear","pixel_match":"yes|no|unclear","question_fit":"yes|no|unclear","verdict":"pass|mismatch|unclear","reason":"20字內","confidence":0到1}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--provider", choices=("llmshare", "ollama"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-image-width", type=int, default=1200)
    parser.add_argument("--case-ids", default="")
    return parser.parse_args()


def read_visual_packets(path: Path, case_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = str(row.get("case_id") or "")
        if row.get("lane") != "visual" or (case_ids and case_id not in case_ids):
            continue
        visual = row.get("visual_evidence") or {}
        rows.append(
            {
                "case_id": case_id,
                "contact_sheet": str(visual.get("contact_sheet") or ""),
            }
        )
    return rows


def bounded_image(source: Path, destination: Path, max_width: int) -> Path:
    if max_width <= 0:
        return source
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Pillow is required only when --max-image-width requests image resizing"
        ) from exc
    with Image.open(source) as image:
        if image.width <= max_width:
            return source
        resized = image.convert("RGB")
        height = max(1, round(resized.height * max_width / resized.width))
        resized = resized.resize((max_width, height), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        resized.save(destination, format="PNG", optimize=True)
    return destination


def parse_json_content(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def run_one(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    case_id = row["case_id"]
    source = Path(row["contact_sheet"])
    if not source.is_file():
        raise FileNotFoundError(f"missing contact sheet for {case_id}: {source}")
    image = bounded_image(
        source,
        args.output_dir / "input-images" / f"{case_id}.png",
        args.max_image_width,
    )
    probe_args = SimpleNamespace(
        provider=args.provider,
        model=args.model,
        image=image,
        prompt=VISION_PROMPT,
        output=None,
        timeout_seconds=args.timeout_seconds,
        max_tokens=args.max_tokens,
        ollama_base_url="http://127.0.0.1:11434",
    )
    result = run_probe(probe_args)
    parsed = parse_json_content(str(result.get("content") or ""))
    result.update({"case_id": case_id, "strict_json": parsed is not None, "result": parsed})
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    case_ids = {case_id.strip() for case_id in args.case_ids.split(",") if case_id.strip()}
    rows = read_visual_packets(args.packets, case_ids)
    if not rows:
        raise SystemExit("no visual packets matched")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as executor:
        futures = {executor.submit(run_one, row, args): row["case_id"] for row in rows}
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # preserve the rest of a bounded evaluation batch
                errors.append({"case_id": case_id, "error": str(exc)})
                print(f"ERROR {case_id}: {exc}", flush=True)
                continue
            results.append(result)
            write_json(args.output_dir / "cases" / f"{case_id}.json", result)
            print(f"DONE {case_id}: strict_json={result['strict_json']}", flush=True)
    results.sort(key=lambda row: row["case_id"])
    errors.sort(key=lambda row: row["case_id"])
    summary = {
        "schema_version": "national_exam_visual_balance_probe_v1",
        "advisory_only": True,
        "provider": args.provider,
        "model": args.model,
        "case_count": len(rows),
        "completed_count": len(results),
        "strict_json_count": sum(bool(row.get("strict_json")) for row in results),
        "errors": errors,
        "results": results,
    }
    write_json(args.output_dir / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
