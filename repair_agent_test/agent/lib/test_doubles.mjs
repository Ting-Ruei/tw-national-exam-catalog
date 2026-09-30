/**
 * 測試替身（兩個測試檔共用，避免同樣的假代理寫兩份——兩份假代理是兩個可以不一致的地方）。
 *
 * 這個檔只被測試載入；放在 lib/ 而不是測試旁邊，是因為它被**兩個**測試檔用。
 */

import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { draftPath } from "./consumer.mjs";

/**
 * 假代理：記下收到的任務文案；`alwaysDraft` 時把一筆草案寫進 `storeDir` 的草案檔
 * （append-only，與 bridge 的 propose_repair 同形：action=repair_draft、status=proposed、
 * 帶 candidate_key）；`failOn` 給一個子字串，讓這題的 prompt 丟例外。
 *
 * `subscribe`：真 session 有（回話收集器掛在它上面）；假代理也發 `message_end` 事件——
 * 一個「有回話」的替身（回話可以由 `replies` 逐題給，沒給就用通用結語），讓收集器的合約
 * 在測試裡也真。
 */
export function fakeSession(storeDir, { alwaysDraft = true, failOn, replies } = {}) {
  const tasks = [];
  const listeners = [];
  let turn = 0;
  return {
    tasks,
    subscribe(fn) { listeners.push(fn); },
    async prompt(text) {
      tasks.push(text);
      if (failOn && text.includes(failOn)) throw new Error("model blew up");
      const reply = (replies && replies[turn]) || "判定：沒有草案落成（測試替身）。";
      turn += 1;
      for (const fn of listeners) {
        fn({ type: "message_end",
             message: { role: "assistant", content: [{ type: "text", text: reply }] } });
      }
      const key = /candidate key：(\S+)/.exec(text)?.[1];
      if (alwaysDraft && key) {
        const path = draftPath(storeDir);
        const prev = existsSync(path) ? readFileSync(path, "utf8") : "";
        writeFileSync(path,
          prev + JSON.stringify({ action: "repair_draft", candidate_key: key, status: "proposed" }) + "\n",
          "utf8");
      }
    },
  };
}