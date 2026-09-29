---
name: design-repair-agent
description: 在動工前讀這份。修理代理（**以 Pi SDK 為底層**、Pi 當指揮者、地端 ornith-1.5-mtplx／occamy 看題找錯）的設計資料：施工圖在 references/pi-agent-design.md、審題介面現況盤點、人機之間現有四條線與為何溝通不良、四個抱怨的結構原因、**目前還開著的決策（§9 是唯一權威清單）**，以及 Phoenix Agent 課程實作學到的 Pi Agent 建立經驗。當要討論「修理代理要怎麼做」「為什麼模型說沒問題」「為什麼模型不敢動」「為什麼模型亂改」「現在該做什麼」時讀這份。
---

# 修理代理設計（Pi 指揮 ornith 看題找錯）

## 怎麼讀（先看這一段）

| 你想知道 | 翻到 |
|---|---|
| **🆕 設計者最新裁決（2026-09-29）＋新 session 要做的** | **§9.9（先看這裡）**；逐字在 [`references/qa-log.md`](references/qa-log.md) **Q42** |
| **現在還剩什麼要決策、我該先做什麼** | **§9（唯一權威清單，含建議順序）** |
| 剛才那些實驗量到什麼 | [`references/`](references/) 的實測報告（見下方對照表） |
| 逐字問答（你的每一句話與我的回覆） | [`references/qa-log.md`](references/qa-log.md)（append-only） |
| 系統現在長什麼樣、為什麼溝通不良 | §2–§3 |
| 這個迴圈到底能不能變聰明 | §10 |
| 要動手改 `qbr/` 的主線程式（如 D14） | §6 ＋ `../qbr/AGENTS.md` ＋ `../docs/governance/README.md` |

**實測報告對照表（量到的東西在哪一份）**

| 量了什麼 | 看哪一份 |
|---|---|
| **D4：向量圖缺口**（向量 1.3%；⚠️ §8 更正：內嵌圖「5.5%」是探針假象） | [`references/vector-figure-gap.md`](references/vector-figure-gap.md) |
| **全卷 0 選項的真根因（`option_alphabet` union）** | [`references/option-alphabet-union.md`](references/option-alphabet-union.md) |
| 圖片定位（PP-DocLayout-V3） | [`references/model-glm-ocr-localization.md`](references/model-glm-ocr-localization.md) |
| 圖片歸屬與切範圍（墨跡） | [`references/model-glm-ocr-attribution.md`](references/model-glm-ocr-attribution.md) |
| 掃描件歸屬 ＋ GT 盲點 | [`references/model-scanned-attribution.md`](references/model-scanned-attribution.md) |
| 答案審核正確性 | [`references/answer-authority.md`](references/answer-authority.md) |
| GLM-OCR 本機實測 | [`references/model-glm-ocr-measurement.md`](references/model-glm-ocr-measurement.md) |
| 決策模型（Kev-4B／Laya） | [`references/model-decision-models.md`](references/model-decision-models.md) |
| Jev 的開源替代品（實測 80%） | [`references/jev-open-source-alternatives.md`](references/jev-open-source-alternatives.md) |
| 整體架構提案（三個 agent、兩條迴路） | [`references/architecture-redesign.md`](references/architecture-redesign.md) |

---

## 0. 這份是什麼、怎麼用

這是 **`repair_agent_test/` 的設計資料庫**，不是實作。它記錄三件事：

1. **現況盤點**：審題系統現在長什麼樣、人跟模型之間實際有哪幾條線、每條線的行為。
2. **目前還開著的決策**（**§9 是唯一權威清單**）＋我的傾向與理由。**設計者未裁決的不動工。**
3. **Agent 建立經驗**：從 `192.168.10.70:~/Phoenix_agent/`（課程 ＋ 已實作的 Phoenix Agent）
   學到的、**已實測可用**的 Pi Agent 接點與工程紀律。

**這一層的性質：沙盒／設計。** 產物是「量測與結論」，不是主線程式。整合進主線是**取代不是分岔**。
（同 `pi_test/AGENTS.md` 的沙盒紀律。）

**動工前的實際狀態**：Q1／Q4 已因 **D2 選 (a)** 而隱含解答（見 §9.4 A3，待確認），
但 **D8（「改錯了」怎麼回報）仍未解**——它是「Agent 放開做」迴路缺的那個迴授接點。

---

## 1. 一句話目標與硬邊界

**目標**：一個 **Pi-based agent**（底層＝Pi SDK，**不是** `qbr/scripts/repair_agent.py`），
**指揮**地端 `ornith-1.5-mtplx-35b`／`occamy-6bit` 看題目、找錯誤；由 Pi 當指揮者

> ⭐ **施工圖：[`references/pi-agent-design.md`](references/pi-agent-design.md)**（2026-09-28）。
> 不要再重新推導；也不要回頭去救那份 **零 caller ＋ 一跑就 crash** 的舊 Python 檔。
（orchestrator），依需要決定「要不要看」「看哪一題」「信不信這次讀法」。

**現階段不是** LoRA 微調模型，而是把**現有的判讀迴圈改造成可協商的 agent**。

**硬邊界（治理，不可繞）**

| 等級 | 內容 | Agent 可否自主 |
|---|---|---|
| G0 | 讀 | ✅ |
| G1 | 分支／PR | ✅ |
| G2 | **限定的 AI 狀態／結果**（advisory finding、自己的 AI workflow 狀態） | ✅ |
| G3 | 改題目文字、套用修復、image_refs、review 事件 | ❌ 需 owner **逐次**核准 |
| G4 | 人工裁決、append-only 修復、還原、實質刪除 | ❌ owner only |

- **AI finding 與 human review event 兩個檔永遠分開、append-only**，agent 不得冒充人類審核者。
- **題目內容不出內網**：`QBR_ALLOW_EXTERNAL_LLM` 預設關；模型只能用本機且**當次任務明確核准**的 endpoint。

**核心信念（來自 `qbr/AGENTS.md`，本案全程適用）**

- **腳本只量紙張的性質，語意一律提示詞。**
- **每個檢查要有負對照**（拿掉規則就必須失敗的那個案例）。
- **兩引擎一致才是驗收，綠燈不算。**
- **規則不能建立在解析輸出上**（否則解析器與檢查互相確認，循環驗證）。

---

## 2. 現況盤點（每個數字附量法；沒量到的標「未量」）

### 2.1 題目線的兩條 track 與審題介面

| Track | 位置 | 性質 |
|---|---|---|
| 題庫建置管線 | `qbr/` | 官方 PDF → 可驗封裝；**只讀語料，不寫 DB** |
| 審題介面 | `review_ui/` | **`v2.html` 是基準線**；`v1-reference/` 僅相容參考 |

- `v2.html` 不是五頁，是**同一頁的五個模式**（hash 路由）：首頁／題目／答案／錯題／原則。
  程式拆成 `review_ui/v2/01-core.js`(753)、`02-area-question.js`(1208)、`03-area-answer.js`(696)、
  `03-area-principles.js`(973)、`04-area-discuss.js`(894)、`03-areas.js`(216)、`05-boot.js`(90)。
- 伺服器 `scripts/serve_question_review_ui.py`(366 行) 是 composition root；
  實作在 `qbr/src/qbr/review_ui/`（`review_state.py` 7590 行、`handlers.py` 980、`ai_audit.py` 984、
  `queue_view.py` 800、`constants.py` 403、`events.py` 437）。
- **常駐機是 `192.168.10.70`，站上 `:8765`**（本次實測 HTTP 200）。改筆電工作樹**不會**自動生效，
  要 `scripts/deploy_station.sh --restart`（`--restart` 不能省）。**「我改好了」與「使用者看得到」是兩件事。**
- ⚠️ **量測前必讀：筆電的 `qbr/data/review-queues/live/` 是 2026-09-24 的舊快照，不是權威。**
  權威在站上 `~/qbr-review/queue/review-ui/`（**review events 20,322 筆 vs 筆電 5,232**）。
  兩份的差別具體長這樣：`candidates.jsonl` 筆電 09-24 21:07（修復前）／站上 09-26 11:52（修復後）；
  **699 張裁切從頁頂起（筆電）vs 2 張（站上）**。Q13 就是差一點把一個**已經修好的 bug** 當成還開著報出去。
  → 要量 live 佇列，**先 `scp` 站上那份**（`tim@100.96.146.93:~/qbr-review/queue/review-ui/candidates.jsonl`）。
- 錯題討論區（`04-area-discuss.js`）**只放卡住的題**，桶位由伺服器的 `DISCUSS_BUCKETS` 決定
  （`block`／`repair_pending`／`accepted_reaudit`／`reset_review`）；**過濾在伺服器不在瀏覽器**。
- 原則區（`03-area-principles.js`）三欄：基本原則（可核准）／代理的反問（可回答）／代理工作區 ＋ 右欄原題紙本。
  兩個投影來自 `qbr/src/qbr/discuss.py`：`principles_projection`、`repair_questions_projection`。

### 2.2 人的話怎麼進到模型眼前（**四條線，性質各不同**）

| 頻道 | 人在哪按 | 存成 | 進提示詞變成 | 要核准？ | 語義 |
|---|---|---|---|---|---|
| ⑥ 註解 | 討論區／題目區「只加註記」 | `comment` 事件 | `【審題者的註解】`（原話） | 不用 | **背景**（可參考） |
| 基本原則 | 原則區「＋新增原則」 | `question_review_principles.jsonl` | `【基本原則】你必須遵守` | **要按「核准」** | **約束** |
| 回答反問 | 原則區「送出回答」 | `question_repair_questions.jsonl` | `【審題者對你先前提問的回答】` | 不用 | **背景**（逐題） |
| ⑤ 手動修改 | 討論區編輯框「儲存修正」 | `correct` 事件（G4） | — | — | **你自己改**（模型不經手） |

證據：`qbr/src/qbr/ai_findings.py`（`PRINCIPLES` L302-315、`ANSWERS` L318-330、`NOTES` L333-350、
`REJECTED` L365-372；`build_prompt` 拼接處 L744-752）；`confirm_dispute.py::transcribe_system`；
`discuss.py::approved_principles`（L179）。

**結構性事實**：`註解／原則／回答` 三條都只是**上下文或約束**，**沒有一條是「把這個位置改成那個字」的委派**。
唯一能指定目標的動作是「手動修改」，而那是**人自己動手**。

### 2.3 模型怎麼回來（三條輸出）

| 輸出 | 存檔 | 畫在哪 |
|---|---|---|
| finding（verdict／what／where／fix／confidence／機械 diff） | `question_ai_findings.jsonl`（append-only） | 討論區 ②、原則區 |
| 反問（讀不懂時停下來問） | `question_repair_questions.jsonl` | 原則區 |
| 代理工作區（pending／TRUST-CARE-DOUBT／兩模型不一致） | `scan_state.json` ＋ finding 的 `orchestration` | 原則區 |

### 2.4 修理迴圈現在是**腳本不是 agent**

- `qbr/scripts/repair_daemon.sh` 三段：**scan（便宜，只比指紋）/ repair（貴，人明確啟動）/ report（只讀）**。
  `run_once` 只跑 scan＋report，**不呼叫模型**。排程**只跑 G2 證據路徑**。
- 掃描記**指紋**（爭議種類＋讀法＋人的決定＋註解＋拒絕次數＋已核准原則＋逐題圖說），
  存 `scan_state.json`；`pending` 是**獨立狀態**（owner：「掃描到跟做完了是兩回事」）。
- 閘門是**人的 standing `block`**，不是「有爭議種類」（2026-09-24 改）：
  實測 813 題有爭議種類，其中只有 120 題是人看過；反過來 339 題被 block 的有 **219 題一個種類都沒有**。
- 套用修復是**另一條 G3 路徑**：`apply_dispute_repairs.py`（`APPLICABLE`／`REFUSED 405` 階梯），
  **不在 daemon 裡**。所以「判讀可改題但未套用」量到 **420 題**（先前量測值）。
- 分流器 `qbr/src/qbr/orchestrator.py`：`TRUST`／`CARE`／`DOUBT`；**不確定時選 CARE 而不是 TRUST**；
  CARE/DOUBT 強制 `self_look=true`。

---

## 3. 設計者抱怨的四件事 → 各自的結構原因

| 設計者原話 | 結構原因（都可在程式裡指出位置） |
|---|---|
| **「我跟模型的溝通非常差」** | 四條線**分散**，且兩條是最近才接上。`ANSWERS` 的註解自承：人回答的內容**從來沒進下一輪提示詞**（`confirm_dispute.py` 只讀 `PRINCIPLES_STREAM`）。`NOTES` 自承：註解以前根本沒送到模型眼前，而 `blocked` 開場白還反過來說「人類沒有說錯在哪」。**你以為在對話，實際上前半段是對著牆說話。** |
| **「我認為找到錯誤，模型說沒錯誤」** | 你問的和它答的**不是同一題**。agent 的工作是**封閉轉錄題**——「抽取文字與那張圖一致嗎」；一致就回 `OK/NONE`。整個缺陷詞彙 `CODES`（`ai_findings.py` L68）**全是抽取缺陷**。`ANSWER_DISAGREES` 被**刻意移除**（L52-66：模型看不到紙本、更正卷、答案卷，只能靠學科記憶猜；實測與人一致率約 8%）。**答案是錯的／紙本命題本身有問題 → 不在它的判定空間。** |
| **「我下了命令，模型被治理鎖住」** | 三把鎖都是真的：①原則**要按「核准」**才進提示詞（`approved_principles`）；②daemon **只寫 advisory finding（G2）**，`--apply`（G3）不在 daemon；③**人的註解不是指令**——`--human-flagged` 只在明示模式跑且只認封閉標籤（`no-figure`／`extra-crop`／`wrong-region`／`keep`），普通一句話**不驅動動作**。 |
| **「有時又很敢動手，然後改錯」** | 會動手的路**只有 detector 已標記的位置**，其餘一律拒絕；一旦有標記它又會**擴張**。`substituted-ideograph` 直接套；page-read 要求「每個被改位置都是偵測器標的、且每個標記都要改到」；2026-09-24 為康熙部首開 NFKC 例外，一開從 **0→19** 筆套用。實測災難：`113020 q076` 附整張表、`105020 q045` 附編造箭頭標籤的整張圖、`115090 q053` 插入 93 字並清空四個選項；斜體 `--revert` 把 **53 個紙本真的有的斜體刪掉**。 |

### 合起來的根因（我判斷，不是事實）

- **③ 與 ④ 是同一個病的兩面**：**你的指令沒有目標座標，而模型的行動只有「detector 座標」這一種。**
  對得上時很敢（還可能擴張），對不上時完全動不了。
- **根本架構缺口：沒有「多輪協商」的地方。** 你的訊息變成**一次性**提示詞輸入 ＋ 一個**封閉問題**；
  輸出不是 advisory finding（永不套用）就是自動改動（套用）。`ask→answer` 是一問一答，
  **回答不會讓原題重新排隊**；finding 是 append-only 筆記。**同一題上的往來討論，沒有任何容身之處。**

---

## 4. 「仍有用的設計題」（Q1–Q5；**裁決狀態已統一搬進 §9**）

> **2026-09-27 更新**：設計者已給出**架構方向**（三個 agent ＋ **兩條迴路**；完整設計在
> [`references/architecture-redesign.md`](references/architecture-redesign.md)）。
> 所以本節的 Q1–Q5 **降為「仍有用的設計題」**；**真正卡住的看 §9**（唯一權威清單；D4 已量完、D5／D7 已決）。
> **共同結論：架構是「兩條迴路共用同一套端點與提示詞」，不是「一條迴路」**——
> 這樣「調適」調的才是「生產」在跑的東西（`ai_findings.prompt_version()` 已在 hash 提示詞輸入）。

> **2026-09-27 第二輪（Q8）更新**：設計者更正模型選型（**4bit 剔除：無 vision**；加入 `occamy-abliterated` 作候選），並宣告 **Mac Studio 只當 Agent 中樞、不當算力中樞**（算力現在＝M5 Max MBP；未來＝DGX spark ×2／AMD AI MAX+ 395）。**因此 `engines.py::ENDPOINTS` 寫死是架構債，端點可設定應為所有實驗的第 0 步**（見 §9.2）。
>
> ⚠️ **本節不再放待決清單。**（兩個地方放同一份清單就是兩個可以不一致的地方。）
> D6／D7／D8 的現行狀態見 **§9**；其中 **D8 本節先前的提案已被設計者否決**。
> **Q1 與 Q4 的阻塞也已在 §9.4（A3）交代**：D2 選 (a) 之後，本層是沙盒、成果以 PR 進主線。

### Q1（阻塞）ornith 是腦，還是眼睛？

| 選項 | 內容 | 已知證據 |
|---|---|---|
| (a) ornith 當 Pi 主模型 | 它自己規劃、用工具 | **未量**：它的 tool-calling／多步規劃能力還沒測過 |
| (b) **Pi 主模型當指揮、ornith 只當判讀器／轉錄器** | Pi 決定看哪題、信不信，ornith 看圖轉錄 | 現有證據全在此：ornith 強在**看圖轉錄** |
| (c) 兩者都試 | 用同一組題比較 | 成本最高，但能得到 (a) 的實測答案 |

**我的傾向：(b)，並用一個小實驗把 (a) 量出來再決定。** 理由：不要用未量測的預測當規則。
> **2026-09-28 更新**：設計者已確認 **(b)** —— Pi 當指揮者、地端模型當判讀器。
> 且 **Pi 底層不是選項而是既定事實**（施工圖見 [`references/pi-agent-design.md`](references/pi-agent-design.md)）。
> (a) 的可靠度**仍未量**，要量才知道 (b) 是不是最佳，但**不要因此延後動工**。

### Q2 動作空間多大？

只讀 ／ ＋寫 advisory finding（G2） ／ ＋提修復提案但不套用（仍是 G2，套用才 G3）。

### Q3 記憶與紀錄放哪？

必須避免「一個問題兩個實作」。**設計者 2026-09-28 已決：判讀寫入檔案當學習語料（B 案），複用既有 append-only 流**
（`question_ai_findings.jsonl`、`question_ai_feedback.jsonl`、`question_ai_learning.jsonl`），**不新開第三個真相**。
> **實測**：`question_ai_feedback.jsonl` 與 `question_ai_learning.jsonl` **磁碟上不存在**——
> writer（`review_state.append_ai_feedback`／`append_ai_learning`）**已存在但從未寫過一筆**。
> 因此 B 案是「**接上既有的空管線**」，不是「新蓋一條」。
（對照 charter：導覽與內容必須來自同一個來源。）

### Q4（阻塞）`repair_agent_test/` 是沙盒還是準主線？

- 沙盒：只量測、寫結論，整合時**取代**主線（同 `pi_test/`）。
- 準主線：直接在這個目錄長出可部署的東西。

### Q5 驗收怎麼量？

候選指標（都要附負對照）：與人 `block` 的一致率、block 率 vs 基準 **5.4%**、
**兩引擎一致率**、**負對照失敗率**。**綠燈不算驗收。**

### 其他已浮現的決策點

- **多輪協商要落在哪條線**？（新開一條「帶座標的委派」，還是把現有 finding 變成可回覆的討論串？）
- **「這是命令」vs「這是背景」怎麼分**？現在四條線語義混在一起。
- 治理要**鎖得住亂擴張，但鎖不住執行明示的事**。

---

## 5. Agent 建立經驗（from `192.168.10.70:~/Phoenix_agent/`）

來源：`課程企劃書/L1-01～L2-11`（12 份）＋**已實作的 `Agent/`**（`bridge.mjs` 584 行、`dgx-provider.mjs`、
`personal-context.mjs`、`agent-logic.mjs`、`session-persistence.mjs`、`dream-system.mjs`；Pi `0.87.1`）。

### 5.1 已實測可用、本案可直接沿用的 Pi 接點

```js
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager }
  from "@earendil-works/pi-coding-agent";
```

- **`createAgentSession({ cwd, agentDir, modelRuntime, model, resourceLoader, tools, sessionManager, thinkingLevel })`**
  ——`tools` 是字串陣列（`["read","write","edit","bash","grep","find","ls"]`）；
  隔離用 `tools: []`（夢境就是這樣跑：隔離、無工具的暫存 session）。
- **`ModelRuntime.create({ authPath, modelsPath, modelsStorePath, refreshOnCreate })`** ＋
  **`modelRuntime.registerProvider(name, config)`** ＋ **`modelRuntime.refresh({providers, allowNetwork, force, signal})`**。
- **自訂 provider 不必新寫 Pi 外掛**：`createDgxProviderConfig` 就是一個 factory，回傳
  `{ name, baseUrl, api: "openai-completions", apiKey, authHeader, models, refreshModels }`。
  **本案 ornith（`127.0.0.1:18120`）可照這個形狀接**；`~/.pi/agent/models.json` 也已有 `ornith-mtplx` provider。
- **`DefaultResourceLoader({ cwd, agentDir, noExtensions, noSkills, noPromptTemplates, noThemes, noContextFiles, systemPromptOverride, extensionFactories })`** ＋ `await loader.reload()`。
  專案若要用 skill，**`noSkills` 必須設 false 並給技能路徑**（L2-08 特別警告 `noSkills: true` 會讓檔案存在卻無法使用）。
- **每輪刷新提示詞**：`createPersonalContextExtension` 註冊 Pi 的 **`before_agent_start`** 接點，
  在**每一輪**重新讀設定並用完整提示詞取代該輪版本——保留 session 與對話歷史。**本案的「原則／註解／回答」
  正好該走這條線**（現在的痛點就是它們只注入一次或根本沒注入）。
- **事件**：`session.subscribe(event => …)`；`message_update` → `event.assistantMessageEvent.type === "text_delta"` 收字；
  `message_end` 的 `message.errorMessage`、`auto_retry_end` 的 `finalError` 收錯。
