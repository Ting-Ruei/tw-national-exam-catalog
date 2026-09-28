# 考題修復 Agent 架構（重新設計，2026-09-27 業主提案）

**性質**：**設計文件**，不是實作授權，也不是 G3/G4 核准。
**來源**：業主 2026-09-27 的架構構想 ＋ Pi agent 讀 `qbr/` 程式碼後的查證。
**每一條「事實」都附出處（檔名:行號）；推論標「我的判斷」。**

---

## 0. 業主的三層構想（原話摘要）

| # | Agent | 做什麼 |
|---|---|---|
| 1 | **下載 agent** | 每天定期掃考選部網站，找新題目／答案／**修正答案**，下載後進管線 |
| 2 | **管線＋AI 審核 agent** | 切題成 JSONL → 地端 AI 逐題審（**要告訴它每題該看的重點**）→ 有錯且可處理就自己改 → 無法處理或改過的要人引導 |
| 3 | **ReviewUI 人審** | 找出容易出錯的模式 → 訓練 AI 或調管線 |

**業主的兩條工程紀律（很重要，要當設計約束）**

> 「**管線腳本不建議越寫越大或是 overfitting，寫太大就要變成拆分＋路由，不要 overfitting**，題目進來能夠解決 90-95% 的問題，剩下的可以讓 LLM 來協助處理」

**調適階段（開發迴路）**

> 「開發階段：我跟指導者模型應該有一個 UI 可以對話……**你（Pi）要全程讓 35B MoE 模型去實作，監控它輸入與輸出的結果或思考過程，然後調整提示詞或要求輸出符合特定格式**，然後將它改的東西呈現給我看，我跟你互動完，你再去跟他做調適」

---

## 1. 模型分工（業主 2026-09-27 本輪更正後）

> **⚠️ 本節已更正**。2026-09-27 第二輪業主指示：**4bit 版排除（無 vision）**；**Mac Studio 只當 Agent 中樞，不當算力中樞**。

| 角色 | 模型 | 端點 | 用途 |
|---|---|---|---|
| **調適期指導者** | 雲端 `deepseek-v4.1-flash`（本 session）／未來地端 `GLM-5.3-flash EXL3` | 見下 | 智能高、不一定快 |
| **未來驅動／判錯主力** | 本地 35B MoE（會演進） | — | 快速判斷、知道人類要看的重點 |
| **現在可測候選 1** | `ornith-1.5-mtplx-35b` | `127.0.0.1:18120` | ✅ 現在 UP |
| **現在可測候選 2** | `occamy-1.0-6bit-xl-mlx` | `127.0.0.1:18130` | ⏹ 需啟動；**含 vision**（28G） |
| **新增候選 3** | `occamy-1.0-abliterated` | `127.0.0.1:18131` | ⚠️ **無審查＋GGUF IQ4_XS 17G（有量化，含 vision mmproj）**；速度快（~120 tok/s）但智能可能受影響 → **要測會不會亂做事**。**須明示 `occamy start abl`，不與 fit6 同時開** |
| **排除** | ~~`occamy-1.0-4bit`~~ | 18130 | ❌ **無 vision**——判斷要帶視覺 |
| 只測不主用 | `Qwen3.8-27B-Splash` | `127.0.0.1:8088` | ⚠️ **現在 down**；耗資源 |
| **新角色 4：路由器／不確定性** | **Laya（`laya-multilingual`，微調後）** | 本機 MLX（不需端點） | **不是判讀，是路由**：6 ms/題、「這題要不要進人佇列」的機率、誠實的 confidence（AUROC 0.77）。**零樣本不可用（我實測 45–50% vs 亂猜 25%）** → 見 [`model-decision-models.md`](model-decision-models.md)。**⚠️ 純文字、不能讀圖** |

### ⚠️ 算力與中樞必須分離（業主指示，重要）

> 「你剛剛提出讓 Mac Studio 本機跑 35B MoE 模型，我必須說**不能這麼做**，因為這台設備的 ram 有限，還要負責考題網站，所以**僅能作為 Agent 中樞不能變成算力中樞**。」

```
┌─ Agent 中樞（Mac Studio）──────────────┐
│  Pi 指揮者 · 排程 · ReviewUI · 事件流   │  ← 不跑模型
└────────────┬───────────────────────────┘
             │ 可設定的端點（不應寫死 127.0.0.1）
    ┌────────┴────────┬──────────────┐
  現在            之後新增        之後新增
  M5 Max MBP      DGX spark ×2    AMD AI MAX+ 395
                  （大模型）      （特定優化模型）
```

