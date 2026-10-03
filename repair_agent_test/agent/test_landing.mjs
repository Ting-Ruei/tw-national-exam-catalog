/**
 * 文字落地與 ✅ 的契約（L3 後端）。每個檢查帶負對照——舊行為下必須失敗的那一例。
 *
 * Run: `node --test test_landing.mjs`
 *
 * 鐵律（REPAIR_LOOP_PLAN.md §3）在此被逐條逼問：
 *   ✅ 的當下＝已寫入（accept_and_apply 一次完成；驗證不過一個 byte 都不寫）
 *   未 ✅ 的 key 落地被逐鍵拒絕（授權是帳本裡那一筆 sandbox accept）
 *   fix 空／等於現值／insert 認不得 → 拒絕，不是猜
 *   人的 block 理由進經驗；機器列不冒充人
 *   pending 一個數字回答「積幾題等我」；機器 accept 不算人的話
 *
 * 全部跑在**沙盒佇列**上（REPAIR_AGENT_QUEUE 指到 scratch），真佇列一個 byte 都不碰。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFileSync, writeFileSync, mkdirSync, mkdtempSync, existsSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const run = promisify(execFile);
const AGENT_DIR = dirname(fileURLToPath(import.meta.url));
const SERVER = join(AGENT_DIR, "ui", "server.py");
const PY = process.env.REPAIR_TEST_PYTHON || "python3";

const SCRATCH = mkdtempSync(join(tmpdir(), "landing-test-"));
const QUEUE = join(SCRATCH, "queue");
const REVIEW = join(QUEUE, "review-ui");
const STORE = join(SCRATCH, "store");
mkdirSync(REVIEW, { recursive: true });
mkdirSync(STORE, { recursive: true });

const LEDGER = join(REVIEW, "question_review_events.jsonl");
writeFileSync(LEDGER, "", "utf8");

const KEY_STEM = "moex:t:311:0704:1:question:q001";
const KEY_ANSWER = "moex:t:311:0704:1:question:q002";
const KEY_OPTION = "moex:t:311:0704:1:question:q003";
const KEY_FRESH = "moex:t:311:0704:1:question:q004";
const KEY_STALE = "moex:t:311:0704:1:question:q005";
const KEY_MACHINE = "moex:t:311:0704:1:question:q006";
const KEY_VIEW = "moex:t:311:0704:1:question:q008";
const KEY_NOACCEPT = "moex:t:311:0704:1:question:q007";

const STEM = "下列有關一般人過度換氣（hyperventilation）之敘述，何者正確？";
const ROWS = [
  { candidate_key: KEY_STEM, question_number: 1, stem: STEM,
    options: [{ key: "A", text: "甲" }, { key: "B", text: "乙" }], answer: "A",
    answer_payload: { answer: "A", raw_answer: "A", accepted_values: ["A"] },
    metadata: { normalized_subject_name: "物理治療基礎學" } },
  { candidate_key: KEY_ANSWER, question_number: 2, stem: "第二題", options: [], answer: "A",
    answer_payload: { answer: "A", raw_answer: "A", accepted_values: ["A"] }, metadata: {} },
  { candidate_key: KEY_OPTION, question_number: 3, stem: "第三題",
    options: [{ key: "A", text: "舊選項" }, { key: "B", text: "乙" }], answer: "A", metadata: {} },
  { candidate_key: KEY_FRESH, question_number: 4, stem: "第四題", options: [], answer: "A", metadata: {} },
  { candidate_key: KEY_STALE, question_number: 5, stem: "第五題", options: [], answer: "A", metadata: {} },
  { candidate_key: KEY_MACHINE, question_number: 6, stem: "第六題", options: [], answer: "A", metadata: {} },
  { candidate_key: KEY_NOACCEPT, question_number: 7, stem: "第七題", options: [], answer: "A", metadata: {} },
  { candidate_key: KEY_VIEW, question_number: 8, stem: "第八題", options: [], answer: "A", metadata: {} },
];
writeFileSync(join(REVIEW, "candidates.jsonl"),
  ROWS.map((r) => JSON.stringify(r)).join("\n") + "\n", "utf8");

/** 一列人類事件（reviewer: local）或機器事件（帶 repair_ 前綴）追加上去。 */
function ledgerEvent(row) {
  const prev = readFileSync(LEDGER, "utf8");
  writeFileSync(LEDGER, prev + JSON.stringify(row) + "\n", "utf8");
}

