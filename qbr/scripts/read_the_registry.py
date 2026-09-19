# -*- coding: utf-8 -*-
"""The registry the corpus ships with is the authority for which paper a key came from.

`Registry/asset_manifests/*.csv` carries, per asset, the columns

    status, year, exam_ordinal, exam_code, category_code, category_name,
    subject_code, subject_name, document_role, source_url, destination,
    bytes, sha256, registry_key

which is everything `three_way.resolve_pdf()` was previously made to infer out of the paper
code by heuristic — the year, the ordinal of the examination, the subject, the role of the
sheet, the very path of the file, and its digest besides. Run this to see how much of the
bank's own keys the registry can answer for, before the resolver is changed to ask it.
"""
import csv
import glob
import json
import os
import re
import sys

MANIFESTS = ("/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog"
             "/國考題資料夾/Registry/asset_manifests")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
sys.path.insert(0, os.path.join(HERE, "src"))

COLUMNS = ("status", "year", "exam_ordinal", "exam_code", "category_code", "category_name",
           "subject_code", "subject_name", "document_role", "source_url", "destination",
           "bytes", "sha256", "registry_key")

_MANIFEST_STAMP = re.compile(r"(\d{8}-\d{6})\.csv$")

# The manifests were written where the checkout stood then, and record an absolute
# `destination` under that home. The checkout has since moved (it is now under
# `AI workspace/`), so 943 of the 2,341 paths the registry names for the bank's own keys do not
# exist as they stand. The tree below `國考題資料夾/` is the tree the manifest describes, and that
# is what is kept: the recorded path is cut at the asset root and re-attached to the root this
# program is reading the manifests from. Both are carried on the record - `destination-as-recorded`
# for the audit, `destination` for the use - and a rebase that finds nothing is a finding in
# itself, reported by `rebased` rather than hidden.
_ASSET_ROOT = "國考題資料夾"
# The asset root is taken out of the manifests' own address rather than out of a number of
# directories walked up from here: the manifests live at
# `<checkout>/國考題資料夾/Registry/asset_manifests`, so the component they are two levels under
# is the root the recorded paths are to be re-attached to, whatever depth this sandbox sits at.
_parts = os.path.normpath(MANIFESTS).split(os.sep)
_ASSET_ROOT_DIR = os.sep.join(_parts[:_parts.index(_ASSET_ROOT) + 1]) if _ASSET_ROOT in _parts else ""
CORPUS_ROOT = os.path.join(_ASSET_ROOT_DIR, "10_official_pdf", "by_official_catalog") \
    if _ASSET_ROOT_DIR else ""


def rebase(destination):
    """A recorded path, attached again to the root this checkout is read from.

    Returns `(path, rebased)`. `path` is the original when it already exists, else the rebased
    one when that exists, else the original unchanged with `rebased` false - so a caller can
    tell a file that is not to be had from one that was merely relocated.
    """
    if not destination:
        return destination, False
    if os.path.isfile(destination):
        return destination, False
    marker = destination.rfind(_ASSET_ROOT + "/")
    if marker < 0:
        return destination, False
    tail = destination[marker + len(_ASSET_ROOT) + 1:]
    candidate = os.path.join(_ASSET_ROOT_DIR, tail)
    if os.path.isfile(candidate):
        return candidate, True
    return destination, False


def manifest_timestamp(path):
    """The moment a manifest was written, out of its own name (`…__y100-115__20260720-112804.csv`).

    The same key is carried by several manifests, the corpus having been downloaded and then
    re-downloaded, and a re-download renames and relocates. So the newest of them that can be
    found on the disk is the one to be asked; an older one may name a path that has since been
    moved, and 5,285 of the keys in the present set are named by more than one manifest.
    """
    match = _MANIFEST_STAMP.search(os.path.basename(path or ""))
    return match.group(1) if match else ""


def load_registry(patterns=(MANIFESTS + "/*.csv",), prefer_existing=True):
    """Every manifest row that names a moex asset, indexed by the key the bank stores.

    Read newest manifest first, and keep the first row of a key whose file is to be had on the
    disk: the registry is the authority for which paper a key came from, but it is only of use
    where the paper it names is where it says it is.
    """
    best = {}
    rows = 0
    conflicts = 0
    paths = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern))
    for path in sorted(paths, key=lambda name: (manifest_timestamp(name), name), reverse=True):
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            head = next(reader, None)
            if not head or "registry_key" not in head:
                continue
            position = dict(zip(head, range(len(head))))
            for record in reader:
                if len(record) != len(head):
                    continue
                key = record[position["registry_key"]]
                if not key.startswith("moex:"):
                    continue
                rows += 1
                entry = dict(zip(COLUMNS, record))
                entry["manifest"] = os.path.basename(path)
                entry["manifest-stamp"] = manifest_timestamp(path)
                entry["destination-as-recorded"] = entry["destination"]
                where, moved = rebase(entry["destination"])
                entry["destination"] = where
                entry["rebased"] = moved
                entry["on-disk"] = bool(where) and os.path.isfile(where)
                before = best.get(key)
                if before is None:
                    best[key] = entry
                    continue
                conflicts += 1
                if prefer_existing and before.get("on-disk") and not entry.get("on-disk"):
                    continue                       # the earlier reading already had the file
                best[key] = entry
    return best, rows, conflicts


def main(argv):
    import three_way as tw
    table, rows, conflicts = load_registry()
    print("manifest rows read: %d ; distinct keys: %d ; keys named by two files: %d"
          % (rows, len(table), conflicts))

    def role_counts(field):
        out = {}
        for entry in table.values():
            out[entry[field]] = out.get(entry[field], 0) + 1
        return out
    print("by document_role:", json.dumps(role_counts("document_role"), ensure_ascii=False))
    print("by status       :", json.dumps(role_counts("status"), ensure_ascii=False))
    on_disk = sum(1 for entry in table.values()
                  if entry["destination"] and os.path.isfile(entry["destination"]))
    print("files actually on the disk: %d / %d" % (on_disk, len(table)))

    ids = [int(row["id"]) for row in tw.load_csv_rows() if str(row.get("id")).isdigit()]
    keys = [row.get("source_registry_key") for row in tw.load_csv_rows()
            if str(row.get("id")).isdigit()]
    wanted = set(k for k in keys if k)
    answered = [k for k in wanted if k in table]
    print("\nthe bank's candidates name %d distinct registry keys; the manifests answer for %d "
          "(%.1f%%)" % (len(wanted), len(answered), 100.0 * len(answered) / max(1, len(wanted))))
    unanswered = [k for k in wanted if k not in table]
    print("unanswered, samples:", json.dumps(unanswered[:5], ensure_ascii=False))
    if "--verify" in argv:
        import hashlib
        checked = matched = differed = 0
        for key, entry in list(table.items())[:120]:
            path = entry["destination"]
            if not os.path.isfile(path):
                continue
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 16), b""):
                    digest.update(block)
            checked += 1
            if digest.hexdigest() == entry["sha256"]:
                matched += 1
            else:
                differed += 1
        print("digests checked %d : matched %d : differed %d" % (checked, matched, differed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
