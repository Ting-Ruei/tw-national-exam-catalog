/**
 * The rules house: the single authority for the designer's standing rules.
 *
 * Before 2026-09-30 the agent injected whatever `question_review_principles.jsonl` carried —
 * a stream that also contained 13 empty rows, one text written twice, and no way to retire a
 * rule that had stopped earning its place. The designer ruled otherwise (§9.11-6/7/8):
 * rules are **entries** (`text` + provenance + measured hits + lineage), injected under a
 * **budget** with the recently-hit ones first, compressed periodically by an **approved** pass,
 * and read by two consumers (the review doer here, the producer's repair line later).
 *
 * Division of labour:
 *   - `rules/rules.json`  — tracked, hand-editable, the one place a rule is defined.
 *     Written only through an approved gate (`rules/import_stream.mjs --approve <sha>` today;
 *     the compression pass reports, the designer approves, a later batch applies).
 *   - the hits stream      — appended facts (agent-operational evidence, not human review events):
 *     one line per recorded hit, `schema: rule_hit_v0.1`.
 *   - this module          — reads, validates, orders, measures. It *never* edits the house.
 *
 * Failure discipline: the house is **tracked code-adjacent memory**, so a broken house must
 * stop the run loudly (throw), not shorten a prompt silently. That is the exact inverse of the
 * old stream read — and the inverse on purpose: the stream tolerated drift because it was a
 * memory *input*; the house is the authority, and an unreadable authority is not "zero rules".
 *
 * Paths resolve **at call time** through env seams — the same convention `identity.mjs` uses for
 * `REPAIR_AGENT_STORE` — so tests point them at fixtures and never at the tracked house:
 *   - `REPAIR_AGENT_RULES`   — house path (default: `<agent>/rules/rules.json`)
 *   - `REPAIR_AGENT_HITS`    — hits stream (default: `<store>/rule_hits.jsonl`, `<store>` =
 *     `REPAIR_AGENT_STORE` or `<agent>/store`)
 *   - `REPAIR_AGENT_RULE_BUDGET` — injection cap; 0/absent = unlimited (every active rule injects,
 *     ordered by recent hits anyway).
 */

import { existsSync, mkdirSync, readFileSync, appendFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = join(HERE, "..");

/** The house of record. Resolved per call so tests and a redirected run can aim elsewhere. */
export function rulesPath() {
  return process.env.REPAIR_AGENT_RULES || join(AGENT_DIR, "rules", "rules.json");
}

/** Where hit facts append. Resolved per call; the store itself is `identity`'s convention. */
export function hitsPath() {
  return (
    process.env.REPAIR_AGENT_HITS ||
    join(process.env.REPAIR_AGENT_STORE || join(AGENT_DIR, "store"), "rule_hits.jsonl")
  );
}

function budgetFromEnv() {
  const raw = process.env.REPAIR_AGENT_RULE_BUDGET;
  if (raw === undefined || raw === "") return 0;
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`REPAIR_AGENT_RULE_BUDGET must be a non-negative integer, got ${JSON.stringify(raw)}`);
  }
  return value;
}

/**
 * Load and validate the house. Throws on anything suspicious — a rules file with a dead entry is
 * an emergency, not a decoration.
 */
export function loadRules(path = rulesPath()) {
  if (!existsSync(path)) {
    throw new Error(`rules house missing: ${path} — run rules/import_stream.mjs to build it`);
  }
  let doc;
  try {
    doc = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    throw new Error(`rules house unreadable (${path}): ${error.message}`);
  }
  if (doc?.schema !== "qbr_review_rules_v0.1") {
    throw new Error(`rules house ${path} has schema ${JSON.stringify(doc?.schema)}; expected "qbr_review_rules_v0.1"`);
  }
  if (!Array.isArray(doc.rules)) {
    throw new Error(`rules house ${path} has no rules array`);
  }
  const seen = new Set();
  for (const rule of doc.rules) {
    if (!rule.principle_id || typeof rule.principle_id !== "string") {
      throw new Error(`rules house ${path}: entry without a string principle_id: ${JSON.stringify(rule).slice(0, 120)}`);
    }
    if (!rule.text || typeof rule.text !== "string") {
      throw new Error(`rules house ${path}: ${rule.principle_id} has no rule text`);
    }
    if (typeof rule.active !== "boolean") {
      throw new Error(`rules house ${path}: ${rule.principle_id} has no boolean active`);
    }
    if (rule.superseded_by !== null && rule.superseded_by !== undefined && typeof rule.superseded_by !== "string") {
      throw new Error(`rules house ${path}: ${rule.principle_id} superseded_by must be a string id or null`);
    }
    if (seen.has(rule.principle_id)) {
      throw new Error(`rules house ${path}: duplicate principle_id ${rule.principle_id}`);
    }
    seen.add(rule.principle_id);
  }
  return doc;
}

