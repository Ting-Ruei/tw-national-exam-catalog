# Parser 與醫事檢驗師規則總表

這份文件是規則的導覽，不取代程式本身。規則分成三層：

1. **Parser deterministic rules**：可以由文字結構明確判斷，直接產生 candidate 或 issue。
2. **OCR normalization rules**：確認過的字形修正；通用規則在 parser，新增的通用／類科／科目詞組在 JSON registry。
3. **AI / human advisory rules**：需要看語意、PDF 或圖片，不能自動改成人工通過。

## 規則來源與優先順序

| 層級 | 來源 | 用途 | 是否可直接改題目 |
|---|---|---|---|
| 1 | [build_question_candidates_from_mineru.py](/Users/tim/tw-national-exam-catalog/scripts/build_question_candidates_from_mineru.py) | 題號、選項、表頭、題組候選、圖片資產、固定題數與結構 issue | 只做確定的 parser normalization；結構錯誤要保留 issue |
| 2 | [text_normalization_rules.json](/Users/tim/tw-national-exam-catalog/configs/text_normalization_rules.json) | 可追蹤的通用／類科／科目詞組修正 | 需先對照 PDF，再由 repair script 追加 SQL event |
| 3 | [core-rules.md](/Users/tim/tw-national-exam-catalog/docs/skills/national-exam-ai-audit/references/core-rules.md) | 所有科目的 AI advisory 標準 | 不可單獨改人工狀態 |
| 4 | [subject-overrides.md](/Users/tim/tw-national-exam-catalog/docs/skills/national-exam-ai-audit/references/subject-overrides.md) | 醫事檢驗師、藥師等科目的特殊風險 | 只產生提示或建議修正 |
| 5 | [ocr-correction-inbox.md](/Users/tim/tw-national-exam-catalog/docs/ocr-correction-inbox.md) | 人工逐步新增的發現 | `new` 尚未生效，確認後才轉入 registry |

## 一、既有通用 Parser 規則

### 題號與切題

- 民國 106 年起優先辨識 `1.`、`1、`、`1．` 等現代格式。
- 民國 105 年以前保留 `1 題幹` 等早期格式。
- 小數例如 `1.7` 不應被當成題號。
- 若現代格式疑似被 PDF 表頭吞掉，且解析結果過少，才使用 legacy fallback。
- 題號等於年度時，若同時含 `類科名稱`、`科目名稱`、`考試時間`、`座號` 等表頭訊號，不產生題目。

### 選項與切錯防護

- 支援 `(A)`、`（A）`、`A.`、`A、`、部分 LaTeX 包裝及少數 `A-` 格式。
- 選項依標籤排序，但保留原始順序供 issue 比對。
- 少於 4 個選項：`too_few_options`。
- 重複標籤：`duplicate_option_label`。
- 出現 `A B C D A B C D` 或 `A A B B C C D D`：另外標記 `merged_next_question_suspect`，不能只修目前題，必須回 PDF 切出下一題。
- 原始順序異常：`option_order_unusual`，不直接判定內容錯誤。

### 題組候選

Parser 只用明確格式產生候選：

- `第 7 至 9 題`
- `7-9 ...`
- `回答下列 3 題`
- `回答下列三題`、`作答以下兩題`（中文小寫數字二至二十）

`下列資料`、`以下資料`、`依據下列資料` 單獨出現不會自動判定為題組；真正題組仍由題組審核確認範圍、順序、共同題幹與 `group_type`。

`承上題`、`呈上題`、`上題`、`前述` 只建立待人工確認的連續題組候選，不會自動確認題組。自動工作流會檢查這些 continuation marker 是否有前題錨點；找不到時停止 SQL 匯入。

### 題號緊接年齡

考選部題目常寫成 `44.45 歲男性…` 或 `21.3 週齡白肉雞…`，其語意是「第 44 題，45 歲」與「第 21 題，3 週齡」，不是小數。Parser 只有在小數點後數字緊接 `歲`、`日齡`、`週齡`、`周齡`、`月齡` 或英文 year-old 時才接受此格式；一般 `1.7 3.4` 數值仍不可當成題號。

### 圖片、表格與公式

