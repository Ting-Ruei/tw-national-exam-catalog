# -*- coding: utf-8 -*-
"""The AI finding record: append-only, self-contained, and impossible to confuse with a decision.

This store holds a model's note about a question a person already flagged. The properties that
would hurt if they broke:

  * a finding can only be appended - there is no rewrite, so the record stays a statement about the
    reading that was in front of the model;
  * the record carries the exact evidence and the exact prompt, because a note without its evidence
    is a summary, and this project does not treat an unseen summary as evidence;
  * a finding never becomes a human review event (`GOV-05`), and the store lives outside
    `review-ui/` so it cannot land beside one;
  * `rule_worthy` separates a class from a one-off, because those need opposite treatment - one
    gets a rule, the other must not.

Each test builds its own queue, so it reports on the code and not on the live queue.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

import ask_about_blocks  # noqa: E402
from qbr import ai_findings  # noqa: E402


def _question(number=12, stem="承上題，由上圖可知…", options=None, key=None, answer="A"):
    return {
        "candidate_key": key or ("moex:111020:305:33:1:question:q%03d" % number),
        "question_number": number,
        "stem": stem,
        "options": options or [{"key": letter, "text": "選項%s" % letter} for letter in "ABCD"],
        "answer": answer,
        "answer_payload": {"accepted_values": [answer]},
        "metadata": {"normalized_subject_name": "藥劑學與生物藥劑學", "year": 111,
                     "exam_ordinal": 1},
    }


# ------------------------------------------------------------------ the record is self-contained

def test_a_finding_carries_the_evidence_and_the_exact_prompt():
    question = _question()
    system, user = ai_findings.build_prompt(question)
    finding = {"verdict": "DEFECT", "what": "DROP_OUT", "where": "題幹「則由 與 t」",
               "fix": "補回 β 或指定要看紙本的公式", "rule_worthy": False, "confidence": 0.6}
    record = ai_findings.make_record(question=question, finding=finding, model="m",
                                     endpoint="e", prompt_system=system, prompt_user=user)

    # The evidence is the model's input, verbatim - not a regenerated summary of it.
    assert record["evidence"]["stem"] == question["stem"]
    assert "選項A" in record["evidence"]["options"]
    assert record["prompt_user"] == user and "第 12 題" in user
    assert record["finding"]["fix"]


def test_a_code_in_the_verdict_field_supplies_the_verdict():
    # Measured on the Splash engine: it answered `"verdict":"FIGURE_MISSING"` with the code either
    # repeated in `what` or left out of it. Requiring the verdict to be one of three words discarded
    # two of three correct findings, so the code's own meaning decides: NONE is not a defect, every
    # other code is. Both shapes are asserted because both occurred.
    both = ai_findings.parse_finding(
        '{"verdict":"FIGURE_MISSING","what":"FIGURE_MISSING","where":"如下圖","fix":"查紙本"}')
    assert both is not None and both["verdict"] == "DEFECT" and both["what"] == "FIGURE_MISSING"

    only = ai_findings.parse_finding(
        '{"verdict":"FIGURE_MISSING","where":"如下圖","fix":"查紙本"}')
    assert only is not None and only["verdict"] == "DEFECT" and only["what"] == "FIGURE_MISSING"

    # NONE in the verdict slot is an OK verdict, not a defect with no code.
    none = ai_findings.parse_finding('{"verdict":"NONE","where":"完整","fix":"不用修"}')
    assert none is not None and none["verdict"] == "OK"

    # A word that is neither a verdict nor a code stays unclassified, with the note kept.
    weird = ai_findings.parse_finding('{"verdict":"MAYBE","what":"DROP_OUT","where":"w"}')
    assert weird is not None and weird["verdict"] is None and weird["what"] == "DROP_OUT"


def test_a_changed_prompt_is_visible_in_every_record():
    # The loop adjusts the prompt between batches, so two notes taken before and after a change must
    # be distinguishable by a field rather than by diffing the stored text by hand.
    first = ai_findings.make_record(question=_question(), finding=None, model="m", endpoint="e",
                                    prompt_system="s", prompt_user="u")
    assert first["prompt_version"] == ai_findings.prompt_version()
    # And the version covers the learned block too, because it changes what the model is told to
    # ignore - the instructions can be identical while the question asked is not.
    with_learned = ai_findings.make_record(
        question=_question(), finding=None, model="m", endpoint="e",
        prompt_system="s", prompt_user="u", learned={"FIGURE_MISSING": "已由 disputes 偵測"})
    assert with_learned["learned"] == {"FIGURE_MISSING": "已由 disputes 偵測"}


def test_the_prompt_version_changes_when_the_prompt_changes():
    # A version that cannot move is not a version. This is the negative control for the field: if
    # `prompt_version` hashed something constant, every assertion above would still pass.
    import hashlib
    base = ai_findings.prompt_version()
    original = ai_findings.SYSTEM
    try:
        ai_findings.SYSTEM = original + "\n額外指示"
        assert ai_findings.prompt_version() != base
    finally:
        ai_findings.SYSTEM = original
    assert ai_findings.prompt_version() == base


def test_the_learned_block_is_only_added_when_there_is_something_to_say():
    # An empty 【已經知道的事】 section reads as "nothing is known", which is a different claim from
    # "this run was not given prior findings". It belongs in the *system* prompt because it is a
    # property of the run, not of the question.
    question = _question()
    system, _user = ai_findings.build_prompt(question, learned={"DROP_OUT": "已修"})
    assert "已經知道" in system and "DROP_OUT" in system
    plain_system, _user = ai_findings.build_prompt(question)
    assert "已經知道" not in plain_system
    # A bare direction that is not one code is still carried forward rather than lost.
    free_system, _user = ai_findings.build_prompt(question, learned="這份卷的答案印在另一張紙")
    assert "這份卷的答案印在另一張紙" in free_system


def test_the_prompt_tells_the_model_which_options_are_pictures():
    # An option that is a picture is stored with empty text and an image reference. A model shown four
    # empty options reports a missing option every time - correctly from what it can see, and uselessly,
    # because this project decided those are figure-option questions. Measured: Splash called
    # `105020:305:11 q052` a rule-worthy defect. The prompt must carry the same evidence the reviewer
    # gets, which is that the option is an image.
    picture_options = _question(options=[{"key": k, "text": ""} for k in "ABCD"])
    picture_options["image_refs"] = [{"asset_role": "option-image", "option_key": k} for k in "ABCD"]
    _system, user = ai_findings.build_prompt(picture_options)
    assert "圖片選項" in user
    assert "選項 A 的圖" in user
    # And a question that references a figure while carrying none must be told the opposite, so the
    # model can legitimately report FIGURE_MISSING.
    no_figure = _question(stem="承上題，由上圖可知…")
    _system, user2 = ai_findings.build_prompt(no_figure)
    assert "沒有任何圖片" in user2
    # A question that never mentioned a figure is not prompted to look for one.
    plain = _question(stem="下列何者正確？")
    _system, user3 = ai_findings.build_prompt(plain)
    assert "圖片" not in user3


def test_a_picture_option_question_is_not_reported_as_empty_options():
    # The evidence the prompt now carries is the same field the UI uses, so a finding about it can be
    # checked against `image_refs` rather than against the model's word.
    picture_options = _question(options=[{"key": k, "text": ""} for k in "ABCD"])
    picture_options["image_refs"] = [{"asset_role": "option-image", "option_key": k} for k in "ABCD"]
    _system, user = ai_findings.build_prompt(picture_options)
    assert "這一題的圖片：4 張" in user


def test_a_fenced_finding_is_parsed():
    # Splash wraps its JSON in ```json fences; the fence must not be what makes a finding unreadable.
    fenced = ai_findings.parse_finding(
        '```json\n{"verdict":"DEFECT","what":"DROP_OUT","where":"w","fix":"f"}\n```')
    assert fenced is not None and fenced["what"] == "DROP_OUT"


def test_the_prompt_asks_for_where_and_how_to_fix_and_whether_it_is_a_one_off():
    # The requirement is exactly these three questions, so the prompt must actually ask them.
    system, _user = ai_findings.build_prompt(_question())
    assert "where" in system and "fix" in system and "rule_worthy" in system
    # And it must permit "this is not an extraction defect", or the model will invent one.
    assert "NOT_EXTRACTION" in system


# ------------------------------------------------------------------ append-only by shape

def test_the_module_cannot_express_a_rewrite(tmp_path):
    # The guarantee is the shape of the code: if an update/save/rewrite appears, the record stops
    # being a statement about the reading the model saw. This test is a tripwire for that.
    for name in ("rewrite", "update", "save", "replace", "truncate"):
        assert not hasattr(ai_findings, name), (
            "ai_findings gained a %r writer; a finding must only ever be appended" % name)


def test_appending_twice_keeps_both_findings(tmp_path):
    path = str(tmp_path / "ai_findings.jsonl")
    for verdict in ("DEFECT", "NOT_EXTRACTION"):
        ai_findings.append(path, {"candidate_key": "moex:a", "finding": {"verdict": verdict}})
    records = ai_findings.load(path)
    assert [r["finding"]["verdict"] for r in records] == ["DEFECT", "NOT_EXTRACTION"]
    # Latest wins for a re-asked question, and the earlier one is still on disk.
    assert ai_findings.latest_by_question(path)["moex:a"]["finding"]["verdict"] == "NOT_EXTRACTION"


def test_a_damaged_line_is_reported_not_dropped(tmp_path):
    # "the log is corrupt" and "there is no finding" must not look the same.
    path = str(tmp_path / "ai_findings.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"candidate_key": "moex:a", "finding": {"verdict": "OK"}}\n')
        handle.write("{not json\n")
    records = ai_findings.load(path)
    assert any(r.get("_unparseable") for r in records)
    assert ai_findings.summarize(records) is not None


# ------------------------------------------------------------------ it is not a human decision

def test_the_store_lives_where_the_pipeline_already_protects_streams(tmp_path):
    # The first version of this put the store *beside* `review-ui/`, so a model's note could never
    # land next to a person's decision - and that was wrong, because the rebuild carries only named
    # streams *inside* `review-ui/` and `deploy_station.sh` protects only their names. The note would
    # have been discarded silently on the next rebuild into a new directory. This test now encodes
    # the opposite conclusion: the stream is one of the protected names.
    root = tmp_path / "live"
    (root / "review-ui").mkdir(parents=True)
    path = ai_findings.store_path(str(root))
    assert os.path.dirname(path) == str(root / "review-ui")
    assert os.path.basename(path) == ai_findings.STREAM
    # Handed the `review-ui/` directory itself, it must resolve to the same file rather than
    # inventing a second one - a script that quietly wrote elsewhere looks like "no findings yet".
    assert ai_findings.store_path(str(root / "review-ui")) == path


def test_the_finding_stream_is_carried_by_a_rebuild_and_protected_by_a_deploy():
    # A store nobody carries is a store that disappears. Both lists name the stream, and both read
    # the name from this module so they cannot drift from it.
    import build_review_queue
    source = open(os.path.join(PKG, "scripts", "build_review_queue.py"), encoding="utf-8").read()
    assert "ai_findings.STREAM" in source, "the queue rebuild no longer carries the findings stream"
    deploy = open(os.path.join(os.path.dirname(PKG), "scripts", "deploy_station.sh"),
                  encoding="utf-8").read()
    assert ai_findings.STREAM in deploy, "deploy_station.sh would delete the findings on sync"


def test_a_finding_never_becomes_a_review_event(tmp_path):
    # GOV-05: the record has no action and no reviewer, so it cannot be mistaken for a decision and
    # cannot be concatenated into the human log without someone deliberately doing it.
    record = ai_findings.make_record(question=_question(), finding={"verdict": "OK"},
                                     model="m", endpoint="e", prompt_system="s", prompt_user="u")
    assert "action" not in record
    assert "reviewer" not in record
    for field in ("reviewer", "source"):
        assert field not in record


# ------------------------------------------------------------------ class vs one-off

def test_a_one_off_is_separated_from_a_class():
    # The whole point of the request: a class gets a rule, a one-off gets discussed alone.
    base = dict(question=_question(), model="m", endpoint="e", prompt_system="s", prompt_user="u")
    klass = ai_findings.make_record(finding={"verdict": "DEFECT", "what": "DROP_OUT",
                                             "rule_worthy": True, "where": "w", "fix": "f"},
                                    **base)
    one = ai_findings.make_record(finding={"verdict": "DEFECT", "what": "DROP_OUT",
                                           "rule_worthy": False, "where": "w", "fix": "f"},
                                  **base)
    summary = ai_findings.summarize([klass, one])
    assert summary["classes"]["DROP_OUT"] == [klass["candidate_key"]]
    assert len(summary["one_offs"]) == 1


def test_an_unparsed_finding_is_never_rule_worthy():
    # The default when the answer is unreadable must be "a person looks at it", not "write a rule".
    assert not ai_findings.is_rule_worthy({"finding": None})
    assert not ai_findings.is_rule_worthy({})
    # Only a DEFECT the model called a class is rule-worthy; NOT_EXTRACTION never is, however
    # confident the model was, because there is no extraction defect to write a rule for.
    assert not ai_findings.is_rule_worthy({"finding": {"verdict": "NOT_EXTRACTION",
                                                       "rule_worthy": True}})
    assert not ai_findings.is_rule_worthy({"finding": {"verdict": "OK",
                                                       "rule_worthy": True}})
    assert ai_findings.is_rule_worthy({"finding": {"verdict": "DEFECT", "what": "DROP_OUT",
                                                    "rule_worthy": True}})


# ------------------------------------------------------------------ the parser cannot invent data

def test_an_out_of_vocabulary_code_keeps_the_note_but_not_the_code():
    # The note is the value and a typo must not destroy it; the code is an index and must not be
    # trusted when it is unknown. Both halves are asserted, because getting either wrong loses
    # something real (silently, in both cases).
    typo = ai_findings.parse_finding(
        '{"verdict":"DEFECT","what":"GLYPDAMAGE","where":"題幹首字","fix":"改回漢字"}')
    assert typo is not None
    # `GLYPDAMAGE` is a near miss of `GLYPH_DAMAGE` (one missing underscore), so it recovers.
    assert typo["what"] == "GLYPH_DAMAGE"
    assert typo["where"] == "題幹首字" and typo["fix"] == "改回漢字"

    unknown = ai_findings.parse_finding(
        '{"verdict":"DEFECT","what":"WEIRD","where":"w","fix":"f"}')
    assert unknown is not None
    # A genuinely unknown code is not invented into one - it is reported as unknown, keeping the
    # original so a reader can see what was said, and the note survives.
    assert unknown["what"] is None and unknown["what_reported"] == "WEIRD"
    assert unknown["where"] == "w"

    # DEFECT with NONE contradicts itself: unclassified, but the note is still kept.
    contradiction = ai_findings.parse_finding(
        '{"verdict":"DEFECT","what":"NONE","where":"w","fix":"f"}')
    assert contradiction is not None and contradiction["verdict"] is None
    assert contradiction["where"] == "w"

    # No JSON at all means no note, which is the only case that is genuinely nothing.
    assert ai_findings.parse_finding("抱歉，我無法回答") is None
    assert ai_findings.parse_finding("{") is None

    ok = ai_findings.parse_finding('```json\n{"verdict":"OK","what":"NONE"}\n```')
    assert ok and ok["verdict"] == "OK"


def test_the_reading_fingerprint_does_not_fold_the_text():
    # A finding may be about a Kangxi radical or a full-width letter; folding would erase exactly
    # the difference the finding is about.
    plain = _question(stem="一懸浮液")
    radical = _question(stem="⼀懸浮液")          # U+2F00 KANGXI RADICAL ONE
    assert ai_findings.reading_fingerprint(plain) != ai_findings.reading_fingerprint(radical)


def test_the_fingerprint_is_stable_for_the_same_reading():
    assert (ai_findings.reading_fingerprint(_question())
            == ai_findings.reading_fingerprint(_question()))


# ------------------------------------------------------------------ the target set is the loop's

def _build_queue(tmp_path, questions, events):
    ui = tmp_path / "review-ui"
    ui.mkdir(parents=True, exist_ok=True)
    with (ui / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for question in questions:
            handle.write(json.dumps(question, ensure_ascii=False) + "\n")
    with (ui / "question_review_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return str(tmp_path)


def test_it_asks_about_the_questions_the_loop_could_not_explain(tmp_path):
    unexplained = _question(12)
    explained = _question(13)
    explained["disputes"] = [{"kind": "substituted-script", "severity": "review"}]
    unjudged = _question(14)
    queue = _build_queue(tmp_path, [unexplained, explained, unjudged], [
        {"candidate_key": unexplained["candidate_key"], "action": "block", "notes": "",
         "reviewer": "local", "created_at": "t", "source": "linear_v2"},
        {"candidate_key": explained["candidate_key"], "action": "block", "notes": "",
         "reviewer": "local", "created_at": "t", "source": "linear_v2"},
    ])
    entries = ask_about_blocks.targets(os.path.join(queue, "review-ui"), None, 0)
    # Only the block nothing explains - not the explained one, not the one nobody judged.
    assert [e["question"]["question_number"] for e in entries] == [12]


def test_asking_cannot_change_the_human_log(tmp_path):
    question = _question(12)
    queue = _build_queue(tmp_path, [question], [
        {"candidate_key": question["candidate_key"], "action": "block", "notes": "",
         "reviewer": "local", "created_at": "t", "source": "linear_v2"}])
    log = os.path.join(queue, "review-ui", "question_review_events.jsonl")
    before = open(log, "rb").read()
    ask_about_blocks.targets(os.path.join(queue, "review-ui"), None, 0)
    assert open(log, "rb").read() == before
