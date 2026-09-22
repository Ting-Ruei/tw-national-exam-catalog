# -*- coding: utf-8 -*-
"""The candidate export: what the Review UI reads, and what it must never read.

`serve_question_review_ui.py` keys every human decision on `candidate_key`, joins candidates
against a review log, and writes append-only events. If this export drifts from the shape it
reads, the failure is silent until a reviewer's decision is filed against nothing.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import review_queue  # noqa: E402

RUN = "/tmp/qbr-golden-001"
CANDIDATES = os.path.join(RUN, "review-ui", "candidates.jsonl")
requires_run = pytest.mark.skipif(not os.path.isfile(CANDIDATES),
                                  reason="no golden-path run with a candidate export")

# The fields the server reads unconditionally, so an absent one is a crash rather than a
# missing datum.
REQUIRED_ROWS = ("candidate_key", "stem", "options", "answer", "metadata", "quality_status")


def _questions():
    with open(CANDIDATES, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --------------------------------------------------------------------- unit properties

def test_quality_status_blocks_the_numbers_the_gate_names():
    gate = {"blocking": ["numbering-gaps:2"], "numbering_gaps": [7],
            "empty_stem": [8], "options_not_four": [{"number": 9, "options": 3}],
            "duplicate_option_labels": [10], "answer_not_on_sheet": [11]}
    assert review_queue._quality_status(gate, 7) == "blocked"
    assert review_queue._quality_status(gate, 8) == "blocked"
    assert review_queue._quality_status(gate, 9) == "blocked"
    assert review_queue._quality_status(gate, 10) == "blocked"
    assert review_queue._quality_status(gate, 11) == "needs_review"
    assert review_queue._quality_status(gate, 12) == "pass"


def test_a_paper_wide_failure_marks_every_question_for_review_not_block():
    """A count mismatch is not one question's fault; saying `blocked` would blame it."""
    gate = {"blocking": ["count-mismatch:items=79 anchors=80"]}
    assert review_queue._quality_status(gate, 1) == "needs_review"


def test_answer_string_is_what_the_reader_displays():
    assert review_queue._answer_string(["A"]) == "A"
    assert review_queue._answer_string(["A", "C"]) == "A,C"
    assert review_queue._answer_string([]) == ""


def test_write_candidates_refuses_duplicate_keys(tmp_path):
    row = {"source_question_key": "dupe", "source_registry_key": "r", "question_number": 1,
           "normalized_category_name": "c", "normalized_subject_name": "s",
           "official_category_name": "c", "official_subject_name": "s",
           "metadata": {}, "options": [], "answer": [], "stem": "x", "group_ref": None,
           "stem_image": None, "question_type": "single_choice"}
    with pytest.raises(ValueError):
        review_queue.write_candidates(str(tmp_path / "c.jsonl"), [row, dict(row)], gate={})


def test_issues_csv_is_written_with_its_header_even_when_empty(tmp_path):
    path = str(tmp_path / "issues.csv")
    review_queue.write_issues(path, [], gate={})
    content = open(path, encoding="utf-8").read()
    assert content.startswith("candidate_key,")
    assert len(content.strip().splitlines()) == 1, "header only: no parser issues to report"


# --------------------------------------------------------------------- measured shape

@requires_run
def test_every_candidate_carries_every_field_the_server_reads():
    for row in _questions():
        for field in REQUIRED_ROWS:
            assert row.get(field) not in (None, "", []), (row.get("candidate_key"), field)


@requires_run
def test_candidate_keys_are_unique_and_match_the_package():
    rows = _questions()
    keys = [row["candidate_key"] for row in rows]
    assert len(set(keys)) == len(keys), "the Review UI keys on candidate_key"
    package = os.path.join(RUN, "package", "questions.jsonl")
    with open(package, encoding="utf-8") as handle:
        packaged = {json.loads(line)["source_question_key"] for line in handle if line.strip()}
    assert set(keys) == packaged, "the queue must name exactly the questions that were built"


@requires_run
def test_options_carry_the_order_the_reader_renders():
    for row in _questions():
        orders = [option.get("raw_order") for option in row["options"]]
        assert orders == list(range(1, len(orders) + 1)), row["candidate_key"]
        assert [option["key"] for option in row["options"]] == \
            sorted(option["key"] for option in row["options"])


@requires_run
def test_the_answer_field_is_display_text_and_the_payload_keeps_the_list():
    for row in _questions():
        assert isinstance(row["answer"], str), "the reader renders this directly"
        assert isinstance(row["answer_payload"]["accepted_values"], list)


