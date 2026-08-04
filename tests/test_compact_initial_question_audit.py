from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "compact_initial_question_audit.py"
SPEC = importlib.util.spec_from_file_location("compact_initial_question_audit", SCRIPT_PATH)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)

SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
IMPORTER_PATH = SCRIPTS_DIR / "import_codex_audit_results.py"
IMPORTER_SPEC = importlib.util.spec_from_file_location(
    "compact_audit_importer",
    IMPORTER_PATH,
)
assert IMPORTER_SPEC and IMPORTER_SPEC.loader
importer = importlib.util.module_from_spec(IMPORTER_SPEC)
sys.modules[IMPORTER_SPEC.name] = importer
IMPORTER_SPEC.loader.exec_module(importer)


def task(number: int, stem: str = "辅酶參與反應。") -> dict[str, object]:
    return {
        "candidate_key": f"moex:test:q{number:03d}",
        "effective_content_hash": f"hash-{number}",
        "exam": {
            "category": "醫事檢驗師",
            "subject": "測試科目",
            "year": "115",
            "ordinal": "2",
            "question_number": str(number),
        },
        "content": {
            "answer": "A",
            "stem": stem,
            "options": [
                {"key": "A", "text": "第一個選項", "image": None, "markup": None},
                {"key": "B", "text": "第二個選項", "image": None, "markup": None},
            ],
            "image_refs": [],
            "group_ref": None,
            "group_sequence_no": None,
        },
        "human_state": {"action": "accept", "notes": "人工註記"},
        "neighbors": {
            "previous": None,
            "next": {
                "candidate_key": f"moex:test:q{number + 1:03d}",
                "question_number": str(number + 1),
                "stem": "下一題",
            },
        },
        "signals": {"parser_issues": []},
    }


def response(packet: dict[str, object], issues: list[dict[str, object]]) -> str:
    return json.dumps({"issues": issues}, ensure_ascii=False)


