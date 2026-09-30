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
 */
export function fakeSession(storeDir, { alwaysDraft = true, failOn } = {}) {
  const tasks = [];
  return {
    tasks,
    async prompt(text) {
      tasks.push(text);
      if (failOn && text.includes(failOn)) throw new Error("model blew up");
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