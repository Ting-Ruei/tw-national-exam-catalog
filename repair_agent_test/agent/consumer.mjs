#!/usr/bin/env node
/**
 * 常駐事件消費者（workplan 3.3）——把設計者的 block＋註解變成代理自動出的草案。
 *
 *   node consumer.mjs --once          # 吃完現有事件就結束（隔離驗收、排程用）
 *   node consumer.mjs --watch        # 常駐：每 15 秒看一次帳本
 *   node consumer.mjs --watch 5      # 常駐：自訂輪詢秒數
 *   node consumer.mjs --from-start --once   # 從頭吃歷史 block（明確要求才做）
 *
 * 邊界：
 * - 佇列**唯讀**：candidates、帳本、裁片一個位元組都不寫。草案落在沙盒 store 的
 *   `repair_drafts.jsonl`（bridge 的 propose_repair 寫，append-only、status=proposed、
 *   帶 run/session/prompt_version 憑證）；沒有草案落成的題目記進 `consumer_questions.jsonl`
 *   等設計者裁決。
 * - 迴路本體在 `lib/consumer.mjs::runConsumerLoop`（session 由參數注入）——這裡只負責
 *   進入點：旗標、路徑、身分打點、與**同一個** session builder（不另開第二個建構點）。
 * - 首跑游標跳檔尾（lib/consumer.mjs 有量測理由）；`--from-start` 才吃歷史。
 */

import { statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { STORE_DIR, PROMPT_VERSION } from "./lib/identity.mjs";
import { PATHS } from "./lib/tools.mjs";
import { buildSession, BRAIN } from "./lib/session.mjs";
import {
  ledgerPath, loadMachinePrefixes, runConsumerLoop, loadCursor,
} from "./lib/consumer.mjs";

const HERE = fileURLToPath(new URL(".", import.meta.url)).replace(/\/$/, "");
const DEFAULT_QUEUE = join(PATHS.CATALOG, "qbr", "data", "review-queues", "live");
const POLL_SECONDS = 15;

async function main() {
  const argv = process.argv.slice(2);
  const once = argv.includes("--once");
  const fromStart = argv.includes("--from-start");
  const watchIndex = argv.indexOf("--watch");
  const interval = watchIndex >= 0 && argv[watchIndex + 1] && /^\d+$/.test(argv[watchIndex + 1])
    ? Number(argv[watchIndex + 1]) : POLL_SECONDS;

  // 憑證打點與 agent.mjs 同款：bridge 在**寫入時**讀這些環境變數，草案才帶得出身分。
  process.env.REPAIR_AGENT_RUN_ID = `consumer-${new Date().toISOString()}-${process.pid}`;
  process.env.REPAIR_AGENT_PROMPT_VERSION = PROMPT_VERSION;

  const queueRoot = process.env.REPAIR_AGENT_QUEUE || DEFAULT_QUEUE;
  const ledger = ledgerPath(queueRoot);

  let machinePrefixes = [];
  try {
    machinePrefixes = await loadMachinePrefixes({
      python: PATHS.PYTHON,
      // qbr.review_ui 的家（qbr/src）；bridge.py 自己也這樣接上。
      qbrSrc: join(PATHS.CATALOG, "qbr", "src"),
    });
  } catch (error) {
    console.error(`[consumer] 拿不到機器審查者前綴清單（${error.message.split("\n")[0]}）——` +
      `拒絕在沒有這份清單時消費：把機器的 block 當人的，是自己開自己的工單。`);
    return 3;
  }

  const ledgerStat = statSync(ledger, { throwIfNoEntry: false });
  if (!ledgerStat?.isFile()) {
    console.error(`[consumer] 帳本不在：${ledger}——先 sync（scripts/sync_from_station.sh）`);
    return 2;
  }

  const cursor = fromStart
    ? { offset: 0, from: "from-start" }
    : loadCursor(STORE_DIR, ledgerStat.size);
  console.error(`[consumer] queue=${queueRoot}`);
  console.error(`[consumer] store=${STORE_DIR}`);
  console.error(`[consumer] 從 offset=${cursor.offset} 開始（${cursor.from}；帳本 ${ledgerStat.size} bytes）`);

  const identity = {
    run_id: process.env.REPAIR_AGENT_RUN_ID,
    session_id: "",                       // session 建好後補
    prompt_version: PROMPT_VERSION,
  };

  const manager = SessionManager.create(HERE);
  let session;
  try {
    session = (await buildSession({ sessionManager: manager })).session;
  } catch (error) {
    console.error(`[consumer] session 建不起來：${error.message.split("\n")[0]}`);
    return error.code === "MODEL_NOT_FOUND" ? 4 : 1;
  }
  identity.session_id = manager.getSessionId?.() || "";
  process.env.REPAIR_AGENT_SESSION_ID = identity.session_id;

  let stop = false;
  const onSignal = () => { stop = true; };
  process.on("SIGINT", onSignal);
  process.on("SIGTERM", onSignal);

  try {
    const summary = await runConsumerLoop({
      ledger, storeDir: STORE_DIR, machinePrefixes, startOffset: cursor.offset,
      session, identity, stopAfterBatch: once, intervalMs: interval * 1000,
      shouldStop: () => stop,
    });
    console.error(`[consumer] 完成：blocks=${summary.blocks}，drafts=${summary.drafts}，` +
      `等裁決=${summary.questions}，失敗=${summary.failures}；結束於 offset=${summary.offset}`);
    return summary.failures ? 1 : 0;
  } finally {
    session.dispose();
  }
}

process.exit(await main());