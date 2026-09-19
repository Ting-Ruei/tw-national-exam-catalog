# -*- coding: utf-8 -*-
"""Reading a manifest row, and finding the file it names.

A manifest row stores its asset path **relative to the manifest's own directory**:

```
{"uid": "...", "role": "question", "raw_copy": "raw/營養師/104/第1次/1041_營養師_膳食療養學.pdf"}
```

This module exists because that was not always true, and the failure was silent. The sample
manifest was written with absolute paths pointing into `pi_test/question_bank_rebuild/`, which
worked exactly as long as the pipeline stayed in that sandbox. Moving the pipeline into
`tw-national-exam-catalog/qbr/` - the merge this module is part of - left every row naming a file
in the old tree: 3 of the 201 tests failed on `FileNotFoundError`, and the other 198 passed because
they happened not to read the manifest. A path that is correct only in the directory it was written
in is not a path; it is a coincidence.

Two rules, and both are governance rather than convenience:

* **Relative, always.** The charter forbids absolute paths in package assets, for the same reason:
  an artifact has to be findable after it is moved. `scripts/build_sample_set.py` now writes
  relative paths, and this module is how they are read back.
* **Resolved against the manifest, not against the process.** `os.getcwd()` is not part of the
  manifest and must not be part of the answer. A caller that runs from a different directory - the
  Review UI server, a batch driver, `pytest` from the repo root - gets the same file.
"""
from __future__ import annotations

import json
import os

#: The key a row uses for the file it points at. One key today; named so that a second one is a
#: change here rather than in every reader.
PATH_KEYS = ("raw_copy",)


def resolve(row, base):
    """One manifest row with every path key made absolute against `base`.

    A row that already carries an absolute path is returned unchanged rather than repaired. That is
    deliberate: a repair would hide a manifest that is still absolute, and a manifest that is
    absolute is the defect this function exists to remove. An absolute row should be rebuilt with
    `scripts/build_sample_set.py`, and until it is, it still works - so the wrong thing keeps
    working while being visibly wrong, rather than being silently rewritten into something that
    looks right.
    """
    out = dict(row)
    for key in PATH_KEYS:
        value = out.get(key)
        if isinstance(value, str) and value and not os.path.isabs(value):
            out[key] = os.path.normpath(os.path.join(base, value))
    return out


def load(path):
    """Every row of a manifest, with its paths resolved against the manifest's own directory.

    Returns `[]` for a manifest that is not there, because "the sample was not built yet" is a
    state a caller may legitimately be in - `build_sample_set.py` has to run before anything that
    reads a sample - and the callers distinguish it (`pytest.skip`). Raising here would make a
    missing optional fixture look like a broken manifest.
    """
    if not os.path.isfile(path):
        return []
    base = os.path.dirname(os.path.abspath(path))
    with open(path, encoding="utf-8") as handle:
        return [resolve(json.loads(line), base) for line in handle if line.strip()]


def of_package(package_root, name="sample_manifest.jsonl"):
    """The sample manifest that belongs to a package root, e.g. `<root>/data/sample_manifest.jsonl`."""
    return os.path.join(package_root, "data", name)
