from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    PROJECT_ROOT / "docs" / "skills" / "national-exam-ai-audit" / "scripts"
)


def import_script():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "fetch_review_ui_backlog",
        SCRIPTS / "fetch_review_ui_backlog.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class FetchReviewUiBacklogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = import_script()

    def freezer(
        self,
        output_dir: Path,
        *,
        review_status: str = "correct",
        ai_review_status: str = "needs_review",
        q: str = "聽力",
        resume: bool = False,
        expect_ai_model: str | None = None,
        expect_ai_provider: str | None = None,
    ):
        return self.module.BacklogFreezer(
            base_url="https://review.example/",
            output_dir=output_dir,
            review_status=review_status,
            ai_review_status=ai_review_status,
            q=q,
            limit=100,
            timeout=5,
            resume=resume,
            expect_ai_model=expect_ai_model,
            expect_ai_provider=expect_ai_provider,
        )

    def test_cli_accepts_review_ai_and_text_scope(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "fetch_review_ui_backlog.py",
                "--output-dir",
                "snapshot",
                "--review-status",
                "correct",
                "--ai-review-status",
                "needs_review",
                "--q",
                "聽力",
                "--expect-ai-model",
                "luna-4",
                "--expect-ai-provider",
                "llmshare",
            ],
        ):
            args = self.module.parse_args()
        self.assertEqual(args.review_status, "correct")
        self.assertEqual(args.ai_review_status, "needs_review")
        self.assertEqual(args.q, "聽力")
        self.assertEqual(args.expect_ai_model, "luna-4")
        self.assertEqual(args.expect_ai_provider, "llmshare")

    def test_every_api_query_contains_fixed_base_scope(self) -> None:
        payload = {
            "candidates": [],
            "filtered_count": 0,
            "returned_count": 0,
        }
        with tempfile.TemporaryDirectory() as temporary:
            freezer = self.freezer(Path(temporary))
            with mock.patch.object(
                self.module.urllib.request,
                "urlopen",
                return_value=FakeResponse(payload),
            ) as urlopen:
                freezer.fetch({"category": "聽力師"})

        request = urlopen.call_args.args[0]
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(request.full_url).query,
            keep_blank_values=True,
        )
        self.assertEqual(query["reviewStatus"], ["correct"])
        self.assertEqual(query["aiReviewStatus"], ["needs_review"])
        self.assertEqual(query["q"], ["聽力"])
        self.assertEqual(query["category"], ["聽力師"])
        self.assertEqual(query["limit"], ["100"])

    def test_resume_probe_key_is_isolated_by_base_scope(self) -> None:
        filters = {"category": "fixture"}
        first_scope = {
            "reviewStatus": "unreviewed",
            "aiReviewStatus": "unreviewed",
            "q": "first",
        }
        second_scope = {**first_scope, "q": "second"}
        self.assertNotEqual(
            self.module.probe_name(filters, first_scope),
            self.module.probe_name(filters, second_scope),
        )
        self.assertEqual(
            self.module.probe_name(filters, first_scope),
            self.module.probe_name(dict(filters), dict(first_scope)),
        )
        self.assertNotEqual(
            self.module.probe_name(
                filters,
                first_scope,
                {"model": "luna-4", "provider": "llmshare"},
            ),
            self.module.probe_name(
                filters,
                first_scope,
                {"model": "luna-5", "provider": "llmshare"},
            ),
        )

    def test_resume_does_not_reuse_probe_from_different_scope(self) -> None:
        first_payload = {
            "candidates": [],
            "filtered_count": 0,
            "returned_count": 0,
        }
        second_payload = {
            "candidates": [{"candidate_key": "scope-two"}],
            "filtered_count": 1,
            "returned_count": 1,
        }
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            first = self.freezer(output_dir, q="first")
            with mock.patch.object(
                self.module.urllib.request,
                "urlopen",
                return_value=FakeResponse(first_payload),
            ):
                first.fetch({})

            second = self.freezer(output_dir, q="second", resume=True)
            with mock.patch.object(
                self.module.urllib.request,
                "urlopen",
                return_value=FakeResponse(second_payload),
            ) as urlopen:
                payload = second.fetch({})

        urlopen.assert_called_once()
        self.assertEqual(payload["candidates"][0]["candidate_key"], "scope-two")

    def test_manifest_records_exact_base_scope(self) -> None:
        root_payload = {
            "candidates": [
                {
                    "candidate_key": "fixture-key",
                    "ai_review": {
                        "model": "聽力-luna-4",
                        "provider": "llmshare",
                    },
                }
            ],
            "filtered_count": 1,
            "returned_count": 1,
            "facets": {"categories": ["fixture"]},
        }
        with tempfile.TemporaryDirectory() as temporary:
            freezer = self.freezer(
                Path(temporary),
                expect_ai_model="聽力-luna-4",
                expect_ai_provider="llmshare",
            )

            def fake_fetch(filters: dict[str, str]) -> dict:
                self.assertIn(filters, ({}, {"category": "fixture"}))
                return root_payload

            freezer.fetch = fake_fetch
            manifest = freezer.run()

        self.assertEqual(manifest["review_status"], "correct")
        self.assertEqual(manifest["ai_review_status"], "needs_review")
        self.assertEqual(manifest["q"], "聽力")
        self.assertEqual(
            manifest["base_scope"],
            {
                "reviewStatus": "correct",
                "aiReviewStatus": "needs_review",
                "q": "聽力",
            },
        )
        self.assertEqual(
            manifest["ai_review_expectation"],
            {
                "model": "聽力-luna-4",
                "provider": "llmshare",
            },
        )

    def test_leaf_rejects_q_match_outside_latest_ai_identity(self) -> None:
        payload = {
            "candidates": [
                {
                    "candidate_key": "false-hit",
                    "stem": "聽力 appears only in the stem",
                    "ai_review": {
                        "model": "luna-4",
                        "provider": "llmshare",
                    },
                }
            ],
            "filtered_count": 1,
            "returned_count": 1,
        }
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            freezer = self.freezer(output_dir)
            with self.assertRaisesRegex(
                RuntimeError,
                "q matched outside latest AI identity",
            ):
                freezer.freeze_leaf({"category": "fixture"}, payload)
            self.assertFalse((output_dir / "shards").exists())
            self.assertEqual(freezer.leaves, [])

    def test_leaf_requires_exact_latest_ai_model_and_provider(self) -> None:
        payload = {
            "candidates": [
                {
                    "candidate_key": "wrong-provider",
                    "ai_review": {
                        "model": "luna-4",
                        "provider": "codex",
                    },
                }
            ],
            "filtered_count": 1,
            "returned_count": 1,
        }
        with tempfile.TemporaryDirectory() as temporary:
            freezer = self.freezer(
                Path(temporary),
                q="luna",
                expect_ai_model="luna-4",
                expect_ai_provider="llmshare",
            )
            with self.assertRaisesRegex(RuntimeError, "latest AI provider mismatch"):
                freezer.freeze_leaf({"category": "fixture"}, payload)

    def test_leaf_accepts_q_and_exact_latest_ai_expectation(self) -> None:
        payload = {
            "candidates": [
                {
                    "candidate_key": "expected",
                    "ai_review": {
                        "model": "Luna-4",
                        "provider": "llmshare",
                    },
                }
            ],
            "filtered_count": 1,
            "returned_count": 1,
        }
        with tempfile.TemporaryDirectory() as temporary:
            freezer = self.freezer(
                Path(temporary),
                q="lUnA",
                expect_ai_model="Luna-4",
                expect_ai_provider="llmshare",
            )
            freezer.freeze_leaf({"category": "fixture"}, payload)
            shard_path = Path(freezer.leaves[0]["path"])
            self.assertTrue(shard_path.exists())


if __name__ == "__main__":
    unittest.main()
