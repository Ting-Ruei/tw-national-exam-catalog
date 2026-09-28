#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The A2 scorer's own tests, run with plain `python3` (no pytest, no model, no network).

The comparison harness decides which model is better, so it is the last place where an unchecked
scorer is acceptable. Two things are asserted, and the second is the one that matters:

1. the verdicts name the right failure (`missed` vs `wrong` vs `over`), and
2. the **negative control is zero** - a model that hands the input back unchanged must score exactly
   `0.0` accuracy and `0` over-edits. If that control is ever non-zero the scoring is crediting the
   model for agreeing with text it was shown, and every model's number is inflated by the same
   amount.

Run:
    python3 test_scoring.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import compare_models as compare  # noqa: E402

FIELDS = ["stem", "answer", "option A"]


def _row(ship_stem, hum_stem, *, shipped_answer="A", human_answer="A",
         shipped_a="甲", human_a="甲", touched=("stem",)):
    return {"candidate_key": "moex:test:1:question:q001",
            "fields_touched": list(touched),
            "shipped": {"stem": ship_stem, "answer": shipped_answer,
                        "options": [{"key": "A", "text": shipped_a}]},
            "human": {"stem": hum_stem, "answer": human_answer,
                      "options": [{"key": "A", "text": human_a}]}}


def _reply(stem, *, answer="A", option_a="甲"):
    return {"stem": stem, "answer": answer, "options": {"A": option_a}}


def test_a_correct_repair_is_exact():
    row = _row("下ৡ有關", "下列有關")
    assert compare.score(row, _reply("下列有關"), FIELDS)["stem"] == "exact"


def test_the_control_a_model_that_changes_nothing_misses_the_field():
    # "missed" is not a failure of the model's reading - it did nothing, and doing nothing is not a
    # repair. The distinction matters because it is the one a do-nothing model is most likely to
    # produce, and it must not be scored as agreement.
    row = _row("下ৡ有關", "下列有關")
    assert compare.score(row, _reply("下ৡ有關"), FIELDS)["stem"] == "missed"


def test_a_markup_only_difference_is_near_not_wrong():
    # The model added markup to the right text but picked the wrong kind: the visible characters are
    # the person's, the meaning is not. That is not "different text" and not "the same text".
    row = _row("Cax 增高", "C<sub>ax</sub> 增高")
    verdicts = compare.score(row, _reply("C<sup>ax</sup> 增高"), FIELDS)
    assert verdicts["stem"] == "near"


def test_the_negative_control_a_genuinely_different_repair_is_wrong_and_not_near():
    # Negative control for `near`: a model that rewrites the sentence must not be given the same
    # credit as one that only got the markup kind wrong. Without this, `near` could be widened until
    # it swallowed every mismatch.
    row = _row("Cax 增高", "C<sub>ax</sub> 增高")
    assert compare.score(row, _reply("Cax 降低"), FIELDS)["stem"] == "wrong"


def test_an_edit_the_person_did_not_make_is_over_not_same():
    # The over-edit column exists so that `field_accuracy` cannot be gamed by rewriting everything.
    # The fixture is consistent: the person changed option A (so it is the touched field the model
    # must reproduce), and left the stem alone (the field the model must not touch).
    row = _row("下列何者錯誤？", "下列何者錯誤？", shipped_a="甲", human_a="乙",
               touched=("option A",))
    verdicts = compare.score(row, _reply("下列何者為是？"), FIELDS)
    assert verdicts["stem"] == "over"
    assert verdicts["option A"] == "missed"


def test_the_summary_counts_accuracy_over_touched_fields_only():
    rows = [
        {"candidate_key": "a", "fields_touched": ["stem"], "verdicts": {"stem": "exact"}},
        {"candidate_key": "b", "fields_touched": ["stem"], "verdicts": {"stem": "wrong"}},
        {"candidate_key": "c", "fields_touched": [], "verdicts": {"stem": "same"}},
    ]
    summary = compare.summarize(rows)
    assert summary["field_accuracy"] == 0.5
    assert summary["touched_fields"] == 2
    assert summary["over_edit_count"] == 0


def test_the_negative_control_the_do_nothing_model_scores_exactly_zero():
    # This is the assertion the whole harness is judged by. It is written against the real dataset
    # when one exists, and against hand-built rows otherwise, so it runs in a fresh clone.
    dataset = os.path.join(HERE, "dataset.jsonl")
    if os.path.isfile(dataset):
        with open(dataset, encoding="utf-8") as handle:
            source = [json.loads(line) for line in handle if line.strip()]
        rows = [{"candidate_key": row["candidate_key"], "fields_touched": row["fields_touched"],
                 "verdicts": compare.score(row, json.loads(compare.control_reply(row)), FIELDS)}
                for row in source]
        assert rows, "dataset.jsonl exists but has no rows"
    else:
        rows = [{"candidate_key": "a", "fields_touched": ["stem"],
                 "verdicts": compare.score(_row("下ৡ有關", "下列有關"),
                                           _reply("下ৡ有關"), FIELDS)}]
    summary = compare.summarize(rows)
    assert summary["field_accuracy"] == 0.0, summary
    assert summary["over_edit_count"] == 0, summary


def test_parse_answer_finds_the_json_through_a_fence():
    # A model that wraps its JSON in a code fence still said the thing; failing it for the fence
    # would be scoring formatting rather than judgement.
    assert compare.parse_answer('```json\n{"stem":"x"}\n```') == {"stem": "x"}
    assert compare.parse_answer("no json here") is None


