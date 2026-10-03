#!/usr/bin/env node
// L1 派工器：帳本上**問了未答的題** → 修題工單 →（呼叫 L2 生產者）→ 草案或「AI 答不出」
// → 設計者的 ✅/↩（REPAIR_LOOP_PLAN.md §4 L1；本機沙盒，站上同步另經 owner 核准）。
//
// 派工契約（2026-10-03 owner 修正：**狀態驅動，不看 cursor 新舊**——「我問了卻沒有修法」
// 的根因是舊版只派 cursor 之後的新 block，歷史 block 靜默掉題）：
// - 目標集＝最新一筆**人類**事件是 block/return、且其後**沒有任何草案列**（proposed 或
//   degraded）的題。cursor 已廢——每輪重掃帳本推導狀態，冪等靠狀態本身：
//   答過（草案列在）就不重派；你再 block 一次（新事件）就重派。
// - 機器列不派工：reviewer 前綴比對 `REPAIR_REVIEWER_PREFIXES`
//   （`qbr/src/qbr/review_ui/constants.py` 的權威清單，由 bridge 匯出——這裡不硬抄一份）。
// - 沒有理由的 block 也派工（reason 空字串）——設計者按了 block 鈕就是授權。
// - 每輪最多 --limit 題（預設 40，occamy ~3-8s/題）：單輪有界，watcher 逐輪消化積壓。
// - 工單列：{key, human_block_reason, return_reasons, lessons, crops, fields}；
//   crops 由 bridge `crop --key` 現切（絕對路徑，只收真實存在的檔案）；切不出來就空陣列，
//   生產者誠實 degraded（並在草案流記一筆「AI 答不出」），不虛構證據。
//
//   node repair_agent_test/agent/dispatch.mjs --once        # 跑一輪（最多 --limit 題）
//   node repair_agent_test/agent/dispatch.mjs --plan-only   # 只寫預覽工單不呼叫模型
//   node repair_agent_test/agent/dispatch.mjs --watch 60    # 常駐
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CATALOG = path.resolve(HERE, "..", "..");          // tw-national-exam-catalog/
const LESSON_TOP_N = 5;

const args = process.argv.slice(2);
const ONCE = args.includes("--once");
const PLAN_ONLY = args.includes("--plan-only");
const watchIdx = args.indexOf("--watch");
const WATCH_S = watchIdx >= 0 ? Number(args[watchIdx + 1] || 60) : 0;
const limitIdx = args.indexOf("--limit");
const LIMIT = limitIdx >= 0 ? Number(args[limitIdx + 1] || 40) : 40;
if (!ONCE && !PLAN_ONLY && !WATCH_S) {
  console.error("用法：dispatch.mjs --once | --plan-only | --watch <秒> [--limit N]");
  process.exit(1);
}

// python：bridge 設定與裁題都要 PyMuPDF——用 qbr 的 venv；沒有才退到 python3（crop 會失敗）。
const VENV_PY = path.join(CATALOG, "qbr", ".venv", "bin", "python");
const PY = fs.existsSync(VENV_PY) ? VENV_PY : "python3";
const BRIDGE = path.join(HERE, "bridge.py");

const cfg = JSON.parse(execFileSync(PY, ["-c", `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("bridge_cfg", ${JSON.stringify(BRIDGE)})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(json.dumps({
    "prefixes": list(m.REPAIR_REVIEWER_PREFIXES),
    "queue": m.QUEUE_ROOT, "candidates": m.CANDIDATES, "human": m.HUMAN_EVENTS,
    "store": m.STORE_DIR, "drafts": m.DRAFTS,
}))`], { encoding: "utf8" }));

const WO = path.join(cfg.store, "workorders", "wo-repair.jsonl");
fs.mkdirSync(path.dirname(WO), { recursive: true });

const isMachine = (reviewer) =>
  (cfg.prefixes || []).some((p) => String(reviewer || "").startsWith(p));

