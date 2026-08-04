# OCR 到可用題庫的 Agent 自動化策略

## 目標

將官方 PDF 轉成可供 Review UI 與正式題庫使用的候選資料，同時保留來源、降低模型
token、限制模型權限，並把人工接受／拒絕結果轉成可版本化規則。

模型負責發現殘餘問題，不負責定義真相。來源證據、deterministic validator 與人工作業
共同決定能否修復、導流或放行。

## 單一資料流

```text
官方 PDF / 答案 PDF
  -> MinerU 原始輸出與 lineage
  -> parser candidate
  -> deterministic QA + active rules
  -> residual lane selection
  -> sparse model advisory
  -> output validator
  -> active-rule-only patch materializer
  -> Review UI / human evidence
  -> rule retrospective and promotion
  -> formal sync and release validator
```

不可把任一衍生層覆蓋回來源層。正式題庫可用性仍要求題目、答案及必要資產通過既有
release gate。

## Agent 分工

| Agent | 輸入 | 輸出 | 禁止事項 |
| --- | --- | --- | --- |
| Inventory | SQL metadata | counts, strata, frozen scope | 判斷題文 |
| Parser QA | raw/parser structure | deterministic issue, parser worklist | 猜回缺失題文 |
| OCR rules | active exact rules | exact correction candidates | 使用 proposed 規則 |
| OCR discovery | minimal text packet | sparse OCR/notation issues | 判答案、改原卷疑義 |
| Semantic transcription | bilingual/context anchors | transcription issue or PDF route | 醫學解題、改 distractor |
| Group router | explicit range and neighbors | group route/proposal | 寫人工 group confirmation |
| Visual router | cues and asset metadata | visual route | 假裝看過 pixels |
| Vision reviewer | source pixels and placement | visual advisory | 改題文或人工視覺結論 |
| Materializer | validated issue + active rule | complete UI-safe patch | 模糊比對、部分選項 patch |
| Validator | task ledger + result | pass/fail report | 修補模型輸出後放行 |
| Retrospective | reviewed outcomes | rule proposals and counterexamples | 啟用自己提出的規則 |
| Promotion gate | evidence + gold regression | state transition | 跳過 shadow 或來源核對 |

## 四條模型 lane

### OCR text

處理可見 OCR 字形、簡繁混用、科學記號與已知 exact token。active 規則先在本地處理；
模型只看剩餘疑點。模型提出的新 replacement 預設為 `human_pdf` 或 `propose_rule`，不得
直接套用。

### Semantic transcription

只判斷轉錄是否造成明顯句意斷裂，例如中文專有名詞與同列英文錨點矛盾、OCR 漏字或
選項黏合。禁止判斷選項醫學真假。官方原卷即如此時使用 `source_original` 或
`source_original_suspected_typo`，忠實保留來源。

### Group

使用明示範圍、題數、`承上題`、共同題幹與相鄰題。模型可以提出範圍並送題組關卡，
但最終 range、sequence 與 shared stem 由 group review 擁有。

### Visual

文字模型只能判斷是否值得送圖像關卡。只有實際收到 pixels 的模型可以檢查圖表內容、
張數、裁切與綁定；其結果仍是 advisory。

## 低 Token 設計

1. 不把 active exact rules 再送模型。
2. 不把所有 subject override 送每一科。
3. 正常題只由本地 coverage ledger 記錄，不要求逐題 pass JSON。
4. 一個 batch 只回 `batch_id`、`checked_count` 與 issue rows。
5. issue row 只含 key、field、before/after、family、source class、route、confidence 與
   必要的 rule id。
6. 完整 stem/options patch 由本地 materializer 生成。
7. 只有 boundary/group lane 才加入 neighbors；只有 visual lane 才加入 assets。
8. 批次大小由 model profile 決定，不以一個數字套用所有模型。
9. 每次 run 記錄 input/output/thinking/cache token、retry、invalid batch、latency 與新抓漏。

## 修復層級

