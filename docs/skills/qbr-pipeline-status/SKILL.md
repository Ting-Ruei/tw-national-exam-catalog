---
name: qbr-pipeline-status
description: The measured state of the qbr question-bank pipeline as of 2026-09-20 — what is stable, what is not, what is blocked, and which decisions are still open. Use when asked how far extraction/figure/group/AI-review work has got, when deciding what to build next, when a queue shows empty options or missing pictures, or before trusting a "two engines agree" claim. Includes the commands to re-measure, because every number here decays.
---

# qbr 管線現況（量測快照）

`tw-national-exam-catalog/qbr/`。每一條數字都是量出來的，日期 **2026-09-20**。
**重測指令在第 7 節**——這份文件會過期，指令不會。

**先讀 [`../../qbr/AGENTS.md`](../../../qbr/AGENTS.md) 的鐵則，再讀這份。**
這份是**現況**，不是規範；衝突時以 AGENTS.md 與 charter 為準。

## 0. 一句話

**骨架在 78,690 題上穩定；圖片與 AI 審核在新管線上各缺一整個階段。**

| 面向 | 現況 |
|---|---|
| 抽取（切題／選項／答案） | **穩定**（78,690 題，8 類科，S3 只擋 11 卷） |
| **選項續行被截斷** | **已修 2026-09-21**：抽樣 344 → **1** 欄位（160 卷）；新測試在舊碼上紅；詳見 `qbr/reports/option_continuation_fix.md` |
| 圖片截圖 | **舊佇列穩定（已驗收）；新佇列完全沒有** |
| 題組 | **穩定**（908 組／1,967 題，三項完整性檢查全過） |
| AI 審核呈現 | **新管線 0 題；舊管線有 543,944 筆但不在 v2 顯示** |
| 驗收標準 | **已決定（第 6.1 節）**：出貨欄位必須被第二引擎找到；不再是「文字一致」 |

---

## 1. 抽取引擎：七階段

```text
S0_intake   凍結 Q / ANS / MOD（digest）
S1_triage   PDF 便宜分類
S2_dual     雙引擎讀取（A=PyMuPDF、B=poppler）+ 遮罩 + 分段
S3_gate     這卷能不能發布（確定性，無模型）
S4_records  一題一筆，答案合併
S5_package  不可變封裝 + lineage
S6_verify   把封裝對回紙本
```

### 目前（984 卷／78,690 題／8 類科）

| 階段 | 通過 | 未通過 |
|---|---|---|
| S0 intake | 566 | 0 |
| S1 triage | 566 | 0 |
| S2 | 566 | 0 |
| **S3 gate** | **555（publish）** | **11（quarantine）** |
| S4 records | 555 | 0 |
| S5 package | 555 | 0 |
| **S6 verify** | **554** | **4** |

**已被擋下的 15 卷**（`/tmp/qbr-expand/*/package_report.jsonl`）：

| 症狀 | 卷數 | 位置 |
|---|---|---|
| `count-mismatch`（讀到的題數 ≠ 答案卷） | **6** | **全部在 醫師(二)** |
| `options-not-four` + `answer-not-on-sheet` | 4 | 醫師(一)、醫事放射師 |
| `options-not-four` 單獨 | 2 | 物理治療師、醫事放射師 |
| `replacement-codepoints` | 1 | 醫事放射師 |
| `S6 catalog_package_validator` | **4** | 醫師(一)、藥師、醫事檢驗師×2 |

**那 4 卷 S6 不是建置缺陷**：catalog validator 要求
`metadata.review_status=accepted`，而封裝寫的是 `machine_verified_pending_human`。
**沒有任何真人審過**，所以 validator 拒絕——這是 `REV-04 / REL-01 / GOV-05` 的治理缺口，不是讀錯。

### 已穩定（不要動）

- **切題邊界**：後繼號碼；折行不是新題（全庫 89,463 個 anchor 只有 2 個符合折行條件）。
- **選項歸屬**：私用區字母表、逐家族試切、拒絕為預設。
- **答案**：只從官方答案卷（`_ANS`）讀，修正表（`_MOD`）疊在上面。**送分／`A或D` 都正確**（387 題 `is_special_correction`）。
- **字形遺失**：只報位址、不補字。
- **`⻑`→`長`**（`substituted-ideograph`）：CJK Radicals Supplement 的區塊測試；NFKC **折不回**，必須用碼位區間判。在沒看過的新科目上仍然成立（新科目 0.28% 反而略高，因為 `年⻑者`）。
- **上下標**：用 A 的字級（`size`）判方向，B 沒有 `size`。