| 時期 | 算力來源 |
|---|---|
| **現在（測試）** | **M5 Max MacBook Pro** |
| 之後 | **DGX spark ×2**（可提供大模型） |
| 之後 | **AMD AI MAX+ 395**（可能部署特定常用且優化過的模型） |

**立即的技術後果（事實）**：`qbr/src/qbr/engines.py` 的 `ENDPOINTS` 是**寫死的 dict**（`{"splash": 8088, "mtplx-35b": 18120}`）。**算力一搬家就要改程式** → **「端點改成可設定」應是所有實驗的第 0 步**。

### 「不亂做事」負對照（無審查模型專用，我的判斷）

測 `occamy-1.0-abliterated` 之前必須先有它。作法：

| 步驟 | 內容 |
|---|---|
| 1 | 取一批**設計者已 `accept` 過**的題目 |
| 2 | 丟給該模型，**數它動了幾個欄位** |
| 3 | 判準：**在「人已 accept」的題目上動手 ＝ 立刻不可接受** |

（這條紅線不需要爭論，因為它與「不動已定案」一致。）

### 啟動／關閉指令（業主指定：記下來，測試時自行開關）

```bash
# Ornith 1.5 MTPLX 35B（載入快）
~/models/ornith/bin/ornith start          # → 127.0.0.1:18120, model ornith-1.5-mtplx-35b
~/models/ornith/bin/ornith status
~/models/ornith/bin/ornith stop
~/models/ornith/bin/ornith bench

# Occamy 1.0（fit6 與 fit4 共用 18130，同時只能開一個；abl 在 18131，獨立）
~/models/occamy/bin/occamy start           # = start fit6（預設）
~/models/occamy/bin/occamy start fit6      # 127.0.0.1:18130, occamy-1.0-6bit-xl-mlx, 含 vision（約 88 tok/s）✔ 候選
~/models/occamy/bin/occamy start abl       # 127.0.0.1:18131, occamy-1.0-abliterated（GGUF IQ4_XS 17G，含 vision mmproj）⚠ 需明示
~/models/occamy/bin/occamy stop            # 預設 all
~/models/occamy/bin/occamy status
~/models/occamy/bin/occamy bench           # [mlx|abl|all]
```

**⚠️ 更正先前紀錄的兩點（以 `~/models/occamy/bin/occamy` 實作為準）**：
1. **`occamy start` 預設只開 fit6，不會同時開 abl**（usage 原文：「`abl` = 無審查版（**須明示，不會與 fit6 同時開**）」）。
2. **abl 是 GGUF（`occamy-1.0-abliterated-FIT-QUALITY-17G-IQ4_XS.gguf` ＋ `mmproj-...-BF16.gguf`）** → 即業主說的「**有量化**」；但備有 `mmproj` → **含 vision**。

**不用 `fit4`**（18G，**無 vision**）——判斷需要帶視覺（業主指示）。

### 雲端指導者：llm-share 與 LiteLLM 是同一份 Ollama cloud（業主事實）

> 「目前的雲端 `deepseek-v4.1-flash`，是用 **llm-share 或是 LiteLLM** 提供，兩者**都是 Ollama cloud 訂閱，只是訂閱者不同**，我會根據當下使用量決定任務要切換哪一組。」

**我的判斷：這件事直接解掉了治理矛盾。** `qbr/src/qbr/engines.py::external_egress_allowed()` 的 `QBR_ALLOW_EXTERNAL_LLM` **預設關**，真正要防的是「**題目內容離開內網**」。所以把開發迴路分兩層：

| 層 | 送出去的內容 | 可不可以走外部 |
|---|---|---|
| **A 層** | 只有提示詞、格式、錯誤摘要、程式碼 | ✅ **可以**（不含題目原文） |
| **B 層** | 題目原文、圖 | ❌ **只能內網** |

→ **指導者只要做 A 層的事，就不需要每次核准。** 這比「每次請 owner 核准」可持續。（`~/.pi/agent/models.json` 另有內網的 `dgx-spark` `192.168.10.90:8888`，不需核准。）

### Occamy 與視覺（本輪新發現，重要）

`occamy start fit6` 的模型**含 vision**（`mlx-vlm`）。這對「**圖片歸屬／切範圍**」這條線是**備援視覺引擎**：
`ornith-1.5-mtplx-35b` README 也寫「支援讀圖」。**兩者都未在圖片任務上實測**（未量）。