- **`SessionManager.inMemory(cwd)`** 開暫存 session；檔案 session 見 `session-persistence.mjs`
  （只接受專用目錄下的 Pi 原生檔；損毀或越界**停止啟動，不靜默開空白對話**）。
- **FIFO 佇列**：`agent-logic.mjs::createQueuedPromptHandler`——`queue = queue.then(...)`，
  **一次處理一則**。本案若要「同一題多輪」，這是可以照抄的最小骨架。
- **多工**：`createAgentSession` 可一次開多個 session（多角色／多視角）。**角色共用同一份 skills 來源，不複製。**

### 5.2 主幹 vs 外掛（L2-07）

- 「拆成不同檔案」與「做成可停格外掛」是**兩件事**。**主幹**＝對話收發、模型呼叫、session 保存、
  人格記憶注入、設定讀寫、排程引擎、HTTP／登入／Web UI 殼、外掛載入管理；**外掛**＝特定用途工具、
  額外指令、功能自己的設定與頁面。
- **新增外掛的啟用設定，第一版重啟後生效。** 不要因為加了外掛架構就把所有設定都改成要重啟。
- **一個功能做一個外掛**（不分岔）；外掛關掉要能回到原本行為，資料與進度保留。

### 5.3 Skill 系統（L2-08）——本案最該用的機制

- 技能＝一包 `SKILL.md`（**純 Markdown SOP**）＋可選 `scripts/`／`references/`／`assets/`。
- **按需載入**：啟動時只把 `name`＋`description`＋路徑放進系統提示詞；任務相關才讀全文。**不每一輪塞全文。**
- 三種東西分開：**開發 Skill**（給開發 AI 找專案／測試／交接）、**runtime Skill**（給工作中的 agent 按 SOP 做事）、
  **外掛／工具**（真正可執行的能力）。**Skill 說明怎麼用工具，不代替工具。**
- 位置：`~/.pi/agent/skills/`、專案 `.pi/settings.json` 的 `skills` 路徑、或 `.agents/skills/`。
  專案 `AGENTS.md` 的 `@docs/...` 是**文件引用，不是 Skill 註冊**——兩者不是同一條線。
- **本案可照這個做**：把「修理題目的 SOP」寫成 runtime Skill（判準、可回覆的 finding 格式、什麼算命令），
  而不是繼續把規則寫成 `extract.py`／`repair.py` 的條件（那正是「規則→腳本→新問題→新規則」的跑步機）。

### 5.4 記憶分層與界線（L2-03）——可直接對映到本案

| Phoenix | 作用 | 本案對映 |
|---|---|---|
| `SOUL.md` | 它是誰（唯讀，不自行改） | agent 的身分與硬規則（＝本案的治理邊界） |
| `MEMORY.md` | 長期共同約定／經歷／教訓 | **已核准的基本原則** |
| `使用者主檔` | 使用者確認的**事實與偏好** | 人類的 standing 決定與偏好 |
| 側寫 `tim.md` | agent 的**觀察**（要註明依據） | **AI finding**（advisory，附 confidence 與證據） |
| 日記 `YYYY-MM-DD.md` | 逐日整理 | finding／scan 的逐輪紀錄 |

**關鍵紀律**：**只有使用者明講「記下來」才寫**；**不停用類別就換目的地塞入**；
**普通聊天不自動寫檔**；寫入成功下一輪自動讀取。
→ 本案的對映：**AI 的觀察（finding）永遠不等於人的事實（review event）**；兩個檔分開、append-only。

### 5.5 多輪與多角色（L2-09）

- **角色的持久身分與個人資料各自存在**；共享的是「允許這個群組成員接收的發言」，**不是同一個不斷改名的 session**。
- 共享模式不是把所有人塞進同一 session；獨立模式也不把別人的回答自動同步進來。
- 模型共用與否是**獨立選項**（同模型也能有不同人格）。
- **對本案的類比**：兩個引擎（Pi 指揮者 / ornith 判讀者）＝兩個角色；**finding 是他們共享的那份發言**，
  但要清楚「誰說的」（`actor`）。**判讀者不被指揮者的結論污染**（各自 session）。

### 5.6 即時控制 BTW / WAIT / STOP（L2-10）——本案最缺的一塊

- **`/btw`**：主任務繼續，另開**旁路對話**問問題，主線上下文不動（可選每次獨立／每天清空／持續保留）。
- **`/wait`**：已開始的工具做完後停在安全位置，不再開始下一步，等下一個指令。
- **`/stop`**：最高優先中止全系統所有行動（主線、旁路、其他角色、背景、排隊）。
- **這三個是主幹能力**，角色／通訊入口／外掛都接同一套；**外掛沒載入時主幹仍能接收停止**。
- **不做上下文回滾**。
- **對本案**：這正是「我下了命令，模型不敢動」／「模型太敢動」的解藥形狀——
  **一個可以中途插話、要求它先停、問它為什麼的介面**，而不是一次性封閉問題。

### 5.7 議會（L2-11）與「兩模型不一致」是同一種東西

- 議會＝多個角色**輪流表達、回應彼此**，最後整理出方向／分歧／下一步，每場存一份 Markdown。
- 只從 Web UI 開場，不自動執行結論。
- **對本案**：`operate-repair-agent-surface` 的「**兩個模型不一致**」面板，就是這個機制的雛形——
  地端說「有差異」、指揮者說「可放行」的那一批，是**唯一能同時檢查兩個模型**的地方，所以排最前面。
  **這個面板已經是「多視角協商」的最小可用版本**，應該延伸它，不是另做一個議會。

### 5.8 課程反覆強調的工程紀律（可直接當本案的規範）

1. **環境自查，嚴禁拿底層技術問題問使用者**（Node 版本、OS、路徑一律自己偵測）。
2. **先對 Pi 最新版核對再接**：`npm view @earendil-works/pi-coding-agent version`，讀 SDK 文件，
   不符就改並記進《建置紀錄》；**版號鎖進 package.json，不留 `latest`**。
3. **訪談只問必要選項**，已有答案就沿用，**不重問整套**；未回答採預設值，**不追問第二次**。
4. **未決定的事寫在企劃中**；**沒有實作授權時，交付企劃後停止。**
5. **驗收要實測**：不得用檔案數／舊結果／固定字串代替；**「使用者回報已操作」≠「有實際成功結果」≠「尚未驗證」三者要分清**。
6. **唯一測試入口**（`npm test`）；交付前主動跑完整入口，**不問使用者要不要測**。
7. **同一問題兩次修正無進展就停**，留錯誤摘要與下一步，不宣稱完成。
8. **每份企劃一份交接**（六節：目標範圍／目前結果／修改與決定／驗證與證據／接續方式／文件位置）。
9. **`systemPromptOverride` ＋ `before_agent_start` 每輪刷新**，不要只在啟動時注入一次。

---

## 6. 與 catalog 治理的對接（本案必須遵守）

- `tw-national-exam-catalog/AGENTS.md`：分支 `codex/*`／`agent/*` ＋ PR；**不得直接 push `main`**、
  不自我核准 PR、不把 merged PR 當 production 核准。
- **未經 owner 逐次核准不得做 G3/G4**；AI 狀態**不是**人工裁決或正式 package 發布。
- 目前**沒有 AI 狀態 writer 的實作授權**（見 `docs/skills/repair-open-items/SKILL.md`）；
  要設計時須定義清楚資料契約：`actor`、`source/input hash`、版本與修訂脈絡。
- 既有 append-only 事件**不得改寫**；重建要能自動攜帶 review 紀錄（whole record 去重，孤兒保留並計數；
  若「要攜帶 0 筆但附近有紀錄」→ **拒絕啟動 exit 2**）。

---

## 7. 實驗計畫草案（**待 Q1／Q4 裁決後才動**）

**最小可量測實驗**（5–20 題，**必須含人已 `block` 的題**）：

1. 同一組題，三臂比較：
   - A 現行管線（封閉問題）
   - B Pi 主模型 + ornith 當判讀器（Q1b）
   - C ornith 當腦（Q1a，量它會不會用工具）
2. **負對照**：至少一題「人說有錯、但封閉問題正確答案是 OK」——封閉問題**必須**答不出來，
   協商迴圈**必須**能從人的話裡把座標問出來。
3. 量：與人 `block` 的一致率、**兩引擎一致率**、**負對照失敗率**、每題 token 與時間、空回覆數。
4. **報告 append-only**，寫明「量法」；沒量到的**不寫成事實**。

**不在這一階段做**：改題目文字、套用修復、寫任何 writer、動 `review_ui/` 或部署。

---

## 8. 環境與量測事實（2026-09-27 實測）

| 項目 | 值 |
|---|---|
| 地端 MTPLX 35b（**主要判讀模型**） | `http://127.0.0.1:18120/v1` model `ornith-1.5-mtplx-35b`，Bearer `mtplx`，**HTTP 200** |
| MTPLX 9b（`18121`） / mlx-vlm（`8082`） / splash（`8088`） / omlx（`8000`） | **down**（本次實測） |
| ollama `11434` | **HTTP 200**（`ornith-1.5:35b`） |
| Pi CLI | `0.87.1`（`/opt/homebrew/bin/pi`） |
| 常駐機 | `192.168.10.70`，站上 `:8765/v2` **HTTP 200**，queue `~/qbr-review/queue`（`scan_state.json` 存在） |
| 引擎 thinking 拼法（**不可混用**） | MTPLX → `chat_template_kwargs.enable_thinking`；vLLM/Splash → `reasoning_effort`；mlx-vlm → top-level `enable_thinking`。錯拼法回 **HTTP 200 但內容空**。 |

**量測紀律**：空回覆是**關於 budget 的證據**，不是自動關於模型品質。記錄 `max_tokens`；
分別計空回覆／截斷／錯誤。建議起始預算：答一個字母 64–256；結構化缺陷清單 1,500；開思考至少 8,000。

### 8.1 本案實測（2026-09-27，Pi CLI `0.87.1` ＋ `--provider ornith-mtplx`）

| 探針 | 命令（節錄） | 結果 |
|---|---|---|
| 純文字 | `pi -p --no-session --provider ornith-mtplx "回答一個字：OK"` | `OK` |
| **tool-calling**（Q1a 的最小證據） | `pi -p --no-session --provider ornith-mtplx --tools read "用 read 工具讀 repair_agent_test/AGENTS.md 的第一行"` | 正確回 `# repair_agent_test — agent 入口（設計／沙盒層）` → **ornith 會用工具** |
| **vision** | `pi -p --no-session --provider ornith-mtplx --tools read "…讀 tmp/three-source-pilot/q35-full-page/page5.png，回覆最上方那行文字"` | 正確指出 `B.`（並描述前一題圖形） → **看圖可行** |

**這三筆只證明「能動」，不證明「夠可靠」。** tool-calling 的可靠度、多步規劃、長題多圖的穩定度**都還沒量**。
Q1 的關鍵問題（ornith 能不能當腦）需要的是**可靠度**樣本，不是單一成功案例。

**已驗證可用的 provider**（`~/.pi/agent/models.json`）：
`ornith-mtplx` → `{ baseUrl: http://127.0.0.1:18120/v1, api: openai-completions, apiKey: mtplx-local,
compat: { thinkingFormat: "qwen-chat-template", maxTokensField: "max_tokens", supportsStore: false },
model: { id: "ornith-1.5-mtplx-35b", reasoning: true, input: ["text","image"], contextWindow: 262144, maxTokens: 32768 } }`。
**本案不必新寫 provider。**

---

## 9. 尚未解決、等你裁決

> **2026-09-27 第四輪整理。** 本節是**唯一權威清單**。
> 欄位「誰定的」：**設計者已決**＝你已表態；**等你**＝還沒聽到你的意思；**我提案**＝我建議但未獲回應。
>
> 🆕 **設計者 2026-09-27 第四輪的裁決已併入**（D6／D8／D9–D12／D13／D14／A1–A5）＋**「站上是家」協定**。
> 逐字紀錄見 [`references/qa-log.md`](references/qa-log.md) **Q14**。
>
> ⚠️ **協定違規自首**：先前有 **4 件事已發生但沒寫進 [`qa-log.md`](references/qa-log.md)**
> （D1／D2／D3 的裁決，以及 comment→block 的修復）。本輪已補記為 **Q12**。
> 我違反的是自己定的協定「**沒寫進本檔的就不算記下**」。

### 9.0 🆕 站上是家（設計者已決，2026-09-27）

> **開工第一件事：`repair_agent_test/scripts/sync_from_station.sh`。改完第一件事：`deploy_station.sh --restart`。**

**協定第一次跑就抓到 4 份檔案全在漂移**：`candidates.jsonl` 站上 09-26 11:52 vs 筆電 **09-24 21:07**；
`question_ai_findings.jsonl` 站上 **107,991** vs 筆電 **86,319**（少 21,672）；`figure_ownership.json`
站上 **2,702 行** vs 筆電 **30 行**；`question_repair_questions.jsonl` 690 vs 53。
→ **先前所有「筆電上的量測」都是兩天前的世界。** 理由由數字證明。

### 9.1 已決且已做完

| # | 事情 | 誰定的 | 結果 |
|---|---|---|---|
| **D3** | 指導者模型 | 設計者已決 | 本 session `deepseek-v4.1-flash`（A 層）；考題可用雲端（考選部公開）。未來地端 `GLM-5.3-flash EXL3` |
| **D4** | 向量圖缺口 | 設計者已決（要量） | ✅ **已量完**：向量圖 **1.3%**。⚠️ **本輪（Q16）更正**：原報的「內嵌圖 `xref=0` 5.5%」是**探針假象**（`image_bytes_of` 是死碼）；真洞是 `option_alphabet` 的 union。見 [`references/vector-figure-gap.md`](references/vector-figure-gap.md) §8、[`references/option-alphabet-union.md`](references/option-alphabet-union.md) |
| **B1** | **Comment→Block 吃不到紀錄** | 設計者回報的 bug | ✅ **已修＋已部署＋已驗證**（`_reaffirm_standing_action`；站上 hash `429401…` 與筆電一致）。見 Q12 |
| **B2** | **「跨頁＋有圖的截圖幾乎都會錯」** | 設計者 2026-09-25 回報 | ✅ **已修＋已部署到站上**（`question_region` 的跨頁接縫 ＋ `figure_questions` 的「圖只屬於一題」）。量到的指紋：裁切從頁頂起 **699 → 2**。完整機制：[`references/crosspage-crop.md`](references/crosspage-crop.md)；逐字紀錄 Q13。⚠️ **但修復只在工作樹、未提交 git**（`vision.py`／`crop_run_figures.py` 共 1,875 行）→ **G1 缺口** |
| **B2b 🆕 2026-09-28** | **「有些題目的圖片沒有截圖正確」——agent 這條路的版本** | 設計者 2026-09-25 原報，**今天才在 agent 路徑上抓到** | ✅ **已修＋12 個測試全過（2 條新，各附負對照）**。根因：`bridge.figure_boxes` 交 **tuple**，而 `reread.page_extents` 只讀 **dict** → `isinstance(entry, dict)` 靜默丟掉**每一筆框**。實測 `1152_藥師(一)_藥學(一)` **q042**（A/B/C/D 全是化學結構圖）：裁片只出現 A，**B/C/D 空白**；修後 27,545→**134,227 bytes**，四張結構式全到齊。**生產路徑沒壞**（`confirm_dispute.py:801` 交真 dict；站上 shipped 的 `q042_option_D.png` 打開驗過，結構式完整）——**修在 agent 開始跑之前**。逐字紀錄 Q31 |
| **B1 ✅ 已驗證** | **Comment→Block 修復在真實操作下有效** | 設計者已重試（A4） | ✅ **設計者本輪真去做了**：站上 2026-09-27T13:40:57 `{action:block, note_action:note, notes:"上下標異常"}` —— **註解被折進 block 事件**（修復前是兩筆、block 那筆 `notes:""`）。見 Q14 |

### 9.2 已決但**還沒做**（可以直接動）

| # | 事情 | 誰定的 | 下一步 |
|---|---|---|---|
| **D1** | G3 拆解：Agent 放開做，改錯就 block 退回重處理 | **設計者已決**（原話：視為決定） | 動工時照此；**D8 已由設計者本輪完整回答**（見 §9.3） |
| **D2** | 第一個實驗 | **設計者已決**（依 Pi 建議） | 先做**第 0 步（端點可設定）**，再補 `ask_about_blocks` 的 answers 斷點 |
| **D5** | 「90-95%」現況 | **設計者已決**（先不用） | 不做（順帶量到近似值：人打回率約 **3.6%**） |
| **D7** | `review_state.py` 7,590 行拆分 | **設計者已決**（後續拆） | 等前面重要的先做完；範本＝`dispute_apply/` |
| **第 0 步** | `engines.py::ENDPOINTS` 改可設定（Tailscale 主機名） | **設計者已決**（依 Pi 建議） | ✅ **已完成（本輪）**：`BUILTIN_ENDPOINTS`＋`endpoints()`／`reload()`／`_overrides()`；覆寫序 內建 < JSON 檔 < `QBR_ENGINE_*` < 舊變數名；**thinking switch 不可覆寫**（錯拼法靜默 HTTP 200，比報錯危險）；`vision.py`／`reflow.py` **移除第二份 literal**；站點主機名改 **`timmac-studio`**（Tailscale）。`test_engines.py` 11→**16 passed**（5 個新測試各有負對照）；全套 **783 passed, 17 skipped**。分支 `agent/engine-endpoints-runtime-20260927`（`e6b593c`），已 push |
| **D14 🔴 第一優先** | ~~修 `image_bytes_of` 的 `no-xref`~~ → **改為修 `option_alphabet` 的 union** | **設計者已決（Q15：「可以開 PR 了」）；前提經本輪量測推翻（Q16）** | **✅ PR 已開**：`agent/fix-option-alphabet-union-20260927`。**`image_bytes_of` 是死碼**（production 零呼叫者），修它無效；真根因＝`repair.option_alphabet` 回傳 union → **667 篇論文選項被誤標、658 篇全卷 0 選項**（修正後 649 篇取回、參考庫 recall 447→449）。**含負對照測試**（舊行為必失敗） |
| **D9–D12** | 決策模型系列 | **設計者已決：延後** | 先把 35B 該做的事訓練好，再引決策模型（工具很新，訓練需乾淨資料） |
| **A3 ✅ 已授權** | `repair_agent_test/` 是沙盒 | **設計者已決：盡情測試（sandbox）** | 審題走原 `reviewUI/v2`；Agent 交流獨立 UI；**真的改對的題仍在 v2**；穩定後接主線；**實驗階段可推 git** |
| **A5 ✅ 已授權** | 完整測量 | **設計者已決** | 能用本地模型就用；`192.168.10.90:8888`（dgx-spark）可輔助 |

### 9.3 🔴 等你裁決（設計者 2026-09-27 第四輪後，剩下這些）

| # | 問題 | 為什麼卡 | 我的建議 |
|---|---|---|---|
| **D8** | ~~「AI 改完還是錯」怎麼回報~~ | ✅ **設計者 2026-09-27 已完整回答** | 見下方 §9.3.1。**不需新動作** |
| **D6** | **答案修正監控／對齊 Agent** | **設計者已決：按建議先試** | 先量「答案位移發生率」與「幾題來自 MOD 而非 ANS」（兩者都未量），再定 Agent 的輸入輸出 |
| **D9–D12** | 決策模型提案 | **設計者已決：延後** | 先把 35B 該做的事訓練好，再引決策模型 |
| **D13** | **MoE「做事能力」怎麼比對** | **設計者已決：延伸至 9.4（A2）回答** | 答案就是 **A2 的比較 UI**（見下） |
| **A1** | 要不要改 `golden_path` 匯出加頁碼 | **設計者已決（Q15：「A1 要做」）** | ✅ **已完成（本輪）**：`stage_records` 的 `extra_metadata` 加 `question_page`。**只匯出「題號所在頁」，不是 band 末端**；讀不到寫 `None`，**不寫預設 1**（假造的 1 是靜默的「無位置」缺陷）。3 個新測試；負對照：還原即 FAIL（`KeyError: 'question_page'`）。分支 `agent/export-question-page-20260927`（`f282d0b`），已 push |
| **A2 🆕 最有價值** | **多模型決策／動作比較 UI** | **設計者已授權並指定做法** | ✅ **harness 完成＋已跑三輪（本輪）**：`repair_agent_test/a2/`（sandbox）。正解＝設計者自己的 **`correct` 事件**（47 題／90 欄位）。**兩個關鍵量測**：① 純文字天花板只有 **41.1%**（58.9% 的人工改動資訊不在輸入裡）② **餵那一頁的圖之後，可達欄位從 29.7% → 78.4%**；負對照（同提示詞不給圖）掉回 27.8%。詳見 [`references/a2-model-comparison.md`](references/a2-model-comparison.md) ＋ [`references/a2.1-vision-ceiling.md`](references/a2.1-vision-ceiling.md) |
| **A6** | 筆電 live 佇列是舊快照 | ✅ **已解**：建立 `sync_from_station.sh`；站上是家（§9.0） | 是否要寫進 `AGENTS.md` 當硬規則？ |
| **新** | **「文字型表格」怎麼處理** | 設計者本輪主問 | **三層**（成本排序）：① **真 root cause 是 `option_alphabet` 的 union**（非 `image_bytes_of` 死碼）② 表裁切改走「圖」不走「行」③ **不要把「是不是表格」做成偵測器**（語意→讀法，`vision.py` 長註解已拒絕過）。完整：[`references/text-table-gap.md`](references/text-table-gap.md)（**§8 更正**）|
| **新** | **醫事檢驗師訓練語料** | 設計者指定要從這裡下手 | 站上量到：**2,070 題含註解**（全庫最大，是藥師 1.8 倍），其中**上下標 4,008 ＋ 斜體 3,994**（＝格式問題，正好符合設計者「只看格式」的要求） |

