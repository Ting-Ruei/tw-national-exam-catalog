#!/usr/bin/env node
/**
 * 壓縮 apply 端：把壓縮報告裡「設計者已核可退場」的條目改為 retired。
 *
 * 為什麼是腳本而不是手編 JSON：退場要一次做完「標記＋壓縮記錄＋版本＋sha 自檢」，
 * 手編容易漏一樣；而且這是規則家的正式寫入路徑，之後每次退場/合併都用同一個入口。
 *
 * 只做四件事：
 *   1. 退場（active=false，superseded_by=<marker>；**不刪文字**——lineage 可回查、可逆）
 *   2. `compressions[]` 追加一筆（pass/at/approved_by/basis/retired 清單）
 *   3. `version` bump：+compress-N（N = 本檔既有 compressions 數＋1）
 *   4. 寫入 tmp + rename 單次落盤；改前改後 sha 打出來
 *
 * 拿不到東西就大聲停（exit 2）：規則 id 不在、已 inactive、--sha 與現況不符。
 * 用法：node rules/retire_rules.mjs p1,p3,p4 --approved-by "designer-2026-09-30-chat"
 *      [--basis "說明"] [--sha <pre-sha>]
 */
import { readFileSync, writeFileSync, renameSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const HOUSE = join(HERE, "..", "rules", "rules.json");

function sha(raw) {
  return createHash("sha256").update(raw).digest("hex").slice(0, 16);
}

const argv = process.argv.slice(2);
const flag = (name) => {
  const i = argv.indexOf(name);
  return i === -1 ? null : argv[i + 1] || "";
};
const ids = (flag("--ids") || "").split(",").map((s) => s.trim()).filter(Boolean);
if (!ids.length) {
  console.error("用法：--ids p1,p3,p4 --approved-by designer-… [--basis 說明] [--sha <pre-sha>] [--mark <superseded_by>] [--utc-now <time>] [檔]");
  process.exit(2);
}
const approvedBy = flag("--approved-by");
const basis = flag("--basis") || "設計者核可退場（zero-hit／不再需要；lineage 保留，可逆）";
const marker = flag("--mark") || "retired-by-designer";
const housePath = flag("--rules") || HOUSE;

const raw = readFileSync(housePath, "utf8");
const current = sha(raw);
if (flag("--sha") && flag("--sha") !== current) {
  console.error("sha 不符：家內容在我讀之前又動過嗎？現況 " + current + " ≠ 你帶的 " + flag("--sha"));
  process.exit(2);
}
const house = JSON.parse(raw);
const at = flag("--utc-now") || new Date().toISOString();
const retired = [];
for (const id of ids) {
  const entry = house.rules.find((r) => r.principle_id === id);
  if (!entry) {
    console.error("找不到 " + id + " ——只退場實際存在的條目，不清不楚的拒絕寫入");
    process.exit(2);
  }
  if (entry.active === false || entry.superseded_by) {
    console.error(id + " 已是退場狀態（superseded_by=" + (entry.superseded_by || "（inactive）") + "）——重複退場拒絕");
    process.exit(2);
  }
  entry.active = false;
  entry.superseded_by = marker;
  entry.retired_at = at;
  entry.retired_reason = "designer-decision-zero-hit";
  retired.push(id);
}
house.compressions = house.compressions || [];
house.compressions.push({
  pass: "retire-" + at.slice(0, 10),
  at, approved_by: approvedBy, basis, retired,
});
house.version = house.version.replace(/compress-(\d+)$/, (_, n) => "compress-" + (Number(n) + 1));
const out = JSON.stringify(house, null, 2) + "\n";
const tmp = housePath + ".retire.tmp";
writeFileSync(tmp, out, "utf8");
renameSync(tmp, housePath);
console.log(JSON.stringify({ retired, version: house.version, sha_before: current,
                             sha_after: sha(readFileSync(housePath, "utf8")), at }, null, 1));