/**
 * L1 派工器的契約（2026-10-03 owner 修正後：狀態驅動，cursor 已廢）。負對照＋計畫 §4 L1 驗收：
 *   機器列的 block 不派工（repair_* 前綴是機器，不是人）
 *   沒有理由的 block 也派工（reason 空字串）——按了 block 鈕就是授權
 *   「問了未答」不分歷史與新：只要最新人審是 block/return 且之後沒有任何草案列，就派
 *   已答的題不重派：晚於 block 的 proposed **或 degraded** 列都算「已答」
 *   最新人審是 accept（✅ 落地）→ 不派
 *   你再 block 一次（新事件）→ 重派
 *   --limit 有界；plan-only 不寫主帳
 *
 * 全部跑在 scratch 佇列/商店（REPAIR_AGENT_QUEUE／REPAIR_AGENT_STORE），真佇列一個 byte 不碰。
 * 端到端（wo→草案→pending）由真沙盒 --once＋test_landing.mjs 覆蓋。
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
  // 只看**最新**一份（runTag 含毫秒：兩次跑是兩個檔，不是同一檔累加）
  const latest = files.sort().at(-1);
  return readFileSync(join(s.store, "workorders", latest), "utf8").trim().split("\n")
    .filter(Boolean).map((l) => JSON.parse(l));
};

const blockEvent = (key, at, notes = "錯字", reviewer = "local") =>
  JSON.stringify({ at, action: "block", candidate_key: key, reviewer, notes }) + "\n";
const draftRow = (key, at, status = "proposed") => JSON.stringify({
  schema: "repair_draft v1", at, candidate_key: key, status,
  fix: "改好的題幹。", insert: "題幹", basis: "紙本。",
}) + "\n";

test("機器列的 block 不派工——repair_ 前綴不是人（負對照）", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    blockEvent(KEY, "2026-10-03T11:00:00", "機器自己說要修", "repair_text_landing"), "utf8");
  const out = await dispatch(s, "--plan-only");
  assert.match(out, /無待答/);
  assert.equal(woRows(s).length, 0, "機器列絕不進派工佇列");
});

test("沒有理由的 block 也派工：reason 空、fields 預設題幹、return/lessons 帶齊", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    blockEvent(KEY, "2026-09-01T00:00:00", ""), "utf8"); // 一個月前的「歷史」也要答
  writeFileSync(join(s.store, "agent_feedback.jsonl"),
    [{ at: "2026-10-01T09:00:00", action: "repair_return", candidate_key: KEY, reason: "第二次退回" },
     { at: "2026-09-30T08:00:00", action: "repair_return", candidate_key: KEY, reason: "第一次退回" }]
      .map((r) => JSON.stringify(r)).join("\n") + "\n", "utf8");
  writeFileSync(join(s.store, "lessons.jsonl"),
    [{ at: "x", subject: "藥劑學（包括生物藥劑學）", text: "經驗乙", count: 3 },
     { at: "x", subject: "藥劑學（包括生物藥劑學）", text: "經驗甲", count: 7 }]
      .map((r) => JSON.stringify(r)).join("\n") + "\n", "utf8");
  await dispatch(s, "--plan-only");
  const rows = woRows(s);
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.equal(row.key, KEY);
  assert.equal(row.human_block_reason, "", "沒有理由也要派工，不是跳過");
  assert.deepEqual(row.return_reasons, ["第一次退回", "第二次退回"], "依時序，不是檔案倒序");
  assert.deepEqual(row.lessons, ["經驗甲（見過 7 次）", "經驗乙（見過 3 次）"]);
  assert.deepEqual(row.fields, ["題幹"]);
  assert.deepEqual(row.crops, [], "scratch 沒有 PDF：切不出裁片＝空陣列（生產者誠實 degraded），不虛構路徑");
});

test("block 理由點名選項／答案 → fields 記下來", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    blockEvent(KEY, "2026-10-03T11:00:00", "選項 C 的單位錯了，答案也要看"), "utf8");
  await dispatch(s, "--plan-only");
  assert.deepEqual(woRows(s)[0].fields, ["選項 C", "答案"]);
});

test("晚於 block 的 proposed 草案已存在 → 不重派", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    blockEvent(KEY, "2026-10-03T11:00:00"), "utf8");
  writeFileSync(join(s.store, "repair_drafts.jsonl"), draftRow(KEY, "2026-10-03T11:05:00"), "utf8");
  const out = await dispatch(s, "--plan-only");
  assert.match(out, /無待答/);
  assert.equal(woRows(s).length, 0);
});

test("晚於 block 的 degraded 列（AI 已答「答不出」）→ 也不重派", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    blockEvent(KEY, "2026-10-03T11:00:00"), "utf8");
  writeFileSync(join(s.store, "repair_drafts.jsonl"),
    draftRow(KEY, "2026-10-03T11:05:00", "degraded"), "utf8");
  const out = await dispatch(s, "--plan-only");
  assert.match(out, /無待答/);
  assert.equal(woRows(s).length, 0, "答不出也是答案；要再問請重新 block");
});

test("你再 block 一次（新事件晚於草案）→ 重派", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    blockEvent(KEY, "2026-10-03T11:00:00") + blockEvent(KEY, "2026-10-03T12:00:00", "又發現一處"), "utf8");
  writeFileSync(join(s.store, "repair_drafts.jsonl"), draftRow(KEY, "2026-10-03T11:05:00"), "utf8");
  await dispatch(s, "--plan-only");
  assert.equal(woRows(s).length, 1, "新問題是新授權");
});

test("最新人審是 accept（✅ 落地）→ 不派，即使更早有 block", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    blockEvent(KEY, "2026-10-03T11:00:00") +
    JSON.stringify({ at: "2026-10-03T12:00:00", action: "accept", candidate_key: KEY,
      reviewer: "local", notes: "" }) + "\n", "utf8");
  const out = await dispatch(s, "--plan-only");
  assert.match(out, /無待答/);
  assert.equal(woRows(s).length, 0);
});

test("退回過的題（草案晚於 block 但晚於退回）→ 重派，理由進工單", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    blockEvent(KEY, "2026-10-03T11:00:00"), "utf8");
  writeFileSync(join(s.store, "repair_drafts.jsonl"), draftRow(KEY, "2026-10-03T11:05:00"), "utf8");
  writeFileSync(join(s.store, "agent_feedback.jsonl"),
    JSON.stringify({ at: "2026-10-03T12:00:00", action: "repair_return", candidate_key: KEY,
      reason: "把答案也改了，不要動答案" }) + "\n", "utf8");
  await dispatch(s, "--plan-only");
  const rows = woRows(s);
  assert.equal(rows.length, 1, "退回＝重開，不是已答");
  assert.deepEqual(rows[0].return_reasons, ["把答案也改了，不要動答案"]);
});

test("--limit 有界；最舊優先", async () => {
  const s = scratch();
  const keys = [KEY, "moex:t:311:0704:1:question:q009", "moex:t:311:0704:1:question:q010"];
  writeFileSync(join(s.review, "question_review_events.jsonl"),
    keys.map((k, i) => blockEvent(k, `2026-10-03T1${i}:00:00`)).join(""), "utf8");
  await dispatch(s, "--plan-only --limit 2");
  const rows = woRows(s);
  assert.equal(rows.length, 2, "單輪有界");
  assert.equal(rows[0].key, KEY, "最舊的 block 先答");
});

test("plan-only 不寫主帳", async () => {
  const s = scratch();
  writeFileSync(join(s.review, "question_review_events.jsonl"), blockEvent(KEY, "2026-10-03T11:00:00"), "utf8");
  await dispatch(s, "--plan-only");
  assert.equal(existsSync(join(s.store, "workorders", "wo-repair.jsonl")), false,
    "plan-only 只寫預覽檔");
});
