# qbr — 考題建立流程（Agent Instructions）

這一包是「官方 PDF → 平台可驗封裝」的建置管線。它從 `pi_test/question_bank_rebuild/` 併入主線，
路徑不動、測試在合併後的位置全綠。

**先讀 [`README.md`](README.md)，再讀 [`PROPOSED_WORKFLOW.md`](PROPOSED_WORKFLOW.md)
（後者是方案本身，含每個主張背後的量測），然後 [`../../docs/ARCHITECTURE_CHARTER.md`](../../docs/ARCHITECTURE_CHARTER.md)
（跨專案最高規範）。** 三者衝突時以 charter 為準。

工作程序見 [`docs/skills/build-exam-question-bank/SKILL.md`](../docs/skills/build-exam-question-bank/SKILL.md)；
**修抽取／切題的缺陷**見 [`docs/skills/repair-qbr-extraction/SKILL.md`](../docs/skills/repair-qbr-extraction/SKILL.md)
（量測紀律、負對照、「壞掉的儀器看起來和壞掉的產品一樣」、已找到的缺陷目錄）。

**`docs/` 有 48 份文件，先看索引 [`docs/README.md`](../docs/README.md)。**
那個目錄橫跨兩代管線：類 2 的文件描述的是 **`qbr` 已經量過並取代的 MinerU + 全文件 VLM 路徑**，
保留作證據，不是規範。

---

## 這一包的責任

**做什麼**：把一卷官方試題卷讀成結構化題目、對答案、切圖片、標爭議，產出一個不可變的
review queue 與 package；人類審核後才算完成。

**不做什麼**（越界就是錯）：

- 不寫 production runtime database，不寫 catalog 正式 PostgreSQL。
- 不自動接受或阻擋題目。AI 一律 advisory（GOV-05），不得冒充人類審核者、
  不得寫入人類 review event。
- 不下載。語料由 catalog registry 管理，本包只**讀**。
- 不在學生流量路徑上執行任何東西。

---

## 核心信念（違反它們的程式碼是缺陷，不是 style）

1. **規則必須建立在紙張的性質上。** 不是建立在解析結果、不是建立在關鍵字、
   不是建立在「上一次跑出來的值」。一條以解析輸出為前提的規則，會讓解析與驗證互相證明
   對方正確 —— 那是循環，不是驗證。
2. **規則必須同時在兩個引擎上成立。** 單一引擎的共識不是共識。
   兩個引擎一致的看法才是量測。
3. **驗收標準是兩個引擎一致，不是測試全綠。** 一個綠的測試可能只證明了一個錯的規則。
4. **看不到的證據不是證據。** 量測前先確認東西真的被掃到、真的被渲染出來。
5. **不一致要分類，不是全部送 AI。** 先問「這是哪一種不一致」，再決定怎麼處理。
6. **不要用門檻代替結構。** 門檻用來**選**，不用來**切**；邊界是結構，不是距離。
7. **不要留下未經量測驗證的預測規則。** 自由參數要掃描，不要推導。
8. **修規則之前先量「另一條規則會丟掉什麼」。** 每個規則都要有**負對照**。
9. **量測與解讀要分開。** 先報數字，再報意思；不要把意思寫成數字。
10. **真的能重複的才寫成腳本。** 其餘轉成提示詞/skill —— 見下節。

---

## 提示詞 vs 腳本：這一包的分界線

這是最容易做錯的一件事，也是過去做錯的地方。

**腳本只保留「紙張的性質」**：掃描幾頁、有幾個字、字型家族有哪些、墨跡在哪、
區塊的幾何、頁面的分欄。這些是**可重複的**：同一張紙量兩次得到同一個答案。

**凡是「讀出文字的意義」都是提示詞。** 哪一行是題幹、哪個是選項、這張圖是什麼、
這題的答案是哪個 —— 這些沒有單一正確演算法，只有「讀對了沒有」。
過去把它們一條條寫成規則，於是走上「規則 → 腳本 → 新問題 → 新規則」的跑步機：
每一條新規則都在修上一條規則造成的傷害。

遇到不確定時，**先決定，不要用「再疊一層」回答**。
提示詞裡任何模型不需要的欄位都是風險來源。

---

## 目錄

