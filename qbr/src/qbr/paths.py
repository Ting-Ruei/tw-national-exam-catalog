# -*- coding: utf-8 -*-
"""Where the corpus is, found by looking for it.

**Why this module exists.** Before it, eight places each decided the corpus location for
themselves, and they used two incompatible rules:

* `three_way.py`, `survey_categories.py`, `golden_path.py` - `dirname(dirname(PKG))` then
  append `tw-national-exam-catalog/國考題資料夾`
* `batch_run.py`, `batch_package.py`, `fetch_corrections.py` - `dirname(PKG)/..` then the same
* `where_is_the_text.py` - a **hard-coded absolute path**, which the charter forbids

The rule based on counting directories is not wrong in the sense of being broken; it is wrong in
the sense of being a statement about **where this code happens to live**, dressed up as a
statement about the repository. It is correct today, in this checkout, by coincidence:
`pi_test/question_bank_rebuild/` and `tw-national-exam-catalog/qbr/` sit at the same depth, so the
same two `dirname`s happen to land on `ai_learning_platform/`.

A fresh `git clone` of the catalog repository alone is one directory shallower, and there the
counted path points outside the repository entirely. Measured: on a clean checkout the corpus
resolution fails, and two tests report a defect in the pipeline (`'moex:105020:305:33:1' and None`)
when the actual cause is that the test could not find a directory. **A path that is right in one
checkout and wrong in another is not a path.** The corpus is found by searching, and the answer is
the same wherever the code sits.

Resolution order, and the reason for it:

1. The environment, because an operator overriding the corpus must win.
2. Walking up from this file looking for a directory that *holds* `國考題資料夾`. This is the
   answer in both real layouts - the workspace (where the corpus is inside
   `tw-national-exam-catalog/`) and a standalone clone (where it is at the repository root).
3. `repo_root()/tw-national-exam-catalog/國考題資料夾`, because the corpus is not checked into git,
   so its absence is normal and a caller deserves the canonical path to report rather than `None`.

`repo_root()` finds the repository by looking for a well-known child (`catalogs/`) rather than by
counting, for the same reason: a repository is a thing with contents, not a thing at a depth.
"""
from __future__ import annotations

import os

#: The corpus directory's name. It is the thing being looked for, so it is the one string that has
#: to be right; everything else is derived.
CORPUS_DIR_NAME = "國考題資料夾"

#: A child of the catalog repository. Used to recognise the repository root without counting
#: directories, because a repository is identified by what is in it.
REPO_MARKER = "catalogs"

#: Environment override. An operator setting this is the authority, so it is consulted first.
ENV_VAR = "ASSET_ROOT"


def _candidates(start):
    """`start` and each of its parents, nearest first."""
    path = os.path.abspath(start)
    while True:
        yield path
        parent = os.path.dirname(path)
        if parent == path:
            return
        path = parent


def repo_root(start=None):
    """The catalog repository root: the nearest ancestor holding `catalogs/`.

    Falls back to the parent of this package (`qbr/` -> repository root) when no marker is found,
    which is the answer inside the merged layout. The fallback is a *last* resort, not the rule.
    """
    here = start or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in _candidates(here):
        if os.path.isdir(os.path.join(candidate, REPO_MARKER)):
            return candidate
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def workspace_root(start=None):
    """The `ai_learning_platform` workspace, when this checkout is inside one.

    Only the `platform-app/` default argument needs this, and that directory genuinely is *outside*
    the catalog repository - it is a sibling repository. So this is the one place where the answer
    cannot be derived from the catalog repository alone: in a standalone clone there is no
    workspace, and the honest answer is the repository root, which at least keeps the path inside
    the tree that was checked out rather than pointing at `/Users/tim/...`.
    """
    here = start or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in _candidates(here):
        if os.path.isdir(os.path.join(candidate, "platform-app")) \
                or os.path.isdir(os.path.join(candidate, "tw-national-exam-catalog")):
            return candidate
    return repo_root(here)


def asset_root(start=None, *, must_exist=False):
    """The folder that contains `10_official_pdf` - i.e. the corpus root.

    `must_exist=True` returns `None` when there is no corpus, which is the honest answer for a
    caller that has to distinguish "no corpus here" from "a corpus with nothing in it". The default
    returns the canonical expected path even when absent, so that an error message can name the
    place it looked instead of saying `None`.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        return os.path.abspath(os.path.expanduser(override))

    here = start or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in _candidates(here):
        # Two shapes, and both are real: the corpus inside `tw-national-exam-catalog/` (the
        # workspace layout) and directly at the repository root (a standalone checkout).
        for probe in (os.path.join(candidate, CORPUS_DIR_NAME),
                      os.path.join(candidate, "tw-national-exam-catalog", CORPUS_DIR_NAME)):
            if os.path.isdir(os.path.join(probe, "10_official_pdf")):
                return probe

    canonical = os.path.join(repo_root(here), CORPUS_DIR_NAME)
    if must_exist and not os.path.isdir(canonical):
        return None
    return canonical
