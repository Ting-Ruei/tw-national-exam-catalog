# 國考題四路初審、LLM-Share 抓漏與人工重審規劃書

狀態：第六版；舊 verbose v2 與原本的本地模型分支均已暫停。主線改為四路 compact 初審，
先以本地 `qwen3.6:35b-mlx` 驗證延遲、vision、輸出完整性與 Review UI 串接；通過後才恢復
LLM-Share 模型 pilot。全年度對帳與正式全量掃描尚未開始。

日期：2026-07-29

## 一、已確認的任務決策

### 1.1 本次正式實作範圍

第一優先：

- `醫事檢驗師`：民國 100 至 115 年的全部有效題目候選。
- 包含已由人工接受、修正、審核及目前未審的題目。
- 目的不是推翻既有人工審核，而是從已審題目中找出可能被漏掉的錯別字、簡體字、
  科學符號及語句斷裂。

第二優先：

- 藥學系全部年度。
- SQL 類科包含歷史單階段 `藥師`，以及分階段 `藥師(一)`、`藥師(二)`。
- 民國 100 至 115 年全部有效題目候選均納入。

本次明確不實作：

- `醫事放射師`
- `醫師`
- `物理治療師`

上述三類科保留為後續目標。只有醫事檢驗師與藥學系全年度流程跑穩定、完成品質評估後，
才另開後續實作。

### 1.2 固定模型

五個模型都要獨立讀過每一題：

- `gemma4:31b`
- `minimax-m2.7`
- `deepseek-v4-flash`
- `qwen3.5:397b`
- `gpt-oss:120b`

LLM-Share 已確認這五個正式 model ID 目前可用。模型彼此不能看到其他模型的答案，
以免形成互相抄寫的假共識。

### 1.3 人工狀態原則

- AI 掃描、匯入與 ensemble 階段不得改動任何人工標記。
- AI 只提出 advisory finding 與建議修正。
- 已審題目即使被 AI 找到疑點，也先維持原本的 `accept`、`reviewed`、`correct`、
  `block`、`needs_review` 或 `exclude`。
- 必須先完成整個指定範圍的 AI 掃描，再產出「抓漏範圍報告」。
- 只有使用者看過報告並明確同意後，才能把報告中指定的已審題目追加
  `reset_review`，送回未審。
- 不得以類科 wildcard 或模糊條件直接重設；批准前必須解析成一份固定的
  `candidate_key` 清單及 dry-run 數量。
- 人工舊註記、舊 correction 與原始 action 全部保留。

## 二、目前資料現況與 Phase 0 必要性

### 2.1 Legacy 全量 snapshot

本機 2026-06-20 legacy candidate snapshot 的初步唯讀盤點：

| 類科 | 年度 | 候選題數（盤點值） |
| --- | --- | ---: |
| 醫事檢驗師 | 100-115 | 14,884 |
| 藥師 | 100-107 | 6,759 |
| 藥師(一) | 105-115 | 4,803 |
| 藥師(二) | 105-115 | 4,208 |
| 藥學系合計 | 100-115 | 15,770 |

醫事檢驗師 legacy 人工題目事件依 question-stage 規則取最新值後約為：

- `accept`：14,867
- `exclude`：16
- `reviewed`：1

這證實醫事檢驗師先前已進行大規模人工審核，適合作為「已審題目隱藏錯字抓漏」的第一個
全量類科。

上述數字仍是 legacy snapshot 盤點值，不直接等於最後模型 task 數。parser repair、
候選去重、人工 exclude、較新 correction 及後來匯入的 115-2 資料都可能使正式 SQL worklist
數量不同。

### 2.2 現行 PostgreSQL staging

目前運行中的 PostgreSQL staging 只含 115-2 新批次：

- 醫事檢驗師：478 題。
- 藥師(一)／藥師(二)：449 題。

因此正式執行前必須先完成：

1. 找出包含全年度候選與最新人工事件的權威 snapshot。
2. 將 legacy JSONL、較新 parser repair、115-2 資料與現行 PostgreSQL 對帳。
3. 以 `candidate_key`、來源文件、parser version 與有效文字 hash 去重。
4. 確認人工 question／answer events 的最新狀態沒有遺失。
5. 將全年度範圍載入或連接到 PostgreSQL review staging。
6. 由 SQL worklist builder 產生正式題數與年度／科目／人工狀態 inventory。