### 待優化

| 項目 | 為什麼 |
|---|---|
| **選項續行被截斷**（第 6.1 節） | 抽樣 2.06%；**未被標記**，審題的人看不到；`repair.py` 只對題幹有折行測試 |
| `count-mismatch` 6 卷 | 同一症狀集中在 醫師(二)，值得單獨一輪 |
| 「兩引擎一致」沒有被執行 | 見第 6 節；標準已改為可執行（第 6.1 節） |
| 提示詞第 4 條（「以『的』結尾」） | **會過度觸發**：正常問句也被判缺陷（兩引擎都誤報） |
| `queue_index.json` 的原始 `categories` | 半形／全形變體未折疊（UI 不受影響） |

---

## 2. 圖片截圖

### 舊佇列（429 卷／33,150 題）：**已驗收，穩定**

| 項目 | 數字 |
|---|---|
| 有圖的題 | 1,169（選項圖 264） |
| 裁切檔 | 270 |
| HTTP 取回並**用 PIL 真的解碼** | **2,015 / 2,015，0 失敗** |
| 指向不存在的檔／絕對路徑 | 0 / 0 |
| 外部標準（手工題庫） | 召回 **97.1%**、漏 13、多 5 |

驗收方式是「**看不到的證據不是證據**」：HTTP 200 不算，要用 PIL 解碼。

### 新佇列（984 卷／78,690 題）：**完全沒有圖**

```
image_refs   : 0 / 78,690
stem_image   : 0 / 78,690
crops/       : 這個目錄不存在
```

**原因不是讀不到，是那一步沒被呼叫**：`batch_package.py` / `batch_run.py` 從不呼叫
`crop_run_figures.py`（這是設計上的第二階段，不是封裝的一部分）。
另外 `qbr/src/qbr/package.py:210` 把 `has_visual_asset` **寫死成 `False`**。

### 為什麼這件事嚴重

**437 題 blocker 正是要看圖才能審的題**：

| 症狀 | 題數 | 真正是什麼 |
|---|---|---|
| `option-shape`「四個選項都沒有文字」 | 326 | 選項是化學結構圖 |
| `option-shape`「選項數 0」+ `dangling-answer` | 111 | 整組選項是圖 |

打開新版 v2，這些題**只會看到空白選項**，而且沒有任何欄位說它本來有圖。
`disputes.py` 的 `empty-option` 抑制邏輯（「251/274 是對的，因為選項就是圖」）**失去依據**，
因為它要讀 `image_refs[].asset_role == 'option-image'`。

**修它的前提**：先確認圖片綁定在哪一層（`crop_run_figures` 或 exporter），
否則是在錯的樓層加欄位。**未決定。**

---

## 3. 題組

### 現況：**穩定**

| 項目 | 數字 |
|---|---|
| 組數 | **908** |
| 題數 | **1,967**（2 題組 769、3 題組 127、4 題組 12） |
| `shared_stem` 缺少的組 | **0** |
| `group_size` 與實際成員數不符的組 | **0** |
| `group_position` 不連續的組 | **0**（0-based，0…n-1；**0 是組頭**，不是缺號） |
| 以續接詞開頭但沒有 `group_ref` | **0 / 828** |
| 宣告題組但沒有 `bound` | **0 / 112** |

### 兩個訊號（都用紙本自己的話）

| 訊號 | 組數 | 題數 |
|---|---|---|
| `marker:承上題` | 794 | 1,615 |
| `declared:3`（依序回答下列三題） | 101 | 303 |
| `declared:4` | 11 | 44 |
| `marker:上述` | 2 | 5 |

**第三個訊號（幾何間距）已量測並否決**：組間 104.9 pt vs 非組間 99.8 pt，
掃過 20–300 pt 的最佳精確率 **6.9%**（13.4 個誤報換 1 個真組）。
留在 `groups.py` 的 `GROUP_GAP_RATIO` 是**記錄，不是規則**，沒有任何地方使用它。

