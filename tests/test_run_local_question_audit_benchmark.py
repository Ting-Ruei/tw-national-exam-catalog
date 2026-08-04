from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_local_question_audit_benchmark.py"
SPEC = importlib.util.spec_from_file_location("local_audit_benchmark", SCRIPT_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


class LocalQuestionAuditBenchmarkTests(unittest.TestCase):
    def test_paper_aligned_windows_never_cross_paper_boundaries(self) -> None:
        tasks = [
            {"source_registry_key": paper}
            for paper in ["paper-a", "paper-a", "paper-a", "paper-b", "paper-b"]
        ]
        self.assertEqual(
            benchmark.chunk_windows(tasks, 2, True),
            [(0, 2), (2, 1), (3, 2)],
        )
        self.assertEqual(
            benchmark.chunk_windows(tasks, 2, False),
            [(0, 2), (2, 2), (4, 1)],
        )

    def test_combined_summary_keeps_compatible_prior_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for slug, model, task_count, batch_sizes in [
                ("model-b", "model:b", 80, [16, 24, 40, 80]),
                ("model-a", "model:a", 80, [16, 24, 40, 80]),
                ("other-data", "model:c", 40, [16, 24, 40, 80]),
            ]:
                path = root / slug / "benchmark_summary.json"
                path.parent.mkdir(parents=True)
                path.write_text(
                    json.dumps(
                        {
                            "model": model,
                            "task_count": task_count,
                            "batch_sizes": batch_sizes,
                            "schemes": [],
                        }
                    ),
                    encoding="utf-8",
                )
            summaries = benchmark.collect_compatible_model_summaries(
                root,
                80,
                [16, 24, 40, 80],
            )
            self.assertEqual(
                [summary["model"] for summary in summaries],
                ["model:a", "model:b"],
            )

    def test_chunk_offsets_cover_full_exam(self) -> None:
        self.assertEqual(benchmark.chunk_offsets(80, 16), [0, 16, 32, 48, 64])
        self.assertEqual(benchmark.chunk_offsets(80, 24), [0, 24, 48, 72])
        self.assertEqual(benchmark.chunk_offsets(80, 40), [0, 40])
        self.assertEqual(benchmark.chunk_offsets(80, 80), [0])

    def test_only_offsets_argument_supports_targeted_retries(self) -> None:
        previous_argv = sys.argv
        try:
            sys.argv = [
                "run_local_question_audit_benchmark.py",
                "--tasks",
                "tasks.jsonl",
                "--output-root",
                "out",
                "--model",
                "test:model",
                "--batch-sizes",
                "10",
                "--only-offsets",
                "40",
                "50",
            ]
            args = benchmark.parse_args()
        finally:
            sys.argv = previous_argv
        self.assertEqual(args.only_offsets, [40, 50])
        self.assertEqual(args.max_retries, 3)
        self.assertEqual(args.max_split_depth, 2)

    def test_failed_chunk_details_preserve_validation_errors_before_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chunk = Path(directory) / "bs10-o20"
            chunk.mkdir()
            (chunk / "summary.json").write_text(
                json.dumps(
                    {
                        "all_lanes_complete": False,
                        "lane_validation": {
                            "ocr_text": {"errors": ["invalid_json"]},
                            "meaning": {"errors": []},
                        },
                    }
                ),
                encoding="utf-8",
            )
            details = benchmark.chunk_failure_details(chunk, 0)
            self.assertEqual(details["reason"], "validation_failed")
            self.assertEqual(details["errors"], {"ocr_text": ["invalid_json"]})
            archived = benchmark.archive_failed_output(chunk, "attempt01")
            self.assertIsNotNone(archived)
            self.assertFalse(chunk.exists())
            self.assertTrue(archived.exists())

    def test_aggregate_keeps_validation_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            complete = root / "bs40-o0"
            failed = root / "bs40-o40"
            complete.mkdir(parents=True)
            failed.mkdir(parents=True)
            (complete / "summary.json").write_text(
                json.dumps(
                    {
                        "task_count": 40,
                        "all_lanes_complete": True,
                        "metrics": [
                            {
                                "total_latency_ms": 1000,
                                "prompt_eval_count": 100,
                                "eval_count": 20,
                            }
                        ],
                        "lane_validation": {
                            "ocr_text": {"issue_count": 2, "errors": []},
                            "meaning": {"issue_count": 1, "errors": []},
                        },
                        "review_ui_results_preview": "preview.jsonl",
                    }
                ),
                encoding="utf-8",
            )
            (failed / "summary.json").write_text(
                json.dumps(
                    {
                        "task_count": 40,
                        "all_lanes_complete": False,
                        "metrics": [
                            {
                                "total_latency_ms": 2000,
                                "prompt_eval_count": 200,
                                "eval_count": 40,
                            }
                        ],
                        "lane_validation": {
                            "ocr_text": {
                                "issue_count": 0,
                                "errors": ["invalid_json"],
                            },
                            "meaning": {"issue_count": 0, "errors": []},
                        },
                        "review_ui_results_preview": None,
                    }
                ),
                encoding="utf-8",
            )
            result = benchmark.aggregate_model("test:model", root, [40], 80)
            scheme = result["schemes"][0]
            self.assertEqual(scheme["complete_chunks"], 1)
            self.assertEqual(scheme["present_chunks"], 2)
            self.assertFalse(scheme["all_chunks_complete"])
            self.assertEqual(scheme["total_latency_ms"], 3000)
            self.assertEqual(
                scheme["chunks"][1]["errors"]["ocr_text"],
                ["invalid_json"],
            )


if __name__ == "__main__":
    unittest.main()