依既有 repository 原則，JSONL 是歷史與交換來源，PostgreSQL 才是模型工作清單、Review UI
與後續 `reset_review` 的唯一即時主庫。

### 2.3 正式納入與排除

模型文字抓漏納入：

- 所有非 `exclude` 的有效 question candidate。
- 已人工接受的題目。
- 已有人工 correction 的題目；模型看的是合併 correction 後的有效文字。
- 未審、reset、block 或 needs_review 的題目。

預設不納入：

- 最新人工狀態為 `exclude` 的非題目。
- 已確認為試卷表頭或重複來源的候選。
- 只有答案、圖片或題組問題，且題幹選項文字本身沒有可審內容的工作單。

`exclude` 題目可另做 deterministic inventory 或少量抽樣，但不混入文字糾錯率。

## 三、不可變更的安全邊界

- 官方 PDF 是內容真實來源；模型看不到 PDF 時只能提出疑點，不能宣稱已核實。
- `question_review_events`、`answer_review_events`、`question_ai_review_events` 維持 append-only。
- AI 執行只新增 `model_runs` 與 `question_ai_review_events`。
- AI 不得產生人工 `accept`、`block`、`exclude`、`correct` 或 `reset_review`。
- 題目文字預審不判斷正確答案，也不把考題故意錯誤的干擾選項當成 OCR 錯誤。
- 圖片內容、答案格式及題組範圍分別交給 image、answer、group 關卡。
- LLM-Share 是外部資料處理者。只送完成文字預審所需的最小題目內容。
- 不傳 API key、資料庫連線資訊、使用者個資、整個 repository、整份 PDF、本機絕對路徑或
  無關人工註記。
- 原始模型回覆、批次 input、checkpoint 與大型結果放在 `tmp/` 或本機資料根，不提交 Git。
- 未取得使用者對抓漏範圍的明確同意前，不套用新修正、不重設題目、不改變 formal readiness。

## 四、要抓的問題

五模型檢查：

1. 異常簡體字。
2. 形近錯字、漏字、多字及專有名詞拼寫破損。
3. 公式、上下標、希臘字母、單位、百分號、LaTeX／Markdown 損壞。
4. 選項遺漏、合併、重複、標號錯位及跨題邊界問題。
5. 疑似 OCR／轉錄造成的語句斷裂或語意不通。

`semantic_disfluency` 只能表示「轉錄後不成句或明顯斷裂」。模型不得用這個標籤批評題目的
醫學知識、臨床敘述或錯誤選項是否正確。

question-stage issue family：

- `non_question_header`
- `boundary_merge`
- `boundary_missing`
- `empty_stem`
- `option_structure`
- `ocr_character`
- `notation_markup`
- `semantic_disfluency`
- `visual_dependency`
- `group_dependency`

## 五、模型工作單與批次打包

### 5.1 單題資料

每題的獨立 task 包含：

- `candidate_key` 與 `stage=question`。
- 類科、科目、年度、考次、題號與 occurrence。
- 合併最新人工 correction 後的有效題幹。
- A-D 選項及 option count。
- 同一試卷的前一題／本題／下一題短節錄。
- active parser issue 及必要結構訊號。
- 圖片／題組依賴路由訊號。
- 與本題 scope 相關的已確認錯字提示與反例。
- `effective_content_hash`、prompt version、normalization registry version。

模型不會取得本機檔案或 SQL 存取權，也不能自行讀取官方 PDF。

### 5.2 API 批次

不採用一題一個 API request。多題打包為一個 LLM-Share request，但每題仍保有獨立 task ID
與獨立 JSON result。

打包原則：

- 同一 request 優先放同一類科、科目、年度、考次的相鄰題目。
- 不跨過 input/output token 安全上限。
- 整份 80 或 100 題試卷只作 pilot／壓力測試，不作正式預設批次。
- 每個 input `candidate_key` 必須恰好有一個 output。
- batch 失敗只重跑該 batch 的缺失／無效題目。
- 同一題、同一模型、同一 input hash 不重複匯入。

初始測試 batch strategy：

- 8 題／request
- 16 題／request
- 24 題／request
- 半份試卷：同一科目、年度、考次、來源試卷的全部題目均分成兩個 batch；80 題卷通常為
  40 題／batch，100 題卷通常為 50 題／batch。
- 整份試卷：同一科目、年度、考次、來源試卷的所有題目放在同一個 batch；實際題數以 worklist
  為準，不假設一定是 80 或 100 題。