/** 一列草案寫進沙盒 store，回傳它的行 sha256（裁決事件指涉的那個不可變標的）。 */
function seedDraft(key, { fix, insert, at }) {
  const row = { action: "repair_draft", schema: "repair_agent_test/repair_drafts v1",
    candidate_key: key, question_number: Number(key.slice(-3)), subject: "物理治療基礎學",
    fix, insert, basis: "紙本第 3 行", crop: null, status: "proposed", at };
  const line = JSON.stringify(row) + "\n";
  const path = join(STORE, "repair_drafts.jsonl");
  const prev = existsSync(path) ? readFileSync(path, "utf8") : "";
  writeFileSync(path, prev + line, "utf8");
  return createHash("sha256").update(line.replace(/\n$/, "")).digest("hex");
}

const ENV = () => ({ ...process.env,
  REPAIR_AGENT_QUEUE: QUEUE, REPAIR_AGENT_STORE: STORE, REPAIR_AGENT_NO_INDEX: "1" });

/** 跑一段用真 server module 的 Python；回傳解析後的 stdout。 */
async function drive(body) {
  const script = [
    "import importlib.util, json, sys, traceback",
    "spec = importlib.util.spec_from_file_location('ui_server', sys.argv[1])",
    "mod = importlib.util.module_from_spec(spec)",
    "spec.loader.exec_module(mod)",
    "try:",
    `    print(json.dumps(eval(sys.argv[2]), ensure_ascii=False))`,
    "except Exception as error:",
    "    print(json.dumps({'__error__': type(error).__name__, 'message': str(error)}, ensure_ascii=False))",
  ].join("\n");
  const { stdout } = await run(PY, ["-c", script, SERVER, body],
    { timeout: 120_000, maxBuffer: 16 * 1024, env: ENV() });
  const out = JSON.parse(stdout.trim().split("\n").pop());
  if (out && out.__error__) { const e = new Error(out.message); e.name = out.__error__; throw e; }
  return out;
}

const candidates = () => readFileSync(join(REVIEW, "candidates.jsonl"), "utf8")
  .split("\n").filter(Boolean).map((l) => JSON.parse(l));
const rowOf = (key) => candidates().find((r) => r.candidate_key === key);

// ---------------------------------------------------------------- ✅ 即寫入