def test_text_sufficiency_a_markup_change_needs_no_source():
    import probe_text_sufficiency as probe
    # `Cax` and `C<sub>ax</sub>` are the same characters; the string says which. Reachable.
    assert probe.classify("Cax 增高", "C<sub>ax</sub> 增高") == "markup_only"
    assert probe.classify("Cax 增高", "C<sub>ax</sub> 增高") not in probe.NEEDS_SOURCE


def test_text_sufficiency_a_dropped_character_needs_the_source():
    import probe_text_sufficiency as probe
    # The person wrote `酶` where the text has nothing. No reading of the input recovers it: this is
    # the class that caps a text-only comparison, and it must not be quietly counted as reachable.
    assert probe.classify("下列何種於適當", "下列何種酶於適當") == "gained_chars"
    assert probe.classify("下列何種於適當", "下列何種酶於適當") in probe.NEEDS_SOURCE


def test_text_sufficiency_a_garbled_glyph_needs_the_source():
    import probe_text_sufficiency as probe
    # Negative control for NFKC: the OCR read `ax` where the paper printed `max`. NFKC folds
    # spelling variants and must **not** turn this into a foldable change, or the probe would
    # report the corpus is easier than it is.
    kind = probe.classify("C∞<sub>ax</sub>增高", "C<sup>∞</sup><sub>max</sub> 增高")
    assert kind in probe.NEEDS_SOURCE, kind


def test_text_sufficiency_nfkc_still_folds_true_variants():
    import probe_text_sufficiency as probe
    # The counterweight to the test above: a genuine compatibility variant (full-width digits) is
    # reachable from the text, and widening `needs_source` until it swallowed these would make the
    # report's headline number meaningless in the other direction.
    assert probe.classify("清除率 130", "清除率 １３０") == "foldable"
    assert probe.classify("清除率 130", "清除率 １３０") not in probe.NEEDS_SOURCE


def main() -> int:
    tests = [(name, value) for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = []
    for name, test in tests:
        try:
            test()
            print("ok   %s" % name)
        except AssertionError as exc:
            failures.append(name)
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:                                   # noqa: BLE001 - report, do not hide
            failures.append(name)
            print("ERROR %s: %r" % (name, exc))
    print("\n%d passed, %d failed" % (len(tests) - len(failures), len(failures)))
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Prompt alignment: the ablation's prompts must be *exactly* the production ones, or the table it
# produces lies. These exist because a missing 23-character trailing cue was once misread as 9.4%
# of model nondeterminism. An ablation is only an ablation if two variants differ in exactly one
# thing, and "exactly" has to be checked by machine, not by eye and not after the fact.
# ---------------------------------------------------------------------------


def test_full_variant_is_the_text_prompt():
    import ablate_prompt as ablation
    import compare_models as compare
    assert ablation.build_system(ablation.variant_spec("full")) == compare.SYSTEM


def test_image_variant_is_the_proven_vision_prompt():
    import ablate_prompt as ablation
    import compare_models_vision as vision
    assert ablation.build_system(ablation.variant_spec("image")) == vision.VISION_SYSTEM


def test_image_cue_user_message_reproduces_the_production_script():
    """The negative control for the whole ablation: the cue must be the *only* difference.

    `compare_models_vision.ask_model_vision` builds its user message inline and cannot be imported,
    so the sentence is reproduced here from that function. If the two drift apart this test fails,
    and the ablation table stops being comparable to the earlier vision runs.
    """
    import ablate_prompt as ablation
    row = {"subject": "S", "question_number": "7",
           "shipped": {"stem": "T", "options": [{"key": "A", "text": "a"}], "answer": "A"}}
    path = "/tmp/p0001.png"
    expected = ("科目：S\n題號：7\n\n題幹：\nT\n\n選項：\nA. a\n\n答案：A\n\n"
                "（附圖：這題在官方 PDF 的第 %s 頁。）" % path)
    got = ablation.user_message(row, describe_images=False, page_cue=True, image_paths=[path])
    assert got == expected, got


def test_cue_is_the_only_difference_between_image_and_image_cue():
    import ablate_prompt as ablation
    assert (ablation.build_system(ablation.variant_spec("image"))
            == ablation.build_system(ablation.variant_spec("image_cue")))
    row = {"subject": "S", "question_number": "7",
           "shipped": {"stem": "T", "options": [{"key": "A", "text": "a"}], "answer": "A"}}
    without = ablation.user_message(row, describe_images=False, page_cue=False)
    with_cue = ablation.user_message(row, describe_images=False, page_cue=True, image_paths=["p"])
    assert without != with_cue
    assert with_cue.startswith(without), with_cue


def test_unknown_variant_is_refused():
    """A typo must not silently run the full prompt while claiming to be an ablation."""
    import ablate_prompt as ablation
    for bad in ("drop:nonexistent", "imgae", "ful"):
        try:
            ablation.variant_spec(bad)
        except ValueError:
            continue
        raise AssertionError("variant %r was accepted" % bad)


def test_each_drop_variant_removes_exactly_one_rule():
    import ablate_prompt as ablation
    full = ablation.build_system(ablation.variant_spec("full"))
    for key, line in ablation.RULES:
        dropped = ablation.build_system(ablation.variant_spec("drop:" + key))
        assert line not in dropped, "rule %r still present" % key
        assert len(full) - len(dropped) == len(line) + 1, "drop:%s changed more" % key


if __name__ == "__main__":
    raise SystemExit(main())