```text
src/qbr/          確定性核心 + 提示詞層呼叫（無模型時可全部單元測試）
  triage.py          PDF 的便宜分類（頁數、字數、字型、損壞字元、圖物件）
  extract.py         兩個獨立抽取引擎 + 逐項比對；上下標轉換
  cjk.py             碼位普查、simplified-only 偵測（**永不轉換**）
  repair.py          Level-0 規則、幾何 chrome mask、anchor 樣式偵測/分段
  canon.py           宣告的 canonical form、答案表與其合併、比對與判定
  reflow.py          折行重排、區塊分組、選項邊界
  groups.py          題組（共用題幹的題目群）偵測
  subitems.py        子題標記（①②③、A. B.）與共用圖例
  answer_sheets.py   三卷（Q/ANS/MOD）定位與更正答案表讀取
  corrections.py     更正答案 = 重新發表的表，不是一句註記
  disputes.py        爭議：**對紙張的量測**，不是模型意見
  vision.py          圖片題描述（本地 LLM）
  verify_crops.py    裁切完整性驗證（**在框外**量墨跡，不信任解析）
  audit_crops.py     裁切稽核
  package.py         package 建構 + registry key 的**唯一**權威
  review_queue.py    review queue 與候選列表
  generation.py      生成 router 雛形（寫題候選，advisory）
  manifests.py       manifest 讀取（相對路徑解析，不依賴 cwd）
scripts/          33 支，全部 `--help` 可查
  golden_path.py     **唯一權威的端到端路徑**：S0..S6 七階段、單卷
  batch_run.py       整個類科：讀 + 抽
  batch_package.py   整個類科：封裝
  build_review_queue.py  合併多個 run 成一個佇列（**自動承接審核紀錄**）
  crop_run_figures.py    切圖（**必須在封裝之後跑**，見下）
  measure_*.py       量測（corpus / skeleton / speed / balance）
  test_arbitration.py    仲裁測試工具（含負對照）
  test_vision.py / audit_crops.py / verify_crops.py  圖片驗證
tests/            pytest suite；`tests/golden/` 是固定的整卷 golden
docs/ reports/    決策與量測報告（reports 是證據，不是規範）
prompts/          系統提示詞，版本化（`structure_question.v1.*.md`）
```

---

## 流程（順序是規範，不是建議）

```sh
cd tw-national-exam-catalog/qbr

# 單卷，走完整七階段；這是驗收用的路徑
.venv/bin/python scripts/golden_path.py run \
    --registry-key moex:115090:308:0504:1 \
    --year 115 --ordinal 2 --category 醫事檢驗師 --subject 生物化學與臨床生化學 \
    --asset-root "../國考題資料夾" --out /tmp/run1 \
    --package-version tw-national-exam-medtech-v0.0.1

# 整個類科
.venv/bin/python scripts/batch_run.py     --category 醫事檢驗師 --work /tmp/work
.venv/bin/python scripts/batch_package.py --category 醫事檢驗師 --work /tmp/work

# 切圖**在封裝之後**（它會綁定選項圖，爭議必須重算）
.venv/bin/python scripts/crop_run_figures.py --work /tmp/work

# 合併成一個佇列；審核紀錄會自動承接，不需旗標
.venv/bin/python scripts/build_review_queue.py --work /tmp/work --out /tmp/live

# 驗收
.venv/bin/python -m pytest tests/ -q
```

### 順序上的三個陷阱（都踩過）

1. **切圖必須在封裝之後。** `disputes_for_paper()` 要看到選項圖的綁定才算得出
   `empty-option`。在封裝時算一次（讓未切圖的佇列也帶爭議），切圖後**再算一次**
   （權威版）。只算一次會讓 274 個空選項裡真正的 23 個缺陷被埋在 251 個誤報裡。
2. **重建進「正在服務」的目錄時，列表與候選檔會有短暫不一致。** 重建期間應暫停服務。
   （已知未解，見 `reports/review_record_safety.md`。）
3. **審核紀錄不可以被重建弄丟。** 見下節。

---

## 審核紀錄：不得被重建弄丟

人類的決定存在 `question_review_events.jsonl`，**append-only**。重建時：