test("✅ 即寫入：一個動作寫齊帳本 accept＋candidates 整欄＋reset_review＋manifest＋經驗（負對照：分兩段或漏任何一件）", async () => {
  ledgerEvent({ action: "block", candidate_key: KEY_STEM, reviewer: "local",
    notes: "題幹第 3 行錯字", created_at: "2026-10-03T09:00:00.000Z" });
  const sha = seedDraft(KEY_STEM,
    { fix: STEM.replace("一般人", "健康成人"), insert: "題幹", at: "2026-10-03T10:00:00.000Z" });

  const out = await drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_STEM)}, 'draft_sha256': '${sha}'})`);
  assert.equal(out.wrote, true, "✅ 的當下就是已寫入，不是「判通了再按寫入」");
  assert.deepEqual(out.applied, [KEY_STEM]);

  // candidates：整欄替換真的發生
  assert.equal(rowOf(KEY_STEM).stem, STEM.replace("一般人", "健康成人"));

  // 帳本：sandbox accept（帶草案 sha）＋ reset_review（機器前綴、原值在 changes[].from）
  const ledger = readFileSync(LEDGER, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const accept = ledger.find((r) => r.action === "accept");
  assert.equal(accept.repair_draft_sha256, sha, "驗收事件指到不可變的草案行");
  assert.equal(accept.source, "sandbox_accept");
  const reset = ledger.find((r) => r.action === "reset_review");
  assert.equal(reset.reviewer, "repair_text_landing", "機器列帶機器前綴，不冒充人");
  assert.equal(reset.changes[0].field, "stem");
  assert.equal(reset.changes[0].from, STEM, "原值保存在 changes[].from（只增不刪）");
  assert.equal(reset.changes[0].to, STEM.replace("一般人", "健康成人"));

  // manifest：before/after sha256＋backup 在磁碟上
  const manifest = JSON.parse(readFileSync(join(STORE, "text_landing.jsonl"), "utf8").trim());
  assert.equal(manifest.applied[0], KEY_STEM);
  assert.ok(manifest.sha_before && manifest.sha_after && manifest.sha_before !== manifest.sha_after);
  assert.ok(manifest.drafts[KEY_STEM] === sha, "manifest 記下是哪一版草案落地");
  assert.ok(existsSync(manifest.backup), "備份真的存在");
  assert.equal(readdirSync(join(QUEUE, "backups")).length >= 1, true);

  // 經驗入庫：你的 block 理由是「錯誤：」半句
  const lesson = JSON.parse(readFileSync(join(STORE, "lessons.jsonl"), "utf8").trim());
  assert.match(lesson.text, /錯誤：題幹第 3 行錯字 → 修法：/);
  assert.equal(lesson.actor, "human:designer");
  assert.equal(lesson.kind, "approved-repair");
});

// ---------------------------------------------------------------- §7 view 一致性

test("§7：✅ 之後 /api/question 同 key 立即反映新值（含熱快取的第二讀）——不存在舊 view 的窗口", async () => {
  ledgerEvent({ action: "block", candidate_key: KEY_VIEW, reviewer: "local",
    notes: "第八題錯字", created_at: "2026-10-03T12:00:00.000Z" });
  const sha = seedDraft(KEY_VIEW,
    { fix: "第八題（新）", insert: "題幹", at: "2026-10-03T12:30:00.000Z" });
  const out = await drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_VIEW)}, 'draft_sha256': '${sha}'})`);
  assert.equal(out.wrote, true);

  // 同一個 server module（熱快取）連讀兩次：apply 的 os.replace 改了 mtime，
  // index guard 每 call 都 stat——兩讀都必須是新值，不是第一讀新、第二讀回舊。
  // （合成列沒有 PDF，把 pdf_path_of stub 成空字串——view 的快取/judgements/trace 路徑全是真的。）
  const expr = `[mod.bridge.__dict__.update({'pdf_path_of': lambda q: ''}), mod.question_payload(${JSON.stringify(KEY_VIEW)})][1]`;
  const view1 = await drive(expr);
  const view2 = await drive(expr);
  for (const [label, view] of [["第一讀", view1], ["熱快取第二讀", view2]]) {
    assert.equal(view.stem, "第八題（新）", `${label}反映 ✅ 後的題幹`);
  }
});

test("未 ✅ 的 key 落地逐鍵拒絕（負對照：沒有帳本授權也寫得進去）", async () => {
  const out = await drive(`mod._apply_text_drafts([${JSON.stringify(KEY_NOACCEPT)}])`);
  assert.equal(out.wrote, false);
  assert.match(out.skipped[0].reason, /未 accept/);
  assert.equal(rowOf(KEY_NOACCEPT).stem, "第七題", "candidates 一個 byte 都沒動");
});

test("無 draft sha／草案不存在的 ✅ 拒絕，且帳本不動（負對照：憑空 accept）", async () => {
  const before = readFileSync(LEDGER, "utf8");
  await assert.rejects(drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_STEM)}, 'draft_sha256': ''})`),
    /checksum/);
  await assert.rejects(drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_STEM)}, 'draft_sha256': '${"0".repeat(64)}'})`),
    /checksum/);
  assert.equal(readFileSync(LEDGER, "utf8"), before, "驗證不過就整個拒絕——帳本一列不多");
});

