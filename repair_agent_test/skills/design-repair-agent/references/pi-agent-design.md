# 自進步 Agent：以 **Pi 為底層**的設計（權威版）

> **2026-09-28 建立。** 這份是**施工圖**：下一個 session 直接照這裡做，不要重新推導。
> 起因是一次**方向錯誤**（見下節），所以本檔開頭先把「不要做什麼」寫死。

---

## 0. 先講錯誤：不要回去救 `qbr/scripts/repair_agent.py`

設計者一開始就指定 **以 Pi 為底層**（`SKILL.md:55`）。助理卻在 2026-09-28 早上說
「接上學習迴圈是一行改動」，指的是**那個舊的 Python 版**。**設計者糾正：那個之前根本用不了。**

實測確認助理說法是錯的：

| 事實 | 證據 |
|---|---|
| `qbr/scripts/repair_agent.py` **沒有任何 caller** | `repair_daemon.sh` 實際跑 `confirm_dispute.py`／`scan_for_repairs.py`／`report_repair_progress.py` |
| 它**一跑就 crash** | `args=argparse.Namespace(max_tokens=12000, learned=None, timeout=900)`，但 `ask_one` 讀 `args.principles_for_prompt` → `AttributeError` |
| 經驗檔從未生效 | 同處傳 `learned=None`；且磁碟上沒有 `experience.json` |

→ **它不是「差一行」，是「這條迴路從未活過」，而且設計者已否決。**
→ **`repair_agent.py` 只保留一個用途：當「怎麼呼叫地端模型、怎麼組提示詞、怎麼記 finding」的
Python 參考。不修它、不擴它、不當 agent 的本體。**

---

## 1. 角色分工（設計者已決）

```
┌──────────────────────────────────────────────────────┐
│  Pi（Agent 中樞）   ← 底層就是這個，不是 Python        │
│  · 決定看哪一題、信不信、要不要再問                    │
│  · 有工具：read / bash / 呼叫地端模型 / 寫 advisory     │
│  · 記憶：Pi 原生持久 session                          │
└────────────┬─────────────────────────────────────────┘
             │ 指揮（下提示詞、收結構化輸出）
   ┌─────────┴─────────┬─────────────────┐
   ▼                   ▼                 ▼
ornith-1.5-mtplx    occamy-6bit      （未來科科目模型）
（18120, vision）   （18130, vision）
判讀器／轉錄器       第二意見
```

**這是 Q1 的選項 (b)：Pi 當指揮、地端模型當判讀器。**
理由不是猜的：現有全部證據都指向地端模型**強在看圖轉錄**，
而它的 tool-calling／多步規劃**可靠度未量**（三筆探針只證明「能動」）。

**注意**：這不代表「Pi 的腦是雲端」。Pi 是**執行框架**，它可以被指向任何 provider。
本案的 Pi session 用哪個模型當「腦」是**另一個獨立問題**（見 §6）。

---

## 2. 已實測的 Pi 接點（`0.87.1`；不要再靠記憶寫）

**版本事實（2026-09-28 實測）**：`pi` CLI `0.87.1`（`/opt/homebrew/bin/pi`）、
`npm view @earendil-works/pi-coding-agent version` → **`0.87.1`**、
Node **`v24.21.0`**（SDK 要求 `>=22.19.0`）。

### 2.1 最小起點（SDK 官方範例 `examples/sdk/01-minimal.ts`）

```ts
import { createAgentSession } from "@earendil-works/pi-coding-agent";

const { session } = await createAgentSession();
try {
  session.subscribe((event) => {
    if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
      process.stdout.write(event.assistantMessageEvent.delta);
    }
  });
  await session.prompt("...");
} finally {
  session.dispose();
}
```

### 2.2 本案會用到的接點

| 需求 | 接點 | 出處 |
|---|---|---|
| 建 session | `createAgentSession({ cwd, modelRuntime, model, resourceLoader, tools, sessionManager, thinkingLevel })` | `sdk.md` |
| **選模型** | `modelRuntime.registerProvider("<name>", config)`；config `{ name, baseUrl, api:"openai-completions", apiKey, authHeader, models, refreshModels({signal}) }` | `custom-provider.md` |
| 自訂工具（呼叫地端判讀器） | `customTools` / `tools` / `excludeTools`；`session.getActiveToolNames()` | `sdk.md` |
| **每輪刷新提示詞** | `DefaultResourceLoader({ extensionFactories: [fn] })`，fn 內註冊 Pi 的 **`before_agent_start`**，每輪重讀檔並覆寫該輪 system prompt | `extensions.md` |
| 串流收字 | `session.subscribe`，`message_update` → `assistantMessageEvent.type==="text_delta"` → `.delta` | `sdk.md` |
| 收錯 | `message_end` ＋ `message.role==="assistant"` ＋ `message.errorMessage` | `phoenix-agent-notes.md` §2 |
| 持久記憶／續作 | `SessionManager`（預設持久）；隔離用 `SessionManager.inMemory()` | `sdk.md` |
| 等它真的停 | `agent_settled`（`agent_end` 之後仍可能有自動重試或排隊工作） | `sdk.md` |
| 清理 | `session.dispose()` | `sdk.md` |
| 換模型／思考等級 | `await session.setModel(model)`；`session.setThinkingLevel(level)` | `phoenix-agent-notes.md` §2 |