// --- 狀態推導（權威）：帳本最新人類事件 ＋ 草案流最新回答 -----------------------------
// 帳本 24MB／草案 7609 列，全掃一次 <1s；狀態在檔案裡，不在記憶體 cursor 裡——
// crash、換機、重跑都得到同一個答案。
function openQuestions() {
  const latestHuman = new Map(); // key -> {at, action, notes}
  for (const line of fs.readFileSync(cfg.human, "utf8").split("\n")) {
    if (!line.trim()) continue;
    let row;
    try { row = JSON.parse(line); } catch { continue; }
    if (!row.candidate_key || isMachine(row.reviewer)) continue;
    const at = String(row.created_at || row.at || "");
    const prev = latestHuman.get(row.candidate_key);
    if (!prev || at > prev.at) {
      latestHuman.set(row.candidate_key,
        { at, action: row.action, notes: row.notes || "", reviewer: row.reviewer || "" });
    }
  }
  const answeredAt = new Map(); // key -> 最新草案列 at（proposed 與 degraded 都算「已答」）
  if (fs.existsSync(cfg.drafts)) for (const line of fs.readFileSync(cfg.drafts, "utf8").split("\n")) {
    if (!line.trim()) continue;
    let row;
    try { row = JSON.parse(line); } catch { continue; }
    if (row.candidate_key && (row.status === "proposed" || row.status === "degraded")) {
      const at = String(row.at || "");
      if (at > (answeredAt.get(row.candidate_key) || "")) answeredAt.set(row.candidate_key, at);
    }
  }
  // ↩ 退回＝重開：設計者退過的題，草案不算答案——理由是下一輪的第一行輸入（§1 迴圈 F→C）。
  const returnedAt = new Map();
  const feedback = path.join(cfg.store, "agent_feedback.jsonl");
  if (fs.existsSync(feedback)) for (const line of fs.readFileSync(feedback, "utf8").split("\n")) {
    if (!line.trim() || !line.includes("repair_return")) continue;
    let row;
    try { row = JSON.parse(line); } catch { continue; }
    if (row.candidate_key && row.action === "repair_return") {
      const at = String(row.at || "");
      if (at > (returnedAt.get(row.candidate_key) || "")) returnedAt.set(row.candidate_key, at);
    }
  }
  const open = [];
  for (const [key, st] of latestHuman) {
    if (st.action !== "block" && st.action !== "return") continue;
    const lastAnswer = answeredAt.get(key) || "";
    const reopenedAt = returnedAt.get(key) || "";
    // 已答＝有一筆草案晚於 block **且**晚於最後一次退回（退回把先前的草案作廢成待重答）
    if (lastAnswer > st.at && lastAnswer > reopenedAt) continue;
    open.push({ key, at: st.at, notes: st.notes || "", action: st.action, reviewer: st.reviewer });
  }
  open.sort((a, b) => a.at < b.at ? -1 : 1); // 最舊優先：問最久的先得到答案
  return open;
}

function returnReasons(key) {
  const file = path.join(cfg.store, "agent_feedback.jsonl");
  if (!fs.existsSync(file)) return [];
  const rows = [];
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    if (!line.trim() || !line.includes(key)) continue;
    try {
      const row = JSON.parse(line);
      if (row.candidate_key === key && row.action === "repair_return" && row.reason)
        rows.push({ at: row.at, reason: row.reason });
    } catch { /* skip */ }
  }
  return rows.sort((a, b) => String(a.at).localeCompare(String(b.at))).map((r) => r.reason);
}

function lessonsFor(subject) {
  const file = path.join(cfg.store, "lessons.jsonl");
  if (!fs.existsSync(file) || !subject) return [];
  const rows = [];
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (row.subject === subject) rows.push(row);
    } catch { /* skip */ }
  }
  return rows.sort((a, b) => (b.count || 1) - (a.count || 1)).slice(0, LESSON_TOP_N)
    .map((r) => `${r.text}（見過 ${r.count || 1} 次）`);
}

// block 理由 → 目標欄位（advisory）：點名「選項 X」「答案」就記下來；否則題幹。
function fieldsFrom(reason) {
  const fields = new Set();
  for (const m of String(reason || "").matchAll(/選項\s*([A-D])/g)) fields.add(`選項 ${m[1]}`);
  if (/答案/.test(reason)) fields.add("答案");
  if (!fields.size) fields.add("題幹");
  return [...fields];
}

// 該題的紙本裁片：bridge crop 現切（含它自己的圖框），回絕對路徑；切不出來＝空陣列。
function cropFor(key) {
  const r = spawnSync(PY, [BRIDGE, "crop", "--key", key], { encoding: "utf8", timeout: 120_000 });
  if (r.status !== 0) return [];
  try { const png = JSON.parse(r.stdout).png; return png && fs.existsSync(png) ? [png] : []; }
  catch { return []; }
}

