from __future__ import annotations

import base64
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "review_ui_workflow_console",
    ROOT / "scripts" / "serve_question_review_ui.py",
)
assert SPEC and SPEC.loader
review_ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_ui)


class ReviewUiWorkflowConsoleTests(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_workflow_page_is_a_queue_console_not_the_legacy_tab_shell(self) -> None:
        page = (ROOT / "review_ui" / "workflow.html").read_text(encoding="utf-8")
        for marker in (
            "互斥人工 queue",
            "五條 lane 時間線",
            "三證據／PDF evidence",
            "Revision / invalidation",
            "production writes",
            "advisory-only",
        ):
            self.assertIn(marker, page)
        self.assertIn("replace(/\"/g, '&quot;')", page)

    def test_mobile_entrypoint_uses_the_same_workflow_console(self) -> None:
        payload = review_ui.mobile_asset_response("/mobile/workflow/")
        self.assertIsNotNone(payload)
        data, content_type, _cache_control = payload  # type: ignore[misc]
        self.assertEqual(content_type, "text/html; charset=utf-8")
        page = data.decode("utf-8")
        self.assertIn("互斥人工 queue", page)
        self.assertIn("/api/review", page)

    def test_primary_queue_is_disjoint_even_when_many_lanes_have_findings(self) -> None:
        item = {
            "metadata": {
                "parser_status": "pass",
                "ai395_scope_tags": ["notation", "visual_missing"],
            },
            "issues": [],
            "answer_issues": [],
            "visual_profile": {},
            "review": {},
            "ai_review": {
                "checks": {
                    "answer": "finding",
                    "group": "finding",
                    "notation": "finding",
                    "text_evidence": "finding",
                    "vision": "finding",
                },
                "findings": [
                    {"lane": lane, "code": f"{lane}_finding"}
                    for lane in ("answer", "group", "notation", "text_evidence", "vision")
                ],
            },
        }
        queue = review_ui.workflow_primary_queue(item)
        self.assertEqual(queue["id"], "answer")
        self.assertEqual(
            sum(queue["id"] == candidate for candidate, _label, _description in review_ui.WORKFLOW_QUEUE_DEFINITIONS),
            1,
        )

    def test_workflow_payload_binds_three_source_packet_and_hides_raw_model_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_key = "moex:test:question:q001"
            candidates = root / "candidates.jsonl"
            issues = root / "issues.csv"
            review_log = root / "question_review_events.jsonl"
            ai_log = root / "question_ai_review_events.jsonl"
            summary = root / "summary.json"
            analysis = root / "analysis.json"
            packets = root / "blind-packets.jsonl"
            crop = root / "cases" / "case-001" / "official-question-region.png"
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"PNG test evidence")

            self.write_jsonl(
                candidates,
                [
                    {
                        "candidate_key": candidate_key,
                        "source_registry_key": "moex:test:question",
                        "question_number": "1",
                        "stem": "下列何者正確？",
                        "options": [{"key": key, "text": f"選項 {key}"} for key in "ABCD"],
                        "answer": "A",
                        "metadata": {
                            "normalized_category_name": "藥師",
                            "normalized_subject_name": "測試科目",
                            "year": "115",
                            "exam_ordinal": "1",
                            "parser_status": "pass",
                            "ai395_staging_revision_id": "run:key:r1",
                            "ai395_staging_revision_status": "active",
                        },
                    }
                ],
            )
            with issues.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["candidate_key", "question_number", "issue_code", "severity", "message", "owner_stage", "status", "issue_json", "source"],
                )
                writer.writeheader()
            self.write_jsonl(
                ai_log,
                [
                    {
                        "candidate_key": candidate_key,
                        "action": "ai_audit",
                        "provider": "local_qwen_mlx",
                        "model": "qwen3.8:27b-mlx",
                        "audit": {
                            "status": "needs_review",
                            "checks": {"text_evidence": "finding", "notation": "machine_pass"},
                            "findings": [{"lane": "text_evidence", "code": "model_ocr_residue"}],
                            "lane_results": [
                                {
                                    "lane": "text_evidence",
                                    "status": "finding",
                                    "provider": "local_qwen_mlx",
                                    "model": "qwen3.8:27b-mlx",
                                    "result": {
                                        "status": "finding",
                                        "finding_codes": ["model_ocr_residue"],
                                        "model_called": True,
                                        "model_result": {
                                            "reason": "需要 PDF 比對",
                                            "raw_content": "不要送到瀏覽器的原始內容",
                                            "context_guard": {"context_limit_tokens": 131072, "within_budget": True},
                                        },
                                    },
                                },
                                {"lane": "notation", "status": "machine_pass", "result": {"status": "machine_pass"}},
                            ],
                        },
                    }
                ],
            )
            summary.write_text(
                json.dumps({"run_id": "run-001", "model_name": "qwen3.8:27b-mlx", "production_write_count": 0, "model_context_policy": {"hard_limit_tokens": 131072}}, ensure_ascii=False),
                encoding="utf-8",
            )
            analysis.write_text(
                json.dumps({"automation_assessment": {"auto_rule_candidate_count": 1}, "cases": [{"candidate_key": candidate_key, "source": {"status": "compatibility_glyph_difference", "question_consensus": "usable_question_consensus", "family_count": 3, "raw_support": 0, "nfkc_support": 3, "auto_rule_candidate": True}, "pdf_flags": []}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            self.write_jsonl(
                packets,
                [
                    {
                        "candidate_key": candidate_key,
                        "case_id": "case-001",
                        "official_pdf_second_source": {
                            "families": ["pdfplumber", "poppler", "pypdf"],
                            "question_consensus": {
                                "status": "usable_question_consensus",
                                "family_count": 3,
                                "question_text_by_engine": {"pdfplumber": {"text": "1 下列何者正確？"}},
                            },
                        },
                        "visual_evidence": {"official_question_crop": str(crop)},
                    }
                ],
            )
            state = review_ui.ReviewState(
                candidates,
                issues,
                review_log,
                review_backend="jsonl",
                run_summary_path=summary,
                three_source_analysis_path=analysis,
                three_source_packets_path=packets,
            )
            payload = state.workflow_payload({"candidate_key": candidate_key})
            self.assertEqual(payload["queue_total"], 1)
            self.assertEqual(sum(payload["queue_counts"].values()), 1)
            self.assertEqual(payload["selected"]["candidate"]["candidate_key"], candidate_key)
            self.assertEqual(payload["selected"]["queue"]["id"], "text")
            self.assertTrue(payload["selected"]["evidence"]["source"]["auto_rule_candidate"])
            self.assertEqual(payload["selected"]["evidence"]["pdf"]["families"], ["pdfplumber", "poppler", "pypdf"])
            self.assertTrue(state.evidence_file_path(candidate_key, "official_question_crop").is_file())  # type: ignore[union-attr]
            self.assertIsNone(state.evidence_file_path(candidate_key, "../issues.csv"))
            lane_result = payload["selected"]["candidate"]["ai_review"]["lane_results"][0]
            self.assertNotIn("raw_content", lane_result)


if __name__ == "__main__":
    unittest.main()