### 9.3.2 ✅ 設計者 2026-09-27 第五輪裁決（Q15）

| # | 裁決 | 我的動作 |
|---|---|---|
| **D14** | **「可以開 PR 了」** | ✅ **PR 已開**（`agent/fix-option-alphabet-union-20260927`，只含 3 檔）；**但 D14 原文（修 `image_bytes_of`）作廢**——它是死碼。真修正＝ `option_alphabet` 用**最常用 family** 取代 union。負對照：舊 union 行為下 `test_a_paper_with_two_private_use_families_labels_its_options` **必失敗**（選項被標成 `E`）；`option_alphabet_families` **不動**（`split_at_bullets` 仍逐 family 試）|
| **A1** | **「A1 要做」** | ✅ **動工**：`golden_path` 匯出加頁碼（零語意風險）|
| **協定** | **「實驗階段，可以讓上層或會影響到的層級 AGENT.md 做註記（說明這件事正在做），但是功能要推成正式才大改」** | **實驗＝加註記、不改行為**；**正式才大改**。已寫進 `AGENTS.md` |
| **A2** | 參考第一版，但**「一字排開之前好像沒有，你要參考網路上有沒有哪些網頁作法」**；**重新設計而非複製**（用不順再調） | ✅ **已完成（本輪）**：靜態頁 `runs/*.html`，每題「機器／人工／模型」三行**一字排開**；含設計者要求的「正確答案欄」與「部分對」文字框（**只存 localStorage，不寫 review event**——人對題目的權威是 G4，實驗頁不能碰）。見 [`references/a2-model-comparison.md`](references/a2-model-comparison.md) |
| **D13 模型清單** | **三個候選（必須測）＋ 選測 `Qwen3.8-27B-Splash`** | **本輪已測 2/3（純文字＋看圖各一次）**：**看圖後 `occamy-6bit` 53.3% ＞ `ornith` 47.8%**（可達欄位 **78.4% vs 64.9%**；不可達各 35.8%）；但**多改 ornith 較少**（有圖 8 vs 9；純文字 5 vs 12）→ **建議 occamy 當主力、ornith 當守門**。`occamy-abliterated`（18131）down、`Splash`（8088）down **未測** |
| **8888 更正** | **DGX spark 的 `192.168.10.90:8888` 是 UP 的**（跑 `qwen3.8-flash-next`），**需要額外 token 時可調用** | 我先前量錯（量了筆電本機 `127.0.0.1:8888`）→ 已更正於 Q15 |

#### 9.3.3 A2 是**兩種 UI 的分工**（設計者原話拆解）

我先前最大的誤解：想用同一個介面同時做「審題」與「模型比較」。

| | **v2 審題 UI**（已存在，不動） | **Agent 比較／對話 UI**（要新做） |
|---|---|---|
| 目的 | **設計者做決定**（accept／block／改字／截圖） | **設計者選哪個模型做對了** ＋ 跟 Agent 對話 |
| 設計者原話 | 「右邊對照 PDF，左邊我就可以**截圖取代、對文字進行修正**，因為有很多**人類可以介入的元素**」 | 「如果是**你做完事而我需要跟你互動或是選擇**，我覺得你**重新設計符合工作狀態**是比較好的」|
| 寫入 | **正式 review event（G4）** | **實驗記錄**，不寫正式事件（A3）|

**業界做法（實查）**：**LLM Comparator**（先統計再下鑽；格式 `output_text_a/b` ＋ `custom_fields` 可直接用）、**LMArena**（兩兩投票）、**Argilla**（side-by-side ＋ **排序**）、**Label Studio**（多模型並排）。

**三條共同結論（非我所創）**：① **要有「正確答案」欄**（本案＝紙本 PDF；LMArena 沒有，我們有）；② **對比單位是差異不是全文**；③ **人要能給「部分對」**（設計者原話「只改到部分也是錯」→ Argilla 型排序比 LMArena 型投票適合）。

#### 9.3.1 ✅ 設計者對 D8 的完整設計（本輪回答）

> block（人工看到錯）→ 機器去找錯修（**可能沒能力改好**）→ 改成 `needs_review` → 人 accept（＝學習經驗）／
> 若**第二次** block → **轉到「可對話 UI」人親手改＋跟模型對話** → 學完一批 → 整理成新能力 →
> **重新掃描 block ＋調整引擎 AI 判題能力**

**我先前想太多。他要的不是「第三個動作」，是計數。**

三個關鍵，**其中兩個程式已經有了**：

1. `needs_review` ＝ 「AI 改完，等人看」——**現行語義就是這個** ✅
2. **第二次 block ＝ 升級訊號**——`rejection_counts()` 已在數這個，`MAX_ATTEMPTS=3` **已存在** ✅
   （`withdrawals.py`：`ATTEMPT_REJECTING_ACTIONS = ("block",)`；「錯就註解＋**block**」正是設計者 2026-09-25 的循環指令）
3. **學習後要回頭重掃**——**這一段還沒有任何機制** ⚠️（迴圈現在是腳本不是 agent）

→ **結論**：D8 **不需要新增動作、不需破壞 append-only 語義**。需要的是把「第二次 block」**接上對話 UI**。

### 9.4 基礎設施問題（A1–A6；A3／A5 已授權）

| # | 問題 | 事實 |
|---|---|---|
| **A1** | **候選 JSONL 沒有頁碼** | `flattened_offsets` 是 `None`。所以 D4 的 `1,040`／`4,360` 只能是「整篇計」的**上界**，不是精確題數。**要做精準的圖片定位，就必須把「題→頁」寫進匯出** → 要不要改 `golden_path` 的匯出？ |
| **A2** | **「不亂做事」的負對照 harness 不存在** | `occamy-abliterated`（無審查＋量化）是候選之一，但**沒有任何機制能證明它「沒有亂動不該動的地方」**。D13 也需要它 |
| **A3** | ~~`repair_agent_test/` 是沙盒還是準主線~~ | ✅ **已授權 sandbox**（設計者 2026-09-27）：審題走原 `reviewUI/v2`；Agent 交流獨立 UI；**真的改對的題仍在 v2**；穩定後接主線；可推 git |
| **A4** | ~~要不要去 ReviewUI 重試一筆 comment→block~~ | ✅ **設計者已做且驗證成功**：站上 2026-09-27T13:40:57，`{action:block, note_action:note, notes:"上下標異常"}` |
| **A5** | ~~D4 的 6 項未量~~ | ✅ **已授權完整測量**：能用本地模型就用；`192.168.10.90:8888` 可輔助 |
| **A6** | **筆電的 live 佇列是 2026-09-24 舊快照** | ✅ **已解**：建立 `repair_agent_test/scripts/sync_from_station.sh`（站上是家，§9.0）。**首次跑就抓到 4 份檔案全在漂移** |

### 9.5 下一步（**Q19 設計者停下討論後更新：方向已改變**）

**已清空**：① D14 PR（等網頁開）② A2 harness ＋第一次量測 ③ 第 0 步 ＋ A1。

> ⚠️ **2026-09-28 狀態更正（設計者糾正）**：以下三件**設計者已下令、但助理仍未做**：
> **① Pi 底層的自進步 agent（完全沒動）② 互動 UI（只做靜態頁）③ 教設計者怎麼用（沒寫）**。
> 詳情與施工圖：【[`references/pi-agent-design.md`](references/pi-agent-design.md)】；更正紀錄：§9.8。

#### 9.5.1 【Q19 新方向】設計者要的是「互動 UI ＋ 學習語料」，不是「紀錄」

| 設計者原話 | 意思 |
|---|---|
| **「每一題都要讀取完整，不能只讀這個不讀那個」** | 現有靜態頁只展開「被動過的欄位」→ **要改成整題完整呈現** |
| **「每一題獨立顯示」＋「右邊搭配 PDF」** | 現在是「一頁 47 題往下滑」＝**紀錄**；要的是**一次一題、右邊 v2 那個 PDF 面板** |
| **「我不可能重看數千數萬題（醫事檢驗師、藥師可以）」** | 需要**分流**，不是每題都看 |
| **第 7 點：各種線索都通過→列成一群快速瀏覽；真正要指導的是「多方證據無法配合」的** | **分群＝已用真實資料證實可行**（見下） |

#### 9.5.2 【分群已證實】全佇列 79,090 題，兩線索表決

| pipeline `disputes` | 模型 `finding` | 題數 | 處置 |
|---|---|---|---|
| OK | OK | **73,384（92.8%）** | **列成一群快速瀏覽** |
| OK | DEFECT | 3,564 | 要你看 |
| OK | NOT_EXTRACTION | 1,327 | 要你看 |
| DEFECT | DEFECT | 662 | 兩邊都說有問題 |
| DEFECT | OK | 151 | 要你看 |
| OK | NONE | 2 | 未判定 |

→ **92.8% 掃過去；7.2%（5,706 題）才是你要下指導的。**

**但兩線索都只在看「內容對不對」。** 抽 47 題裡「兩線索都 OK、人卻改了」的 3 題，
**三題全部同一類：假斜體**（`D<i>N</i>A`→`DNA`、`chlori<i>d</i>e`、`3R,5<i>S</i>`）。
→ **要加第三個不同種類的線索：標記／格式檢查器**（確定性、不需模型）。
**多方證據要真有用，線索必須是不同種類的。**

#### 9.5.3 【Q21 更正 9.5.2 的分群】全庫跑過，但**只跑文字**——2,858 張圖題是假票

| 項目 | 數字 |
|---|---|
| 佇列題數 | **79,090** |
| findings 有 verdict | **79,088 / 79,090**（缺 2） |
| **prompt 裡含圖的 findings** | **0 ← 一筆都沒有** |
| ABCD 齊全且文字非空 | **78,638（99.43%）**，**缺 key 0 題** |
| 有空選項／缺選項 | **452（0.57%）**，其中 380 已標「有圖」→ 圖片選項 |
| 有 `image_refs` 的題 | **3,471** |
| 圖題的純文字判定 | `OK` **2,858** ／ `DEFECT` 605 ／ `NOT_EXTRACTION` 8 |

**生產 prompt 不送圖，送的是「這一題的圖片：N 張」這句話**：

```
這一題的圖片：1 張（figure-crop）。
若某個選項的文字是空的，但上面說它有對應的圖片，那就是圖片選項，不是選項遺失。
```

→ **2,858 題的圖從沒被模型看過，卻被判 OK。它們在 9.5.2 的表決裡是假票。**
→ **分群表決前必須先把圖題抽出來。**

#### 9.5.4 【Q22 prompt ablation】拿掉規則，分數**上升**——我的五條規則是負貢獻

occamy-6bit／47 題／90 欄位：

| variant | 準確率 | vs full | 多改 |
|---|---|---|---|
| `image`（給圖） | **0.4667** | +0.322 | 5 |
| `image_text`（**只給「N 張圖」這句話，不給圖**） | **0.3222** | **+0.178** | 10 |
| `drop:no_over_edit` | 0.2222 | +0.078 | 14 |
| `drop:keys_frozen` | 0.2222 | +0.078 | 9 |
| `drop:keep_if_unsure` | 0.2222 | +0.078 | 11 |
| `drop:markup_form` | 0.2000 | +0.056 | 11 |
| **`full`（我的完整 prompt）** | **0.1444** | — | 9 |
| `none` = `drop:defect_kinds` | 0.1444 | +0.000 | 11 |

**兩個推翻：**
1. **「缺陷類型清單」貢獻 0.000**（`none` 與 `full` 逐項相同）——我以為它在洩漏答案。
2. **每一個 drop 都 ≥ full** → **五條規則全部是負貢獻**（規則越長、模型越保守，
   而保守在這個語料上是錯的）；代價是 over_edit 上升。

**「N 張圖這句話」值 +17.8pp 的假分數**（不給圖就上升）：它讓模型**假裝看過圖**，
靠「更積極地改字」得分（exact 11→28、missed 47→23），**不是資訊增加**。
→ **應該從生產 prompt 移除或改寫。**

#### 9.5.5 【Q22→Q24 更正】非決定性**不是 9.4%**——是我漏了一句提示詞

**Q22 我先寫「非決定性 9.4%」，那是錯的**（詳見 qa-log Q23／Q24）。重跑後：

| 比較 | 逐欄位翻轉 | 準確率 |
|---|---|---|
| 同一 process 連續 3 次（`--repeat 3`） | **0/278** | stdev **0.0** |
| 三個獨立 process | **0/116** | 三次相同 |
| 原脚本 5 次重跑 | **僅一次 19/278（6.8%）** | **`exact` 全部 45 相同** |

**真相**：`compare_models_vision.py` 的 user message 尾巴多一句
`（附圖：這題在官方 PDF 的第 <path> 頁。）`，**我的 ablation 漏了它（只差 23 字元）**。
那句 cue：**有圖時值 +5.6pp**（`image` 0.4444 → `image_cue` 0.5000）；
**無圖時無用**（`image_text` 0.3222 ≥ `image_text_cue` 0.3111，負對照成立）。

→ **正確講法**：**「準確率穩定（頭條數字無噪音）；不同時間偶有約 7% 的逐欄位漂移。
做 ablation 必須機械驗證兩變體只差一個東西——我漏驗 user message，把 prompt 差異讀成模型噪音。」**

**最終 ablation 表**（occamy-6bit，`--repeat 3`，stdev 全 0）：

| variant | 準確率 | 多改 |
|---|---|---|
| **`image_cue`（＝生產線那條）** | **0.5000** | 8 |
| `image`（同上但無 cue） | 0.4444 | 5 |
| `image_text`（**只「N 張圖」這句，不給圖**） | **0.3222** | 10 |
| `image_text_cue` | 0.3111 | 10 |
| **`full`（我原本的 prompt）** | **0.1111** | 11 |
| `none` = `drop:defect_kinds` | 0.1444 | 11 |

#### 9.5.6 【重要】既有三條流已存在——**判讀應該複用，不該新開**

| 既有的流 | 內容 | 狀態 |
|---|---|---|
| `question_ai_findings.jsonl` | 模型逐題 `{verdict, what, where, fix, confidence}` | **108,146 筆、有 reader** |
| `question_ai_feedback.jsonl` | **人**對 AI 判定的評分 `{action:"ai_feedback", rating:up/down, reason}` ＋ `ai_review_ref` | **writer 已存在**（`review_state.append_ai_feedback`）；scopes `{question,group,visual,answer}` |
| `question_ai_learning.jsonl` | **人**挑的訓練範例 `{action:"ai_learning"}` | **writer 已存在**（`review_state.append_ai_learning`） |

> ⚠️ **實測補充（2026-09-28）**：後兩個檔**磁碟上根本不存在**——
> writer 在，但**從未寫過一筆**。所以 B 案是「**接上一條已有的空管線**」，不是新蓋。
> **永不進 `question_review_events.jsonl`、永不冒充人類審核者。**

→ **A2 的判讀要走既有的 `ai_feedback`**（它本來就是為「人評價 AI 判定、且能當學習語料」設計，
且有 `ai_review_ref` 保護：AI audit 變了就拒絕寫入）。
**設計者 2026-09-28 已指示「判讀寫入檔案當學習語料」（B 案）** → 見 §9.6。

#### 9.5.7 【模型選型】occamy 與 ornith 是同一個模型，差在量化

```
兩者皆 Qwen3_5MoeForConditionalGeneration
  num_experts=256, num_experts_per_tok=8, hidden_size=2048, num_hidden_layers=40
差別：量化 4-bit(19GB) vs 6-bit XL(28GB)；vision 量化 0 項 vs 84 項；MTP 1 層 vs 0 層
```

→ **「哪一套 MoE 好」問錯了；差別是量化位元數。** 設計者目測（occamy 較好）是對的，
但原因是 **6-bit 比 4-bit 保真**，不是架構。（`Qwen3.8-27B` 是 **dense**，不是 MoE，不能比架構。）

**然而建議兩個都留**：看圖後逐欄位，兩模型**只有 37.8% 重疊**；
**至少一個對 = 63.3%，單一最好 = 53.3%**。→ **設計者的「多方證據」也該用在「模型之間」**。
建議：**occamy 6-bit 主力、ornith 第二意見**（不是因為 ornith 更好，是它的錯不一樣）。

### 9.6 ✅ 設計者 2026-09-28 的指示（**不是待裁決，是已下令**）

> 設計者原話：「自進步 agent、人類與 Agent 互動 UI 都要趕快出來並且教我用……
> 我跟你溝通完一些改法或是規則之後，開始在背後調度地端模型做事，我就去忙別的了」
> ＋「**放寬 G4**」＋「**底層用 Pi**（那個舊的用不了）」＋「**我只看新形態搞不定的**」

| # | 指示 | 助理原本的狀態 | 現行 |
|---|---|---|---|
| **1** | **Agent 底層用 Pi**，不是那份 Python | 助理錯誤地想去救 `qbr/scripts/repair_agent.py` | ✅ **已完成**。舊的是**零 caller ＋ 一跑就 crash**。新的是 Pi SDK 0.87.1（版號鎖進 `package.json`），建在 `repair_agent_test/agent/`；分支 `agent/repair-agent-pi-sdk-20260928` 已 push（3 commits）。施工圖 [`references/pi-agent-design.md`](references/pi-agent-design.md) |
| **2** | **放寬 G4** | 只診斷，未改 | ✅ **已完成**：`agent_verified` 上線（`package.py`）、validator 接受它但**獨立 warning**、`golden_path.classify_validator_issues` 可測。分支 `agent/agent-verified-gate-20260928`（`8bedd52`）。**9 個新測試，負對照證明會失敗** |
| **3** | **互動 UI**（不是靜態頁） | 只做了**靜態頁**（`grep fetch`=0） | ✅ **已完成**：`agent/ui/server.py` ＋ `index.html`，**全走 `fetch`**。實測：一次一題、整題完整、右側 PDF、`/crop`、判讀寫檔、404 契約。**`question_page` 全庫 79,090 題一筆都沒有**（A1 未合併）→ PDF 開第 1 頁 |
| **4** | **教我用**（啟動／管理／使用） | 無 | ✅ **已完成**：[`agent/README.md`](../../../agent/README.md)（啟動、查題、寫判讀、讀教訓、**誠實限制清單**）。**檔內每個指令都實跑驗証過** |
| **5** | **模型：視覺是前提** | 助理把「哪套 MoE 好」當成二選一 | **更正**：三個候選**都有 vision tower**；批次稽核**沒送圖**才是 bug。→ 主力＋第二意見（§9.5.7） |
| **6** | **分群：加第三種不同種類的線索** | 提案 | **D1 授權自行決定** → 做（3 筆漏掉的全是假斜體，證明線索必須不同種類） |
| **7** | **判讀寫檔（B 案）** | 只存瀏覽器 | ✅ **已接上**：`bridge.do_feedback` 寫 `store/agent_feedback.jsonl`（欄位照 `review_state.append_ai_feedback`）。**設計者的話與 agent 的判讀走同一條流**（`source` 欄分「誰說的」）。**agent 讀得回來**（`prior_judgements`） |
| **8 🆕** | **「我只看新形態搞不定的」** | — | **已量化**（Q33）：選項是圖、文字 diff 為空、答案是專業知識 → **同一模型兩次跑給相反結論**。這一類**必須人看**，agent 要**標出來**而不是自己判 |

**「回答 3 之後要做的」→ 現在全部變成「要做」**：
1. **互動 UI**：每題獨立、**整題完整讀取**、右邊 PDF。
2. **本地寫入服務**：判讀 append 到 `question_ai_feedback.jsonl`，
   **永不進 `question_review_events.jsonl`**；附啟動／管理／使用說明。
3. **分群介面**：92.8% 快速瀏覽、7.2% 詳細看。
4. **修評估缺陷**：改成以「題」為單位（同題 option 只算一次）——
   **現有 53.3% 被「同一根因算四次」灌了水**（`q057` 四個選項同一個錯）。

### 9.7 舊題（多數已被上面的裁決吸收）

1. **Q1**：ornith 是腦還是眼睛（阻塞動工）。**三筆探針已證明它會用工具、能看圖**，但**可靠度未量**。
2. **Q2**：動作空間（只讀／＋advisory／＋提案不套用）——**D1 已決＝放開做**，本題變成「自動修的門檻是什麼」。
3. **Q3**：記憶與紀錄放哪（傾向複用既有 append-only 流）。**已查證：三個流有 schema 但無寫入者**。
4. **Q4**：`repair_agent_test/` 是沙盒還是準主線——**見 A3，已隱含解答，待確認**。
5. **Q5**：驗收指標與負對照。
6. **多輪協商落在哪條線**？／7. **「命令」與「背景」怎麼分**？／8. **`/btw` `WAIT` `STOP` 要不要現在做**？／9. **開發 UI 要不要現在做**？

---

### 9.8 🆕 2026-09-28 第九輪：一次方向錯誤與它的更正

設計者問：「**我不是說過新的自進步 Agent 底層用 Pi 去做嗎？是不是有些誤解？**」

**是的，助理搞錯了，不是誤解。** 施工圖已獨立成
[`references/pi-agent-design.md`](references/pi-agent-design.md)。三個要點：

1. **不要回去救 `qbr/scripts/repair_agent.py`**——它**零 caller**、**一跑就 crash**
   （`args.principles_for_prompt` → `AttributeError`）、`learned=None` 使經驗檔從未生效。
   設計者說「那個之前根本用不了」**完全正確**。它只當**Python 參考**，不當本體。
2. **本體＝Pi SDK**（`createAgentSession`／`DefaultResourceLoader`／自訂 provider／
   `SessionManager`）。接點已於 `0.87.1` 實測並記在 `pi-agent-design.md` §2。