### 限制（誠實）

**沒有 marker、也沒有宣告的組，偵測不到。** 這不是實作偷懶，是上面那個量測的結論：
間距分不出來。所以「共用題幹」這個類別只覆蓋紙本**明說**的那些。
另有一批 486 題題幹 <12 字且不在題組（例如「下列何者不是酮體？」）——**尚未確認**
是正常短題，還是依賴前題的未偵測題組。

---

## 4. 後續題目審核的自動化（AI 審核）

### 答案：**在新管線裡從未呈現過。**

| | 新管線（qbr / v2） | 舊制度 |
|---|---|---|
| `ai_review` 欄位 | **78,690 題全部 `None`** | — |
| `v2.html`（基準線）提到 `ai_review` | **0 次** | — |
| `mobile.html`（v1 參考） | — | **10 次** |
| 事件量 | **0** | SQL `exam.question_ai_review_events` **543,944 筆**（2026-06-20 → 08-03） |

舊 SQL 的來源是 codex 系列稽核（`ai:codex-subagent:gpt-5.6-luna` 328,583 筆等），
那是**舊路線**，依決定不混入新管線。

### 新管線量過什麼（`reports/ai_first_pass_triage.md`）

視覺重讀當 triage 的**精確率只有 12–16%**：單引擎旗標 42 題只有 5 題值得 block；
兩引擎一致 19 題只有 3 題。**決定不接成 triage 佇列。**
根本限制：**一個讀錯的模型與一個真的錯字，在單次轉錄裡無法區分。**

模型在本專案**唯一已被證明可靠的工作**：**回答關於它看到的圖的封閉問題**
（例：「紙本印的是 `長` 還是 `⻑`？」）。

### 引擎現況

| 引擎 | 端點 | 狀態 |
|---|---|---|
| `qwen3.8-flash-next` | DGX `192.168.10.90:8888` | **可用**（讀取主力：0.44 s/題，85%） |
| `qwen3.8-27B` | `127.0.0.1:8082` | **已上線但沒載模型**（`/v1/models` 回空 `data`） |
| `ornith-1.5-mtplx-35b` | `127.0.0.1:18120` | 量測時用過 |

**已知未修**：`qbr/scripts/compare_engines.py` 的 `QWEN38_MODEL` 預設是 repo-id，
但 server 服務的是檔案系統路徑 → **HTTP 400**。

---

## 5. 待決策議題

按「擋住什麼」排序：

| # | 議題 | 擋住什麼 |
|---|---|---|
| 1 | **新佇列補圖**（wire `crop_run_figures` + 修 `has_visual_asset`） | 437 題 blocker 不可審；要先決定綁定在哪一層 |
| ~~2~~ | ~~**驗收標準到底是什麼**~~ | **已決定（第 6.1 節）** |
| 3 | **S6 治理缺口**：`review_status=accepted` 由誰決定 | 4 卷永遠發布不了；這是 GOV-05 的政策題，不是程式題 |
| 4 | **醫師(二) 6 卷 `count-mismatch`** | 同一症狀；值得單獨一輪 |
| 5 | **佇列持久化**：`qbr/data/review-queues/live` 被 `.gitignore` 第 27 行忽略 | 能過重開機，**不在版控**；長期備份未決定 |
| 6 | 486 題短文不在題組 | 是否為未偵測的共用題幹 |
| 7 | 提示詞第 4 條重寫 | 缺陷偵測誤報 |
| 8 | `compare_engines.py` 模型 id | 兩引擎比較工具跑不動 |

**已決定（不要重開）**：

- **只審新管線的題，不與舊 SQL 合併。**（舊制度不混入。）
- **不把視覺重讀接成 triage 佇列。** 精確率 12–16%。
- **`--carry-from` 必須指向佇列根目錄**，不是 `review-ui/`；指錯會靜默漏帶。
- **驗收標準 = 每個欄位都被第二個引擎在同一邊界找到**（第 6.1 節）。
- 改進用**取代**，不是分岔；**儲存的字永不重寫**（偵測，不是轉換）。

---

## 6. 最重要的更正：驗收標準沒有被執行

`qbr/AGENTS.md` 第 40 行與多份文件都寫：

