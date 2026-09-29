"""Review-UI queue/paper projection.

Extracted verbatim from `scripts/serve_question_review_ui.py` so one file is no longer 10,700 lines.
`serve_question_review_ui` re-exports every name here; that indirection is deliberate and is why the
test files that load the server by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations



import sys as _sys
from pathlib import Path as _Path

# The server's guarded imports fall back to `from scripts.x import ...`. `scripts/` has no
# `__init__.py`, so that is a namespace-package import and needs the *repository root* on `sys.path`
# — not the `scripts/` directory. Both are added: the root for `scripts.x`, the directory for the
# plain `import x` form that a bare `sys.path` entry would otherwise be needed for.
_REPO_ROOT = str(_Path(__file__).resolve().parents[4])
for _p in (_REPO_ROOT, _REPO_ROOT + "/scripts"):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from typing import Any
from pathlib import Path
from qbr import discuss
import json
import re
import threading
from .constants import CATEGORY_GROUP_FILTERS, QUESTION_READY_ACTIONS, REPAIR_REVIEWER_PREFIXES, _TAIL_ANCHOR_BYTES
from .events import _first_event_value

def principles_projection(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The principles currently in force, from the append-only add/remove stream.

    Delegates to `qbr.discuss.principles_projection`; see `load_append_only_events` for why the rule
    has exactly one implementation.
    """
    return discuss.principles_projection(events)


