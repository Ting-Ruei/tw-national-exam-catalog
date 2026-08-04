from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "docs" / "skills" / "national-exam-ai-audit" / "scripts"


def import_script():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "build_review_ui_advisory_results",
        SCRIPTS / "build_review_ui_advisory_results.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def issue(**overrides):
    value = {
        "candidate_key": "k",
        "field": "stem",
        "before": "錯",
        "after": "正",
        "issue_family": "ocr_character",
        "source_class": "source_unverified",
        "route": "propose_rule",
        "confidence": 0.99,
        "note": "字形疑點",
    }
    value.update(overrides)
    return value


class BuildReviewUiAdvisorySuggestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = import_script()

    def test_builds_full_stem_patch_for_multiple_findings(self) -> None:
        task = {
            "content": {
                "stem": "錯字與錯字",
                "options": [{"key": "A", "text": "保留"}],
            }
        }
        correction, changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [issue(before="錯", after="正", note="題幹兩處錯字")],
        )
        self.assertEqual(correction, {"stem": "正字與正字"})
        self.assertEqual(coverage, "complete")
        self.assertEqual(uncorrected, [])
        self.assertIn("題幹：錯 → 正（2 處）", changes)

    def test_builds_complete_option_array_without_dropping_other_options(self) -> None:
        task = {
            "content": {
                "stem": "題幹",
                "options": [
                    {"key": "A", "text": "甲"},
                    {"key": "B", "text": "錯誤"},
                    {"key": "C", "text": "丙"},
                ],
            }
        }
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [issue(field="option_B", before="錯", after="正")],
        )
        self.assertEqual(coverage, "complete")
        self.assertEqual(uncorrected, [])
        self.assertEqual(
            correction["options"],
            [
                {"key": "A", "text": "甲"},
                {"key": "B", "text": "正誤"},
                {"key": "C", "text": "丙"},
            ],
        )

    def test_structural_or_source_original_findings_are_not_patched(self) -> None:
        task = {"content": {"stem": "錯字", "options": []}}
        correction, changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [
                issue(route="human_pdf", after="正字"),
                issue(source_class="source_original", before="錯字", after="正字"),
            ],
        )
        self.assertIsNone(correction)
        self.assertEqual(changes, [])
        self.assertEqual(coverage, "none")
        self.assertEqual(len(uncorrected), 2)

    def test_ambiguous_replacement_becomes_partial_without_patch_for_that_finding(self) -> None:
        task = {"content": {"stem": "錯字錯字", "options": []}}
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [issue(issue_family="semantic_ocr", confidence=0.98, note="只有一處語意疑點")],
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")
        self.assertEqual(len(uncorrected), 1)
        self.assertIn("位置不唯一", uncorrected[0]["reason"])

    def test_options_without_target_key_are_not_automatically_mapped(self) -> None:
        task = {
            "content": {
                "stem": "題幹",
                "options": [{"key": "A", "text": "錯"}],
            }
        }
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [issue(field="options", before="錯", after="正")],
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")
        self.assertEqual(len(uncorrected), 1)

    def test_propose_rule_without_reason_is_not_a_one_click_patch(self) -> None:
        task = {"content": {"stem": "錯字", "options": []}}
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [issue(before="氩", after="氬", note="")],
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")
        self.assertIn("具體理由", uncorrected[0]["reason"])

    def test_hydrogen_bond_anchor_blocks_generic_traditional_conversion(self) -> None:
        task = {"content": {"stem": "氩鍵（hydrogen bonds）", "options": []}}
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [issue(before="氩", after="氬", note="字形疑點")],
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")
        self.assertIn("hydrogen bond", uncorrected[0]["reason"])

    def test_latin_binomial_is_not_patched_from_model_memory(self) -> None:
        task = {"content": {"stem": "Staphylococcus aureus", "options": []}}
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [issue(before="Staphylococcus aureus", after="Staphylococcus aerues")],
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")
        self.assertIn("拉丁學名", uncorrected[0]["reason"])

    def test_explicitly_unverified_propose_rule_stays_manual(self) -> None:
        task = {"content": {"stem": "横川吸蟲", "options": []}}
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [
                issue(
                    before="横川",
                    after="橫川",
                    note="模型指出可能應改，但目前沒有逐字來源或語意錨點證據；保留人工核對。",
                )
            ],
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")
        self.assertIn("尚無來源證據", uncorrected[0]["reason"])

    def test_abbreviated_binomial_is_preserved_and_not_expanded(self) -> None:
        task = {"content": {"stem": "B. cereus", "options": []}}
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [
                issue(
                    before="B.",
                    after="Bacillus",
                    note="模型想展開學名縮寫",
                )
            ],
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")
        self.assertIn("拉丁學名", uncorrected[0]["reason"])

    def test_continuation_marker_is_group_owned_not_a_text_patch(self) -> None:
        task = {
            "content": {
                "stem": "承上題，下列何者正確？",
                "options": [],
            }
        }
        routed = self.module.route_group_scope_issues(
            task,
            [
                issue(
                    before="承上題",
                    after=None,
                    issue_family="group_dependency",
                    route="group",
                    source_class="source_unverified",
                )
            ],
        )
        self.assertEqual(routed[0]["field"], "group_ref")
        self.assertIsNone(routed[0]["before"])
        self.assertIsNone(routed[0]["after"])
        self.assertEqual(routed[0]["route"], "group")
        correction, _changes, coverage, _uncorrected = self.module.build_candidate_suggestion(
            task, routed
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")

    def test_capsule_compound_name_requires_pdf_before_patch(self) -> None:
        task = {
            "content": {
                "stem": "荧膜組織肥漿菌（Histoplasma capsulatum）",
                "options": [],
            }
        }
        correction, _changes, coverage, uncorrected = self.module.build_candidate_suggestion(
            task,
            [issue(before="荧膜", after="莢膜", note="字形疑點")],
        )
        self.assertIsNone(correction)
        self.assertEqual(coverage, "none")
        self.assertIn("完整菌名", uncorrected[0]["reason"])


if __name__ == "__main__":
    unittest.main()
