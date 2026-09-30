/**
 * 事件消費者（workplan 3.3）的合約，每條帶負控制。測試不建 session：迴路本體
 * `runConsumerLoop` 的 session 由參數注入，這裡給一個**假代理**（記下任務文案、把草案
 * 寫進「該輪」的沙盒 draft 檔），所以「模擬 block 事件驅動全迴路」是這套測試的一部分，不靠真模型。
 *
 * 負控制的地圖：
 *   機器的 block 開出工單        → 會把 AI 自己寫的字當設計者的指示（拒）
 *   部分尾行被消費              → 帳本還在寫就被讀走／位移失真（拒）
 *   游標在縮小的帳本上繼續走    → 同一題被當新事件（拒：炸掉重吃）
 *   任務文案沒帶 key／註解／工具名 → 代理瞎猜要修什麼（拒）
 *   沒落草案的題目沒進等裁決清單 → 消費者吞掉了沒完成的工作（拒）
 *   佇列被寫入                  → 消費者越界（拒：它是讀者）
 *   吞掉任務失敗                → 失敗題被當作完成（拒：游標停住、下輪重吃）
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const SCRATCH = mkdtempSync(join(tmpdir(), "repair-consumer-test-"));

import {
  isDesignerBlock, readNewEvents, loadCursor, saveCursor, composeTask,
  countDrafts, appendConsumerQuestion, runConsumerLoop, loadMachinePrefixes,
  cursorPath, questionsPath, draftPath, ledgerPath,
} from "./lib/consumer.mjs";
import { fakeSession } from "./lib/test_doubles.mjs";

const BLOCK = (key, notes, extra = {}) => ({
  action: "block", candidate_key: key, notes: notes || "", reviewer: "local",
  created_at: "2026-09-30T21:00:00", source: "v2", ...extra,
});
const MACHINE_BLOCK = (key) => ({
  action: "block", candidate_key: key, notes: "AI 自己的流程狀態", reviewer: "repair_dispute_apply",
  created_at: "2026-09-30T21:00:00", source: "qbr",
});

function makeLedgerDir(rows, { trailing = "" } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "consumer-fixture-"));
  const reviewUi = join(dir, "review-ui");
  mkdirSync(reviewUi, { recursive: true });
  const body = rows.map((r) => JSON.stringify(r) + "\n").join("");
  writeFileSync(join(ledgerPath(dir)), body + trailing, "utf8");
  return dir;
}

// ------------------------------------------------------------------ 誰的工作機會

test("只有人的 block 開工單；機器的 block（AI 流程狀態）被拒——負控制", () => {
  const prefixes = ["repair_"];
  assert.equal(isDesignerBlock(BLOCK("moex:a:q1", "答案沒進去"), prefixes), true,
    "人（local）的 block 開工作");
  assert.equal(isDesignerBlock(MACHINE_BLOCK("moex:a:q1"), prefixes), false,
    "舊行為（任何 block 都開）會把機器狀態當設計者指示——必須拒");
  assert.equal(isDesignerBlock({ action: "accept", candidate_key: "k", reviewer: "local" }, prefixes), false);
  assert.equal(isDesignerBlock({ action: "block", reviewer: "local" }, prefixes), false,
    "沒有 candidate_key 的 block 開不出工作");
});

// ------------------------------------------------------------------ 游標語義

test("游標只吃完整行：UTF-8 位元組對齊、部分尾行留白、縮小就炸", () => {
  const queue = makeLedgerDir([BLOCK("moex:a:q1", "第一題的註解"), MACHINE_BLOCK("moex:a:q2"),
    { action: "comment", candidate_key: "moex:a:q3", notes: "只是註解", reviewer: "local" }],
    { trailing: '{"action":"block","candidate_key"' }); // 正在寫的半行
  const ledger = ledgerPath(queue);
  const first = readNewEvents(ledger, 0);
  assert.equal(first.events.length, 3, "三個完整事件都被讀到（含不含工作機會的行）");
  assert.equal(first.events[1].event.reviewer, "repair_dispute_apply",
    "機器事件也在讀到的清單裡（要不要開工是 isDesignerBlock 的事）");
  const text = readFileSync(ledger, "utf8");
  const trailing = '{"action":"block","candidate_key"';
  assert.equal(first.offset, Buffer.byteLength(text, "utf8") - Buffer.byteLength(trailing, "utf8"),
    "位移停在最後一個完整行的尾端（UTF-8 位元組，不是字元數）");

  // 補完尾行再讀：補上剩餘字元與換行，那行被完整消化。
  writeFileSync(ledger, text + ':"moex:a:q9","notes":"後補"}\n', "utf8");
  const second = readNewEvents(ledger, first.offset);
  assert.equal(second.events.length, 1, "半行補完後被讀到");
  assert.equal(second.events[0].event.candidate_key, "moex:a:q9");

  // 帳本縮小（外部重建洗掉）：游標指向未來——炸掉重吃，不猜。
  assert.throws(() => readNewEvents(ledger, Buffer.byteLength(text, "utf8") + 99), /shrank/);
});

test("首跑游標跳檔尾且留痕；--from-start 才吃歷史", () => {
  const store = join(SCRATCH, "cursor-store");
  mkdirSync(store, { recursive: true });
  const queue = makeLedgerDir([BLOCK("k", "n")]);
  assert.equal(loadCursor(store, 999).offset, 999,
    "沒有游標檔＝跳到檔尾（live 帳本已有 1,141 筆 carry 來的 block，開機全回爐是危險）");
  assert.equal(loadCursor(store, 999).from, "eof");
  saveCursor(store, { offset: 123, key: "moex:a:q1" });
  const again = loadCursor(store, 999);
  assert.equal(again.offset, 123);
  assert.equal(again.from, "cursor");
  rmSync(cursorPath(store));
  assert.equal(loadCursor(store, 999).from, "eof", "游標檔被刪＝回到檔尾語義，不是報錯");
});

// ------------------------------------------------------------------ 任務文案

test("任務文案帶 key、設計者原話與工具名；沒註解也開得成工作", () => {
  const text = composeTask(BLOCK("moex:a:q1", "答案沒進去"));
  assert.match(text, /moex:a:q1/, "鍵必須在文案裡");
  assert.match(text, /答案沒進去/, "設計者的原話必須在（不是概括轉述）");
  assert.match(text, /propose_repair/, "修法工具名在");
  assert.match(text, /record_rule_hit/, "規則命中工具名在");
  assert.match(text, /find_question|get_question/, "先看現況的指示在");
  assert.match(text, /不要硬提草案|需要設計者裁決/, "無法對應就開口的邊界在");
  assert.match(composeTask(BLOCK("moex:a:q2", "")), /沒有寫註解/, "沒註解的 block 仍然說得清楚要做什麼");
});

// ------------------------------------------------------------------ 全迴路（假 session）

test("模擬 block 事件驅動全迴路：草案落成、佇列未被寫、游標到尾", async () => {
  const store = join(SCRATCH, "loop-store");
  mkdirSync(store, { recursive: true });
  const eventAccept = { action: "accept", candidate_key: "moex:b:q1", reviewer: "local" };
  const eventBlock2 = BLOCK("moex:b:q2", "這個 sub/sup 都糊掉了");
  const eventMachine = MACHINE_BLOCK("moex:b:q3");
  const eventBlock4 = BLOCK("moex:b:q4", "");
  const json = JSON.stringify(eventAccept) + "\n" + JSON.stringify(eventBlock2) + "\n" +
    JSON.stringify(eventMachine) + "\n" + JSON.stringify(eventBlock4) + "\n";
  const queue = makeLedgerDir([]);
  const ledger = ledgerPath(queue);
  writeFileSync(ledger, json, "utf8");
  const beforeBytes = readFileSync(ledger, "utf8");

  const session = fakeSession(store, { alwaysDraft: true });
  const summary = await runConsumerLoop({
    ledger, storeDir: store, machinePrefixes: ["repair_"],
    startOffset: 0, session, identity: { run_id: "r1", session_id: "s1", prompt_version: "v1" },
    stopAfterBatch: true,
  });

  assert.equal(session.tasks.length, 2, "只有人的 block 交給 session（accept 與機器 block 都不開工）");
  assert.equal(summary.blocks, 2);
  assert.equal(summary.drafts, 2, "兩題的草案都落成");
  assert.equal(summary.questions, 0);
  assert.equal(summary.offset, Buffer.byteLength(json, "utf8"), "游標推進到帳本尾（含被跳過的行）");
  assert.equal(readFileSync(ledger, "utf8"), beforeBytes, "帳本一個位元組都沒變（消費者是讀者）");
  const log = readFileSync(join(store, "consumer.log.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(log.filter((r) => r.event === "consumed_block").length, 2, "每個工作機會都有消費日誌");
  assert.equal(existsSync(questionsPath(store)), false, "都落成了，沒有等裁決項");
});

test("沒落草案的題目進等裁決清單，且帶身分憑證（負控制：吞掉沒完成的工作）", async () => {
  const store = join(SCRATCH, "question-store");
  mkdirSync(store, { recursive: true });
  const queue = makeLedgerDir([BLOCK("moex:c:q1", "圖引用不見了")]);
  const session = fakeSession(store, { alwaysDraft: false });
  const summary = await runConsumerLoop({
    ledger: ledgerPath(queue), storeDir: store, machinePrefixes: ["repair_"],
    startOffset: 0, session, identity: { run_id: "r2", session_id: "s2", prompt_version: "v1" },
  });
  assert.equal(summary.drafts, 0);
  assert.equal(summary.questions, 1);
  const rows = readFileSync(questionsPath(store), "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(rows[0].key, "moex:c:q1");
  assert.equal(rows[0].run_id, "r2", "等裁決行帶身分（哪一輪跑出來的）");
  assert.match(rows[0].why, /沒有草案落成/);
});

test("任務失敗：游標停在失敗題之前，成功題的進度保留（重吃安全）", async () => {
  const store = join(SCRATCH, "fail-store");
  mkdirSync(store, { recursive: true });
  const queue = makeLedgerDir([BLOCK("moex:d:q1", "先成功"), BLOCK("moex:d:q2", "然後失敗")]);
  const ledger = ledgerPath(queue);
  const json1 = JSON.stringify(BLOCK("moex:d:q1", "先成功")) + "\n";

  const failing = fakeSession(store, { alwaysDraft: true, failOn: "moex:d:q2" });
  const summary = await runConsumerLoop({
    ledger, storeDir: store, machinePrefixes: ["repair_"], startOffset: 0,
    session: failing, identity: { run_id: "r3" },
  });
  assert.equal(summary.failures, 1, "失敗被記下，不是被吞");
  assert.equal(summary.offset, Buffer.byteLength(json1, "utf8"),
    "游標停在成功題的結尾＝失敗題與它之後的行，下一輪重吃");

  const retry = fakeSession(store, { alwaysDraft: true });
  const again = await runConsumerLoop({
    ledger, storeDir: store, machinePrefixes: ["repair_"], startOffset: summary.offset,
    session: retry, identity: { run_id: "r4" },
  });
  assert.equal(again.blocks, 1, "第二輪把失敗的題重吃一次");
  assert.equal(again.drafts, 1);
  assert.match(retry.tasks[0], /moex:d:q2/, "重吃的正是失敗的那題");
});

test("同一題被 block 兩次＝第二版草案（append，多版可存；計數靠 before/after 差）", async () => {
  const store = join(SCRATCH, "revise-store");
  mkdirSync(store, { recursive: true });
  const queue = makeLedgerDir([BLOCK("moex:e:q1", "第一輪註解")]);
  const ledger = ledgerPath(queue);
  const session = fakeSession(store, { alwaysDraft: true });

  const first = await runConsumerLoop({
    ledger, storeDir: store, machinePrefixes: ["repair_"], startOffset: 0,
    session, identity: { run_id: "r5" },
  });
  assert.equal(first.drafts, 1);

  const afterFirst = readFileSync(ledger, "utf8");
  writeFileSync(ledger, afterFirst +
    JSON.stringify(BLOCK("moex:e:q1", "第二輪再看，還是不對")) + "\n", "utf8");
  const second = await runConsumerLoop({
    ledger, storeDir: store, machinePrefixes: ["repair_"],
    startOffset: loadCursor(store, 0).offset, // 上一輪留下的游標
    session, identity: { run_id: "r6" },
  });
  assert.equal(second.drafts, 1, "第二個 block 也落成一版");
  assert.equal(countDrafts(store, "moex:e:q1"), 2, "同題兩版草案並存");
});

// ------------------------------------------------------------------ 單一權威

test("機器審查者前綴來自 qbr 常數層（bridge.py 同一份），且非空", async () => {
  const { PATHS } = await import("./lib/tools.mjs");
  const prefixes = await loadMachinePrefixes({
    python: PATHS.PYTHON, qbrSrc: join(PATHS.CATALOG, "qbr", "src"),
  });
  assert.ok(prefixes.length > 0, "前綴清單非空");
  assert.ok(prefixes.every((p) => typeof p === "string" && p.length > 0));
});

// ------------------------------------------------------------------ 等裁決清單的寫入形

test("appendConsumerQuestion 寫進指定的 store（append、不洗舊行）", () => {
  const store = join(SCRATCH, "q-store");
  appendConsumerQuestion(store, { key: "k1", why: "w1" });
  appendConsumerQuestion(store, { key: "k2", why: "w2" });
  const lines = readFileSync(questionsPath(store), "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(lines.length, 2);
  assert.deepEqual(lines.map((r) => r.key), ["k1", "k2"]);
  assert.ok(lines[0].at, "每行帶時間");
});