> 驗收標準是**兩個引擎一致**，不是測試全綠。

**但實際跑的程式沒有做這件事。**

| 檢查 | 結果 |
|---|---|
| `golden_path.py` 呼叫 `extract_lines_b` 或 `extract.compare` | **0 次** |
| `batch_run.py` / `batch_package.py` 同上 | **0 次** |
| `S2_dual` 的 `ok` 是什麼 | **分段連續性**（anchor／gaps），不是 A/B 一致 |
| `disputes.of_question(engine_counts=...)` | **從未被傳入** → `engine-disagreement` 是**死碼** |
| `/tmp/qbr-expand` 的 `package_report` 有 A/B 欄位嗎 | **沒有** |

`stage_dual` 確實**讀了兩個引擎**（`analyse_items` 讀 `rows_a`、`rows_b`），
但 B 只被當成**分段的額外候選視圖**（`extra_views`），**不是**被拿來跟 A 逐項比對。

### 我自己量的（40 卷隨機抽樣，遮罩後、Level-0 修復後都一樣）

| 分類 | 卷數 |
|---|---|
| `TEXT_DISAGREEMENT` | **31** |
| `STRUCTURAL_DISAGREEMENT` | **9** |
| `EXACT_AGREEMENT` / `SAFE_NORMALIZED_AGREEMENT` | **0** |

（40 卷，`random.seed(42)` 且 `glob` 先排序；未排序會跳成 32/8。內容 sha12 = `4b7def38762c`。）

`content_similarity`：min 0.98158、median 0.99723、max 1.00000（≥0.999 只有 9/40）。

**兩引擎讀出的題數相同：38/40。** 2 卷差很多：

```
1011_藥師_藥事行政與法規 : A=50  B=3
1032_醫師(二)_醫學(六)   : A=80  B=2
```

### 所以文件裡那個「3,516/3,516 兩引擎一致」是什麼

- **3,516 = 語料裡的主考卷總數**（不含 `_ANS` / `_MOD`），不是已打包的卷數（984）。
- 產生它的輸出檔（`qbr/data/survey*`）**不在版控、也不在磁碟上**，我**無法重現**。
- 如果「一致」指的是 `EXACT_AGREEMENT`，抽樣是 **0/40**，不是 100%。
  如果指的是「題數相同」，抽樣是 **38/40**，也不是 100%。

**我不是在說那個量測造假，是在說它無法被重跑驗證，而且它命的指標不是出貨路徑在算的指標。**
**在把標準寫成可執行之前，「已驗收」不等於已驗收。**

---

## 6.1 驗收標準（已決定 2026-09-20）

### 決定

> **驗收標準 = 出貨的每一個欄位，都必須被第二個引擎在它自己的讀數裡，於同一個邊界找到。**
> **找不到的欄位，就是必須看人（或看模型）的清單；而「找不到的數量」就是驗收數字。**

**不是**「兩引擎的文字相同」。那個標準從來就不可達成，而且理由已量過：引擎 A 有字級、引擎 B 沒有
（上下標）；引擎 B 把選項記號與每一個折行放在自己一列，A 沒有（`ENGINE_STRATEGY.md` §4.1）。
**一個不可達成的標準不是嚴格，是沒有數字。** 可達成的標準才會產生「還要看多少題」。

### 為什麼是「每個欄位」而不是「整卷文字」

我把 120 卷用「整卷文字相似度」量過，結論只有一個：**它永遠不會是綠的**。

| 量法 | 結果 |
|---|---|
| 40 卷 `extract.compare`（整卷 blob） | 31 `TEXT_DISAGREEMENT`、9 `STRUCTURAL_DISAGREEMENT`、**0 `EXACT`** |
| 80 卷「B 領讀再切題」逐欄比對 | 14.21% 欄位「無法解釋」——**幾乎全部是同一個儀器性質** |
| 120 卷「B 領讀」精修後 | 9.06% 仍「無法解釋」，集中在 **110 / 9,320 題** |

那 9% 不是紙本的爭議，是 **B 領讀切題比較差**：B 把記號放自己一列，所以 B 領讀的選項
每一格都錯開一格（量到 558 題）。**把儀器能力差異算成缺陷，就是過去的規則爆炸。**