五個模型可採不同的最終 batch size，不能因某模型適合 24 題就強迫其他模型使用同一設定。

### 5.3 API 擁擠、timeout 與重試控制

- Temperature 固定為 0。
- 品質評估允許單次推論超過 300 秒；不能因前端 connector 約 300 秒的等待上限就把模型判為
  失敗。
- 品質 pilot 預設全域 concurrency 1，五模型依序送出，接受合理延遲以避免壅塞。
- 正式批次最多每模型 1 個 request；只有確認服務容量後才提高全域 concurrency。
- 只有在無 429、timeout、截斷及 schema degradation 時，才測試每模型 concurrency 2。
- 遇到 service error 使用 bounded exponential backoff。
- 每個 run 有 manifest、checkpoint、完成狀態、retry count 與 raw response。
- connector timeout 後不得立即自動重送。必須先用 request ID、後端紀錄或人工對帳確認原推論
  是否仍在執行；無法確認時標記為 `completion_unknown`，避免同一工作在後端重複執行。
- `max_tokens` 依協定的最壞輸出量動態估算，pilot 不再一律設為 32,768。任何模型反覆碰到
  token 上限都視為協定或 hidden-reasoning 問題，不視為可接受的完整輸出。
- API 擁擠時降低 concurrency，不能用無上限自動重試放大流量。

### 5.4 2026-07-29 Virtual Key SLO 排查與協定修正

LLM-Share dashboard 的 `.timliang` Virtual Key 共 427 筆樣本：P50 5.13 秒、P95 97.1 秒、
P99 301.3 秒，TTFT P95 48.8 秒。這確實遠高於全站 P95 26.3 秒，但逐筆拆解顯示並非固定的
Virtual Key 路由劣化：

- 本次題目審核 request 共 65 筆，佔該 key 59 筆 `>30s` 樣本中的 55 筆。
- 該 key 全部 40 筆 `>60s` 與全部 18 筆 `>120s` 樣本都來自本次審核 workload。
- 審核 request 平均 input 約 6,661 tokens，平均 completion 約 8,137 tokens。
- 相同模型的其他使用者常有更長 input，但只輸出數百 tokens，因此可在數秒至十餘秒完成。
- LiteLLM 已記錄的額外 overhead 平均只有毫秒級，無法解釋數十至數百秒差距。
- `llmshare-subagent` 是非串流等待；部分紀錄的 TTFT 等於整段推論時間，不能直接拿來和串流
  client 的 TTFT SLO 比較。
- 已觀察到 connector 約 304 秒回報 timeout，但後端在約 404 秒完成且記為成功。timeout 後
  立刻重試可能造成重複推論、額外 token 與排隊。

主因是目前 v2 要每一題都輸出完整狀態、原因、證據、finding、coverage，甚至重寫完整題幹與
四個選項；模型也可能產生大量 hidden reasoning。半份或整份題目雖然增加上下文，卻同時把
completion 放大到數千至數萬 tokens。這會污染互動型 Virtual Key 的 SLO，也提高截斷、漏題
與重試風險；因此外部多份 benchmark 先暫停，不再用既有 v2 繼續補跑。

下一版改為四路 compact 初審：

1. `ocr_text`：只抓簡體字、錯別字、OCR 字形、公式／符號及影響閱讀的標點空格。
2. `meaning`：只抓轉寫造成的漏字、重複、斷句、選項黏合與明顯題意不通；不審答案真假。
3. `visual`：先以文字與資產 metadata 判斷是否缺圖，再由 vision 模型核對圖片模糊、裁切或
   配錯；不利用圖片解題。
4. `group`：只核對明示題組範圍、共同題幹、承上題、`group_ref` 與題序。

四個通道彼此獨立、依序執行並個別計時。每批輸入使用短整數 `q` 代表題目，模型回覆只含
`batch_id / checked_count / issues`；正常題不逐題輸出，疑點才輸出
`q / code / location / observed / replacement / confidence / note`。其中只有 `ocr_text` 可以
提供最小 `replacement`；其他三路一律不得產生文字 patch。

本機 validator 必須驗證 batch ID、題數、JSON 完整性、finish reason、issue code、欄位位置，
並確認 OCR 的 `observed` 逐字存在且能唯一替換。通過後才由本機把局部替換 materialize 成
Review UI 既有的完整 `suggested_correction`，所以模型不再重傳完整題幹或全部選項。

