/**
 * 工單 runner（workplan 3.4）的核心：把一批「代理要做的事」從一個檔案（工單）批次執行，
 * 量收斂指標，並把「G3 落地」這一關**用程式的形擋在門口**。
 *
 * 工單 = JSONL；每一列：
 *   { key, note?, lens?, acceptance? }
 *   key＝candidate_key；note＝設計者註解原文（有就帶給代理）；lens＝3.2 掃描鏡頭（上下文）；
 *   acceptance＝驗收條件（文字，記進報告作為後查）。
 *
 * 三件事（都在這個檔，跑者只做進入點）：
 * - 重測性：同一份工單跑兩輪（--variant A／B；prompt_version 由環境戳進憑證），
 *   收斂指標＝重判一致性（同題兩輪的 fix 是否相同）／拒收率（沒落草案的比例）／
 *   欄位填全率（fix/insert/basis 三欄全非空的比例）。報告 append-only 落沙盒 store。
 * - 唯讀：佇列一個位元組不碰；落草案／等裁決行／日誌全在 storeDir。
 * - G3 閘：`--apply`／任何「落地」旗標＝拒絕執行並 exit 2。草案的核准在沙盒 UI 由設計者按；
 *   正式落地（改題目文字／寫 candidates／寫帳本）＝ G3，需要 owner 逐次核准——程式不提供。
 */
import { readFileSync, existsSync, mkdirSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { composeTask, countDrafts, draftPath, questionsPath, attachReplyCollector,
         appendConsumerQuestion, appendConsumerLog } from "./consumer.mjs";

/** 讀工單：每列一個 JSON 物件；空行跳過；壞行列出來（不靜默吞）。 */
export function loadWorkOrder(path) {
  if (!existsSync(path)) throw new Error(`workorder not found: ${path}`);
  const items = [];
  const bad = [];
  for (const [index, line] of readFileSync(path, "utf8").split("\n").entries()) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const row = JSON.parse(trimmed);
      if (!row || typeof row !== "object" || !row.key) {
        bad.push({ line: index + 1, why: "missing key" });
        continue;
      }
      items.push(row);
    } catch {
      bad.push({ line: index + 1, why: "not JSON" });
    }
  }
  if (bad.length) {
    const detail = bad.map((b) => `L${b.line} ${b.why}`).join(", ");
    throw new Error(`workorder has unreadable rows (${detail}); fix the file instead of skipping silently`);
  }
  return items;
}

/** 工單項的任務文案：與事件消費者同一種程序，但把掃描鏡頭與驗收條件帶在文案上。 */
export function composeWorkOrderTask(item) {
  const note = String(item.note || "").trim();
  const lens = String(item.lens || "").trim();
  const acceptance = String(item.acceptance || "").trim();
  const lines = [
    `工單：設計者要你處理這一題。`,
    `candidate key：${item.key}`,
    note ? `設計者的註解：${note}` : `（沒有附註解）`,
    lens ? `已知缺陷類（deterministic 掃描鏡頭）：${lens}` : `（沒有附鏡頭）`,
    ``,
    `程序：`,
    `1. 先用 find_question/get_question 看這一題的現況；需要紙本證據就 crop_question。`,
    `2. 判斷缺陷；哪條原則幫你看到或決定了什麼，用 record_rule_hit 記它的編號（沒用到就不要記）。`,
    `3. 只有在**親眼看過證據**之後才 propose_repair 提草案；fix／insert／basis 三欄都要寫實際內容。`,
    `4. 看不出缺陷或無法對應原則，就**不要**硬提草案——明說「需要設計者裁決」與理由。`,
  ];
  if (acceptance) lines.push(`驗收條件：${acceptance}`);
  return lines.join("\n");
}

/** 一次執行：逐項跑 session、記草案計數。回每項結果（不寫佇列、不寫正式檔）。 */
export async function runWorkOrder({ items, session, storeDir, identity = {}, onItem } = {}) {
  const collector = attachReplyCollector(session);
  const results = [];
  for (const item of items) {
    if (results.some((r) => r.key === item.key)) {
      results.push({ key: item.key, skipped: "duplicate-row-in-workorder" });
      continue;
    }
    const before = countDrafts(storeDir, item.key);
    collector.reset();
    let failed;
    try {
      await session.prompt(composeWorkOrderTask(item));
    } catch (error) {
      failed = String(error.message || error).slice(0, 600);
    }
    const after = countDrafts(storeDir, item.key);
    const landed = after > before;
    const reply = collector.latest();
    const result = {
      key: item.key, drafts_before: before, drafts_after: after, landed,
      note: String(item.note || ""), lens: String(item.lens || ""),
      acceptance: String(item.acceptance || ""),
      reply: reply.slice(0, 500), reply_chars: reply.length,
      ...identity,
    };
    if (landed) {
      // 欄位填全率吃的是草案本體（fix/insert/basis），不是計數。
      Object.assign(result, latestDraftColumns(storeDir, item.key, identity.run_id || "") || {});
    }
    if (failed) {
      result.failed = failed;
    } else if (!landed) {
      appendConsumerQuestion(storeDir, {
        key: item.key, notes: result.note, lens: result.lens,
        why: reply ? "工單跑完但沒有草案落成——它的結語：" + reply.slice(0, 800)
                   : "工單跑完但沒有草案落成、也沒有結語——需要設計者裁決",
        source: "workorder", ...identity,
      });
    }
    results.push(result);
    if (onItem) await onItem(result);
  }
  appendConsumerLog(storeDir, {
    event: "workorder_done", items: items.length, results: summarize(results), ...identity,
  });
  return results;
}

