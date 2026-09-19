# 平台串接可行性 + 優先科目試點計畫（v0.1，2026-09-12）

關聯：`PROPOSED_WORKFLOW.md`（同一沙盤的兩份文件之一）
本文件只讀引用，未修改 platform-app 或 tw-national-exam-catalog 內任何檔案。

---

## 1. 與 platform-app 的串接：可行，接口已經存在且對齊

### 1.1 平台已定義的「邊界」（不需要改動平台）

`platform-app/docs/question-bank-data-package-contract.md` 已規定一個**不可變的**交付封裝格式：

```text
<package>/
  manifest.json          # 封裝識別：identity, schema version, row counts, checksums
  subjects.json          # 學科/類別 對照提示
  questions.jsonl        # 題目（主）
  groups.jsonl           # 題組
  asset_manifest.jsonl   # 圖片/表格 的來源與裁切記錄（含 page + bbox 欄位）
  assets/**.png          # 相對路徑，僅相供
```

匯入器：`platform-app/scripts/import_question_bank_package.py`，**預設 dry-run**，`--apply` 才寫入；
報表（report-json）輸出至 `data/question_bank_import_reports/`。

### 1.2 必要的欄位（匯入器要求，缺一個不可）

`validate_package()` 逐列檢查以下必填欄位（實測自 `scripts/import_question_bank_package.py:340-372`）：

| 欄位 | 我們的來源 | 狀態 |
|---|---|---|
| `external_source` | 常數 `"tw-national-exam-catalog"` | 就緒 |
| `official_category_name` / `official_subject_name` | 官方 PDF 檔名 + 官方目錄 metadata（無則賦，沒有則註） | 就緒 |
| `roc_year` / `exam_number` / `question_number` | 檔名與「題號」解析；題號連續由 detect_anchor_style 校驗 | 就緒 |
| `question_type` | 由 item-type detection 判定（見 §3），不再硬編 `single_choice` | **改良** |
| `stem` / `options[]` / `answer` | 雙引擎（PyMuPDF / poppler）一致者錄；不一致者覆核 | 就緒 |
| `metadata` | 載入 visual_profile / feature_tags / provenance | 就緒 |
| `source_question_key` | 唯一識別：`MOEX-<year>-<category>-<session>-<pdf>#<qno>` | 就緒 |

### 1.3 平台自帶的品質管制（與我們的檢查相輔，相得益彰）

匯入器已在做（列舉）：

- 選項 `key` 重複 → `duplicate_option_keys`
- `answer` 不在 `options[].key` 集合內 → `unsupported_answer_tokens`
- 內容雜湊（content hash / source hash）→ 重複題與試卷去重
- 可視化：`visual`、`feature_tags` 計數（Counter）

⇒ 結論：**平台端與輸出端（我們的產線）無須協調，只需照章**。我們只要把 package 產生對，
再用平台的匯入器乾（dry-run）驗證；分秒Record 由 asset_manifest 的 page/bbox/dpi/checksum 承載。

### 1.4 圖片題（放射類）與合同：native support 已存在

契約已明文規定 `stem_image`、`options[].image`、`explanation_image`、
`metadata.visual_profile{has_visual_asset, visual_review_status, asset_roles, asset_quality_statuses,
asset_count, feature_tags}`，且 `asset_manifest.jsonl` 「may include **optional page/bbox metadata**」。

⇒ 我們報告的重點：「裁切位置不正確」這個缺陷，在平台側**已經有可以容納它的欄位**，
過去只是沒有被填滿。修復方案是在 asset_manifest 每行寫入：

```json
{"asset": "assets/medrad/114/q017_option_c.png", "role": "option_image",
 "source_page": 6, "bbox": [78.1, 306.0, 561.3, 372.4], "render_dpi": 300,
 "derived_by": "image_bbox ∪ text_block_bbox ∪ bullet_strip", "sha256": "…",
 "quality_status": "awaiting_visual_review"}
```

如此平台的前端篩即可按「圖片題」「表格題」「選項圖」「圖片待確認」的分類（feature_tags）。

### 1.5 風險與阻礙（誠信，但必須告知）

1. **正式來源是 PostgreSQL**（「The external source of truth is PostgreSQL」）。本機（MacBook）
   與 AI395 目前都reach不到該庫，因此第一階段以 official PDF + 官方 metadata 直接產生 package，
   並在 `manifest.json` 記錄此權重（provenance：未經資料庫），待機（395/Studio）回線時再補。
2. 契約禁止「external absolute paths」進入 package —— 只准相對路徑（relative asset paths）。
3. `--public-eligible` 需要授權（licensing）；校內（school）與公開（public）是兩次不同的決策。

---

## 2. 取捨：100 以前 & OCR