半份／整份是否提升糾錯率，只比較人工 precision、known-defect recall 與漏題；不再以冗長
逐題 JSON 當作大批次必要輸出。互動工作與長時間 batch 工作仍建議分開 Virtual Key／SLO
報表，但監控分流不能取代協定瘦身。

## 六、四路輸出、Review UI 串接與多模型共識

### 6.1 模型 wire protocol

每個通道每批只輸出一個小型 JSON object：

```json
{
  "batch_id": "ocr_text-...",
  "checked_count": 8,
  "issues": [
    {
      "q": 3,
      "code": "simplified_character",
      "location": "stem",
      "observed": "辅酶",
      "replacement": "輔酶",
      "confidence": 0.98,
      "note": ""
    }
  ]
}
```

`checked_count` 不是品質證明，只是截斷／漏回的結構 gate；還必須同時有合法 JSON、
`done_reason=stop`、正確 `batch_id` 及本機欄位驗證。任何通道不完整，整批不得產生可匯入的
Review UI preview，也不得自動重送。

### 6.2 Review UI 流程

- 本機聚合器為每題建立 `checks.ocr_text / meaning / visual / group` 四個獨立狀態，再轉成
  現有 `question_ai_review_events` 的 advisory audit。
- Review UI 繼續以 findings 顯示疑點，並另顯示四路狀態；不新增人工通過狀態。
- OCR 的安全替換才會顯示「套用 AI 建議校正」；套用後仍為 `needs_review`。
- 已人工審過的題目維持鎖定。只有使用者看過抓漏範圍並批准精確 candidate 清單後，才能追加
  `reset_review` 送回未審，再由人工決定是否套用。
- 題義、圖片與題組 findings 只導向原有文字、圖片或題組人工流程，不自動更改內容或關係。

### 6.3 保存策略

每題保存：

1. Gemma 原始有效結果。
2. MiniMax 原始有效結果。
3. DeepSeek 原始有效結果。
4. Qwen 原始有效結果。
5. GPT-OSS 原始有效結果。
6. 本機 deterministic ensemble 結果。

五筆模型事件分別 append 到 `question_ai_review_events`。五模型都完成後，再 append
`model_name=llmshare-multi-model-ensemble` 的共識事件，引用五筆 member event／model run。

個別模型建議永久保留，不會被 ensemble 覆蓋。Review UI 的 active 摘要使用 ensemble，
detail pane 可展開五個模型結果。

### 6.4 共識規則

相同 finding 依 `location + observed + suggested + issue_family` 正規化：

- 5/5 pass：ensemble pass，但不改人工狀態。
- 1/5 有疑點：單模型疑點。
- 2/5 同一局部 correction：可顯示共有 patch，但仍只供人工採用。
- 3/5 以上同一疑點：多數模型一致。
- 5/5 同一疑點：全模型一致。
- 同一位置提出不同修正：模型分歧，禁止直接套用。
- 任一模型缺結果、timeout 或 schema invalid：ensemble incomplete，不得當成 pass。
- advisory block 只用於有具體結構證據的候選；不確定只能 needs_review。

pilot 完成前五模型先等權。模型品質較差時仍保留其事件，但可將其標為 experimental，
不讓它單獨決定低風險分類。

## 七、建議修改按鈕

Review UI 對安全的文字建議顯示：

- before／after。
- 題幹或選項位置。
- 哪些模型提出。
- 1/5 至 5/5 共識。
- 建議信心與 evidence。

「套用 AI 建議」按鈕只在下列條件成立時提供：

- observed 仍存在於目前有效文字。
- 修正是精確局部 patch。
- 沒有模型衝突。
- input hash 與目前候選相符。
- 不修改答案、圖片或題組。

為符合「未批准前不更改人工標記」：

- 已審題目在抓漏報告批准前，只顯示建議及按鈕預覽，不寫入 correction。
- 使用者批准 reset 範圍並完成 `reset_review` 後，才啟用真正的套用動作。
- 本來就未審的題目可直接顯示可用按鈕，但仍由人工主動點擊。
- 點擊按鈕只建立可追蹤的人工 correction；題目仍需人工再次按接受。
- 不因套用 suggestion 自動 accept。

## 八、抓漏範圍報告與批准閘門

### 8.1 完成條件

醫事檢驗師或藥學系的一次全量掃描，只有在以下條件都成立時才算完成：

