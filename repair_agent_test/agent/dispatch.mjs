#!/usr/bin/env node
// L1 派工器：帳本上**新的人類 block** → 修題工單 →（呼叫 L2 生產者）→ 草案（status proposed）
// → 設計者的 ✅/↩（REPAIR_LOOP_PLAN.md §4 L1；本機沙盒，站上同步另經 owner 核准）。
//
// 派工契約：
// - 只派**人類** block：reviewer 前綴比對 `REPAIR_REVIEWER_PREFIXES`
//   （`qbr/src/qbr/review_ui/constants.py` 的權威清單，由 bridge 匯出——這裡不硬抄一份）。
//   機器列的 block 不派工（負對照 test_dispatch.mjs）。
// - 沒有理由的 block 也派工（reason 空字串）——設計者按了 block 鈕就是授權。
// - 同一題已有「晚於此 block 的未結草案」⇒ 不重派（crash 後重跑也靠這條保持冪等）。
// - cursor：`store/dispatch_cursor.json` 記 ledger 的 byte offset／mtime；生產者**成功退出**
//   才前進——中途死了，下次 --once 從原 offset 重派，靠「未結草案」檢查不會重複。
// - 工單列：{key, human_block_reason, return_reasons, lessons, crops, fields}；
//   crops 由 bridge `crop --key` 現切（絕對路徑，只收真實存在的檔案）；切不出來就空陣列，
//   讓 L2 誠實 degraded，不虛構證據。
//
//   node repair_agent_test/agent/dispatch.mjs --once        # 跑一輪
//   node repair_agent_test/agent/dispatch.mjs --plan-only   # 只寫工單不呼叫模型（檢查用）
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
const backIdx = args.indexOf("--backlog");
const BACKLOG = backIdx >= 0 ? Number(args[backIdx + 1] || 0) : 0;
if (!ONCE && !PLAN_ONLY && !WATCH_S) {
  console.error("用法：dispatch.mjs --once | --plan-only | --watch <秒> [--backlog N]");
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

const CURSOR = path.join(cfg.store, "dispatch_cursor.json");
const WO = path.join(cfg.store, "workorders", "wo-repair.jsonl");
fs.mkdirSync(path.dirname(WO), { recursive: true });

const isMachine = (reviewer) =>
  (cfg.prefixes || []).some((p) => String(reviewer || "").startsWith(p));

function readCursor() {
  if (!fs.existsSync(CURSOR)) return null; // 第一次跑
  try { return JSON.parse(fs.readFileSync(CURSOR, "utf8")); }
  catch {
    console.error("dispatch：cursor 檔壞了，當作第一次跑（歷史不重派）");
    return null;
  }
}
function writeCursor(c) { fs.writeFileSync(CURSOR, JSON.stringify(c) + "\n"); }

// cursor 不存在（第一次跑）：**歷史 block 不是待辦**——存量清不清是 owner 的裁決。預設從
// 檔尾起算（只看以後的新 block）；`--backlog N` 才回看最後 N 筆人類 block。
// 有 cursor 之後這個函式不再被叫。
function freshCursorOffset(ledger) {
  const st = fs.statSync(ledger);
  if (!BACKLOG) return { offset: st.size, size: st.size, mtime: st.mtimeMs, note: "從現在起（歷史不重派）" };
  const text = fs.readFileSync(ledger, "utf8");
  const human = [];
  let off = 0;
  for (const line of text.split("\n")) {
    const next = off + Buffer.byteLength(line) + 1;
    if (line.trim()) {
      try {
        const row = JSON.parse(line);
        if (row.action === "block" && !isMachine(row.reviewer) && row.candidate_key) human.push(off);
      } catch { /* skip */ }
    }
    off = next;
  }
  const start = human.length ? human[Math.max(0, human.length - BACKLOG)] : st.size;
  return { offset: start, size: st.size, mtime: st.mtimeMs, note: `回看最後 ${BACKLOG} 筆人類 block` };
}

// ledger 自 offset 起**完整行**（尾行沒寫完就不消費——cursor 不會卡在半行上）。
function newLedgerRows(cursor) {
  const fd = fs.openSync(cfg.human, "r");
  try {
    const st = fs.fstatSync(fd);
    let offset = cursor.offset;
    if (st.size <= offset) { // 沒有 offset 之後的位元組＝沒有新列（檔案變小則下面重置）
      return { rows: [], next: offset, size: st.size, mtime: st.mtimeMs, unchanged: true };
    }
    if (st.size < offset) offset = 0; // ledger 被換寫過（不該發生）：從頭重消費
    const len = st.size - offset;
    if (len === 0) return { rows: [], next: offset, size: st.size, mtime: st.mtimeMs, unchanged: true };
    const buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, offset);
    const text = buf.toString("utf8");
    const lastNl = text.lastIndexOf("\n");
    const rows = [];
    if (lastNl > 0) for (const line of text.slice(0, lastNl).split("\n")) {
      if (!line.trim()) continue;
      try { rows.push(JSON.parse(line)); } catch { /* 壞行跳過，不擋整輪 */ }
    }
    if (!rows.length) return { rows: [], next: offset, size: st.size, mtime: st.mtimeMs, unchanged: true };
    // next 是**位元組**位移：lastNl 是解碼後字串的索引，中文 ledger 兩者差很大
    // （量到 2026-10-03：2.1MB 的短算讓 cursor 永遠不到 EOF，每輪重消費整段尾）。
    return { rows, next: offset + Buffer.byteLength(text.slice(0, lastNl + 1), "utf8"), size: st.size, mtime: st.mtimeMs };
  } finally { fs.closeSync(fd); }
}