**SDK 官方範例（14 支，全部有 typecheck）**：
`examples/sdk/01-minimal.ts` … `13-session-runtime.ts`，見 `sdk.md` 的表格。
**本案最相關**：`02-custom-model.ts`（選模型）、`05-tools.ts`（工具）、
`06-extensions.ts`（inline extension）、`11-sessions.ts`（記憶）。

### 2.3 已驗證可用的 provider（`~/.pi/agent/models.json`，**不必新寫**）

```
ornith-mtplx → { baseUrl: http://127.0.0.1:18120/v1, api: openai-completions,
                 apiKey: mtplx-local,
                 compat: { thinkingFormat: "qwen-chat-template",
                           maxTokensField: "max_tokens", supportsStore: false },
                 model: { id: "ornith-1.5-mtplx-35b", reasoning: true,
                          input: ["text","image"], contextWindow: 262144, maxTokens: 32768 } }
```

### 2.4 工程紀律（課程反覆強調，逐條照做）

1. **先核對 Pi 最新版再接**：`npm view` → 讀 SDK 文件 → 不符就改並記《建置紀錄》。
2. **版號鎖進 `package.json`，不留 `latest`**。
3. 環境自查（OS／Node／npm／Git），**嚴禁拿底層技術問題問設計者**。
4. 讀不到已啟用的資料 → **丟錯，不靜默開空白對話**。

---

## 3. 地端判讀器怎麼接（Python 已有、可直接用）

**判讀器不是 Pi 的「模型」，是 Pi 的「工具」。** Pi 用 `bash` 或自訂工具呼叫既有 Python：

| 既有資產 | 做什麼 | 檔案 |
|---|---|---|
| **裁圖** | 題號 → 這一題的列（跨頁縫合）→ PNG，含這題自己的圖框 | `confirm_dispute.py::crop_for` |
| **轉錄** | 送 PNG 給地端模型（**真的送 `image_url`**） | `reread.py::transcribe` |
| **比較** | 紙本讀值 vs 抽取值逐字元比 | `reread.py::compare` |
| **引擎表** | 端點／thinking 拼法**單一出處** | `engines.py::BUILTIN_ENDPOINTS`／`endpoints()` |
| **記 finding** | append-only，含逐字 prompt | `ai_findings.py::append` |

⭐ **關鍵**：`confirm_dispute`／`reread` 這條路**真的有送圖**。
壞掉的是**批次稽核**（`ai_findings.build_prompt` 只送 `FIGURE_NOTE` 文字，0 張圖）——
那 85,586 筆 corpus 判讀從沒看過紙本。**修這個，不是修 Pi 那端。**

**端點可設定已完成**（分支 `agent/engine-endpoints-runtime-20260927`，`e6b593c`）：
`BUILTIN_ENDPOINTS`＋`endpoints()`／`reload()`／`_overrides()`；覆寫序
內建 < `QBR_ENDPOINTS_FILE` < `QBR_ENGINE_*` < 舊變數名；**thinking 拼法不可由環境覆寫**
（錯拼法 HTTP 200 靜默忽略）。

**Pi 的 provider 與 `engines.py` 必須指向同一組端點**——否則「開發迴路調的東西」
與「生產迴路跑的東西」是兩份，正是 charter 禁止的分岔。
→ **`models.json` 的 provider 由 `engines.BUILTIN_ENDPOINTS` 導出，不手寫第二份。**

---

## 4. 記憶與學習：寫哪裡（避免第三個真相）

### 4.1 實測到的現況

| 流 | 檔案存在？ | writer 存在？ | 筆數 |
|---|---|---|---|
| `question_ai_findings.jsonl` | ✓ | ✓ | **108,146** |
| `question_ai_feedback.jsonl` | **✗ 從未寫過** | ✓（`review_state.append_ai_feedback`） | 0 |
| `question_ai_learning.jsonl` | **✗ 從未寫過** | ✓（`review_state.append_ai_learning`） | 0 |
| `experience.json`（舊 agent） | **✗** | ✓ | 0 |

→ **「有人類評分 AI 的流」與「人挑的訓練範例流」都設計好了，但從來沒有人寫過。**
（`AI_FEEDBACK_SCOPES={question,group,visual,answer}`、`AI_FEEDBACK_RATINGS={up,down}`、
有 `ai_review_ref` 保護：AI audit 變了就拒絕寫入。）

### 4.2 分層（三種東西，不要混）