---

## 2. 後台資料流：8 條流，只有 4 條有讀者（查證）

**位置**：`<queue>/review-ui/`（live queue = `qbr/data/review-queues/live/`）

| 流 | 誰寫 | 誰讀 | 進提示詞？ | 實測 |
|---|---|---|---|---|
| `question_review_events.jsonl` | 人（A/B/註解/修改） | 伺服器、`repair_loop` | ✅ 註解 ✅ 被退過的改動 | 5,232 行 / 4,790 題 |
| `question_review_principles.jsonl` | 人（新增＋核准） | 伺服器、`confirm_dispute`、`ask_about_blocks` | ✅ **只送已核准** | 8 行 |
| `question_repair_questions.jsonl` | 模型（ask）＋人（answer） | 伺服器、`confirm_dispute` | ⚠️ **只有 `confirm_dispute`** | 53 行 |
| `question_ai_findings.jsonl` | 模型 | 伺服器 | —（是輸出） | **86,264 行 / 524 MB** |
| **`question_ai_learning_events.jsonl`** | **無** | **無** | ❌ | **不存在** |
| **`question_ai_feedback_events.jsonl`** | **無** | **無** | ❌ | **不存在** |
| **`question_correction_feedback_events.jsonl`** | **無** | **無** | ❌ | **不存在** |

### 這是「原則區難懂」的結構原因（我的判斷）

畫面上有「👍/👎 給 AI 這次稽核打分」「選為訓練範例」這類功能，**後端沒有寫入者也沒有讀者**——UI 上看得見，資料上不存在。

再加上三條流的**語義不同卻長在同一欄**：

| 流 | 語義 | 該放哪 |
|---|---|---|
| 原則 | **通則**（「早期試卷的 酶 是異體字」） | 全站 |
| 反問回答 | **對某一題的具體說明**（「這題的 ① 是羅馬數字」） | 逐題 |
| 註解 | **對某一題「我看到什麼不對」** | 逐題 |

**我的判斷**：開發 UI 應該讓「通則」只有一種，其餘**貼在題目身上**。

---

## 3. 人→模型的四條頻道（查證：`qbr/src/qbr/ai_findings.py`）

| 頻道 | 模板常數 | 誰讀 | 語義 |
|---|---|---|---|
| ⑥ 註解 | `NOTES` | ✅ `build_prompt` + `transcribe_system` | 背景 |
| 基本原則 | `PRINCIPLES` | ✅ 但**只 `approved_principles`**（`ai_findings.py:904`） | 約束 |
| 回答反問 | `ANSWERS` | ⚠️ **只有 `confirm_dispute`** | 逐題說明 |
| 被退過的改動 | `REJECTED` | ✅ `confirm_dispute` | 反面經驗 |
| 【已經知道的事】 | `LEARNED` | ⚠️ **是手打的 flag**（`--learned CODE=...`） | 既知形狀 |

---

## 4. 三個查證過的斷點（Q3 的答案）

### 斷點一：回答反問的迴路（已修一半）

`qbr/scripts/confirm_dispute.py:965` 自己承認：

> 「這是 `repair_daemon.sh:55-59` 描述的迴路的另一半（人對 ask 的回答下一輪讀得到）。**它從來不是真的**：只有 `PRINCIPLES_STREAM` 被讀，所以回答進了 UI 就停在那裡。」
> 「Measured 2026-09-24: the stream is empty (0 bytes), so nothing was lost — **the wire was just not connected**.」

### 斷點二：接上了，但只接一條路（**還在**）

```bash
rg "answers|learning|feedback|human_answers" qbr/scripts/ask_about_blocks.py
# → 只有一行註解提到 "answers"，沒有實際讀取
```

| 路徑 | 讀 `ANSWERS`？ | 讀 `NOTES`？ |
|---|---|---|
| `confirm_dispute.py`（紙本重讀路） | ✅ | ✅ |
| **`ask_about_blocks.py`（逐題判讀路）** | ❌ | ⚠️ **有** `notes`（line 443） |

→ **走判讀路時，模型永遠看不到你回答過什麼。**

### 斷點三（最根本）：`LEARNED` 不是從你的 block 學來的

`ai_findings.LEARNED` 的內容來自 `--learned CODE=...`（`ask_about_blocks.py:119`）——**手打的 flag**。

