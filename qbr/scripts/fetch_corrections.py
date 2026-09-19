# -*- coding: utf-8 -*-
"""Fetch corrections sheets (MOD) for a category, including ones the catalog predates.

The catalog's `has_correction` column was right when it was built and is not right now. The
Examination Yuan publishes corrections *after* the results, so a paper and its answer sheet
are catalogued months before the note that changes two of its answers exists. Measured on
115090 (115年第二次, 醫事檢驗師, 生物化學與臨床生化學): the catalog says `has_correction = no`,
the file was not on disk, and the endpoint served a valid corrections PDF that same day.

So the catalog is used as a *hint*, never as the authority, and the authority is the endpoint.
A correction is fetched only when the server actually has one - which is decided by looking at
what came back, not by what the URL looked like. A URL that answers with an HTML error page is
a 200 by the time it reaches here, so the check is on the bytes: a PDF begins `%PDF`.

Nothing is overwritten. A file that already exists is left alone unless `--force` says
otherwise, because the digest of a downloaded official document is the thing that makes every
later comparison mean something.
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)                      # .../tw-national-exam-catalog/qbr
sys.path.insert(0, os.path.join(PKG, "src"))

from qbr import paths  # noqa: E402

# Found, not counted to. `dirname(PKG)/../..` named `ai_learning_platform` only while this lived
# under the workspace; a standalone clone is shallower and the whole path left the repository.
PROJECT = paths.workspace_root()
CATALOG = os.path.join(paths.repo_root(), "catalogs",
                       "moex_subject_catalog__y100-115.csv")
ASSET_ROOT = paths.asset_root()
OUTPUT_ROOT = os.path.join(ASSET_ROOT, "10_official_pdf", "by_official_catalog")

UA = {"User-Agent": "Mozilla/5.0 (compatible; tw-national-exam-catalog/0.1)"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def catalog_rows(category=None, years=None):
    """The catalog, filtered. Only rows that name a correction URL are returned."""
    rows = []
    with open(CATALOG, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if category and row.get("category_name") != category:
                continue
            if years and int(row["year"]) not in years:
                continue
            if not row.get("correction_url"):
                continue
            rows.append(row)
    return rows


def destination_for(row):
    """Where the corrections sheet belongs, following the corpus's own naming.

    The name comes from the *question paper already on disk*, not from the catalog. The two
    disagree in ways that are invisible until a file has been written twice: the catalog
    writes `微生物學及臨床微生物學` where the corpus writes `微生物學與臨床微生物學`, and it writes
    full-width `（包括寄生蟲學）` where the corpus writes half-width `(包括寄生蟲學)`. Both are
    correct spellings of the same subject, and neither is a rename worth performing on
    twenty thousand existing files.

    The match is made on the NFKC form of the directory contents, which is what brings the
    two spellings of the parentheses together - the same normalisation the rest of the
    pipeline uses for comparison. Measured: without it, 51 downloads created 16 duplicate
    files under names nothing else in the corpus used.
    """
    base = os.path.normpath(os.path.join(OUTPUT_ROOT, row["category_name"], row["year"],
                                         "第%s次" % row["question_set"]))
    prefix = "%s%s_%s_%s" % (row["year"], row["question_set"], row["category_name"],
                              row["subject_name"])
    wanted = unicodedata.normalize("NFKC", prefix)
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            stem = os.path.splitext(name)[0]
            if stem.endswith("_ANS"):
                stem = stem[: -len("_ANS")]
            elif stem.endswith("_MOD"):
                stem = stem[: -len("_MOD")]
            if unicodedata.normalize("NFKC", stem) == wanted:
                return os.path.join(base, stem + "_MOD.pdf")
    return os.path.join(base, prefix + "_MOD.pdf")


def existing_sibling(path):
    """An already-downloaded file of the same paper, so the name can be matched to it."""
    base = path[:-len("_MOD.pdf")]
    for suffix in (".pdf", "_ANS.pdf"):
        if os.path.isfile(base + suffix):
            return base + suffix
    return None


def fetch(url, timeout=60):
    """Return (bytes, note). A non-PDF answer is reported as a note, not raised."""
    request = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_CTX) as response:
            data = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return None, "request-failed:%s" % exc
    if data[:4] != b"%PDF":
        # The endpoint answers with an HTML error page and status 200 when it has no file,
        # so this is the only reliable signal that there is nothing to fetch.
        return None, "not-a-pdf:%d-bytes" % len(data)
    return data, "pdf:%d-bytes" % len(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download corrections sheets for a category.")
    parser.add_argument("--category", required=True)
    parser.add_argument("--years", nargs="*", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="re-download what is already there")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = catalog_rows(args.category, args.years)
    if not rows:
        print("no catalog rows with a correction URL for %s" % args.category, file=sys.stderr)
        raise SystemExit(2)

    counts = collections.Counter()
    for row in rows:
        path = destination_for(row)
        if os.path.isfile(path) and not args.force:
            counts["already-present"] += 1
            continue
        if args.dry_run:
            print("  would fetch %s" % os.path.basename(path))
            counts["would-fetch"] += 1
            continue
        data, note = fetch(row["correction_url"])
        if data is None:
            counts["no-correction:%s" % note.split(":")[0]] += 1
            print("  --  %-58s %s" % (os.path.basename(path)[:58], note))
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        counts["downloaded"] += 1
        print("  ok  %-58s %s" % (os.path.basename(path)[:58], note))

    print("\n%s: %s" % (args.category, dict(counts)))


if __name__ == "__main__":
    main()