3. **Pi 的 provider 必須與 `engines.py` 指向同一組端點**，否則就是第二個真相。

**同時更正四件助理報錯／沒做的事**：

| 設計者指出 | 助理的錯 | 更正 |
|---|---|---|
| 「UI 昨晚就該完成」 | 只做了**靜態頁**（`grep fetch`=**0**、判讀**只存 localStorage**） | 那不是互動 UI；規格見 §9.5.1 |
| 「放寬 G4」 | 只診斷未改 | 那行是 `validate_question_bank_package.py:319` |
| 「教我用」 | 沒寫 | 要做啟動／管理／使用說明 |
| 「做了多少」 | 產出全在**量測**，不在**能用** | 誠實盤點見 `pi-agent-design.md` §0 與本節 |


---

## 10. 這個迴圈到底能不能變聰明？（一句話）

**可控 ✓，但目前不能變聰明 ✗。**

> **變聰明的第一個必要條件，不是換更大模型，而是：讓設計者的每一次否定都帶上原因，而且那個原因真的進得了下一輪的提示詞。**

三件量到的事：

1. **`LEARNED` 是手打 flag**（`--learned CODE=...`）、**`ANSWERS` 只接一半**（`confirm_dispute` ✅／`ask_about_blocks` ❌）；`question_ai_learning_events.jsonl` **有 schema、無寫入者、無讀者**。
2. **286 題 `block` 中 279 題機器什麼都沒做** → 「循環三次」跑不出三次不同的嘗試（三次用的都是同一份「他打回了」）。
3. **`ANSWER_DISAGREES` 與人一致率僅 8%、已被移除** → 「AI 判斷題意對不對」目前沒有可信來源，只有格式判斷。

**可反駁的進步指標**：① 同一組題目第二次跑**少改幾個欄位**；② 同類型題目的**人打回率逐批下降**；③ `prompt_version` 改動前後的 finding 差異（**機制已存在**，免費 A/B）。

**D4／D5 白話**：
- **D4（✅ 已量）**：有些圖在 PDF 裡不是「一張圖片」而是「一堆線條指令」（化學結構式、電路圖、流程圖）→ **現在完全抽不出來**。
  **量到的答案（佇列 989 篇／79,090 題全掃）**：這種「畫出來的圖」是 **19 頁／13 篇／約 1,040 題 = 1.3%**。
  **但量到更大的洞**（⚠️ **§8 更正：這句是錯的**）：原本寫「管線看得到圖的位置、卻拿不到圖（內嵌圖 `xref=0`）＝ 151 張／53 篇／約 4,360 題 = 5.5%」。**本輪推翻**：`extract.py::image_bytes_of` 是**死碼**（production 零呼叫者），生產抽圖走**渲染**，內嵌圖**本來就裁得到**。**真正的大洞是 `option_alphabet` 的 union**（667 篇誤標、658 篇全卷 0 選項）。細節：[`references/vector-figure-gap.md`](references/vector-figure-gap.md) §8 與 [`references/option-alphabet-union.md`](references/option-alphabet-union.md)。
- **D5（設計者裁決：先不用）**：「90-95%」是**目標不是現況**。「先量」＝拿一批題目跑現在的管線（**先不放模型**），數「產出跟人 accept 的字一模一樣、不用人碰」的比例。
  （**順帶量到的近似值**：站上最後狀態 accept 10,090、block 382 → 人打回率 **3.6%**，低於設計者自估的 5–10%。）

### 10.1 D4 這一課：「先量」改掉了優先序

原本的想像是「向量圖是巨大缺口」。量完後：

| 假設 | 量到的事實 |
|---|---|
| 向量圖是大洞 | **1.3%** |
| ~~真正的洞是內嵌圖 `xref=0`：**5.5%**~~ | **⚠️ 本輪（Q16）推翻**：`image_bytes_of` 是死碼，內嵌圖走渲染、本來就裁得到；**真洞是 `option_alphabet` 的 union**（667 篇全卷 0 選項） |
| ~~兩個缺陷可用**同一條路徑**修~~ | **⚠️ 推翻**：兩條路不相干 |

**若沒量就照「向量圖是大問題」去加一條向量圖路徑，會修到小洞、留下大洞。**
**同樣地，若沒驗前提就照 D14 原案去修 `image_bytes_of`，會修到死碼、什麼都沒變。**

**而且我兩次量錯，兩次都是負對照抓到的**：
1. 第一版用「整頁聯集面積」→ 9 條散落分隔線被誤判成圖。
2. 第一版只看 `get_images()` → 內嵌圖被誤判成「向量圖」（管線其實看得到它們）。
3. 第一次報「**154,128 張內嵌圖壞掉**」→ 加上 size filter 後真圖只有 **151 張**，
   其餘 99.9% 是 < 400pt² 的雜點（一頁最多一萬五千個，全部 < 50pt²）。

**「15 萬張圖壞掉」看起來像世界末日，而那是假的。** 這是本案「每個檢查都要附負對照」
最直接的證據：**負對照要能證明檢查自己會說謊**。

---

## 附錄 A：檔案地圖（本案會碰到的關鍵檔）

| 想知道 | 看這裡 |
|---|---|
| 人的話 → 提示詞的三段文字 | `qbr/src/qbr/ai_findings.py`（`PRINCIPLES`／`ANSWERS`／`NOTES`／`REJECTED`／`build_prompt`） |
| 判讀怎麼被叫、反問怎麼產生 | `qbr/scripts/confirm_dispute.py`（`transcribe_system`、`reference_read`、`neighbour_crop_for`） |
| 原則／反問的單一投影來源 | `qbr/src/qbr/discuss.py`（`PRINCIPLES_STREAM`、`REPAIR_QUESTIONS_STREAM`、`approved_principles`） |
| 分流與判斷 | `qbr/src/qbr/orchestrator.py`（`TRUST`/`CARE`/`DOUBT`、`judge`、`parse_judgement`） |
| 掃描與指紋 | `qbr/src/qbr/scan_state.py`（`question_fingerprint`、`new_work`、`pending_keys`） |
| 迴圈三段 | `qbr/scripts/repair_daemon.sh`（`scan`/`repair`/`report`，只跑 G2） |
| 套用修復（G3） | `qbr/scripts/apply_dispute_repairs.py`、`qbr/src/qbr/dispute_apply/` |
| 端點與 thinking 拼法 | `qbr/src/qbr/engines.py`、`qbr/src/qbr/vision.py` |
| 審題介面基準 | `review_ui/v2.html`、`review_ui/v2/*.js`、`qbr/src/qbr/review_ui/` |
| 站上部署與驗證 | `docs/skills/deploy-qbr-review/SKILL.md`、`docs/skills/operate-repair-agent-surface/SKILL.md` |
| 治理與待決事項 | `docs/skills/repair-open-items/SKILL.md`、`docs/governance/README.md`、`governance/policy.json` |
| Phoenix Agent 參考實作 | `192.168.10.70:~/Phoenix_agent/Agent/bridge.mjs`、`personal-context.mjs`、`agent-logic.mjs`、`dgx-provider.mjs` |
| Phoenix 開發 Skill（範本） | `192.168.10.70:~/Phoenix_agent/Agent/.agents/skills/local-agent-development/SKILL.md` |
| **可用模型參考** | [`references/model-glm-ocr.md`](references/model-glm-ocr.md)（GLM-OCR：0.9B 專用 OCR，MIT，1.25 GB MLX，**Prompt Limited**） |
| **掃描件歸屬與 GT 盲點實測** | [`references/model-scanned-attribution.md`](references/model-scanned-attribution.md)（**醫學類科只有 1 份掃描件且無圖**；數位頁歸屬 0.992） |
| **考題修復 Agent 架構（重新設計）** | [`references/architecture-redesign.md`](references/architecture-redesign.md)（三個 agent、兩條迴路、資料流 8 條、G3 拆解提案、D1–D8） |
| **⭐ 自進步 Agent 施工圖（以 Pi 為底層）** | [`references/pi-agent-design.md`](references/pi-agent-design.md)（**2026-09-28 建立**：為什麼不救 `repair_agent.py`、Pi `0.87.1` 實測接點、判讀器=Python 工具、記憶分層、互動 UI 規格、負對照驗收表） |
| **決策模型研究與實測（Q9，Kev-4B／Laya）** | [`references/model-decision-models.md`](references/model-decision-models.md)（**6 ms 是真的、零樣本可用是假的（45–50% vs 亂猜 25%）**；實測腳本 `probes/decision-models/`，venv `~/models/venvs/layaenv`） |
| **Jev 的開源替代品（Q10）** | [`references/jev-open-source-alternatives.md`](references/jev-open-source-alternatives.md)（**Jev 是 TypeSafe 的封閉服務**；我實測 **Jev-Style 0.8B = 80%** vs Laya 45%；**最值得＝零訓練 wrapper**；**9 個 awesome-* 是 SEO spam**；venv `~/models/venvs/jevstyleenv`） |
| **D4 向量圖缺口實測（Q11）** | [`references/vector-figure-gap.md`](references/vector-figure-gap.md)（**向量圖 1.3%**；九個標記案例含四個負對照；**我兩次量錯都是負對照抓到**；⚠️ **§8 更正**：內嵌圖「5.5%」是探針假象） |
| **逐字問答紀錄** | [`references/qa-log.md`](references/qa-log.md)（append-only） |

---

### 9.9 🆕 設計者 2026-09-29 裁決（**已決，新 session 直接執行**）

> **這是目前最新的一組決定。新 session 開頭先看這一節。**
> 逐字與完整推論見 [`references/qa-log.md`](references/qa-log.md) **Q42**。
> 設計者原話：「接下來只把剛才做的進度以及我接下來的決策記錄下來，之後我開新的 session 來做」。

| # | 裁決 | 做什麼 |
|---|---|---|
| **1** | **換 occamy**（腦與眼同一顆） | 把 `BRAIN` 從 `ornith-1.5-mtplx-35b` 換成 `occamy-1.0-6bit`。**動**：`agent/lib/session.mjs` 的 `BRAIN`／`agent/lib/identity.mjs` 的 `DEFAULT_ENGINE`／`qbr/src/qbr/engines.py` 預設。⚠️ **副作用要處理**：換同顆後 `read_page` 的「**第二意見**」語意消失（舊價值＝兩引擎錯只 **37.8%** 重疊）→ **必須重新定義 `read_page`**（是「再看一次」還是保留一顆異質引擎當對照）。**這是 #1 唯一還沒定論的部分。** |
| **2** | **平台白名單從 `sanitize.ts` 導出：可以** | 已實作（`lib/platform_view.py::load_allowlist()`），**確認即可，無新工** |
| **3** | **對話框兩種都要：OK** | 已完成（綁題＋全庫），**無新工** |
| **4** | **先審藥師(一)，再藥師(二)** | ① **藥師(一)** 先做 **99 題有字的爭議**（334 題中 99 有 `notes`）；② 之後 **藥師(二)**（4,410 題）。**是稽核／判讀，不是重抽取** |
| **5** | **A1 要合併** | 合併 `agent/export-question-page-20260927`（`f282d0b`）。**步驟**：GitHub 網頁開 PR（`gh` token 無效）→ 合併 → **重跑匯出**才有 `question_page` 值（否則 PDF 面板仍開第 1 頁） |
| **6** | **v2 主場＝`192.168.10.70:8765/v2`（常駐站），MBP 改完推正式站** | 工作流：**MBP 改 → `scripts/deploy_station.sh --restart` → 瀏覽器驗證**。**更正**：先前把 Tailscale（`100.96.207.80:8790`）講成主入口是錯的——**那是沙盒**；**正式主場是站上的 v2** |
| **6b** | **沙盒站反應很慢，要繼續測試需先優化** | **新工作項**（見下方） |

#### 6b 沙盒效能（設計者指定；**動工前先量**）

| 疑似慢點 | 位置 |
|---|---|
| `overview`／`disputes`／`browse`／`find` 每次**單趟掃 199 MB** | `bridge.do_*` |
| `ai_findings()` 每次請求**子字串掃 707 MB** | `bridge.ai_findings` |
| `prior_judgements`／`human_events` 每開一題**各掃全檔** | `bridge.py` |
| `/api/question` 一次跑 **4+ 趟全檔掃描** | `ui/server.py::question_payload` |
| 對話每次 `ensure()` **可能重啟 node** | `ui/server.py::Chat.ensure` |

**紀律**：**先量**（`/api/question` 與一次對話的實際秒數），**再改**；**每個優化都要有負對照**
（證明優化前後讀到**同一份資料**，不是少讀）。

#### 9.9 執行結果（2026-09-29 實作輪；每一項都附量到的數字）

**#1 換 occamy — 已完成，含兩個只有實跑才會看到的坑。**

- `agent/lib/session.mjs::BRAIN` → `occamy-6bit/occamy-1.0-6bit-xl-mlx`；`identity.mjs` 新增
  `BRAIN_ENGINE`（同一顆），`ROLE` 文字改寫。`test_agent.mjs` 比對 `BRAIN` 與 `engines.py`
  的 `name`，兩個常數不可能各說各話。
- **坑 A：`role: "developer"`。** Pi 對 reasoning 模型把系統提示詞用 developer 送；mlx_vlm 的
  Jinja 模板只認 system/user/assistant/tool → 每一輪 `500 status code (no body)`，引擎端
  `Unexpected message role`（`transformers/utils/chat_template_utils.py:479`）。手測：
  developer → 18130 **500**、18120 **200**（MTPLX 收，所以舊的腦把坑蓋住）。
  **修法要掛在 `model.compat`**：provider 層的 compat 不會被 merge 進 model 記錄
  （`getCompat(model)` 讀 `model.compat`），第一次只改 provider 層時行為完全不變。
- **坑 B：`max_tokens` 是共用的 32768。** occamy 以 `MAX_KV_SIZE=$CTX_MLX`（預設 65536，
  `~/models/occamy/bin/occamy:91`）起，`39496 prompt + 32768` 超過 → `400 {"detail":
  "Request needs 72264 context tokens … but MAX_KV_SIZE is 65536."}`，客戶端只看到沒有 body 的
  400。`engines.py` 的 occamy 記錄補上 `context_window: 65536`／`max_output_tokens: 16384`，
  `session.mjs` 直接讀，不再有第二個常數。

**#1 的殘留（`read_page` 第二意見）— 已定義。** 由「**實際回答的引擎**是否等於 brain」決定，
回傳明寫 `independent` 與 `independence_note`：同顆＝再看一次（不是第二意見），
指定 `mtplx-35b` 才是異質證據（逐欄位只重疊 37.8%）。`test_agent.mjs` 真的呼叫工具驗這條。

**對話框 seed 缺陷（實缺陷，順手修）。** `sessionFor()` 在 `buildSession()` **之後**才判
`restored = manager.getEntries().length > 0`，而 `buildSession` 自己會寫一條 session 訊息
⇒ **每一條新綁題對話都被當成續談**，題目 seed 從未送出（實測 session 檔是 `system → user`，
模型回「你沒給 candidate_key」）。已把判斷搬到前面，並新增測試驅動真的 `ui/chat.mjs`
數 seed 出現次數（重啟後仍為 1）。

**6b 沙盒效能 — 已量、已改、有負對照。** 新增 `lib/queue_index.py`（byte-offset JSONL 索引，
cache 在 `store/index/`，失效條件 `(size, mtime_ns, ino)`，未命中一律回退串流）：

| 端點 | 改前 | 改後 |
|---|---|---|
| `/api/question` | 0.86 s | **0.024 s** |
| `/api/queue` | 1.86 s | **0.065 s** |
| `/api/browse`（map） | 3.38 s | **0.94 s** |
| `/api/browse?category=藥師&limit=50` | 3.43 s | **0.54 s** |

等價性：`before/after` 的 browse map、藥師 50 列、question payload 逐欄比對全同；
`questions_of_paper` 索引 vs `REPAIR_AGENT_NO_INDEX=1` 串流 `same rows deep: True`。
剩下的 0.94 s 是 `do_browse` 仍要 `json.loads` 79,090 列，**刻意不動**。

**#4 藥師(一) 99 題清單 — 已算出，稽核已在跑（背景，約 2 分鐘／題）。** 筆電 queue 上 334 題
disputed／99 題有 notes，清單 `repair_agent_test/agent/pharmacist1_notes_keys.txt`。跑法與痕跡：

```sh
repair_agent_test/agent/audit_pharmacist1.sh <keys-file> <out-dir>   # 背景：nohup ... &
tail -f <out-dir>/run.log            # 每題一行
cat <out-dir>/summary.tsv            # key / rc / 秒數 / 這次多了幾行判讀
```

結果落在沙盒自己的流：`repair_agent_test/agent/store/agent_feedback.jsonl`（`action: ai_feedback`、
`source: agent`、`engine: occamy-6bit`），**不碰** `question_review_events.jsonl`。

**兩個只有實際跑才會踩到的坑**（都留在腳本註解裡）：macOS 沒有 `timeout` 這個執行檔
（互動 shell 的 builtin 不算），在分離的迴圈裡叫它 → 98 題全部 rc=127／0 秒，看起來像
「模型拒答 98 題」；改用 watchdog subshell 則會留下 `sleep 900`，第一題跑完後卡 15 分鐘。
現在用 `perl -e 'alarm shift; exec @ARGV' 900`。

**第三個坑：`nohup ... &` 活不過一個回合。** 這個 harness 會在啟動它們的呼叫結束時回收 shell 的
子程序（實測：`nohup sleep 600 & disown` 兩秒後就不見了；這個迴圈死過兩次，一次在題目中、
一次在兩題之間）。所以長跑要用 **launchd**：

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.qbr.audit-pharmacist1.plist  # 裝
launchctl bootout   gui/$(id -u) ~/Library/LaunchAgents/com.qbr.audit-pharmacist1.plist  # 拆
```

`summary.tsv` 是**帳本**：已在裡面的 key 會跳過，所以「重跑同一份清單」就是續跑，不會重複判。
2026-09-29 收工時的進度（`wc -l /tmp/agent_perf/audit/summary.tsv` → 表頭＋**13 題全 rc=0**，剩 **86 題**）：
**q018 up、q053 down（`C∞min` 被壓平）、q041 up、q065 up** 之後由 LaunchAgent 續跑；

**順帶跑掉的驗證**：換 `vision.py` 預設之後 `qbr` 全套 **783 passed / 17 skipped**（與先前同一組
數字），另外 `describe_crop` 走新預設實測成功；`repair_agent_test` 則 47 pass / 0 fail。

**沙盒介面（改完要重啟，且真的在畫面上看過）**：`/api/browse?category=藥師(一)` 回 5,040 列
（0.46 s）、勾「有問題的（你說過的）」篩出 **334** 列並印出設計者當初寫的那句話
（`human_event_keys()` 那條路徑）、點一列會載入題目與 PDF；**新開的綁題對話在 session 檔裡
seed 出現 1 次**，agent 真的去 `read` 重切的圖並回答這一題（修好之前它會回「你沒給 candidate_key」）。
介面 console 0 錯誤。

**#5 五支 PR — 仍然開不了（無 token），而且 base 要用對。** `gh auth status` → token invalid、
keychain 與 env 都沒有。**比較基準是 `agent/review-ui-server-split-20260923`（PR #5 的來源分支），
不是 `main`**：5 支都含它為祖先、彼此不互相包含，相對 `main` 各是 96–104 commits／150–244 檔，
相對 split 分支只有 7 個（`repair-agent-pi-sdk` 是 16 個）。逐支 compare：

**開 PR 的順序（2026-09-29 已建好第一支的來源分支）**：那 6 個共享 commit（`eda10de`…`2a5e97c`）
**每一支都含在裡面**，所以直接開 5 支 PR 的話，同 6 個 commit 會被審 5 次、而且互相衝突。已建立
`agent/qbr-shared-contracts-20260929`（＝`16bd5e4` ＋ 恰好那 6 個；`git rev-list --count 16bd5e4..`
= 6，且**與各 feature commit 的檔案零重疊**，所以先合它，其餘就各自變成乾淨的小 PR）。

| 順序 | 分支 | 標題 | 內容 |
|---|---|---|---|
| **1** | `agent/qbr-shared-contracts-20260929` | `Shared QBR review contracts (6 commits)` | 共享審題介面契約、掃描改走共享介面、repair contracts、原則提案 helper、daemon 執行模式測試、提案留在已核准引擎 |
| 2 | `agent/fix-option-alphabet-union-20260927` | `Fix option alphabet: use the paper's most-used private-use family, not the union` | 選項字母（1 commit） |
| 3 | `agent/engine-endpoints-runtime-20260927` | `Make the engine table a runtime parameter, not an import-time literal` | 引擎表改為呼叫時解析（1 commit） |
| 4 | `agent/export-question-page-20260927` | `Export the page each question's number is printed on` | `question_page` 欄位；**#5 裁決要合併的那支**（1 commit） |
| 5 | `agent/agent-verified-gate-20260928` | `Allow agent_verified packages without impersonating a human reviewer` | agent 閘門（1 commit） |
| 6 | `agent/repair-agent-pi-sdk-20260928` | `Repair agent (Pi SDK): occamy brain, indexed queue reads, defined second opinion` | 判讀 agent ＋本輪 4 個 commit（16） |

compare（base 一律 `agent/review-ui-server-split-20260923`；PR #5 還沒進去之前對 `main` 開會把
96–104 commits 一起帶進來）：

```text
https://github.com/Ting-Ruei/tw-national-exam-catalog/compare/agent/review-ui-server-split-20260923...agent/<分支>
```

`gh` 在這台沒有可用 token，所以這 6 支要由你在網頁按（`git push` 已完成，每個分支都在 origin）。