| 業主的想像 | 實際 |
|---|---|
| 你 block 一題 → 模型下次知道這形狀 | ❌ 沒有任何機制把 block 變成 `LEARNED` |
| 你回答反問 → 下一輪讀到 | ⚠️ 只有一半的路 |
| 模型找到錯 → 你知道它怎麼找的 | ✅ finding 有記錄（524 MB） |

**我的判斷**：**經驗以「文字」被保存了，但沒有以「產能」被使用。** 循環的第二段（把紀錄變成下一次的輸入）目前是**人工的**（你寫原則、你打 `--learned`）。

---

## 5. 業主點出的矛盾：治理讓模型「不修正」

### 三把鎖（查證）

1. `apply_dispute_repairs --apply` **不在 daemon 排程裡**（`repair_daemon.sh` 只跑 scan＋report，不呼叫模型）
2. `--human-flagged` **只認封閉標籤**
3. AI **不得寫 review event**（`AGENTS.md`：append-only，不得冒充人類審核者）

### 我的判斷：矛盾的本質是「兩件事被綁在同一個權限級別」

`AGENTS.md` 把**「改題目文字」與「決定這題對的」綁在一起**（都是 G3/G4）。但這兩件事在證據上完全不同：

| | 改題目文字 | 決定題目對錯 |
|---|---|---|
| 性質 | **可回溯的資料變更** | **判斷** |
| 錯誤成本 | 低（可 revert） | 高（定案） |
| 需要什麼 | **證據** | **人** |

### 提案：把 G3 拆開，有證據的修復 **default-open**

六個條件**全部滿足**才自動修：

| # | 條件 | 為什麼 |
|---|---|---|
| 1 | **有紙本證據**（crop 存在、人看得到） | 沒證據的修改就是幻覺 |
| 2 | 改動是**欄位級 before/after** | 可機械驗證，不是散文 |
| 3 | 每輪**預算上限**（N 題） | 「改進不是擴張」 |
| 4 | 每次改 append `correct` 事件，**不覆寫** | 可回溯 |
| 5 | **revert 是一次操作** | 錯誤成本壓到最低 |
| 6 | **人 accept 過的題目不自動改**（除非人明示） | 不動已定案 |

**反轉語義**：人的 `block` 是**「否決」**，不是「需要核准」。

### 這不是新發明（查證：機制已存在）

| 既有機制 | 位置 | 實測狀態 |
|---|---|---|
| `dispute_apply` | `qbr/src/qbr/dispute_apply/` | 機器整欄修復 104 筆涉及 90 題 |
| `withdrawals`（撤回） | 同上 | **已撤回 64 題 / 91 欄** |
| `REPAIR_REVIEWER_PREFIXES` | `review_ui/constants.py:198` | — |
| `REJECTED` 頻道 | `ai_findings.py` | 「不准重貼」已實作 |

**所以工作是「把既有機制接進 agent 迴路」，不是造新的。**

---

## 6. 架構提案：**兩條迴路，不是一條**

