#!/usr/bin/env node
/**
 * Promote designer principles from the review stream into the rules house.
 *
 * **The stream is the inbox, the house is the authority.** New designer rules arrive as rows in
 * `question_review_principles.jsonl` (written by the review UI's 討論區); the agent *injects* only
 * what `rules/rules.json` carries. Between the two sits this importer — report-first, apply behind
 * an approval hash, because §9.11-6 rules it:「規則誕生需要設計者，執行不需要」. An unreviewed row
 * must never land in the prompt by itself, the same way an unreviewed candidate never lands in a
 * package.
 *
 * Usage:
 *   node rules/import_stream.mjs [--stream <jsonl>] [--house <json>]
 *     → dry-run: prints what would change and the SHA-256 of the would-be house file.
 *       Writes nothing. Exit 0.
 *   node rules/import_stream.mjs --approve <sha256>
 *     → applies only if the freshly computed house content hashes to exactly `sha256`
 *       (i.e. the operator approved what the dry-run measured, not something newer).
 *       Exit 2 on mismatch — re-run the dry-run.
 *
 * Import semantics (measured on the live stream 2026-09-30):
 *   - Rows are grouped by `text` (stripped). One house **entry per distinct text**.
 *   - Within a group the entry carries the **first curator row's** fields verbatim
 *     (reviewer `principle_curator`, then earliest `created_at`), its `principle_id` stays the row's
 *     `principle_id`, and repeated rows show up as `occurrences` + `other_row_stamps` — provenance is
 *     preserved, duplication is not.
 *   - Empty-text rows are **not imported** (a blank row is not a rule); they are reported, never
 *     silently dropped.
 *   - Fields absent on older stream rows import as `null` and the report names them.
 */