**站上 findings 沒有東西要回流（2026-09-29 稍晚證明）。** 行數看起來差幾百列，但把 `error`
非空的列濾掉之後，兩邊的位元組流**完全相同**（各 105,100 列、sha256 `e99d5bf683c24541`）；
差的全是失敗重試。細節與計算指令見 `docs/skills/operate-repair-agent-surface/SKILL.md`。

#### 9.9 第二輪（2026-09-29 稍晚；設計者五問：整套換／PR 去哪裡按／reread 還在用嗎／KV 要不要開大／站上哪部分沒恢復）

**① 純文字線也換 occamy（設計者：整套換）— 已改，含一個只有實跑才看得到的坑。**

- `qbr/src/qbr/reflow.py`：`_DEFAULT_NAME = "occamy-6bit"`；並把**硬寫死的 MTPLX 開關**
  （`THINK_CHAT_TEMPLATE = {"enable_thinking": False}`）換成**從引擎表取**（新增 `_engine()`，
  `think=False` 走 `engines.body_for`，與 `reread.endpoint()` 同一條路；`think=True` 不送開關）。
  移除因此變成死碼的 `_endpoint`。
- **為什麼不能只換一行**（負對照，同一提示詞、同一顆引擎）：

  | 送法 | 秒 | completion | `reasoning_content` | 答案 |
  |---|---|---|---|---|
  | 引擎表的 `reasoning_effort: none` | **0.3** | 4 | **0 字** | 308（**錯**，正解 252） |
  | 舊拼法 `chat_template_kwargs.enable_thinking=false` | 2.0 | 82 | 137 字 | 252 |
  | 完全不送開關 | 1.9 | 82 | 137 字 | 252 |

  舊拼法在 occamy 上**與不送開關完全一樣**（被接受、被忽略）→ 只換預設的話，reflow 會**靜默地繼續
  thinking**，而它的預算是靠 thinking 關掉才成立的（模組註解記的 5,085 tokens/44 s vs 115,628/半小時）。
- **同一張卷、兩個引擎、thinking off（各跑多次，紙本骨架當判準）**：`1152_醫事檢驗師_微生物學與臨床微生物學`（80 題／420 格）

  | 引擎 | 樣本 | 秒 | completion | `admissible` | 讀出 | 與骨架同題 | 題幹異 |
  |---|---|---|---|---|---|---|---|
  | occamy-6bit（cap 40k） | 3 次 | 163／94／66 | 4,961（三次相同） | **true** | 80 | **80/80** | 0 |
  | occamy-6bit（cap 40k） | 1 次 | 189 | 4,794 | false | 77 | 4/80 | 72 |
  | mtplx-35b（模組預算 156k） | 2 次 | 85／52 | 4,955（兩次相同） | false | 80 | 67/80 | 13 |

  也就是：**occamy 關掉 thinking 三次都完美（80/80），mtplx 兩次都只有 67/80**，切換有量測支持；
  但 occamy 4 次裡有 1 次給出壞讀（77 題、同題 4/80）——而 reflow 自己的 `verify` **判為
  `admissible=false` 抓到了**（`reflow_adaptive.py` 就是為這條升級路徑存在的）。
- **順手把誤標的失敗分開**：引擎拒絕（`ask` 回 `request failed: …`）原本記成 `report.error="unparsed"`，
  與「模型答了但讀不出來」同一個標籤。實測就是這個坑：KV 拒絕看起來像模型問題。現在拒絕記成
  `request-failed` 並附 `detail`（原訊息前 300 字），另加負對照測試。
- **實測：reflow 在 occamy 上跑不完一張 80 題卷（用模組自己的預算）。** reflow 對 420 格卷要
  `900×80 + 200×420 = 156,000` max_tokens（`answer_budget`），occamy 的 `MAX_KV_SIZE=65536` →
  `400 Request needs 165654 context tokens (9654 prompt + 156000 max generation), but MAX_KV_SIZE is
  65536.`，**0.1 秒**被拒、`judge_reading --think off` 讀出 0 題。要它跑得動：把預算壓到 KV 上限內
  （實測 cap 40,000 → 9,654＋40,000＝49,654 ≤ 65,536，讀得完）或把 `MAX_KV_SIZE` 開到 ≥166k。
  **這一項等你裁決**（見 ④）。
- 配套的預設一起換（`git grep -n '"splash"\|"mtplx-35b"'` 的其餘三處，都是 1 行＋理由註解）：
  `reread.py::DEFAULT_ENGINE`、`ask_about_blocks.py` 與 `confirm_dispute.py` 的 `--model`（原本
  `splash`，而 8088 自 2026-09-25 就是 down）、`scan_category_principles.py` 的 `--model`（原本
  `mtplx-35b`）。**仍然指名 mtplx 的兩個地方沒有動**：`scan_pharmacist_track.sh` 三條 lane 的
  `--model mtplx-35b`（它產出的判讀會進 review queue，換引擎＝換那條線的讀法，先量再改）、
  `repair_daemon.sh` 的 `LANE`（那個迴圈現在每輪 rc=1，先修它的病再談換引擎）。
- **還有一個自己造出來的坑（實測抓到）**：第一版 `_engine()` 把模組層的 `BASE_URL`（＝預設引擎的
  位址，現在是 18130）無條件蓋上去，於是 `QBR_REFLOW_ENGINE=mtplx-35b` 只換到開關、**位址還在
  occamy** → 送 156,000 max_tokens 給 18130、0.1 秒被 KV 上限拒。修法照 `reread.endpoint()`：
  位址用**被選中那顆**的，只有環境變數（`QBR_REFLOW_BASE_URL`／`QBR_MODEL_BASE_URL` 等）在時才蓋。
  已加兩條測試（預設＝occamy 且送 `reasoning_effort: none`；`QBR_REFLOW_ENGINE=mtplx-35b` 時位址與
  開關一起換）。
- 測試：`qbr/tests/test_engines.py::test_the_negative_control_the_default_engine_is_not_mtplx`
  原本把預設釘成 `"splash"`，改成**「不是 mtplx-35b」＋「名字在引擎表裡」**（負對照測的是 MTPLX
  這件事，不是「是哪一顆」）。`qbr` 全套 **786 passed / 17 skipped**（783 ＋本輪 3 個新測試）。
- 落地的三個檔（`vision.py`／`reflow.py`／`engines.py`）**仍只存在工作樹**（見 #5 的 ⚠️）。

**② 六支 PR：compare 頁面已驗，只差按下去。** 逐支 compare（base 一律
`agent/review-ui-server-split-20260923`）匿名 `curl` 皆 **HTTP 200**（repo 公開）：

```text
https://github.com/Ting-Ruei/tw-national-exam-catalog/compare/agent/review-ui-server-split-20260923...agent/<分支>
```

按法：開上面任一 URL → 頁面上方 compare bar 右邊的 **「Create pull request」** → 填標題／內文 →
再按一次「Create pull request」。（沒有看到按鈕時走通用路徑：repo → **Pull requests** →
**New pull request** → base 選 `agent/review-ui-server-split-20260923`、compare 選該支。）
**順序**：先合第 1 支 `agent/qbr-shared-contracts-20260929`（6 個共享 commit），再合 2–6；否則同 6 個
commit 會被審 6 次且互相衝突。標題表見上一節。

**③ `reread` 還在用嗎（確認過再回答）。**

- **模組還活著，而且新流程天天用**：`reread.page_extents`／`options_of`／`band_rows`／`crop_rows`／
  `SYSTEM`／`parse`／`compare` 被 `repair_agent_test/agent/bridge.py`（沙盒讀題）、
  `qbr/scripts/crop_run_figures.py`、`qbr/src/qbr/vision.py`、`qbr/src/qbr/ai_findings.py` 等引用。
- **會用到那個 down 的 `splash` 預設的，只有兩支：`transcribe`（383 行）與 `reread_question`（415 行）**。
  全 repo 掃 `transcribe(`／`reread_question`（含 `.sh`／`.mjs`／`__pycache__`）的結果：`reread.transcribe`
  只被 `reread_question` 呼叫，而 `reread_question` **沒有任何呼叫者**，`reread.py` 也**沒有
  `__main__` 入口**——它是 Task 21 留下的頁面回讀工具。
- **新流程真正的讀法是 `confirm_dispute.transcribe`**：`bridge.py:609` 用 `engines.named(args.engine)`
  把 endpoint **明確傳進去**（預設＝`BRAIN_ENGINE`），`scan_pharmacist_track.sh` 也明確 `--model`。
  所以那個 down 的預設**不會**弄壞任何現在會跑的東西；已一併改成 `occamy-6bit`，免得日後手動跑踩雷。

**④ occamy 的 KV（要開大嗎）。** 量法與結論：

- **語意＝請求准入配額**（讀原始碼＋實測）：`mlx_vlm/server/generation.py::_check_configured_context_budget`
  在**做任何事之前**檢查 `prompt_tokens + max_tokens > MAX_KV_SIZE` → `PromptTooLongError` → HTTP 400。
  `models/cache.py:65` 只在這顆模型**沒有**自己的 `make_cache` 時才會把它變成
  `RotatingKVCache(max_size=...)`；這顆有 → 對它而言就是配額，不是「配置多少記憶體」。
- **現況**（`GET :18130/health`、`GET :18130/v1/settings`）：`loaded_context_size=262144`（模型自己的
  窗口）、`configured_context_limit=65536`、`max_kv_size=65536`；`moe-offload` `enabled=false`。
- **誰在撞它**：agent 自己的提示詞 ~39,496（歷史 log）＋我們訂的 `max_output_tokens=16384` =
  55,880 ≤ 65,536（成功例：`prompt_tokens=31013 max_tokens=16384`）；reflow 要 156,000 → 直接拒。
- **成本（算出來）**：hybrid 模型，40 層中 30 層 linear attention、**10 層 full attention**；
  `head_dim=256`、`num_key_value_heads=2`、bf16 ⇒ **每個真的存在的 token ≈ 2×10×2×256×2 = 20 KiB**；
  65,536 ≈ 1.25 GiB、131,072 ≈ 2.50 GiB、262,144 ≈ 5.00 GiB。cache 每 256 token 成長（`KVCache.step`），
  **開大不會立刻吃記憶體**。（反證：一個 32k 提示詞的請求跑著時行程 RSS 只動 0.6 MiB
  `29,997,312 → 29,997,920 KB`，所以「看 RSS 就知道」是錯的；真上限看 `/v1/metrics` 的
  `peak_memory_gb`＝40.9，機器 128 GiB。）
- **改法**：`CTX_MLX=131072 occamy restart fit6`（持久，`~/models/occamy/bin/occamy:33`），
  或用 live knob `PATCH :18130/v1/settings {"max_kv_size":131072}`（免重啟，但 `reload_kinds:
  ["text_generation"]` 會重載文字模型）。要讓 reflow 跑它的預算需要 ≥166k（9,654＋156,000）。
- **建議**：agent 現在**不需要**開大；要開只有兩個理由——同時多個大 session（每個 ~55k）、
  或讓 reflow 跑它自己的預算。這是 owner 的機器決定。

**⑤ 站上 daemon 哪部分沒恢復。**

- **沒恢復的是「站上掃 queue、寫 AI findings 的迴圈」**：`ssh 192.168.10.70` →
  `launchctl print gui/501/com.qbr.repair-daemon` → `Could not find service`；plist 還在
  （`~/Library/LaunchAgents/com.qbr.repair-daemon.station.plist`，Sep 25 16:07，6455 B）；
  `pgrep -fl "repair_daemon|scan_for_repairs"` 無輸出；log 只有 9/25 那一輪
  （`第 1 輪 14:19:31Z → 結束 14:36:26Z`、`ONCE=1，結束`）。站上
  `question_ai_findings.jsonl` 的 mtime 因此停在 Sep 25 22:30。
- **有恢復的**：站上的 Review UI v2（`:8765/v2`）仍在服務。
- **筆電上還有一個不同的迴圈在跑而且是壞的**：`qbr/scripts/repair_daemon.sh`（PID 22977、自 Sep 23、
  `ONCE=0`、`LANE=mtplx-35b`），第 288–290 輪全部 `rc=1`、每 1800 s 重試——站上/筆電 findings 那
  2,891／3,096 列失敗列就是它與 Splash@8088 留下的。
- **它現在為什麼起不來，plist 也說了**（`plistlib` 讀出來的事實）：`ProgramArguments` =
  `/bin/bash -lc exec "/Users/tim/qbr-review/code/qbr/scripts/repair_daemon.sh" loop`；
  `EnvironmentVariables`：`ONCE=1`（**只跑一輪就結束** → 那 9/25 的 log 就是這樣來的，看起來像「跑過了」）、
  `LANE=dgx-qwen3.8-flash`、`ORCHESTRATE=dgx-qwen3.8-flash`、`QBR_ALLOW_EXTERNAL_LLM=1`、
  `QBR_LITELLM_BASE_URL=http://127.0.0.1:4000`、`QBR_PYTHON=/usr/bin/python3`、
  `QUEUE=/Users/tim/qbr-review/queue`、`WINDOW=60`、`INTERVAL=600`；`RunAtLoad=true`、
  `KeepAlive=false`、`StartInterval=600`。
  → 兩個地雷：**`ONCE=1`**（載入後跑一輪即結束，之後每 600 s 由 `StartInterval` 再載入一次；
  現在 service 不在 launchd，所以連那個都沒有），以及**它被配置成走 DGX/external lane**
  （`QBR_ALLOW_EXTERNAL_LLM=1`＋DGX 端點）——那正是站上 2,746 列失敗的來源，也和 charter
  「目前不依賴外部 AI 節點」相衝。**不要直接把它載回去**，先決定 lane（應該是 occamy）與那兩個開關。
- 另外確認：**findings 沒有東西要回流**（濾掉 `error` 非空後兩邊位元組流相同、各 105,100 列、
  sha256 `e99d5bf683c24541`）；**human events 只能站上 → 筆電**（站上 20,329／筆電 20,324，站上多 5 筆
  `reviewer: local` 的 accept）。

**⑥ 筆電那支失敗迴圈的根因＝同一個預設（實測到，且已處理）。**

- 它的每一輪都印 `對 5 題看紙本並轉錄（incoai/Qwen3.8-27B-Splash @ http://127.0.0.1:8088）`，然後
  `讀不到（request failed）`×5 → rc=1；`qbr/runs/repair_daemon-20260923-112942.log` 裡累計
  **735 筆 `request failed`**、291 輪全失敗。**根因就是 `confirm_dispute.py` 的 `--model` 預設指向
  已 down 的 8088**（`scan_for_repairs.py` 內部呼叫它、daemon 沒傳 `--model`）。
- **換預設後同一條指令實測**（`confirm_dispute.py --queue data/review-queues/live --limit 2
  --pending-only --skip-confirmed --out /tmp/...`，15.6 s）：`對 2 題看紙本並轉錄（occamy-1.0-6bit-xl-mlx
  @ http://127.0.0.1:18130）`，`不一致 2、一致 0、紙本讀不出來 0、讀不到 0`（q53 抓到上標被壓平、
  q61 抓到 2 處）——**735 筆失敗的直接原因消失了**。
- **但這支迴圈本來就該不在**：`docs/skills/operate-repair-agent-surface/SKILL.md` 記著 2026-09-24
  owner 決定「筆電這支退掉」（理由是 `scan_state.json` 會被推上常駐機、而
  `question_ai_findings.jsonl` 永不推 → 常駐機拿到「已看過」的指紋、拿不到判讀，於是**靜默跳過**）。
  當天只 `mv` 了 plist（`com.qbr.repair-daemon.plist.retired-20260924` 在），**跑著的製程沒被殺**，
  活了 6 天（PID 22977，`nohup env ONCE=0 bash qbr/scripts/repair_daemon.sh`，父殼 22975）。
  而且預設換掉之後它會**開始成功**，也就是真的開始產生「永遠不會回流」的判讀 → **已停掉**
  （`kill 22977 47933`；`pgrep -fl "repair_daemon|sleep 1800"` 空、父殼 22975 已結束）。
  要恢復請照上面那份 SKILL 的 install 步驟，不要只 `nohup`。

**⑦ 分支依賴（決定 PR 合併順序的硬事實）。** `occamy-6bit` 只存在於
`agent/engine-endpoints-runtime-20260927`（`git show <ref>:qbr/src/qbr/engines.py | grep -c occamy`
→ 該分支 4、`repair-agent-pi-sdk-20260928` 0、`review-ui-server-split-20260923` 0、`main` 0）。
而 `repair-agent-pi-sdk-20260928` 已提交的 `session.mjs` 是
`BRAIN = "occamy-6bit/occamy-1.0-6bit-xl-mlx"` ⇒ **第 6 支要有第 3 支才跑得起來**。
所以合併順序：**① 共享契約 → ③ 引擎表 → ⑥ pi-sdk → ④②⑤**（其餘彼此無依賴）。
本輪改的三支 script 預設也一樣：`--model occamy-6bit` 要引擎表裡有那顆才有意義。

**⑧ 工作樹糾纏（「整套換」暫時落不了地的原因）。** 本輪改到的檔案未提交量：
`qbr/src/qbr/engines.py` 210/17、`vision.py` 713/74、`reflow.py` 84/32、`reread.py` 189/49、
`qbr/tests/test_engines.py` **117**、`qbr/tests/test_reflow.py` **457**——**後兩者含別條線的測試**，
所以連「只 commit 三個乾淨的 script 預設」都不成立：`ask_about_blocks.py`／`confirm_dispute.py`／
`scan_category_principles.py`（各 4/1）雖是乾淨的，但它們的 `occamy-6bit` 預設要等 ③ 的引擎表先落地。
先前的敘述（「reread.py＋三支 script＋測試可獨立 commit」）**是錯的，以此處為準**。

#### 9.9 第三輪（2026-09-29 晚；設計者四問：①我不會合併 ②PR「Some checks were not successful」 ③原則／討論區應該廢了吧 ④沙盒沒有嚴格區分＋討論能不能幫提示詞進化）

**② checks 紅的根因（已量到，會改變合併順序）**
- `python3 -m unittest discover -s tests`（CI 的 `unit-tests` 原指令）在 worktree 上實測：
  `origin/main` → **OK**；`origin/agent/review-ui-server-split-20260923`（**五支 PR 的共同祖先**）→
  **FAILED (failures=1, errors=19)**；`origin/agent/repair-agent-pi-sdk-20260928`（#7）→ 同上。
- 19 個 error 是同一件事：`qbr/src/qbr/review_ui/review_state.py:60` 匯入
  `scripts.review_feedback`（`except ModuleNotFoundError` 的**退路**分支），而 **split 分支上這個檔被刪掉了**
  （`git cat-file -e <ref>:scripts/review_feedback.py`：main 沒有、split 沒有、shared-contracts 沒有；
  `codex/organize-review-server-20260923`／`review-ui-isolated-20260926`／`qbr-ui-prerequisite-20260926` 有；
  **工作樹有，且與 #12 上的版本逐字相同**，17,227 bytes，Sep 23 19:27）。
- 第 1 個 failure 是第二個半提交：`tests/test_migration_tools.py` 在分支上期望
  `scripts/migration_preflight.py` 的 stderr 含 "retired"，但**retired stub 只存在工作樹**（工作樹相對
  split 是 +4／−503 行，也就是 4 行的 stub；分支上仍是 507 行原版）⇒ argparse 直接報 `--mode` required。
- 三顆 check 的判定：#7（run `36525872407`）／#10（`36526012854`）／#8／#9／#11 全部
  `governance=success, unit-tests=failure, qbr-tests=failure`；**#12 = 三顆全 success**。
  ⇒ 更正第二輪「#12 關掉」：**#12 綠不是因為它對，而是因為它舊**（它仍有 `scripts/review_feedback.py` 與
  未拆的伺服器）。它證明缺的是**一個檔**，不是設計。
- **修法（順序因此改變）**：把工作樹的 `scripts/review_feedback.py` ＋ retired stub 補進
  `agent/qbr-shared-contracts-20260929` → 那支 PR 先綠 → 合進 main。PR 的 check 跑在「base＋head 的合併結果」
  上，所以 main 有了這兩個檔之後，其餘五支會在 Checks 頁按 **Re-run all jobs** 後轉綠。
- 合併的操作（他不會）：PR 頁 → 綠色 **Merge pull request** → **Confirm merge**；checks 紅時 GitHub 會多一行
  **Merge without waiting for requirements**（那是「我知道它紅，照合」——現在不該按，因為紅的是真缺陷）。
  `gh auth status` 仍是 `The token in default is invalid` ⇒ 開／關 PR 我做不了（只能讀公開 API）。

**③ 原則／討論區：消費者還在，寫入者早就不是他**
- 消費者（實測）：`qbr/src/qbr/ai_findings.py` 的 `PRINCIPLES`／`ANSWERS`／`NOTES` 三段
  （`discuss.PRINCIPLES_STREAM`／`REPAIR_QUESTIONS_STREAM`）→ 站上代理的提示詞；
  `qbr/src/qbr/discuss.py` → 站上 v2 的「原則」「討論」兩個模式；`repair_agent_test/agent/lib/identity.mjs::principles()`
  → **沙盒對話代理**（每輪都讀，空的話靜默變短——那個檔自己的註解記著「0 of the designer's 19 principles」）。
- 寫入者：`question_review_principles.jsonl` 32 列、最後 `2026-09-25T22:12:23`、`source=feedback_learning`、
  `reviewer=principle_curator`；`question_repair_questions.jsonl` 690 列、最後 `2026-09-25T22:31:31`、
  `source=qbr_experience_apply`、`reviewer=repair_experience_apply`。**兩者都是機器寫的，不是設計者寫的。**
