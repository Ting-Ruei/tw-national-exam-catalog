# Phoenix Agent 課程與實作：細節抄本

來源：`ssh tim@192.168.10.70` 的 `~/Phoenix_agent/`。
- `課程企劃書/`：`L1-01`～`L2-11`（12 份企劃，V1）。
- `Agent/`：**已實作的 Phoenix**（Pi `0.87.1`，Docker 常駐，Telegram 入口）。讀取日：2026-09-27。

> 本檔只記**量到的事實與可沿用的接點**；設計結論與對映寫在 `../SKILL.md`。

---

## 1. 課程逐課一句話

| 課 | 標題 | 一句話 |
|---|---|---|
| L1-01 | 把 Pi 做成 Agent | 用 ChatGPT OAuth（非 API Key）、免租雲端；`bridge.mjs`＋`SOUL.md`＋`MEMORY.md`，Pi 預設工具全開，白名單守門 |
| L2-01 | 本地開發流程 | 專案座標／企劃管理／每份企劃一份交接（六節）／歸檔條件 |
| L2-02 | 本地測試 | **唯一入口 `npm test`**（`node --test`）；交付前主動跑完，不問使用者 |
| L2-03 | 記憶與人格側寫日記 | 分層資料（SOUL／MEMORY／使用者主檔／側寫／日記）＋**每輪** `before_agent_start` 重新注入＋字元上限 |
| L2-04 | 夢境系統 | Pi 原生持久 session＋每日 03:00 整理；隔離無工具暫存 session；游標／去重／失敗續作 |
| L2-05 | 自動記錄與主動關懷 | 背景整理與提醒（心跳、待提醒清單、每日發送上限） |
| L2-06 | 網頁主控台 | 設定讀寫、模型切換、人格／記憶／日記查看編輯 |
| L2-07 | 外掛與主幹架構 | 主幹共同能力 vs 可停用外掛；載入器／接點／狀態；**第一版開關重啟後生效** |
| L2-08 | Skill 技能系統 | 純 Markdown `SKILL.md`；**按需載入**；與外掛／開發 Skill 三者分開 |
| L2-09 | 多 Agent 人格與群聊協作 | 多角色各自持久 session；共享群聊脈絡 vs 各自獨立；**角色共用同一份 skills** |
| L2-10 | 即時對話控制與緊急停止 | **`/btw` 旁路、`/wait` 安全停、`/stop` 全域中止**；三者是主幹能力；不做上下文回滾 |
| L2-11 | 多人議會 | 多角色輪流發言、回應彼此、整理方向／分歧／下一步；每場存 Markdown；只從 Web UI 開場 |

---

## 2. 實測到的 Pi API（`0.87.1`）

```js
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager }
  from "@earendil-works/pi-coding-agent";
```

| 接點 | 實測用法（節自 `bridge.mjs`） |
|---|---|
| `ModelRuntime.create({...})` | `{ authPath, modelsPath: null, modelsStorePath, refreshOnCreate: false }` |
| 自訂 provider | `modelRuntime.registerProvider("dgx", createDgxProviderConfig({...}))`；config 形狀：`{ name, baseUrl, api:"openai-completions", apiKey, authHeader, models, refreshModels({signal}) }` |
| 刷新模型清單 | `await modelRuntime.refresh({ providers: [p], allowNetwork: true, force: true, signal })`；錯誤在 `result.errors.get(p)` |
| `createAgentSession` | `{ cwd, agentDir, modelRuntime, model, resourceLoader, tools: ["read","write","edit","bash","grep","find","ls"], sessionManager, thinkingLevel }` |
| 隔離 session | `tools: []` ＋ `SessionManager.inMemory(WORK_DIR)`（夢境用；跑完 `dispose()`） |
| `DefaultResourceLoader` | `{ cwd, agentDir, noExtensions, noSkills, noPromptTemplates, noThemes, noContextFiles, systemPromptOverride, extensionFactories }`；`await loader.reload()` |
| 每輪提示詞刷新 | `extensionFactories: [createPersonalContextExtension(fn)]` → 註冊 Pi 的 `before_agent_start`，每輪重新讀檔並覆寫該輪 system prompt |
| 收字 | `session.subscribe(e => e.type==="message_update" && e.assistantMessageEvent.type==="text_delta" → e.assistantMessageEvent.delta)` |
| 收錯 | `e.type==="message_end" && e.message.role==="assistant" && e.message.errorMessage`；`e.type==="auto_retry_end" && !e.success && e.finalError` |
| 換模型 | `await session.setModel(model)`；`session.setThinkingLevel(level)` |
| 讀最新回覆 | `[...session.messages].reverse().find(m => m.role==="assistant")`，content 篩 `block.type==="text"` |
| 思考等級 | `model.reasoning` ＋ `model.thinkingLevelMap`，依 `["max","xhigh","high","medium","low","minimal"]` 找第一個有值者 |
| 工具清單 | `session.getActiveToolNames()` |
| FIFO | `agent-logic.mjs::createQueuedPromptHandler({session, refreshBeforePrompt, resetResponseState, readResponse, readFallbackResponse, formatError})` |
| 清理 | `session.dispose()` |

