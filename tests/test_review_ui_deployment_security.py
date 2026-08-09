from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import serve_question_review_ui as review_ui  # noqa: E402


class StubHandler:
    require_authorization = review_ui.Handler.require_authorization

    def __init__(self, authorization: str = "") -> None:
        self.headers = {"Authorization": authorization}
        self.status = None
        self.response_headers: list[tuple[str, str]] = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.response_headers.append((key, value))

    def end_headers(self) -> None:
        return None

    def send_error(self, status: int, _message: str) -> None:
        self.status = status


class ReviewUiDeploymentSecurityTests(unittest.TestCase):
    def test_project_files_are_denied_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REVIEW_UI_ALLOW_PROJECT_FILES", None)
            self.assertIsNone(review_ui.safe_file_path(str(PROJECT_ROOT / "README.md")))

    def test_project_files_require_explicit_local_development_opt_in(self) -> None:
        with patch.dict(os.environ, {"REVIEW_UI_ALLOW_PROJECT_FILES": "1"}):
            self.assertEqual(
                review_ui.safe_file_path(str(PROJECT_ROOT / "README.md")),
                (PROJECT_ROOT / "README.md").resolve(),
            )

    def test_basic_auth_challenge_and_success(self) -> None:
        credentials = base64.b64encode(b"tim:secret").decode("ascii")
        with patch.dict(
            os.environ,
            {
                "REVIEW_UI_BASIC_AUTH_USERNAME": "tim",
                "REVIEW_UI_BASIC_AUTH_PASSWORD": "secret",
            },
        ):
            denied = StubHandler()
            self.assertTrue(denied.require_authorization())
            self.assertEqual(denied.status, 401)
            self.assertIn("WWW-Authenticate", {key for key, _value in denied.response_headers})

            allowed = StubHandler(f"Basic {credentials}")
            self.assertFalse(allowed.require_authorization())
            self.assertIsNone(allowed.status)

    def test_cutover_read_only_and_request_limit_are_environment_controlled(self) -> None:
        with patch.dict(
            os.environ,
            {"REVIEW_UI_READ_ONLY": "1", "REVIEW_UI_MAX_REQUEST_BYTES": "1024"},
        ):
            self.assertTrue(review_ui.Handler.writes_are_blocked())
            self.assertEqual(review_ui.Handler.max_request_bytes(), 1024)


if __name__ == "__main__":
    unittest.main()
