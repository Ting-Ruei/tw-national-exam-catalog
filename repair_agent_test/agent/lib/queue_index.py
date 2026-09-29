"""Byte-offset index for the queue's append-only JSONL files.

**Why this exists** (measured 2026-09-29 on this sandbox, laptop queue):

| call | before | what the time went into |
|---|---|---|
| `/api/question` | **0.86 s** | `ai_findings()` reading 708 MB **in text mode** |
| `/api/browse` (map) | **3.38 s** | one `prior_judgements()` file open **per candidate row** (79,090 opens) |

The cost is not the bytes: a byte-mode pass over `candidates.jsonl` + `question_ai_findings.jsonl`
(907 MB) takes **0.20 s** warm, while the same files read as decoded text cost 0.87 s per call —
decoding 6.5 KB lines into `str` is what the request actually paid for. So the accelerator is a
byte-offset index: scan once in bytes, keep `key -> [(offset, length)]`, then read only the lines
the request asked about.

**The index is never the authority.** A key that is not in it falls back to the full streaming
read, so a partial last line (a writer is mid-append), an unfamiliar field layout, or a file that
grew since the cache was written can only cost time, never change an answer. The cache is discarded
whenever the source's `size`, `mtime_ns` or `inode` differs, which is the whole validity rule —
`REPAIR_AGENT_NO_INDEX=1` disables the index entirely and is how the tests prove both paths return
the same rows.

Keys are read from a bounded prefix of each line (`PREFIX_BYTES`), not from the whole line: the real
files put `candidate_key` within the first 119–263 bytes, while a findings record carries a
multi-KB prompt later in the line. Lines whose key cannot be read that way are counted as
`unkeyed` and simply are not in the index — they are still reachable by the streaming fallback.
"""

from __future__ import annotations

import hashlib
import json
import os

# Keys sit within the first 263 bytes of every line in the live queue (measured: candidates 263,
# findings 119, review events 71). 1024 leaves room for a field added ahead of `candidate_key`
# without letting the search wander into a prompt body.
PREFIX_BYTES = 1024


def _key_of(raw: bytes, prefix: bytes, limit: int | None = PREFIX_BYTES) -> str | None:
    at = raw[:limit].find(prefix)
    if at < 0:
        return None
    i = at + len(prefix)
    while i < len(raw) and raw[i] in (0x20, 0x09):
        i += 1
    if i >= len(raw) or raw[i] != 0x22:  # not a quoted string: not the field we index
        return None
    end = raw.find(b'"', i + 1)
    if end < 0:
        return None
    return raw[i + 1:end].decode("utf-8", "replace")


class Index:
    """`key -> [(offset, length)]` for one JSONL file, in file order."""

    def __init__(self, path: str, offsets: dict, guard: dict, field: str,
                 total_lines: int, unkeyed: int, limit: int | None = PREFIX_BYTES) -> None:
        self.path = path
        self.offsets = offsets
        self.guard = guard
        self.field = field
        self.total_lines = total_lines
        self.unkeyed = unkeyed
        self.limit = limit

    def has(self, key: str) -> bool:
        return key in self.offsets

    def rows(self, key: str) -> list:
        """The parsed records for `key`, in the order the file holds them."""
        spans = self.offsets.get(key)
        if not spans:
            return []
        out = []
        with open(self.path, "rb") as handle:
            for offset, length in spans:
                handle.seek(offset)
                try:
                    out.append(json.loads(handle.read(length)))
                except ValueError:
                    continue
        return out


def guard_of(path: str) -> dict:
    stat = os.stat(path)
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino}


def build(path: str, field: str = "candidate_key",
          limit: int | None = PREFIX_BYTES) -> Index:
    """Scan `path` in bytes and index every line by `field`.

    `limit` bounds how much of each line is searched for the field name. `None` searches the whole
    line, which is what the paper index needs: `metadata.question_pdf_relative` sits as far as
    2,023 bytes in, and 880 of 79,090 lines carry it past any small fixed prefix. A JSON fragment
    quoted inside a string cannot be mistaken for the field (its quotes are escaped), so the wider
    search is safe; `unkeyed` still counts whatever it fails to read.
    """
    prefix = ('"%s":' % field).encode()
    offsets: dict[str, list] = {}
    total = 0
    unkeyed = 0
    with open(path, "rb") as handle:
        offset = 0
        for raw in handle:
            start = offset
            offset += len(raw)
            if not raw.strip():
                continue
            total += 1
            key = _key_of(raw, prefix, limit)
            if key is None:
                unkeyed += 1
                continue
            offsets.setdefault(key, []).append([start, len(raw)])
    return Index(path, offsets, guard_of(path), field, total, unkeyed, limit)


def cache_path(path: str, store_dir: str, field: str = "candidate_key",
               limit: int | None = PREFIX_BYTES) -> str:
    digest = hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:10]
    window = "all" if limit is None else str(limit)
    name = "%s.%s.%s.%s.json" % (os.path.basename(path), field, window, digest)
    return os.path.join(store_dir, "index", name)


def load_or_build(path: str, store_dir: str, field: str = "candidate_key",
                  limit: int | None = PREFIX_BYTES) -> Index:
    """The cached index for `path`, rebuilt when the source moved under it.

    A cache whose guard does not match the file's current `(size, mtime_ns, ino)` is **discarded and
    rebuilt** — a stale index would answer with a question that has since been rewritten, which is
    the one failure mode this design refuses to have.
    """
    cache = cache_path(path, store_dir, field, limit)
    guard = guard_of(path)
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as handle:
                payload = json.load(handle)
            if (payload.get("guard") == guard and payload.get("field") == field
                    and payload.get("limit", PREFIX_BYTES) == limit):
                return Index(path, payload["offsets"], guard, field,
                             payload.get("total_lines", 0), payload.get("unkeyed", 0), limit)
        except (ValueError, KeyError, OSError):
            pass  # unreadable cache is not an error: it is a rebuild
    index = build(path, field, limit)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    tmp = cache + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"guard": index.guard, "field": field, "limit": limit,
                   "total_lines": index.total_lines, "unkeyed": index.unkeyed,
                   "offsets": index.offsets}, handle, ensure_ascii=False)
    os.replace(tmp, cache)
    return index