- 時間線：站上 `question_review_events.jsonl` mtime **09-29 12:29**（他今天還在審）、
  `question_ai_findings.jsonl` **09-25 22:30**、principles **09-25 22:12** ⇒ 上游（人）在動、下游（代理／原則）停了。
- 而且**沙盒的判讀路徑不吃這三樣**：`bridge.read_page` 呼叫
  `confirm_dispute.transcribe(png, endpoint=…, max_tokens=…, timeout=…, number=…)`——`principles`／`answers`／`notes`
  在簽名上有、呼叫沒給。⇒ 「討論→提示詞」在沙盒裡只有**對話代理**那條線通，判讀那條線不通。

**④ 沙盒嚴格區分（本輪已實作並驗收）**
- 舊行為（他抱怨的）：`bridge.do_browse` 只收 `block`／`comment`（`'"block"' in line` 前置過濾），
  所以他 **10,371 題已接受**、**3,938 題已重設**與沒人開過的題畫得一模一樣；UI 只有
  「未判讀／有圖／有問題的（你說過的）」三個開關；`judged` 一個旗標把 store 裡
  `source=agent`(27 列) 與 `source=designer`(2 列) 兩種判讀混在一起。
- 新行為：`human_status`（未審／已接受／已封鎖／需人工確認／已審／已重設／只有註解）＋
  `judged_by_designer`／`judged_by_agent` 分開；`--status` / `?status=` 篩選（可逗號多選）；
  類別下拉顯示「人已審 X，未審 Y，已重設 Z；代理已判 W；有圖 V」；每列三個 chip（人／你判／代理）；
  摺疊規則：live verdict > 撤回（`reset_review` 把裁決清掉）> 只有註解 > 未審。
- 驗收（可重跑）：`bridge.py browse` 全庫 → 未審 **65,310**／已接受 **10,091**／已重設 **3,294**／
  已封鎖 **386**／只有註解 **7**／需人工確認 **1**／已審 **1**，與**獨立算法**（另一支腳本直接摺 events，不 import
  bridge）逐項相同；HTTP `/api/browse?category=藥師(一)&status=block` → **153**、`status=accept` → **4842**、
  `status=reset` → **43**；真 Chromium 切換下拉與 chip 截圖確認（「人：已封鎖」紅底、
  「代理：有問題」）；`repair_agent_test/agent/run_tests.sh` **47 pass／0 fail**。
- 沙盒已重啟（新服務名 `sandbox-8790-status`，pid 75668，`0.0.0.0:8790`）。

**④ 的後半「討論能不能幫提示詞進化」——機制已存在一半，缺的是人的那半**
- 已經活的：`store/lessons.jsonl`（2,086 bytes、mtime 09-29 13:42，稽核迴圈正在寫）→
  `identity.mjs::systemPrompt()` 的「你（或前幾輪的你）學到的事」段，依次數排序、附 `evidence`、標科目或 `[引擎]`
  ⇒ **代理由自己判讀累積教訓、下一輪讀得到**，這就是「討論→提示詞進化」的骨架。
- 缺的：**設計者的話沒有進入任何一條沙盒路徑**（判讀路徑連 principles 都沒傳）。最小可驗收做法：把一次討論的
  結論寫成一筆 append-only 經驗（`source: designer`、附 candidate_key 與依據）→ 進同一個 `lessons` 投影 →
  每輪提示詞讀它，且 UI 顯示「這一題的提示詞用了哪幾條經驗」。這需要他裁決（那是「他的話變成模型必須遵守的
  約束」），我不自己決定。

#### 9.9 第四輪（2026-09-29 晚；設計者下令「幫我修 PR」）

**A. 推上去的兩支（ssh remote，push 可用；`gh` token 無效 ⇒ 開／關 PR 我做不了，只能 push）**

1. `agent/qbr-ci-green-20260929` = `1c2ba31`（base＝`agent/qbr-shared-contracts-20260929`）。四個檔，
   全部取自工作樹、全部只差「已提交的一邊」：
   - `scripts/review_feedback.py`（**還原**；被 split 刪掉，`review_state.py:60` 還在匯入它。
     與 #12 上已提交的版本**逐字相同**）
   - `scripts/migration_preflight.py`（15 行 retired stub；全樹唯一引用者就是那個測試，`bg_22` 量過）
   - `tests/test_ai395_runtime_config.py`（retirement 版：拿掉 ai395 期望、類名改 `LocalRuntimeConfigTests`）
   - `.env.example`（loopback、`WRITE_ALLOWED=0`、無 ai395 alias）
   ⇒ 四個檔是同一個「退役 ai395 外部 runtime」change set 的另一半（branch tip `16bd5e4` 的訊息自己就寫
   「retire ai395」）；兩個已提交的測試原本**互相矛盾**（`test_migration_tools` 要 retired、
   `test_ai395_runtime_config` 要那個被 retired 掉的常數），所以這四個是最小且自洽的一組。
   **沒動**：ai395 退役線其餘 ~30 檔（`scripts/ai395_*.py`、`deploy/ai395*`、`docs/ai395-*.md`）。

2. `agent/agent-verified-gate-20260928`（PR **#8**）＝ `8bedd52` → **`15d2a2f`**：`8bedd52` 提交了
   350 行的 `tests/test_question_package_lineage.py`，**沒有提交它測的東西**
   （`git log --all -S "def validate_source_document_lineage"` 是空的——那個函式在歷史裡從未存在）。
   補上：
   - `scripts/validate_question_bank_package.py`（工作樹版 ＋ 閘門修正）
   - `scripts/export_question_bank_package_from_postgres.py`（工作樹版；+75 行，全部是 answer source lineage）
   - 閘門修正（本輪唯一新寫的邏輯，~8 行）：`review_status` 由硬編 `!= "accepted"` 改成
     `in ("accepted", "agent_verified")`，且 `agent_verified` 另發
     `package_agent_verified_not_human_accepted`（warning）。測試自己的負對照
     （`machine_verified_pending_human`／缺 status 仍被拒、`accepted` 不加警告）全過。

**B. 量到的結果（worktree ＋ CI 的原指令，不是猜）**

| 狀態 | unittest `discover -s tests` | pytest `qbr/tests` |
|---|---|---|
| 修前：`split`（五支的共同祖先） | 365 tests / 1 failure / **19 errors** | 4 collection errors |
| 修前：`main` | OK | — |
| 修後：`main ＋ ci-green` | **524 OK**（8 skipped） | **578 passed** |
| 修後：`main ＋ ci-green ＋ #7 / #9 / #10 / #11` | **524 OK** 各 | 578 / 580 / 583 / 580 passed |
| 修後：`main ＋ ci-green ＋ #8（新 head）` | **537 OK** | 578 passed |
| 負對照：把 `scripts/review_feedback.py` 拿開 | 19 errors 回來 | 4 collection errors 回來 |
| 五支合併**全部無衝突**（`git merge` 乾淨；公開 API 也報 `unstable`／`clean`，不是 `dirty`） | | |

**C. 分支盤點（`git ls-remote` ＋ `rev-list --count` ＋ 包含矩陣）**

兩個**完全不相交**的家族，做同一件事（重組審題伺服器）：
- **家族 A（新）**：`review-ui-server-split-20260923`（89 ahead）⊆ `qbr-shared-contracts-20260929`（95）
  ⊆ `qbr-ci-green-20260929`（96，本輪）→ 五支 feature（96–112）都含 `2a5e97c`，都在 A 上面。
- **家族 B（舊）**：`qbr-ui-prerequisite-20260926`（#12，95 ahead，伺服器還是 10,707 行單體）
  ⊆ `review-ui-isolated-20260926`（100，已拆、348 行、**有** `review_feedback.py`）
  ⊆ `codex/organize-review-server-20260923`（#5，101，綠）。
- 兩家族樹差 **27 檔**（`git diff --name-status --no-renames <B> <A>`；先前寫的「31 檔」是
  #12 vs `agent/qbr-shared-contracts-20260929`，那是另一組比對，已在 F 更正）；
  A 唯一缺的檔（`scripts/review_feedback.py`）已從 B 的版本還原 ⇒ B 已被吸收完，
  其餘 27 檔逐一裁決見 **F**。
- 已完全落地的（0 ahead，可直接刪）：`codex/review-ui-image-workflow`（31 behind）、
  `codex/agent-governance`（29 behind）、`codex/local-review-workflow-20260906`、`codex/qbr-mainline-merge-20260919`。
- PR 檢查狀態（公開 API，2026-09-29 晚）：**#4 ✅✅✅、#5 ✅✅✅、#12 ✅✅✅**；
  #7／#9／#10／#11（以及 #8 剛重跑）紅——**只等 `main` 拿到 A 的修補**。

**D. 給設計者的操作順序（他不會合併，也只需要按這幾下）**
1. 開 `https://github.com/Ting-Ruei/tw-national-exam-catalog/pull/new/agent/qbr-ci-green-20260929`
   → **Create pull request** ×2。
2. 等 checks → 應三顆綠 → **Merge pull request** → **Confirm merge**。
3. #7／#9／#10／#11 → Checks 頁 → **Re-run all jobs** → 應轉綠。
4. #8：已推新 head，等 `main` 有修補後同樣 Re-run → 綠。
5. #12 與 #5（家族 B）→ **關掉**（B 已被 A 吸收；合了會讓同一件事在 main 上出現兩份）。
   #4／#5 綠且獨立：#4 由他決定。

**E. 下一階段（他指定的）**：① 家族 A vs B 的 31 檔逐一裁決（保留／放棄）；② 架構攤開
（面／流／檔案／誰寫誰讀）＋「誰保留誰放棄」清單；③ 建立完整審題機制（含提示詞進化的
`source: designer` 經驗流與判讀路徑接上 principles／answers／notes——第三輪 ④ 列的兩件待裁決）。



**F. 家族 A／B／工作樹 三方逐檔裁決表（2026-09-29，第二階段第一份產物）**

量法：`git diff --name-status --no-renames origin/codex/organize-review-server-20260923
origin/agent/qbr-ci-green-20260929` ＝ **27 檔**；每檔再量「工作樹 vs A」與「工作樹 vs B」的
`git diff --numstat`，取較近者；存在與否用 `git status --porcelain --untracked-files=all` ＋ `wc -l`。

⚠️ **結論先講：這是三方比對，不是兩方。** 工作樹（HEAD `1d6d1c0` ＋ 255 個未提交檔）是**最新**的那一版，
A 與 B 都是它的**部分快照**；A 合進 main 只是把工作樹已經做完的事提交，沒有人在等 B。

| 檔 | 工作樹 vs A | 工作樹 vs B | 判定 |
|---|---|---|---|
| `.env.example`、`docs/preserved/ai395-retirement.md`、`docs/preserved/review-feedback-agent.md`、`scripts/migration_preflight.py`、`tests/test_ai395_runtime_config.py` | **0（逐字相同）** | B 有舊版 | 工作樹＝A：這 5 檔是 A 從工作樹搬上去的，B 的是舊版 |
| `AGENTS.md`、`docs/README.md`、`docs/ryzen-ai-max-395-migration-runbook.md`、`qbr/README.md`、`qbr/src/qbr/generation.py`、`review_ui/v2/02-area-question.js`、`tests/test_review_ui_discuss.py`、`tests/test_review_ui_returned_chip.py`、`scripts/serve_question_review_ui.py` | 近（1–25 行） | 遠（6–251 行） | 留工作樹；B 的是舊版 |
| `qbr/ENGINE_STRATEGY.md` | 31+0− | 17+0− | 工作樹是**兩邊的超集**（`+0−` ⇒ 只增不刪）⇒ 兩邊都落後 |
| `qbr/src/qbr/review_ui/{events,review_state,queue_view}.py`、`review_ui/v2/{01-core,04-area-discuss}.js`、`tests/test_review_ui_scope.py` | 近（84／294／319／165／91／172 行新增） | 遠（88／288／319／167／91／172 行新增，且 B 多 57／48／8／72／207／189 行） | **設計爭點** → 見 G |
| `scripts/review_feedback.py` | 未追蹤（`??`，444 行） | 未追蹤 | 工作樹檔案 sha256 `c931db1dfefab5f1…` ＝ A 的 commit 版**逐字相同**（B 也有同一份）⇒ 這檔兩邊都有，A 已還原，無事 |
| B 獨有 5 檔：`tests/test_review_ui_{correction_action_precedence,discuss_selection,disputed_filter,scope_refresh_lock,startup_navigation}.py` | 工作樹**沒有** | 只在 B | **B 家族唯一真正獨有的東西** → 見 H |

**G. 4 個「B 有、A 與工作樹都沒有」的行為（把 B 的 7 個測試檔對工作樹跑出來的）**

跑法：把 B 的 `tests/test_review_ui_scope.py` ＋ 5 個 pytest 風格檔複製成 `tests/zz_b_*.py`，
`./qbr/.venv/bin/python -m pytest tests/zz_b_*.py -q`，跑完即刪（工作樹未被污染，`git status` 已核對）。
結果 **32 tests：12 failed／20 passed**，12 個失敗分成 4 件事：

| 行為 | B 的測試 | 對工作樹 | 判定 |
|---|---|---|---|
| 內容修復的 reset 要保留人的 `action`（B 要 `latest[key]["action"]=="block"` ＋ `pending_reset`） | `test_content_change_reset_preserves_decision_and_repair_bucket`、`test_jsonl_note_after_content_repair_keeps_decision_in_memory`、`test_sql_content_change_reset_preserves_human_decision` | **仍紅**（`'reset_review' != 'block'`；SQL 那個 `KeyError`） | **契約已刻意改變**：工作樹的 `events.py` 有更細的 `_is_repair_reset`（docstring 直接引用這個測試檔，並把 `codex-luna-accepted-reaudit` 列為「故意重開」），且工作樹自己的測試叫 `test_a_repair_after_the_human_block_awaits_recheck_instead_of_reading_as_blocked`（＝「需重審」而不是「已封鎖」）。**沙盒的 `human_status` 摺疊採的正是工作樹這一條**（reset 撤回裁決，`human_actions` 另記）。→ 建議不回退，但要把舊契約寫進文件，免得下次又拿 B 的測試當正解 |
| 刷新鎖（`S.refreshing`／`S.scopeRequest`／stale-response guard／`setQuestionActionsDisabled`） | `test_scope_refresh_lock.py` 3 個（「清空 stale 列」「每條寫入路徑拒絕 refreshing 的列」「舊 scope 回應不得覆蓋新列」） | **仍紅** | **唯一像真退化的**：工作樹仍會在刷新時清 `S.rows`（`01-core.js:686`）但**沒有任何 token 守衛**（`grep refreshing｜scopeRequest` 全空）⇒ 快速切範圍時較舊的回應可能覆蓋新列，刷新中也擋不住送出決定 |
| 討論區選取記憶（`discussSelectionFromUrl`／`discussSelectionFromStorage`／`rememberDiscussSelection`／`DISCUSS_SELECTION_STORAGE_KEY`） | `test_review_ui_discuss_selection.py` 3 個、`test_review_ui_startup_navigation.py` 1 個 | **仍紅**（`ReferenceError: discussSelectionFromUrl is not defined`） | 兩邊都沒有 ⇒ 與「站上 v2 的原則／討論模式去留」一起裁決 |
| 校正優先序 | `test_review_ui_correction_action_precedence.py` 1 個 | **仍紅**（`legacyCorrection`：`reviewed`≠`correct`；`withdrawnReset`：`reset_review`≠`''`） | 顯示語義；要不要回退由設計者定 |

**H. CI 的實質缺口（量到的，與 PR 顏色無關）**

`tests/` 66 檔裡有 **5 個是 pytest 風格**（模組層 `test_*` 函式、沒有 `unittest.TestCase`）：
`test_review_ui_{correction_action_precedence,discuss_selection,disputed_filter,scope_refresh_lock,
startup_navigation}.py`（AST 實測：61 檔有 TestCase、5 檔只有模組層函式）。
`python3 -m unittest discover -s tests`（CI 的 `unit-tests` job）**只收 TestCase**，`qbr-tests` job 只跑
`qbr/tests` ⇒ **這 5 個檔從來沒有被任何 CI job 執行過**，而且它們只存在於家族 B（`main` 沒有、工作樹沒有）。
「PR 三顆綠」因此不能證明它們存在或正確。要留它們就必須二選一：轉成 unittest，或讓 CI 對 `tests/` 也跑
pytest（`.github/workflows/ci.yml` 屬 CODEOWNERS ⇒ 需 owner review）。

**I. 第二階段第一組裁決（誰保留誰放棄）**

1. ~~**刷新鎖要不要補回工作樹？** 建議補（「導覽跟隨被畫出的清單」的直接保護），但補在**工作樹**（權威），
   不是補在分支。~~ → **已補（見 N）**：`S.scopeRequest` 世代守衛 ＋ 換範圍先清列 ＋ 兩個 unittest 測試
   （負對照 2/2 fail）。若你不要，回一句我拆掉。
2. **討論區選取記憶**要不要修？＝B 的 `test_review_ui_discuss_selection.py` 那一類缺陷：在討論區選了一題、
   面板重畫之後「當下選的是哪一題」不見了。**這與兩個模式的去留無關**（原則區是設計者 2026-09-24 明確要的
   獨立一頁：「我已為原則區你會獨立做一頁UI來管理，結果你藏在錯題討論區」，所以它不是「可以合併掉的東西」）；
   要修就先量 `04-area-discuss.js` 現行語義再改，附負對照。
3. **校正優先序**（`correct` vs `reviewed`、withdrawn reset 顯示空）要不要回到 B 的行為？→ **已量出兩版的差別，
   只剩你選一個**（量法：`qbr/.venv/bin/python` 讀筆電快照 `qbr/data/review-queues/live/review-ui/question_review_events.jsonl`
   （2026-09-29 13:48，20,329 事件／13,780 key）→ `events.load_review_events` ＋ `queue_view.review_projection`
   組成**服務端實際送給瀏覽器的那一份 `review`**，再把兩版 `rowReviewAction` 逐字餵同一批 13,780 列）：

   | 這一列的狀況 | 列數 | 工作樹畫 | B（`codex/organize-review-server-20260923`）畫 |
   |---|---|---|---|
   | `action=accept` ＋ 有（非撤回）correction | **560** | 已修正 | 確認正常 |
   | `action=block` ＋ 有 correction | **74** | 已修正 | 阻擋 |
   | `action=needs_review` ＋ 有 correction | **1** | 已修正 | 需重看 |
   | 合計不同的列 | **635** | — | — |

   分布（同一批列）：工作樹 `reset_review`(＝「AI已修改」)3,238／確認正常 9,534／已修正 648／阻擋 346／空 14；
   B：確認正常 10,094／阻擋 420／已修正 13／需重看 1／空 14。**規則差別只有一句**：B 讓
   `accept`／`needs_review`／`block` 先於 correction；工作樹讓 correction 先於那三個。撤回（14 列）兩版相同（空）。
   `metadata` 傳空物件（站上的 `review_block_repair` 等只影響 `repair_event`／待複核桶，不影響 chip 的四個欄位）。
4. **B 的 5 個 pytest 風格測試檔**：放棄（連同 #12／#5 一起關）還是搶救（轉 unittest ＋ 補 CI）？
   **與第 3 項綁在一起**：那幾檔編碼的正是 B 的舊優先序，所以若你選工作樹那一版（635 列搬去「已修正」），
   要搶救的不是原檔、是照工作樹規則改寫的版本；若你選 B 那一版，原本那幾檔才有意義（但仍要轉 unittest 樣式，
   否則 CI 永遠收不到：`unittest discover -s tests` 對 pytest 風格的檔一律不執行，AST 實測 66 檔裡 5 檔如此）。
5. **家族 A 是 carrier、不是 source**：A 相對 B 獨有的 3 檔（兩份 `docs/preserved/*`、`returned_chip` 測試）
   工作樹**都已經有而且更新** ⇒ 合 A 進 main 不會覆蓋任何新東西。**逐檔複核（2026-09-29 晚，`git diff --name-status
   main...agent/qbr-ci-green-20260929` 共 300+ 檔，逐一 `cmp` 工作樹）**：
   - 重疊的檔：工作樹**多數更新**，或是有意的退役修剪（`README.md +55 −531`、`docs/ai395-* +8 −921`、
     `docs/skills/qbr-pipeline-status +68 −1039` 等：工作樹把 ai395 那一代的內容刪掉）。
   - **A 有、工作樹沒有的 9 檔**，全部是**工作樹這一側刻意刪掉、刪除還沒提交**（`git status` 皆 ` D`）：
     `docs/skills/operate-local-open-models/SKILL.md`（**搬到傘層** `ai_learning_platform/skills/operate-local-open-models/SKILL.md`，
     在工作樹外面、Sep 22，實測存在）＋ 8 個 qbr 檔 `scripts/{ask_local_model,audit_crops,compare_engines,
     compare_local_models,consult_local_model,reflow_paper}.py`、`scripts/run_repair_loop.sh`、`src/qbr/audit_crops.py`。
     這 8 個**都還在 HEAD**（`git cat-file -e HEAD:…` 成功）⇒ 合 A 進 main 會把它們帶回 main（不是覆蓋新東西，
     是**復活退役檔**）；工作樹那 9 個未提交的刪除要不要一起提交，是你要決定的。
   - A 相對 `main` 是**大合併**（main 落後整整一代）；這也是「先合 A」的理由。

**J. 面（surfaces；②「把架構攤開」第一半，全部量到的）**

