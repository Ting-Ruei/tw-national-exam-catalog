from __future__ import annotations

import threading
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import serve_question_review_ui as review_ui  # noqa: E402


class _FakeCursor:
    def __init__(self) -> None:
        self.execute_calls: list[str] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, _values: object = None) -> None:
        self.execute_calls.append(query)

    def fetchone(self) -> tuple[int, ...]:
        return tuple(range(1, 20))


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor_value = cursor

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.cursor_value


class ReviewUiPerformanceTests(unittest.TestCase):
    def test_pipeline_sql_uses_one_consolidated_query(self) -> None:
        cursor = _FakeCursor()
        connection = _FakeConnection(cursor)
        state = review_ui.ReviewState.__new__(review_ui.ReviewState)
        state.sql_review_enabled = True
        state.legacy_jsonl_backup_enabled = False
        state.candidate_path = Path("candidates.jsonl")
        state.issue_path = None
        state.review_log = Path("question_review_events.jsonl")
        state.formal_sync_status = Mock(return_value={})

        @contextmanager
        def fake_connect():
            yield connection

        state._sql_connect = fake_connect
        payload = state._sql_pipeline_payload()

        self.assertEqual(len(cursor.execute_calls), 1)
        query = cursor.execute_calls[0]
        self.assertIn("active_candidates AS MATERIALIZED", query)
        self.assertIn("latest_question AS MATERIALIZED", query)
        self.assertIn("latest_answer AS MATERIALIZED", query)
        self.assertEqual(payload["layers"][0]["count"], 1)

    def test_pipeline_payload_reuses_fresh_sql_statistics(self) -> None:
        state = review_ui.ReviewState.__new__(review_ui.ReviewState)
        state.sql_review_enabled = True
        state._pipeline_cache_lock = threading.Lock()
        state._pipeline_cache = None
        state._pipeline_refreshing = False
        state._pipeline_cache_ttl_seconds = 30.0
        state._sql_pipeline_payload = Mock(return_value={"layers": [], "storage": {}})

        first = state.pipeline_payload()
        second = state.pipeline_payload()

        self.assertEqual(state._sql_pipeline_payload.call_count, 1)
        self.assertEqual(first["statistics_updated_at"], second["statistics_updated_at"])
        self.assertFalse(second["statistics_stale"])


if __name__ == "__main__":
    unittest.main()