def repair_questions_projection(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The repair agent's questions and the person's answers, newest question first.

    Delegates to `qbr.discuss.repair_questions_projection`; see `load_append_only_events` for why the
    rule has exactly one implementation.
    """
    return discuss.repair_questions_projection(events)


def repair_asks_by_key(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The newest **unanswered** ask per `candidate_key` — what the row's own line needs.

    Delegates to `qbr.discuss.repair_asks_by_key`: same folding rule, grouped by question.
    """
    return discuss.repair_asks_by_key(events)


def category_matches_filter(category: str, category_filter: str) -> bool:
    normalized_category = normalize_category_name(category)
    if category_filter in CATEGORY_GROUP_FILTERS:
        return normalized_category in CATEGORY_GROUP_NORMALIZED_FILTERS[category_filter]
    return normalized_category == normalize_category_name(category_filter)


def normalize_category_name(value: Any) -> str:
    """Normalize category spelling for ReviewUI matching only.

    Official/raw names are deliberately not rewritten.  This matcher only
    removes harmless spacing and bracket-shape differences so old JSONL rows,
    SQL rows, and catalog-derived rows share one filter behavior.
    """
    text = str(value or "")
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


CATEGORY_GROUP_NORMALIZED_FILTERS = {
    key: frozenset(normalize_category_name(value) for value in values)
    for key, values in CATEGORY_GROUP_FILTERS.items()
}


def category_filter_values(category_filter: str) -> tuple[str, ...]:
    """Return SQL-safe aliases for a direct or制度群組 category filter."""
    values = CATEGORY_GROUP_FILTERS.get(category_filter, (category_filter,))
    expanded: set[str] = set()
    for value in values:
        raw = str(value or "")
        normalized = normalize_category_name(raw)
        expanded.update(
            {
                raw,
                normalized,
                normalized.replace("(", "（").replace(")", "）"),
            }
        )
    return tuple(sorted(value for value in expanded if value))


PRINCIPLES_STREAM = discuss.PRINCIPLES_STREAM


REPAIR_QUESTIONS_STREAM = discuss.REPAIR_QUESTIONS_STREAM


DISCUSS_BUCKETS = ("block", "repair_pending", "accepted_reaudit", "reset_review")


def is_discuss_bucket(review: dict[str, Any] | None) -> bool:
    """Is this row's projected review state one the 錯題討論區 shows?

    One function, because the rule had three copies - the JSONL filter loop and both SQL CTEs - and
    a fourth in the browser's `discussRowLabel`. Three copies of "what is stuck" is three chances
    for the list and the filter to disagree, and the area was rebuilt precisely because the previous
    definition ("every question") made the stuck ones unfindable.

    The input is the projection `filtered_candidate_payloads` already computed per row, so this
    cannot drift from the buckets the row displays: it reads the same four flags the row carries.
    """
    review = review if isinstance(review, dict) else {}
    return bool(
        review.get("action") == "block"
        or review.get("is_repair_pending")
        or review.get("is_accepted_reaudit_pending")
        or (review.get("is_reset_unreviewed")
            and not review.get("is_repair_pending")
            and not review.get("is_accepted_reaudit_pending"))
    )


SQL_DISCUSS_PREDICATE = "(" + " OR ".join((
    "COALESCE(review_action, '') = 'block'",
    "is_repair_pending",
    "is_accepted_reaudit_pending",
    "(is_reset_unreviewed AND NOT is_repair_pending AND NOT is_accepted_reaudit_pending)",
)) + ")"

#: `is_reset_unreviewed` 的 SQL 版，與 `review_projection` 的 `reset_waiting` 同一條判準。
#:
#: 2026-09-25 之前，兩邊都只讀「最後一筆是不是 reset_review」，於是機器把自己改錯的字收回去
#: （`applied: "withdrawn"`）的那一題在 SQL 後端仍然算「待複核」——`reviewStatus=reset_review`、
#: `reviewStatus=discuss` 那兩條路都會把它撈出來，而它在題目區那一列自己的字是「已還原（機器改錯）」。
#: 撤回不是修改（owner 原文：「「還原」這種事情不是修改」），所以兩個後端都要看同一件事：`applied`。
#: 站上量到 127 題帶著撤回、其中 3 題是這一種（撤回之後沒有任何人的決定）。
SQL_RESET_UNREVIEWED_PREDICATE = (
    "(lq.action IN ('unreviewed', 'reset_review') "
    "AND COALESCE(lq.event_json->>'applied', '') <> 'withdrawn')"
)


def _paper_of_candidate(row: dict[str, Any]) -> str:
    """The paper a candidate row belongs to, spelled exactly as the browser's `paperOf()`.

    One spelling, because the review UI's scope walk (`whereOfPaper`) matches a tree entry's
    `papers` list against *this* string. A second server-side spelling that differed by a character
    - a full-width bracket, a trailing `.pdf` - would produce a taxonomy whose papers the browser
    can never find, and the picker would offer a choice that opens nothing. The two spellings are
    pinned together by `tests/test_review_ui_discuss.py`.
    """
    metadata = row.get("metadata") or {}
    source = metadata.get("question_pdf_relative") or metadata.get("question_pdf") or ""
    name = str(source).split("/")[-1]
    return name[:-4] if name.lower().endswith(".pdf") else name


def paper_entries_for(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One taxonomy entry per paper, built from the rows themselves.

    `questions` counts the rows **passed in**, not the paper's whole size. In the 錯題討論區 the rows
    are the stuck ones, so the count beside a subject is how many stuck questions it holds - which
    is the number a reviewer choosing that subject can then act on. The whole paper's count is a
    second number the picker cannot act on, and showing it would make a filter look empty.

    Handed to `review_queue.taxonomy_of`, which is the **single** tree implementation - the one that
    exists because a second copy once shipped a different shape and blanked the question area.
    """
    seen: dict[str, dict[str, Any]] = {}
    order: list[dict[str, Any]] = []
    for row in rows:
        paper = _paper_of_candidate(row)
        if not paper:
            continue
        entry = seen.get(paper)
        if entry is None:
            metadata = row.get("metadata") or {}
            exam_code = str(metadata.get("exam_code") or "")
            year = metadata.get("year")
            if year in (None, "") and len(exam_code) >= 3 and exam_code[:3].isdigit():
                year = int(exam_code[:3])
            entry = {
                "paper": paper,
                "questions": 0,
                "category": metadata.get("normalized_category_name")
                or metadata.get("group_name") or metadata.get("official_category_name") or "",
                "subject": metadata.get("normalized_subject_name")
                or metadata.get("official_subject_name") or "",
                "year": year,
                "ordinal": metadata.get("exam_ordinal"),
            }
            seen[paper] = entry
            order.append(entry)
        entry["questions"] += 1
    return order


QBR_AI_FINDING_FIELDS = ("candidate_key", "created_at", "model", "endpoint", "prompt_version",
                         "population", "reading_sha256", "error", "seconds",
                         # The two fields a *confirmation* adds, and the whole value of it: `crop` is
                         # the screenshot the model was shown, so the reviewer can check the note
                         # against the same picture instead of trusting the sentence; `changes` is the
                         # mechanical difference, which is what makes the note repairable rather than
                         # merely a report. Dropping them here would leave the finding looking complete
                         # while its evidence and its repair were both missing from the screen.
                         "crop", "changes",
                         # Where the finding is *about*, so a list of questions built from this store
                         # can be read by a person: a dropdown of 25 candidate keys with no question
                         # number and no paper is a list nobody can choose from. Part of the record
                         # already (`make_record` writes them), and three short strings per finding is
                         # cheaper than a second lookup per row.
                         "question_number", "paper", "subject")


def _compact_qbr_finding(record: dict[str, Any]) -> dict[str, Any]:
    """One finding, stripped to what the screen draws.

    The one place the projection lives, so the whole-file loader and the tail loader cannot
    disagree about which fields survive.
    """
    compact = {field: record.get(field) for field in QBR_AI_FINDING_FIELDS}
    compact["finding"] = record.get("finding") or {}
    compact["evidence"] = record.get("evidence") or {}
    return compact


def load_qbr_ai_findings(path: Path) -> dict[str, dict[str, Any]]:
    """The latest qbr AI finding per question, stripped to what the screen draws.

    Append-only, last record per key wins - the same rule `load_keyed_events` uses for the human
    log, and for the same reason: a finding is a statement about the reading that was in front of
    the model, and the newest statement is the one that describes the current reading.

    Read as **bytes** and decoded one line at a time. Text-mode iteration raises
    `UnicodeDecodeError` on a file that is being appended to right now (a multi-byte character can
    be half-written) or one that was truncated, and that crashed the whole server: the whole-file
    loader is what a rewrite falls back to, so a damaged file took down every request instead of
    dropping one bad line. A line that does not decode is skipped exactly like one that does not
    parse.
    """
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            key = record.get("candidate_key")
            if not key:
                continue
            latest[key] = _compact_qbr_finding(record)
    return latest


class QbrAiFindingsStore:
    """The latest AI finding per question, kept fresh by reading only what was appended.

    The stream is append-only by contract, and enforced as one: the model-side writer is
    `qbr/src/qbr/ai_findings.py::append` (a single `open(path, "a")`), the push helper appends with
    `cat >>` and never truncates, and a rebuild carries the records forward into a fresh directory.
    A new line at the end is therefore the only way the file can grow. Reading the whole file again
    for each appended line cost **1.4 s per request** on the served queue (measured: 480 MB,
    73,688 records), because every `refresh_event_logs` sees a changed signature while the corpus
    sweep is running. Reading the tail costs the size of the new line - and the result must be
    **identical**, which is what `test_the_tail_loader_equals_the_whole_file_loader` pins.

    A rewrite must not be mistaken for an append: reading "from the old offset in a different
    file" would silently produce a store that mixes two files, which is worse than a slow one
    because it looks like a working answer. Three cheap tests decide it, and all of them are about
    the bytes rather than about `mtime` (an append moves `mtime` too, so it cannot separate them):

      * the file's identity (`st_dev`, `st_ino`) - changes when a deploy or a rebuild swaps the
        file in (`rsync -a` without `--inplace` renames into place);
      * its size - a truncated file is shorter than the offset we hold;
      * two 64-byte windows, the head and the bytes just before our offset. An append cannot change
        either.

    What this deliberately does **not** claim: a same-inode, same-size patch of the **middle** of the
    file is not distinguishable from the file we already read, and is not detected. No writer does
    that - the contract is `open(path, "a")`, `cat >>`, and a rebuild that writes a **new
    directory** - so the guard is sized to the failure modes that exist rather than to every way a
    file could be edited. A guard that claimed more would be the kind of rule that only holds until
    someone reads it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._latest: dict[str, dict[str, Any]] = load_qbr_ai_findings(path)
        self._offset = 0
        self._identity: tuple[int, int] | None = None
        self._anchors: list[tuple[int, bytes]] = []
        self._observe()

    def _window(self, start: int, length: int = _TAIL_ANCHOR_BYTES) -> bytes:
        """Exactly `length` bytes at `start`, or fewer at the end of the file.

        The length is passed in rather than always being `_TAIL_ANCHOR_BYTES` because an anchor must
        be a **fixed** number of bytes: when the file starts out shorter than the window, re-reading
        a fixed 64 bytes would return the old bytes *plus* the start of whatever was just appended,
        and an ordinary append would look like a rewrite (found by
        `test_a_half_written_line_is_not_a_record_yet`).
        """
        try:
            with self.path.open("rb") as handle:
                handle.seek(max(0, start))
                return handle.read(length)
        except OSError:
            return b""

    def _observe(self) -> None:
        """Record the identity, size and anchor windows of the file as it is now."""
        try:
            stat = self.path.stat()
        except OSError:
            self._identity = None
            self._offset = 0
            self._anchors = []
            return
        self._identity = (stat.st_dev, stat.st_ino)
        self._offset = stat.st_size
        # Each anchor is `(start, bytes)`, and `len(bytes)` is the length that will be compared
        # against later, so the comparison can never grow.
        size = stat.st_size
        self._anchors = [
            (0, self._window(0, min(_TAIL_ANCHOR_BYTES, size))),
            (max(0, size - _TAIL_ANCHOR_BYTES), self._window(size - _TAIL_ANCHOR_BYTES)),
        ]

    @property
    def latest(self) -> dict[str, dict[str, Any]]:
        return self._latest

    def refresh(self) -> bool:
        """Read whatever was appended since the last call. True if the store changed.

        The read starts at the last complete line's end, so a line caught mid-write is simply not a
        record yet - it becomes one on the next refresh, once its newline has arrived.
        """
        with self._lock:
            try:
                stat = self.path.stat()
            except OSError:
                return False
            size = stat.st_size
            identity = (stat.st_dev, stat.st_ino)
            rewritten = identity != self._identity or size < self._offset
            if not rewritten and self._offset:
                # An append cannot change a byte that is already written. Each anchor is compared at
                # the exact position and length it was taken at; either differing means: read the
                # whole file again.
                rewritten = any(
                    self._window(start, len(expected)) != expected
                    for start, expected in self._anchors
                )
            if rewritten:
                self._latest = load_qbr_ai_findings(self.path)
                self._observe()
                return True
            if size == self._offset:
                return False
            try:
                with self.path.open("rb") as handle:
                    handle.seek(self._offset)
                    data = handle.read()
            except OSError:
                return False
            # Only complete lines are records: the last one may be half-written right now.
            end = data.rfind(b"\n")
            if end < 0:
                return False
            consumed = data[: end + 1]
            # Copy-on-write, not in-place: every reader holds the dict object it got from `latest`,
            # and the old loader gave each new generation its own dict (an atomic reference swap).
            # Mutating this one in place would let a request that is halfway through answering see
            # a half-updated store. The copy is 0.35 ms for 73k records and only happens when there
            # is something new.
            updated = dict(self._latest)
            changed = False
            for raw in consumed.split(b"\n"):
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                key = record.get("candidate_key")
                if not key:
                    continue
                updated[key] = _compact_qbr_finding(record)
                changed = True
            self._latest = updated
            self._offset += len(consumed)
            # The stored anchors still describe the prefix, which an append did not touch - but the
            # one nearest the end may now be short of the new end, so it is re-taken at the fixed
            # length we will compare next time.
            self._anchors = [
                (start, self._window(start, len(expected)))
                for start, expected in self._anchors
            ]
            return changed


#: How many questions the 類似題 dropdown lists for one principle. The list is built **server-side**
#: over the finding store (73k records on the served queue): a browser cannot scan that, and a list
#: that silently stops at the cap is the truncation the question area already had to fix - so the
#: response carries `matched`／`capped` and the screen says it.
SIMILAR_LIMIT = 25

#: The most a caller may ask for. The list is meant to be read, and a dropdown with 200 rows in it is
#: a list nobody reads; the cap exists so `?limit=` cannot turn the endpoint into a bulk export.
SIMILAR_LIMIT_MAX = 200


def change_field_key(field: Any) -> str:
    """One change's **field shape**: `option A` and `option C` are the same shape, `option`.

    The option *letter* is part of the difference, not of its shape - a principle about "an option
    whose text was misread" covers every letter. And the `where` sentence a finding carries is built
    from exactly these field names (`confirm_dispute.finding_from` joins them with 「；」), so matching
    on the field set **is** matching the `where` shape; the machine-readable form is the one that can
    be compared without parsing a sentence, and parsing it would be a second opinion about what the
    finding is.
    """
    text = str(field or "").strip().lower()
    return text.split(" ", 1)[0] if text.startswith("option") else text


def finding_signature(finding: dict[str, Any] | None) -> dict[str, Any]:
    """A finding's shape: which detectors raised it, and which fields differ.

    `kinds` is read from `evidence.disputes[].kind` - the dispute kinds the confirmation was made
    about, which `confirm_dispute._append_finding` stores beside the note. `what` is the model's own
    code for the shape, used only as a bonus in the score: two findings that name the same code and
    no shared field are a weaker claim than two that differ in the same field.
    """
    record = finding if isinstance(finding, dict) else {}
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    kinds = {str(dispute.get("kind") or "").strip()
             for dispute in (evidence.get("disputes") or []) if isinstance(dispute, dict)}
    fields = {change_field_key(change.get("field"))
              for change in (record.get("changes") or []) if isinstance(change, dict)}
    body = record.get("finding") if isinstance(record.get("finding"), dict) else {}
    return {"kinds": sorted(kind for kind in kinds if kind),
            "fields": sorted(field for field in fields if field),
            "what": str(body.get("what") or "").strip()}


def question_identity(item: dict[str, Any] | None) -> dict[str, str]:
    """Which question a candidate key is: 類別／年／次／科目／第 N 題.

    The five fields the question area prints for a row (`#where`: 「第 N 題 · 類別 · YYYY年第N次 ·
    科目」), projected off the raw candidate record. It exists as one function because the principles
    area has to answer "which question is this principle / this question from the agent *about*" for
    keys that are not on screen — a list of `moex:114020:305:0403:1:question:q054` is a list nobody
    can read (measured 2026-09-25: 121 open agent questions, all shown as a truncated key). Two
    places composing that sentence would be two places it can disagree, so the composition is the
    client's and the *fields* are here.

    A key with no record (a question a rebuild moved away) yields empty strings; the caller says so
    rather than printing a half-identity, because an identity that is wrong is worse than none.
    """
    row = item if isinstance(item, dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "question_number": str(row.get("question_number") or ""),
        "category": str(metadata.get("normalized_category_name") or metadata.get("group_name") or ""),
        "subject": str(metadata.get("normalized_subject_name") or ""),
        "year": str(metadata.get("year") or ""),
        "ordinal": str(metadata.get("exam_ordinal") or ""),
    }


def similar_question_row(candidate_key: str, finding: dict[str, Any] | None, source: str,
                         score: int = 0) -> dict[str, Any]:
    """One row of the 類似題 list: enough to *read* it (題號／卷／科目) and to call it up by key."""
    record = finding if isinstance(finding, dict) else {}
    return {"candidate_key": str(candidate_key), "question_number": record.get("question_number"),
            "paper": record.get("paper"), "subject": record.get("subject"),
            "source": source, "score": int(score)}


def similar_questions(principle: dict[str, Any] | None,
                      findings: dict[str, dict[str, Any]] | None,
                      *, limit: int = SIMILAR_LIMIT) -> dict[str, Any]:
    """The questions a principle covers: **its own `evidence` keys first**, then the same shape.

    The order is the claim. `evidence` is what the principle says it came from (a curated principle
    names its question; one typed in the review UI while a paper is open carries that question's key),
    so those rows are listed first and marked `source: "evidence"` - they are read off the principle,
    not guessed from a shape. Everything after them is *inferred*, and is marked as such.

    The shape is the one the principle's own findings have: the union of their dispute `kinds` and
    their difference `fields` (`finding_signature`). A question matches when it shares at least one of
    either, and the score is `2 × shared fields + shared kinds` (+1 when the model's own `what` code
    agrees), because sharing the *field* that was misread is the stronger statement: two findings
    raised by one detector on different fields are the same kind of suspicion, while two that differ
    in the same field are the same repair. Ties are broken by candidate key, so the list is stable
    between two requests - a list that reorders itself looks like new information every time.

    A principle with no evidence has no shape to match on, so the list is empty; that is a real
    answer, and the screen says 「這條沒有指定題目」 rather than showing nothing.
    """
    findings = findings if isinstance(findings, dict) else {}
    keys = [str(key).strip() for key in ((principle or {}).get("evidence") or []) if str(key).strip()]
    seeds = [similar_question_row(key, findings.get(key), "evidence") for key in keys]
    signature = {"kinds": [], "fields": [], "what": ""}
    for key in keys:
        one = finding_signature(findings.get(key))
        signature["kinds"] = sorted(set(signature["kinds"]) | set(one["kinds"]))
        signature["fields"] = sorted(set(signature["fields"]) | set(one["fields"]))
        signature["what"] = signature["what"] or one["what"]
    kinds = set(signature["kinds"])
    fields = set(signature["fields"])
    matched: list[tuple[int, str, dict[str, Any]]] = []
    if kinds or fields or signature["what"]:
        for key, record in findings.items():
            key = str(key)
            if key in keys:
                continue
            one = finding_signature(record)
            score = 2 * len(fields & set(one["fields"])) + len(kinds & set(one["kinds"]))
            if signature["what"] and one["what"] == signature["what"]:
                score += 1
            if score:
                matched.append((score, key, record))
    matched.sort(key=lambda item: (-item[0], item[1]))
    rows = seeds + [similar_question_row(key, record, "shape", score)
                    for score, key, record in matched]
    return {"rows": rows[:limit], "matched": len(rows), "returned": min(len(rows), limit),
            "capped": len(rows) > limit, "limit": limit, "signature": signature,
            "evidence": keys}


#: The three kinds of machine-applied text change, and the owner's words for them:
#:
#:   * `field`          「依紙本改字」 — the whole field was replaced from the page read. **This is the
#:                      one a person has to look at.**
#:   * `glyph`          「字形替換」 — a character-level change whose characters are *not* all radical
#:                      codepoints (`ћ`→`①`, Cyrillic U+04xx): the screen shows something different,
#:                      so it needs eyes.
#:   * `normalisation`  「正規化（部首碼位）」 — a character-level change where **every** changed
#:                      character is a CJK radical (`U+2E80–U+2EFF`, `U+2F00–U+2FDF`). The glyph on
#:                      screen is identical; only the codepoint was wrong.
#:
#: The values are the contract the tests assert; the Chinese above is what the screen prints for them.
#: Why the third class is not folded into the second: measured on the station (2026-09-24), of 354
#: machine repairs across 225 questions **345 are radical-codepoint normalisations, 9 are Cyrillic /
#: glyph substitutions and 0 are text-content repairs**. A review bucket where 97% of the rows read
#: the same on screen as before is a bucket nobody can review; counting them separately is what makes
#: 「依紙本改字」 a number the owner can act on.
APPLIED_KINDS = ("field", "glyph", "normalisation")

#: The machine's own retraction of one of those repairs (2026-09-24). It is deliberately **not** in
#: `APPLIED_KINDS`: those three answer "how much of this bucket do I still have to read", and a
#: withdrawal is the opposite of work left over — the row is back to the paper's text. It gets its
#: own count so the owner can see that a machine edit he rejected was actually taken back.
WITHDRAWN_KIND = "withdrawn"

#: Where a radical codepoint lives: CJK Radicals Supplement and Kangxi Radicals.
RADICAL_BLOCKS = ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF))