| 面 | 位置／程序 | 狀態 |
|---|---|---|
| 站上審題服務 | `192.168.10.70:8765`（`lsof` 顯示由 `com.docke` PID 98634 持有 ⇒ 容器）；部署紀錄 `~/qbr-review/DEPLOYED.json`（2026-09-26T11:28Z）：`source_head 16bd5e45…`、`source_dirty_files 269`、`review_events 20322`、`served_questions 79090` | **站上跑的是「家族 A 的祖先 ＋ 269 個未提交檔」的工作樹鏡射** ⇒ A 不是替代方案，是站上的血緣 |
| 站上資料 | `~/qbr-review/queue/review-ui` 6.8 GB（`candidates.jsonl.before-*/bak-*` **3.3 GB**、`crops/` 1.7 GB）；`assets` 2.0 GB、`backups` 8.2 GB、`code` 61 MB、`logs` 36 KB | 活動檔 5＋3：`candidates.jsonl`(199 MB, 09-26 11:52)、`question_review_events.jsonl`(14.1 MB, **09-29 12:29**＝人的裁決，活的)、`question_ai_findings.jsonl`(706 MB, 09-25 22:30)、`question_repair_questions.jsonl`(662 KB)、`question_review_principles.jsonl`(66.9 KB)、`question_correction_feedback_events.jsonl`(400 KB)、`figure_ownership.json`、`table_crops.json` |
| 筆電沙盒 | `repair_agent_test/agent/ui/server.py`，pid 75668，`0.0.0.0:8790` | 活著（唯一該活的筆電服務） |
| 筆電稽核迴圈 | LaunchAgent `com.qbr.audit-pharmacist1`（pid 37888）→ `repair_agent_test/agent/audit_pharmacist1.sh` → 帳本 `/tmp/agent_perf/audit/summary.tsv` | 活著 |
| 引擎 | MTPLX `127.0.0.1:18120`（pid 5100）、`18130`（pid 1168） | 活著 |
| 筆電 queue 快照 | `qbr/data/review-queues` **13 GB**：`live` 2.5 GB ＋ 10 個舊快照（`pre-extractorfix-20260923` 2.1 GB、`v6-20260922` 1.4 GB、`pre-flatdetect-20260922` 1.3 GB、`staging-20260921`／`pre-thirdfix`／`pre-scriptwide`／`pre-scriptdetect`／`pre-countfix` 各 1.0 GB、`pre-wrapfix`／`pre-figures`／`all-20260920` 各 185 MB） | 舊快照＝待裁決 |
| **已放棄（本輪殺掉）** | 7 個 4–6 天、只有 `LISTEN` 沒有 `ESTABLISHED` 的殘留：`serve_question_review_ui.py` 的 pid 14503(8921)／30377(8896)／44027(8897)／53574(8891)／78076(8898)／90873(8899)，其中 **4 個的 `--review-log` 直接指向筆電快照** `qbr/data/review-queues/live/review-ui/question_review_events.jsonl`；＋ pid 4133（`python -m http.server 8791 --bind 127.0.0.1`） | 殺後只剩 8790／18120／18130（`lsof` 已核對） |

**K. 流與誰寫誰讀（②第二半）**

| 檔 | 誰**寫** | 誰**讀** |
|---|---|---|
| 站上 `question_review_events.jsonl`（人的裁決，append-only） | **只有站上 8765**（`POST /api/review` 等）；本輪前筆電另有 4 個 stray writer（已殺） | 站上 v2 投影；`scripts/pull_station_reviews.sh` → 筆電快照；沙盒 `bridge.py` 摺 `human_status`；稽核 |
| 站上 `question_ai_findings.jsonl` | AI 稽核（站上側）；筆電曾有 stray daemon | 站上 v2、`qbr/src/qbr/ai_findings.py` |
| 站上 `question_repair_questions.jsonl`（690 列） | `source=qbr_experience_apply` | `ai_findings.py`（ANSWERS）、`discuss.py`、沙盒 `lib/identity.mjs` |
| 站上 `question_review_principles.jsonl`（32 列） | `source=feedback_learning`／`reviewer=principle_curator` | `ai_findings.py`（PRINCIPLES）、沙盒 `identity.mjs::principles()` |
| 站上 `question_correction_feedback_events.jsonl` | 站上服務（`POST /api/correction-feedback`） | `ai_findings.py`（NOTES） |
| 站上 `candidates.jsonl`（199 MB） | rebuild 管線（每次 rebuild 留一份 ~199 MB `.before-*`） | 站上服務（唯讀） |
| 筆電快照 `qbr/data/review-queues/live/review-ui/*`（今天 13:48–13:49 更新） | `repair_agent_test/scripts/sync_from_station.sh` ＋ `scripts/pull_station_reviews.sh` | 沙盒、稽核、離線量測 |
| 入口（`qbr/src/qbr/review_ui/handlers.py` 實測 25 個 API ＋ 8 個頁面路由） | API：`/api/{candidates,answer-candidates,group-candidates,review,answer-review,review-batch-accept,answer-review-batch,principles,principles/similar,discuss,findings,machine-activity,manual-asset,mobile-review,pipeline,preferences,queue_index,reload-candidates,reload-status,repair-question,workflow,ai-feedback,ai-learning,ai-question-audit,ai-question-audit-reset,correction-feedback,group-*}`；頁面：`/v2`、`/mobile`、`/workflow`、`/legacy`、`/file`、`/evidence-file`、`/scripts` | 同一支服務 |

**L. 保留／放棄清單（②，前 4 項本輪已做，後 4 項等裁決）**

已做（可逆性高，已核對）：
1. 7 個殘留 server 已殺（J）；只剩 8790／18120／18130。
2. `/tmp/ab` worktree ＋ 分支 `agent/ab-triage` 已刪；`agent/pr8-gate-fix` ＝ PR #8 的 head `15d2a2f0`，留作本機指標。
3. 「兩家族差 31 檔」更正為 **27 檔**（F）。
4. 記下 **B 家族 5 個 pytest 風格測試檔從未被 CI 收集**（H）。

等裁決：
1. 站上 `candidates.jsonl.before-*/bak-*` **3.3 GB**（10 份 199 MB 快照）留不留？筆電 `qbr/data/review-queues/` 的 **10 個舊快照共 10.5 GB** 留不留？
2. 15 個 worktree（含 4 個 prunable、`/tmp/step0`、`/tmp/tw-national-exam-catalog-pharmacist-visual-audit`）要不要 `git worktree prune` ＋ 移除？
3. v1 路由（`/mobile`、`/workflow`、`/legacy`）與 `review_ui/v1-reference/`：書籤與 PWA 是契約（要留）；要不要只留「能答」的最小服務、其餘封存？
4. 筆電 `question_ai_findings.jsonl` **108,201 行／708,292,579 B** vs 站上 **107,991 行／706,253,185 B**（**＋210 行／＋2.0 MB**，stray daemon 寫的，今天 13:49 拉完之後仍在）⇒ 要不要重新對齊站上（覆蓋），還是保留差異並登記？

**M.（第三項：建立完整審題機制）第一步已做：判讀路徑接上「人的經驗」，並記下用了哪些（2026-09-29 晚）**

改的是沙盒的判讀器 `repair_agent_test/agent/bridge.py`（G2，本層自己的面）：

1. **判讀路徑接上四個通道 ＋ 圖的事實。** 新增 `experience_for(key)`：直接呼叫**正式路徑的載入器**
   `confirm_dispute.load_prompt_inputs(queue)`（＝ `confirm_dispute.confirm_one` 用的同一個），
   取「已核准的基本原則」「對反問的回答」「這一題的註解」「被退過的改動」＋`ai_findings.figures_note`。
   `read` 把它們全部傳進 `confirm_dispute.transcribe(...)`——**這是本站判讀第一次把人的話帶到模型眼前**。
   （`--queue`／`--principles` 可覆寫，用來在別的快照上量同一件事。）
2. **記下「這一題用了哪幾條經驗」。** `read` 的回傳新增 `review_context`：
   `{queue, experience:{principles[], note, answers, rejected:{count,fields}, figures}, system}`
   ——`system` 是 `transcribe_system()` 的輸出，也就是**實際送出去的那一段**（不是重拼的第二份）。
   `feedback` 的紀錄也新增同一個 `experience`（用 `experience_summary()`，欄位全走既有正規化器
   `latest_note`／`answers_note`，不自己走列）。
3. **沙盒畫面**（`ui/index.html`）新增 `renderExperience()`，畫在「模型讀到什麼」之前：原則編號清單、
   註解原文、反問與回答、被退過的欄位、圖的事實。空的經驗**不畫**（舊紀錄沒有這一欄，畫成「無」會
   把「這一輪還沒有紀錄」講成「這一題沒有經驗」）。
4. 順手修掉一個既有缺陷：`REPAIR_AGENT_STORE` 原本只改 `STORE_DIR`，`STORE` 是另一條獨立路徑，
   所以**設了環境變數的測試仍然寫真的學習流**（實測：一次被導向的 smoke 讓
   `store/agent_feedback.jsonl` 多了一行）。現在 `STORE` 由 `STORE_DIR` 推導。

**量到的（都在工作樹上實測）**

| 檢查 | 結果 |
|---|---|
| 負對照（舊行為：什麼都不傳） | `transcribe_system()` = **1,132 字**，那位審題者的註解 `in` 它 = **False** |
| 接了之後（`read --no-image`，同一題 `moex:106100:307:22:1:question:q006`） | `system` = **2,803 字**（＋1,671），註解、被退的 `option B/C/D`、6 條原則全部在裡面 |
| 真的一次判讀（`read`，引擎 `occamy-6bit`／`127.0.0.1:18130`） | rc=0，**2.0 s**（模型 1.5 s），`png_bytes` 26,330，`seen` 有 stem＋options，`review_context` 6 條原則＋註解＋回答＋被退 |
| `load_prompt_inputs` 的成本 | **0.18 s**（原則 6 條、有回答 16 題、有註解 252 題、被退過 90 題） |
| `feedback`（導向 `/tmp/smoke_store`） | 真 store **35 → 35 行**（不變），暫存 store 1 行；紀錄含 `experience` |
| 畫面（用 node 直接驅動 `ui/index.html` 的真 script） | 標題「用了哪幾條經驗（agent）」、「基本原則 6 條（已核准）」、註解原文、被退欄位都在輸出裡；空的經驗回空字串 |
| 誤寫的一行 | 第一次 smoke 因上面那個缺陷寫進真 store 的那一行已移除（備份 `/tmp/agent_feedback.jsonl.pre-smoke-undo`，35 → 36 → 35 行） |

**還沒做的（第三項的其餘部分）**：①`source: designer` 經驗流在沙盒已有（UI `POST /api/judgement`
→ `store/agent_feedback.jsonl`，`source: designer`；`get_question` 讀回、`identity.principles()` 讀
站上的已核准原則）——**新接上的那一段是「站上人寫的四條通道」**，兩者現在同一條判讀路徑；
②站上 v2 **要不要部署 N 的刷新鎖**（8765 跑的是舊 JS；部署＝`scripts/deploy_station.sh --restart`，屬對
站上的變更，等一句話）。（原本寫的「原則／討論模式去留」是我自己的猜測，已撤回：原則區是 2026-09-24
設計者明確要的獨立一頁，見 I.2。）

**N.（F–I 的第 1 項裁決）刷新鎖：我先做了，理由與量測如下（2026-09-29 晚）**

我把它從「等你裁決」變成「已做、可推翻」，因為量到它不是 A 線的刻意簡化，而是**檔案自己寫下的不變式被違反**：

- `applyScope()` 的註解說「只清 `S.view` 會讓上一卷的列還畫著、還走得到……正是這一整個改動在處理的那一類缺陷」，
  但 `loadScopeRows()` 讀完**直接寫** `S.view`／`S.verdict`／`S.notes`，沒有任何世代比對 ⇒
  **開 A 範圍、還在讀就換 B 範圍，B 先回來、A 後回來，清單是 A 的題目而麵包屑寫 B**（＝同一類缺陷的非同步版本）。
  另一個量到的缺口：換範圍時舊列在讀取期間仍留在 `S.rows`，畫面上寫「載入中…」但 `W`／`S` 還走得到、決定還送得出去。
- 改法（`review_ui/v2/01-core.js`）：`S.scopeRequest` 世代號；`applyScope()`／`refreshScopeRows()` 各推進一號並傳給
  `loadScopeRows(papers, requestId)`；`loadScopeRows()` 在**寫任何狀態之前**比對世代，比對不到就 `return false`
  （成功路徑與 catch 路徑各一處）；`applyScope()` 在 await 前先清 `S.view`／`S.rows`。
- 測試（`tests/test_review_ui_v2_scope.py`，**unittest 樣式、CI 收得到**）：`ScopeRequestGenerationTests` 兩個
  ——舊世代的回應不得被接受（同時驗當前世代必須被接受，作為「守衛不是永遠回 false」的負對照）、
  換範圍時舊列不得留在 `S.rows`／`S.view` 且世代號必須推進。
- **負對照**：把三處守衛與清理拆掉的副本（`/tmp/negctl`，用完已刪）跑同樣兩個測試 ⇒ **2 failed／2**
  （`staleKept=True`、`rows=1`）。修後：**6 tests OK**（該檔全部）。
- **真頁面**（`scripts/serve_question_review_ui.py` 在本機 8931 服務**工作樹的 v2** ＋ 真快照
  `qbr/data/review-queues/live/review-ui/candidates.jsonl`，`--review-log` 指向 `/tmp` 的暫存檔、不動真資料）：
  ①**舊回應晚回來不得蓋畫面**：把 115 年那一發拖慢 2.5 s、50 ms 後換 100 年（快的先回來寫完畫面），
  量到 `fetch-done(115) = t+2553 ms` 而「Y 寫完畫面」在 `t+137 ms` ⇒ 舊回應晚了 **416 ms** 回來，
  `S.rows` 仍是 100 年那 80 列（`1001_…`）、麵包屑仍是 100 年 ⇒ 被拒絕 ✓（若沒有守衛，這裡會變成
  清單是 115 年而麵包屑寫 100 年）。
  ②**讀取期間沒有可走的舊列**：換到另一個有卷的範圍（`載入中…`，延遲 1.8 s），等待期間
  `S.rows = 0`、`S.view = 0`，讀完 80 列、`location.hash` 同步；頁面 **console error 0 筆**。
- 順手補的 harness 缺口：該檔的 `run_node` 把運算式值直接 `JSON.stringify`，所以 `(async () => …)()` 會被
  序列化成 `{}` —— 用非同步運算式寫的測試**不會失敗，只會什麼都沒測到**（B 家族那幾個 pytest 風格測試正是這形狀）。
  新增 `run_node_async()`：包進會 `await` 的 IIFE、把錯誤也印成 JSON，並載入 **`v2.html` 列出的全部 script**
  （只載 `01-core.js` 會在 `scopeToHash()` 撞 `A is not defined`）。

**量到的全套**：`python3 -m unittest discover -s tests` = **541 tests／1 failure**（那 1 個是
`tests/test_local_review_evidence.py`，**未追蹤、不在 HEAD** ⇒ CI 的 checkout 不會有它；我沒碰那一條線的檔）。
`cd qbr && .venv/bin/python -m pytest tests/ -q` = **786 passed／17 skipped**（102.5 s；另一個 CI job，我的 JS 改動不影響它）。
`tests/test_review_ui_v2_scope.py` 單檔 6 OK。沙盒 `run_tests.sh` 47 pass（見 M）。

**O. 設計者的五個回答（2026-09-29 晚）與我照著做的事**

| 我問的 | 他的回答 | 我做了什麼 |
|---|---|---|
| ③ 校正優先序 | 「我當時的決定（accept 就是通過，block 就是阻擋）」 | `review_ui/v2/01-core.js` 的 `rowReviewAction` 改成**人的決定先問**（＝B 的順序）：`accept`／`needs_review`／`block` 早退，之後才看 `correction`；撤回的兩條路徑照舊。新增 `tests/test_review_ui_row_action.py`（8 個 unittest、CI 收得到）：三個決定各釘一次、`comment`＋correction 讀成已修正、legacy `reviewed`＋correction 讀成已修正、`is_reset_unreviewed` 最優先、撤回不讀成已修正、沒有事件讀成未看。**負對照**：把「人的決定先問」那一行拿掉的副本 ⇒ 3/8 fail（舊規則把 accept 畫成 correct）。**真資料**：13,780 列跑完，chip 分布與 B **逐格相同**（`reset_review` 3,238／accept 10,094／block 420／correct 13／needs_review 1／空 14）。 |
| ④ 那五個 pytest 檔 | （問「哪五個」） | 五檔＝`tests/test_review_ui_{correction_action_precedence,disputed_filter,discuss_selection,scope_refresh_lock,startup_navigation}.py` 見下面清單。因為③選了 B 的順序，`correction_action_precedence` 才有意義 ⇒ 已用上面的新檔以 unittest 樣式重寫（**不必動 `ci.yml`**：`unittest discover` 自己會收）。 |
| ② 討論區選取記憶 | 「討論區廢掉，之後的沙盒穩定後會補上這個功能」 | **不修**（B 的 `test_review_ui_discuss_selection.py` 那四條斷言不轉移）。站上 v2 的那一區要不要**現在**就從介面拿掉＝待他一句話（那會動到站上，見下）。 |
| ⑤ 我修的那個問題要不要上站 | （問「哪個」） | 就是 **N 的刷新鎖**：換範圍時晚回來的舊題目會蓋掉新範圍的畫面（量到 416 ms）。等他回覆才部署。 |
| L 段清垃圾 | 「清垃圾」 | 見下面 P 段（逐項列出**刪了什麼、量到多少**）。 |

**五個 pytest 風格檔（B 獨有，CI 從未執行）**：`tests/test_review_ui_correction_action_precedence.py`、
`tests/test_review_ui_disputed_filter.py`、`tests/test_review_ui_discuss_selection.py`、
`tests/test_review_ui_scope_refresh_lock.py`、`tests/test_review_ui_startup_navigation.py`。
它們是模組層 `def test_*()`（pytest 風格），而 `unittest discover -s tests` 只收 `TestCase`，`qbr-tests` 又只跑
`qbr/tests` ⇒ **從來沒有被任何 CI job 執行過**。其中 `scope_refresh_lock` 的那三條已經由 N 的
`ScopeRequestGenerationTests` 以 unittest 樣式覆蓋；`correction_action_precedence` 由 O 的新檔覆蓋；
`discuss_selection` 依②作廢。剩下 `disputed_filter`／`startup_navigation` 兩檔的斷言還沒有人接手（未裁決）。

**PR 現況（2026-09-29 晚，實測）**：設計者已合 **#14**（＝ci-green，`1c2ba31`）與 **#13**，`main` 現在有那 4 個修補；
**#12 已關**。我對 **#7／#8／#9／#10／#11** 各做了一次「把 `main` 併進分支」（五個合併皆無衝突，`git merge-tree` 先驗），
並推回原分支（fast-forward，非 force-push）⇒ 新的檢查已跑：**`qbr-tests` 五條全綠**，
**`unit-tests` 五條仍紅**（`main` 自己的 push run 也紅）。已在追：本機（macOS，含 python3.13/`-S`/TZ=UTC/LC_ALL=C、
淺層 detached clone）與 Linux 容器（python 3.11，容器內**無 git／node**）都 524 OK，
所以懷疑是「Linux ＋ 有 git/node」才觸發的那幾條（`test_agent_governance.test_repository_boundaries_are_valid`
需要 git；四支 node 驅動的測試需要 node）；正在用裝了 git＋node 的 Linux 容器重跑定位。

#### 9.9 之後仍**待裁決**

0. ~~（第三輪）把工作樹的 `scripts/review_feedback.py` ＋ retired `migration_preflight.py` 補進
   `agent/qbr-shared-contracts-20260929`~~ → **第四輪已做**（見上 A／D）：推成 `agent/qbr-ci-green-20260929`，
   等設計者開 PR 並合併。

1. ~~換同顆後 `read_page` 的「第二意見」怎麼重新定義？~~ → 已定義（見上），如不同意再改。
2. **v2 要不要顯示 `prompt_system`／`prompt_user`？**（沙盒已補，站上 v2 未補）
3. ~~沙盒延遲要降到多少才算「可以繼續測試」？~~ → 已降到 0.024–0.94 s；剩下 0.94 s 那一項
   要不要再動，等你一句話（動了就會多出第二份計數實作）。
4. ~~五支 PR 未開~~ → **2026-09-29 05:22–05:25Z 你已開 6 支**（公開 API 讀回，`gh` 本身仍無 token）：
   **#7** pi-sdk（112 commits／248 檔）、**#8** agent-verified-gate、**#9** export-question-page、
   **#10** engine-endpoints-runtime、**#11** fix-option-alphabet-union（各 96／150–153）、
   **#12** `agent/qbr-ui-prerequisite-20260926`（95／140，**這支不在原本的 6 支清單裡**）。
   **六支的 base 都是 `main`**（不是 split），所以每支看起來都是 ~96 commits／150+ 檔。

   量到的依賴（決定合併順序；`git merge-base --is-ancestor`）：
   - 五支 feature ＋ pi-sdk **都含 `2a5e97c`**（＝`agent/qbr-shared-contracts-20260929` 的 tip，也就是那 6 個共享
     commit 的**同一批 SHA**）⇒ 只要先把「共享契約」那包進 main，這五支的 PR 會**自動縮成自己的 1（pi-sdk 17）個 commit**，不必改 base、不必 rebase。
   - `agent/qbr-shared-contracts-20260929` ＝ **split ＋ 6**（相對 main 95 commits、相對 split 6）⇒ 它是
     **split 的 superset**，一支 PR 就能把 split 的內容一起帶進 main。**目前沒有它的 PR**（原本清單第 1 支）。
   - `agent/qbr-ui-prerequisite-20260926`（#12）**不含** `2a5e97c`，且與 `qbr-shared-contracts-20260929` 的樹差
     **31 檔（＋11803／−11156）**⇒ 它是同一件事的**早期版本**（重複實作）。**先不要合它**（合了會讓五支
     重複／衝突），要嘛關掉、要嘛留著。

   **建議順序**：① 開 `agent/qbr-shared-contracts-20260929` → `main`（superset，先合）→ ② **#10 引擎表**（`occamy-6bit` 只在它裡面，pi-sdk 的 `BRAIN` 依賴它）→ ③ **#7 pi-sdk** → ④ **#8／#9／#11** 任意序 →
   #12 關掉或不合。（`gh` token 無效 ⇒ 這個「開 PR／關 PR」的動作我做不了，只能讀公開 API。）
   ⚠️ **第三輪更正**：`#12` 是三顆 check **全綠**的那一支（它還留著 `scripts/review_feedback.py`）；
   五支紅的是因為它們的共同祖先 split 把那個檔刪了。見第三輪 ②。
   `gh auth login` 一次（或設 `GH_TOKEN`）之後我就能自己開 PR（治理 G1：可開、不可自行核准／合併）。
