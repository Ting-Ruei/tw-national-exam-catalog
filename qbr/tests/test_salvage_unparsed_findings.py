# -*- coding: utf-8 -*-
"""Reading back a finding the parser threw away - and not inventing one it never had.

The property that would hurt if it broke: this script decides *by itself* what to write into the
findings stream, so a wrong rule here does not fail loudly - it appends a confident-looking record.
Two ways that could happen, one test each: treating a call that never returned as a recoverable
answer (there is no `raw` to read), and appending the same recovery twice (which would look like two
independent findings about one question).

Each test builds its own queue, so it reports on the code and not on the live stream.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import salvage_unparsed_findings as salvage  # noqa: E402
from qbr import ai_findings  # noqa: E402

#: The measured shape: a complete object up to `where`, then a repetition loop that never closes it.
DEGENERATE = (
    '{"verdict":"DEFECT","what":"GLYPH_DAMAGE","where":"題幹：「抗癲癇藥物」中的「癲癇」二字'
    '正確詞彙為「抗癲癇」->「抗癲癇」? 錯誤。正確詞彙為「抗癲癇」->「抗癲癇」? 錯誤。'
    '正確詞彙為「抗癲癇」->「抗癲癇」? 錯誤。正確詞彙為「抗癲癇」->「抗癲癇」?')


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _recover(rows):
    """Run the script's selection over `rows` and return what it would append."""
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, ai_findings.STREAM)
        _write(path, rows)
        return salvage.recoverable(ai_findings.latest_by_question(path))


def test_a_call_that_never_returned_is_not_recovered():
    # `error: request failed` records carry no `raw`, and there is nothing in them to read. Treating
    # one as recoverable would write an answer the model never gave.
    recoverable = _recover([
        {"candidate_key": "no-raw", "raw": "", "finding": None, "error": "request failed"},
        {"candidate_key": "chatty", "raw": "抱歉，我無法回答這個問題。", "finding": None,
         "error": "unparsed"},
        {"candidate_key": "degenerate", "raw": DEGENERATE, "finding": None, "error": "unparsed"},
    ])
    assert [record["candidate_key"] for record, _ in recoverable] == ["degenerate"]
    finding = recoverable[0][1]
    assert finding["verdict"] == "DEFECT" and finding["what"] == "GLYPH_DAMAGE"
    # `where` never closed, so it is dropped rather than guessed at from the loop text - and the
    # record says the generation collapsed, because a reader has to be able to tell.
    assert "where" not in finding and finding["truncated"] is True
    # A collapsed generation may not become a rule, whatever it claimed about itself.
    assert ai_findings.is_rule_worthy({"finding": finding}) is False


def test_the_recovery_is_written_once_and_then_the_question_is_answered():
    # A rerun must add nothing: `is_answer` sees the appended record, so the question stops being
    # selected. Without that, every run would append another copy and the stream would show the same
    # question answered several times.
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, ai_findings.STREAM)
        original = {"candidate_key": "k1", "question_number": 7, "raw": DEGENERATE, "finding": None,
                    "error": "unparsed", "prompt_version": "v1", "prompt_system": "S",
                    "principles": ["p1"]}
        _write(path, [original])

        first = salvage.recoverable(ai_findings.latest_by_question(path))
        assert len(first) == 1
        ai_findings.append(path, salvage.recovered_record(*first[0]))

        assert salvage.recoverable(ai_findings.latest_by_question(path)) == []
        rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        assert len(rows) == 2
        # The appended record is the same call: the raw reply, the prompt, the evidence and the
        # constraints are copied, so the note is still checkable against what the model was shown.
        assert rows[1]["raw"] == rows[0]["raw"] == DEGENERATE
        assert rows[1]["prompt_version"] == rows[0]["prompt_version"]
        assert rows[1]["prompt_system"] == rows[0]["prompt_system"]
        assert rows[1]["principles"] == rows[0]["principles"]
        assert rows[1]["question_number"] == 7
        # The failure is cleared because this record *does* carry a finding; saying `unparsed` beside
        # a finding would be a record that contradicts itself.
        assert rows[1]["error"] is None