/**
 * 某一輪 run 在某一題的**最新**草案行的三欄（fix/insert/basis）。欄位填全率要評的是
 * 草案本身，不是計數——結果行只帶計數，填全率從草案行讀。
 */
export function latestDraftColumns(storeDir, key, runId) {
  const path = draftPath(storeDir);
  if (!existsSync(path)) return null;
  let latest = null;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (row.candidate_key === key && row.run_id === runId) latest = row;
    } catch { /* 壞行不算 */ }
  }
  return latest ? { fix: String(latest.fix || ""), insert: String(latest.insert || ""),
                    basis: String(latest.basis || "") } : null;
}

/** 收斂指標（workplan 3.4）：拒收率、欄位填全率、重判一致性。 */
export function summarize(results) {
  const done = results.filter((r) => !r.failed && !r.skipped);
  const drafted = done.filter((r) => r.landed);
  return {
    items: results.length,
    drafted: drafted.length,
    rejected: done.length - drafted.length,
    failed: results.filter((r) => r.failed).length,
    reject_rate: done.length ? +( (done.length - drafted.length) / done.length ).toFixed(3) : null,
    field_fill_rate: drafted.length
      ? +( drafted.filter((r) => r.basis && r.insert && r.fix).length / drafted.length ).toFixed(3)
      : null,
  };
}

// ------------------------------------------------------------------ G3 閘與報告

/**
 * G3 逐批核可閘：任何「落地」旗標都被程式拒絕。落地（改題目文字／寫 candidates／寫帳本／
 * 上站）＝G3，需要 owner 逐次核准；草案的核准在沙盒 UI 由設計者按。「沒有這個旗標」不夠——
 * 閘要寫成**出現它就拒絕執行**，錯字過不了的（量測：旗標拼錯時要安靜地不吃單才叫閘失效）。
 */
export function applyFlags() { return ["--apply", "--land", "--write-queue", "--import"]; }

export function checkApplyFlags(argv) {
  const refused = (argv || []).filter((flag) => applyFlags().includes(flag));
  return refused.length ? { refused } : null;
}

/** 報告（沙盒 store 內，append-only）：一次批次或一次 A/B 比對，都是一行。 */

export function reportsPath(storeDir) { return join(storeDir, "workorders", "report.jsonl"); }

export function appendReport(storeDir, row) {
  const path = reportsPath(storeDir);
  mkdirSync(join(storeDir, "workorders"), { recursive: true });
  appendFileSync(path, JSON.stringify({ schema: "repair_agent_test/workorder_report v1", at: new Date().toISOString(), ...row }) + "\n", "utf8");
  return path;
}

/**
 * 從草案檔收集某一次 run 的修法（key->fix，首次出現為準）。草案行帶 run_id（憑證信封），
 * 所以「哪些草案是這一輪出的」不用另記。
 */
export function fixesByRun(storeDir, runId) {
  const path = draftPath(storeDir);
  if (!existsSync(path)) return {};
  const fixes = {};
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (row.run_id === runId && row.candidate_key && fixes[row.candidate_key] === undefined) {
        fixes[row.candidate_key] = String(row.fix || "");
      }
    } catch { /* 壞行不算 */ }
  }
  return fixes;
}

/**
 * 重判一致性：同一份工單、兩個 prompt_version 的 run，在「兩輪都落了草案」的題目上，
 * 修法文字**連同空白一併刪除**後相同的比例（草案的 fix 是自由文字——「A→MAO<sub> A</sub>」
 * 與「A→MAO<sub>A</sub>」是同一個判斷）。1.0＝兩版提示做出同樣的判斷；低＝改動動搖了判斷。
 */
export function judgeConsistency(fixesA, fixesB) {
  const keys = Object.keys(fixesA).filter((k) => fixesB[k] !== undefined);
  if (!keys.length) return { both: 0, same: 0, rate: null };
  const same = keys.filter((k) => spacedLike(fixesA[k]) === spacedLike(fixesB[k])).length;
  return { both: keys.length, same, rate: +(same / keys.length).toFixed(3) };
}

function spacedLike(text) { return String(text || "").replace(/\s+/g, ""); }