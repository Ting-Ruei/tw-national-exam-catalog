# -*- coding: utf-8 -*-
"""Apply the repairs that are **already decided by measurement** - no model opinion involved.

The gap this fills
------------------

The loop had three halves that did not meet:

    a `dispute` names the exact place and the exact character (`h-1` at stem[88], `⻑` -> `長`)
    a `confirm_dispute` finding carries the same thing when the page agrees (`changes`)
    the review UI writes a `correct` event, which *overlays* `stem`/`options` for the reader; and
    this step writes a `reset_review` that carries the same correction (see below for why a reset
    and not a `correct`)

and nothing joined them, so 304 blocked questions sat with prose findings and **215 of them had no
text change at all** (measured: 89 changed, 215 untouched). A finding that says "把 X 改成 Y" is a
description; this is the change.
What is applied, and what is not
--------------------------------

Three paths in, in this order, and none of them asks a model what to do:

1. **the dispute carries the target character** - deterministic, allowed on any question:

       substituted-ideograph  `⻑` -> `長`                     (the dispute carries `means`)

2. **a page reading that is a pure character-level repair** at positions a detector already flagged
   (`anchored_page_changes`); also allowed on any question;

3. **a page reading that replaces the whole field** (`rule=page-read-field`, `applied="field"`).
   This is the gate that used to be shut, and the measurement is what shut it: 2026-09-24, the mirror
   carried **204** readings with `changes` while `--page-read` applied **0** of them, because path 2
   demands a detector's own flagged position and a detector cannot flag a character it does not know
   about (`⻑` `⻄` are in its table, the Kangxi radicals beside them are not).

   Path 3 is fenced on the question, on the second engine, and on the measurement - and the anchor is
   **never a detector**:

   * on the **question**: a person must have rejected it - a standing `block`/`needs_review` with no
     later `accept`/`unblock` (see `standing_rejections`). The owner's decision (2026-09-24) is what
     the fence implements: 「判讀 → 文字 跟 文字 → 抽取檔要打通，並且改標籤送到『AI已解決』，我才能知道
     有沒有改過」, with the person reviewing that bucket and bouncing a wrong one back with a `block`
     plus a 註解.
   * on the **second engine**: `orchestration.verdict` must be `TRUST` (see `_verdict_complaint`).
     `CARE`/`DOUBT`/absent all mean a person should look, and a whole-field rewrite is the most
     invasive thing this loop does; measured on the station 2026-09-24, of the 146 field candidates
     the other fences let through, 81 are `TRUST`, 44 `CARE`, 17 with no second read and 4 `DOUBT` -
     so this fence halves the whole-field path. (The same census earlier that evening, on a smaller
     queue, read 117 = 59/37/17/4: the loop is still writing readings while we measure.)
   * on the **measurement**: the reading must look like a reading of *this* field - alignment ratio at
     least `PAGE_READ_ALIGN_MIN` and length within `PAGE_READ_LENGTH_FACTOR` either way. Both numbers
     are measured, not chosen; the table and the case counts are in the constants below.

   Two per-field clauses come after those, both of them "the replacement is worse than what is there":

   * a field whose stored text carries HTML markup (`<sub>`/`<sup>`, the platform's own spelling of a
     subscript) is refused - the reading renders the same maths as real subscript characters
     (`Cₚ`, `4e⁻⁵ᵗ`), so replacing the whole field would strip the markup. That hole survived both
     measurements (`104090 q052`: the shared suffix is a whole sentence, so the length line never
     engaged) and a dress rehearsal caught it.
   * a reading that brings in the **unreadable mark** (`▢`, the character `reread.SYSTEM` tells the
     model to write when it cannot read a glyph) is refused when the stored field does not have one:
     that writes "I don't know" over text the extraction did read. Measured on the station: 10 of the
     370 field candidates bring it in (`115090:305:0402 q075` option C, `113020:308:11 q021` option A
     among them), 3 of those pass the other fences, and of the examples both are `CARE` - so the
     verdict fence stops them first and this clause is what stops a `TRUST` one.

   Both clauses are refusals of the *whole-field* form only; the character-level paths stay open for
   those fields, because they substitute measured characters and leave the tags (and the readable
   characters) standing.

   Four more clauses, added after the 2026-09-24 evening, and three of them are the machine's own
   whole-field work being judged (see the measured numbers below):

   * a reading that writes **Unicode sub/superscripts** (`GABAₐ`, `Kₘ`, `[AUC₍ᵢᵥ₎]₀∞`) where the
     stored text has the plain characters is refused (`unicode_sub_sup_complaint`): `<sub>`/`<sup>`
     is how the platform typesets this (6540 fields already use it, 55 carry the characters), and
     the characters have no capital letters - so the reading changed `GABA`A`` into a lowercase `ₐ`,
     which is not the same text. Markup *inside* the field is not what this clause measures.
   * a reading that introduces `<sub>`/`<sup>` is **wanted**, but only when the characters inside are
     exactly the stored characters in order (`markup_fidelity_complaints`): `KM` -> `K<sub>M</sub>`
     is a repair, `KM` -> `K<sub>m</sub>` is a different character wearing markup.
   * a **character pair** the whole-field readings of this run change on `SYSTEMATIC_PAIR_MIN` or more
     distinct questions is refused (`systematic_pairs`): one misread character repeated across
     questions is evidence about the *reading*, not four independent slips.
   * a **question** whose newest human word is a bounce of a machine whole-field repair has that
     repair **withdrawn** (`closed_questions`): the wrong change is put back, and the question is
     *not* closed to the machine (2026-09-24: 「機器改錯就給我重改，為什麼還給我還原回去原本錯的地方」
     - withdrawing restores `parser_original`, which is the flattened text the machine was fixing, so
     a permanent refusal parks the row on the wrong text). What stops a bounce becoming a loop is the
     signature (the same change is never written twice) and the markup gate, both of which are above.

   The first three of those are per-field; the fourth is per-question, and so is **atomicity**: a
   question's whole-field repairs pass together or none of them is written (a half-applied reading
   would leave two spellings of the same thing in one question - the owner's "某些KM 四個選項都有，
   結果只改某些，還改錯").

   Path 3 also **subsumes** the character-level edits on the same field (a whole-field statement is the
   same measurement at higher resolution), and it refuses to subsume the disputed positions it does not
   actually fix - see `_flagged_conflict`.

and explicitly **not** the `flat-offset` class any more, and that is a measured reversal. It was
in this list; the measurement that took it out is in `extract._body_centre`. `cm-1` was not a defect
in the text, it was a defect in the *reading*: the page raises the `-1` (9.03pt at y-centre 446.04
against 10.83pt prose at 449.93) and the extractor averaged the raised run into the baseline, so the
shift measured -1.61 instead of -2.82 and the run printed flat. Applying a substitution would have
written the right characters while leaving the reader that made them flat still in place, and the
next paper would have produced the same class again. The repair is the extractor, and it is measured
end to end: rebuilding `1141_醫事放射師_醫學物理學與輻射安全` now yields `0.5 R m² Ci⁻¹ h⁻¹` and the
`flat-offset` dispute is gone.

Explicitly **not** applied, and the reason is measured rather than stylistic:

    substituted-script     the dispute names the script (CYRILLIC) and *not* the intended character;
                           a Cyrillic `ћ` in `不可變參數？ћ總時間` is not recoverable by rule. 26
                           questions carry this and all 26 are left alone.
    lost-glyph             the paper has a character the text layer lacks; only the page says which.
    flattened-offset       the exponent form does not exist in Unicode; nothing to substitute.
    option-shape, empty-option, punctuation-only-option, dangling-answer, table-flattened
                           these are "content is missing", not "content is wrong" - the repair is a
                           re-read of the page, which is `confirm_dispute`'s job, not a substitution.
    the model findings     `qbr_ai_finding` is advisory (AGENTS.md); a prose `fix` never edits text.
                           Only the mechanical `changes` of a **page reading** are read here, and
                           only inside path 3's fence. The repair is never written as the model's
                           decision either: the event carries `reviewer=repair_dispute_apply`, a name
                           the server's `repair_` prefix keeps out of "a person decided this".

What a page reading can still not do - every clause is a measured refusal from this corpus, and path
3 reuses them (they are enforced in `anchored_page_changes` and `page_read_shape_complaint` /
`whole_field_refusals`):

    a reading that changes the *number* of characters
                           `115090 q053`'s reading inserted 93 characters and deleted the four
                           options, `114020 q060`'s inserted 80. Every position after the change moved,
                           so the diff describes a different line, not a repair.
    a reading that only inserts or deletes
                           `113020 q076`'s reading appended the acceptance-criteria table, `113020
                           q050`'s a figure description, `105020 q045`'s a whole compartment diagram
                           with invented arrow labels. Measured length ratios: 2.71, 2.02, 2.11.
    a reading that leaves a flagged character exactly where the detector said it was wrong
                           a partial repair would reopen the field with the defect still in it.
                           (measured 2026-09-24: 0 of the mirror's 509 field readings did this)
    a reading that reads a flagged position as a *different* character than the dispute's `means`
                           two measurements disagreeing about one position. The character-level path
                           hands the question to a person (`merge_substitutions` returns `None`); the
                           whole-field one is refused by `_flagged_conflict`. (measured: 0 cases too)
    a reading that would strip HTML markup the platform renders
                           `<sub>`/`<sup>` are the platform's own spelling of a subscript
                           (`H<sub>2</sub>PO<sub>4</sub><sup>-</sup>`); a reading renders the same
                           maths as real subscript characters (`Cₚ`, `4e⁻⁵ᵗ`), so replacing the whole
                           field would throw the markup away. `104090 q052`'s reading passed both
                           measurements - its shared suffix is a whole sentence, so the length line
                           never engaged - and a dress rehearsal showed the replacement had destroyed
                           `<sub>`/`<sup>`; it is refused for this reason alone, and the
                           character-level path stays open for such fields because it substitutes
                           measured characters and leaves the tags standing. (measured 2026-09-24,
                           station, read-only: 0 of the 225 events carrying a correction strip markup,
                           so this guards the whole-field path only)
    a reading that brings in the unreadable mark
                           `▢` is what `reread.SYSTEM` tells the model to write when it cannot read a
                           glyph, so applying it writes "I don't know" over text that was read.
                           (measured 2026-09-24, station: 10 of the 370 whole-field candidates -
                           `115090:305:0402 q075` option C, `113020:308:11 q021` option A among them;
                           both of those are `CARE`, which the verdict fence stops first)
    a reading the second engine did not call `TRUST`
                           `orchestration.verdict` (`orchestrator.py:46-52`). `CARE` means the
                           difference may change the answer or the reading contradicts itself,
                           `DOUBT` means the local reading is clearly wrong or invented, and an
                           absent verdict means no second read happened at all - all three are "a
                           person should look", which is where those questions already are. The
                           whole-field path is the most invasive action this loop takes, so it needs
                           that agreement; the character-level paths do not.

Why an event and not a rewrite of `candidates.jsonl`
----------------------------------------------------

The same reason `append_reset_review_events.py` gives. A candidate file is a *reading of the paper*;
a correction is a *decision about* that reading, and the two are different artifacts with different
lifetimes. Writing the event keeps the original reading intact (`parser_original` is preserved by the
server for exactly this), records who decided what, and survives a queue rebuild. Editing the
candidate text would destroy the evidence that the extraction was wrong - which is the thing the
rule was built from.

**The event is one `reset_review`, not a `correct` plus a reset.** `load_review_events` counts every
non-reset action as "reviewed", so a machine `correct` on a question nobody has judged makes it read
as **已看過** - a machine event wearing a person's decision, which AGENTS.md forbids outright. The
server reads a `correction` from `latest_reset_review` as well as from `latest`, so one reset event
both serves the fixed text and lands the question in `repair_pending` (**修復後待複核**) - repaired,
waiting for a person, which is what happened.

Machine repairs that were wrong, and what tonight measured
----------------------------------------------------------

The three rules added after the 2026-09-24 evening (the systematic pair, the two content clauses and
the bounce-back closure) are each named after a measurement, not after a worry. All of these are the
station's own numbers, read-only (the log, the findings, `candidates.jsonl`):

    18:06:35  the machine wrote **66** whole-field repairs - 66 questions, 90 `changes` - and the
              producer folded them into `candidates.jsonl`, so `parser_original` now holds what the
              extraction had said before each one.
    the damage  one character pair came out wrong on **four** questions with both engines saying
              `TRUST` (`抗癲癇` -> `抗癲癲`): `115090:305:0401 q037` (stem and option A),
              `113090:305:11 q053`, `108100:305:11 q035` option A, `108030:305:11 q061`. **69** fields
              of that run had Unicode sub/superscripts written into them (`GABAₐ`, `Kₘ`,
              `[AUC₍ᵢᵥ₎]₀∞`) where the paper prints plain capitals - the owner's "字型異常".
    the census  the CJK->CJK census over this run's readings finds **16** pairs; 15 of them stand on a
              single question each (`中→可`, `及→口`, `之→性`, `氯→氫` ... - the artefacts of a reflowed
              line), and `癇→癲` stands on **2** inside the census population. A third
              (`106020:302:22 q071`) is not standing-rejected and the other two
              (`115090:305:0401 q037`, `113090:305:11 q053`) have no changes at all in their newest
              reading (18:37:23 and 18:39:36), so the pair line at 3 did not fire tonight - it is the
              rule that would have caught the same misreading one question later.
    18:43:49  a second `--apply` wrote **14** more whole-field repairs (14 questions) on questions
              whose newest human word was a `block`. The signature check did not stop them because
              the re-read text was not identical to the bounced text: the ping-pong the owner's
              guardrail exists for (`110101:305:55 q061`: block -> repair -> block -> repair). That
              is why the bounce comparison is now also a refusal (`closed_questions`), and why the
              withdrawal covers every field the machine ever wrote on such a question.
    the revert  the applier wrote **52** withdrawal events (75 fields over 52 rows) and the producer
              restored them in one pass. Measured again with the ping-pong shape included, the same
              comparison finds **64 questions / 91 fields** (`bounce-back` 63/90, `unicode-sub-sup`
              1/1); **12** of those questions (16 fields) are new, and all 14 of the 18:43 repairs are
              inside the set.
    the fence   over the same run: `held back 268` readings on questions nobody rejected,
              `REFUSED 405` entries, `to repair 0`, `atomicity 2` - the ladder a person reads before
              trusting the number at the top of it.
    the ask     the census above is the machine's record; it is not a place a person looks. Measured
              2026-09-25 on the station: of the **197** blocked questions whose newest reading the
              fences refused (`CARE` 109, markup 39, `DOUBT` 27, characters 17, no second read 5),
              only **27** had an open question in the review UI - the other 170 were stuck, measured,
              and silent. The refuse line now asks a person about each one (`questions_to_ask`), with
              the two readings of up to two fields side by side, 40 a run, and never twice for the same
              text (`ask_id`, the reader's own id shape). `--ask-limit 0` turns it off.
    the markup  the two measurements now count the characters, not the tags: in a short field
              (`藥理作用標的是GABAA 受體` -> `藥理作用標的是 GABA<sub>A</sub> 受體`) the tags alone made
              the raw length factor 1.79 and the alignment 0.52 - both outside the fence, so a
              *wanted* reading was refused. Flattened: `(1.00, 1.00)`.

Governance
----------

    G3. This mutates the review state of questions a person may already have judged, so `--apply`
    is required. Dry run is the default, and the reviewer name carries the server's `repair_`
    prefix so the projection cannot read the event as a human decision. The event log's sha256
    before the write is printed, and a backup of the log is taken before the append.

    It never writes `accept`. A repair reopens a question; it does not close one. A withdrawal does
    not either: it puts the machine's own rewrite back to `parser_original` and says so (`withdrawn`),
    so the producer labels the row 已還原（機器改錯） and the question waits for a person.

    The ask is the same shape of thing: it appends to `question_repair_questions.jsonl`, the stream the
    reader already writes to, with `reviewer="repair_dispute_apply"` (a `repair_` prefix, so the
    projection cannot read it as a human decision), it is written only with `--apply`, and it is a
    question rather than a verdict - it decides nothing and moves no text. The reader's own gate
    (「人 must have blocked it」) is what picked the population, so every ask is about a question the
    person already said was wrong.

    The page-read path is the one place where a model's *reading* can move text, so it is the one
    place with a gate of its own: a standing human rejection on the question (`standing_rejections`)
    plus the alignment and length measurements on the field. The gate is never a detector. Everything
    the gate refuses is printed with its reason, so a person can see which readings stayed out and
    why - the list is the other half of the same decision.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import ai_findings, discuss, extract  # noqa: E402,F401

# ── re-exports ────────────────────────────────────────────────────────────────────────────────
#
# 這支是 composition root：實作在 `qbr/src/qbr/dispute_apply/`；此處**再匯出**那些模組定義的每一個
# 符號，因為腳本與測試都直接載入這個路徑。
#
# Every symbol the extracted modules define, made reachable from this module again. `test_*.py`
# loads this file by path and calls these directly, so removing a name here breaks tests that never
# import `qbr.dispute_apply` at all.
#
# The modules themselves, under their plain names, so that patching a module-level global patches the
# module that *owns* it - the re-exported copy here is a different binding (see `review_ui/AGENTS.md`).
from qbr.dispute_apply import asking as _asking
from qbr.dispute_apply import cli as _cli
from qbr.dispute_apply import page_read as _page_read
from qbr.dispute_apply import report as _report
from qbr.dispute_apply import text as _text
from qbr.dispute_apply import withdrawals as _withdrawals

asking = _asking
cli = _cli
page_read = _page_read
report = _report
text = _text
withdrawals = _withdrawals

# `cli.parse_args` builds the parser with `description=__doc__`，而 `--help` 印的就是那份說明。把實作
# 模組的 `__doc__` 指到這支腳本的 docstring，`--help` 的輸出才與拆分前逐字相同（含 Governance 段）。
_cli.__doc__ = __doc__

from qbr.dispute_apply.text import (  # noqa: F401,E402
    CJK_IDEOGRAPH_BLOCKS,
    KANGXI_BLOCK,
    RADICAL_BLOCKS,
    REPAIR_REVIEWER_PREFIXES,
    SUBSCRIPT_CHARS,
    SUPERSCRIPT_CHARS,
    UNICODE_SUB_SUP,
    _cjk_ideograph,
    _compatibility_fold,
    _normalisation_pairs,
    _radical_glyph,
    _sub_sup_char,
    _unique,
    _without_whitespace,
    aligned_changes,
    anchored_page_changes,
    applied_signature,
    apply_substitutions,
    build_correction,
    corrected_field,
    field_text,
    flagged_positions,
    is_machine_event,
    is_normalisation,
    last_repair_signature,
    latest_events,
    load_candidates,
    merge_substitutions,
    queue_relative,
    repair_signature,
    satisfied_sub,
    sha256_file,
    substitutions_for,
    verify,
)
from qbr.dispute_apply.page_read import (  # noqa: F401,E402
    MARKUP,
    PAGE_READ_ALIGN_MIN,
    PAGE_READ_LENGTH_FACTOR,
    SYSTEMATIC_PAIR_MIN,
    TRUST_VERDICT,
    UNREADABLE_MARK,
    _first_trigger,
    _flagged_conflict,
    _flagged_survivors,
    _page_read_shape,
    _record,
    _refuse,
    _verdict_complaint,
    _whole_field_decision,
    markup_dropped_complaint,
    markup_fidelity_complaints,
    markup_introduced,
    page_read_fields,
    page_read_replacements,
    page_read_shape_complaint,
    page_read_substitutions,
    reading_refused_fields,
    second_read_verdict,
    systematic_pairs,
    unicode_sub_sup_complaint,
    unicode_sub_sup_introduced,
    whole_field_refusals,
)
from qbr.dispute_apply.withdrawals import (  # noqa: F401,E402
    APPLICABLE,
    CLEARING_ACTIONS,
    FIELD_WITHDRAW_TRIGGERS,
    HUMAN_ACTIONS,
    REJECTING_ACTIONS,
    WITHDRAWN_LABEL,
    WITHDRAW_PRIORITY,
    _withdrawal_is_new,
    add_withdrawal,
    build_repair_event,
    build_withdrawal_event,
    closed_questions,
    existing_withdrawals,
    standing_rejections,
    withdrawal_reason,
    withdrawal_state,
)
from qbr.dispute_apply.asking import (  # noqa: F401,E402
    ASK_EXCERPT,
    ASK_LIMIT_DEFAULT,
    _clip,
    _reading_refusal,
    ask_for_refusals,
    ask_id,
    questions_to_ask,
    refusal_ask,
)
from qbr.dispute_apply.report import (  # noqa: F401,E402
    print_asks,
    print_page_read_census,
    print_plan,
    print_refusals,
    print_withdrawals,
)
from qbr.dispute_apply.cli import (  # noqa: F401,E402
    main,
    parse_args,
)
from qbr.dispute_apply.withdrawals import (  # noqa: F401,E402
    MAX_ATTEMPTS,
    rejection_counts,
)


if __name__ == "__main__":
    raise SystemExit(main())