- 正式 worklist 已凍結。
- 五模型對每個 task 都有有效結果，或報告明列最終失敗題。
- validator 通過。
- 個別模型事件及 ensemble 已 append。
- 沒有任何人工狀態被改動。

不得在全量掃描中途分批 reset。

### 8.2 報告內容

每一類科完成後產出：

1. Coverage report
   - 目標題數、送出題數、各模型成功／失敗／重試數。
   - 年度、科目、人工狀態與 parser version 分布。
   - 缺結果或 input hash 不一致的 candidate。
2. Catch report
   - AI 有疑點題數。
   - 有明確文字 correction 的題數。
- 單模型、2/5、多數（3/5 以上）、全模型與模型分歧數。
   - 依年度、科目、issue family、subtype 分組。
   - 已審題與未審題分開計數。
3. Reset proposal
   - 建議送回未審的精確 candidate 清單。
   - 每題目前人工 action、before／after、模型共識與 AI event ID。
   - 不同批准方案，例如只選多模型一致、只選 exact correction、或指定年度／科目。
4. No-change statement
   - 明確聲明截至報告產出時沒有 reset 或改寫人工狀態。

### 8.3 使用者批准後

使用者可批准全部或部分 proposal。執行前再次 dry-run，將批准條件解析為固定
`candidate_key` 清單並回報：

- 將追加多少筆 `reset_review`。
- 各舊 action 數量。
- 已是 unreviewed／reset／block／needs_review 而不需重設的數量。
- `exclude` 或 candidate hash 已變更而跳過的數量。

取得明確同意後才 apply：

- 只對批准清單中、目前為已審 closed state 的題目追加 `reset_review`。
- event 保存 `previous_action`、`previous_notes`、`previous_correction`、
  `approval_ref`、ensemble event ID 與 run ID。
- `exclude` 不自動解除。
- 已未審或已 open 的題目不重複 reset。
- reset 本身不先套用 AI correction。

之後由使用者在 Review UI 逐題查看 PDF、決定是否套用建議，再重新接受或保留疑問。

## 九、批次大小與糾錯率評估

### 9.1 真實評估資料

醫事檢驗師 pilot 優先從既有大量人工事件建立：

- 真實 correction positive：人工 correction 中可從 before／after 確認的 OCR、notation、
  option defect。
- clean control：同科、同年、人工接受且沒有文字 correction 的題目。
- hard cases：希臘字母、血型符號、胺基酸中英對照、表格／圖片路由及長醫學名詞。

藥學 pilot 加入：

- 已有人工作業的藥動學上下標、簡繁錯字與 registered mark 案例。
- 藥師、藥師(一)、藥師(二)各年度分層 clean control。
- 調劑學、藥物治療學、生藥學與法規的科目特有風險。

人工 gold 與 injected defect 分開報告。合成錯誤只測格式與基本檢出能力，不能取代真實
糾錯率。

為避免模型只照 confirmed list 抄答案，pilot 分兩種：

1. Discovery benchmark：暫不注入該題 gold correction，測模型自己能否找出。
2. Production prompt benchmark：注入已確認且 scope 適用的提示，測穩定性及誤報。

### 9.2 指標定義

- Suggestion precision：
  `人工確認正確的 AI 建議數 / 人工實際檢查的 AI 建議數`
- Known-defect recall：
  `模型找到的真實已知錯誤數 / gold set 的真實已知錯誤數`
- New catch yield：
  `人工新確認的隱藏錯字題數 / AI 掃描題數`
- False-alert rate：
  `人工否決的 finding 數 / AI 掃描題數`
- Exact-correction rate：
  `可直接作局部 before/after 的正確建議 / 全部正確 finding`
- Coverage rate：
  `有合法結果的 candidate 數 / worklist candidate 數`
- Schema-valid rate、missing／duplicate key rate、retry rate。
- 每題平均 input/output token、平均與 P95 latency、timeout／429 rate。
- 各共識層級 finding 的人工 precision。

每個模型、每個 batch size、每個科目群組分開計算，不能只看五模型混合平均。

### 9.3 Batch size 選擇

對同一批 pilot 題分別測 8、16、24 題／request、半份試卷與整份試卷。半份／整份模式只和
相同試卷的固定題數模式比較，避免把不同科目或年度的長度混在一起。選擇每模型可穩定使用的
最大 batch：

