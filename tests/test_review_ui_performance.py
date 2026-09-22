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

ROOT = PROJECT_ROOT


def _function_body(source: str, name: str) -> str:
    """取一個 Python 函式的活體，去掉 docstring。

    整個檔案的字串搜尋會把「講這條規則的散文」當成規則本身——`filtered_candidate_payloads` 的
    註解裡就寫著 `_count`。所以要讀實際會執行的行。
    """
    import re

    match = re.search(rf"def {name}\(", source)
    assert match, f"找不到函式 {name}"
    start = source.find("\n", match.start())
    body = source[start:]
    # 到下一個同縮排的 `def ` 或檔尾為止。
    rest = re.search(r"\n    def ", body[1:])
    if rest:
        body = body[: rest.start() + 1]
    body = re.sub(r'""".*?"""', "", body, flags=re.S)
    return body


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


class CountOnlyRequestTests(unittest.TestCase):
    """`_count=1` 要兩個數字，不要一列。

    首頁的卡片只讀 `total_count` 與 `reviewed_count`，不畫任何一列，但它以前送 `limit=1000`——
    而 `_count` 伺服器從不讀。於是每一次開首頁都建、序列化、傳了一千筆完整 payload（在真實的
    佇列上實測：gzip 後 358.4 KB、原始 6.12 MB、1,000 列）。count-only 在同一份佇列上是同一個
    篩選迴圈、不建 payload（gzip 後 1.5 KB）。

    這裡的規則是「同一個迴圈、只是不 append」，所以計數必須與有建列時**一模一樣**——包含篩選。
    負對照：把 `_count` 拿掉，列數就回到 `limit`。
    """

    def _state(self):
        import json
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        candidates = tmp / "candidates.jsonl"
        rows = [
            {"candidate_key": f"k{i}", "question_number": i, "stem": f"題{i}",
             "options": [{"key": "A", "text": "甲"}], "answer": "A",
             "metadata": {"normalized_category_name": "藥師(一)", "year": 108,
                          "exam_ordinal": 1, "normalized_subject_name": "藥學(一)"}}
            for i in range(1, 26)
        ]
        rows.append({"candidate_key": "other", "question_number": 99, "stem": "別的",
                     "metadata": {"normalized_category_name": "物理治療師", "year": 115,
                                  "exam_ordinal": 2, "normalized_subject_name": "骨科"}})
        candidates.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
        log = tmp / "question_review_events.jsonl"
        log.write_text("", encoding="utf-8")
        state = review_ui.ReviewState(candidates, None, log, review_backend="jsonl")
        state.append_review({"candidate_key": "k1", "action": "accept", "reviewer": "local"})
        state.append_review({"candidate_key": "k2", "action": "block", "reviewer": "local"})
        return state

    def test_count_only_returns_no_rows_but_the_same_numbers(self) -> None:
        state = self._state()
        full = state.filtered_candidate_payloads({"limit": "1000"})
        counted = state.filtered_candidate_payloads({"limit": "1000", "_count": "1"})
        self.assertEqual(counted["returned_count"], 0, "count-only 不該回任何一列")
        self.assertEqual(counted["candidates"], [])
        self.assertEqual(
            (counted["filtered_count"], counted["reviewed_count"], counted["total_count"]),
            (full["filtered_count"], full["reviewed_count"], full["total_count"]),
            "count-only 的數字必須與建列時一模一樣",
        )
        self.assertEqual(full["returned_count"], 26)

    def test_count_only_counts_the_same_filtered_subset(self) -> None:
        state = self._state()
        full = state.filtered_candidate_payloads({"limit": "1000", "category": "藥師(一)"})
        counted = state.filtered_candidate_payloads(
            {"limit": "1000", "category": "藥師(一)", "_count": "1"}
        )
        self.assertEqual(counted["returned_count"], 0)
        self.assertEqual(counted["filtered_count"], 25)
        self.assertEqual(counted["filtered_count"], full["filtered_count"],
                         "篩選後的計數也必須一致")
        self.assertEqual(counted["reviewed_count"], full["reviewed_count"])

    def test_the_count_only_switch_is_recognised_the_same_way_in_both_backends(self) -> None:
        """JSONL 與 SQL 兩條路必須認得同一個開關，不能一條認、一條不認。"""
        for source in ("filtered_candidate_payloads", "filtered_candidate_payloads_sql"):
            with self.subTest(source=source):
                body = _function_body(
                    Path(ROOT / "scripts" / "serve_question_review_ui.py").read_text(encoding="utf-8"),
                    source,
                )
                self.assertIn('params.get("_count")', body, f"{source} 沒有讀 _count")
                self.assertIn("limit = 0", body, f"{source} 沒有把 limit 歸零")