### 可執行版本（量到 160 卷，`qbr/src/qbr/continuation.py`）

把「選項自己的文字範圍」當成單位，而不是把「整卷文字」當成單位：
一條選項從它的記號到下一個開頭的東西（記號或題號），中間全部是它的續文，
可以跨好幾列（引擎 B 常把一個選項拆成三、四列）。

| 量測 | 數字 |
|---|---|
| 讀取的題數 | 12,840（160 卷） |
| 引擎 A 自己看到「選項被截斷」 | 658 |
| 引擎 B 自己看到 | 2,207 |
| **兩個引擎都看到** | **385 欄位 / 265 題（2.06%）** |
| 被截斷的卷 | **72 / 160 卷** |

**「兩個引擎都看到」才是可用的門檻。** 單一引擎的 658／2,207 差一個量級，因為兩個引擎的
折行切法不同；**但同時被兩個獨立儀器看到的，不是任何一個的切法造成的。**
用單一引擎會得到兩個不好的結果：2,207 會淹掉審題的人，658 會漏掉一半的真案例。

> **我自己的臨時腳本量到 631／9,157／463，模組量到 658／2,207／385——兩個都真的，但不是同一件事。**
> 差別在「出貨欄位」怎麼取得：臨時腳本用 `analyse_items`，模組用 `segment_best`。
> **模組現在是權威**（它有負向對照、有測試），文件跟它。一個指標有兩個數字，就是有兩個可以
> 不一致的地方——所以我沒有保留舊數字當「另一個說法」，只留這一段說明為什麼它們不同。

### 這個標準已經抓到一個真缺陷（不是假說）

我抽 20 筆「兩引擎都看到」人工核對，**20/20 都是真的**。出貨佇列裡就是這個樣子：

```
1002_物理治療師_骨科疾病物理治療學 q10
  shipped A : 薦髂關節疼痛…導致步態          ← 「異常」被丟掉
  stem      : 下列有關薦髂關節…何者正確？異常 iliac spine）均較右邊…   ← 掉下去的字跑到題幹
  quality_status: pass   disputes: None
```

紙本（兩個引擎都這樣讀）：

```
A.薦髂關節疼痛可能對臀中肌（gluteus medius）造成反射性抑制…導致步態
異常                          ← 續行，出貨時丟掉
B.當發現左邊的前上腸骨棘（anterior superior
```

同一個症狀在 **72 / 160 卷**（抽樣）出現，而且**沒有任何一題被標記**：
`quality_status=pass`、`disputes=None`、`dispute_severity=None`。

**這是目前為止找到最大的單一未標記缺陷類別**——比 `option-shape`（437）大，而且
`option-shape` 至少會被標記。

### 機制（在 `repair.py` 裡讀出來的，不是猜的）

`segment_questions` 有一個「折行測試」，但**只給題幹的錨點用**
（`_looks_like_a_wrap`，`repair.py:776`）：一條不開頭的短行，若前一句沒結束，就算前題的續行。
選項**沒有**對應的測試：`repair.py:1006` 之後，一旦選項已開始，
任何不是選項記號的行都落到 `current["stem"].append(line)`。
所以**選項的續行被交給題幹，而選項被留在折行處**。

## 6.2 這個缺陷已修（2026-09-21），並且量到下降

### 做了什麼

三段的第一段（**偵測**）就是修復本身，而且不需要第二、三段——因為把續行交回選項，
缺陷就在**出貨之前**消失，不是先標記再讓人審。我加了 `_continues_an_option`（`repair.py`），
與題幹的 `_looks_like_a_wrap` 同構：一條不開頭、不是頁尾／節標題／metadata／純標點、
有文字的行，在選項已開始之後算**最後一個選項**的續行（`join_lines`，與題幹用同一條接字規則）。

**刻意不是**長度測試（題幹那條用 `<= 14`，因為題幹的折行是短語）。選項的續行常常很長：
兩個引擎都看到的案例裡有一個 24 字的尾巴。加上長度上限會擋掉大多数真的損失。

### 量到的下降（同一個偵測器、同一批 160 卷、`seed 7`）