- candidate coverage 經有限重試後為 100%，否則有明確 failed list。
- schema-valid rate 建議至少 99%。
- unsafe correction 為 0。
- 沒有輸出截斷或漏掉 batch 後半題目。
- 相較 batch 8，known-defect recall 與 precision 不得有明顯下降。
- 合理延遲可接受；latency 僅作容量規劃，不作糾錯品質淘汰門檻。
- 仍需限制 timeout、截斷、重試與併發，避免 API 壅塞。

若整份試卷或半份試卷降低品質，依序退回 24、16、8。模型可採不同設定。

第二輪採多試卷配對設計：每份試卷以相同前 24 題比較 `24`、`half-paper` 與
`full-paper`，五模型全部全域 concurrency 1 依序執行。主要比較 finding 重疊、24 題已找到
而大批次漏掉的疑點、大批次新增疑點、完整 correction 比率、schema/coverage 警告與人工核對
後的 precision/recall。大批次若只是多報、但漏掉較小批次的確定錯字，不能視為效果較好。

### 9.4 模型 pilot gate

建議正式全量前達到：

- 經 bounded retry 後 coverage 100% 或所有失敗題可重現並列出。
- schema-valid rate至少 99%。
- clean control false-positive rate不高於 5%。
- 真實已知 OCR／notation／option defect recall至少 85%。
- unsafe correction 為 0。

未過 gate 時先調整 prompt、batch size 或將該模型降為 experimental。仍不得跳過保存個別結果
與失敗原因。

### 9.5 本地 Ollama／MLX 成本效益 pilot

原本的獨立本地模型任務已依使用者指示暫停並保留 checkpoint。其 `medgemma:4b` 舊 v2 與初版
compact 測試皆碰到輸出 token 上限，不能作為品質結論。

新策略先在主線固定使用 `qwen3.6:35b-mlx`，因本機 Ollama 已確認該模型具 completion、
vision、thinking 與 tools capability。第一輪只跑 frozen 8 題、四路依序、concurrency 1，
API 明確設定 `think=false`、structured output 與保守 `num_predict`，記錄各通道 TTFT、
total latency、prompt/eval tokens、thinking bytes、finish reason、schema 完整性及 findings。
不因單次延遲較高而淘汰，但只要有截斷、漏回或危險專業改寫，就不推送 LLM-Share。

2026-07-29 實測結果：

- 本地 `qwen3.6:35b-mlx` 的 OCR／題義通道分別約 2.18／2.00 秒，TTFT 約
  0.25／0.76 秒，completion 209／136 tokens，均 `done_reason=stop` 且無 thinking 輸出。
- 模型的 vision capability 宣告與實際不一致。CLI 與 API 都只把圖片顯示成 `[img-0]`
  placeholder；模型無法看見 q16 圖片中的 A–D 分圖。此模型的圖片通道標記為 `unavailable`，
  只路由回 Review UI 圖片人工流程。
- LLM Share 純文字 compact pilot 中，`gemma4:31b` 產生有效短 JSON 並抓到 q5 選項錯字與
  q39 選項黏合；`qwen3.5:397b` 也抓到疑點，但 patch 與 evidence 需 validator。
- `minimax-m2.7` 回傳有效空 findings，漏掉已知缺陷；`deepseek-v4-flash`、
  `gpt-oss:120b`、`minimax-m3` 都在 1,200 completion tokens 內只生成 reasoning，沒有 final
  JSON。這三者不得立即重送或進入全量。

詳細報告見 `docs/compact-initial-audit-pilot-2026-07-29.md`。

後續候選仍保留：

- `qwen3.6:35b-mlx`
- `qwen3.6:27b-mlx`
- `gemma4:26b-mlx`
- `gemma4:12b-mlx`
- `medgemma:27b`
- `medgemma:4b`

下載前先唯讀確認本機 Ollama/MLX runtime、精確 registry tag、quantization、可用磁碟、統一記憶體
與長上下文需求；不在容量未知時一次拉取全部模型。後續模型不再重跑 verbose v2，只使用同一版
四路 compact 協定，避免把已知錯誤協定重新當基準。比較維度包含人工 gold precision/recall、
correction 完整率、危險專業改寫、prompt/eval tokens、每題 wall time、time-to-first-token、
tokens/s、可持續吞吐、電力／硬體折舊與 API token 成本。外部與本地模型必須使用同一題組，
不能以不同 workload 比 latency 或成本。

## 十、人工確認錯字清單與 Python 規則

### 10.1 修正在 MinerU 還是現階段

