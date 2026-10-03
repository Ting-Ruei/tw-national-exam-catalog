/**
 * L1 派工器的契約。負對照（舊行為下必失敗的那一例）＋計畫 §4 L1 驗收：
 *   機器列的 block 不派工（repair_* 前綴是機器，不是人）
 *   沒有理由的 block 也派工（reason 空字串）——按了 block 鈕就是授權
 *   該題已有「晚於 block 的未結草案」不重派（crash 重跑也靠這條冪等）
 *   工單列帶齊：block 理由／歷次退回理由（時序）／科目經驗／絕對路徑裁片（切不出＝空，L2 誠實 degraded）
 *   plan-only 不動主帳、不推 cursor
 *
 * 全部跑在 scratch 佇列/商店（REPAIR_AGENT_QUEUE／REPAIR_AGENT_STORE），真佇列一個 byte 不碰。
 * 端到端（wo→草案→cursor）在真沙盒 --once 驗收（見 REPAIR_LOOP_PLAN §6.4）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, existsSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const run = promisify(execFile);
const AGENT_DIR = dirname(fileURLToPath(import.meta.url));
const DISPATCH = join(AGENT_DIR, "dispatch.mjs");

function scratch() {
  const dir = mkdtempSync(join(tmpdir(), "dispatch-test-"));
  const queue = join(dir, "queue");
  const review = join(queue, "review-ui");
  const store = join(dir, "store");
  mkdirSync(review, { recursive: true });
  mkdirSync(store, { recursive: true });
  writeFileSync(join(review, "candidates.jsonl"), JSON.stringify({
    candidate_key: KEY, question_number: 31, stem: "某題幹。",
    metadata: { normalized_subject_name: "藥劑學（包括生物藥劑學）" },
  }) + "\n", "utf8");
  writeFileSync(join(review, "question_review_events.jsonl"), "", "utf8");
  return { dir, queue, review, store };
}

const KEY = "moex:t:311:0704:1:question:q031";

async function dispatch(s, mode) {
  const { stdout } = await run(process.execPath, [DISPATCH, ...mode.split(" ")], {
    cwd: join(AGENT_DIR, "..", ".."),
    env: {
      ...process.env,
      REPAIR_AGENT_QUEUE: s.queue,
      REPAIR_AGENT_STORE: s.store,
      REPAIR_AGENT_NO_INDEX: "1",
    },
  });
  return stdout;
}

const woRows = (s) => {
  const files = readdirSync(join(s.store, "workorders")).filter((f) => f.startsWith("wo-repair-"));
  if (!files.length) return [];
  // 只看**最新**一份（runTag 含秒：兩次跑是兩個檔，不是同一檔累加）
  const latest = files.sort().at(-1);
  return readFileSync(join(s.store, "workorders", latest), "utf8").trim().split("\n")
    .filter(Boolean).map((l) => JSON.parse(l));
};

test("機器列的 block 不派工——repair_ 前綴不是人（負對照）", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"), JSON.stringify({
    at: "2026-10-03T11:00:00", action: "block", candidate_key: KEY,
    reviewer: "repair_text_landing", notes: "機器自己說要修",
  }) + "\n", "utf8");
  const out = await dispatch(s, "--plan-only --backlog 5");
  assert.match(out, /非人類|無工單|無新列/);
  assert.equal(woRows(s).length, 0, "機器列絕不進派工佇列");
});

test("沒有理由的 block 也派工：reason 空、fields 預設題幹、return/lessons 帶齊", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"), JSON.stringify({
    at: "2026-10-03T11:00:00", action: "block", candidate_key: KEY,
    reviewer: "local", notes: "",
  }) + "\n", "utf8");
  // 歷次退回理由（兩筆，時序）＋科目經驗（兩條，次數高的在前）
  writeFileSync(join(s.store, "agent_feedback.jsonl"),
    [{ at: "2026-10-01T09:00:00", action: "repair_return", candidate_key: KEY, reason: "第二次退回" },
     { at: "2026-09-30T08:00:00", action: "repair_return", candidate_key: KEY, reason: "第一次退回" }]
      .map((r) => JSON.stringify(r)).join("\n") + "\n", "utf8");
  writeFileSync(join(s.store, "lessons.jsonl"),
    [{ at: "x", subject: "藥劑學（包括生物藥劑學）", text: "經驗乙", count: 3 },
     { at: "x", subject: "藥劑學（包括生物藥劑學）", text: "經驗甲", count: 7 }]
      .map((r) => JSON.stringify(r)).join("\n") + "\n", "utf8");
  await dispatch(s, "--plan-only --backlog 5");
  const rows = woRows(s);
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.equal(row.key, KEY);
  assert.equal(row.human_block_reason, "", "沒有理由也要派工，不是跳過");
  assert.deepEqual(row.return_reasons, ["第一次退回", "第二次退回"], "依時序，不是檔案倒序");
  assert.deepEqual(row.lessons, ["經驗甲（見過 7 次）", "經驗乙（見過 3 次）"]);
  assert.deepEqual(row.fields, ["題幹"]);
  assert.deepEqual(row.crops, [], "scratch 沒有 PDF：切不出裁片＝空陣列（L2 會誠實 degraded），不虛構路徑");
  assert.ok(row.crops.every((p) => !p.includes("/Users/") || existsSync(p)));
});

test("block 理由點名選項／答案 → fields 記下來", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"), JSON.stringify({
    at: "2026-10-03T11:00:00", action: "block", candidate_key: KEY,
    reviewer: "local", notes: "選項 C 的單位錯了，答案也要看",
  }) + "\n", "utf8");
  await dispatch(s, "--plan-only --backlog 5");
  assert.deepEqual(woRows(s)[0].fields, ["選項 C", "答案"]);
});

test("該題已有晚於 block 的未結草案 → 不重派（冪等）", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"), JSON.stringify({
    at: "2026-10-03T11:00:00", action: "block", candidate_key: KEY,
    reviewer: "local", notes: "錯字",
  }) + "\n", "utf8");
  writeFileSync(join(s.store, "repair_drafts.jsonl"), JSON.stringify({
    schema: "repair_draft v1", at: "2026-10-03T11:05:00", candidate_key: KEY,
    status: "proposed", fix: "改好的題幹。", insert: "題幹", basis: "紙本。",
  }) + "\n", "utf8");
  const out = await dispatch(s, "--plan-only --backlog 5");
  assert.match(out, /已有未結草案|無工單/);
  assert.equal(woRows(s).length, 0);
});

test("第一次跑不重派歷史：無 --backlog 時 cursor 標記已見、不派任何舊 block", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"), JSON.stringify({
    at: "2026-09-01T00:00:00", action: "block", candidate_key: KEY,
    reviewer: "local", notes: "一個月前的存量 block",
  }) + "\n", "utf8");
  const out = await dispatch(s, "--plan-only");
  assert.match(out, /歷史不重派/);
  assert.equal(woRows(s).length, 0, "存量是 owner 的裁決，不是派工器的");
  assert.ok(existsSync(join(s.store, "dispatch_cursor.json")), "cursor 已初始化");
  const out2 = await dispatch(s, "--plan-only");
  assert.match(out2, /無新列/);
});

test("cursor 前進後同一輪不再派工；plan-only 不推 cursor", async () => {
  const s = scratch();
  const line = JSON.stringify({
    at: "2026-10-03T11:00:00", action: "block", candidate_key: KEY,
    reviewer: "local", notes: "錯字",
  }) + "\n";
  writeFileSync(join(s.review, "question_review_events.jsonl"), line, "utf8");
  await dispatch(s, "--plan-only --backlog 5");
  assert.equal(woRows(s).length, 1, "plan-only 有預覽工單");
  // plan-only 不該推 cursor：再跑一次同樣派得出來
  await dispatch(s, "--plan-only --backlog 5");
  assert.equal(woRows(s).filter((r) => r.key === KEY).length, 1,
    "preview 檔被覆寫而不是累加（主帳沒動）");
  assert.equal(existsSync(join(s.store, "workorders", "wo-repair.jsonl")), false,
    "plan-only 不寫主帳");
  // 模擬一輪成功後的 cursor：再跑 → ledger 無新列
  writeFileSync(join(s.store, "dispatch_cursor.json"),
    JSON.stringify({ offset: Buffer.byteLength(line), size: Buffer.byteLength(line) }));
  const out = await dispatch(s, "--plan-only --backlog 5");
  assert.match(out, /無新列/);
});