import { readFileSync, writeFileSync, mkdirSync, renameSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const RULES_DIR = HERE;
const DEFAULT_HOUSE = join(RULES_DIR, "rules.json");
const DEFAULT_CATALOG = join(HERE, "..", "..", "..");
const DEFAULT_STREAM = join(
  DEFAULT_CATALOG, "qbr", "data", "review-queues", "live", "review-ui",
  "question_review_principles.jsonl",
);

const argv = process.argv.slice(2);
function arg(flag) {
  const i = argv.indexOf(flag);
  return i >= 0 ? argv[i + 1] : undefined;
}
const approve = arg("--approve");
const housePath = arg("--house") || DEFAULT_HOUSE;
const streamPath = arg("--stream") || DEFAULT_STREAM;

/** Read the stream loosely on *shape* but strictly on *parse*: a torn line is reported, not swallowed. */
function readStream(path) {
  if (!existsSync(path)) throw new Error(`stream not found: ${path}`);
  const text = readFileSync(path, "utf8");
  const rows = [];
  const torn = [];
  for (const [index, line] of text.split("\n").entries()) {
    if (!line.trim()) continue;
    let parsed;
    try {
      parsed = JSON.parse(line);
    } catch {
      torn.push(index + 1);
      continue;
    }
    // A line that parses to `null`/a scalar is not a row; count it as torn rather than crash.
    if (!parsed || typeof parsed !== "object") {
      torn.push(index + 1);
      continue;
    }
    rows.push({ line_no: index + 1, row: parsed });
  }
  return { rows, torn };
}

const strip = (s) => String(s ?? "").trim();
const isCurator = (r) => r.reviewer === "principle_curator";

function buildEntries(streamRows) {
  const groups = new Map();
  for (const { line_no, row } of streamRows) {
    const text = strip(row.text);
    if (!text) continue; // blank rows are reported upstream, never imported
    if (!groups.has(text)) groups.set(text, []);
    groups.get(text).push({ line_no, row });
  }
  const entries = [];
  for (const [text, group] of groups) {
    // Curator rows are the provenance of record; among equals the earliest wins.
    const ordered = [...group].sort((a, b) =>
      (isCurator(b.row) ? 1 : 0) - (isCurator(a.row) ? 1 : 0) ||
      String(a.row.created_at).localeCompare(String(b.row.created_at)));
    const first = ordered[0].row;
    const evidence = [];
    const stamps = [];
    for (const { row } of ordered) {
      for (const ev of Array.isArray(row.evidence) ? row.evidence : row.evidence ? [row.evidence] : []) {
        if (!evidence.includes(ev)) evidence.push(ev);
      }
      if (row.created_at) stamps.push(String(row.created_at));
    }
    const missing = ["rationale", "source", "scope", "action"]
      .filter((field) => strip(first[field]) === "");
    entries.push({
      principle_id: String(first.principle_id),
      text,
      rationale: strip(first.rationale) || null,
      evidence,
      reviewer: first.reviewer || null,
      source: strip(first.source) || null,
      scope: strip(first.scope) || null,
      action: strip(first.action) || null,
      created_at: first.created_at || null,
      schema: first.schema || null,
      active: true,
      superseded_by: null,
      occurrences: ordered.length,
      other_row_stamps: stamps.slice(1),
      imported_missing_fields: missing.length ? missing : undefined,
    });
  }
  return entries.sort((a, b) => a.principle_id.localeCompare(b.principle_id, undefined, { numeric: true }));
}

function houseDocument(entries, streamMeta) {
  return {
    schema: "qbr_review_rules_v0.1",
    // The version tags **which stream state** was imported (short sha of the stream file), so the
    // document is byte-stable across dry-run → approve: the approval hash must cover exactly the
    // bytes being written. When the import itself happened lives in git, not in the file.
    version: `stream-import-${streamMeta.sha256.slice(0, 8)}`,
    imported_from: streamMeta,
    rules: entries,
  };
}

const sha256 = (s) => createHash("sha256").update(s).digest("hex");

/** Apply only what was approved: the house content must hash to exactly `--approve`'s value. */
function writeHouse(path, content) {
  const tmp = `${path}.importing`;
  writeFileSync(tmp, content, "utf8");
  renameSync(tmp, path);
}

const { rows, torn } = readStream(streamPath);
const entries = buildEntries(rows);
const emptyRows = rows.map((r) => r.row).filter((r) => !strip(r.text));
const existing = existsSync(housePath) ? JSON.parse(readFileSync(housePath, "utf8")) : null;
const existingTexts = new Set((existing?.rules ?? []).map((r) => r.text));

const newEntries = entries.filter((e) => !existingTexts.has(e.text));
const doc = houseDocument(
  newEntries,
  {
    file: streamPath,
    sha256: sha256(readFileSync(streamPath, "utf8")),
    row_count: rows.length,
    empty_rows: emptyRows.length,
    empty_row_ids: emptyRows.map((r) => r.principle_id).filter(Boolean),
    torn_lines: torn,
  },
);
const merged = existing ? { ...existing, rules: [...existing.rules, ...newEntries] } : doc;
const content = `${JSON.stringify(merged, null, 2)}\n`;
const hash = sha256(content);

const report = {
  stream: streamPath,
  stream_rows: rows.length,
  torn_lines: torn,
  distinct_texts: entries.length,
  empty_rows: emptyRows.length,
  already_in_house: entries.length - newEntries.length,
  to_import: newEntries.length,
  missing_fields: newEntries.filter((e) => e.imported_missing_fields).map((e) => ({
    principle_id: e.principle_id, fields: e.imported_missing_fields,
  })),
  house: housePath,
  content_sha256: hash,
};

console.log(JSON.stringify({ ...report, entries: newEntries.map((e) => ({
  principle_id: e.principle_id, occurrences: e.occurrences, text: e.text.slice(0, 44),
})) }, null, 2));

if (!approve) {
  console.log("\n[dry-run] nothing written. To apply: node rules/import_stream.mjs --approve " + hash);
  process.exit(0);
}
if (approve !== hash) {
  console.error(`[refuse] approved sha ${approve} != current house content ${hash}. Re-run the dry-run.`);
  process.exit(2);
}
mkdirSync(dirname(housePath), { recursive: true });
writeHouse(housePath, content);
console.log(`[written] ${housePath} (${content.length} bytes, sha ${hash})`);