不直接改 MinerU 原始輸出。MinerU JSON／Markdown 是可追溯的 OCR 證據層，需保留原貌。依錯誤
來源分三層處理：

1. 單題、偶發 OCR 錯字：目前由人工在 Review UI 建立 correction event。這是最快且可追溯的
   修正，也會成為未來規則的正例。
2. 同一 OCR／切題模式反覆出現：在 MinerU 後方的 deterministic parser／normalization 層
   加 Python 規則，對全庫 dry-run、加正負例測試後重建 derived candidate。若已審文字改變，
   仍須先產出精確影響範圍並取得批准，再 append `reset_review`。
3. 官方 PDF 本身就有錯字：不能把它當 MinerU OCR 錯誤改寫來源。保留官方原文，在候選或 UI
   另存人工註記／建議版本。

因此現階段人工 correction 不是重工，而是收集可驗證證據；只有已證明可泛化的錯誤才往上游
提升成 parser/normalization 規則。若問題屬於 MinerU 引擎本身且本地後處理無法可靠修復，再
評估重跑不同 MinerU 版本或參數，但仍保留原 run 與 parser version。

沿用：

- `docs/ocr-correction-inbox.md`：人可讀發現入口。
- `configs/text_normalization_rules.json`：機器可讀 registry。

生命週期：

- `candidate`：AI 或人工發現，尚未對照 PDF。
- `confirmed_hint`：人工已對照 PDF，可在下一個 prompt version 使用。
- `active_rule`：通過 deterministic promotion gate，可由 parser／repair script 套用。
- `rejected`：確認不應修正，可作負例。

每次全量 run 凍結 prompt version 與 registry version。掃描進行中新增的 confirmed hint 不會
改變同一 run 後半段 prompt；它只在下一個 run 生效，確保結果可比較、可重現。

Python rule promotion：

1. narrow scoped exact phrase 可在一筆 PDF-confirmed 證據後成為 scoped rule。
2. 跨科／全域規則原則上至少三個獨立正例，並跨兩份來源文件。
3. 全庫 dry-run 列出 match、衝突及會改動的 accepted 題目。
4. 每條規則加入正例與負例 regression test。
5. 零未解釋衝突後才 apply。
6. 保留 raw candidate，只更新 derived correction。
7. 重跑冪等，不產生重複事件。
8. 若修復會改變已審文字，仍需先依本規劃的抓漏報告與使用者批准閘門處理。

## 十一、實作階段

### Phase 0：全年度 SQL 對帳

- 醫事檢驗師 100-115。
- 藥師、藥師(一)、藥師(二) 100-115。
- 匯整最新 candidates、parser repair、人工 review events 與 115-2 新資料。
- 產出正式 inventory、重複／缺漏／exclude／人工狀態報告。
- 不呼叫模型、不修改人工事件。

### Phase 1：共通契約、validator 與 UI（已實作）

- 延伸 SQL worklist builder 支援 include-accepted 全年度抓漏。
- 更新 issue taxonomy 與 output schema。
- 建立可恢復的 LLM-Share 批次 runner。
- 建立逐模型 validator、importer 與 ensemble builder。
- Review UI 加入共識、分歧、批次不完整及 correction preview。
- 實作批准前 suggestion preview、批准後才可套用的安全閘門。

已完成的元件：

- SQL worklist 附加 `source_registry_key` 與有效內容 hash，可安全按同一來源試卷打包。
- `scripts/llmshare_question_audit.py`：派送 manifest、最小化外送 payload、原始結果正規化、五模型
  ensemble、抓漏報告與 benchmark report。
- `scripts/apply_llmshare_reset_proposal.py`：只有同時給 `--apply` 與人工 `--approval-ref` 才會追加
  `reset_review`；執行前重新核對人工 action 與內容 hash。
- Review UI 對已審題的 AI 建議顯示為鎖定狀態；前端與伺服器端都拒絕以 AI 建議直接覆寫人工狀態。

### Phase 2：批次與品質 pilot（結構可行性已完成；gold accuracy 待補）

- 以真實 medtech／pharmacy gold、clean control 與 hard cases 測試。
- 每模型比較 8、16、24、半份試卷與整份試卷。
- 比較 quality、coverage、latency、token、timeout、retry。
- 固定每模型 production batch size 與 prompt version。

2026-07-29 第一輪已在一份真實 115-2 醫事檢驗師 80 題來源試卷完成三模型代表性壓測：每個模型各測一次
8、16、24、40（半份）與 80（整份）題；15 次皆完整回傳、無漏題。以「固定批次、60 秒內、零
正規化警告」選擇正式初始設定：

