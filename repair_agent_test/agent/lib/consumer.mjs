/**
 * 事件消費者（workplan 3.3）的核心：把設計者的 block＋註解變成代理的工作。
 *
 * 分層：這個檔（core）**不建 session、不呼叫模型**——測試用合成帳本驅動它；
 * `consumer.mjs` 是常駐跑者，共用 `lib/session.mjs::buildSession`（同一個建構點，
 * 不另開第二個）。它**只讀佇列**：草案由 bridge 的 `propose_repair` 寫進沙盒 store
 * （append-only、可多版、寫入不等於核准），人工帳本一個位元組都不碰。
 *
 * 消費語義（量測後立下的）：
 * - 帳本 append-only，游標是「已推進到的位元組位移」，只推進過**完整行**（尾行沒有換行符
 *   ＝writer 還在寫＝不消費）。崩潰＝下一輪重吃最後一題；草案是多版可存，重吃產生的是
 *   同一題的第二版草案，不是損失。
 * - 首 run（store 裡沒有游標檔）＝**跳到檔尾**：live 帳本已有 1,141 筆 carry 過來的 block
 *   （2026-09-30 量測），開機就把歷史全部回爐既危險也沒有意義。要吃歷史時用 `--from-start`。
 * - 機器審查者清單不抄第二份：向 `qbr.review_ui.constants::REPAIR_REVIEWER_PREFIXES` 要
 *   （bridge.py 用同一個來源；兩份清單是兩個會漂移的地方）。
 */