class CompactInitialQuestionAuditTests(unittest.TestCase):
    def test_ollama_format_mode_can_defer_schema_enforcement_to_validator(self) -> None:
        schema = {"type": "object"}
        self.assertEqual(audit.ollama_format_value("schema", schema), schema)
        self.assertEqual(audit.ollama_format_value("json", schema), "json")
        self.assertIsNone(audit.ollama_format_value("prompt-only", schema))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            audit.ollama_format_value("other", schema)

    def test_task_window_supports_full_exam_chunk_offsets(self) -> None:
        rows = [task(number) for number in range(1, 81)]
        self.assertEqual(
            [row["candidate_key"] for row in audit.select_task_window(rows, 24, 24)],
            [f"moex:test:q{number:03d}" for number in range(25, 49)],
        )
        self.assertEqual(len(audit.select_task_window(rows, 72, 24)), 8)
        with self.assertRaisesRegex(ValueError, "offset"):
            audit.select_task_window(rows, -1, 24)

    def test_model_packets_exclude_answers_and_human_review_state(self) -> None:
        row = task(1)
        for lane in audit.LANES:
            packet = audit.build_lane_packet([row], lane)
            prompt = json.loads(packet["prompt"])
            serialized = json.dumps(prompt, ensure_ascii=False)
            self.assertNotIn('"answer"', serialized)
            self.assertNotIn("人工註記", serialized)
            self.assertNotIn('"candidate_key"', serialized)
            self.assertNotIn("測試科目", serialized)
            self.assertNotIn("batch_id", prompt)
            self.assertNotIn("checked_count", prompt)
            self.assertEqual(len(prompt["questions"]), 1)
            schema = packet["response_schema"]
            self.assertEqual(
                schema["properties"]["issues"]["maxItems"],
                3 * packet["task_count"],
            )

    def test_ocr_exception_becomes_local_review_ui_correction(self) -> None:
        tasks = [task(1)]
        validations: dict[str, dict[str, object]] = {}
        for lane in audit.LANES:
            packet = audit.build_lane_packet(tasks, lane)
            issues: list[dict[str, object]] = []
            if lane == "ocr_text":
                issues = [
                    {
                        "q": 1,
                        "code": "simplified_character",
                        "location": "stem",
                        "observed": "辅酶",
                        "replacement": "輔酶",
                        "confidence": 0.99,
                        "note": "",
                    }
                ]
            validation = audit.validate_lane_response(
                packet,
                tasks,
                response(packet, issues),
                "stop",
            )
            self.assertTrue(validation["complete"], validation["errors"])
            validations[lane] = validation
        rows = audit.review_ui_results(tasks, validations, "qwen3.6:35b-mlx")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "needs_review")
        self.assertEqual(rows[0]["suggested_correction"], {"stem": "輔酶參與反應。"})
        self.assertEqual(rows[0]["checks"]["ocr_text"]["status"], "needs_review")
        self.assertEqual(rows[0]["checks"]["meaning"]["status"], "pass")
        self.assertNotIn("answer", rows[0]["suggested_correction"])

    def test_option_correction_updates_embedded_markup_text(self) -> None:
        row = task(1)
        row["content"]["options"][1]["text"] = "纍胺酸（valine）"
        row["content"]["options"][1]["markup"] = {
            "format": "plain-or-mineru-markdown",
            "plain": "纍胺酸（valine）",
            "markup": "纍胺酸（valine）",
            "needs_review": False,
        }
        correction, changes, rejected = audit.materialize_ocr_correction(
            row,
            [
                {
                    "location": "option_B",
                    "observed": "纍胺酸",
                    "replacement": "纈胺酸",
                }
            ],
        )
        self.assertEqual(rejected, [])
        self.assertEqual(changes, ["選項 B：纍胺酸 → 纈胺酸"])
        option = correction["options"][1]
        self.assertEqual(option["text"], "纈胺酸（valine）")
        self.assertEqual(option["markup"]["plain"], "纈胺酸（valine）")
        self.assertEqual(option["markup"]["markup"], "纈胺酸（valine）")

    def test_meaning_lane_replacement_is_removed_but_finding_is_kept(self) -> None:
        tasks = [task(1)]
        packet = audit.build_lane_packet(tasks, "meaning")
        raw = response(
            packet,
            [
                {
                    "q": 1,
                    "code": "broken_sentence",
                    "location": "stem",
                    "observed": "參與反應",
                    "replacement": "參與此反應",
                    "confidence": 0.8,
                    "note": "疑似漏字",
                }
            ],
        )
        validation = audit.validate_lane_response(packet, tasks, raw, "stop")
        self.assertTrue(validation["complete"])
        self.assertIsNone(validation["issues"][0]["replacement"])
        self.assertTrue(
            any(
                "replacement_not_allowed_for_lane_removed" in warning
                for warning in validation["warnings"]
            )
        )

    def test_long_medical_rewrite_is_kept_as_a_finding_without_a_patch(self) -> None:
        tasks = [task(1, "有關鎂細胞貧血症之敘述。")]
        packet = audit.build_lane_packet(tasks, "ocr_text")
        raw = response(
            packet,
            [
                {
                    "q": 1,
                    "code": "ocr_character",
                    "location": "stem",
                    "observed": "鎂細胞貧血症",
                    "replacement": "鐮刀型紅血球貧血症",
                    "confidence": 0.9,
                    "note": "疑似名詞錯誤",
                }
            ],
        )
        validation = audit.validate_lane_response(packet, tasks, raw, "stop")
        self.assertTrue(validation["complete"])
        self.assertIsNone(validation["issues"][0]["replacement"])
        self.assertIn("issue_1:unsafe_replacement_removed", validation["warnings"])

    def test_duplicate_root_json_is_removed_without_failing_the_batch(self) -> None:
        tasks = [task(1)]
        packet = audit.build_lane_packet(tasks, "ocr_text")
        raw = response(packet, [])
        validation = audit.validate_lane_response(
            packet,
            tasks,
            raw + "\n" + raw,
            "stop",
        )
        self.assertTrue(validation["complete"], validation["errors"])
        self.assertIn("duplicate_root_json_removed:1", validation["warnings"])

    def test_trailing_explanation_is_removed_but_conflicting_json_is_rejected(self) -> None:
        tasks = [task(1)]
        packet = audit.build_lane_packet(tasks, "ocr_text")
        raw = response(packet, [])
        trailing = audit.validate_lane_response(
            packet,
            tasks,
            raw + "\n已完成。",
            "stop",
        )
        self.assertTrue(trailing["complete"], trailing["errors"])
        self.assertIn("trailing_text_removed", trailing["warnings"])
        conflicting = audit.validate_lane_response(
            packet,
            tasks,
            raw
            + "\n"
            + json.dumps(
                {
                    "issues": [
                        {
                            "q": 1,
                            "code": "ocr_character",
                            "location": "stem",
                            "observed": "辅酶",
                            "replacement": "輔酶",
                            "confidence": 0.9,
                            "note": "",
                        }
                    ]
                }
            ),
            "stop",
        )
        # A second, semantically different root must not be silently combined.
        self.assertFalse(conflicting["complete"])
        self.assertTrue(
            any("conflicting_json_tail" in error for error in conflicting["errors"])
        )

    def test_excess_and_duplicate_findings_are_dropped_without_batch_failure(self) -> None:
        tasks = [task(1, "辅酶辅酶辅酶辅酶")]
        packet = audit.build_lane_packet(tasks, "ocr_text")
        findings = [
            {
                "q": 1,
                "code": code,
                "location": "stem",
                "observed": "辅酶",
                "replacement": "輔酶",
                "confidence": confidence,
                "note": "",
            }
            for code, confidence in [
                ("simplified_character", 0.6),
                ("ocr_character", 0.9),
                ("notation_markup", 0.8),
                ("punctuation_spacing", 0.7),
            ]
        ]
        # Bypass schema enforcement to exercise the defensive validator.
        raw = response(packet, [findings[0], *findings])
        validation = audit.validate_lane_response(packet, tasks, raw, "stop")
        self.assertTrue(validation["complete"], validation["errors"])
        self.assertEqual(len(validation["issues"]), 3)
        self.assertTrue(
            any("duplicate_finding_dropped" in warning for warning in validation["warnings"])
        )
        self.assertTrue(
            any("excess_findings_dropped" in warning for warning in validation["warnings"])
        )
        self.assertEqual(validation["dropped_issue_count"], 2)

    def test_valid_display_markup_and_domain_judgment_are_filtered(self) -> None:
        markup_task = task(1)
        markup_task["content"]["options"][0]["text"] = "Vitamin B<sub>12</sub>"
        markup_packet = audit.build_lane_packet([markup_task], "ocr_text")
        markup_validation = audit.validate_lane_response(
            markup_packet,
            [markup_task],
            response(
                markup_packet,
                [
                    {
                        "q": 1,
                        "code": "ocr_character",
                        "location": "option_A",
                        "observed": "<sub>12</sub>",
                        "replacement": None,
                        "confidence": 0.95,
                        "note": "HTML tag detected",
                    }
                ],
            ),
            "stop",
        )
        self.assertTrue(markup_validation["complete"])
        self.assertEqual(markup_validation["issues"], [])
        self.assertIn(
            "issue_1:valid_display_markup_finding_dropped",
            markup_validation["warnings"],
        )

        meaning_task = task(1, "完整但專業內容反直覺的題目。")
        meaning_packet = audit.build_lane_packet([meaning_task], "meaning")
        meaning_validation = audit.validate_lane_response(
            meaning_packet,
            [meaning_task],
            response(
                meaning_packet,
                [
                    {
                        "q": 1,
                        "code": "broken_sentence",
                        "location": "stem",
                        "observed": "完整但專業內容反直覺的題目。",
                        "replacement": None,
                        "confidence": 0.9,
                        "note": "臨床上通常不應發生",
                    }
                ],
            ),
            "stop",
        )
        self.assertTrue(meaning_validation["complete"])
        self.assertEqual(meaning_validation["issues"], [])
        self.assertIn(
            "issue_1:domain_judgment_finding_dropped",
            meaning_validation["warnings"],
        )

    def test_common_exam_wording_is_not_treated_as_an_ocr_error(self) -> None:
        row = task(1, "下列那一種藥物作用最強？")
        packet = audit.build_lane_packet([row], "ocr_text")
        validation = audit.validate_lane_response(
            packet,
            [row],
            response(
                packet,
                [
                    {
                        "q": 1,
                        "code": "ocr_character",
                        "location": "stem",
                        "observed": "那一種",
                        "replacement": None,
                        "confidence": 0.95,
                        "note": "「那」應為「哪」。",
                    }
                ],
            ),
            "stop",
        )
        self.assertTrue(validation["complete"])
        self.assertEqual(validation["issues"], [])
        self.assertIn(
            "issue_1:non_defect_ocr_finding_dropped",
            validation["warnings"],
        )

    def test_long_note_is_truncated_without_retrying_the_batch(self) -> None:
        row = task(1)
        packet = audit.build_lane_packet([row], "ocr_text")
        validation = audit.validate_lane_response(
            packet,
            [row],
            response(
                packet,
                [
                    {
                        "q": 1,
                        "code": "simplified_character",
                        "location": "stem",
                        "observed": "辅酶",
                        "replacement": "輔酶",
                        "confidence": 0.95,
                        "note": "明顯錯字" * 100,
                    }
                ],
            ),
            "stop",
        )
        self.assertTrue(validation["complete"], validation["errors"])
        self.assertEqual(len(validation["issues"][0]["note"]), 240)
        self.assertIn("issue_1:note_truncated", validation["warnings"])

    def test_self_dismissed_ocr_finding_is_dropped(self) -> None:
        row = task(1, "tolvaptan")
        packet = audit.build_lane_packet([row], "ocr_text")
        validation = audit.validate_lane_response(
            packet,
            [row],
            response(
                packet,
                [
                    {
                        "q": 1,
                        "code": "ocr_character",
                        "location": "stem",
                        "observed": "tolvaptan",
                        "replacement": None,
                        "confidence": 0.9,
                        "note": "無明顯 OCR 錯誤，非必須回報。",
                    }
                ],
            ),
            "stop",
        )
        self.assertTrue(validation["complete"])
        self.assertEqual(validation["issues"], [])
        self.assertIn(
            "issue_1:non_defect_ocr_finding_dropped",
            validation["warnings"],
        )
        self.assertEqual(validation["filtered_non_issue_count"], 1)

    def test_malformed_individual_finding_does_not_retry_other_questions(self) -> None:
        rows = [task(1), task(2)]
        packet = audit.build_lane_packet(rows, "ocr_text")
        raw = json.dumps(
            {
                "issues": [
                    {
                        "q": 1,
                        "finding": "投與",
                        "replacement": "投予",
                        "note": "錯別字",
                    },
                    {
                        "q": 2,
                        "code": "simplified_character",
                        "location": "stem",
                        "observed": "辅酶",
                        "replacement": "輔酶",
                        "confidence": 0.95,
                        "note": "",
                    },
                ]
            },
            ensure_ascii=False,
        )
        validation = audit.validate_lane_response(packet, rows, raw, "stop")
        self.assertTrue(validation["complete"], validation["errors"])
        self.assertEqual(validation["dropped_issue_count"], 1)
        self.assertEqual(
            [issue["candidate_key"] for issue in validation["issues"]],
            ["moex:test:q002"],
        )
        self.assertTrue(
            any(
                "malformed_finding_dropped" in warning
                for warning in validation["warnings"]
            )
        )

    def test_style_equivalent_drug_administration_wording_is_not_corrected(self) -> None:
        row = task(1, "藥物由鼻腔投與。")
        packet = audit.build_lane_packet([row], "ocr_text")
        validation = audit.validate_lane_response(
            packet,
            [row],
            response(
                packet,
                [
                    {
                        "q": 1,
                        "code": "ocr_character",
                        "location": "stem",
                        "observed": "投與",
                        "replacement": "投予",
                        "confidence": 0.9,
                        "note": "",
                    }
                ],
            ),
            "stop",
        )
        self.assertTrue(validation["complete"])
        self.assertEqual(validation["issues"], [])
        self.assertEqual(validation["filtered_non_issue_count"], 1)
        self.assertIn(
            "issue_1:style_equivalent_replacement_dropped",
            validation["warnings"],
        )

    def test_truncated_lane_blocks_review_ui_preview(self) -> None:
        tasks = [task(1)]
        validations: dict[str, dict[str, object]] = {}
        for lane in audit.LANES:
            packet = audit.build_lane_packet(tasks, lane)
            validations[lane] = audit.validate_lane_response(
                packet,
                tasks,
                response(packet, []),
                "length" if lane == "visual" else "stop",
            )
        with self.assertRaisesRegex(ValueError, "visual"):
            audit.review_ui_results(tasks, validations, "qwen3.6:35b-mlx")

    def test_unavailable_vision_is_routed_to_review_instead_of_pass(self) -> None:
        tasks = [task(1, "依下圖判斷。")]
        validations: dict[str, dict[str, object]] = {}
        for lane in audit.LANES:
            packet = audit.build_lane_packet(tasks, lane)
            validations[lane] = audit.validate_lane_response(
                packet,
                tasks,
                response(packet, []),
                "stop",
            )
        validations["visual"] = {
            "complete": True,
            "unavailable": True,
            "selected_candidate_keys": [tasks[0]["candidate_key"]],
            "issues": [
                {
                    "q": 1,
                    "candidate_key": tasks[0]["candidate_key"],
                    "lane": "visual",
                    "code": "visual_dependency_uncertain",
                    "location": "images",
                    "observed": "attached_image_count=1",
                    "replacement": None,
                    "confidence": 0.0,
                    "note": "vision probe failed",
                    "evidence_validated": True,
                }
            ],
        }
        rows = audit.review_ui_results(tasks, validations, "qwen3.6:35b-mlx")
        self.assertEqual(rows[0]["status"], "needs_review")
        self.assertEqual(rows[0]["checks"]["visual"]["status"], "unavailable")
        self.assertIsNone(rows[0]["suggested_correction"])

    def test_visual_packet_preserves_image_order_without_embedding_it_in_saved_packet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "figure.jpg"
            image_path.write_bytes(b"not-a-real-jpeg")
            row = task(1)
            row["content"]["image_refs"] = [{"path": str(image_path), "exists": True}]
            packet = audit.build_lane_packet([row], "visual")
            prompt = json.loads(packet["prompt"])
            self.assertEqual(prompt["questions"][0]["image_slots"], [1])
            self.assertEqual(packet["image_paths"], [str(image_path.resolve())])
            stored = audit.packet_for_storage(packet, "qwen3.6:35b-mlx")
            self.assertNotIn("images", stored)
            self.assertEqual(stored["image_count"], 1)

    def test_visual_and_group_lanes_only_receive_applicable_candidates(self) -> None:
        plain = task(1)
        visual = task(2, "依下圖判斷。")
        grouped = task(3, "第 3 至 4 題共用下列題幹。")
        rows = [plain, visual, grouped]
        self.assertEqual(audit.select_lane_tasks(rows, "ocr_text"), rows)
        self.assertEqual(
            [row["candidate_key"] for row in audit.select_lane_tasks(rows, "visual")],
            [visual["candidate_key"]],
        )
        self.assertEqual(
            [row["candidate_key"] for row in audit.select_lane_tasks(rows, "group")],
            [grouped["candidate_key"]],
        )

    def test_existing_review_ui_importer_preserves_lane_findings_and_checks(self) -> None:
        record = {
            "provider": "ollama",
            "model": "qwen3.6:35b-mlx",
            "prompt_version": audit.PROMPT_VERSION,
            "status": "needs_review",
            "confidence": 0.99,
            "reason": "四路初審完成。",
            "labels": ["simplified_character"],
            "recommended_action": "human_review_text",
            "findings": [
                {
                    "code": "simplified_character",
                    "severity": "warning",
                    "field": "stem",
                    "message": "疑似簡體字。",
                    "evidence": "辅酶 → 輔酶",
                    "suggestion": "人工比對原卷。",
                    "audit_lane": "ocr_text",
                    "confidence": 0.99,
                }
            ],
            "checks": {
                "ocr_text": {"status": "needs_review"},
                "meaning": {"status": "pass"},
            },
            "suggested_correction": {"stem": "輔酶參與反應。"},
        }
        normalized = importer.normalize_record(record, "fallback")
        self.assertEqual(normalized["provider"], "ollama")
        self.assertEqual(normalized["findings"][0]["audit_lane"], "ocr_text")
        self.assertEqual(normalized["checks"]["meaning"]["status"], "pass")
        self.assertEqual(normalized["suggested_correction"]["stem"], "輔酶參與反應。")


if __name__ == "__main__":
    unittest.main()