/** Rules that still inject: active and not superseded, in file order. */
export function activeRules(path = rulesPath()) {
  return loadRules(path).rules.filter((r) => r.active && !r.superseded_by);
}

/**
 * Read the hits stream. Tolerant the way a *log* may be: a missing file is "no hits yet", not an
 * error; torn lines are returned so measurement can name them instead of swallowing them.
 */
export function readHits(path = hitsPath()) {
  if (!existsSync(path)) return { hits: [], torn: [] };
  const hits = [];
  const torn = [];
  for (const [index, line] of readFileSync(path, "utf8").split("\n").entries()) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (row && typeof row === "object" && row.rule_id) hits.push(row);
      else torn.push(index + 1);
    } catch {
      torn.push(index + 1);
    }
  }
  return { hits, torn };
}

/** rule_id → { count, last, keys } ; keys keeps insertion order of first sighting. */
export function hitIndex(hits = readHits().hits) {
  const index = new Map();
  for (const hit of hits) {
    const id = hit.rule_id;
    if (!index.has(id)) index.set(id, { count: 0, last: "", keys: new Set() });
    const entry = index.get(id);
    entry.count += 1;
    if (hit.at && String(hit.at) > entry.last) entry.last = String(hit.at);
    if (hit.key) entry.keys.add(String(hit.key));
  }
  return index;
}

const epochOf = (ts) => (ts ? Date.parse(ts) || 0 : 0);

/**
 * The injection slice: active rules ordered by **most recent useful hit**, never-hit ones last
 * (oldest curation first, a deterministic order that does not depend on the wall clock), then the
 * budget cut. With no budget every active rule injects — the order still keeps hot rules at the
 * top of the prompt section instead of burying them under stale ones.
 */
export function promptRules({ budget = budgetFromEnv(), path = rulesPath(), hits = readHits() } = {}) {
  const doc = loadRules(path);
  const index = hitIndex(hits.hits);
  const ordered = doc.rules
    .filter((r) => r.active && !r.superseded_by)
    .map((r) => ({
      id: r.principle_id,
      text: r.text.trim(),
      last_hit: index.get(r.principle_id)?.last || "",
      created: r.created_at || "",
    }))
    .sort(
      // Hot rules first; never-hit rules last, oldest curation first, then id — every order
      // here is a function of file content + logged facts, never of the wall clock.
      (a, b) =>
        epochOf(b.last_hit) - epochOf(a.last_hit) ||
        String(a.created).localeCompare(String(b.created)) ||
        a.id.localeCompare(b.id, undefined, { numeric: true }),
    );
  const capped = budget > 0 && ordered.length > budget;
  return {
    ordered: capped ? ordered.slice(0, budget) : ordered,
    total: ordered.length,
    budget,
    capped,
  };
}

/**
 * Record that a rule was actually used this run. Appends one `rule_hit_v0.1` line; with a `key`
 * (the question), a second identical (rule, key) pair is reported `duplicated` and **not**
 * appended — a question either used a rule or it did not.
 */
export function recordRuleHit(ruleId, { key = "", why = "" } = {}, paths = {}) {
  const house = loadRules(paths.rulesPath || rulesPath());
  const rule = house.rules.find((r) => r.principle_id === ruleId && r.active && !r.superseded_by);
  if (!rule) {
    throw new Error(`record_rule_hit: no active rule ${JSON.stringify(ruleId)} in ${rulesPath()}`);
  }
  const path = paths.hitsPath || hitsPath();
  const { hits } = readHits(path);
  if (key) {
    const already = hits.find((h) => h.rule_id === ruleId && h.key === key);
    if (already) {
      return { ok: true, duplicated: true, rule_id: ruleId, key };
    }
  }
  mkdirSync(dirname(path), { recursive: true });
  // `prompt_version` rides on every hit from day one (§9.11-8 / 3.4's A/B): the designer may later
  // replace a rule's wording with a fuller one, and the comparison that justifies it — version A's
  // hit rate vs version B's — needs to know WHICH prompt did the earning. `agent.mjs`/`chat.mjs`
  // export `PROMPT_VERSION` into the env before a run; a bare library call simply records empty.
  const prompt_version = process.env.REPAIR_AGENT_PROMPT_VERSION || "";
  appendFileSync(
    path,
    `${JSON.stringify({ schema: "rule_hit_v0.1", rule_id: ruleId, at: new Date().toISOString(), by: "agent", key, why, prompt_version })}\n`,
    "utf8",
  );
  return { ok: true, rule_id: ruleId, key, why, prompt_version };
}