class CountOnlyNegativeControlTests(unittest.TestCase):
    def test_without_the_switch_the_limit_is_still_honoured(self) -> None:
        state = CountOnlyRequestTests()._state()
        payload = state.filtered_candidate_payloads({"limit": "5"})
        self.assertEqual(payload["returned_count"], 5, "沒有 _count 時 limit 必須照舊")


if __name__ == "__main__":
    unittest.main()


class ImageContentTypeTests(unittest.TestCase):
    """A crop whose bytes are not what its name claims must not be mislabelled.

    Measured on the served queue: 44 option pictures are JPEG 2000. Serving them as `image/png`
    because the file says `.png` makes Chrome refuse to draw them - the file exists, the request
    returns 200, and the reviewer sees an empty box. The bytes therefore decide the type and the
    name is only the fallback.
    """

    def test_png_bytes_are_labelled_png(self) -> None:
        self.assertEqual(review_ui.content_type_of("a.bin", b"\x89PNG\r\n\x1a\n" + b"x" * 8),
                         "image/png")

    def test_jpeg_bytes_are_labelled_jpeg_whatever_the_name_says(self) -> None:
        self.assertEqual(review_ui.content_type_of("a.png", b"\xff\xd8\xff" + b"x" * 8),
                         "image/jpeg")

    def test_webp_and_gif_are_recognised(self) -> None:
        self.assertEqual(review_ui.content_type_of("a.png", b"RIFF\x00\x00\x00\x00WEBP"),
                         "image/webp")
        self.assertEqual(review_ui.content_type_of("a.png", b"GIF89a" + b"x" * 8), "image/gif")

    def test_a_pdf_is_labelled_pdf_not_binary(self) -> None:
        self.assertEqual(review_ui.content_type_of("a.pdf", b"%PDF-1.7"), "application/pdf")

    def test_an_unknown_file_falls_back_to_its_name(self) -> None:
        self.assertEqual(review_ui.content_type_of("a.txt", b"hello"),
                         "text/plain")


class FacetProjectionTests(unittest.TestCase):
    """The facets must not need a regex per row per request.

    Measured on the served queue: `facets()` walked all 79,090 candidates and ran
    `category_matches_filter` (two regex substitutions plus a `re.sub`) on each of them for
    **every** `/api/candidates` response. That was 0.755 s of the 0.79 s a single sitting cost, and
    a 32-sitting category paid it 32 times. The fix folds each row's category once per queue load,
    so the per-request cost is set membership - and the answer must be **identical**, because a
    faster wrong number is worse than the slow right one.

    The negative control is `_facets_reference`: the implementation as it was, kept here as a second
    engine. If the folded path and the reference ever disagree, this fails.
    """

    def _state(self) -> "review_ui.ReviewState":
        state = review_ui.ReviewState.__new__(review_ui.ReviewState)
        state.candidates = [
            {"candidate_key": "a", "metadata": {"normalized_category_name": "物理治療師",
                                                "normalized_subject_name": "骨科疾病物理治療學",
                                                "year": 115, "exam_ordinal": 2}},
            {"candidate_key": "b", "metadata": {"normalized_category_name": "藥師(一)",
                                                "normalized_subject_name": "藥學(一)",
                                                "year": 108, "exam_ordinal": 1}},
            # A second spelling of the same category, which the fold must treat as equal.
            {"candidate_key": "c", "metadata": {"normalized_category_name": "藥師（一）",
                                                "normalized_subject_name": "藥學(二)",
                                                "year": 108, "exam_ordinal": 1}},
            # No category at all - it must not appear in `categories` and must not crash.
            {"candidate_key": "d", "metadata": {"normalized_subject_name": "無類科",
                                                "year": 110, "exam_ordinal": 1}},
        ]
        return state

    @staticmethod
    def _reference(state, params):
        """`facets` as it was before the fold - the negative control's second engine."""
        values = {"categories": set(), "subjects": set(), "years": set(), "ordinals": set()}
        for item in state.candidates:
            metadata = item.get("metadata") or {}
            category = metadata.get("normalized_category_name") or metadata.get("group_name") or ""
            subject = metadata.get("normalized_subject_name") or ""
            year = str(metadata.get("year") or "")
            ordinal = str(metadata.get("exam_ordinal") or "")
            if category:
                values["categories"].add(category)
            if state._facet_match(category, subject, year, ordinal, params, ignore="subject") and subject:
                values["subjects"].add(subject)
            if state._facet_match(category, subject, year, ordinal, params, ignore="year") and year:
                values["years"].add(year)
            if state._facet_match(category, subject, year, ordinal, params, ignore="ordinal") and ordinal:
                values["ordinals"].add(ordinal)
        return {
            key: sorted(value, key=lambda item: (int(item) if item.isdigit() else 9999, item))
            if key in {"years", "ordinals"} else sorted(value)
            for key, value in values.items()
        }

    def test_the_folded_facets_equal_the_per_row_regex_facets(self) -> None:
        cases = [
            {},
            {"category": "物理治療師"},
            {"category": "藥師(一)"},
            # The full-width spelling must select the same rows as the half-width one.
            {"category": "藥師（一）"},
            {"category": "藥師"},
            {"category": "不存在"},
            {"year": "108", "ordinal": "1"},
            {"subject": "藥學(二)"},
            {"category": "藥師(一)", "subject": "藥學(二)"},
        ]
        for params in cases:
            with self.subTest(params=params):
                state = self._state()
                self.assertEqual(state.facets(params), self._reference(state, params))

    def test_a_category_with_no_name_is_in_no_category_facet(self) -> None:
        state = self._state()
        self.assertNotIn("", state.facets({})["categories"])

    def test_the_facets_are_asked_for_once_per_queue_not_once_per_request(self) -> None:
        state = self._state()
        state._facet_row_values()
        first = state._facet_rows
        state._facet_row_values()
        # The same list object, so the fold was not redone.
        self.assertIs(state._facet_rows, first)

    def test_a_filtered_request_only_scans_its_own_sitting(self) -> None:
        state = self._state()
        state._build_candidate_index()
        self.assertEqual(len(state._candidate_scan({"year": "108", "ordinal": "1"})), 2)
        # Without both levels there is no bucket that can be proven complete, so the whole queue is
        # scanned - the answer stays right, it is only slower.
        self.assertEqual(len(state._candidate_scan({"year": "108"})), 4)
        self.assertEqual(len(state._candidate_scan({})), 4)