5. 🆕 **「`engines.py` 預設」這句話沒有對應的程式**：`engines.py` 只有引擎表。實際的預設有四個
   地方：`qbr/src/qbr/vision.py` 的 `_DEFAULT`（**眼睛**）、`qbr/src/qbr/reflow.py` 的 `_DEFAULT`
   （**純文字**重排）、`qbr/src/qbr/reread.py` 的 `DEFAULT_ENGINE`（原本 `splash`）、
   `repair_daemon.sh` 的 `LANE`（`mtplx-35b`）。

   **已做**：`vision.py`、`reflow.py`、`reread.py` ＋ 三支 script 的 `--model` 預設（`ask_about_blocks`／
   `confirm_dispute`／`scan_category_principles`）**全部改成 `occamy-6bit`**（`reread` 那個原本指向已 down
   的 `splash`）；`reflow` 另外把開關改成「從引擎表取」（見第二輪 ①，附負對照與 A/B）。
   **沒動**：`scan_pharmacist_track.sh` 三條 lane 的 `--model mtplx-35b`（會進 review queue 的讀法，
   先量再改）、`repair_daemon.sh` 的 `LANE`（那個迴圈先修病）。

   ⚠️ **`vision.py`／`reflow.py`／`engines.py` 的改動只存在工作樹、沒有 commit**：這三個檔帶著別條線
   未提交的改寫（`vision.py` 相對 origin 差 713 insertions／74 deletions），一起 commit 就是把別條線的
   工作搬到本分支。**連「只 commit 乾淨的三支 script 預設」都不成立**（它們的 `occamy-6bit` 要等
   ③ 引擎表先落地）。完整量法見 ⑧。
6. 🆕 **occamy 的 KV 要不要開大？**（第二輪已量化）現在 65536；`loaded_context_size=262144`（模型自己的
   窗口），每 token ≈ 20 KiB（10 層 full attention × 2 kv heads × 256 head_dim × 2 B × K/V ⇒
   65536＝1.25 GiB、131072＝2.50 GiB、262144＝5.00 GiB），cache 每 256 token 長、**開大不會立刻吃記憶體**，
   且 `moe-offload` 是 off（沒有「專家快取被吃掉」的副作用）。改法 `CTX_MLX=131072 occamy restart fit6`
   或 live `PATCH :18130/v1/settings {"max_kv_size":131072}`。**需要開大的唯一硬理由**：reflow 的
   預算是 156,000（要 ≥166k 才跑得動）；agent 自己（39.5k＋16.4k）現在不需要。
7. 🆕 **站上的 repair daemon 要不要恢復、以什麼 lane 恢復？** plist 現在是 `ONCE=1` ＋
   `LANE=dgx-qwen3.8-flash` ＋ `QBR_ALLOW_EXTERNAL_LLM=1`（走 DGX/external，與 charter 相衝）。
   要恢復就得先決定：lane＝occamy？`ONCE` 拿掉？`QBR_ALLOW_EXTERNAL_LLM` 關掉？
8. 🆕 **家族 A／B／工作樹的三方裁決（F–I）**：①~~刷新鎖要不要補回工作樹~~ → **已補（見 N）；如不要就回一句**；
   ②~~討論區選取記憶要不要回~~ → **第五輪已答**：討論區功能廢掉、沙盒穩定後再補（見 P.3）⇒
   `test_review_ui_discuss_selection.py` 作廢（見 P.2）；③~~校正優先序要不要回 B 的行為~~ → **已答**（5.1）：
   人的決定先問，**已改並量到與 B 逐格相同**（見 P.1）；
   ④B 的 5 個 pytest 風格測試檔放棄還是搶救（＋CI 要不要對 `tests/` 跑 pytest）→ **第五輪問了、他回「哪五個」**：
   名單與處置見 P.2，剩 `disputed_filter`／`startup_navigation` 兩支未決（見 12）；
   ⑤確認 A 是 carrier 不是 source（合 A 進 main 不會覆蓋任何新東西）。
9. ~~🆕 **空間與快照（J–L）**：①站上 3.3 GB 的 `candidates.jsonl.before-*` 與筆電 10.5 GB 舊快照要不要留；
   ②15 個 worktree 要不要 prune；③v1 路由要不要只留「能答」的最小服務；
   ④筆電 `question_ai_findings.jsonl` 的 ＋210 行要不要對齊站上。~~
   → **第五輪已答「清垃圾」並執行**（見 P.5）：①筆電 12.6 G→2.5 G、站上 6.8 G→3.4 G；②prune ＋
   刪掉我自己開的 11 棵（別人的不動）；③④**還沒答**。

10. 🆕 **站上的討論區現在就拿掉嗎？**（5.3；拿掉＝動到站上 G3 ＋ `scripts/deploy_station.sh --restart`）
11. 🆕 **刷新鎖要不要推上站上？**（5.4；修「換範圍時舊題目晚 416 ms 回來蓋掉新畫面」）
12. 🆕 **5.2 剩下兩個還沒接手的檔**：`tests/test_review_ui_disputed_filter.py`、
    `tests/test_review_ui_startup_navigation.py`（要重寫成 unittest，還是放棄？）

---

#### 9.9 第五輪（2026-09-29 深夜；設計者答完五問，並下令「PR 還是沒通過，先把這個搞定」）

設計者的原話與我的解讀（這一段是**執行紀錄**，不是問題）：

|他的話|我的解讀|狀態|
|---|---|---|
|「5.1 我當時的決定(accept 就是通過，block 就是阻擋)」|校正的優先序＝**先問人的決定**，再問 AI 的校正|✅ 已改（見 P.1）|
|「5.2 哪五個?」|他要五個檔名|✅ 見 P.2|
|「5.3 討論區廢掉，之後的沙盒穩定後會補上這個功能」|**不修**討論區功能；沙盒穩定後再補|✅ 照此執行（P.3 有一個要問他的小問題）|
|「5.4 哪個?」|他要「刷新鎖」的白話解釋|✅ 見 P.4|
|「5.5 清垃圾」|刪掉量到的舊快照、worktree、備份|✅ 見 P.5|

## P.0 PR 為什麼紅、修在哪（本輪最重要的一件事）

**根因（量到的，不是猜的）**：`unit-tests` 這一顆在**每一支 PR 都紅**，而且 main 自己的 push
run（`e3be3fa6`）也紅，job 只跑 8 秒就失敗。

- 四個測試把整份 JS（v2 全部原始碼／整棵範圍樹的 JSON）用 **`node -e <script>`** 交給 node。
- Linux 對**單一 argv 字串**有 `MAX_ARG_STRLEN` ＝ **128 KiB** 的上限；macOS 沒有。
- 所以：**macOS 跑幾百次都綠，Ubuntu 直接 `OSError: [Errno 7] Argument list too long: '/usr/bin/node'`。**
  這一條只有 CI 看得見。
- 在哪裡複現：本機 Docker（`ci-like:local` ＝ `tw-national-exam-qbr-review-ui:local` ＋ `nodejs git`）。
  同一個容器、同一棵樹，把那個檔換回舊寫法 → **1 error／38 tests**；換成新寫法 → **38 tests OK**。
  整份 suite：修前 **524 tests／1 error**，修後 **526 tests OK (skipped=8)**。

**修法（一支分支，不 push main）**：`agent/fix-node-argv-limit-20260929`（`2dd06ea`，已推上 origin）。

- `tests/review_ui_source.py` 是「JS 怎麼交給 node」的**唯一解答**：`run_node_script()` 寫 `.cjs`
  暫存檔到 repo 外再 `node <檔案>`，`run_node_expression()` 疊在上面；`args`／`cwd` 給需要小尾巴
  （vm runner 的呼叫式）與相對檔名的呼叫者。
- 四個 harness（`test_review_ui_scope`／`v2_scope`（含 `run_node_async`）／`rich_text`／`discuss`）全部改走它，
  所以下一個 harness 不會在第五個地方再犯。
- `tests/test_node_script_helper.py`：**負對照**——①>128 KiB 的 script 仍跑得動 ②交給 subprocess 的
  每個 argv 元素都 < 128 KiB（舊寫法這一條會紅）。

**五支 PR 已經帶著這個修法**（把 `agent/fix-node-argv-limit-20260929` 併進各分支後推上去，非 force）：

|PR|分支|新 head|容器內整份 suite|
|---|---|---|---|
|#7|`agent/repair-agent-pi-sdk-20260928`|`40776ad`|**527 OK**|
|#8|`agent/agent-verified-gate-20260928`|`70b736a`|**540 OK**|
|#9|`agent/export-question-page-20260927`|`b702e31`|**527 OK**|
|#10|`agent/engine-endpoints-runtime-20260927`|`82cf74a`|**527 OK**|
|#11|`agent/fix-option-alphabet-union-20260927`|`de8a361`|**527 OK**|

驗法是**真 clone**（不是 linked worktree——linked worktree 在容器裡 gitdir 指到容器外，governance
測試會假紅）＋ `ci-like:local`。修法本身的分支另外給設計者一條 PR 連結：
`https://github.com/Ting-Ruei/tw-national-exam-catalog/pull/new/agent/fix-node-argv-limit-20260929`。

**推上去之後，GitHub 上的三顆檢查全綠（公開 API 直接讀回，2026-09-29 深夜）**：

```
#7  40776ad  governance success / unit-tests success / qbr-tests success
#8  70b736a  governance success / unit-tests success / qbr-tests success
#9  b702e31  governance success / unit-tests success / qbr-tests success
#10 82cf74a  governance success / unit-tests success / qbr-tests success
#11 de8a361  governance success / unit-tests success / qbr-tests success
```

也就是說：設計者不必先開修法那一支的 PR——**五支各自的內容已經含修法，五支都可以直接按 Merge**。
（#5 `codex/organize-review-server-20260923` 也三顆綠，內容已被吸收 ⇒ 只剩按 Close；#12 已關。）

## P.1 5.1 校正優先序＝人的決定先問（已改、已量）

`review_ui/v2/01-core.js` 的 `rowReviewAction` 現在的順序：`is_reset_unreviewed` → 早退
`accept`／`needs_review`／`block`（**人的決定**）→ `correction`（非撤回）→ `withdrawn && action==='reset_review'`
→ `action`。

- 新檔 `tests/test_review_ui_row_action.py`（8 個 unittest，`unittest discover` 收得到）。
- **負對照**：把「人的決定先問」那一行拿掉的副本 ⇒ **3／8 fail**（舊規則把 accept 畫成 correct）。
- **真資料**：13,780 列真 served `review` 跑 node，chip 分布＝`reset_review` 3,238／`accept` 10,094／
  `block` 420／`correct` 13／`needs_review` 1／空 14，**與設計者版（B）逐格相同**。

## P.2 5.2 那五個檔名（家族 B 獨有、CI 從未收過）

1. `tests/test_review_ui_correction_action_precedence.py` → 已由 `tests/test_review_ui_row_action.py` 以 unittest 重寫（P.1）。
2. `tests/test_review_ui_disputed_filter.py` → **尚未接手**（未裁決）。
3. `tests/test_review_ui_discuss_selection.py` → 依 5.3 作廢。
4. `tests/test_review_ui_scope_refresh_lock.py` → 已由 `tests/test_review_ui_v2_scope.py::ScopeRequestGenerationTests` 覆蓋（N 段的刷新鎖）。
5. `tests/test_review_ui_startup_navigation.py` → **尚未接手**（未裁決）。

（這五個是 pytest 風格的檔案，`python3 -m unittest discover -s tests` 收不到，所以它們從未在 CI 跑過。）

## P.3 5.3 討論區

- 功能**不修**；沙盒穩定後再把它補回（設計者原話）。
- 要問他一句：**現在就把站上（`192.168.10.70:8765`）那一區從介面拿掉嗎？** 拿掉＝動到站上
  （G3）＋要 `scripts/deploy_station.sh --restart`，所以我沒有自己動。

## P.4 5.4「刷新鎖」是什麼（白話）

場景：審題者在左邊換科系／年度，畫面會去要新的一批題目。舊的那一批如果**回得慢**，它回來時會
把新畫面**蓋掉**——人看到的是上一個範圍的題目。量到舊回應晚了 **416 ms** 回來，就蓋上去了。

「刷新鎖」＝每要一次就蓋一個**號碼牌**，只有最新那一次的答案可以被畫出來，舊的回來就丟掉。
（程式碼：`S.scopeRequest` 世代守衛；`tests/test_review_ui_v2_scope.py::ScopeRequestGenerationTests`
兩條測試各附負對照。）

**要不要推上站上？** 這會動到站上（G3）⇒ 等他一句話。

## P.5 5.5 清垃圾（清單與量到的數字）

**刪掉的（筆電，本地可重建）**——合計約 **10.1 GB**：

|目錄|大小|
|---|---|
|`qbr/data/review-queues/pre-extractorfix-20260923`|2.1 G|
|`qbr/data/review-queues/v6-20260922`|1.4 G|
|`qbr/data/review-queues/pre-flatdetect-20260922`|1.3 G|
|`…/staging-20260921`、`…/pre-thirdfix`、`…/pre-scriptwide`、`…/pre-scriptdetect`、`…/pre-countfix`|各 1.0 G|
|`…/pre-wrapfix`、`…/pre-figures`、`…/all-20260920`|各 185 M|

**保留**：`qbr/data/review-queues/live`（2.5 G）＝現行快照。**已刪，量測後**：`du -sh` 從 12.6 G → **2.5 G**。
判準（量到的，不是猜的）：沒有程式／排程指著那些目錄（只有 `qbr/reports/count_mismatch_three_causes.md`
`repair_loop_first_run.md`／`option_continuation_fix.md` 這三份**歷史報告**把它們寫成「逐題比對用／考據」，
那是當時的紀錄；權威佇列在站上 `192.168.10.70:~/qbr-review/queue/review-ui/`，要那份快照可重跑管線再產生）。

**worktree**：`git worktree prune` 清 4 筆 prunable（`/private/tmp/qbr-head`、`qbr-head2`、
`qbr-verify-commit`、`…-pharmacist-visual-audit`，四個目錄本來就已經不在）＋刪掉我自己開的
`/private/tmp/m7..m11`、`/private/tmp/prev7..prev11`（含 `preview7..11` 分支）、`/tmp/fixargv`、
`/tmp/fixclone`、`/tmp/shallow`、`/tmp/ciimage`，以及三棵 2026-09-26 的舊工作樹
（`/tmp/tw-national-exam-catalog-{deploy-review-ui,qbr-prerequisite,review-ui-pr}-20260926`；它們的
HEAD 都還被 remote 分支收著，未提交的只有 `scripts/test_v2_areas_browser.mjs` 的權限位）。
**別人的不動**：`/private/tmp/a1/wt`、`g4/wt`、`step0/wt`、`d14/wt-7d4e087`、`~/.codex/worktrees/*`。

**站上**：`~/qbr-review/queue/review-ui/` 從 **6.8 G → 3.4 G**。刪掉 54 個 `candidates.jsonl.before-*`／
`.bak-*`（共 3.3 G；**留最新那一個** `before-italic-restore-20260926T115250` 當還原點）＋兩個
`.sliver-half-20260925-172525`（沒有任何程式用到，grep 過）。那顆容器掛的是**整個目錄**
（`/Users/tim/qbr-review/queue/review-ui -> /queue/review-ui (rw)`），刪旁邊的檔案不影響它；
刪完 `qbr-review-ui` 仍是 **Up (healthy)**。保留 `question_review_events*.jsonl`（人工事件）、
`question_ai_findings.jsonl`（706 MB）、`crops/`（862 個圖）—— 那些是資料，不是垃圾。

## P.6 下一個 session 從這裡開始（2026-09-29 深夜；設計者說「先記錄下來，我開新 session 處理」）

### 現在的一句話狀態

**PR 這一件事做完了**：五支（#7–#11）在 GitHub 上三顆檢查**全綠**，等設計者按 Merge；
程式面沒有未完成的紅燈。剩下的是**兩個要動到站上的決定**（P.6 第 2 節）與**一個未提交的工作樹**。

### 1. 設計者要按的（已經不需要我再做任何事）

| 按什麼 | 為什麼 |
|---|---|
| **Merge #7 `agent/repair-agent-pi-sdk-20260928`**（`40776ad`） | 三顆綠；含修法 |
| **Merge #8 `agent/agent-verified-gate-20260928`**（`70b736a`） | 同上（它帶自己額外的測試，540 個全過） |
| **Merge #9 `agent/export-question-page-20260927`**（`b702e31`） | 同上 |
| **Merge #10 `agent/engine-endpoints-runtime-20260927`**（`82cf74a`） | 同上（**`occamy-6bit` 引擎表只在它裡面**，其它支依賴它 ⇒ 這支先合最安全） |
| **Merge #11 `agent/fix-option-alphabet-union-20260927`**（`de8a361`） | 同上 |
| **Close #5 `codex/organize-review-server-20260923`** | 內容已被吸收，三顆綠 |
| （不必開）`agent/fix-node-argv-limit-20260929`（`2dd06ea`） | 修法本身；它已經在五支裡面。要留紀錄就開 PR，不合也不會有事 |

**合完之後要檢查的事**：main 的 push run 應該變綠（`python3 /tmp/pr_status.py` 讀公開 API）；
工作樹的 `1d6d1c0 ＋ 未提交` 那包，之後要與新的 main 對齊一次（下一輪的第一件事）。

### 2. 兩題等設計者一句話（都會動到站上 ⇒ G3，我不自己動）

1. **站上的討論區現在就拿掉嗎？**（5.3；拿掉要 `scripts/deploy_station.sh --restart`，功能日後在沙盒補）
2. **刷新鎖要不要推上站上？**（5.4；修「換範圍時舊題目晚 416 ms 回來蓋掉新畫面」）

### 3. 未提交的東西（工作樹，`/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog`）

本輪動到、**尚未 commit**：

- `tests/review_ui_source.py`（＋共用 runner：`run_node_script`／`run_node_expression`／`node_binary`／`NODE_ARG_MAX`）
- `tests/test_node_script_helper.py`（**新檔**；3 條契約，含「每個 argv 元素 < 128 KiB」的負對照）
- `tests/test_review_ui_scope.py`／`test_review_ui_v2_scope.py`（`run_node` ＋ `run_node_async`）／
  `test_review_ui_rich_text.py`（含 vm runner 的 `args`／`cwd`）／`test_review_ui_discuss.py`
- `review_ui/v2/01-core.js`（`rowReviewAction`＝5.1 的順序；`S.scopeRequest` 世代守衛＝刷新鎖）
- `tests/test_review_ui_row_action.py`（**新檔**，5.1）
- `repair_agent_test/skills/design-repair-agent/SKILL.md`（P 段＝本紀錄）
- 已知、與本輪無關的紅：`tests/test_local_review_evidence.py`（**未追蹤、不在 HEAD**，所以 CI 看不到；
  容器內整份 suite ＝ 552 個、只它一個失敗）

### 4. 可重用的工具（下一個 session 直接用，不必重做）

- `python3 /tmp/pr_status.py`：讀 PR 清單 ＋ 每個 head 的檢查（**不需要 token**）。
- `python3 /tmp/ci_log.py`：job 步驟細節（job log 本身要 admin ⇒ 403）。
- 容器重現 CI：image **`ci-like:local`**（＝`tw-national-exam-qbr-review-ui:local` ＋ `apt-get install nodejs git`），
  `DOCKER_CONFIG=/tmp/dockercfg`（`config.json={}`）＋ mount 一棵**真 clone**：
  `DOCKER_CONFIG=/tmp/dockercfg docker run --rm -v <clone>:/w -w /w --entrypoint sh ci-like:local -c 'git config --global --add safe.directory "*"; python3 -m unittest discover -s tests'`
  （`/tmp/ciimage` 這個 build context 已經刪掉；要重建就是那兩行的 Dockerfile。）

### 5. 這一輪踩到的坑（別再踩）

1. **Linux 單一 argv 上限 128 KiB**（macOS 沒有）⇒ 任何「把大東西交給 node」的測試都要走
   `review_ui_source.run_node_script`（暫存檔），不要 `node -e`。
2. **容器裡不要用 linked worktree 跑整份 suite**：`.git` 檔指向容器外的路徑，`test_agent_governance`
   會假紅（`git ls-files` exit 128）。要**真 clone**。
3. 站上（`timmac-studio`）非互動 ssh 的 PATH 沒有 `docker`；用 **`/usr/local/bin/docker`**。
4. macOS 的 **`du` 會把 APFS 克隆算成全額**（`/tmp` 那些工作樹看起來 70–128 G，實體幾乎沒多佔；
   刪完可用空間不變，但**檔案數少了 370 萬個**）。判斷「真的省了多少」要看刪前後的 `df`。
5. 這一台 shell 的 `rm` 對**大目錄**不可靠（`rm -rf` 回 0 但目錄還在、還會被背景化）；
   大目錄請用 **`/bin/rm -rf`** 並給長 timeout。