**注意**：Pi `0.85.0` 用 `thinkingLevel`（不是舊的 `thinking` 物件）；Node `>=22.19.0`；
啟動用 `node --env-file=.env bridge.mjs`（不要自寫脆弱的 `.env` 解析）。

---

## 3. `dgx-provider.mjs` 全文要點（自訂 provider 的最小範本）

- `normalizeDgxBaseUrl`：只准 http/https，去掉 query/hash，去尾斜線。
- `createDgxProviderConfig({ baseUrl, apiKey, fallbackModelId, contextWindow, maxTokens })` 回傳
  `{ name, baseUrl, api:"openai-completions", apiKey: apiKey || "local-dgx-no-auth", authHeader: Boolean(apiKey), models: fallbackModels, refreshModels({signal}) }`。
- `toModelConfig`：`id` 必填；context 取 `model.max_model_len ?? context_length ?? contextWindow` 的最小值；
  `cost: { input:0, output:0, cacheRead:0, cacheWrite:0 }`；`reasoning:false`；`input:["text"]`。
- 有單元測試 `tests/dgx-provider.test.mjs`（用假 `fetchImpl`）。

**對本案**：ornith（`127.0.0.1:18120`，`Bearer mtplx`，**有 vision**）可照同一形狀接；
差別是 `input: ["text","image"]`。另 `~/.pi/agent/models.json` 已存在 `ornith-mtplx` provider。

---

## 4. `personal-context.mjs` 記憶注入要點

- `DEFAULT_CONTEXT_LIMITS = { totalCharacters: 16_000, generalFileCharacters: 6_000, diaryFileCharacters: 3_000, diaryCount: 2 }`。
- 一般來源（依序）：`使用者主檔`（使用者確認的事實與偏好）／`SOUL`（人格）／`MEMORY`（長期記憶）／側寫（**觀察，不是使用者自述事實**）。
- 側寫檔名由英文名推導（`/^[a-z]+(?:-[a-z]+)*$/`，≤64）；**保留名不可用**（`soul.md`／`memory.md`／`使用者主檔.md`／`使用者詳情.md`／`日記格式.md`）；已存在則丟錯，**不覆寫**。
- 日記只挑**有效日期檔名**、非空、**日期最近 N 份**；截斷只作用於**提示詞副本**，原檔不動（附 `[本輪內容已節錄；原始檔保持完整]`）。
- 讀不到已啟用的資料 → **丟錯**（不靜默跳過）。

---

## 5. 工程紀律（課程反覆強調，逐條）

1. 環境自查（OS／Node／npm／Git），**嚴禁拿底層技術問題問使用者**。
2. **先對 Pi 最新版核對再接**：`npm view @earendil-works/pi-coding-agent version` → 讀 SDK 文件 → 不符就改並記《建置紀錄》；**版號鎖進 `package.json`，不留 `latest`**。
3. 訪談只問必要選項；**已有答案就沿用**；未答採預設、**不追問第二次**。
4. **未決定的事寫在企劃中**；**沒有實作授權時，交付企劃後停止**（「僅整理或撰寫企劃，不代表獲准實作」）。
5. 驗收**禁止虛報**；區分「使用者回報已操作」／「有實際成功結果」／「尚未驗證」。
6. 唯一測試入口；**交付前主動跑完整入口**，不問使用者要不要測；**修改後舊結果失效必須重跑**；逾時／取消／零筆／非零退出碼／必要測項略過**都不算綠燈**。
7. **同一問題兩次修正仍無進展 → 停止**，留錯誤摘要與下一步，不宣稱完成。
8. **每份企劃一份交接**（目標範圍／目前結果／修改與決定／驗證與證據／接續方式／文件位置）。
9. 不覆蓋／不刪除使用者既有變更；不留 `latest`；不為湊數寫無用斷言；**測試要呼叫正式程式共用的邏輯**，不複製實作自測。
10. **不讀 `.env`／憑證**；錯誤訊息要遮蔽 secret（`errorText` 做 `replaceAll(secret, "[已隱藏]")`）。
11. Docker 限制掛載範圍，**但不是完整安全沙箱**；SOUL 唯讀、MEMORY 可寫、workspace 限縮。

---

## 6. 尚未在 Phoenix 實作、但 L2 課表已規劃的

- L2-05 自動記錄與主動關懷、L2-06 主控台、L2-07 外掛框架、L2-08 runtime Skill、L2-09 多角色、
  L2-10 BTW/WAIT/STOP、L2-11 議會——**企劃書在 `開發/企劃書/`，未實作**。
- 已在 `Agent/開發/已開發/`：L2-01、L2-02、L2-03。
- 已在跑：L2-04（夢境＋持久 session，本地 30/30 綠燈；Telegram／正式模型端到端待使用者實測）。