| 項目 | 取捨 | 理由（實證） |
|---|---|---|
| 100 及以前（含 100） | **捨**：移入 `legacy_isolated/`，唯讀，不復更改，不入新流程、不分-package | 你已聲明；且實測顯示其 43% 從未成功（`ok`）且缺字多為 `PermissionError`；為不費（同樣成本），讓（省） | 
| 101–（含） | **取**：重建 | 官方 PDF 30/30 有文字層，無須 OCR 即可得到題 |
| MinerU 全文件 VLM | **捨**（作為預設路徑） | 121.9 s/份 vs 0.045 s/份；且 12/24 份產生簡體字（見 §3.1 PROPOSED_WORKFLOW） |
| 局部 OCR（region-level） | **取**：用進 | 三處必要：①無文字層之頁面 ②PUA/EUDC 列表符 ③圖片題之圖；皆宜準确 |
| 字符映射表（OCR_CHAR_MAP 等） | **捨** | 其「注意事項」與被修復對象同屬同一類缺陷 |
| 人工審核 | **取**，且為之樹（作為基準） | 見 §4 |

---

## 3. 題型偵測（item-type detection，你允許的改良）

判定的標準（依據 雙引擎的行，參考 題號的連續）：

| 偵測結果 | 依據 | 建議 `question_type` |
|---|---|---|
| `number_dot` + 每題 4 個 A–D | options 計數 = 4 | `single_choice` |
| `bare_number_line` + 0 options + 「甲、申論題部分」 | 章節標題關鍵字 | `constructed_response` |
| 同一 PDF 內兩者並存 | 混合卷 | 以 section 拆分，分别產生兩類 records |
| `判断`（√／×） | options 為二且 | `true_false` |
| 答案卡為 A/B/C/D 之類 | 答案 ∈ 選項 | 校驗 `answer ⊆ option keys` |

---

## 4. 優先科目試點計畫（依你的科系優先順序）

| 順序 | 科別 | 可用 PDF（10 目錄全載，實際以執行為準） | 試點目的 |
|---|---|---|---|
| 1 | 醫事檢驗師 | 該類 PDF 共 478 份（101+ 子集於執行時統計） | 用你已有的人工審核記錄，做「漏網之錯」的勘誤 |
| 2 | 藥師 | 227 份 | 同上（兩科並蒂） |
| 3 | 醫事放射師 | 462 份 | **圖片題**專項：裁切位置、渲染對比 |
| 4 | 物理治療師 | 已入樣（本次 30 份樣本內含 3 份 MCT） | 對照組 |
| 5 | 醫師 | 待統計 | 分組 |
| 6 | 中醫師 | 待統計 | 分段 |

### 4.1 用「人工審核的舊題」找出「漏」（不是用來当門檻）

你已有的兩科人工審核結果，是**資產**也是**基準**，但用法要轉換：

1. 把「已審核通過」的題目（含答案）當作 gold；
2. 用確定性規則（§2 PROPOSED_WORKFLOW）重新核對一遍 → 得到「機器檢查」；
3. 用 AI 獨立作答（§5 方法二）重新核對一遍 → 得到「智能檢查」；
4. **两者不一致者，記錄為「疑似」**，列入「復核清單」→ 這才是你要找的「漏網」。

> 重點：這不是把「相似度」当作「正確率」。**相似不等於正確**（你的原則，我方完全同意）。
> 因此不設 0.95 之類的全域門檻；相似度僅用於「分類」（同類題合併），不用於「判斷對錯」。

### 4.2 樣本規模（第一批次）

| 批次 | 科別 | 份數 | 題數（約） | 用途 |
|---|---|---|---|---|
| B1 | 醫事檢驗師 | 12 | 480 | 與人工審核結果對照，找出漏 |
| B2 | 藥師 | 8 | 320 | 同上 |
| B3 | 醫事放射師 | 10 | 400 | 圖片題裁切位置專項測試 |
| B4 | 對照組（100 及以前樣本，只讀） | 4 | 160 | 驗證「捨」的判斷是否正確 |

---

## 5. 「正確性」的檢查方法（不用相似度門檻）

### 方法零：兩項先決（比對之準，二件之紀）

比對之前，必先立二項；此二者，讀之紀也。非此，不能比；非canonical，不可以為一行之紀錄。
Two preconditions must be settled before any comparison is worth the paper it is printed on
（`src/qbr/canon.py`；其來龍之驗證、其失之修復，見 `reports/DEFECTS-AND-FIXES.md`）：

| 先決 | 內容 | 實作 |
|---|---|---|
| **一曰比**（何以謂同） | 全形折之（NFKC，只用於比對，不改紀錄）；佈局標記非本文——`<sub>`、`<sup>`、`<br>` 等標記，依目錄 whitelist 刪之；標記孤立者（`A<B` 之類）勿刪 | `canon.fold` / `canon.strip_markup` / `canon.norm` |
| **二曰對（讀的是同一題）** | 凡對題目，先校其題：一題之兩端，必以**兩位證人**（人工校訂之 stem 與機器擷取之 stem，取其最近者）；最近者，**不踰**《ALIGN_MIN》（0.90）則不為同，雖萬卷、皆準也，**誤則闕之**——其讀不可校者，謂之`quarantine`，不判 | `three_way.pick_item` / `canon.compare(aligned=…)` |