// 帳本裡該題「晚於 block 的未結草案」＝ status proposed 且 at > block（檔案序＝時間序）。
function hasOpenDraftAfter(key, at) {
  if (!fs.existsSync(cfg.drafts)) return false;
  for (const line of fs.readFileSync(cfg.drafts, "utf8").split("\n")) {
    if (!line.trim() || !line.includes(key)) continue;
    try {
      const row = JSON.parse(line);
      if (row.candidate_key === key && row.status === "proposed" && String(row.at) > String(at)) return true;
    } catch { /* skip */ }
  }
  return false;
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

// 從 candidates 撈需要的題（subject/stem 給 lessons 與人工檢視）；314MB 串流掃一遍。
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
  let cursor = readCursor();
  if (cursor === null) {
    cursor = freshCursorOffset(cfg.human);
    console.log(`dispatch：第一次跑，cursor ${cursor.note}`);
    if (!BACKLOG) writeCursor(cursor); // 純初始化：直接標記已見
  }
  const { rows, next, size, mtime, unchanged } = newLedgerRows(cursor);
  if (unchanged) return { dispatched: 0, reason: "ledger 無新列" };
  const blocks = rows.filter((r) => r && r.action === "block" && !isMachine(r.reviewer) && r.candidate_key);
  // 同題多筆 block：只派最新一筆（理由最新）。
  const latest = new Map();
  for (const b of blocks) {
    const prev = latest.get(b.candidate_key);
    if (!prev || String(b.created_at || b.at || "") > String(prev.created_at || prev.at || ""))
      latest.set(b.candidate_key, b);
  }
  const todo = [...latest.entries()].filter(([key, b]) => {
    const at = String(b.created_at || b.at || "");
    if (hasOpenDraftAfter(key, at)) return false; // 已有更晚的未結草案：不重派
    return true;
  });
  if (!todo.length) return { dispatched: 0, reason: "新 block 都已有未結草案或非人類", next };

  const questions = loadQuestions(todo.map(([key]) => key));
  const runTag = new Date().toISOString().replace(/[-:]/g, "").slice(0, 15);
  const runWo = path.join(cfg.store, "workorders", `wo-repair-${runTag}.jsonl`);
  const woRows = [];
  for (const [key, b] of todo) {
    const q = questions.get(key) || {};
    const subject = (q.metadata || {}).normalized_subject_name || "";
    const reason = String(b.notes || "").trim(); // 沒有理由也要派工：空字串
    woRows.push({
      key,
      human_block_reason: reason,
      return_reasons: returnReasons(key),
      lessons: lessonsFor(subject),
      crops: cropFor(key),
      fields: fieldsFrom(reason),
      block_at: b.created_at || b.at || "",
      reviewer: b.reviewer || "",
      dispatched_at: new Date().toISOString(),
    });
  }
  // plan-only 是檢視：只寫預覽檔，不動主帳、不推 cursor——之後真跑不會重複派工。
  if (preview) fs.writeFileSync(runWo, woRows.map((r) => JSON.stringify(r)).join("\n") + "\n");
  else {
    for (const row of woRows) fs.appendFileSync(WO, JSON.stringify(row) + "\n"); // 主帳（稽核）
    fs.writeFileSync(runWo, woRows.map((r) => JSON.stringify(r)).join("\n") + "\n");
  }
  return { dispatched: woRows.length, runWo, next, size, mtime, woRows, preview };
}

function runProducer(runWo, out) {
  const script = path.join(CATALOG, "qbr", "scripts", "repair_second_pass.py");
  const r = spawnSync(PY, [script, "--workorder", runWo, "--queue", cfg.queue, "--out", out, "--tag", "D"],
    { encoding: "utf8", cwd: CATALOG, timeout: 30 * 60_000 });
  return r;
}

function cycle() {
  const plan = planOnce({ preview: PLAN_ONLY });
  if (!plan.dispatched) {
    console.log(`dispatch：${plan.reason || "無工單"}`);
    return plan;
  }
  console.log(`dispatch：${plan.preview ? "預覽" : "派工"} ${plan.dispatched} 題 → ${path.basename(plan.runWo)}`);
  for (const row of plan.woRows)
    console.log(`  ${row.key.split(":").slice(-1)[0]}｜${row.fields.join("/")}｜理由：${row.human_block_reason.slice(0, 60) || "（無，設計者只按了 block）"}`);
  if (PLAN_ONLY) { console.log("plan-only：不呼叫生產者，cursor 不前進"); return plan; }
  const outDir = path.join(cfg.store, "scans", new Date().toISOString().slice(0, 10) + "-repair");
  fs.mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "repair_dispatch.jsonl");
  const r = runProducer(plan.runWo, out);
  if (r.status !== 0) {
    console.error(`生產者失敗（exit ${r.status}）：cursor 不前進，草案重派安全（未結草案檢查）`);
    console.error(r.stdout || "", r.stderr || "");
    return plan;
  }
  writeCursor({ offset: plan.next, size: plan.size, mtime: plan.mtime });
  console.log(`${r.stdout.trim().split("\n").slice(0, 3).join("\n")}`);
  return plan;
}

if (WATCH_S > 0) {
  console.log(`dispatch：常駐，每 ${WATCH_S}s 一輪（Ctrl-C 停）`);
  cycle();
  setInterval(cycle, WATCH_S * 1000);
} else {
  cycle();
}
