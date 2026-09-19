# -*- coding: utf-8 -*-
"""Find and read the answer and corrections sheets for one paper.

Two things live here that were first written inside the golden-path script and had to be
lifted out once a second caller appeared: locating the sheets, and reading them.

**Locating.** The corpus ships registry manifests that name every file, and they are the
authority on which paper a registry key came from. They are not complete. The Examination
Yuan publishes corrections *after* the results, so a question paper and its answer sheet are
in a manifest built months earlier while the corrections sheet that arrived last week is not.
Measured on `moex:115090:308:0504:1`: the manifest resolved `question` and `answer` and knew
nothing of `correction`. The directory is therefore always walked for the roles the manifest
did not supply, and what the manifest did supply is never overridden.

**Reading.** The two engines measure different physical quantities and each is used for the
table it measures well. Poppler reports where each cell sits, so it keeps the ten points of
white space between one answer and the next and `答案 Ｄ Ｂ Ｃ` arrives as five cells that zip
positionally with the question numbers. PyMuPDF reports how large each glyph is, and reads
the same row as the single string `答案ＢＤＣＣＢ…` with nothing left to split on. Measured on
115090: poppler yields all 80 answers, PyMuPDF yields four, and the gate then blocks the run
with `answer-not-on-sheet` - a correct complaint about the wrong reading.
"""
from __future__ import annotations

import os

from . import canon, extract, repair

_ROLES = {"": "question", "_ANS": "answer", "_MOD": "corrected"}


def sheet_paths(asset_root, *, year, category, ordinal, subject, registry_key,
                registry_reader=None):
    """Locate Q / ANS / MOD for one paper. Returns (paths_by_role, how).

    `how` is not decoration. A path that was looked up in the manifest and a path that was
    guessed from a directory listing are different kinds of fact, and a resolution that was
    guessed at must never read like one that was looked up.
    """
    found, how = {}, None

    # The catalog's `registry_key` may already carry a role (`moex:115090:308:0504:1:question`), and
    # the manifest table is keyed by `<paper key>:<role>`. Appending a role to a key that already
    # had one produced `...:question:question`, which is in no table - so the manifest was consulted,
    # found nothing, and the paper fell through to the directory guess. Passing the key as the
    # catalog spells it is the natural thing to do, and it used to be the thing that silently
    # disabled the registry lookup; the role suffix is a *property of the caller's key*, not part of
    # the paper's identity.
    base_key = str(registry_key or "")
    for role in ("question", "answer", "correction"):
        if base_key.endswith(":" + role):
            base_key = base_key[: -len(role) - 1]
            break

    reader = registry_reader
    if reader is None:
        try:
            from read_the_registry import load_registry as reader  # type: ignore
        except Exception:                                    # noqa: BLE001
            reader = None
    if reader is not None:
        try:
            table, _rows, _conflicts = reader()
            for role in ("question", "answer", "correction"):
                if not base_key:
                    break
                entry = table.get("%s:%s" % (base_key, role))
                if not entry:
                    continue
                destination = entry.get("destination") or ""
                if destination and os.path.isfile(destination):
                    found["corrected" if role == "correction" else role] = destination
            if found:
                how = "registry-manifest"
        except Exception:                                    # noqa: BLE001 - no registry is a datum
            pass

    # The directory is the *corpus's* spelling of the category, and the catalog's spelling may
    # differ: the catalog says `藥師（一）` and the directory on disk is `藥師(一)`. Both are tried,
    # because only one of them exists and the corpus is the authority on its own layout.
    root = os.path.join(asset_root, "10_official_pdf", "by_official_catalog")
    directory = next((path for path in
                      (os.path.join(root, name, str(year), "第%d次" % ordinal)
                       for name in (category, _swap_brackets(category)))
                      if category and os.path.isdir(path)), None)
    # The prefix is built from every spelling of the subject and every spelling of the category,
    # because the catalog and the corpus disagree on both and neither is authoritative about the
    # other's file names. Measured: the catalog names `藥劑學（包括生物藥劑學）` with full-width
    # brackets while the file is `藥劑學(包括生物藥劑學).pdf` with half-width ones, and the catalog
    # names the category `藥師（一）` while the directory is `藥師(一)`. A prefix built from the
    # catalog alone matched no file at all, and S0 refused the paper with "no question sheet" even
    # though the sheet was sitting right there - 13 papers of 藥師(一) and 10 of 藥師(二).
    #
    # A prefix that matches nothing is not an error here: the lookup falls through to the
    # `not-found` answer, which the caller reports.
    categories = {category, _swap_brackets(category)}
    subjects = {subject, _swap_brackets(subject)}
    prefixes = sorted({"%d%d_%s_%s" % (year, ordinal, cat, sub)
                       for cat in categories if cat for sub in subjects if sub},
                      key=len, reverse=True)
    if directory:
        for name in sorted(os.listdir(directory)):
            stem, extension = os.path.splitext(name)
            if extension.lower() != ".pdf":
                continue
            prefix = next((candidate for candidate in prefixes if stem.startswith(candidate)), None)
            if prefix is None:
                continue
            role = _ROLES.get(stem[len(prefix):])
            if role and role not in found:
                found[role] = os.path.join(directory, name)
                if how:
                    how = "registry-manifest+directory"

    # The two sources are complementary, not alternatives, and the registry is consulted **first**
    # rather than **instead of** the directory. `role not in found` above is what keeps the
    # manifest's answer from being overwritten by the directory's - but it left the reverse hole:
    # a manifest row for the question and answer sheets with **no** row for the correction sheet
    # meant the correction was never looked for, and a partial hit of two sheets silently disabled
    # the directory for the third. Measured on `1152_醫事檢驗師_生物化學與臨床生化學`: Q10 and Q41
    # are 送分 in the 更正答案, the golden run read them as `A` and `D`, and the registry manifest for
    # that paper holds only `:question` and `:answer` - so the correction was there on disk the whole
    # time and unreachable. Two of 80 answers were wrong, and they were wrong in the direction that
    # matters: a 送分 (everyone scores) published as a single letter marks every other answer wrong.
    #
    # The directory pass therefore runs a second time, for the roles the registry did not answer, and
    # only for those: a directory file must never displace a manifest one, because the manifest is the
    # registry and the directory is a name that happens to match.
    if directory and any(role not in found for role in ("corrected",)):
        for name in sorted(os.listdir(directory)):
            stem, extension = os.path.splitext(name)
            if extension.lower() != ".pdf":
                continue
            prefix = next((candidate for candidate in prefixes if stem.startswith(candidate)), None)
            if prefix is None:
                continue
            role = _ROLES.get(stem[len(prefix):])
            if role == "corrected" and role not in found:
                found[role] = os.path.join(directory, name)
                how = (how or "inferred") + "+directory-correction"

    if "question" in found and ("answer" in found or "corrected" in found):
        return found, how
    if not directory:
        return found, how or "not-found"
    return found, (how or "inferred-from-directory-name")