// 從 candidates 撈需要的題（subject 給 lessons）；314MB 串流掃一遍——只在有派工時發生。
function loadQuestions(keys) {
  const want = new Set(keys), found = new Map();
  const CHUNK = 8 << 20;
  const fd = fs.openSync(cfg.candidates, "r");
  try {
    let carry = "";
    const buf = Buffer.alloc(CHUNK);
    for (let pos = 0; pos < fs.fstatSync(fd).size;) {
      const n = fs.readSync(fd, buf, 0, CHUNK, pos);
      if (n <= 0) break;
      pos += n;
      const text = carry + buf.toString("utf8", 0, n);
      const lines = text.split("\n");
      carry = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const hit = [...want].find((k) => line.includes(k));
        if (hit === undefined) continue;
        try {
          const row = JSON.parse(line);
          if (row.candidate_key === hit) found.set(hit, row);
        } catch { /* skip */ }
      }
    }
  } finally { fs.closeSync(fd); }
  return found;
}

function planOnce({ preview = false } = {}) {
  const open = openQuestions();
  const todo = open.slice(0, LIMIT);
  if (!todo.length) return { dispatched: 0, reason: `無待答的題（問了未答 ${open.length}）` };
  const questions = loadQuestions(todo.map((t) => t.key));
  const runTag = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 17);
  const runWo = path.join(cfg.store, "workorders", `wo-repair-${runTag}.jsonl`);
  const woRows = todo.map(({ key, at, notes, action, reviewer }) => {
    const q = questions.get(key) || {};
    const subject = (q.metadata || {}).normalized_subject_name || "";
    const reason = String(notes || "").trim(); // 沒有理由也要派工：空字串
    return {
      key,
      human_block_reason: reason,
      return_reasons: returnReasons(key),
      lessons: lessonsFor(subject),
      crops: cropFor(key),
      fields: fieldsFrom(reason),
      block_at: at, block_action: action,
      reviewer: reviewer || "local",
      dispatched_at: new Date().toISOString(),
    };
  });
  // plan-only 是檢視：只寫預覽檔，不動主帳——之後真跑不會重複派工。
  if (preview) fs.writeFileSync(runWo, woRows.map((r) => JSON.stringify(r)).join("\n") + "\n");
  else {
    for (const row of woRows) fs.appendFileSync(WO, JSON.stringify(row) + "\n"); // 主帳（稽核）
    fs.writeFileSync(runWo, woRows.map((r) => JSON.stringify(r)).join("\n") + "\n");
  }
  return { dispatched: woRows.length, remaining: open.length - woRows.length, runWo, woRows, preview };
}

function runProducer(runWo, out) {
  const script = path.join(CATALOG, "qbr", "scripts", "repair_second_pass.py");
  const r = spawnSync(PY, [script, "--workorder", runWo, "--queue", cfg.queue, "--out", out, "--tag", "D"],
    { encoding: "utf8", cwd: CATALOG, timeout: 120 * 60_000 });
  return r;
}

function cycle() {
  const plan = planOnce({ preview: PLAN_ONLY });
  if (!plan.dispatched) {
    console.log(`dispatch：${plan.reason || "無工單"}`);
    return plan;
  }
  console.log(`dispatch：${plan.preview ? "預覽" : "派工"} ${plan.dispatched} 題（尚餘 ${plan.remaining}）→ ${path.basename(plan.runWo)}`);
  for (const row of plan.woRows)
    console.log(`  ${row.key.split(":").slice(-1)[0]}｜${row.fields.join("/")}｜理由：${row.human_block_reason.slice(0, 60) || "（無，設計者只按了 block）"}`);
  if (PLAN_ONLY) { console.log("plan-only：不呼叫生產者"); return plan; }
  const outDir = path.join(cfg.store, "scans", new Date().toISOString().slice(0, 10) + "-repair");
  fs.mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "repair_dispatch.jsonl");
  const r = runProducer(plan.runWo, out);
  if (r.status !== 0) {
    console.error(`生產者失敗（exit ${r.status}）：這批沒有留草案，下一輪重派同批（狀態未變）`);
    console.error(r.stdout || "", r.stderr || "");
    return plan;
  }
  console.log(`${r.stdout.trim().split("\n").slice(0, 3).join("\n")}`);
  return plan;
}

if (WATCH_S > 0) {
  console.log(`dispatch：常駐，每 ${WATCH_S}s 一輪、每輪最多 ${LIMIT} 題（Ctrl-C 停）`);
  cycle();
  setInterval(cycle, WATCH_S * 1000);
} else {
  cycle();
}