| Tier | 條件 | 系統行為 |
| --- | --- | --- |
| A | active exact rule、唯一命中、scope 符合 | 可產生 deterministic patch |
| B | PDF/MinerU alignment 已證實，但尚未 active | 產生 preview 與 rule proposal |
| C | 語意推測、原卷疑義、source 不足 | `human_pdf`，不得物化 |
| D | boundary、缺題、重題、非題目表頭 | parser repair |
| E | 圖片／表格／題組 | 專屬 review lane |

若 Tier A 改到已審內容，保留原 note/correction，並只對實際可見內容變更的 candidate
追加 `reset_review`。不得整批重設。

## 規則自我進化

批次完成後，本地先聚合 accepted、rejected、source-original、parser、visual/group route
與 repeated replacement。Retrospective agent 只讀摘要與少量代表案例，輸出
`rule-proposal.schema.json`。所有模型輸出先經 `scripts/validate_rule_proposals.py`；
模型只能提交 `observed` 或 `proposed`，不得直接宣告 `shadow`、`verified` 或 `active`。

規則狀態：

```text
observed -> proposed -> shadow -> verified -> active -> retired/revoked
```

晉升要求：

- 模型自己的 finding 不能作為唯一證據。
- replacement 必須有官方 PDF、可信 MinerU/source alignment 或明確人工作業結果。
- 全庫 dry-run 必須列出正例、反例、scope 與現有人工 correction 衝突。
- negative controls 與 gold corpus regression 必須通過。
- exact rule 必須有穩定 id、版本、source/target、scope、evidence 與 rollback 資訊。
- source-original、parser、group、visual 規則最多自動導流，不可變成文字 replacement。

## 評估資料與門檻

### 工程 gate

至少 30 題、三份考卷，包含真缺陷、clean controls、source-original、distractor、
canonical markup、parser boundary、group 與 visual route：

- issue-site recall 至少 95%；
- negative-control unsafe edit 為 0；
- clean false-alert 不高於 5%；
- route accuracy 100%；
- deterministic patch 必須 exact unique match。

這個 gate 只允許擴大 shadow，不允許全量自動修復。

### 規模化 gate

在跨類科、年份與版型的較大 gold 上，另外評估：

- correction precision 與 source fidelity；
- 每百題人工工作量；
- 新抓漏 yield；
- parser/visual/group 漏抓率；
- 每個真實新 finding 的 token 與延遲；
- 模型版本或 prompt 變更後的 regression。

只有 active deterministic rules 可以自動產生修復；AI pass 仍不等於人工 accept。

## 模型可攜性

Skill、task/result contract、rules、negative controls、gold、validator 與 materializer 通用。
每個模型只用一份 profile 與 transport adapter，描述：

- 可執行 lane；
- 是否真的支援 pixels；
- batch size；
- structured-output mode；
- reasoning/thinking 行為；
- token/timeout 上限；
- 是否只允許 discovery。

新模型必須逐 lane 認證。不能因 post-MinerU 文字校對 lane 通過就同時授權 group 或
visual lane；文字校對不得重新執行 OCR。

AI MAX 395 作為 inference worker 時，不持有 Review PostgreSQL 寫入權限。Mac Studio 凍結
task/manifest，AI MAX 只消化封包並回傳 sparse result、raw response、token 與 timing；
結果回到 Mac Studio 或維護端後才執行 validator、materializer preview 與人工審核。
部署細節見 `ai-max-395-deployment.md`。

## 失敗與恢復

- Batch 缺漏、重複 key、錯 stage、invalid JSON 或 checked count 不符：整批拒絕，可從
  ledger 安全重跑。
- Model replacement 沒有 active rule：保留 advisory，拒絕 materialize。
- Exact replacement 零命中或多命中：拒絕並送 rule conflict。
- Source fingerprint 或 human state 改變：舊結果標 stale，不匯入。
- 新規則造成 regression：轉 `revoked`，停止後續套用；已產生事件仍保留 lineage。
- Adapter 或模型不可用：改用同 lane 已認證 profile；不可跨能力假裝完成。
