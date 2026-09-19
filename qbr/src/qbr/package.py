# -*- coding: utf-8 -*-
"""S5 —— 條目成包。canonical records become a platform package.

「出」之大義：此檔只管「把已讀懂的一卷，寫成平台契約所要求的封裝」，不讀網路、
不讀機器、不讀資料庫。凡路徑皆由外傳入，故換機器、換主機、換目錄，此檔不改一字。

Nothing in this module reads the machine. Every path arrives as an argument, and no address
of a host, a service or a port is written into any artifact — that property is what the
golden path exists to demonstrate, and it is checked rather than asserted.

The two rules this module obeys, both learned elsewhere in the sandbox:

* the text written into a package is the text **as extracted** (`repair.segment_mixed`'s
  items), never `canon.fold`'s image of it. `fold` normalises NFKC, which is right for
  comparison and wrong for delivery: a package that carried folded text would deliver
  half-width brackets and rewritten glyphs to students. Comparison folds; delivery does not.
* an answer comes only from the official sheet, injected by the caller. This module never
  reads an answer out of a question.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

EXTERNAL_SOURCE = "tw-national-exam-catalog"
EXTERNAL_SCHEMA_VERSION = "postgres-formal-v1"
PACKAGE_SCHEMA_VERSION = "question-bank-data-package/2026.07"
ADAPTER_VERSION = "qbr_golden_path_exporter_v0.1"

_ASSET_ROOT = "國考題資料夾"

# The platform's own vocabulary. Anything outside this set is refused by
# `platform-app/scripts/import_question_bank_package.py`, so it is declared here rather than
# discovered at import time.
SUPPORTED_QUESTION_TYPES = ("single_choice", "single", "multiple", "truefalse", "group")

# A record that has passed the deterministic gates but has met no human reviewer. It is
# written down as such: the package says exactly how far its own claims have been verified,
# and a reader of the manifest does not have to infer it from a date.
REVIEW_STATUS_MACHINE_ONLY = "machine_verified_pending_human"


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_asset(path):
    """`/anything/國考題資料夾/x/y.pdf` -> `國考題資料夾/x/y.pdf`, or None.

    An absolute path must never reach a package: the platform's validator rejects `/Users/`,
    `/Volumes/`, `file://` and drive letters outright. The tree below the asset root is the
    only part of a path that carries meaning to a reader of the package anyway.
    """
    if not path:
        return None
    normalised = os.path.normpath(str(path))
    marker = normalised.rfind(_ASSET_ROOT + os.sep)
    if marker < 0:
        return None
    return normalised[marker:].replace(os.sep, "/")


#: The role suffixes a catalog `registry_key` may already carry. The catalog writes the paper key
#: as `moex:115090:308:0504:1` and the sheets as `...:question` / `...:answer`, and callers pass
#: whichever one they have - `golden_path.py --registry-key` is documented with both spellings.
ROLE_SUFFIXES = (":question", ":answer", ":correction")


def paper_key(registry_key):
    """The paper a registry key names, with any role suffix removed.

    This exists because the role is a property of the *sheet*, not of the paper, and every place that
    appended one to a key that already had one produced a key in no table and in no manifest:
    `moex:115090:308:0504:1:question:question`, and an answer key of
    `moex:115090:308:0504:1:answer:answer`. Those keys are what the Review UI files a human decision
    under, so the damage was not a cosmetic naming problem - it is the identity a reviewer's work is
    recorded against, and a doubled suffix would have filed every decision under a question that no
    later rebuild could find.

    Measured when the pipeline was merged into `tw-national-exam-catalog/qbr/`: passing the key as
    the catalog spells it produced 80 doubled `candidate_key`s and 80 doubled
    `source_registry_key`s against the pinned golden run, plus a doubled answer key.
    """
    text = str(registry_key or "")
    for suffix in ROLE_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def question_key(registry_key, number):
    """`moex:115090:308:0504:1` + 7 -> `moex:115090:308:0504:1:question:q007`.

    The shape is the one the existing reviewed packages already use, so that a package built
    here can be compared key for key against one built from the formal database.

    A key that already carries a role is normalised first, so `...:question` and `...` produce the
    same question key. That is the whole point: a question has one identity however the caller
    happened to name the paper it came from.
    """
    return "%s:question:q%03d" % (paper_key(registry_key), int(number))


def content_hash(stem, options, answer):
    """Stable hash of what a question *is*, for duplicate detection across packages.

    Whitespace is taken out entirely rather than collapsed. Collapsing keeps one space where
    there were three and drops the rest, so `下列 那一個` and `下列那一個` hash apart even though
    they are the same question — and in this corpus a space between two ideographs is a line
    break, not a word boundary. What is left is the characters, which is what "the same
    question" means.
    """
    def only_characters(value):
        return "".join((value or "").split())

    payload = {
        "stem": only_characters(stem),
        "options": [{"key": key, "text": only_characters(options[key])}
                    for key in sorted(options or {})],
        "answer": sorted(answer or []),
    }
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _option_objs(options, order=None):
    keys = [k for k in (order or []) if k in (options or {})] or sorted(options or {})
    return [{"key": key, "text": options[key], "image": None,
             "explanation": None, "explanation_image": None} for key in keys]


def build_question(item, *, meta, answer, registry_key, answer_source, flags,
                   review_status=None, extra_metadata=None, answer_text=None):
    """One canonical record -> one platform question row.

    `item` is an item straight out of `repair.segment_mixed`: its text is the text of the
    paper, unabridged. `answer` is the official key (or None when no sheet speaks), and
    `answer_source` names the sheet it came from, so a reader can trace every key back.

    `answer_text` is the answer as a human would write it, and is used only when it says
    something `answer` cannot. A voided question is the case that matters: its labels are
    every option, because every option is accepted, and printing `ABCD` as the answer would
    tell a reader the opposite of what the corrections sheet said. Where `answer_text` is
    given the record carries it, and the labels stay as they are for comparison.
    """
    number = int(item["number"])
    stem = item.get("stem") or ""
    options = item.get("options") or {}
    labels = list(answer or [])
    metadata = {
        "adapter_version": ADAPTER_VERSION,
        "answer_authority_source": answer_source,
        "canonical_subject_name": meta["subject_name"],
        "category_code": meta.get("category_code"),
        "exam_code": meta.get("exam_code"),
        "exam_ordinal": str(meta.get("exam_number")),
        "external_question_key": question_key(registry_key, number),
        "external_registry_key": registry_key + ":question",
        "external_schema_version": EXTERNAL_SCHEMA_VERSION,
        "external_source": EXTERNAL_SOURCE,
        "parser_version": ADAPTER_VERSION,
        "question_pdf_relative": relative_asset(meta.get("question_pdf")),
        "answer_pdf_relative": relative_asset(meta.get("answer_pdf")),
        "corrected_answer_pdf_relative": relative_asset(meta.get("corrected_pdf")),
        "question_set": str(meta.get("question_set") or 1),
        "review_status": review_status or REVIEW_STATUS_MACHINE_ONLY,
        "source_content_hash": content_hash(stem, options, labels),
        "subject_code": meta.get("subject_code"),
        "subject_mapping_note": meta.get("subject_mapping_note"),
        "year": str(meta["year"]),
    }
    if flags:
        metadata["deterministic_flags"] = sorted(flags)
    if answer_text:
        metadata["answer_display"] = answer_text
    if extra_metadata:
        metadata.update(extra_metadata)
    return {
        "answer": labels,
        "exam_number": int(meta["exam_number"]),
        "explanation": None,
        "external_schema_version": EXTERNAL_SCHEMA_VERSION,
        "external_source": EXTERNAL_SOURCE,
        "feature_tags": [],
        "group_ref": None,
        "metadata": metadata,
        "normalized_category_name": meta["category_name"],
        "normalized_subject_name": meta["subject_name"],
        "official_category_name": meta["category_name"],
        "official_subject_name": meta["subject_name"],
        "options": _option_objs(options, item.get("option_order")),
        "package_version": None,          # filled in by build_package
        "question_number": number,
        "question_type": "single_choice",
        "roc_year": int(meta["year"]),
        "source_question_key": question_key(registry_key, number),
        "source_registry_key": paper_key(registry_key) + ":question",
        "stem": stem,
        "stem_image": None,
        "visual_profile": {
            "has_visual_asset": False,
            "visual_review_status": None,
            "asset_roles": [],
            "asset_quality_statuses": [],
            "asset_count": 0,
            "feature_tags": [],
        },
    }


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _file_entry(path, package_dir):
    return {"bytes": path.stat().st_size,
            "path": str(path.relative_to(package_dir)).replace(os.sep, "/"),
            "sha256": sha256_file(path)}


def build_package(questions, *, meta, registry_key, package_version, output_dir, provenance=None,
                  review_status=None):
    """Write one package and return its manifest.

    The manifest states the counts it wrote, the hash of every file, and — under
    `provenance` — how far the contents have actually been verified. It does not claim
    human review, and it does not claim to be the formal database's export.
    """
    output_dir = os.path.abspath(str(output_dir))
    os.makedirs(output_dir, exist_ok=True)
    package_dir = __import__("pathlib").Path(output_dir)

    rows = []
    for question in sorted(questions, key=lambda q: q["question_number"]):
        question = dict(question, package_version=package_version)
        rows.append(question)
    if not rows:
        raise ValueError("refusing to write an empty package")

    keys = [row["source_question_key"] for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate source_question_key in package")

    subjects = sorted({row["normalized_subject_name"] for row in rows})
    subjects_doc = {
        "canonical_subject_count": len(subjects),
        "canonical_subjects": subjects,
        "category": meta["category_name"],
        "external_schema_version": EXTERNAL_SCHEMA_VERSION,
        "external_source": EXTERNAL_SOURCE,
        "mapping_policy": {
            "official_name_policy": ("official_subject_name and normalized_subject_name are "
                                     "preserved as source lineage."),
        },
        "normalized_subject_count": len(subjects),
        "package_version": package_version,
        "subjects": [{"canonical_subject_name": name,
                      "normalized_subject_name": name,
                      "official_subject_name": name,
                      "subject_mapping_note": meta.get("subject_mapping_note")
                      or ("官方科目名稱保留為 source lineage；本包未經科目重編碼，"
                          "canonical 與 normalized 同名。")} for name in subjects],
    }

    questions_path = package_dir / "questions.jsonl"
    subjects_path = package_dir / "subjects.json"
    groups_path = package_dir / "groups.jsonl"
    assets_path = package_dir / "asset_manifest.jsonl"
    _write_jsonl(questions_path, rows)
    _write_json(subjects_path, subjects_doc)
    _write_jsonl(groups_path, [])
    _write_jsonl(assets_path, [])

    manifest = {
        "adapter_version": ADAPTER_VERSION,
        "category": {"normalized_category_name": meta["category_name"],
                     "slug": meta.get("slug") or "unset"},
        "counts": {
            "asset_files": 0,
            "asset_references": 0,
            "groups": 0,
            "questions": len(rows),
            "subjects": len(subjects),
            "warnings": 0,
        },
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "external_schema_version": EXTERNAL_SCHEMA_VERSION,
        "external_source": EXTERNAL_SOURCE,
        "files": {
            "asset_manifest": _file_entry(assets_path, package_dir),
            "groups": _file_entry(groups_path, package_dir),
            "questions": _file_entry(questions_path, package_dir),
            "subjects": _file_entry(subjects_path, package_dir),
            "warnings": None,
        },
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "package_version": package_version,
        "provenance": dict(provenance or {}, registry_key=registry_key,
                           review_status=review_status or REVIEW_STATUS_MACHINE_ONLY),
    }
    _write_json(package_dir / "manifest.json", manifest)
    return manifest