- 承接是**自動的**（`sibling_queues_with_reviews()` 自動找同層有紀錄的佇列），
  **不需要旗標**，因為「忘了加旗標」正是它出事的方式。
- 去重以**整筆紀錄**為單位，不是 `candidate_key`。同一題可以合法地有多筆
  （`block` → `accept`），以題目為鍵會靜默丟掉後來的決定。
- **孤兒要保留並計數，不可以丟。**
- 若重建會承接 0 筆而附近有紀錄存在，**拒絕啟動（exit 2）**，且檢查在寫入之前。

細節與三個未解項目：`reports/review_record_safety.md`。

### 紀錄的「家」在常駐機（2026-09-21 起）

審題介面現在跑在**常開的機器**上（`192.168.10.70` = `TimsMac.lan`，`http://192.168.10.70:8765/v2`），
因為筆電可以關掉帶走，而審核要能隨時進行。所以：

- **常駐機上的 `~/qbr-review/queue/review-ui/question_review_events.jsonl` 是唯一的家。**
- **筆電不跑第二份審題服務。** 兩個 live writer 就是兩個審核儲存（`reports/two_review_stores.md`
  描述的缺陷），只是換了一種形式再發生一次。
- 筆電把決定**拉回來**（單向）：`scripts/pull_station_reviews.sh`，覆蓋前先備份，
  連不上常駐機時拒絕動作。反方向刻意不做。
- 部署拓撲與維運：`docs/skills/deploy-qbr-review/SKILL.md`。

> 這不是新的儲存，是**搬家**：一個家，換一台機器。

---

## 圖片：本地模型，不是定位擷圖

- 「這張圖在哪」用幾何與墨跡量測；「這張圖是什麼」用**本地 LLM**。
- **不做定位擷圖** —— 那屬於圖片題，交給模型。
- **裁切框是紙本的性質，不是解析結果的性質。** 讓解析決定測試看得到什麼，
  等於讓測試**依建構而正確**。
- **框外墨跡不可用來判斷完整性**（框外的東西不該在框裡，但那不代表框是對的）。
- **裁切標準要一致，並包含題號**。
- 每個圖片題都應該有模型確認過（使用者要求）。
- 模型只用當次任務核准的本機版本；endpoint、budget 與 evidence 必須寫入該批 run。

引擎與埠（**埠是參數，文件不該寫死**；環境變數 `QBR_MODEL_BASE_URL` /
`QBR_MODEL_NAME` / `QBR_MODEL_API_KEY`）：

| 引擎 | 位置 | 量到的中位延遲 |
|---|---|---|
| `ornith-1.5-mtplx-35b` | `127.0.0.1:18120`（預設） | 由當次任務量測 |
| 本機視覺模型 | `127.0.0.1:8082` 或當次指定的 localhost port | 由當次任務量測 |

本機引擎的舊仲裁數字不授權模型選擇。每個新任務都要在本機重新量測，並把
模型、port、budget 與結果寫入該批 evidence。
（含「這份報告不能證明什麼」）。要真的排名，需要更難的題類
（失落字形轉錄、真正的 23 個空選項缺陷、option-shape）。

---

## 兩個引擎的三種思考開關拼法

**錯的拼法會靜默成功**：HTTP 200、無作用。所以必須**雙向探測**，不可硬寫。
一個拼法只能靠「移除確實存在的推理」來證明有效。
`empty answer 不是模型的證據，是預算的證據` —— 本機模型與本地模型都需要
足夠的時間與合理的 token 寬度。

---

## 修改這一包時

- **改規則前**：先量「另一條規則會丟掉什麼」。沒有負對照的量測不算量測。
- **改完之後**：重跑 golden（`tests/test_merge_golden.py` 會逐欄比對整卷），
  不是只跑你改的那支測試。
- **新增資產**：相對路徑**只**。不得出現 `/Users/`、`/Volumes/`、`file://`、磁碟機代號。
  `tests/test_manifests.py` 與 `test_merge_golden.py` 各有一條測試守住。
- **新增 `registry_key` 的拼法**：一律經 `package.paper_key()` 正規化。
  角色後綴（`:question` / `:answer`）是**卷的性質**，不是題目身份的一部分。
  重複後綴曾是 80/80 題的缺陷，而那個鍵是審核紀錄的主鍵。