import { readFileSync, writeFileSync, renameSync, mkdirSync, openSync, readSync,
         fstatSync, closeSync, existsSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

/** 佇列的單行決定流（bridge.py 同一個 layout contract：QUEUE_ROOT 下面的 `review-ui/`）。 */
export function ledgerPath(queueRoot) {
  return join(queueRoot, "review-ui", "question_review_events.jsonl");
}

/** 機器審查者前綴清單——qbr 常數層的**單一權威**，不抄進這個沙盒。 */
export async function loadMachinePrefixes({ python, qbrSrc }) {
  const env = { ...process.env, PYTHONPATH: qbrSrc };
  const { stdout } = await run(python, ["-c",
    "import json; from qbr.review_ui.constants import REPAIR_REVIEWER_PREFIXES; " +
    "print(json.dumps(sorted(REPAIR_REVIEWER_PREFIXES)))"], { env });
  return JSON.parse(stdout.trim());
}

/**
 * 一個事件是否該開出一筆代理工作：**人**（非機器前綴）的 `block`，指向一個真候選。
 * 負控制就是這條的反面：機器的 block（AI 自己記的流程狀態）不能驅動代理改稿——
 * 那是自己看自己的字、開自己的工單。
 */
export function isDesignerBlock(event, machinePrefixes = []) {
  if (!event || event.action !== "block" || !event.candidate_key) return false;
  const reviewer = String(event.reviewer || "");
  return !machinePrefixes.some((prefix) => reviewer.startsWith(prefix));
}

/**
 * 從 `offset` 讀新事件，回 `{ events: [{event}…], offset }`。
 *
 * `events` 是**全部**新行（含機器事件——游標要吃過它們，不消費就會永遠重讀）；
 * 「要不要開工」是 `isDesignerBlock` 的事。位移算的是 UTF-8 位元組，不是字元數，
 * 也不假設行數；帳本若縮小（外部重建）就炸掉重吃，不猜。
 */
export function readNewEvents(path, offset) {
  if (!existsSync(path) ) {
    return { events: [], offset };
  }
  const fd = openSync(path, "r");
  try {
    const size = fstatSync(fd).size;
    if (offset > size) {
      throw new Error(`ledger shrank under the cursor (${offset} > ${size}); restart from a fresh cursor`);
    }
    if (offset >= size) return { events: [], offset };
    const buf = Buffer.alloc(size - offset);
    readSync(fd, buf, 0, buf.length, offset);
    const text = buf.toString("utf8");
    const lines = text.split("\n");
    const trailing = lines.pop(); // 沒有尾隨換行＝可能還在寫；只推進完整行
    const events = [];
    let consumed = offset;
    for (const line of lines) {
      consumed += Buffer.byteLength(line, "utf8") + 1;
      const trimmed = line.trim();
      if (!trimmed) continue;
      let event = null;
      try { event = JSON.parse(trimmed); } catch { event = null; } // 壞行跳過，不讓毒行卡住游標
      events.push({ event, end: consumed });
    }
    return { events, offset: consumed };
  } finally {
    closeSync(fd);
  }
}

/** 游標檔：`{offset, at, …}`。寫入 tmp+rename 傳統（lessons 同款）。 */
export function cursorPath(storeDir) { return join(storeDir, "consumer_cursor.json"); }

/**
 * 首跑游標：store 裡沒有游標檔時**跳到檔尾**（見檔頭註解），但這個決定要留痕——
 * `from: "eof"` 記在回傳值裡，跑者必須把它寫進消費日誌，讓「我為什麼沒看到歷史」可回答。
 */
export function loadCursor(storeDir, ledgerSize) {
  const path = cursorPath(storeDir);
  if (!existsSync(path)) return { offset: ledgerSize, from: "eof" };
  try {
    const raw = JSON.parse(readFileSync(path, "utf8"));
    return { offset: Math.max(0, raw.offset | 0), from: "cursor" };
  } catch {
    return { offset: ledgerSize, from: "eof" };
  }
}

export function saveCursor(storeDir, snapshot) {
  mkdirSync(storeDir, { recursive: true });
  const path = cursorPath(storeDir);
  const tmp = path + ".tmp";
  writeFileSync(tmp, JSON.stringify({ at: new Date().toISOString(), ...snapshot }) + "\n", "utf8");
  renameSync(tmp, path);
}

/**
 * 任務文案。系統提示（identity::systemPrompt）已帶規則家與教訓，這裡只帶**這一題的**事實：
 * 鍵、設計者的原話、以及「先看真證據才提案」的約束。工具名直接寫出，讓任務文案本身
 * 就是可測的合約（改工具名而漏改這裡＝測紅）。
 */
export function composeTask(event) {
  const note = String(event.notes || "").trim();
  return [
    `設計者封鎖（block）了這一題，要你去看。`,
    `candidate key：${event.candidate_key}`,
    `設計者的註解：${note || "（沒有寫註解——以題目本身為準，判斷抽取哪裡錯了）"}`,
    ``,
    `程序：`,
    `1. 先用 find_question/get_question 看這一題的現況（抽取的文字、refs、狀態），需要紙本證據就 crop_question。`,
    `2. 對照設計者的註解，判斷缺陷是哪一類；哪條原則幫你看到或決定了什麼，用 record_rule_hit 記它的編號（沒用到就不要記）。`,
    `3. 只有在你**親眼看過證據**（紙本裁片或明確的字）之後才 propose_repair 提草案；fix／insert／basis 三欄都要寫實際內容。`,
    `4. 若註解無法對應到任何原則、或你看不出缺陷在哪，就**不要**硬提草案——明說「需要設計者裁決」與理由。`,
  ].join("\n");
}

/** 沙盒內的「等設計者裁決」清單：草案沒落成的題目，跑者記它在案。 */
export function questionsPath(storeDir) { return join(storeDir, "consumer_questions.jsonl"); }

export function appendConsumerQuestion(storeDir, row) {
  mkdirSync(storeDir, { recursive: true });
  const path = questionsPath(storeDir);
  const tmp = path + ".tmp";
  const prev = existsSync(path) ? readFileSync(path, "utf8") : "";
  writeFileSync(tmp, prev + JSON.stringify({ at: new Date().toISOString(), ...row }) + "\n", "utf8");
  renameSync(tmp, path);
  return path;
}

/** 讀草案檔裡某一題已有幾筆（只讀；草案行帶 candidate_key 與 status=proposed）。 */
export function countDrafts(storeDir, key) {
  const path = draftPath(storeDir);
  if (!existsSync(path)) return 0;
  let count = 0;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      if (JSON.parse(line).candidate_key === key) count += 1;
    } catch { /* 壞行不算數 */ }
  }
  return count;
}

export function draftPath(storeDir) { return join(storeDir, "repair_drafts.jsonl"); }

/**
 * 逐題回話收集器。試跑量到的缺口（2026-09-30）：代理**拒收**時，理由它都講了——但只在
 * session 轉錄裡；等裁決清單只剩「沒有草案落成」，設計者看不到為什麼。收集器把每次
 * `session.prompt()` 完成時的最新助手結語帶出來，讓「為什麼不修」跟著工作一起落地。
 */
export function attachReplyCollector(session) {
  const state = { latest: "", events: 0 };
  const unsubscribe = session.subscribe?.((event) => {
    if (event?.type === "message_end" && event.message?.role === "assistant") {
      const content = event.message.content || [];
      const text = content
        .filter((part) => part && part.type === "text")
        .map((part) => part.text || "")
        .join("");
      if (text.trim()) {
        state.latest = text.trim();
        state.events += 1;
      }
    }
  });
  return {
    latest: () => state.latest,
    events: () => state.events,
    reset: () => { state.latest = ""; },
    detach: () => { if (unsubscribe) unsubscribe(); },
  };
}

