#!/usr/bin/env node
/**
 * 工單 runner 的進入點（workplan 3.4）。迴路本體與指標在 `lib/workorder.mjs`；這裡只做：
 * 旗標解析、G3 閘、session（同一個 builder）、報告落冊。
 *
 *   node runner.mjs --workorder <file.jsonl> --variant A [--limit N]
 *   node runner.mjs --compare <stampA> <stampB>
 *   node runner.mjs --apply          # ← 永遠拒絕：G3 需要 owner 逐次核准（exit 2）
 *
 * A/B 量測：同一份工單跑兩輪（--variant A／B；prompt_version 以 `PROMPT_VERSION#variant` 戳進
 * 憑證），`--compare` 讀兩行報告算**重判一致性**（同題兩輪修法相同的比例）；拒收率與欄位填全率
 * 在每一輪的報告行裡。全部落在沙盒 store 的 `workorders/report.jsonl`（append-only）。
 */

import { existsSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { STORE_DIR, PROMPT_VERSION } from "./lib/identity.mjs";
import { PATHS } from "./lib/tools.mjs";
import { buildSession, BRAIN } from "./lib/session.mjs";
import {
  loadWorkOrder, runWorkOrder, summarize, checkApplyFlags,
  appendReport, fixesByRun, judgeConsistency, reportsPath,
} from "./lib/workorder.mjs";

const HERE = fileURLToPath(new URL(".", import.meta.url)).replace(/\/$/, "");

function argValue(argv, flag, fallback = "") {
  const index = argv.indexOf(flag);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
}

async function main() {
  const argv = process.argv.slice(2);

  const gate = checkApplyFlags(argv);
  if (gate) {
    console.error(`[runner] G3：${gate.refused.join(" ")} 不是沙盒能做的事。` +
      `落地（改題目文字／寫 candidates／寫帳本）需要 owner 逐次核准——草案的核准在沙盒 UI，` +
      `由設計者按；程式沒有落地路徑，也不該有。`);
    return 2;
  }

  if (argv.includes("--compare")) {
    const storeDir = STORE_DIR;
    if (!existsSync(reportsPath(storeDir))) {
      console.error("[runner] 還沒有任何報告：先各跑一輪 --workorder --variant A/B。");
      return 3;
    }
    const rows = readReports(storeDir);
    const stampA = argv[argv.indexOf("--compare") + 1];
    const stampB = argv[argv.indexOf("--compare") + 2];
    const a = rows.filter((r) => r.kind === "batch" && r.variant === stampA).pop();
    const b = rows.filter((r) => r.kind === "batch" && r.variant === stampB).pop();
    if (!a || !b) { console.error(`[runner] 兩個 variant 的報告行缺一（${stampA}/${stampB}）`); return 4; }
    const consistency = judgeConsistency(
      fixesByRun(storeDir, a.run_id), fixesByRun(storeDir, b.run_id));
    appendReport(storeDir, {
      kind: "compare", workorder: a.workorder, variant_a: stampA, variant_b: stampB,
      runs: [a.run_id, b.run_id], judge_consistency: consistency,
      reject_rate_a: a.metrics.reject_rate, reject_rate_b: b.metrics.reject_rate,
      field_fill_a: a.metrics.field_fill_rate, field_fill_b: b.metrics.field_fill_rate,
      prompt_version_a: a.prompt_version, prompt_version_b: b.prompt_version,
    });
    console.log(JSON.stringify({ judge_consistency: consistency,
      reject_rate: [a.metrics.reject_rate, b.metrics.reject_rate],
      field_fill_rate: [a.metrics.field_fill_rate, b.metrics.field_fill_rate] }, null, 1));
    return 0;
  }

  const workorder = argValue(argv, "--workorder");
  if (!workorder) {
    console.error("用法：node runner.mjs --workorder <file.jsonl> --variant A | --compare A B");
    return 5;
  }
  const variant = argValue(argv, "--variant", "A");
  const limit = Number(argValue(argv, "--limit", "0")) || 0;

  process.env.REPAIR_AGENT_RUN_ID = `runner-${new Date().toISOString()}-${process.pid}`;
  // A/B 的「字」端：同一份工單、同一顆腦，只有 prompt_version 不同——憑證戳進每一筆草案與報告。
  process.env.REPAIR_AGENT_PROMPT_VERSION = `${PROMPT_VERSION}#${variant}`;

  const storeDir = STORE_DIR;
  let items;
  try {
    items = loadWorkOrder(workorder);
  } catch (error) {
    console.error(`[runner] ${error.message}`);
    return 6;
  }
  if (limit > 0) items = items.slice(0, limit);

  const manager = SessionManager.create(HERE);
  let session;
  try {
    session = (await buildSession({ sessionManager: manager })).session;
  } catch (error) {
    console.error(`[runner] session 建不起來：${error.message.split("\n")[0]}`);
    return error.code === "MODEL_NOT_FOUND" ? 4 : 1;
  }
  const identity = {
    run_id: process.env.REPAIR_AGENT_RUN_ID,
    session_id: manager.getSessionId?.() || "",
    prompt_version: process.env.REPAIR_AGENT_PROMPT_VERSION,
  };
  process.env.REPAIR_AGENT_SESSION_ID = identity.session_id;

  const sha8 = createHash("sha256").update(readFileSync(workorder)).digest("hex").slice(0, 8);
  try {
    const results = await runWorkOrder({ items, session, storeDir, identity });
    const fixes = fixesByRun(storeDir, identity.run_id);
    const rows = results.map((r) => ({ ...r, fix: (fixes[r.key] ?? "").slice(0, 200) }));
    appendReport(storeDir, {
      kind: "batch", workorder: workorder, workorder_sha8: sha8, variant,
      brain: BRAIN, metrics: summarize(results),
      run_id: identity.run_id, session_id: identity.session_id,
      prompt_version: identity.prompt_version,
      items: rows,
    });
    console.log(JSON.stringify({ variant, metrics: summarize(results),
      workorder_sha8: sha8, run_id: identity.run_id }, null, 1));
    return 0;
  } finally {
    session.dispose();
  }
}

function readReports(storeDir) {
  return readFileSync(reportsPath(storeDir), "utf8").trim().split("\n")
    .filter(Boolean).map((line) => JSON.parse(line));
}

process.exit(await main());