```
┌─ 生產迴路（無人、常駐機、跑 35B）──────────────────────┐
│                                                        │
│  ① 考選部下載 agent                                     │
│       └→ ② 管線切題 agent（腳本＋路由）                │
│            └→ ③ 判讀 agent（35B 逐題）                  │
│                 ├→ 有紙本證據＋可驗證 → 自動修（§5 六條件）│
│                 └→ 無法判斷 → 進人的佇列                 │
│                                                        │
└────────────────────────────────────────────────────────┘
                     ↕ 共用同一套端點與提示詞
┌─ 開發迴路（業主＋Pi＋35B，在筆電）──────────────────────┐
│                                                        │
│  業主 ↔ Pi 指導者（雲端/本地）↔ 35B                   │
│                                                        │
│  每次對話 = 一次提示詞／格式實驗                        │
│  產物 = 一個新的提示詞版本（可 A/B）                    │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### 為什麼是兩條而不是一條

| 理由 | 證據 |
|---|---|
| 兩條必須跑**同一個模型、同一套提示詞** | 否則「調適」調的不是「生產」在跑的東西 |
| **已經有可比較的機制** | `ai_findings.prompt_version()` **已在 hash 提示詞輸入** |
| 端點已是參數 | `engines.py::ENDPOINTS` 已含 `mtplx-35b` lane（`QBR_MODEL_BASE_URL`） |

### 「全程讓 35B 實作」的正確形狀

不是 Pi 在旁邊看它，而是**兩條迴路共用 `engines.py` 的 `ENDPOINTS`**：

- 開發迴路調的就是生產迴路真正跑的東西
- 每次實驗自動產生一個 `prompt_version` → **同一題在不同提示詞下的答案已經可比**

### 管線紀律（業主指定）→ 對映到既有架構

| 業主說的 | 既有機制 |
|---|---|
| 「不要 overfitting」 | `qbr/AGENTS.md`：「腳本只量紙張的性質，語意一律提示詞」 |
| 「寫太大要拆分＋路由」 | `qbr/src/qbr/` 已分模組；`disputes.py` 是偵測器集合 |
| 「解決 90-95%，剩下給 LLM」 | **尚未量**：管線目前解掉幾 %？ |

**⚠️ 未量**：「90-95%」目前是目標不是現況。要量它需要：全庫跑一遍 → 數「腳本解掉」vs「需要模型」。

---

## 7. 開發 UI 的設計（業主描述 → 對映）

業主說舊 UI 好的是「**改題目＋截圖**」，壞的是「**資訊非常混亂**」。

**我的判斷**：這正是「**取代不是分岔**」的適用場景。

| 業主需要 | 既有機制 | 狀態 |
|---|---|---|
| comment 讓 AI 知道怎麼做 | `NOTES` 頻道 | ✅ 已接 |
| AI 做完顯示**前後對照** | `question_correction_feedback_events.jsonl` | ⚠️ **schema 有、無寫入者** |
| pass / 再 comment 改到好 | `correct` 事件＋review 流 | ✅ 機制有，UI 未做 |
| 修改題目 | 舊 UI 的 `correct` | ✅ 被驗證過的模式 |
| 截圖（圖夾在文字中、多圖、表格） | `image_refs` ＋ `crop_run_figures.py` | ✅ 機制有 |
| 「變通」（人手動截圖＋寫「如下圖」） | `human_review_pdf_visual` 動作 | ✅ 存在 |

**結論**：新介面 = 舊介面的**編輯與截圖能力**（真的被用過）＋ **三層資訊分離**（通則／逐題說明／模型輸出不再混同一欄）。

---

## 7.5 Block 語義與「退回原樣」的根因（2026-09-27 業主提問）

**業主問**：
> 「AI改錯的時候，我之前會按 Block 直接擋住，然後 AI 就會改標籤顯示退回。但我 Block 的意思有可能是**有改但是還是有錯，希望進一步改錯，而不是退回原樣**，這部分的原因是什麼？」

### 根因（事實）

**在程式的定義裡，Block 只有一個意思——「這題現在不可用」；而「撤回機器改動」被綁在「Block 這個事件本身」上，不是綁在「你的 Block 是在說哪一種錯」。**

| # | 事實 | 出處 |
|---|---|---|
| 1 | 撤回的**第一順位理由就是 bounce-back（人打回）**，且**不需要其他條件** | `dispute_apply/cli.py:320-325`：`add_withdrawal(withdrawals, key, "bounce-back", item["fields"], "人把機器的改動打回了…整欄改寫還原")` |
| 2 | 優先序把**人的退件排最前** | `withdrawals.py:363` `WITHDRAW_PRIORITY = ("bounce-back", "manual", "systematic-pair", ...)` |
| 3 | 撤回的標籤**寫死成「機器改錯」** | `withdrawals.py:54` `WITHDRAWN_LABEL = "已還原（機器改錯）"` |
| 4 | **系統有能力分辨**「這一輪不要寫」與「上一次寫錯了」→ 過期／偵測器衝突／`▢`／兩個量測不過 **不撤銷任何東西** | `withdrawals.py:46-49` |
| 5 | **迴圈只有一個方向**：`block → 找已存在的 detector → 沒有就建 → rescan → reopen`。**沒有「照著人的話再改一次」這一段** | `qbr/scripts/repair_loop.py:1-40` |
| 6 | **業主原話已被抄進程式碼** | `review_ui/queue_view.py:715-727`：「你**退回等於沒有解決**…可以改成AI已修改，但是**「還原」這種事情不是修改**」 |
| 7 | **`ask_about_blocks.py` 完全不讀人的回答** | grep `answers` 全檔只命中行 484 的 `PRINCIPLES_STREAM` |
| 8 | **上限（3 次）只數 `block`**，`needs_review` **被刻意排除**（「那是另一種輸入」） | `withdrawals.py:38-42` ＋ `test_the_cap_counts_the_same_events_the_loop_counts` |

**實測（live queue，5,232 筆事件）**

| 量到的 | 數字 |
|---|---:|
| 最後決定是 `block` 的題目 | **286 題** |
| 其中 **block 之後機器什麼都沒做** | **279 題** |
| block 之後只有其他 reset | 7 題 |
| `withdrawn` 事件 | **0** |

→ **279/286 的 block 是「丟進一個黑洞」**：機器沒有再讀、沒有再改、也沒有撤回。

> **⚠️ 更正 Q7**：Q7 寫的「`withdrawals` 已撤回 64 題／91 欄」（出自 `apply_dispute_repairs.py:195-230`，來自機器真的跑過 `dispute_apply` 的那台）**不是在 live queue**。live queue 的 **210 筆機器 reset 事件 `applied` 全部是 `None`**——**這台筆電的 live queue 從未跑過 `dispute_apply --apply`**。兩組數字來自不同 queue，不可混用。

### 修法（D8）：分岔，不是改定義

改了 `block` 的語義會破壞 append-only 既有記錄的意義。**正確做法是在 UI 把 Block 分岔成兩種**：

| 業主的意思 | 應該是哪個動作 | 機器該做什麼 |
|---|---|---|
| 「**改錯了**」 | `block` | 撤回 ＋ 當否決（維持現況） |
| 「**方向對，還沒對**」 | **`needs_review` ＋必填註解** | **繼續改**（讀你的註解，再讀一次紙本） |

**關鍵**：`needs_review` **已經存在**、**已經被刻意排除在撤回之外**（`withdrawals.py:38-42`），**只是沒有把「繼續改」接上去**。

**最小改動三步**（即 D2(a)，不需發明新機制）：

1. 允許 `needs_review` ＋註解 當「繼續改」的輸入
2. 讓 `ask_about_blocks` 讀 `ANSWERS`
3. 撤回只在 `block` 發生，`needs_review` 不撤回

**我的判斷**：這是「**Block 不帶原因 → 循環沒有新資訊可讀**」的直接後果，也是「這套設計能不能變聰明」的第一個必要條件。

---

## 7.6 答案審核的正確性（業主提問，完整報告：[`answer-authority.md`](answer-authority.md)）

| 業主的擔心 | 現況 | 結論 |
|---|---|---|
| **修正答案有沒有被正確採用** | `corrections.authoritative_answers()` 三段優先序（ANS → **MOD 整張表重印** → MOD 腳註）；`＃` 格**跳過不猜**；送分題用該題實際選項；golden 檔有 `answer_authority_source: "answer+corrected"` | ✅ **核心邏輯對，有測試** |
| **位移（題號對位錯）** | 有 `answer-not-on-sheet` gate（檢查答案字母在不在選項裡）——那是「答案不在選項」，**不是位移** | 🔴 **未量，且無專用檢查** |
| **成績公告後的修正答案要回報、人要介入** | ① 取得修正卷**沒有排程**（手動）② 顯示 fallback 把它排在**第三** ③ 答案區**沒有「修正答案卷」文件種類** ④ **沒有任何事件說「這題答案被改過」** | 🔴 **這條路不存在** → D6 |

### 業主自己講出的分野（當設計約束）

| 情境 | 特性 | 需要什麼 |
|---|---|---|
| **第一次建大量題庫（往回看）** | 答案已定案，MOD 與 ANS 同時可得 | **一次定案**即可，**不需要監控** |
| **發布後才出現的修正（往後看）** | 修正卷在成績公告後才出現 | **需要回報 ＋ 人介入**（D6） |

→ **不要為「往回看」蓋監控機制**（它不會再變）；**只為「往後看」蓋**。

---

## 7.7 「能不能循環讓解決問題變聰明可控？」（業主提問）

**直接回答：可控 ✓，但目前不能變聰明 ✗。**

### 「可控」成立（事實）

append-only 事件流、負對照紀律、G0–G2 邊界、`prompt_version()` 的 A/B 機制——**都在，而且是量過的**。

### 「還不能變聰明」的三件量到的事

| # | 事實 |
|---|---|
| 1 | **學習訊號被寫成文字，但沒有被讀成產能**：`LEARNED` 是**手打 flag**（`--learned CODE=...`）；`ANSWERS` **只接一半**（`confirm_dispute` ✅ ／ `ask_about_blocks` ❌）；`question_ai_learning_events.jsonl` **有 schema、無寫入者、無讀者** |
| 2 | **Block 不帶原因 → 循環第二段沒有新資訊可讀**：286 題 block、**279 題機器什麼都沒做**。所以「循環三次」**跑不出三次不同的嘗試**——三次用的都是同一份「他打回了」 |
| 3 | **驗收是「兩引擎一致」，不是「跟人一致」**：`ANSWER_DISAGREES` 與人一致率僅 **8%**，所以被**移除**——**是拿掉，不是修正**。→「AI 判斷題意對不對」目前沒有可信來源，只有格式判斷 |

### 結論（我的判斷）

> **變聰明的第一個必要條件，不是換更大模型，而是：讓你的每一次否定都帶上原因，而且那個原因真的進得了下一輪的提示詞。**

### 三個可反駁的「有沒有真的在變聰明」指標

| 指標 | 為什麼可反駁 |
|---|---|
| **收斂性**：同一組題目，第二次跑比第一次**少改幾個欄位** | 變少＝真的學到 |
| **人打回率逐批下降**（同類型題目相比） | 要控制題目類型，否則不可比 |
| **`prompt_version` 改動前後，同批題目的 finding 差異** | **機制已存在**（免費 A/B） |

---

## 7.8 實驗重要性排序（Pi 的判斷，理由導向）

| 序 | 實驗 | 為什麼在這個位置 |
|---|---|---|
| **0** | **把 `ENDPOINTS` 從寫死改成可設定** | **沒有它，後面每個實驗都綁死在 `127.0.0.1`**；算力確定會搬家。約 1 小時，**零語意風險** |
| **1** | **補 `ask_about_blocks` 的 answers 斷點** | 直接解「我的話沒被讀到」；小、可 A/B |
| **2** | **「不亂做事」負對照 harness** | 要測 `occamy-abliterated`（無審查）**之前必須有它**，否則不能安全測 |
| **3** | **提示詞優化量測** | 需要 0＋1 才有意義。**A/B 機制已存在**：`ai_findings.prompt_version()` 已在 hash 提示詞輸入 |
| **4** | **向量圖缺口（D4）** | 對「圖片定位」這個首要目標是**核心**：連圖都看不到就無從定位 |
| **5** | **「90-95%」現況量（D5）** | 所有「有沒有變聰明」的比較基線 |

**→ 對 D2 的回答：(a)，但先做第 0 步。**

### 「向量圖缺口」與「90-95%」用白話（業主說看不懂這兩題）

| 題 | 白話 |
|---|---|
| **D4 向量圖缺口** | 有些圖，PDF 裡**不是「一張圖片」，而是「一堆線條的畫法指令」**。掃描圖／截圖 → 點陣圖 → **現在抽得出來**；**化學結構式、電路圖、流程圖 → 常常是向量圖 → 現在完全抽不出來**。「要不要量」＝ **要不要去數：醫學類科的題目裡，有多少題的圖是向量圖、因而現在是空的**。來源：`1051_藥師(一)_藥物分析與生藥學` p5 三個化學結構，其中兩個是向量圖；兩條抽圖路只認點陣圖。 |
| **D5「90-95%」** | 業主說過「題目進來能解決 **90-95%**」——這是**目標數字，沒人量過現在是多少**。「先量成現況」＝ **先做一次體檢**：拿一批題目丟進現在的管線（**先不放模型**），數出「**產出跟正確答案一模一樣、完全不用人碰**」的比例。若是 60% → 工程很大；若是 88% → 差距很小。**它是所有「有沒有變聰明」的比較基線。** |

---

## 8. 待決（給設計者）

> ⚠️ **本表已凍結，只作考古。** **唯一權威的待決清單是
> [`../SKILL.md`](../SKILL.md) §9**（本輪重整為 9.1–9.6）。
> 兩處都放「待決清單」就是「兩個做同一件事的東西」——正是 charter 說的「兩個可以不一致的地方」。
> 本節只保留**歷史狀態**：當時（第二輪）寫下來的樣子。

> **2026-09-27 第二輪（歷史）**：新增 **D6–D8**；D1–D3 已由設計者部分表態，D4/D5 已解釋。

| # | 當時的問題 | 當時的狀態 | **現在的真實狀態（見 §9）** |
|---|---|---|---|
| **D1** | G3 拆解（default-open、block=否決） | 🟡 設計者已表態 | **已決**（「視為決定」）→ §9.2 |
| **D2** | 第一個實驗選哪個 | 🟡 Pi 建議 (a)＋先做第 0 步 | **已決**（依 Pi 建議）→ §9.2。**第 0 步仍未做** |
| **D3** | 指導者模型 | 🟡 已定 `deepseek-v4.1-flash` | **已決** → §9.1 |
| **D4** | 向量圖缺口要不要量 | 🟡 Pi 建議要量 | ✅ **已量完** → §9.1＋[`vector-figure-gap.md`](vector-figure-gap.md) |
| **D5** | 「 90-95%」要不要量 | 🟡 Pi 建議先量 | **已決：先不用** → §9.2 |
| **D6** | 答案修正監控 | 🔴 未裁決 | **方向已定（訓練對齊 Agent）、機制未設計** → §9.3 |
| **D7** | `review_state.py` 拆分 | 🔴 未裁決 | **已決：後續拆** → §9.2 |
| **D8** | **Block 分岔**：「改錯了」vs「方向對還沒對」 | 🔴 未裁決，**本表寫的提案已作廢** | ❌ **設計者已否決本表提案**（`needs_review`＝AI 改完之後人應該要看）→ **§9.3，需重新設計** |

### 第 0 步（Pi 的建議，在 D2 之前）

**把 `engines.py::ENDPOINTS` 從寫死的 dict 改成可設定。** 理由：

1. 設計者已宣告算力會搬家（M5 Max → DGX spark ×2 ／ AMD AI MAX+ 395）
2. 不做的話，**每個實驗都綁死在 `127.0.0.1`**
3. 約 1 小時，**零語意風險**（只是把常數變成讀環境變數；`QBR_MODEL_BASE_URL` 已有先例）

### D7 的拆分範本（既有成功案例，事實）

`dispute_apply/withdrawals.py` 開頭：
> 「Extracted verbatim from `scripts/apply_dispute_repairs.py` so **one file is no longer 2,321 lines**. `apply_dispute_repairs` **re-exports every name here**; that indirection is deliberate and is why the test files that load the script by path keep working unchanged. **No behaviour was changed.**」

拆成 6 模組：`text.py` 725／`page_read.py` 648／`cli.py` 464／`withdrawals.py` 382／`asking.py` 141／`report.py` 114。

**可套用到 `review_state.py` 的方法群（量到的）**：

| 群 | 方法 | 行數 |
|---|---:|---|
| SQL filter 拼裝 | `_sql_candidate_filter_parts` | **508**（最長） |
| SQL filter 拼裝 | `_sql_light_candidate_filter_parts`、`_sql_group_candidate_rows`、`_sql_answer_sheet_cte` | 186／112／141 |
| payload 組裝 | `candidate_payload`、`filtered_candidate_payloads`、`filtered_group_payloads_from_rows`、`filtered_answer_payloads`、`answer_sheet_payload`、`group_sheet_payload`、`correction_feedback_payload` | 307／186／181／131／138／121／132 |
| upsert | `_upsert_formal_candidate_rows`、`_upsert_sql_question_group`、`_ensure_ai_feedback_schema` | 262／94／96 |
| pipeline | `_sql_pipeline_payload`、`pipeline_payload`、`_pipeline_payload_from_counts` | 222／95／92 |
| 圖 | `save_manual_image_asset` | 152 |

**137 個方法、中位數 39 行** → 拆分可行（大部分方法小，問題集中在「SQL 拼裝」與「payload 組裝」兩個群）。

---

## 9. 未量（誠實清單）

> **2026-09-27 更新**：**向量圖缺口已量完**（§8 D4）→ 從本清單移除，改記在
> [`vector-figure-gap.md`](vector-figure-gap.md)（含它自己的 6 項未量）。

- ~~**向量圖缺口大小**~~ ✅ **已量**：向量圖 1.3%、內嵌圖 `xref=0` 5.5%。
- **管線目前解掉幾 % 的題**（「90-95%」是目標非現況；量法：對一批已 accept 的題重跑管線，數「零模型產出 == 人 accept 的字」的比例）
- **`ornith-1.5-mtplx-35b` 與 `occamy-1.0-6bit`／`abliterated` 在圖片任務上的能力**（三者宣稱支援讀圖，**均未實測**）
- **`occamy-1.0-abliterated` 會不會「亂做事」**（無審查＋量化；**負對照 harness 尚不存在**）
- **`ask_about_blocks` 補上 answers 之後的品質變化**（無 baseline 對照）
- **提示詞優化的效果**（`prompt_version` 可比，但還沒跑 A/B）
- **答案位移發生率**（未量；且**未查到有對應檢查**）
- **現有已建題庫中多少題的答案來自 MOD 而非 ANS**（未量）
- **`fetch_corrections.py` 上次實際執行時間與抓到幾份**（未量）