test("fix 等於現值的 ✅ 拒絕（負對照：沒有東西可寫也算成功）", async () => {
  const before = readFileSync(LEDGER, "utf8");
  const sha = seedDraft(KEY_ANSWER, { fix: "第二題", insert: "題幹", at: "2026-10-03T10:05:00.000Z" });
  await assert.rejects(drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_ANSWER)}, 'draft_sha256': '${sha}'})`),
    /現值/);
  assert.equal(readFileSync(LEDGER, "utf8"), before);
});

test("insert 指向不存在的選項／欄位拒絕（負對照：靜默猜錯格）", async () => {
  const before = readFileSync(LEDGER, "utf8");
  const shaE = seedDraft(KEY_OPTION, { fix: "A: 無所謂", insert: "選項 E", at: "2026-10-03T10:06:00.000Z" });
  await assert.rejects(drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_OPTION)}, 'draft_sha256': '${shaE}'})`),
    /選項/);
  const shaF = seedDraft(KEY_OPTION, { fix: "無所謂", insert: "不存在的欄位", at: "2026-10-03T10:07:00.000Z" });
  await assert.rejects(drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_OPTION)}, 'draft_sha256': '${shaF}'})`),
    /欄位/);
  assert.equal(readFileSync(LEDGER, "utf8"), before);
});

test("fix 是片段（不含原題幹開頭）拒絕——預覽與落地同一條守門（負對照：整題被建議文字毀掉）", async () => {
  const before = readFileSync(LEDGER, "utf8");
  const sha = seedDraft(KEY_STEM, { fix: "把「一般人」改成「健康成人」", insert: "題幹",
    at: "2026-10-03T10:08:00.000Z" });
  await assert.rejects(drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_STEM)}, 'draft_sha256': '${sha}'})`),
    /完整題幹/);
  assert.equal(readFileSync(LEDGER, "utf8"), before);
  assert.equal(rowOf(KEY_STEM).stem, STEM.replace("一般人", "健康成人"), "已落地的題目不受影響");
});