/** Cheap deterministic similarity: character-bigram Jaccard, punctuation flattened away. */
function bigrams(text) {
  const flat = String(text).replace(/[\s，。、「」（）()：:；;、.,'"！?？！]/g, "").toLowerCase();
  const set = new Set();
  for (let i = 0; i < flat.length - 1; i += 1) set.add(flat.slice(i, i + 2));
  return set;
}
function jaccard(a, b) {
  if (!a.size || !b.size) return 0;
  let shared = 0;
  for (const g of a) if (b.has(g)) shared += 1;
  return shared / (a.size + b.size - shared);
}
export const DUPLICATE_JACCARD = 0.6;

/**
 * The periodic compression pass — **report only** (§9.11-7: the designer approves, then a
 * compression changes the house and bumps the version; this module never edits the house).
 * Names, deterministically:
 *   - zero-hit active rules (retirement candidates),
 *   - near-duplicate active-rule pairs with their measured similarity,
 *   - superseded rules (lineage, for the record, not actions),
 *   - **inbox drift**: stream rows that arrived after (or were never imported to) the house.
 */
export function compressionReport({ rulesPath: housePath, hitsPath: hitPath } = {}) {
  const hp = housePath || rulesPath();
  const doc = loadRules(hp);
  const active = doc.rules.filter((r) => r.active && !r.superseded_by);
  const superseded = doc.rules.filter((r) => r.superseded_by || !r.active).map((r) => ({
    principle_id: r.principle_id,
    active: r.active,
    superseded_by: r.superseded_by ?? null,
  }));
  const { hits, torn } = readHits(hitPath || hitsPath());
  const index = hitIndex(hits);
  const zero_hit = active.filter((r) => !index.has(r.principle_id)).map((r) => r.principle_id);

  const duplicate_candidates = [];
  for (let i = 0; i < active.length; i += 1) {
    const ga = bigrams(active[i].text);
    for (let k = i + 1; k < active.length; k += 1) {
      const score = jaccard(ga, bigrams(active[k].text));
      if (score >= DUPLICATE_JACCARD) {
        duplicate_candidates.push({
          rules: [active[i].principle_id, active[k].principle_id],
          similarity: Number(score.toFixed(3)),
        });
      }
    }
  }
  duplicate_candidates.sort((a, b) => b.similarity - a.similarity);

  const hits_per_rule = active.map((r) => {
    const entry = index.get(r.principle_id);
    return {
      principle_id: r.principle_id,
      count: entry?.count ?? 0,
      last_hit: entry?.last || "",
      distinct_keys: entry ? entry.keys.size : 0,
    };
  });

  // Inbox drift: the house records the stream file it came from; compare against it as it is now.
  let inbox = null;
  const streamFile = doc.imported_from?.file;
  if (streamFile && existsSync(streamFile)) {
    const currentSha = createHash("sha256").update(readFileSync(streamFile, "utf8")).digest("hex");
    const knownTexts = new Set(doc.rules.map((r) => r.text));
    const unknown = [];
    let empty = 0;
    for (const line of readFileSync(streamFile, "utf8").split("\n")) {
      if (!line.trim()) continue;
      let row;
      try {
        row = JSON.parse(line);
      } catch {
        continue;
      }
      const text = String(row.text ?? "").trim();
      if (!text) {
        empty += 1;
        continue;
      }
      if (!knownTexts.has(text)) unknown.push({ principle_id: row.principle_id ?? null, text: text.slice(0, 60) });
    }
    inbox = {
      file: streamFile,
      house_imported_sha: doc.imported_from.sha256 || null,
      current_sha: currentSha,
      drifted: currentSha !== doc.imported_from.sha256,
      rows_not_in_house: unknown,
      empty_rows: empty,
    };
  }

  return {
    house: { file: hp, schema: doc.schema, version: doc.version, rules_total: doc.rules.length },
    active_total: active.length,
    superseded,
    zero_hit,
    duplicate_candidates,
    hits_per_rule,
    budget: budgetFromEnv(),
    hits_torn_lines: torn,
    inbox,
    next_step: "designer approves a merge/retire list; an approved compression writes the house and bumps its version",
  };
}