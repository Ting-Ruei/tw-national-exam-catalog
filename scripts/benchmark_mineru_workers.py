#!/usr/bin/env python3
"""
Benchmark local MinerU concurrency on a small set of official PDFs.

This script calls the existing MinerU CLI directly and writes only benchmark
artifacts under the project asset folder. It does not alter source PDFs.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "國考題資料夾"
DEFAULT_PDF_ROOT = DEFAULT_ASSET_ROOT / "10_official_pdf" / "by_official_catalog"
DEFAULT_OUTPUT_ROOT = DEFAULT_ASSET_ROOT / "20_mineru_output" / "_worker_benchmark"
DEFAULT_MINERU_BIN = Path.home() / "AI workspace" / "OCR_model" / "MinerU" / "venv_mineru" / "bin" / "mineru"


@dataclass
class TaskResult:
    worker_count: int
    pdf_path: str
    output_dir: str
    returncode: int | None
    elapsed_seconds: float
    md_count: int
    image_count: int
    status: str
    error_tail: str


@dataclass
class MetricSample:
    worker_count: int
    elapsed_seconds: float
    active_root_pids: str
    process_count: int | None
    total_rss_mb: float | None
    vm_free_mb: float | None
    vm_active_mb: float | None
    vm_inactive_mb: float | None
    vm_wired_mb: float | None
    vm_compressed_mb: float | None
    gpu_device_utilization_percent: int | None
    gpu_renderer_utilization_percent: int | None
    gpu_tiler_utilization_percent: int | None
    gpu_in_use_system_memory_mb: float | None
    gpu_alloc_system_memory_mb: float | None
    sampler_notes: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark MinerU worker concurrency.")
    parser.add_argument("--mineru-bin", type=Path, default=DEFAULT_MINERU_BIN)
    parser.add_argument("--pdf-root", type=Path, default=DEFAULT_PDF_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 3, 4])
    parser.add_argument("--sample-count", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--monitor-interval-seconds", type=float, default=5.0)
    parser.add_argument("--pdf", type=Path, action="append", help="Explicit PDF path. Repeatable.")
    parser.add_argument("--keep-output", action="store_true")
    return parser.parse_args()


def choose_samples(pdf_root: Path, explicit: list[Path] | None, sample_count: int) -> list[Path]:
    if explicit:
        samples = [p.resolve() for p in explicit]
    else:
        preferred_roots = [
            pdf_root / "醫事檢驗師",
            pdf_root / "藥師",
            pdf_root / "醫師",
            pdf_root / "中醫師",
        ]
        samples = []
        for root in preferred_roots:
            if root.exists():
                samples.extend(sorted(root.glob("**/*.pdf")))
        if len(samples) < sample_count:
            samples.extend(sorted(pdf_root.glob("**/*.pdf")))

    unique: list[Path] = []
    seen: set[Path] = set()
    for sample in samples:
        if sample in seen or not sample.exists():
            continue
        seen.add(sample)
        unique.append(sample)
        if len(unique) >= sample_count:
            break

    if not unique:
        raise SystemExit(f"No sample PDFs found under {pdf_root}")
    return unique


def count_outputs(output_dir: Path) -> tuple[int, int]:
    md_count = sum(1 for _ in output_dir.glob("**/*.md"))
    image_count = sum(1 for p in output_dir.glob("**/*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    return md_count, image_count


def parse_vm_stat() -> dict[str, float | None]:
    try:
        result = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True)
    except Exception:
        return {
            "vm_free_mb": None,
            "vm_active_mb": None,
            "vm_inactive_mb": None,
            "vm_wired_mb": None,
            "vm_compressed_mb": None,
        }

    page_size = 16384
    values: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if "page size of" in line:
            parts = line.split("page size of", 1)[1].split("bytes", 1)[0].strip()
            try:
                page_size = int(parts)
            except ValueError:
                pass
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        try:
            values[key.strip()] = int(value.strip().strip("."))
        except ValueError:
            continue

    def mb(key: str) -> float | None:
        if key not in values:
            return None
        return round(values[key] * page_size / 1024 / 1024, 1)

    return {
        "vm_free_mb": mb("Pages free"),
        "vm_active_mb": mb("Pages active"),
        "vm_inactive_mb": mb("Pages inactive"),
        "vm_wired_mb": mb("Pages wired down"),
        "vm_compressed_mb": mb("Pages occupied by compressor"),
    }


def parse_ioreg_gpu() -> dict[str, int | float | None]:
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-c", "IOAccelerator", "-d", "1"],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return {
            "gpu_device_utilization_percent": None,
            "gpu_renderer_utilization_percent": None,
            "gpu_tiler_utilization_percent": None,
            "gpu_in_use_system_memory_mb": None,
            "gpu_alloc_system_memory_mb": None,
        }

    text = result.stdout

    def int_value(name: str) -> int | None:
        marker = f'"{name}"='
        start = text.find(marker)
        if start == -1:
            return None
        start += len(marker)
        end = start
        while end < len(text) and text[end].isdigit():
            end += 1
        return int(text[start:end]) if end > start else None

    def bytes_to_mb(name: str) -> float | None:
        value = int_value(name)
        if value is None:
            return None
        return round(value / 1024 / 1024, 1)

    return {
        "gpu_device_utilization_percent": int_value("Device Utilization %"),
        "gpu_renderer_utilization_percent": int_value("Renderer Utilization %"),
        "gpu_tiler_utilization_percent": int_value("Tiler Utilization %"),
        "gpu_in_use_system_memory_mb": bytes_to_mb("In use system memory"),
        "gpu_alloc_system_memory_mb": bytes_to_mb("Alloc system memory"),
    }


def process_tree_rss(root_pids: set[int]) -> tuple[int | None, float | None, str]:
    if not root_pids:
        return 0, 0.0, ""
    try:
        result = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, check=True)
    except Exception as exc:
        return None, None, f"ps_unavailable:{exc}"

    parent_by_pid: dict[int, int] = {}
    rss_by_pid: dict[int, int] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid, ppid, rss_kb = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        parent_by_pid[pid] = ppid
        rss_by_pid[pid] = rss_kb

    descendants: set[int] = set()
    changed = True
    while changed:
        changed = False
        for pid, ppid in parent_by_pid.items():
            if pid in descendants:
                continue
            if pid in root_pids or ppid in root_pids or ppid in descendants:
                descendants.add(pid)
                changed = True

    rss_mb = round(sum(rss_by_pid.get(pid, 0) for pid in descendants) / 1024, 1)
    return len(descendants), rss_mb, ""


def monitor_metrics(
    worker_count: int,
    started: float,
    active_pids: dict[int, str],
    active_lock: threading.Lock,
    stop_event: threading.Event,
    samples: list[MetricSample],
    interval_seconds: float,
) -> None:
    while not stop_event.is_set():
        with active_lock:
            root_pids = set(active_pids)
        process_count, total_rss_mb, process_note = process_tree_rss(root_pids)
        vm = parse_vm_stat()
        gpu = parse_ioreg_gpu()
        samples.append(
            MetricSample(
                worker_count=worker_count,
                elapsed_seconds=round(time.monotonic() - started, 3),
                active_root_pids=" ".join(str(pid) for pid in sorted(root_pids)),
                process_count=process_count,
                total_rss_mb=total_rss_mb,
                vm_free_mb=vm["vm_free_mb"],
                vm_active_mb=vm["vm_active_mb"],
                vm_inactive_mb=vm["vm_inactive_mb"],
                vm_wired_mb=vm["vm_wired_mb"],
                vm_compressed_mb=vm["vm_compressed_mb"],
                gpu_device_utilization_percent=gpu["gpu_device_utilization_percent"],
                gpu_renderer_utilization_percent=gpu["gpu_renderer_utilization_percent"],
                gpu_tiler_utilization_percent=gpu["gpu_tiler_utilization_percent"],
                gpu_in_use_system_memory_mb=gpu["gpu_in_use_system_memory_mb"],
                gpu_alloc_system_memory_mb=gpu["gpu_alloc_system_memory_mb"],
                sampler_notes=process_note,
            )
        )
        stop_event.wait(interval_seconds)


def run_one(
    mineru_bin: Path,
    pdf_path: Path,
    output_dir: Path,
    worker_count: int,
    timeout_seconds: int,
    active_pids: dict[int, str],
    active_lock: threading.Lock,
) -> TaskResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    cmd = [
        str(mineru_bin),
        "-p",
        str(pdf_path),
        "-o",
        str(output_dir),
        "-m",
        "ocr",
        "-b",
        "vlm-auto-engine",
    ]

    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with active_lock:
            active_pids[proc.pid] = str(pdf_path)
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        elapsed = time.monotonic() - started
        md_count, image_count = count_outputs(output_dir)
        status = "ok" if proc.returncode == 0 and md_count > 0 else "error"
        return TaskResult(
            worker_count=worker_count,
            pdf_path=str(pdf_path),
            output_dir=str(output_dir),
            returncode=proc.returncode,
            elapsed_seconds=round(elapsed, 3),
            md_count=md_count,
            image_count=image_count,
            status=status,
            error_tail=(stderr or stdout)[-1000:],
        )
    except subprocess.TimeoutExpired as exc:
        if proc is not None:
            proc.kill()
            proc.communicate()
        elapsed = time.monotonic() - started
        md_count, image_count = count_outputs(output_dir)
        return TaskResult(
            worker_count=worker_count,
            pdf_path=str(pdf_path),
            output_dir=str(output_dir),
            returncode=None,
            elapsed_seconds=round(elapsed, 3),
            md_count=md_count,
            image_count=image_count,
            status="timeout",
            error_tail=str(exc)[-1000:],
        )
    finally:
        if proc is not None:
            with active_lock:
                active_pids.pop(proc.pid, None)


def write_reports(
    output_root: Path,
    stamp: str,
    results: list[TaskResult],
    metrics: list[MetricSample],
) -> tuple[Path, Path, Path, Path]:
    csv_path = output_root / f"mineru_worker_benchmark__{stamp}.csv"
    json_path = output_root / f"mineru_worker_benchmark__{stamp}.json"
    metrics_csv_path = output_root / f"mineru_worker_metrics__{stamp}.csv"
    metrics_json_path = output_root / f"mineru_worker_metrics__{stamp}.json"

    rows = [asdict(result) for result in results]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    metric_rows = [asdict(sample) for sample in metrics]
    if metric_rows:
        with metrics_csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(metric_rows[0].keys()))
            writer.writeheader()
            writer.writerows(metric_rows)
        metrics_json_path.write_text(json.dumps(metric_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        metrics_csv_path.write_text("", encoding="utf-8")
        metrics_json_path.write_text("[]\n", encoding="utf-8")

    return csv_path, json_path, metrics_csv_path, metrics_json_path


def main() -> None:
    args = parse_args()
    mineru_bin = args.mineru_bin.expanduser().resolve()
    if not mineru_bin.exists():
        raise SystemExit(f"MinerU executable not found: {mineru_bin}")

    samples = choose_samples(args.pdf_root, args.pdf, args.sample_count)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_root = args.output_root / stamp
    output_root.mkdir(parents=True, exist_ok=True)

    all_results: list[TaskResult] = []
    all_metrics: list[MetricSample] = []
    try:
        for worker_count in args.workers:
            run_dir = output_root / f"workers_{worker_count}"
            if run_dir.exists() and not args.keep_output:
                shutil.rmtree(run_dir)
            run_dir.mkdir(parents=True, exist_ok=True)

            started = time.monotonic()
            active_pids: dict[int, str] = {}
            active_lock = threading.Lock()
            stop_event = threading.Event()
            metric_thread = threading.Thread(
                target=monitor_metrics,
                args=(
                    worker_count,
                    started,
                    active_pids,
                    active_lock,
                    stop_event,
                    all_metrics,
                    args.monitor_interval_seconds,
                ),
                daemon=True,
            )
            metric_thread.start()
            try:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures = []
                    for pdf_path in samples:
                        pdf_output = run_dir / pdf_path.stem
                        futures.append(
                            executor.submit(
                                run_one,
                                mineru_bin,
                                pdf_path,
                                pdf_output,
                                worker_count,
                                args.timeout_seconds,
                                active_pids,
                                active_lock,
                            )
                        )
                    for future in as_completed(futures):
                        result = future.result()
                        all_results.append(result)
                        print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
            finally:
                stop_event.set()
                metric_thread.join(timeout=args.monitor_interval_seconds + 1)

            elapsed = time.monotonic() - started
            ok_count = sum(1 for r in all_results if r.worker_count == worker_count and r.status == "ok")
            run_metrics = [m for m in all_metrics if m.worker_count == worker_count]
            peak_rss = max((m.total_rss_mb or 0 for m in run_metrics), default=0)
            peak_gpu = max((m.gpu_device_utilization_percent or 0 for m in run_metrics), default=0)
            peak_gpu_mem = max((m.gpu_in_use_system_memory_mb or 0 for m in run_metrics), default=0)
            print(
                json.dumps(
                    {
                        "worker_count": worker_count,
                        "sample_count": len(samples),
                        "ok_count": ok_count,
                        "elapsed_seconds": round(elapsed, 3),
                        "peak_total_rss_mb": peak_rss,
                        "peak_gpu_device_utilization_percent": peak_gpu,
                        "peak_gpu_in_use_system_memory_mb": peak_gpu_mem,
                        "run_dir": str(run_dir),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

            write_reports(output_root, stamp, all_results, all_metrics)
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "note": "writing completed results"}, ensure_ascii=False), flush=True)

    csv_path, json_path, metrics_csv_path, metrics_json_path = write_reports(output_root, stamp, all_results, all_metrics)
    print(
        json.dumps(
            {
                "csv": str(csv_path),
                "json": str(json_path),
                "metrics_csv": str(metrics_csv_path),
                "metrics_json": str(metrics_json_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