| 層 | 內容 | 放哪 |
|---|---|---|
| **Pi 的原生記憶** | Agent 自己的長期記憶（我學到這個科目的什麼） | Pi session ＋ `before_agent_start` 讀的檔 |
| **人對 AI 的評分** | 「這個判讀對／不對」 | **複用 `question_ai_feedback.jsonl`**（**永不新開第三個流**） |
| **題目的事實** | finding／dispute／review event | 既有 append-only 流（**分開、永不互相改寫**） |

**紅線**：`question_ai_findings.jsonl` 與 `question_review_events.jsonl` 永遠分開；
**Agent 不得寫 `question_review_events.jsonl`，不得冒充人類審核者。**

---

## 5. 互動 UI（設計者第八輪的原話 → 規格）

| 設計者原話 | 規格 |
|---|---|
| **「每一題都要讀取完整，不能只讀這個不讀那個」** | 整題呈現（stem／A B C D／答案／圖），**不只展開被動過的欄位** |
| **「每一題獨立顯示」** | **一次一題**，不是一頁 47 題往下滑 |
| **「右邊搭配 PDF」** | 右側 PDF 該頁面板，**與左側清單解耦**（同 v2 的作法） |
| **「判讀寫入檔案當學習語料」（B 案）** | **寫進檔案**，不是 localStorage |
| **「線索都通過就列成一群快速瀏覽」** | 分群：**92.8% 一致 → 快速群**；**7.2% 分歧 → 才下指導** |
| **「不做數千數萬題」** | 不需要每題都看；靠分群 |
| **「新 UI 要參考業界做法重新設計」** | 業界研究已完成（LLM Comparator／LMArena／Argilla／Label Studio） |

**版面（沿用 v2 的 `.compare` grid `1fr 1fr` 概念，`review_ui/v2.html:530`）**：

```
┌──────────────────────────────┬─────────────────┐
│ 左：題目（一次一題，完整）      │  右：PDF 該頁   │
│   題幹／A B C D／答案           │  （解耦、可縮放）│
│   ── 機器說法（可展開）          │                 │
│   ── 人（設計者）當時怎麼改      │                 │
│   ── 模型 A 判讀                │                 │
│   ── 模型 B 判讀（第二意見）     │                 │
├──────────────────────────────┴─────────────────┤
│ 判讀／指導：[文字框]  [存檔]  ← **這次真的寫進檔案**  │
└────────────────────────────────────────────────┘
```

⚠️ **現況**：`repair_agent_test/a2/runs/*.html` 是**靜態頁、零 fetch、判讀只存 localStorage**。
**那不是設計者要的互動 UI**（`grep fetch` = 0）。

---

## 6. 未定但必須先量的（不要用預測當規則）

1. **Pi session 的「腦」用哪個模型**：雲端 `deepseek-v4.1-flash`（調適階段掌控）／
   `GLM-5.3-flash EXL3`（未來）／地端。**依 D3：考題可用雲端**（考題是考選部公開資料）。
2. **Pi 的工具集**：最小可用是先給 `read`／`bash`／自訂「問判讀器」；
   **不要一開始就全開**（Phoenix 的 `SOUL.md`／`MEMORY.md` 形狀是參考，不是抄）。
3. **Q1(a) 的可靠度**：ornith 當腦的 tool-calling 可靠度**未量**。要量才知道 (b) 是不是對的。

---

## 7. 驗收（每個都要有負對照）

| 項目 | 正對照 | 負對照（必須失敗） |
|---|---|---|
| Pi agent 建得起來 | `createAgentSession` 成功、能呼叫判讀器 | 拿掉 provider → 必須報錯，**不得靜默用預設模型** |
| 學習真的進提示詞 | 第二輪的 system prompt 含第一輪的教訓 | 清空記憶 → prompt 必須**不含**該段 |
| 判讀寫進檔案 | 檔案多一筆 append-only 記錄 | 重跑 → **不得覆寫**既有記錄 |
| 題目可被使用 | 驗證器 pass | 拿掉 `agent_verified` → 必須 fail（證明那條檢查還活著） |
| 互動 UI | 一次一題、右側 PDF 有圖 | 靜態頁 → 必須不符合驗收（現有 A2 頁就是負對照） |
| 批次稽核有圖 | prompt 含 `image_url` | `--no-image` → 分數必須掉（A2.1 已示範：47.8% → 18.9%） |

---

## 8. 相關文件

- 設計／討論全紀錄：[`qa-log.md`](qa-log.md)（**Q1–Q28**；本檔對應 **Q28**）
- Pi 課實際學到的：[`phoenix-agent-notes.md`](phoenix-agent-notes.md)（Phoenix Agent 實測）
- 模型選型與量測：[`a2-model-comparison.md`](a2-model-comparison.md)、[`a2.1-vision-ceiling.md`](a2.1-vision-ceiling.md)
- 全庫覆蓋缺口：[`qa-log.md`](qa-log.md) Q21（2,858 張圖題是假票）
- Pi 官方 SDK：`/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/sdk.md`
  ＋ `examples/sdk/`（14 支，有 typecheck）