- 題幹出現 `如圖`、`箭頭`、`影像`、`表中`、`下表` 等訊號但沒有資產，產生圖片依賴提示。
- Markdown 圖片不存在或大小為 0，產生 error。
- 表格可保留結構化文字，但重要視覺版面要使用 `manual_assets`，不能修改 MinerU 原始輸出。
- 公式、上下標、希臘字母、LaTeX / HTML markup 會產生 `markup_needs_review` 或保存 `markup_payload`。

### 醫事檢驗師固定題數

- 類科為 `醫事檢驗師` 時，每份試題預期題號 `1-80`。
- 缺題：`fixed_exam_question_count_missing`，severity `blocked`。
- 超出 `1-80`：`fixed_exam_question_count_out_of_range`。
- 題號重複與題號缺口另外記錄，不以「看起來有 80 題」取代檢查。

## 二、目前 Parser 已有的通用 OCR 字形規則

以下是 [parser 的 OCR_CHAR_MAP](/Users/tim/tw-national-exam-catalog/scripts/build_question_candidates_from_mineru.py:56) 目前直接生效的單字修正。它們不是人工審核通過，也不會刪除原始 PDF：

| OCR | 正規化 | OCR | 正規化 |
|---|---|---|---|
| `锌` | `鋅` | `须` | `須` |
| `羟` | `羥` | `钙` | `鈣` |
| `锰` | `錳` | `镁` | `鎂` |
| `减` | `減` | `剂` | `劑` |
| `内` | `內` | `麦` / `麸` | `麩` |
| `黄` | `黃` | `状` | `狀` |
| `肠` | `腸` | `岛` | `島` |
| `题` | `題` | `脱` | `脫` |
| `氢` | `氫` | `铵` | `銨` |
| `巯` | `巰` | `则` | `則` |
| `恶` | `惡` | `溃` | `潰` |
| `疡` | `瘍` | `鳞` | `鱗` |
| `静` | `靜` | `匀` | `勻` |
| `婴` | `嬰` | `脏` | `臟` |
| `肾` | `腎` | `鉯` | `鈀` |

目前直接生效的詞組規則位於同一個 parser 檔案的 `OCR_PHRASE_MAP`，包括鎂、睪固酮、二氫、菸鹼酸、核黃素、鈷胺素、厭氧、鸚鵡熱、麩胺酸、纈胺酸、參考、standard、鈥、銫、鈷、釓、氫離子與上腔靜脈等 OCR 修正。新確認的類科規則不再散落在這裡，而是集中放入 JSON registry。

## 三、醫事檢驗師專用規則

完整細節在 [subject-overrides.md](/Users/tim/tw-national-exam-catalog/docs/skills/national-exam-ai-audit/references/subject-overrides.md)。目前已整理：

### 生物化學與臨床生化學

- 檢查 `α`、`β`、`γ`、`δ` 及 `α1`、`β2`、`γ-GT`、`γ麩胺醯` 等黏連或分離錯誤。
- 英文胺基酸是 OCR 錨點，例如 `valine`、`glutamine`、`tyrosine`、`phenylalanine`。
- 英文存在但附近中文譯名不合理，產生 `amino_acid_translation_suspect`。
- `麸` 視為疑似錯字，建議 `麩`；`酶` 是正體字，不應標錯。
- 保留 `mg/dL`、`μg/dL`、`mmol/L`、`IU/L`、`pH` 等單位。
- 表格、檢測數值、多面板圖、電泳圖需走圖片／表格審核。

### 微生物學與臨床微生物學

- 拉丁學名本身不是錯字，不因為是英文就標記。
- 細菌、黴菌、培養條件比較表需保留表格結構。
- 學名被拆到選項或題幹邊界時，標記 `option_structure`。
- 只有真正的符號損壞才標 `notation_markup`。

### 臨床血液學與血庫學

- 血型抗原上標：`Fyᵃ`、`Jkᵇ`、`Leᵃ`、`Luᵃ`、`Diᵃ`、`Miᵃ`、`Kpᵃ`。
- ABO 與 Bombay phenotype 下標：`A₁`、`A₂`、`Oₕ`。
- `Fy a`、`A 1`、`O_h` 等在血型脈絡下需標 `notation_markup`。
- 直接排版修正仍須人工確認，不自動 accept。