- **不要動 `tests/golden/`**，除非確定紙張內容變了。它是「程式碼還讀得懂這張紙」的證明。
- **`data/` 與 `.venv/` 不進版控**；`tests/golden/` 進版控。

## 兩個未來的方向（不要混淆）

展開成一份獨立文件：[`../docs/PIPELINE_OPTIMIZATION_DIRECTIONS.md`](../docs/PIPELINE_OPTIMIZATION_DIRECTIONS.md)
（含兩者共用的驗收閘門，與為什麼合併記錄最危險）。

1. **橫向：繼續拆不同類科，找通用性的盲點。** 目標是讓**通用腳本**更通用。
   做法是先不改程式跑，再看它壞在哪裡。
2. **縱向：特定科目的原則最佳化。** 這是**針對性**最佳化，必須與上者分開記錄，
   並且**避免把通用腳本過擬合**到某一個科目。
   條件要是**紙張的性質**，不是科目名稱；`if category == ...` 就是過擬合的定義。

**第 3 關（骨架通用性）是方向二的安全網**：一個「針對某科」的修改若讓骨架通用性
從 3,516/3,516 掉下來，它就是過擬合，不管那個科目變得多好。

驗收要用「不是自己產生的資料」（外部標準）。外部標準的**漏掉是錯，沒收錄不是錯**。

---

## 現況

- 測試：**257 passed**（自帶 `.venv`，依賴清單 `../requirements/qbr.txt`）。
- 已展開：30 個類科／**3,516 卷**；合併佇列已建（984 卷／78,690 題）。
- 骨架通用性：**3,516/3,516**（兩個引擎的骨架一致）。
  **這不是「文字相等」**——文字相等達不到，也不是驗收標準。
  可執行的驗收標準是「每個出貨欄位都要被第二個引擎在自己的讀數裡找到」，見
  [`docs/skills/qbr-pipeline-status/SKILL.md`](../docs/skills/qbr-pipeline-status/SKILL.md) 第 6.1 節。
- 已修：`醫師(二)` 六份 `count-mismatch` 卷（兩個根因，現皆 80/80）、選項續行被截斷、
  疊字與 `NN 年…` 題幹被當表頭。逐項見 `reports/`。
- Golden：`tests/golden/golden_1152_medtech_biochem_candidates.jsonl`（80 題）。
- 合併報告：`reports/merge_into_catalog.md`。


## 題目修正迴圈（`scripts/repair_agent.py`）與調試腳本（`scripts/run_repair_loop.sh`）

一份腳本、兩張表（`*.jsonl` append-only；`_split_block` 前缀 `PAPER[0-9]+` 與 `_id` 尾綴 `PAPER[0-9]+`）。
（本機 3.14 核對：`json.load`＝`json.loads`；`str` 的 `isalnum` 對 `Ⅰ`(U+2160) True、`'⁻'`(U+207B) False、`'1'` True；
`re` 的 `\d` 含 `Ⅰ`？實測 False：`Ⅰ` 非 `\d`。故 `isalnum` 判非 `re`。）

| 步 | 打什麼 | 回什麼 |
|---|---|---|
| 1 | `--every 1 --interval 1800 --lane splash` | `run_repair_loop.sh` 之 0~9 段；`{id, ...}` |
| 2 | 兩引擎並行（disjoint key set） | 每個 key 一份 record；`@key` 為時序 |
| 3 | 冪等（同 input sha 同 output） | `run_repair_loop.sh` 5 段核對；`_id` 後寫覆蓋 |
| 4 | 未解（4 項大 table） | 於 `experience.json.open_cases`（含 `@key`） |
| 5 | `--lane qwen3.8-flash-next` 並發（`@key` 尾 2 位） | 85,289 |

**判讀**：`repair_agent.py`（`medium`→`low` 之檔位、非整數）與 `run_repair_loop.sh` 為同一迴圈之两张表。
（實測此 3 個並發端點為本機 4 位埠號、且本機 `python3` 的 `json.load` 可用。）
（實測 3.14：`_id` 為 4 位）

**否決**：`reasoning_effort` 不収整數（HTTP 400）。
**限**：`max_tokens` 頂層；`medium` 4000/`low` 3200。