def _swap_brackets(text):
    """The same name with its full-width brackets made half-width, and the reverse.

    Only the brackets: `及` against `與` is a difference in wording rather than in encoding, and
    two names that differ that way are two names for one subject but not two spellings of one
    file name. Only one of them can be on disk.
    """
    if not text:
        return text
    return (text.replace("（", "(").replace("）", ")") if "（" in text or "）" in text
            else text.replace("(", "（").replace(")", "）"))


def read_table_text(path, *, role="answer"):
    """Read an answer sheet with the engine that keeps the grid's gaps.

    A corrections sheet (`role="corrected"`) is read the same way but means something else,
    and the caller must not treat its rows as a table. `備註：第10題一律給分` is not an answer row;
    `parse_answer_table` finds no `答案` row in it at all when the sheet is a pure correction
    notice, which is the honest outcome rather than a parse.
    """
    text_b = repair.text_from_rows(repair.mask_chrome(extract.extract_lines_b(path))[0])
    if role == "corrected":
        return text_b
    if len(canon.parse_answer_table(text_b)) >= 2:
        return text_b
    # poppler is not always installed. Falling back keeps the run alive and keeps the shortfall
    # visible in the caller's count of known answers, rather than silently emptying the table.
    return repair.text_from_rows(extract.extract_lines_a(path))


def _text_of(entry):
    """The path, whether the role was stored as a path or as a dict with a `path` key.

    The golden path's registry resolver hands back dictionaries; the directory walk hands
    back plain strings. Both are the same fact, and normalising here keeps every caller from
    having to know which resolver answered.
    """
    if isinstance(entry, dict):
        return entry.get("path")
    return entry


def sheet_texts(sheets):
    """(answer_texts, correction_texts) for whatever roles were found."""
    answer_texts, correction_texts = [], []
    for role in ("answer", "corrected"):
        if role not in sheets:
            continue
        path = _text_of(sheets[role])
        if not path:
            continue
        text = read_table_text(path, role=role)
        (correction_texts if role == "corrected" else answer_texts).append(text)
    return answer_texts, correction_texts


def authoritative_for(items, sheets):
    """The official answer for every question, with corrections applied.

    Returns `(labels_by_number, corrections_by_number)`. `labels_by_number` is what a
    comparison should use; `corrections_by_number` carries the correction objects so a caller
    can also say *why* an answer looks the way it does.
    """
    from . import corrections
    answer_texts, correction_texts = sheet_texts(sheets)
    options_by_number = {int(item["number"]): list((item.get("options") or {}).keys())
                         for item in items}
    return corrections.authoritative_answers(answer_texts, correction_texts,
                                             options_by_number=options_by_number)