/** 消費日誌（沙盒內）：每一題吃進與結束都留一行，讓「為什麼這一題沒草稿」可回查。 */
export function appendConsumerLog(storeDir, row) {
  mkdirSync(storeDir, { recursive: true });
  const path = join(storeDir, "consumer.log.jsonl");
  appendFileSync(path, JSON.stringify({ at: new Date().toISOString(), ...row }) + "\n", "utf8");
}

/**
 * 消費迴路本體（跑者與測試共用；session 以參數注入——測試假 session、跑者真 session）。
 *
 * - 佇列只讀：游標、草案、日誌、等裁決清單全部落在 `storeDir`。
 * - 每個人的 block：記日誌 → `session.prompt(composeTask(event))` → 對草案計數（落成？）。
 *   沒落成＝記進等裁決清單；失敗＝游標停在它之前，下一輪重吃（草案是多版可存，重吃安全）。
 * - 全部新行吃完才推進到批尾（非 block 行是被看過而跳過，不是漏看）。
 *
 * @param {object} args
 * @param {string} ledger    帳本路徑（只讀）
 * @param {string} storeDir  沙盒 store（游標／草案／日誌的家）
 * @param {string[]} machinePrefixes 機器審查者前綴（單一權威讀進來的）
 * @param {number} startOffset 從哪個位元組開始
 * @param {object} session    有 `.prompt(text)` 的物件（Pi session 或測試替身）
 * @param {"block"|"non-block"}…
 * @param {object} [identity] 身分憑證（run_id/session_id/prompt_version）記進等裁決行
 * @param {boolean} [stopAfterBatch] true＝吃完現有事件就回（--once）
 * @param {number} [intervalMs] 常駐輪詢間隔
 * @param {() => boolean} [shouldStop] 外部停訊（SIGINT）；測試用它斷開常駐
 */
export async function runConsumerLoop({ ledger, storeDir, machinePrefixes = [], startOffset,
                                        session, identity = {}, stopAfterBatch = true,
                                        intervalMs = 15_000, shouldStop = () => false }) {
  const summary = { blocks: 0, consumed: 0, drafts: 0, questions: 0, failures: 0, offset: startOffset };
  const cursor = { offset: startOffset };
  const collector = attachReplyCollector(session);
  appendConsumerLog(storeDir, {
    event: "consumer_start", ledger, cursor_offset: startOffset,
    machine_prefixes: machinePrefixes.length, ...identity,
  });

  for (;;) {
    if (shouldStop()) break;
    const { events, offset } = readNewEvents(ledger, cursor.offset);
    const end = events.length ? events[events.length - 1].end : cursor.offset;
    const blocks = events.filter((row) => isDesignerBlock(row.event, machinePrefixes))
                         .filter((row) => row.event && row.event.candidate_key);

    let safe = cursor.offset;
    let failed = false;
    for (const row of blocks) {
      if (shouldStop()) break;
      const key = row.event.candidate_key;
      const note = String(row.event.notes || "").trim();
      summary.blocks += 1;
      const before = countDrafts(storeDir, key);
      appendConsumerLog(storeDir, {
        event: "consumed_block", key, notes: note,
        reviewer: row.event.reviewer || "", byte: row.end, drafts_before: before,
      });
      await session.prompt(composeTask(row.event)).catch((error) => {
        appendConsumerLog(storeDir, { event: "task_error", key, error: String(error.message).slice(0, 600) });
        summary.failures += 1;
        failed = true;
      });
      if (failed) break; // 游標停在它之前，下一輪重吃（草案是多版可存，重吃安全）
      const after = countDrafts(storeDir, key);
      const reply = collector.latest();
      if (after > before) {
        summary.drafts += 1;
        appendConsumerLog(storeDir, { event: "task_done", key, drafts_after: after, landed: true });
      } else {
        summary.questions += 1;
        appendConsumerQuestion(storeDir, {
          key, notes: note,
          why: reply ? "代理跑完任務但沒有草案落成——它的結語：" + reply.slice(0, 800)
                     : "代理跑完任務但沒有草案落成、也沒有結語——需要設計者裁決",
          ...identity,
        });
        appendConsumerLog(storeDir, { event: "task_done", key, drafts_after: after, landed: false,
                                      reply_chars: reply.length });
      }
      collector.reset();
      safe = row.end;
      saveCursor(storeDir, { offset: safe, key, action: "block" });
    }

    if (!failed && end > safe) {
      saveCursor(storeDir, { offset: end, through: blocks.length ? "block" : "non-block" });
      safe = end;
    }
    summary.consumed += events.length;
    cursor.offset = safe;
    if (events.length) appendConsumerLog(storeDir, { event: "advanced", offset: safe });
    if (stopAfterBatch || shouldStop()) break;
    if (!events.length) await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  summary.offset = cursor.offset;
  appendConsumerLog(storeDir, { event: "consumer_end", ...summary });
  return summary;
}