@requires_run
def test_lineage_survives_the_export():
    """A reviewer must be able to walk from a decision to the paper behind it."""
    for row in _questions():
        metadata = row["metadata"]
        assert metadata.get("question_pdf_relative"), row["candidate_key"]
        assert metadata.get("answer_pdf_relative"), row["candidate_key"]
        assert metadata.get("answer_authority_source") in ("answer", "corrected",
                                                          "answer+corrected")
        assert metadata.get("review_status") == "machine_verified_pending_human"


@requires_run
def test_no_absolute_path_reaches_the_queue():
    for row in _questions():
        for key, value in row["metadata"].items():
            if key.endswith("_relative") and value:
                assert not str(value).startswith("/"), (key, value)


def test_a_merged_crop_reference_does_not_depend_on_the_calling_directory(tmp_path):
    """A merged queue's crop path must resolve against the queue, not against the CWD it was built in.

    Measured, and the reason this test exists: 3,483 questions holding 4,549 crops were written with
    a `path` of `data/review-queues/<q>/review-ui/crops/...` - the value of `--out` exactly as the
    operator spelled it. Served from a container, where the queue is mounted at `/queue` and the CWD
    is `/workspace`, `/file` answered **404 for every figure**, so the reviewer saw no picture at all.
    The bug was invisible while no merged queue had pictures, which is how it survived.

    This is a **negative control**: it fails on the version that stored `--out` as given
    (`git stash` of `qbr/scripts/build_review_queue.py`), and the assertion is on the stored string
    rather than on a copied file's existence, so it cannot pass for the wrong reason.
    """
    builder = _builder()
    run = tmp_path / "run"
    review_ui = run / "review-ui"
    crops = review_ui / "crops"
    crops.mkdir(parents=True)
    (crops / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    row = {"candidate_key": "k1", "question_number": 1,
           "image_refs": [{"path": str(crops / "figure.png"), "raw_ref": "figure.png",
                           "asset_role": "figure-crop"}]}
    with open(review_ui / "candidates.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    # The caller is one directory up, and `--out` is given **relative** - both halves of the trap.
    import subprocess
    queue = tmp_path / "queue"
    subprocess.run([sys.executable,
                    os.path.join(PKG, "scripts", "build_review_queue.py"),
                    "--work", str(tmp_path), "--out", "queue"],
                   cwd=str(tmp_path), check=True, capture_output=True)

    with open(queue / "review-ui" / "candidates.jsonl", encoding="utf-8") as handle:
        written = json.loads(handle.readline())
    stored = written["image_refs"][0]["path"]
    assert stored == "review-ui/crops/run/figure.png", stored
    assert not os.path.isabs(stored)
    # And the claim that makes the reference true: the file really is inside the queue, so a copy
    # of the queue still serves it.
    assert (queue / stored).is_file()
    # Negative control on the fix itself: the queue is relocatable, which is the whole point.
    moved = tmp_path / "moved"
    moved.mkdir()
    import shutil as _shutil
    _shutil.copytree(queue / "review-ui", moved / "review-ui")
    assert (moved / stored).is_file()


# ------------------------------------------------------- the reviewer's records survive a rebuild
# These import the builder by path rather than by `import`, because the module is a script that
# parses argv in `main()` and has no package home. The point of the tests is narrow and worth
# stating: a rebuild of the queue must not lose, or duplicate, a human being's decisions. Both
# failures happened in this project, so both are asserted.

def _builder():
    import importlib.util
    path = os.path.join(PKG, "scripts", "build_review_queue.py")
    spec = importlib.util.spec_from_file_location("build_review_queue", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_review_queue"] = module
    spec.loader.exec_module(module)
    return module


def _write_log(run, name, records):
    directory = os.path.join(str(run), "review-ui")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _event(key, action="accept", at="2026-09-19T10:00:00"):
    return {"candidate_key": key, "action": action, "created_at": at, "notes": "",
            "reviewer": "local", "source": "test"}


def test_carrying_a_record_twice_does_not_duplicate_it(tmp_path):
    """重建兩次，紀錄數不得成長。

    這是實際發生過的缺陷：`_carried_from` 被寫進紀錄裡，而它又是去重鍵的一部分，所以每一次
    重建都把同一筆紀錄當成新的。量測到的數字是第一次 392 筆、再建一次 629 筆，同一批 181 題
    全部出現兩次；再建下去會是三次，審核歷史會看起來像同一題被反覆判定。
    """
    builder = _builder()
    source = tmp_path / "src"
    _write_log(source, "question_review_events.jsonl", [_event("k1"), _event("k2")])
    first = tmp_path / "first"
    # The first pass is the one that carries them in. (`merge` writes the stream even when it is
    # empty, so the assertion is on the count rather than on the file's existence - an earlier
    # version of this test asserted the file was absent and failed on correct behaviour.)
    builder.merge([], str(first), previous=[str(source)])
    in_first = os.path.join(str(first), "review-ui", "question_review_events.jsonl")
    assert sum(1 for line in open(in_first, encoding="utf-8") if line.strip()) == 2
    once = builder.review_events_to_carry(str(first), [str(first), str(source)])
    assert len(once) == 2, "carrying from a queue that already holds them must not double them"
    # A third pass over the same two sources is the case that grew: assert it is still two.
    again = builder.review_events_to_carry(str(first), [str(first), str(source), str(first)])
    assert len(again) == 2, "repeated rebuilds must not accumulate"


def test_a_record_that_changed_its_mind_is_kept_in_full(tmp_path):
    """同一題的多次事件都要保留，不能只留一筆。

    去重是「整筆紀錄」而不是「candidate_key」：同一題可以合法地有 `block` 之後 `accept`
    （審核者改變了決定），而那個改變正是最需要留下的資訊。用題號去重會靜默丟掉後面的決定，
    是比重複更安靜的錯。
    """
    builder = _builder()
    source = tmp_path / "src"
    _write_log(source, "question_review_events.jsonl", [
        _event("k1", "block", "2026-09-15T21:38:18"),
        _event("k1", "accept", "2026-09-15T21:38:41"),
    ])
    records = builder.review_events_to_carry(str(tmp_path / "out"), [str(source)])
    assert len(records) == 2, "兩次決定是兩筆歷史，不是一筆"
    assert {r["action"] for _name, r in records} == {"block", "accept"}


def test_carrying_is_automatic_so_a_forgotten_flag_cannot_lose_records(tmp_path):
    """不必加旗標就會帶入，所以「忘記加」不會弄丟紀錄。

    原本的設計只有在「原地重建」時預設安全；重建到新目錄（sf7→sf8→sf9，也就是本專案實際
    的做法）預設什麼都不帶，等於用正常操作就能踩到。現在 sibling 佇列的紀錄一律自動帶入。
    """
    builder = _builder()
    sibling = tmp_path / "qbr-live-a"
    _write_log(sibling, "question_review_events.jsonl", [_event("k1"), _event("k2")])
    destination = tmp_path / "qbr-live-b"
    discovered = builder.sibling_queues_with_reviews(str(destination))
    assert str(sibling) in discovered, "sibling 的紀錄應該被自動找到"
    assert str(destination) not in discovered or os.path.isdir(
        os.path.join(str(destination), "review-ui"))


def test_the_destination_is_not_counted_as_its_own_source(tmp_path):
    """目的地自己不算來源，否則「已帶入」的判定會被自己滿足。"""
    builder = _builder()
    run = tmp_path / "qbr-live-x"
    _write_log(run, "question_review_events.jsonl", [_event("k1")])
    discovered = builder.sibling_queues_with_reviews(str(run))
    assert str(run) in discovered, "原地重建時目的地本身就是來源"
    assert discovered.count(str(run)) == 1, "不得重複列出"


def test_an_orphaned_record_is_kept_and_counted_never_dropped(tmp_path):
    """題目已不存在的紀錄要保留並計數，不得丟棄。

    丟掉它是「同一種靜默損失的小尺寸版本」：審核者花了時間讀那一題，那個事實不會因為題目
    被重新切分而消失。
    """
    builder = _builder()
    source = tmp_path / "src"
    _write_log(source, "question_review_events.jsonl", [_event("gone"), _event("k1")])
    records = builder.review_events_to_carry(str(tmp_path / "out"), [str(source)])
    assert len(records) == 2, "孤兒紀錄也要帶"
    carried = [r for _name, r in records if r["candidate_key"] == "k1"]
    orphaned = [r for _name, r in records if r["candidate_key"] == "gone"]
    assert len(carried) == 1 and len(orphaned) == 1


def test_the_carry_names_its_source_queues(tmp_path):
    """重建要說出紀錄是從哪個佇列帶來的。

    自動發現會掃 `--out` 的每一個兄弟，所以把輸出放在 `/tmp` 這種滿是測試殘留的地方，
    一個瀏覽器測試佇列（如 `/tmp/ann_test`）會被當成真人的決定帶進新佇列——這是實際發生過的，
    3 筆 `115090:311:0704` 的假事件混進了重建。進到佇列之後就分不出來了。

    負控制：把 `_carried_from` 拿掉，`origins` 就只剩 `"?"`，這個測試就會失效——
    也就是說它真的在驗「來源被列出來」這件事，不是在驗行數。
    """
    builder = _builder()
    real = tmp_path / "qbr-live-v6"
    _write_log(real, "question_review_events.jsonl", [_event("k1")])
    records = builder.review_events_to_carry(str(tmp_path / "qbr-live-v7"), [str(real)])
    assert len(records) == 1
    _name, record = records[0]
    assert record.get("_carried_from") == str(real), \
        "來源必須跟著紀錄走，否則沒人能說出它從哪來"