| | 未修 | 已修 |
|---|---|---|
| 引擎 A 自己看到 | **658** | **0** |
| 引擎 B 自己看到 | 2,207 | 1,858 |
| **兩個引擎都看到（門檻）** | **344** | **1** |
| 受影響題數 | 235 | 1 |
| 受影響卷數 | 60 | 1 |

`loss_a` 從 658 到 0 是**同一個引擎內部的兩種讀法**（切題器 `segment_best` vs 逐欄讀取器
`continuation_losses`）從不一致變一致——這是修復真的發生的第二個證據，不是只換了一個數字。

### 這條數字自己的負向對照

修復是在**同一支腳本**上加了三件事，每一件都在舊碼上量過：

1. **舊碼上報 344**（`git stash` 掉 `repair.py` 再跑同一支腳本）→ 新碼報 1。
2. **新測試在舊碼上紅**：`test_a_wrapped_option_keeps_its_continuation` 在未修版本
   `assert` 失敗（`步態異常` vs `步態`）。
3. **文字守恆**：160 卷的 stem+options 總字元數 735,459 → 735,457（差 2 是接字空白），
   文字是從題幹**搬**到選項，不是被丢掉。

### 量測工具本身錯了三次（記在這裡，因為它是最容易再犯的錯）

我寫的 `verify_option_continuation.py` 前兩版都給出假答案：

| 版本 | 測什麼 | 為何是假的 |
|---|---|---|
| v1（原版） | 對**每個引擎自己的讀數**都算 loss | 出貨欄位由 A 產生，再用 A 的讀數驗 A 的輸出——**同義反覆**，修復後回報 0 |
| v2 | 「B 報的漏字有沒有出現在 A 的讀數裡」 | A 的讀數有整張紙的文字，幾乎永遠為真 → 已修版本報 **1,784** |
| v3（現行） | A 把**欄位+漏字印成連續一段** | 兩個引擎獨立同意紙本在那裡續下去，而出貨欄位停住 |

v2 的 1,784 出現在**已修**版本上，這是抓到它的方式。**一個壞掉的量測工具看起來像一個壞掉的產品。**

### 剩下的 1 筆不是缺陷，是升級

`1042_醫師(二)_醫學(六)` q43：兩個引擎讀到**同一段文字**，但對**哪個選項擁有它**不一致
（A 把 `，骨盆腔及主動脈旁淋巴結摘` 算在 B 的續行，B 的讀數把它放在下一列而歸給 C）。
這正是標準應該升級的那一類——不是「被丢掉」，是「歸屬有爭議」。**留 1 筆是正確的，歸零才可疑。**

### 三段順序的更正

第 6.1 節說「先偵測、再標記、再驗收」。實際做下去發現**當修復等於偵測時，不需要新增 dispute kind**：
選項不再被截斷，就沒有東西可以標記。`option-continuation-loss` 這個 kind **刻意沒有加**——
加一個永遠不會出現的 kind 就是死程式碼（`engine-disagreement` 就是前例）。
驗收數字仍然要報（就是本節的 344 → 1），因為它是**可下降的數字**。

### 標準的三段（照這個順序做，不要跳）

| 段 | 做什麼 | 為何是這個順序 |
|---|---|---|
| **1. 偵測** | 在 `repair.py` 為**選項本體**加上與題幹同構的折行測試 | **已做（6.2）**：修好後續行歸位，不需要再標記 |
| **2. 標記** | 新的 dispute kind，例如 `option-continuation-loss` | **刻意沒做**：偵測即修復時，這個 kind 永遠不會出現 |
| **3. 驗收** | 報「兩引擎都看到」的欄位數 | **已可下降**：344 → 1（6.2），這是給人看的數字 |

**順序不能顛倒**：先寫 S6 的數字，只會量到「還沒有人處理」；先修 `repair.py`，才有一個能下降的數字。

### 這條標準自己的負向對照

一個新規則必須在舊碼上失敗，否則它什麼都沒證明。所以：

- **新測試必須在未修改的 `repair.py` 上失敗**（目前 q7 的選項 A 會被截斷）。
- **同時要在兩個引擎上都失敗**：只在 A 上成立的規則就是 overfitted（`ENGINE_STRATEGY.md` §4.3）。
- **不能把「B 領讀比較差」算成缺陷**：那是儀器性質，量測時必須用「同一讀數內的選項文字範圍」，
  不是「B 領讀的切法」。我在這一輪犯了這個錯四次（14.21% → 9.06% → 2.34% → 2.06%），
  **每一次都是量測工具重製了它要量的缺陷。**

