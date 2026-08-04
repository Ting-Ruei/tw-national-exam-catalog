import unittest

from scripts.build_accepted_reaudit_advisories import build_row, sanitize_correction


class AcceptedReauditAdvisoryTests(unittest.TestCase):
    def test_sanitize_correction_removes_parser_display_metadata(self):
        correction = {
            "options": [
                {
                    "key": "a",
                    "text": "莢膜",
                    "image": None,
                    "markup": "<b>",
                    "raw_order": 1,
                }
            ]
        }
        self.assertEqual(
            sanitize_correction(correction),
            {"options": [{"key": "A", "text": "莢膜"}]},
        )

    def test_withheld_human_pdf_patch_is_not_marked_applicable(self):
        row = build_row(
            {
                "candidate_key": "q1",
                "audit_status": "needs_review",
                "audit_confidence": 0.94,
                "ai_issue_families": ["semantic_ocr"],
                "ai_summary": "需來源核對",
                "task_content": {"stem": "下列那一題？", "options": []},
                "audit_result": {
                    "status": "needs_review",
                    "findings": [
                        {
                            "issue_family": "semantic_ocr",
                            "location": "stem",
                            "observed": "那",
                            "suggested": "哪",
                            "confidence": 0.94,
                            "correction_applicable": True,
                        }
                    ],
                    "evidence": [
                        {
                            "field": "content.stem",
                            "before": "那",
                            "after": "哪",
                            "route": "human_pdf",
                            "source_class": "source_unverified",
                        }
                    ],
                },
            }
        )
        self.assertIsNone(row.get("suggested_correction"))
        self.assertFalse(row["findings"][0]["correction_applicable"])
        self.assertTrue(row["findings"][0]["correction_omission_reason"])
        self.assertEqual(row["correction_coverage"], "none")

    def test_hydrogen_anchor_rejects_argon_glyph_patch_and_explains_why(self):
        row = build_row(
            {
                "candidate_key": "hydrogen-q1",
                "audit_status": "needs_review",
                "audit_confidence": 0.98,
                "ai_issue_families": ["ocr_character"],
                "ai_summary": "模型提出字形疑點",
                "task_content": {
                    "stem": "蛋白質結構如何維持？",
                    "options": [{"key": "A", "text": "氩鍵（hydrogen bonds）"}],
                },
                "evidence_matches": [
                    {
                        "finding_index": 1,
                        "route": "propose_rule",
                        "source_class": "candidate_mismatch",
                    }
                ],
                "audit_result": {
                    "status": "needs_review",
                    "findings": [
                        {
                            "issue_family": "ocr_character",
                            "location": "option_A",
                            "observed": "氩",
                            "suggested": "氬",
                            "confidence": 0.98,
                            "correction_applicable": True,
                        }
                    ],
                },
            }
        )
        self.assertIsNone(row.get("suggested_correction"))
        finding = row["findings"][0]
        self.assertFalse(finding["correction_applicable"])
        self.assertEqual(finding["suggested"], "氫")
        self.assertIn("hydrogen bond", finding["message"])
        self.assertIn("氬是 argon", finding["message"])

    def test_scientific_name_is_manual_without_source_rule(self):
        row = build_row(
            {
                "candidate_key": "bacteria-q1",
                "audit_status": "needs_review",
                "audit_confidence": 0.99,
                "ai_issue_families": ["semantic_ocr"],
                "ai_summary": "模型認為學名可能有差異",
                "task_content": {
                    "stem": "何者相關？",
                    "options": [{"key": "A", "text": "Staphylococcus aureus"}],
                },
                "evidence_matches": [
                    {
                        "finding_index": 1,
                        "route": "propose_rule",
                        "source_class": "candidate_mismatch",
                    }
                ],
                "audit_result": {
                    "status": "needs_review",
                    "findings": [
                        {
                            "issue_family": "semantic_ocr",
                            "location": "option_A",
                            "observed": "Staphylococcus aureus",
                            "suggested": "Staphylococcus aerues",
                            "confidence": 0.99,
                            "correction_applicable": True,
                        }
                    ],
                },
            }
        )
        self.assertIsNone(row.get("suggested_correction"))
        self.assertFalse(row["findings"][0]["correction_applicable"])
        self.assertIn("拉丁學名", row["findings"][0]["correction_omission_reason"])

    def test_continuation_marker_is_projected_to_group_layer(self):
        row = build_row(
            {
                "candidate_key": "group-q1",
                "audit_status": "needs_review",
                "audit_confidence": 0.91,
                "ai_issue_families": ["group_dependency"],
                "ai_summary": "題組延續線索",
                "task_content": {"stem": "承上題，下列何者正確？", "options": []},
                "audit_result": {
                    "status": "needs_review",
                    "recommended_action": "review_group",
                    "findings": [
                        {
                            "issue_family": "group_dependency",
                            "location": "stem",
                            "observed": "承上題",
                            "suggested": None,
                            "confidence": 0.91,
                        }
                    ],
                },
            }
        )
        self.assertEqual(row["recommended_action"], "review_group")
        self.assertEqual(row["findings"][0]["location"], "group_ref")
        self.assertFalse(row["findings"][0]["correction_applicable"])
        self.assertIsNone(row.get("suggested_correction"))
        self.assertIn("題組頁", row["findings"][0]["message"])

    def test_abbreviated_binomial_does_not_get_expanded(self):
        row = build_row(
            {
                "candidate_key": "abbrev-q1",
                "audit_status": "needs_review",
                "audit_confidence": 0.99,
                "ai_issue_families": ["semantic_ocr"],
                "ai_summary": "模型想展開學名縮寫",
                "task_content": {"stem": "B. cereus", "options": []},
                "evidence_matches": [
                    {
                        "finding_index": 1,
                        "route": "propose_rule",
                        "source_class": "candidate_mismatch",
                    }
                ],
                "audit_result": {
                    "status": "needs_review",
                    "findings": [
                        {
                            "issue_family": "semantic_ocr",
                            "location": "stem",
                            "observed": "B.",
                            "suggested": "Bacillus",
                            "confidence": 0.99,
                            "correction_applicable": True,
                        }
                    ],
                },
            }
        )
        self.assertIsNone(row.get("suggested_correction"))
        self.assertFalse(row["findings"][0]["correction_applicable"])
        self.assertIn("拉丁學名", row["findings"][0]["correction_omission_reason"])

    def test_capsule_compound_name_is_manual_until_pdf_confirms_full_phrase(self):
        row = build_row(
            {
                "candidate_key": "capsule-q1",
                "audit_status": "needs_review",
                "audit_confidence": 0.99,
                "ai_issue_families": ["semantic_ocr"],
                "ai_summary": "可能是莢膜字形",
                "task_content": {
                    "stem": "荧膜組織肥漿菌（Histoplasma capsulatum）",
                    "options": [],
                },
                "evidence_matches": [
                    {
                        "finding_index": 1,
                        "route": "propose_rule",
                        "source_class": "candidate_mismatch",
                    }
                ],
                "audit_result": {
                    "status": "needs_review",
                    "findings": [
                        {
                            "issue_family": "semantic_ocr",
                            "location": "stem",
                            "observed": "荧膜",
                            "suggested": "莢膜",
                            "confidence": 0.99,
                            "correction_applicable": True,
                        }
                    ],
                },
            }
        )
        self.assertIsNone(row.get("suggested_correction"))
        self.assertFalse(row["findings"][0]["correction_applicable"])
        self.assertIn("完整菌名", row["findings"][0]["message"])


if __name__ == "__main__":
    unittest.main()
