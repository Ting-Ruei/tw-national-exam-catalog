"""Where the review UI server's source text lives, for tests that assert on it.

Several tests prove a rule "lives in one place" by searching the server's source for a marker — e.g.
`test_no_path_still_treats_only_correct_as_needing_reaffirming` greps for a predicate, and the
repaired-text test greps for `review_queue.disputes_for_paper(`. Those assertions are about the
implementation, so they must keep reading it.

On 2026-09-23 the 10,713-line `scripts/serve_question_review_ui.py` was split: the implementation
moved to `qbr/src/qbr/review_ui/*.py` and the server became a composition root that re-exports it.
A test that reads the server file alone would now find none of the code it is checking and fail for
the wrong reason — reporting a missing rule when the rule simply moved.

`server_source()` is the one place that answers "what is the server's source". Use it instead of
reading a path directly, so the next move only has to change this file.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The composition root. Kept as a name because a few tests want *only* this file (the re-export
#: contract, the argument parser).
SERVER_PATH = ROOT / "scripts" / "serve_question_review_ui.py"

#: The extracted implementation, in load order. `qbr/src/qbr/review_ui/` holds the modules the
#: server used to contain.
IMPL_DIR = ROOT / "qbr" / "src" / "qbr" / "review_ui"


def server_source() -> str:
    """The server's full source: the composition root plus every extracted module.

    This is the honest answer to "the server's code" after the split, and it is what the
    single-source assertions should search. Order is stable (server first, then modules by name) so a
    failure message is reproducible.

    A test asserting `source.count("def X(") == 1` is checking for a *duplicate implementation*, so
    this must not pick up a copy of the old file. The extractor writes
    `serve_question_review_ui.py.pre-split` next to the server; globbing `*.py` in the impl dir is
    safe, but globbing the scripts directory would not be — hence the explicit list.
    """
    parts = [SERVER_PATH.read_text(encoding="utf-8")]
    if IMPL_DIR.is_dir():
        for path in sorted(IMPL_DIR.glob("*.py")):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def server_files() -> list[Path]:
    """Every file whose text `server_source()` concatenates."""
    files = [SERVER_PATH]
    if IMPL_DIR.is_dir():
        files.extend(sorted(IMPL_DIR.glob("*.py")))
    return files