### 與模型的分工（這條標準不取消那一條，它給模型一個可判的問題）

「兩引擎都看到」是**確定性的**，所以它先跑、免費、可重現。它把清單縮到 265 題／160 卷。
**模型不需要看全部 78,690 題，只需要看兩個儀器互相不同意的那些。**

而且這個問題是**封閉的、有可核對答案的**——正是本專案已經證明模型可靠的那一種工作
（不是「找缺陷」，那量過只有 12–16% 精確率）。我實測了（`scripts/ask_option_continuation.py`）：

| 引擎 | 判為「是」（同意兩引擎） | 還原出的漏字 | 時間 |
|---|---|---|---|
| **`qwen3.8-flash-next`**（DGX 8888） | **10 / 10** | 與 ground truth **逐字相同**，只有拉丁詞旁多餘空白 | 1.1–2.9 s/題 |
| **`ornith-1.5-mtplx-35b`**（18120） | **10 / 10** | 同上 | 2.4–11.2 s/題 |

例：ground truth `lidocaine＞bupivacaine`，兩個引擎都答 `lidocaine＞bupivacaine`；
ground truth `骨頭`，都答 `骨頭`；`maximalhyperemia` → 都答 `maximal hyperemia`（只差空白）。

**注意這與「AI 初篩」的差別**：那一輪問模型「這題有沒有問題」（開放），得到 12–16%。
這一輪問「這句話是不是還沒結束」（封閉、有標準答案），得到 10/10。
**同一批模型，不同的問法。** 這是目前為止模型在本專案唯一可接進流程的位置。

---

## 7. 如何重測（這份文件會過期，指令不會）

```sh
cd tw-national-exam-catalog/qbr
```

### 佇列現況

```sh
Q=qbr/data/review-queues/live/review-ui          # 現行 live 佇列
python3 - <<'PY'
import json, collections
Q="qbr/data/review-queues/live/review-ui"
n=0; opt=collections.Counter(); d=collections.Counter(); sev=collections.Counter()
g=collections.Counter(); img=ai=0
for line in open(Q+"/candidates.jsonl", encoding="utf-8"):
    r=json.loads(line); n+=1
    opt[len(r.get("options") or [])]+=1
    if r.get("image_refs"): img+=1
    if r.get("ai_review"): ai+=1
    if r.get("group_ref"): g[r.get("group_size")]+=1
    sev[r.get("dispute_severity")]+=1
    for x in (r.get("disputes") or []): d[x.get("kind")]+=1
print("題",n,"| 選項數",dict(opt),"| 有圖",img,"| ai_review",ai)
print("disputes",dict(d)); print("severity",dict(sev)); print("題組",dict(g))
PY
```

### 驗收標準（第 6.1 節）：出貨欄位有沒有被第二個引擎找到

```sh
.venv/bin/python scripts/verify_option_continuation.py 160 7
```

**已修（2026-09-21）**，預期（160 卷／12,840 題）：`loss seen by A` **0**、`loss seen by B` 1,858、
**`seen by BOTH` 1**、`distinct questions` 1、`distinct papers` 1。

修復前的同一個量測：`loss_a` **658**、`loss_b` 2,207、**`both` 344**、題 235、卷 60。
**這條數字往下走就是驗收本身。** 負向對照（`git stash push qbr/src/qbr/repair.py`）必須回到 344。

剩下那 1 筆不是缺陷，是升級（兩個引擎對「哪個選項擁有那段文字」不一致）——
**留 1 筆是正確的，歸零才可疑**。詳見 [`qbr/reports/option_continuation_fix.md`](../../../qbr/reports/option_continuation_fix.md)。

### 模型回答「這句話還沒結束嗎」（第 6.1 節的封閉問題）

