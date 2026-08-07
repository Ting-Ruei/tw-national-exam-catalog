from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def import_review_ui():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "serve_question_review_ui_scope_test",
        ROOT / "scripts" / "serve_question_review_ui.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReviewUiScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = import_review_ui()

    def test_group_only_ai_event_is_removed_from_question_scope(self):
        question, group = self.ui.split_ai_audit_scopes(
            {"stem": "承上題，下列何者正確？"},
            {
                "status": "needs_review",
                "recommended_action": "review_group",
                "labels": ["group_dependency"],
                "findings": [
                    {
                        "code": "group_dependency",
                        "field": "stem",
                        "observed": "承上題",
                        "suggested": None,
                    }
                ],
            },
        )
        self.assertIsNotNone(group)
        self.assertEqual(group["audit_scope"], "group")
        self.assertEqual(question["status"], "pass")
        self.assertEqual(question["recommended_action"], "no_action")
        self.assertEqual(question["findings"], [])

    def test_mixed_ai_event_keeps_question_finding_and_separates_group(self):
        question, group = self.ui.split_ai_audit_scopes(
            {"stem": "承上題，下列何者正確？"},
            {
                "status": "needs_review",
                "recommended_action": "review_group",
                "labels": ["group_dependency", "ocr_character"],
                "findings": [
                    {
                        "code": "group_dependency",
                        "field": "stem",
                        "observed": "承上題",
                        "suggested": None,
                    },
                    {
                        "code": "ocr_character",
                        "field": "stem",
                        "observed": "錯",
                        "suggested": "正",
                    },
                ],
            },
        )
        self.assertEqual(len(group["findings"]), 1)
        self.assertEqual(len(question["findings"]), 1)
        self.assertEqual(question["findings"][0]["code"], "ocr_character")
        self.assertNotIn("group_dependency", question["labels"])

    def test_verified_compound_capsule_patch_is_one_click_safe(self):
        reason = self.ui.ai_patch_safety_reason(
            {
                "stem": "",
                "options": [
                    {"key": "A", "text": "荧膜組織胞漿菌（Histoplasma capsulatum）"}
                ],
            },
            {
                "status": "needs_review",
                "findings": [
                    {
                        "field": "option_A",
                        "before": "荧膜",
                        "after": "莢膜",
                        "route": "propose_rule",
                    }
                ],
            },
        )
        self.assertIsNone(reason)
        suggestion, changes = self.ui.ai_suggested_correction(
                {
                    "stem": "",
                    "options": [
                        {"key": "A", "text": "荧膜組織胞漿菌（Histoplasma capsulatum）"}
                    ],
                },
                {
                    "status": "needs_review",
                    "suggested_correction": {
                        "options": [
                            {"key": "A", "text": "莢膜組織胞漿菌（Histoplasma capsulatum）"}
                        ]
                    },
                    "findings": [
                        {
                            "field": "option_A",
                            "before": "荧膜",
                            "after": "莢膜",
                            "route": "propose_rule",
                        }
                    ],
                },
            )
        self.assertIsNotNone(suggestion)
        self.assertEqual(
            suggestion["options"][0]["text"],
            "莢膜組織胞漿菌（Histoplasma capsulatum）",
        )
        self.assertTrue(changes)

    def test_verified_capsule_glyph_rule_offers_partial_one_click_patch(self):
        candidate = {
            "stem": "",
            "options": [
                {"key": "C", "text": "荧膜組織漿菌（Histoplasma capsulatum）"}
            ],
        }
        audit = {
            "status": "needs_review",
            "findings": [
                {
                    "field": "option_C",
                    "before": "荧膜",
                    "after": "莢膜",
                    "route": "human_pdf",
                }
            ],
        }
        self.assertIsNone(self.ui.ai_patch_safety_reason(candidate, audit))
        suggestion, changes = self.ui.ai_suggested_correction(candidate, audit)
        self.assertEqual(
            suggestion["options"][0]["text"],
            "莢膜組織漿菌（Histoplasma capsulatum）",
        )
        self.assertIn("選項 C: 荧膜 -> 莢膜", changes)

    def test_review_page_has_one_header_batch_button_and_inline_ai_rating(self):
        page = self.ui.PAGE_HTML
        self.assertEqual(page.count(">批次通過本頁 pass</button>"), 1)
        quick_actions = page[page.index("const questionQuickActions"):page.index("const manualReviewPanel")]
        non_question_at = quick_actions.index("review('exclude')")
        rating_at = quick_actions.index("${aiFeedbackButtons}")
        reason_at = quick_actions.index("${aiFeedbackReasonField}")
        self.assertLess(non_question_at, rating_at)
        self.assertLess(rating_at, reason_at)
        self.assertEqual(page.count('id="aiFeedbackReason"'), 1)
        self.assertIn("submitAiLearning('question')", page)
        self.assertIn("加入 AI 學習", page)
        self.assertIn("active-learn", page)
        self.assertIn("插入空上橫槓模板", page)
        self.assertIn("插入空下橫槓模板", page)
        self.assertIn("上橫槓</button>", page)
        self.assertIn("下橫槓</button>", page)
        self.assertIn("wrapCorrectionSelection('overline')", page)
        self.assertIn("wrapCorrectionSelection('underline')", page)

    def test_review_page_uses_global_search_and_stable_session_queue(self):
        page = self.ui.PAGE_HTML
        self.assertIn("全域搜尋題幹、選項、註記（跨狀態）", page)
        self.assertIn("globalSearch ? 'visual_all'", page)
        self.assertIn("mode === 'answer' && !globalSearch", page)
        self.assertIn("mode === 'group' && !globalSearch", page)
        self.assertIn("刷新並重排", page)
        self.assertIn("本輪已更新；刷新後重排", page)
        self.assertIn("options.allowStaleModeCache === true", page)
        self.assertIn("workflowStageLabel(item, 'question')", page)
        self.assertIn("workflowStageLabel(item, 'answer')", page)
        self.assertIn("workflowStageLabel(item, 'group')", page)
        self.assertIn("workflowStageLabel(item, 'visual')", page)

        visual_advance = page[page.index("function advanceAfterVisualReview"):page.index("async function saveVisualReviewStatus")]
        group_advance = page[page.index("async function advanceAfterGroupReview"):page.index("async function confirmCurrentSheetGroup")]
        answer_batch_tail = page[page.index("async function answerSheetReviewAction"):page.index("async function answerReviewAction")]
        review_action = page[page.index("async function review(action"):page.index("document.getElementById('search')")]
        self.assertNotIn(".splice(", visual_advance)
        self.assertNotIn(".splice(", group_advance)
        self.assertNotIn(".splice(", answer_batch_tail)
        self.assertNotIn(".splice(", review_action)
        self.assertNotIn("scheduleModeBackgroundRefresh", group_advance)
        self.assertNotIn("await fetchCandidates", review_action)

    def test_sql_queue_searches_effective_options_and_prioritizes_open_attention(self):
        state = object.__new__(self.ui.ReviewState)
        heavy_cte, _ = state._sql_candidate_filter_parts({"q": "缓"})
        light_cte, _ = state._sql_light_candidate_filter_parts({"reviewStatus": "unreviewed"})
        source = (ROOT / "scripts" / "serve_question_review_ui.py").read_text(encoding="utf-8")

        self.assertIn("raw_candidate_json::text", heavy_cte)
        self.assertIn("latest_question_ai AS", light_cte)
        self.assertIn("issue_flags AS", light_cte)
        self.assertIn("has_active_attention", light_cte)
        self.assertIn("CASE WHEN is_reviewed THEN 1 ELSE 0 END", source)
        self.assertIn("CASE WHEN reviewed_count < question_count THEN 0 ELSE 1 END", source)
        self.assertIn("CASE WHEN f.group_action IN ('', 'reset_group_review') THEN 0 ELSE 1 END", source)
        self.assertIn("lq.corrected_candidate_json::text", source)
        self.assertIn("la.corrected_answer_json::text", source)

    def test_correction_notation_normalizer_handles_groups_symbols_and_word_boundaries(self):
        page_script = self.ui.PAGE_HTML.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        start = page_script.index("const greekMap =")
        end = page_script.index("async function load()")
        harness = page_script[start:end] + r'''
const inputs = [
  'C_{r}',
  'HCO_{3}^{-}',
  '_{ }',
  '\\alpha _{1}',
  '\\rightarrow \\times \\pm \\uparrow \\downarrow \\propto \\div',
  '\\mathrm{CO}_{2}',
  '\\bar{x} \\overline{y} \\underline{z}',
  '\\log_{10}',
  'hypothyroidism PO2 PO',
  'A\\_B',
  'D_{u(0-t)}'
];
process.stdout.write(JSON.stringify(inputs.map(normalizeCorrectionNotation)));
'''
        completed = subprocess.run(
            ["node", "-e", harness],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(completed.stdout),
            [
                "C<sub>r</sub>",
                "HCO<sub>3</sub><sup>-</sup>",
                "<sub></sub>",
                "α <sub>1</sub>",
                "→ × ± ↑ ↓ ∝ ÷",
                "CO<sub>2</sub>",
                '<span class="overline">x</span> <span class="overline">y</span> <u>z</u>',
                "log<sub>10</sub>",
                "hypothyroidism PO<sub>2</sub> PO",
                "A_B",
                "D<sub>u(0-t)</sub>",
            ],
        )
        self.assertIn('id="normalizationStatus"', self.ui.PAGE_HTML)
        self.assertIn('class="panel question-correction-panel" open', self.ui.PAGE_HTML)

    def test_legacy_abbreviated_name_expansion_is_suppressed(self):
        reason = self.ui.ai_patch_safety_reason(
            {"stem": "", "options": [{"key": "A", "text": "B. cereus"}]},
            {
                "status": "needs_review",
                "findings": [
                    {
                        "field": "option_A",
                        "before": "B.",
                        "after": "Bacillus",
                        "route": "propose_rule",
                    }
                ],
            },
        )
        self.assertIsNotNone(reason)

    def test_reset_review_keeps_previous_human_content_and_manual_image(self):
        candidate = {
            "candidate_key": "reset-keeps-human-content",
            "question_number": "1",
            "stem": "MinerU 原始題幹",
            "options": [
                {"key": "A", "text": "原始選項", "image": {"path": "20_mineru_output/wrong.png"}}
            ],
            "metadata": {},
        }
        correction = {
            "stem": "人工校正題幹",
            "options": [
                {
                    "key": "A",
                    "text": "人工校正選項",
                    "image": {
                        "path": "40_manual_assets/correct.png",
                        "manual_asset": True,
                    },
                }
            ],
            "visual_review": "visual_asset_ok",
        }
        events = [
            {
                "candidate_key": candidate["candidate_key"],
                "action": "accept",
                "correction": correction,
            },
            {
                "candidate_key": candidate["candidate_key"],
                "action": "reset_review",
                # SQL fallback materializes a missing JSON correction as null;
                # null must still inherit the historical human content.
                "correction": None,
                "notes": "重新審核狀態，內容不可回退",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidates.jsonl"
            review_log = root / "question_review_events.jsonl"
            candidate_path.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
            review_log.write_text(
                "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
                encoding="utf-8",
            )

            latest, _, resets = self.ui.load_review_events(review_log)
            state = self.ui.ReviewState(
                candidate_path,
                None,
                review_log,
                review_backend="jsonl",
            )
            payload = state.candidate_payload(candidate)

        self.assertEqual(latest, {})
        self.assertEqual(resets[candidate["candidate_key"]]["correction"], correction)
        self.assertEqual(payload["stem"], "人工校正題幹")
        self.assertEqual(payload["options"][0]["text"], "人工校正選項")
        self.assertTrue(payload["options"][0]["image"]["path"].endswith("40_manual_assets/correct.png"))
        self.assertTrue(payload["review"]["is_reset_unreviewed"])

    def test_reset_review_allows_ai_one_click_suggestion(self):
        candidate = {
            "candidate_key": "reset-allows-ai-suggestion",
            "question_number": "1",
            "stem": "下列何者具有荧膜？",
            "options": [],
            "metadata": {},
        }
        reset_event = {
            "candidate_key": candidate["candidate_key"],
            "action": "reset_review",
            "created_at": "2026-08-04T08:00:00+08:00",
        }
        ai_event = {
            "candidate_key": candidate["candidate_key"],
            "action": "ai_audit",
            "created_at": "2026-08-04T08:01:00+08:00",
            "provider": "luna",
            "model": "test-model",
            "audit": {
                "status": "needs_review",
                "findings": [
                    {
                        "field": "stem",
                        "before": "荧膜",
                        "after": "莢膜",
                        "route": "deterministic",
                        "rule_id": "global-ocr-capsule-traditional-glyph",
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidates.jsonl"
            review_log = root / "question_review_events.jsonl"
            candidate_path.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
            review_log.write_text(json.dumps(reset_event, ensure_ascii=False) + "\n", encoding="utf-8")
            (root / "question_ai_review_events.jsonl").write_text(
                json.dumps(ai_event, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            state = self.ui.ReviewState(candidate_path, None, review_log, review_backend="jsonl")
            payload = state.candidate_payload(candidate)

        self.assertEqual(payload["review"]["action"], "reset_review")
        self.assertEqual(payload["ai_review"]["suggested_correction"]["stem"], "下列何者具有莢膜？")
        self.assertTrue(payload["ai_review"]["suggestion_apply_allowed"])

    def test_luna_worklist_uses_persistent_content_projection(self):
        source = (
            ROOT
            / "docs"
            / "skills"
            / "national-exam-ai-audit"
            / "scripts"
            / "build_sql_review_worklist.py"
        ).read_text(encoding="utf-8")
        self.assertIn("latest_content_correction AS", source)
        self.assertIn("COALESCE(lcc.corrected_candidate_json", source)
        self.assertIn("LEFT JOIN latest_content_correction lcc", source)

    def test_ai_feedback_is_append_only_and_bound_to_exact_audit(self):
        candidate = {
            "candidate_key": "ai-feedback-test",
            "question_number": "1",
            "stem": "測試題幹",
            "options": [],
            "metadata": {},
        }
        ai_event = {
            "candidate_key": candidate["candidate_key"],
            "action": "ai_audit",
            "created_at": "2026-08-03T12:00:00+08:00",
            "provider": "luna",
            "model": "test-model",
            "prompt_version": "test-v1",
            "input_hash": "abc",
            "audit": {
                "status": "needs_review",
                "summary": "疑似錯字",
                "findings": [{"code": "ocr_character", "field": "stem"}],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidates.jsonl"
            review_log = root / "question_review_events.jsonl"
            candidate_path.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
            review_log.write_text("", encoding="utf-8")
            (root / "question_ai_review_events.jsonl").write_text(
                json.dumps(ai_event, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            state = self.ui.ReviewState(candidate_path, None, review_log, review_backend="jsonl")
            event_ref = self.ui.ai_review_reference(ai_event)
            saved = state.append_ai_feedback(
                {
                    "candidate_key": candidate["candidate_key"],
                    "audit_scope": "question",
                    "rating": "down",
                    "reviewer": "local",
                    "reason": "錯把正確學名標成錯字",
                    "ai_review_ref": event_ref,
                }
            )
            payload = state.candidate_payload(candidate)
            feedback_lines = (root / "question_ai_feedback_events.jsonl").read_text(encoding="utf-8").splitlines()

            with self.assertRaisesRegex(ValueError, "has changed"):
                state.append_ai_feedback(
                    {
                        "candidate_key": candidate["candidate_key"],
                        "rating": "up",
                        "ai_review_ref": "sha256:stale",
                    }
                )

        self.assertEqual(saved["rating"], "down")
        self.assertEqual(saved["ai_context"]["prompt_version"], "test-v1")
        self.assertEqual(len(feedback_lines), 1)
        self.assertEqual(payload["ai_review"]["feedback"]["reason"], "錯把正確學名標成錯字")

    def test_ai_learning_selection_is_independent_and_captures_effective_candidate(self):
        candidate = {
            "candidate_key": "ai-learning-test",
            "question_number": "2",
            "stem": "原始題幹",
            "options": [{"key": "A", "text": "原始選項"}],
            "answer": "A",
            "metadata": {
                "normalized_category_name": "醫事檢驗師",
                "normalized_subject_name": "生物化學",
                "year": "114",
                "exam_ordinal": "1",
            },
        }
        ai_event = {
            "candidate_key": candidate["candidate_key"],
            "action": "ai_audit",
            "created_at": "2026-08-03T12:00:00+08:00",
            "provider": "luna",
            "model": "test-model",
            "prompt_version": "test-v1",
            "input_hash": "learning-hash",
            "audit": {"status": "needs_review", "summary": "可作為訓練案例", "findings": []},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidates.jsonl"
            review_log = root / "question_review_events.jsonl"
            candidate_path.write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")
            review_log.write_text(
                json.dumps(
                    {
                        "candidate_key": candidate["candidate_key"],
                        "action": "correct",
                        "correction": {
                            "stem": "人工有效題幹",
                            "options": [{"key": "A", "text": "人工有效選項"}],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "question_ai_review_events.jsonl").write_text(
                json.dumps(ai_event, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            state = self.ui.ReviewState(candidate_path, None, review_log, review_backend="jsonl")
            saved = state.append_ai_learning(
                {
                    "candidate_key": candidate["candidate_key"],
                    "audit_scope": "question",
                    "reviewer": "local",
                    "reason": "這是我提供的訓練材料",
                    "ai_review_ref": self.ui.ai_review_reference(ai_event),
                }
            )
            payload = state.candidate_payload(candidate)
            learning_lines = (root / "question_ai_learning_events.jsonl").read_text(encoding="utf-8").splitlines()

        self.assertEqual(saved["action"], "ai_learning")
        self.assertTrue(saved["selected"])
        self.assertEqual(saved["candidate_snapshot"]["stem"], "人工有效題幹")
        self.assertEqual(saved["candidate_snapshot"]["parser_original"]["stem"], "原始題幹")
        self.assertEqual(len(learning_lines), 1)
        self.assertEqual(payload["ai_review"]["learning"]["reason"], "這是我提供的訓練材料")


if __name__ == "__main__":
    unittest.main()
