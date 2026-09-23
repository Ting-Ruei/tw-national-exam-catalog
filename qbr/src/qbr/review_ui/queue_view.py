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
                         "crop", "changes")


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
    reset_waiting = bool(reset and not latest)
    action = _first_event_value(event, "action")
    reset_action = _first_event_value(reset, "action")
    previous_action = _first_event_value(
        reset,
        "previous_action",
        "preserved_action",
        "previous_review_action",
    )
    approval_ref = _first_event_value(reset, "approval_ref", "approval", "approval_reference")
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
        "reviewer": reviewer or None,
        "repair_kind": repair_kind or None,
        "approval_ref": approval_ref or None,
        "repair_event": repair_event,
        "queue_bucket": queue_bucket,
        "display_label": display_label,
        "action": action or None,
    }
