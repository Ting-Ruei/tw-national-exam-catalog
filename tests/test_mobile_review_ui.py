from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import serve_question_review_ui as review_ui  # noqa: E402


class MobileReviewUiTests(unittest.TestCase):
    def test_mobile_assets_are_scoped_and_installable(self) -> None:
        page = review_ui.mobile_asset_response("/mobile/")
        manifest_asset = review_ui.mobile_asset_response("/mobile/manifest.webmanifest")
        worker = review_ui.mobile_asset_response("/mobile/sw.js")
        icon = review_ui.mobile_asset_response("/mobile/icon.png")

        self.assertIsNotNone(page)
        self.assertIsNotNone(manifest_asset)
        self.assertIsNotNone(worker)
        self.assertIsNotNone(icon)
        assert page is not None and manifest_asset is not None and worker is not None and icon is not None

        manifest = json.loads(manifest_asset[0])
        worker_text = worker[0].decode("utf-8")

        self.assertEqual(manifest["start_url"], "/mobile/")
        self.assertEqual(manifest["scope"], "/mobile/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(icon[0].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"/api/mobile-review", page[0])
        self.assertIn(
            "if (request.method !== 'GET' || url.pathname.startsWith('/api/')) return;",
            worker_text,
        )

    def test_mobile_page_exposes_decisions_and_queue_navigation(self) -> None:
        page = review_ui.mobile_asset_response("/mobile/")
        self.assertIsNotNone(page)
        assert page is not None
        html = page[0].decode("utf-8")

        for label in ("通過", "手機不看", "阻止", "上一題", "備註待看", "下一題"):
            self.assertIn(label, html)
        self.assertNotIn("要・通過", html)
        self.assertNotIn("不要・阻止", html)
        self.assertNotIn("你只做分流", html)
        self.assertNotIn("跳過", html)
        self.assertIn("submitReview('accept')", html)
        self.assertIn("submitReview('reject')", html)
        self.assertIn("submitReview('note'", html)
        self.assertIn("goToQueue(-1)", html)
        self.assertIn("goToQueue(1)", html)
        self.assertNotIn("splice(state.index", html)
        self.assertIn("paperQueueQuery(scope)", html)
        self.assertIn("resetHistory(selectedIndex)", html)
        self.assertIn("findSelectableIndex(state.index, 1)", html)

    def test_mobile_uses_desktop_text_renderer_features(self) -> None:
        page = review_ui.mobile_asset_response("/mobile/")
        self.assertIsNotNone(page)
        assert page is not None
        html = page[0].decode("utf-8")

        for renderer_feature in (
            "const greekMap",
            "function renderMath",
            "function normalizeInlineScienceText",
            "function canonicalizeUnicodeScripts",
            "function renderInlineMarkupEscaped",
            "function renderText",
            '<span class="overline">',
            "<sub>$1</sub>",
            "<sup>$1</sup>",
        ):
            self.assertIn(renderer_feature, html)

    def test_mobile_overview_is_unfiltered_within_current_exam_scope(self) -> None:
        page = review_ui.mobile_asset_response("/mobile/")
        self.assertIsNotNone(page)
        assert page is not None
        html = page[0].decode("utf-8")

        self.assertIn('id="openOverview"', html)
        self.assertIn('id="overviewGrid"', html)
        self.assertIn("function overviewQuery(scope)", html)
        self.assertIn("function paperQueueQuery(scope)", html)
        self.assertIn("reviewStatus: ''", html)
        self.assertIn("aiReviewStatus: ''", html)
        self.assertIn("category: scope.category", html)
        self.assertIn("subject: scope.subject", html)
        self.assertIn("year: scope.year", html)
        self.assertIn("ordinal: scope.ordinal", html)
        self.assertIn("limit: '1000'", html)
        self.assertIn("overviewStatusClass(candidate)", html)
        self.assertIn("'mobile-deferred'", html)

    def test_mobile_question_flags_are_prominent(self) -> None:
        page = review_ui.mobile_asset_response("/mobile/")
        self.assertIsNotNone(page)
        assert page is not None
        html = page[0].decode("utf-8")

        for label in ("題組", "有圖", "疑似有圖", "表格", "人工註記", "格式疑點", "AI 疑點"):
            self.assertIn(label, html)
        self.assertIn("function itemFlagHtml(item)", html)
        self.assertIn("review-flag", html)

    def test_mobile_decisions_map_to_safe_review_events(self) -> None:
        cases = [
            ("accept", "accept", False),
            ("reject", "block", True),
            ("note", "needs_review", True),
            ("defer", "mobile_defer", False),
            ("resume", "mobile_resume", False),
        ]
        for disposition, expected_action, followup in cases:
            with self.subTest(disposition=disposition):
                event = review_ui.mobile_review_event(
                    {
                        "candidate_key": "sample-key",
                        "disposition": disposition,
                        "notes": "手機註記" if disposition == "note" else "",
                    }
                )

                self.assertEqual(event["action"], expected_action)
                self.assertEqual(event["source"], "mobile_triage")
                self.assertIs(event["ai_followup"]["requested"], followup)
                self.assertIs(event["ai_followup"]["apply_only_approved_rules"], True)
                self.assertIs(event["ai_followup"]["new_rule_requires_human_approval"], True)
                self.assertIs(event["ai_followup"]["auto_accept_allowed"], False)

    def test_mobile_defer_is_a_reversible_non_question_review_tag(self) -> None:
        deferred = review_ui.mobile_review_event(
            {"candidate_key": "sample-key", "disposition": "defer"}
        )
        resumed = review_ui.mobile_review_event(
            {"candidate_key": "sample-key", "disposition": "resume"}
        )

        self.assertEqual(deferred["action"], "mobile_defer")
        self.assertEqual(resumed["action"], "mobile_resume")
        self.assertTrue(deferred["mobile_only_tag"])
        self.assertTrue(resumed["mobile_only_tag"])
        self.assertFalse(deferred["ai_followup"]["requested"])
        self.assertFalse(resumed["ai_followup"]["requested"])
        self.assertIn("mobile_defer", review_ui.NON_QUESTION_REVIEW_ACTIONS)
        self.assertIn("mobile_resume", review_ui.NON_QUESTION_REVIEW_ACTIONS)

    def test_mobile_tag_does_not_replace_latest_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review_log = Path(tmp) / "question_review_events.jsonl"
            review_log.write_text(
                "\n".join(
                    json.dumps(event, ensure_ascii=False)
                    for event in (
                        {"candidate_key": "sample-key", "action": "accept"},
                        {"candidate_key": "sample-key", "action": "mobile_defer"},
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            latest, counts, resets = review_ui.load_review_events(review_log)
            state = review_ui.ReviewState.__new__(review_ui.ReviewState)
            state.sql_review_enabled = False
            state.review_log = review_log
            mobile = state._mobile_review_maps(["sample-key"])

        self.assertEqual(latest["sample-key"]["action"], "accept")
        self.assertEqual(counts["sample-key"], 1)
        self.assertEqual(resets, {})
        self.assertTrue(mobile["sample-key"]["deferred"])

    def test_mobile_note_requires_a_note(self) -> None:
        with self.assertRaisesRegex(ValueError, "notes are required"):
            review_ui.mobile_review_event(
                {
                    "candidate_key": "sample-key",
                    "disposition": "note",
                    "notes": " ",
                }
            )

    def test_mobile_asset_loader_does_not_expose_other_files(self) -> None:
        self.assertIsNone(review_ui.mobile_asset_response("/mobile/../README.md"))
        self.assertIsNone(review_ui.mobile_asset_response("/README.md"))

    def test_mobile_listener_uses_a_constrained_handler(self) -> None:
        self.assertTrue(issubclass(review_ui.MobileHandler, review_ui.Handler))
        self.assertIsNot(review_ui.MobileHandler.do_GET, review_ui.Handler.do_GET)
        self.assertIsNot(review_ui.MobileHandler.do_POST, review_ui.Handler.do_POST)


if __name__ == "__main__":
    unittest.main()