class CompressedResponseTests(unittest.TestCase):
    """A 2.13 MB JSON response must not be sent as 2.13 MB when the browser can read gzip.

    Measured: the review queue's responses carried no `Content-Encoding` at all, and `v2.html`
    fetches a whole sitting per request and a whole category as 32 of them - 80 MB of text to open
    物理治療師. The same bytes gzip to about 5% of their size because the payload is mostly repeated
    field names and CJK text.
    """

    def _handler(self, accept_encoding: str):
        handler = review_ui.Handler.__new__(review_ui.Handler)
        handler.headers = {"Accept-Encoding": accept_encoding}
        return handler

    def test_json_is_gzipped_when_the_client_asked_for_it(self) -> None:
        handler = self._handler("gzip, deflate")
        body = review_ui.Handler.compressible(handler, b"x" * 5000, "application/json; charset=utf-8")
        self.assertLess(len(body), 5000)

    def test_json_is_sent_unchanged_when_the_client_did_not_ask(self) -> None:
        handler = self._handler("")
        data = b"x" * 5000
        self.assertIs(review_ui.Handler.compressible(handler, data, "application/json; charset=utf-8"),
                      data)

    def test_an_image_is_never_gzipped(self) -> None:
        # Images are already compressed; gzipping them costs CPU and can make them bigger.
        handler = self._handler("gzip")
        data = b"\x89PNG\r\n\x1a\n" + b"x" * 5000
        self.assertIs(review_ui.Handler.compressible(handler, data, "image/png"), data)

    def test_a_tiny_body_is_not_wrapped_in_a_gzip_header(self) -> None:
        handler = self._handler("gzip")
        data = b'{"ok": false}'
        self.assertIs(review_ui.Handler.compressible(handler, data, "application/json"), data)


class HttpKeepAliveTests(unittest.TestCase):
    """HTTP/1.0 closes the socket per response, which makes the parallel fetch pointless.

    Measured: every response came from a fresh connection (`curl` reported a new connection each
    time), so a sitting's crops, its PDF and the category's 32 sittings each paid a TCP handshake.
    HTTP/1.1 keeps the connection, and that is only safe because every response declares
    `Content-Length`.
    """

    def test_the_handler_negotiates_http_1_1(self) -> None:
        self.assertEqual(review_ui.Handler.protocol_version, "HTTP/1.1")