test("選項與答案的整欄替換：options[i].text／answer＋answer_payload 同步（負對照：payload 留舊值自相矛盾）", async () => {
  const shaOpt = seedDraft(KEY_OPTION, { fix: "A: 新的選項文字", insert: "選項 A", at: "2026-10-03T10:10:00.000Z" });
  const out = await drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_OPTION)}, 'draft_sha256': '${shaOpt}'})`);
  assert.equal(out.wrote, true);
  assert.equal(rowOf(KEY_OPTION).options[0].text, "新的選項文字");
  const reset = readFileSync(LEDGER, "utf8").split("\n").filter(Boolean)
    .map((l) => JSON.parse(l)).filter((r) => r.candidate_key === KEY_OPTION && r.action === "reset_review").pop();
  assert.equal(reset.changes[0].field, "options[A].text");
  assert.equal(reset.changes[0].from, "舊選項");

  const shaAns = seedDraft(KEY_ANSWER, { fix: "B", insert: "答案", at: "2026-10-03T10:11:00.000Z" });
  await drive(`mod.accept_and_apply({'key': ${JSON.stringify(KEY_ANSWER)}, 'draft_sha256': '${shaAns}'})`);
  const row = rowOf(KEY_ANSWER);
  assert.equal(row.answer, "B");
  assert.equal(row.answer_payload.answer, "B");
  assert.deepEqual(row.answer_payload.accepted_values, ["B"], "payload 不留舊值");
});

// ---------------------------------------------------------------- pending

test("pending：新草案＋1、機器 accept 不算人的話、舊於人類 accept 的草案不算（負對照：全題都進清單）", async () => {
  // KEY_FRESH：人 block 過、草案在其後 → pending
  seedDraft(KEY_FRESH, { fix: "第四題（改）", insert: "題幹", at: "2026-10-03T11:00:00.000Z" });
  // KEY_STALE：人類 accept 在草案**之後** → 不算
  seedDraft(KEY_STALE, { fix: "第五題（舊草案）", insert: "題幹", at: "2026-10-03T08:00:00.000Z" });
  ledgerEvent({ action: "accept", candidate_key: KEY_STALE, reviewer: "local",
    created_at: "2026-10-03T09:00:00.000Z" });
  // KEY_MACHINE：只有機器 accept（晚於草案）→ 機器列不覆寫人的話，仍 pending
  seedDraft(KEY_MACHINE, { fix: "第六題（改）", insert: "題幹", at: "2026-10-03T08:00:00.000Z" });
  ledgerEvent({ action: "accept", candidate_key: KEY_MACHINE, reviewer: "repair_bot",
    created_at: "2026-10-03T09:00:00.000Z" });

  const out = await drive("mod.pending_state()");
  const kinds = Object.fromEntries(out.items.map((it) => [it.key, it.kind]));
  assert.equal(kinds[KEY_FRESH], "text", "新草案進清單");
  assert.equal(kinds[KEY_STALE], undefined, "晚於人類 accept 的草案是過期提案，不進");
  assert.equal(kinds[KEY_MACHINE], "text", "機器列不是人的話——機器 accept 不把草案變舊");
  assert.equal(out.count, out.items.length, "一個數字＝清單長度");
  const fresh = out.items.find((it) => it.key === KEY_FRESH);
  assert.ok(fresh.draft_sha256, "清單列帶草案 sha（✅ 事件要指著它）");
});

test("pending：圖草案 ruling=pass 未落地與未判都進清單；return 的不進", async () => {
  // 直接以生產者流形狀餵兩題：ruling 由學習檔（agent_feedback）的 figure_review 決定。
  const runDir = join(STORE, "scans", "20261001-figure-producer");
  mkdirSync(runDir, { recursive: true });
  const woDir = join(STORE, "workorders");
  mkdirSync(woDir, { recursive: true });
  const figKeyA = "moex:t:311:0704:1:question:q101"; // 未判
  const figKeyB = "moex:t:311:0704:1:question:q102"; // pass 未落地
  const figKeyC = "moex:t:311:0704:1:question:q103"; // return → 不進
  for (const [key, tag] of [[figKeyA, "A"], [figKeyB, "A"], [figKeyC, "A"]]) {
    const row = { schema: "figure_missing_drafts v1", at: "2026-10-03T12:00:00.000Z", tag,
      candidate_key: key, record: { decision: "insert", refs: ["1152_q101-1.png"], where: "題幹後", basis: "紙本" } };
    const path = join(runDir, `figure_drafts_run${tag}.jsonl`);
    const prev = existsSync(path) ? readFileSync(path, "utf8") : "";
    writeFileSync(path, prev + JSON.stringify(row) + "\n", "utf8");
  }
  // 學習檔：q102 判 pass、q103 判 return
  const fb = join(STORE, "agent_feedback.jsonl");
  const verdict = (key, review) => JSON.stringify({ at: "2026-10-03T12:30:00.000Z", action: "ai_feedback",
    candidate_key: key, rating: review === "pass" ? "up" : "down",
    extras: { figure_review: review } }) + "\n";
  writeFileSync(fb, verdict(figKeyB, "pass") + verdict(figKeyC, "return"), "utf8");

  const out = await drive("mod.pending_state()");
  const kinds = Object.fromEntries(out.items.map((it) => [it.key, it.kind]));
  assert.equal(kinds[figKeyA], "figure_new", "未判的圖草案進清單");
  assert.equal(kinds[figKeyB], "figure_pass", "判通過未落地的進清單");
  assert.equal(kinds[figKeyC], undefined, "判退回的不進");
});

test("反覆退回：第 3 次起清單帶 returns≥3（負對照：次數被吞掉、無限迴圈沒訊號）", async () => {
  const fb = join(STORE, "agent_feedback.jsonl");
  const ret = (key, at) => JSON.stringify({ at, action: "repair_return", candidate_key: key,
    rating: "down", reason: "還是錯", engine: "human:designer", source: "designer" }) + "\n";
  for (const at of ["2026-10-03T13:00:00.000Z", "2026-10-03T13:10:00.000Z", "2026-10-03T13:20:00.000Z"]) {
    const prev = readFileSync(fb, "utf8");
    writeFileSync(fb, prev + ret(KEY_FRESH, at), "utf8");
  }
  // 第 3 次退回**之後**的新草案：pending 且帶著 3 次退回的次數（畫面據此標「反覆退回」）。
  // 舊草案（13:00 前）已過期——清單只收晚於最後一次人類說話的提案。
  seedDraft(KEY_FRESH, { fix: "第四題（第三輪修正）", insert: "題幹", at: "2026-10-03T13:30:00.000Z" });
  const out = await drive("mod.pending_state()");
  const fresh = out.items.find((it) => it.key === KEY_FRESH);
  assert.ok(fresh, "退回後的新草案進清單");
  assert.equal(fresh.returns, 3);
});
