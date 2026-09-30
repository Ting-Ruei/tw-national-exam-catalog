/**
 * 工單 runner（workplan 3.4）的合約。與事件消費者同一條紀律——session 由參數注入，
 * 每個檢查帶負控制；不建真 session（模型是另一個維度的驗收：沙盒規模試跑在 workplan 記錄）。
 *
 * 負控制的地圖：
 *   工單列缺 key 被靜默跳過    → 批次「成功」但其實漏了題（拒：列號列出來炸掉）
 *   --apply 被執行             → 沙盒改了正式檔（拒：exit 2，程式沒有落地路徑）
 *   沒落草案的題目不進等裁決   → 拒收率虛低（拒）
 *   A 相同修法被算成不一致    → 空白差異動搖判斷（拒：去空白比較）
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const SCRATCH = mkdtempSync(join(tmpdir(), "workorder-test-"));

import {
  loadWorkOrder, composeWorkOrderTask, runWorkOrder, summarize,
  checkApplyFlags, appendReport, reportsPath, fixesByRun, judgeConsistency,
} from "./lib/workorder.mjs";
import { draftPath, questionsPath } from "./lib/consumer.mjs";
import { fakeSession } from "./lib/test_doubles.mjs";

function writeWorkOrder(name, items) {
  const path = join(SCRATCH, name);
  writeFileSync(path, items.map((r) => JSON.stringify(r) + "\n").join(""), "utf8");
  return path;
}

const IDENTITY = { run_id: "rw1", session_id: "sw1", prompt_version: "pv#A" };

function writeDraftRow(storeDir, row) {
  const path = draftPath(storeDir);
  const prev = existsSync(path) ? readFileSync(path, "utf8") : "";
  writeFileSync(path, prev + JSON.stringify(row) + "\n", "utf8");
}

// ------------------------------------------------------------------ 工單檔

test("工單檔讀取：空行跳過；缺 key 的行列出來炸（負控制：靜默跳過）", () => {
  const path = writeWorkOrder("wo-ok.jsonl", [
    { key: "moex:a:q1", note: "註解", lens: "superscript-family" },
    { key: "moex:a:q2", acceptance: "四格都帶 <sub>" },
  ]);
  const items = loadWorkOrder(path);
  assert.equal(items.length, 2);
  assert.equal(items[1].acceptance, "四格都帶 <sub>");

  // 壞檔直接寫原始字串（不走 helper 的二次 stringify）
  const bad = join(SCRATCH, "wo-bad.jsonl");
  writeFileSync(bad, "{}\n{\"key\":\"k\"}\nnot json\n", "utf8");
  assert.throws(() => loadWorkOrder(bad), /L1 missing key.*L3 not JSON/s,
    "半吊的工單必須停下人工修，不是安靜吃掉");
  assert.throws(() => loadWorkOrder(join(SCRATCH, "absent.jsonl")), /not found/);
});

// ------------------------------------------------------------------ 任務文案

test("工單任務文案帶 key／註解／鏡頭／驗收與工具名；無註解也成立", () => {
  const text = composeWorkOrderTask({ key: "moex:b:q1", note: "下標問題", lens: "superscript-family",
                                      acceptance: "fix 帶 <sub> 標記" });
  assert.match(text, /moex:b:q1/);
  assert.match(text, /下標問題/);
  assert.match(text, /superscript-family/);
  assert.match(text, /fix 帶 <sub> 標記/, "驗收條件隨文案走，留到報告可查");
  assert.match(text, /propose_repair|record_rule_hit/, "工具名在");
  assert.match(text, /硬提草案|需要設計者裁決/, "邊界在（粗體星號不打斷斷言）");
  const bare = composeWorkOrderTask({ key: "moex:b:q2" });
  assert.match(bare, /沒有附註解/);
  assert.match(bare, /沒有附鏡頭/);
});

test("拒收理由跟著等裁決清單走：replies 逐題回話被收集進 why 與結果行（負控制：吞掉理由）", async () => {
  const store = join(SCRATCH, "reply-store");
  mkdirSync(store, { recursive: true });
  const items = [{ key: "moex:r:q1", note: "n1", lens: "superscript-family" },
                 { key: "moex:r:q2", note: "n2", lens: "superscript-family" }];
  const session = fakeSession(store, {
    alwaysDraft: false,
    replies: ["判定：rating up，紙本為普通文字，不需要修。",
              "判定：紙本確為下標，但你看不出確切位置——需要設計者裁決。"],
  });
  const results = await runWorkOrder({ items, session, storeDir: store, identity: IDENTITY });
  assert.ok(results[0].reply.includes("rating up"), "結語隨結果行走（報告可查）");
  const questions = readFileSync(questionsPath(store), "utf8").trim().split("\n").map(JSON.parse);
  assert.match(questions[0].why, /rating up/, "第一題的拒收理由=它的結語，不是泛泛一句");
  assert.match(questions[1].why, /需要設計者裁決/);
  assert.notEqual(questions[0].why, questions[1].why, "泛泛同一句＝理由被吞，兩題不可同why");
});

test("G3 閘：--apply／--land／--write-queue／--import 一律拒絕；一般旗標放行（負控制）", () => {
  assert.deepEqual(checkApplyFlags(["--workorder", "f", "--apply"]).refused, ["--apply"]);
  assert.deepEqual(checkApplyFlags(["--land"]).refused, ["--land"]);
  assert.equal(checkApplyFlags(["--workorder", "f", "--variant", "A"]), null,
    "正常旗標要放行——閘只擋落地，不擋工作");
  assert.equal(checkApplyFlags(["--applyy"]), null, "拼錯的旗標不是落地（但也不是工作：argValue 收不到）");
});

// ------------------------------------------------------------------ 批次迴路

test("批次執行：草案落成、等裁決清單、拒絕指標（負控制：拒收不算完成）", async () => {
  const store = join(SCRATCH, "run-store");
  mkdirSync(store, { recursive: true });
  const items = [
    { key: "moex:c:q1", note: "n1", lens: "superscript-family" },
    { key: "moex:c:q2", note: "n2", lens: "superscript-family" },
    { key: "moex:c:q3", note: "n3", lens: "superscript-family" },
    { key: "moex:c:q4" }, // 同 key 重複列＝跳過記錄在案
    { key: "moex:c:q4" },
  ];
  const session = fakeSession(store, { alwaysDraft: false, }); // q1 落草案、其餘不落的形，見下
  // 讓 q1 落草案：fakeSession 依 failOn 決定，這裡改用兩個替身組合——直接自訂一個。
  let calls = 0;
  session.prompt = async (text) => {
    session.tasks.push(text);
    calls += 1;
    const key = /candidate key：(\S+)/.exec(text)?.[1];
    if (key === "moex:c:q1") {
      writeDraftRow(store, { action: "repair_draft", candidate_key: key, status: "proposed",
                             run_id: "rw1", session_id: "sw1", prompt_version: "pv#A",
                             fix: "A→MAO<sub>A</sub>", basis: "紙本裁片", insert: "option A" });
    }
    // q2、q3：連草案都不落（等裁決）
  };
  const results = await runWorkOrder({ items, session, storeDir: store, identity: IDENTITY });
  assert.equal(results.filter((r) => r.landed).length, 1);
  assert.equal(results.filter((r) => r.skipped).length, 1, "重複列不重跑，記 skipped");
  assert.equal(summarize(results).rejected, 3, "q2,q3 沒落草案；q4 跑了也沒落＝也是等裁決");
  const metrics = summarize(results);
  assert.equal(metrics.drafted, 1);
  assert.equal(metrics.rejected, 3);
  assert.equal(metrics.items, 5, "報告的 items 是工單列數（含重複列）");
  assert.equal(metrics.reject_rate, 0.75);
  assert.equal(metrics.field_fill_rate, 1, "落草案那題的三欄都齊");

  // 沒落草案的題目＝進等裁決清單，帶身分與來源。
  const questions = readFileSync(join(store, "consumer_questions.jsonl"), "utf8")
    .trim().split("\n").map(JSON.parse);
  assert.deepEqual(questions.map((r) => r.key), ["moex:c:q2", "moex:c:q3", "moex:c:q4"]);
  assert.deepEqual(questions.map((r) => r.source), ["workorder", "workorder", "workorder"]);
  assert.ok(questions.every((r) => r.run_id === "rw1" && r.prompt_version === "pv#A"));
});

test("報告落冊：append-only；fixesByRun 只認該輪 run_id 的草案行", () => {
  const store = join(SCRATCH, "report-store");
  mkdirSync(store, { recursive: true });
  writeDraftRow(store, { action: "repair_draft", candidate_key: "k0", run_id: "old-run", fix: "舊輪" });
  writeDraftRow(store, { action: "repair_draft", candidate_key: "k1", run_id: "rw2", fix: "新版修法 A" });
  assert.deepEqual(fixesByRun(store, "rw2"), { k1: "新版修法 A" });
  assert.deepEqual(fixesByRun(store, "other-run"), {}, "憑證不符的草案不進這一輪的比較");

  const p1 = appendReport(store, { kind: "batch", variant: "A", run_id: "rw2", metrics: { drafted: 1 } });
  const p2 = appendReport(store, { kind: "batch", variant: "B", run_id: "rw3", metrics: { drafted: 0 } });
  assert.equal(p1, p2, "同一冊");
  const rows = readFileSync(reportsPath(store), "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].schema, "repair_agent_test/workorder_report v1");
  assert.ok(rows[0].at, "報告行帶時間");
});

test("重判一致性：同 key 兩輪修法，去空白後相同才一致（負控制：空白差異不算不一致）", () => {
  assert.deepEqual(judgeConsistency({ k1: "A→MAO<sub>A</sub>" }, { k1: "A→MAO<sub> A</sub> " }),
    { both: 1, same: 1, rate: 1 }, "純空白差異＝同一個判斷");
  assert.deepEqual(judgeConsistency({ k1: "A→MAO<sub>A</sub>" }, { k1: "A→MAO<sub>B</sub>" }),
    { both: 1, same: 0, rate: 0 }, "實質不同＝不一致");
  assert.deepEqual(judgeConsistency({ k1: "x" }, { k2: "y" }), { both: 0, same: 0, rate: null },
    "沒有交集就誠實說 rate 無法計");
});