### 其他醫事檢驗師科目

臨床生理與病理、醫學分子檢驗與臨床鏡檢、臨床血清免疫與病毒等，目前共用核心規則與 generic image-heavy 規則；只有反覆出現且能明確定義的錯誤，才新增 subject override，避免再次造成大量誤報。

## 四、藥師近期確認規則

本次人工 block 註記集中在 `藥師(二)` 的藥物治療學、調劑學與臨床藥學。已加入 parser regression test 並套用至本次 29 題，原本狀態仍為 `block`：

- `Cl Cr`、`ClCr`、`CLcr` -> `CL<sub>Cr</sub>`。
- `SCr`、`Scr` -> `S<sub>Cr</sub>`。
- `FE Na` -> `FE<sub>Na</sub>`。
- `ER H` -> `ER<sub>H</sub>`。
- `Ctrough` -> `C<sub>trough</sub>`。
- `CSS` -> `C<sub>SS</sub>`。
- `Vd` -> `V<sub>d</sub>`。
- 藥動學縮寫的下標：`KM`、`K_M`、`K_{M}` -> `K<sub>M</sub>`；`Vmax`、
  `V_{max}` -> `V<sub>max</sub>`；`ka`、`ke`、`Vp`、`VD` 及其底線形式分別
  -> `k<sub>a</sub>`、`k<sub>e</sub>`、`V<sub>p</sub>`、`V<sub>D</sub>`。
- 由藥師人工註記確認的完整藥動學 token：`Cmax`、`tmax`、`Cp`、`Ccr`、`Css`、
  `Clcr`、`fe`、`fu`、`D0`、`DL`、`Rin`、`MW dextrose`、`Ksp` 與 `Du∞`。
  分別保存為 `C<sub>max</sub>`、`t<sub>max</sub>`、`C<sub>p</sub>`、
  `C<sub>cr</sub>`、`C<sub>SS</sub>`、`CL<sub>Cr</sub>`、`f<sub>e</sub>`、
  `f<sub>u</sub>`、`D₀`、`D<sub>L</sub>`、`R<sub>in</sub>`、
  `MW<sub>dextrose</sub>`、`K<sub>sp</sub>` 與 `D<sub>u</sub>∞`。
- `Vss`、`V ss`、`V_{ss}`、`Vexp`、`V exp`、`V_{exp}` 依完整藥動學 token
  分別保存為 `V<sub>ss</sub>`、`V<sub>exp</sub>`；不會改寫較長的普通單字。
- `Co` 只有在同一題有「初濃度／initial concentration」語境時才轉成 `C₀`；
  單獨的 `Co` 仍保留為化學元素鈷。還原電位語境的 `Eo/E0` 才轉成上標 `E⁰`。
- 數值後的 `~h⁻¹`／`～h⁻¹`、`~hr`／`～hr`、`~L`／`～L` 僅在完整單位脈絡中清理波浪號；公式中的括號與大括號
  不會因為 token 正規化被刪除。
- `HbA1C`、`HbA₁C` -> `HbA₁<sub>C</sub>`。
- 數值與單位間的 `\\ `、單位前的 `~`／`～`、`\\%` 會在已知單位脈絡下清理。
- `数` -> `數`。

這些是顯示與 OCR 結構修正，不是答案判斷；`block` / `needs_review` 題仍須由人工重新按通過。
若修正實際改動已 `accept` 的文字，SQL 會追加 `reset_review`，讓人工重新確認；若 PDF
顯示和上述規則不同，應把該題列為例外，不擴大 regex。

## 五、你之後要補哪裡

- 發現一個具體錯字：填 [ocr-correction-inbox.md](/Users/tim/tw-national-exam-catalog/docs/ocr-correction-inbox.md)。
- 發現一種 parser 結構錯誤：告訴我年份、考次、題號與 PDF 現象，我會修改 parser 並補 regression test。
- 發現一整科反覆問題：在 `subject-overrides.md` 增加最小範圍的科目規則，再只重掃受影響題目。
- 只看過但不確定：保留 `new`，不放進 active JSON。

原則是：**PDF 是唯一真實來源，Markdown 是解析證據，SQL review event 是人工決定的歷史。**