| 模型 | 初始正式固定批次 | 24 題實測 | 半份／整份處置 |
| --- | ---: | ---: | --- |
| `gemma4:31b` | 24 | 16.8 秒 | 均可完成，但僅實驗模式 |
| `minimax-m2.7` | 8 | 200.9 秒且有 2 筆格式 shape warning | 不作正式預設 |
| `deepseek-v4-flash` | 24 | 29.2 秒 | 均可完成，但僅實驗模式 |

詳細原始回覆與 benchmark 皆在本機 `tmp/llmshare_tri_model_pilot/`，不提交 Git。這個 pilot
只衡量完整率、格式契約、延遲與疑點產量；尚無人工黃金標記，不能把疑點數當作糾錯準確率。

第一輪也發現模型可能在 reason/evidence 指出兩處錯誤，卻漏掉或只產生部分
`suggested_correction`。第二輪升級為 `llmshare_five_model_text_audit_v2`，將 finding 與
correction coverage 設為強制契約，加入 `qwen3.5:397b`、`gpt-oss:120b`，並改用多份試卷的
24／半份／全份配對比較。第二輪不以 60 秒延遲作淘汰依據。

### Phase 3：醫事檢驗師全年度掃描

- 掃描所有有效、非 exclude 的醫事檢驗師題目。
- 已接受題也全部納入。
- 五模型與 ensemble 全部完成後才出報告。
- 報告前及報告後都不自動 reset。

### Phase 4：醫檢抓漏批准與人工重審

- 交付 coverage、catch、model-quality 與 reset proposal。
- 等待使用者選定範圍。
- dry-run 固定 candidate 清單。
- 取得明確同意後才追加 `reset_review`。
- 人工使用建議按鈕、對照 PDF、重新接受或保留疑問。
- 彙整醫檢 confirmed correction 與 false positive。

### Phase 5：藥學系全年度掃描

- 使用經醫檢 pilot 驗證的 runner 與批次策略。
- prompt 只載入 global 及 pharmacy scope 規則，不帶入無關醫檢專屬規則。
- 藥師、藥師(一)、藥師(二)全部年度分層執行。
- 全量完成後才產出抓漏報告。
- 取得使用者明確同意後才 reset 指定題目。

### Phase 6：規則晉升與穩定性報告

- 統計五模型 precision、recall、new catch yield、false alerts。
- 彙整模型共識與人工確認的關係。
- 將穩定錯誤類型晉升 Python 規則。
- 驗證規則不會誤傷已審題目。
- 交付是否足以擴展到醫放、醫學及物治的建議。

## 十二、抓漏後的正式放行

AI pass 或五模型共識都不是正式放行。

單題正式可用仍要求：

- 人工題目狀態為 `accept` 或 `unblock`。
- 人工答案狀態為 `accept` 或 `unblock`。
- 必要圖片／題組關卡已完成。
- formal sync 成功。
- release validator 通過。

AI 只協助縮小人工需要重看的範圍。

## 十三、預計修改範圍

已修改或新增的核心元件：

- `docs/skills/national-exam-ai-audit/references/issue-taxonomy.md`
- `docs/skills/national-exam-ai-audit/references/output-schema.md`
- `docs/skills/national-exam-ai-audit/references/subject-overrides.md`
- `docs/skills/national-exam-ai-audit/scripts/build_sql_review_worklist.py`
- `docs/skills/national-exam-ai-audit/scripts/validate_review_results.py`
- `docs/skills/national-exam-ai-audit/scripts/import_advisory_results.py`
- `scripts/serve_question_review_ui.py`
- `configs/text_normalization_rules.json`
- `scripts/llmshare_question_audit.py`
- `scripts/apply_llmshare_reset_proposal.py`
- `tests/test_llmshare_question_audit.py`

## 十四、實作開始條件

使用者已指示開始實作；下一個阻塞點是完成全年度 SQL 對帳後，再產生醫技全量 worklist。

Phase 0 至 Phase 3 可進行 inventory、程式實作、pilot、AI advisory 匯入及報告，但不得寫入
`reset_review`。

每一次實際 `reset_review` 都是獨立的高影響動作，必須在完整抓漏報告之後取得使用者對
精確範圍的明確同意；本 plan 的總體確認不視為未來 reset 的預先授權。
