import unittest

from scripts.build_accepted_reaudit_proposal import finding_evidence, flatten_result


class AcceptedReauditProposalTests(unittest.TestCase):
    def test_sparse_batch_issue_is_rehydrated_and_evidence_matches(self):
        rows = list(
            flatten_result(
                {
                    "batch_id": "b1",
                    "checked_count": 1,
                    "model": "gpt-5.6-luna",
                    "prompt_version": "national_exam_sparse_audit_v4",
                    "issues": [
                        {
                            "candidate_key": "q1",
                            "field": "stem",
                            "before": "簡體",
                            "after": "繁體",
                            "issue_family": "ocr_character",
                            "source_class": "candidate_mismatch",
                            "route": "human_text",
                            "confidence": 0.97,
                            "note": "候選欄位出現簡體字，需核對繁體字形。",
                        }
                    ],
                }
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_key"], "q1")
        self.assertEqual(rows[0]["findings"][0]["location"], "stem")
        self.assertEqual(rows[0]["findings"][0]["message"], "候選欄位出現簡體字，需核對繁體字形。")
        self.assertEqual(rows[0]["evidence"][0]["reason"], "候選欄位出現簡體字，需核對繁體字形。")
        task = {"content": {"stem": "這是一段簡體文字", "options": []}}
        matched, missing = finding_evidence(task, rows[0])
        self.assertEqual(len(matched), 1)
        self.assertEqual(missing, [])

    def test_missing_observed_text_is_not_verified(self):
        task = {"content": {"stem": "目前題面沒有這個片段", "options": []}}
        row = {
            "findings": [
                {
                    "issue_family": "ocr_character",
                    "location": "stem",
                    "observed": "不存在",
                    "suggested": "修正",
                    "confidence": 0.99,
                }
            ],
            "evidence": [],
        }
        matched, missing = finding_evidence(task, row)
        self.assertEqual(matched, [])
        self.assertEqual(len(missing), 1)


if __name__ == "__main__":
    unittest.main()
