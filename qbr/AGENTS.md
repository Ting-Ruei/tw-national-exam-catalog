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


## 修理代理：常駐迴圈、錯題討論區、以及「哪一種修復才可以自動套用」

一條完整的路是四段，每一段的作者不同，混在一起就會壞：

```
偵測（腳本，紙張的性質）→ 讀紙本（模型，advisory）→ 套用（機械，有錨）→ 人的決定
`disputes.py`                    `confirm_dispute.py`      `apply_dispute_repairs.py`   錯題討論區
```

### 常駐迴圈 `scripts/repair_daemon.sh`（G2）

每 `INTERVAL`（預設 1800s）對「人已 block、且帶著一個**可以看紙本確認的爭議**」的那批題目，
截圖問地端模型，寫下機械差異（advisory finding）。`WINDOW=5` 是一輪的題數。

- **工作清單不是「沒有任何偵測器解釋的 block」。** 三條新爭議規則上線後，304 題全部都有偵測器，
  於是每一輪都選不到題（實測每輪都印「本批沒有待問的 candidate_key / 累計 0 題」）。一個
  30 分鐘迴圈一直跑卻什麼都不做，比沒有迴圈更糟——它看起來像在做事。誠實的清單是「帶著一個
  可以看紙本確認的爭議」，那是一個**不會縮到零**的集合。
- `--skip-confirmed` 以 `reading_sha256` 判斷「這一段讀法已經問過紙本了」，所以同一題不會每輪
  重拍重問；文字真的被修好而改變時，它會自動重新排進來。
- `--principles` 讀審題者在討論區寫下的基本原則（預設就是 queue 自己那一份）。
- `--escalate`：紙本讀不到、或紙本與抽取一致但人仍然阻擋時，寫一筆 `ask` 到
  `question_repair_questions.jsonl`，**不是**再問同一個模型一次，也不是替人做決定。
- **它只寫 advisory finding，不寫任何 review event、不改任何題目文字，所以停在 G2。**
  它不會自動 accept/block。

### 錯題討論區（`review_ui/v2/04-area-discuss.js`）

- 過濾條件是 `reviewStatus=discuss`，桶位在伺服器定義一次：
  `DISCUSS_BUCKETS = block / repair_pending / accepted_reaudit / reset_review`。
  **不是整條佇列**——先前那一版把 79,090 列全拉進來，卡住的題就找不到了。
- **基本原則只在提示詞裡消費**：一句人寫的話，原封不動貼進去（`ai_findings.principles_note`），
  不是編譯成規則。這是這個專案量到的教訓（規則 → 腳本 → 新問題 → 更多規則的跑步機）。
  `principles` 與 `learned` 是分開的提示詞區塊（限制 vs 觀察），而「沒寫」與「寫了空字串」不同
  （`prompt_version` 以渲染後文字計 hash）。
- 兩條流（`question_review_principles.jsonl`、`question_repair_questions.jsonl`）是 append-only，
  摺疊規則在 `qbr/src/qbr/discuss.py` 一處，伺服器 import 它而不是自己再寫一份。

### 哪一種修復可以自動套用：`apply_dispute_repairs.py`

兩條進來的路，可信度不同，規則必須不同：

1. **dispute 自己帶著目標字元**（`substituted-ideograph`：`⻑` → `長`）。目標在爭議裡，
   所以這是代換，不是決定。`APPLICABLE = ("substituted-ideograph",)`。
2. **紙本判讀**（模型轉錄的截圖，`--page-read`）。這是**意見的來源**，而這個專案量過模型會在
   轉錄時重寫公式、截斷、甚至編造圖片說明。`anchored_page_changes` 的每一條都是一個量到的拒絕：
   * **每個 run 等長**——改變字元數的讀法不是代換，是把後面每個位置都移了
     （實測 `115090 q053` 插入 93 字並清空四個選項）。
   * **只能 replace**——`insert`/`delete` 是同一種失敗的另一種寫法（實測 `113020 q076` 附了一張表，
     `105020 q045` 附了整張有編造箭頭標籤的圖）。
   * **每個被改的位置都必須是偵測器已標記的，且每個標記位置都要改到**——改到沒人懷疑的字元
     就是模型在改寫散文；漏掉一個被懷疑的，就是把題目重新打開、缺陷還在裡面。
     實測：`flattened-offset` 類（`C=5e-0.4t` → `C=5e⁻⁰·⁴ᵗ`）**沒有任何**被標記的位置，
     那靠讀法本身修（`extract._body_centre`），不是靠代換。

套用寫一筆 `reset_review`（帶著 `correction`），題目以「修復後待複核」回到討論區。
**不是 `correct`**：機器不宣告任何人的判定。

三個踩過的坑（每個都有測試與負對照）：

- **`candidates.jsonl` 的文字刻意不改寫**，所以同一筆 `⻑ → 長` 每輪都會再被找到。
  沒有簽章比對就會把自己修過的再修一次（實測日誌裡已有 192 筆）。以「編輯集合」比對，
  不是記一個旗標——文字真的移動時簽章會不同，那就該再修一次。
- **人的決定可能排在修復之後**（常駐機時鐘是 UTC、筆電是 UTC+8；實測有人 `accept` 蓋在
  `11:25:44` 的修復之後）。投影是最後一筆贏，於是「上一筆修復」的記憶被洗掉，下一輪會
  再套一次同樣的修復、把剛被接受的題目重新打開。所以簽章來自**掃描最後一筆 `qbr_dispute_apply`**，
  不是最後一筆事件（`last_repair_signature`）。
- **修好的文字不能再顯示那個剛被修掉的爭議**：`disputes` 是從**當時的文字**量的，而文字不會被
  修復改寫。實測 304 題已修復的題目裡有 **204 題**的爭議是舊的，討論區於是顯示一個已經不存在
  的字元的爭議，就寫在修好文字的正上方。修法是在伺服器疊 `correction` 時用**同一個**
  `review_queue.disputes_for_paper` 重量——只改變**什麼時候**跑，沒有第二套規則。

### 量到的現況（2026-09-23）

- 卡住 269 題；其中 174 題**沒有任何爭議**（人的判斷阻擋，模型給不了答案），
  其餘為 `lost-glyph 37`、`flattened-offset 35`、`substituted-script 8`、`empty-option 7`、
  `option-shape 5`、`table-flattened 2`、`punctuation-only-option 1`。
- 已確認的紙本判讀 49 筆，其中 **18 筆**滿足錨定規則（已全數套用），31 筆被拒。
- **自動修復不可能吃掉全部**：`lost-glyph`／`substituted-script`／`flattened-offset` 的其餘部分
  需要重讀紙本或人。這是這條線誠實的邊界，不是待辦清單。

### 思考開關的拼法

**錯的拼法會靜默成功**：HTTP 200、無作用。一個拼法只能靠「移除確實存在的推理」來證明有效。
`reasoning_effort` **不收整數**（HTTP 400 `invalid reasoning_effort`）。
實測（Splash `8088`）：`high` 會吃滿預算（`content_len=0`）；`medium`（`max_tokens` 4000）→
`completion_tokens=1295`、其中 `reasoning_tokens=1102`，可解析；`low`（3200）→ `920/834`。
MTPLX 用 `chat_template_kwargs={"enable_thinking": True}`。