def in_radical_blocks(char: str) -> bool:
    """Is this one character a CJK radical — a glyph whose codepoint was normalised, not a word?"""
    code = ord(char)
    return any(low <= code <= high for low, high in RADICAL_BLOCKS)


def changed_characters(changes) -> set[str]:
    """The characters a recorded change actually swapped, read from the change text.

    Both spellings a change may use are read: `from`/`to` (the pair the applier edited) and
    `stored`/`page` (the whole-field pair from the finding). The **symmetric difference** of the two
    sides is the set of characters that appeared or disappeared; a character that merely moved
    position is not in it, which is right — moving a character is not a glyph substitution.
    """
    chars: set[str] = set()
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        before = change.get("from")
        after = change.get("to")
        if before in (None, ""):
            before = change.get("stored")
        if after in (None, ""):
            after = change.get("page")
        chars |= set(str(after or "")) ^ set(str(before or ""))
    return chars


def machine_applied_kind(event: dict[str, Any] | None) -> str | None:
    """Which of the three classes one machine repair belongs to; `None` when it repaired nothing.

    `None` is the honest answer for an event written before `applied` existed, and for any event that
    is not a machine repair at all — "not recorded" must not read as "nothing changed on screen"
    *or* as "changed text", so it stays a third, nameless state and the screen keeps its plain
    wording.

    The class is decided by, in order: `applied == "field"` (the whole field was replaced — never a
    normalisation, whatever the characters are); the applier's own `normalisation` flag when it is
    present (a boolean, so both `true` and `false` are honoured); otherwise the changed codepoints —
    all radical means the screen did not change, anything else means it did. When a substitution
    records no change text at all, the fallback is `glyph`: the side that needs eyes is the safe one
    to be wrong on.
    """
    if not isinstance(event, dict):
        return None
    applied = str(event.get("applied") or "").strip().lower()
    if applied == WITHDRAWN_KIND:
        # The machine took a repair back. Named rather than hidden: the owner rejected the repair
        # (or the applier refused its own reading on the next run), and the text is the paper's
        # again, which is a different thing from "never touched" — see `WITHDRAWN_KIND`.
        return WITHDRAWN_KIND
    if applied == "field":
        return "field"
    if applied != "substitution":
        return None
    flag = event.get("normalisation")
    if isinstance(flag, bool):
        return "normalisation" if flag else "glyph"
    chars = changed_characters(event.get("changes"))
    if chars and all(in_radical_blocks(char) for char in chars):
        return "normalisation"
    return "glyph"