答案之準（the authority for an answer）：
- 只取官方**答案卷**（`_ANS.pdf`）；有**更正答案**（`_MOD.pdf`）者，**從更正**——更正之卷為
  重新刊印之全表，非僅勘正之補，故凡更正所載，每條皆準，其與舊答案異者，以更正為正；
- **不可**就題目文字「extract_answer」推其事（由題幹內文擷答案）——此以前誤之根源也；
- 答案卷之「題號」與更正答案之「題序」，二者異（用字不同），事事相反（同一題之答案），
  兩句之內，皆須明也：**這兩個詞都要在解析器裡明說**，否則更正之卷讀為空白（測得：12 份樣
  本中有 7 份如此）。

### 方法一：確定性門禁（機械檢查，0 token）

結構欄位校驗：題號連續（1..N 無缺號）、`len(options) == 4`、`answer ⊆ option keys`、
必填欄位非空、編碼字符檢查（簡體/PUA/兼容字符/全形）、閱讀順序（版面）。
⇒ 只能判「格式是否合法」，**不能**判「答案是否正確」。

### 方法二：AI 獨立作答（blind answering，主要正確性訊號）

```
給 模型：題目（題幹 + 四個選項），不给 它 看 標準答案
模型作答 + 寫出理由（explanation）
然 後 與 official answer 比對        （official = 答案卷，有更正答案者以更正為準；
                                     且必先：所比對者，同一題也 — 見上「二曰對」）
    相同 → 通過（但仍有假陰，仍需抽檢：抽樣 10% 覆核）
    不同 → 疑似錯誤 → 人工複核（三選二必，三分為：(a) 題目錯字 (b) 官方答案有誤 (c) 模型答錯）
```

為什麼這比「相似度門禁」更可靠：模型看不到標準答案，就無法「抄襲」；
若它的答案與官方答案不同，必定是其中有一個（或多）個環節出了差錯。

配套的三個現代化（技術）：
1. **雙引擎覆核**：PyMuPDF 與 poppler 兩端比對，若有出入，則对该 region 作「局部 OCR」再覆核；
2. **渲染對比**：把原圖 + 裁切後的圖 + 選項文字一起給模型看，讓它說明「圖文」（Image & Text）；
   並回答：裁切是否完整、有無漏掉、順序是否正確；
3. **舉例**：同題再舉（同一題以不同來源/年份重覆），若出現兩個以上版本，以官方最新版本為準。

### 方法三：抽樣與統計（100% 機械檢查 + 抽樣智能檢查）

| 層次 | 覆蓋率 | 手段 | 成本 |
|---|---|---|---|
| 全數（機械檢查） | 100% | 方法一 | 0 token |
| 抽樣（AI 獨立作答） | 新入庫 100%；存量復核 10–20% | 方法二 | 以「我自身模型」為能力基準，記錄 cost/題 |
| 復核（人工） | 疑似 + 抽檢不合格者 | 人工 | 只處理「疑似清單」 |

未來你會有「本地模型」與「便宜開源模型」，届时用**同一基準**（同一份 gold set、同一套指標）
重新評估；本方案先把「計」的定義與「量」的單位統一。

---

## 6. 需要你確認（三項）

1. **人工審核結果存放在哪裡？** 我在本機找到（只讀）：
   - `國考題資料夾/Registry/remote_review_sync/20260720-134018/bundle/`
     （`question_candidates.jsonl`, `question_parse_issues.csv`, `pair_index.csv`, `mineru_results.csv`, `artifact_manifest.csv`）
   - `國考題資料夾/30_normalized_items/question_candidates/{24 個時間戳目錄, ai_audit_runs, codex_audit_tasks, chatgpt_mcp_audit_tasks, subject_codex_audit_tasks}`
   - `40_ready_for_ingest/{staged_candidates, validated_pairs}`：**空**
   - `50_ingested_snapshots/`：**空**
   
   ⇒ 若你的人工審核記錄（医检、药师两科的「已审核」）主要在 **PostgreSQL**（Mac Studio / AI395），
   請告知，我目前接觸不到、到處都找不到（395 目前不可用）。也可選擇：直接把 review UI 匯出的
   CSV/JSON 放進 `pi_test/question_bank_rebuild/data/`（副本），我即可對照。
2. **題型的判定**：是否同意把 `constructed_response`（申論題）與 `mixed`（混合卷）納入
   `question_type` 的取值範圍？平台契約未規定枚舉，`question_type` 只寫了 `"single_choice"` 一例。
3. **第一批次**：是否同意 §4.2 的樣本規模（B1 医檢 12 份、B2 薬師 8 份、B3 放射 10 份）？