```sh
# 候選引擎（2026-09-21 實測皆在線）
#   flash-next  http://192.168.10.90:8888/v1   qwen3.8-flash-next        Bearer mtplx
#   ornith 35b  http://127.0.0.1:18120/v1      ornith-1.5-mtplx-35b      Bearer mtplx
#   27B-Splash  http://127.0.0.1:8088/v1       incoai/Qwen3.8-27B-Splash （他專案使用中）
#               權重路徑是 /Users/tim/models/qwen38-27b/splash（不是 models/qwen38/splash）
#   ornith 若沒開：models/ornith/bin/ornith start 35b

.venv/bin/python scripts/ask_option_continuation.py --engine flash --papers 8 --limit 10
.venv/bin/python scripts/ask_option_continuation.py --engine ornith --papers 8 --limit 10
```

### 跨引擎一致性（本文件第 6 節的量測）

```sh
.venv/bin/python - <<'PY'
import sys, json, glob, os, random, collections
sys.path.insert(0, "src")
from qbr import extract, repair
ROOT = "/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog"
dirs = sorted(d for d in glob.glob("/tmp/qbr-expand/*/*") if os.path.exists(d + "/package/manifest.json"))
random.seed(42); res = collections.Counter()
for d in random.sample(dirs, 40):
    rel = json.load(open(d + "/package/manifest.json", encoding="utf-8"))["provenance"]["source_sheets"]["question"]["relative"]
    pdf = os.path.join(ROOT, rel)
    if not os.path.exists(pdf): continue
    ta = repair.text_from_rows(repair.mask_chrome(extract.extract_lines_a(pdf))[0])
    tb = repair.text_from_rows(repair.mask_chrome(extract.extract_lines_b(pdf))[0])
    res[extract.compare(ta, tb)["classification"]] += 1
print(dict(res))     # 預期：TEXT_DISAGREEMENT 31、STRUCTURAL_DISAGREEMENT 9、EXACT 0
PY
```

> **`sorted()` 不能省。** `glob` 的順序由檔案系統決定，不加 `sorted()` 時同一份種子
> 抽到不同的 40 卷，結果會在 31/9 與 32/8 之間跳動。我第一次就是這樣得到兩個不同的數字。
> **一個會跳動的數字不是量測。**

### 圖片（新佇列應為 0——若不再是 0，這份文件已過期）

```sh
ls qbr/data/review-queues/live/review-ui/crops 2>/dev/null || echo "沒有 crops/：圖未綁定"
grep -c '"image_refs": \[{' qbr/data/review-queues/live/review-ui/candidates.jsonl || true
```

### 被擋的卷

```sh
python3 - <<'PY'
import json, glob
for f in glob.glob("/tmp/qbr-expand/*/package_report.jsonl"):
    for l in open(f, encoding="utf-8"):
        r = json.loads(l)
        if r.get("status") == "blocked":
            print(r["name"][:44], "|", r.get("stdout_tail"))
PY
```

### 測試

```sh
.venv/bin/python -m pytest tests/ -q      # 現況：237 passed, 0 skipped
```

> 17 個測試靠 `/tmp/qbr-golden-001`、`/tmp/qbr-golden-002` 的真實產物。這兩個目錄被清空後，
> `skipif` 會把「讀不到證據」變成「不必驗」（總數 237 不變，**靜默**）。
> 重建方式見 `build-exam-question-bank` skill；**測試變少要當成缺陷，不是正常。**

### 服務

```sh
# 8774 = 新管線（jsonl 後端）。埠是契約，不要改。
python3 scripts/serve_question_review_ui.py \
  --candidate-jsonl qbr/data/review-queues/live/review-ui/candidates.jsonl \
  --issue-csv      qbr/data/review-queues/live/review-ui/issues.csv \
  --review-log     qbr/data/review-queues/live/review-ui/question_review_events.jsonl \
  --review-backend jsonl --host 127.0.0.1 --port 8774
```

`v2` 有「**爭議**」晶片（客戶端過濾，`data-view="disputed"`），數字就在晶片上——
這是「只看有問題的」入口，不需要模型。

---

## 8. 讀這份文件的三個陷阱

1. **「S2 通過」不等於「兩個引擎一致」。** 它等於分段連續性。看第 6 節。
2. **「有圖」要看 `image_refs`，不要看 `has_visual_asset`。** 後者寫死 `False`，永遠是假的。
3. **`group_position` 從 0 開始。** `0` 是組頭，不是缺號；用 `range(1, …)` 檢查會得到
   908 組全部「不連續」的假缺陷（我犯過）。