def machine_activity_counts(reset_reviews: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """How many questions the machine actually touched, per class, over the whole review log.

    Counted from the **same fold the labels come from** (`machine_applied_kind` per reset event), so
    the home card's three numbers and the three labels on the rows cannot disagree: a number that no
    row matches is worse than no number. The whole log is walked because the question the card
    answers is "how much of this bucket do I have to read", which is not a property of the rows
    currently on screen.
    """
    counts = {kind: 0 for kind in APPLIED_KINDS}
    counts[WITHDRAWN_KIND] = 0
    for event in (reset_reviews or {}).values():
        kind = machine_applied_kind(event)
        if kind:
            counts[kind] += 1
    counts["total"] = sum(counts[kind] for kind in APPLIED_KINDS)
    return counts


def review_projection(
    latest_review: dict[str, Any] | None,
    latest_reset_review: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project append-only review events into disjoint human queue buckets.

    ``unreviewed`` is reserved for candidates with no question-level human
    event at all.  Parser/text repairs and accepted-question re-audits are
    still open work, but they are separate buckets so a reviewer does not see
    a repaired or already-accepted item masquerading as a brand-new question.
    This function is deliberately read-only: it never rewrites events or
    candidate content.
    """

    latest = latest_review if isinstance(latest_review, dict) else None
    reset = latest_reset_review if isinstance(latest_reset_review, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    event = latest or reset or {}
    action = _first_event_value(event, "action")
    reset_action = _first_event_value(reset, "action")
    previous_action = _first_event_value(
        reset,
        "previous_action",
        "preserved_action",
        "previous_review_action",
    )
    approval_ref = _first_event_value(reset, "approval_ref", "approval", "approval_reference")
    # **機器有沒有真的動到這一題的文字（2026-09-24）。** The applier writes `applied` on its
    # `reset_review`: `field` when the whole field was replaced from the page read, `substitution`
    # when character-level substitutions were made. An older reset event carries neither, and
    # "not recorded" must read as a **plain** reset (the machine returned the question without
    # touching the text) rather than as a third state - that is the honest reading of an event
    # written before the field existed, and it is what makes the label below say something true.
    # Projected here rather than re-read from the log in the browser: the row already carries the
    # event's projection, and a second reader of the log is a second place the two can disagree.
    applied_event = reset if _first_event_value(reset, "applied") else latest
    applied = _first_event_value(applied_event, "applied")
    applied_kind = machine_applied_kind(applied_event)
    # **機器在你決定之後動了文字（`pending_reset`）＝這一列等的不是你的判決，是「新文字對不對」。**
    #
    # 2026-09-25 owner 回報：「有些 block 的題目其實已經被改好了，但是沒有被歸類到 AI 已解決，我就
    # 認為 block 還是很多」。站上量到（唯讀）：`latest` 是人的 block 共 **341** 題，其中 **77** 題在
    # block 之後機器又寫了 reset，而這 77 題**全部**落在 `reviewed`——畫面上就是一句「已標記：阻擋」。
    # 77 題裡 52 題的 reset 是 `withdrawn`（機器把改動還原，那是他打回的，維持阻擋才對）、
    # **22 題 `applied=field`（機器的確把字改成了紙本那個字）**、7 題沒有 `applied`（舊事件）。
    # 那 22 題就是他看到的「block 還是很多」：字已經是他要的字，標籤卻停在阻擋。
    #
    # 判準用**檔案順序**而不是時鐘：機器的時鐘是筆電當地時間、人的事件是伺服器 UTC，所以「誰比較晚」
    # 只能讀出來的順序回答，而那正是 `pending_reset` 這個標記的來源（`events.load_review_events`）。
    # 「有沒有真的改到文字」用同一個 `machine_applied_kind`，畫面那三種標籤與這裡的桶位就不可能有
    # 第二種說法。`withdrawn` 不算：文字被還原成抽取原文（`parser_original`），這一題等的是他自己的
    # 下一個決定，不是複核機器的字。
    repaired_after_decision = bool((latest or {}).get("pending_reset")
                                   and applied_kind in APPLIED_KINDS)
    # **撤回不讓這一題在「AI已修改」那一格等你**（owner 2026-09-25，同一天修）。
    #
    # 他的原文：「AI以解決裡面會有一些題目寫「已還原(機器改錯)」…你這樣做是多此一舉，因為你退回
    # 等於沒有解決…又退回到我一定會認真看的「AI已解決」，就會讓我很火大…可以改成AI已修改，但是
    # 「還原」這種事情不是修改」。也就是說：機器把自己改錯的字收回去、文字回到紙本那一版，這一列
    # **沒有被 AI 改過**，它等的不是「新文字對不對」，是那個人自己的下一個決定。
    #
    # 站上量到（唯讀，8,712 筆事件）：`applied=withdrawn` 130 筆、127 題。其中 123 題的人為判決還在
    # `latest`（那些列本來就畫成那個人自己的 `block`，這是 2026-09-24 修好的那一條），**3 題**是
    # 撤回之後沒有人再做過任何決定——那 3 題當天落進「AI已解決」，而同一列自己的字是
    # 「已還原（機器改錯）」，讀起來就是「AI 修好了」。判準與 `machine_applied_kind` 同一份：
    # 只有 `withdrawn` 不算待複核，`field`／`glyph`／`normalisation`（真的動過字）照舊。
    reset_waiting = (bool(reset and not latest and applied_kind != WITHDRAWN_KIND)
                     or repaired_after_decision)
    reviewer = _first_event_value(reset or latest, "reviewer")
    repair_kind = _first_event_value(
        reset or latest,
        "repair_kind",
        "repair_type",
        "repair_action",
        "repair_scope",
        "source_event_id",
    )
    notes = _first_event_value(
        reset or latest,
        "reset_notes",
        "notes",
        "previous_notes",
    )
    metadata_sources = [
        str(metadata.get(key) or "")
        for key in ("review_block_repair", "backfill_repair", "backfill_source")
        if metadata.get(key)
    ]
    repair_reviewer = reviewer.startswith(REPAIR_REVIEWER_PREFIXES)
    repair_note = bool(re.search(r"修復|正規化|待複核|需人工複核", notes))
    repair_event = bool(repair_kind or repair_reviewer or repair_note or metadata_sources)
    was_previously_accepted = (
        previous_action in QUESTION_READY_ACTIONS
        or ("accepted" in approval_ref.lower() and "reaudit" in approval_ref.lower())
        or ("accepted" in reviewer.lower() and "reaudit" in reviewer.lower())
    )
    is_accepted_reaudit_pending = bool(reset_waiting and was_previously_accepted)
    is_repair_pending = bool(reset_waiting and not is_accepted_reaudit_pending and repair_event)
    is_reset_unreviewed = bool(reset_waiting)
    is_never_reviewed = not latest and not reset
    if is_never_reviewed:
        queue_bucket = "never_reviewed"
        display_label = "未看過"
    elif is_accepted_reaudit_pending:
        queue_bucket = "accepted_reaudit"
        display_label = "已通過後待複核"
    elif is_repair_pending:
        queue_bucket = "repair_pending"
        display_label = "修復後待複核"
    elif is_reset_unreviewed:
        queue_bucket = "reset_review"
        display_label = "退回未審"
    else:
        queue_bucket = "reviewed"
        display_label = "已看過"
    return {
        "has_human_event": bool(latest or reset),
        "is_never_reviewed": is_never_reviewed,
        "is_reset_unreviewed": is_reset_unreviewed,
        "is_repair_pending": is_repair_pending,
        "is_accepted_reaudit_pending": is_accepted_reaudit_pending,
        "was_previously_accepted": was_previously_accepted,
        "previous_action": previous_action or None,
        "reset_action": reset_action or None,
        # `field`／`substitution` when the applier replaced text from the page read, else None.
        # The browser turns this into the row label (「依紙本改字」 vs 「AI已修改」); the server
        # does not choose the words, because a label is a sentence and the projection is a fact.
        "applied": applied or None,
        # `field`／`glyph`／`normalisation` — the **class** of that change, so the words above have
        # one source. Absent (None) when nothing was applied, which the screen draws as the plain
        # wording rather than as a fourth kind.
        "applied_kind": applied_kind,
        "reviewer": reviewer or None,
        "repair_kind": repair_kind or None,
        "approval_ref": approval_ref or None,
        "repair_event": repair_event,
        "queue_bucket": queue_bucket,
        "display_label": display_label,
        "action": action or None,
    }
