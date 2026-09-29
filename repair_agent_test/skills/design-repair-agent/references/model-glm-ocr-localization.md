# GLM-OCR 閘門實驗：**圖片定位**（不是文字抽取）

> 執行日 **2026-09-27**，本機 Apple M5 Max。業主指示：「我參考這個模型的目的不是為了讓它抽取文字，
> 而是嘗試**精準界定考題中的圖片定位**」，且「先看讀取狀況，不要大規模送資料」。
> 這一輪共量 **11 個有圖頁 ＋ 9 個無圖頁（負對照）**，只讀本機 PDF，未送出內網。

---

## 0. 一句話結論（先講，因為它改變了整個問題）

**GLM-OCR 的語言模型本身沒有 bbox 能力——它一個框都吐不出來。**
真正的定位能力來自它 SDK 裡另一顆模型：**`PP-DocLayout-V3`（版面偵測器）**。
**那一顆才是你想要的東西**，而且**不需要 GLM-OCR、不需要 torch、不必進 SDK**就能單獨跑。

| 你可能以為 | 實測 |
|---|---|
| GLM-OCR 會輸出圖片座標 | ❌ 自由提示詞被**完全忽略**，只照固定三種提示詞轉錄文字 |
| 要裝 GLM-OCR SDK 才有定位 | ❌ 定位在 `PPDocLayoutDetector`，**一顆 138 MB 的 ONNX 模型**即可 |
| 要 GPU／torch | ❌ **onnxruntime CPU** 就夠（本機 `~/models/venvs/omlxenv` 已有 `onnxruntime 1.30.0`） |

**證據**（同一張有圖頁，換三種提示詞）：
`"Text Recognition:"` / `"請輸出圖中所有圖形區域的座標"` / `"Locate the figure:"`
→ 三次輸出**一模一樣的文字轉錄**，**沒有任何座標**。

---

## 1. 做了什麼

| 步驟 | 結果 |
|---|---|
| 下載 `mlx-community/GLM-OCR-{4bit,8bit,bf16}` | `~/models/glm-ocr/`（1.2／1.5／2.1 GB） |
| 跑精度實驗（4bit vs 8bit vs bf16） | 見 §2 |
| 讀 GLM-OCR SDK 原始碼 | 定位靠 `glmocr/layout/layout_detector.py::PPDocLayoutDetector` |
| 下載 `Bei0001/PP-DocLayoutV3-ONNX` | `~/models/glm-ocr/sdk-weights/pp-doclayoutv3-onnx/`（**138 MB**） |
| 寫 torch-free ONNX 推論器 | `probes/run_pp_doclayout_onnx.py` |
| 寫定位量測器 | `probes/score_figure_localization.py` |
| 量 2 份卷、11 有圖頁 ＋ 9 無圖頁 | 見 §4 |

---

## 2. 精度實驗（閘門）：**精度救不了繁簡混淆**

同一頁（數位原生 page-3），只換權重精度：

| 精度 | 字元相似度 | 漢字錯誤 |
|---|---:|---|
| **4bit** | 0.8734 | `氩`→`氫` ×8、`爲`→`為` ×4、`訳`→`訊` ×2 |
| **8bit** | 0.9082 | `爲`→`為` ×3、`氩`→`氫` ×2 |
| **bf16** | 0.9061 | `爲`→`為` ×3、`氩`→`氫` ×2 |

**量到的**：8bit 與 bf16 **明顯優於 4bit（錯誤減半以上）**，但 **8bit 與 bf16 幾乎一樣**
（相似度 0.908 vs 0.906，錯誤數完全相同）。

**判讀（我的判斷，非事實）**：
- 量化確實傷害字型辨識，**但換到 8bit 就到頂了，再上 bf16 沒有回報**。
- **`爲`／`氩` 這類錯誤在 bf16 仍然存在** → **不是量化問題，是模型本身對繁體字形沒有知識**
  （語言清單只有 `zh`，沒有繁體）。**這個閘門換精度救不回來。**
- **對本案的意義**：你要的是**圖片定位**，不是文字——**這個閘門對定位完全不相關**。
  定位走 PP-DocLayout-V3，它輸出的是**框**，不是字。**原本以為的擋路石，在定位這條路上不存在。**

---

## 3. 定位能力拆解

### GLM-OCR 語言模型：**0 個框**

- 模型卡「Prompt Limited」：只認 `Text Recognition:`／`Formula Recognition:`／`Table Recognition:`。
- 實測自由提示詞（要座標、要 locate）**被忽略**，輸出與 `Text Recognition:` 完全相同。
- `mlx_vlm/models/glm_ocr/` 原始碼**沒有任何 grounding／bbox 程式碼**（grep 0 命中）。
- 它輸出的是 LaTeX 文字（`$\varepsilon$`、`$\textcircled{1}$`），不是 JSON 框。

### `PP-DocLayout-V3`：**25 類版面偵測器，會出框**

SDK 的 `glmocr/layout/layout_detector.py` 用它；`inference.yml` 的 `label_list` 有 25 類：

```
abstract · algorithm · aside_text · chart · content · display_formula · doc_title
figure_title · footer · footer_image · footnote · formula_number · header · header_image
image · inline_formula · number · paragraph_title · reference · reference_content
seal · table · text · vertical_text · vision_footnote
```

**對本案直接相關的類別**：**`image`／`chart`／`table`**（圖、圖表、表格），
另有 `figure_title`（圖說）、`header_image`／`footer_image`（頁眉頁腳圖）、`seal`（印章）。

**輸出四顆張量**：`logits`（300×25 類別分數）、`pred_boxes`（300×4，**正規化 cxcywh**）、
`out_masks`（實例分割遮罩，可轉多邊形）、`order_logits`（**閱讀順序**）。

⚠️ **一個實作陷阱（我踩到並修好）**：官方 `post_process_object_detection` **不對 box 做 softmax**，
直接吃 `pred_boxes`。我一開始多做 softmax，框全錯、跟已知的圖完全對不上。**已修正。**

---

## 4. 定位量測結果

### 4.1 量測方法（為什麼用「覆蓋率／純度」而不是單框 IoU）

Ground truth ＝ **`pdftohtml -xml -zoom 1.0`** 報的**PDF 實際繪製的圖片物件位置**（PDF points）。
這是**獨立引擎**（不是我們的 parser、不是被測模型），所以**不循環**。

但 GT 的**形狀取決於 PDF 作者怎麼畫**，實測兩種：

| 情形 | 例子 | 結果 |
|---|---|---|
| 一張圖畫成**相接的橫條** | 1152 卷 page 9：一張圖＝**5 條 462×34pt** | 合併後是一個真區域 |
| 兩張照片**並排**，但畫成**全寬橫條** | 1152 卷 page 16：**7 條全寬橫條** | GT **無法分開兩張**，但偵測器**正確地分成兩個**（對兩張視覺區域 IoU **0.92／0.87**） |

→ **單框 IoU 會因為作者的畫法而獎懲偵測器。** 改用兩個面積指標：

- **coverage ＝ 被預測覆蓋的 GT 面積 ÷ GT 總面積** —— *有沒有找到*
- **purity ＝ 落在 GT 內的預測面積 ÷ 預測總面積** —— *框有沒有節制*（防止一個大框吞掉整頁）

兩者都容忍「切開」；purity 懲罰「亂框」。單框 IoU 仍附上作次要數字。

### 4.2 有圖頁（11 頁，兩份卷）

| 卷 | 頁 | GT 圖數 | 預測框數 | **coverage** | **purity** | 單框 IoU |
|---|---|---:|---:|---:|---:|---:|
| 1152 物理治療基礎學 | 6 | 1 | 1 | 0.791 | **1.000** | 0.789 |
| | 9 | 1 | 3 | 0.675 | **1.000** | 0.233 |
| | 11 | 1 | 1 | 0.838 | **1.000** | 0.841 |
| | 12 | 1 | 1 | **0.972** | 0.997 | **0.970** |
| | 13 | 1 | 1 | **0.961** | **1.000** | **0.960** |
| | 16 | 1 | 2 | **0.965** | 0.998 | 0.491（GT 限制） |
| 1072 藥理學與藥物化學 | 4 | 1 | 1 | 0.897 | **1.000** | 0.897 |
| | 7 | 1 | 1 | 0.814 | **1.000** | 0.814 |
| | 9 | 1 | 3 | **0.947** | 0.961 | 0.339 |
| | 10 | 2 | 2 | 0.690 | 0.972 | **0.943** |
| | 13 | 1 | 4 | 0.673 | 0.967 | 0.250 |
| **合計** | **11** | **12** | — | **mean 0.838** | **mean 0.990** | — |

### 4.3 負對照：無圖頁（**9 頁，必須 0 誤框**）

1152 卷的 1,2,3,4,5,7,8,10,15 頁（`pdfimages` 確認無圖片物件）：

**→ 9 頁全部 `predicted_figures: 0`。零誤報。**

這是這一輪**最乾淨的一筆**：它**不會把純文字頁說成有圖**。

### 4.4 讀到的三件事

1. **純度 0.99**：它幾乎不框到圖以外的東西。**它不會亂框。**（這正是「界定圖片定位」最需要的性質。）
2. **覆蓋率 0.84**：它少框了一部分。缺的部分有**固定形狀** —— 一頁多區時它把一張圖**切成 3–4 塊**，
   留下縫隙（page 9／13）。**這是設計使然**：SDK 就是「逐區域辨識」，切開是它的工作方式。
3. **單框 IoU 有兩筆很低（0.233／0.250）**，但**同一頁的 coverage 是 0.675／0.673** → 佐證
   **是「切開」不是「框錯」**。這正是為什麼要用面積指標。

---

## 5. 與現行 qbr 的比較（**一筆，不是系統性比較**）

1152 卷 page 6（已知圖在 `[45.3, 178.7, 301.3, 576.7]` pt）：

| 來源 | 框 | IoU |
|---|---|---:|
| **PP-DocLayout-V3** | `[86.1, 183.0, 294.5, 568.9]` | **0.789** |
| qbr 候選檔存的框 | `[28.4, 144.8, 561.8, 682.4]` | **0.355** |

**這一頁上 PP-DocLayout 明顯更貼。** 但**只有一筆**，**不可外推**成「它比現行好」。

⚠️ **兩者的框不是同一種東西**：qbr 存的是「**這一題的圖**」（含題幹範圍），
PP-DocLayout 給的是「**這一頁的圖片物件**」。**要比較必須先對齊語義**，這一輪沒做。

---

## 6. ⚠️ 尚未解 / 尚未量（**不要當已知**）

1. **`pdftohtml` GT 對「掃描件」不適用**：掃描 PDF **沒有圖片物件**，整頁是**一張大 bitmap**
   → `pdftohtml` 會報「整頁一張圖」，**無法當定位 GT**。
   **掃描件的定位要另立 GT**（人工標或另一套方法）。**本輪完全沒量掃描件的定位。**
2. **表格（`table`）類別沒量**：本輪的圖都是 `image` 類。**表格定位完全未量。**
3. **`--threshold` 未調**：固定 0.3（SDK 預設）。**不同門檻的 precision/recall 曲線沒量。**
4. **DPI 未掃**：固定 150。低 DPI 是否影響小圖偵測**未量**。
5. **只有 2 份卷**：**11 頁有圖 ＋ 9 頁無圖**，**樣本小**。
6. **`order_logits`（閱讀順序）完全沒用**：它可能對「這張圖屬於哪一題」有幫助。**未量。**
7. **`out_masks`（多邊形）沒用**：只用了矩形框。
8. **沒和其他版面偵測器比**：MinerU 自己也有 layout。**未比。**（比了才是「兩引擎」。）

---

## 7. 我的建議

### 7.1 對「圖片定位」這件事

**GLM-OCR 語言模型對圖片定位沒有用；`PP-DocLayout-V3` 有。** 建議路線：

1. **把 `PP-DocLayout-V3` 當獨立工具**（不必進 GLM-OCR SDK，不必 GLM-OCR 權重）——
   **138 MB、CPU 可跑、純度 0.99**。
2. **下一步該量的是它真正對上的問題**，而不是繼續跑同一批：
   - **表格定位**（`table` 類）—— 本案表格截圖只有 15 張。
   - **掃描件的定位**（另立 GT）。
   - **「這張圖屬於哪一題」**（用 `order_logits` ＋ 題號位置）—— **這才是「界定考題中的圖片定位」的真問題**。
3. **`--threshold` 與多邊形**：若要更貼，值得調門檻與改用 `out_masks` 的多邊形。
4. **與 MinerU 的 layout 對比** —— 這才是「兩引擎一致才是驗收」的正確用法。

### 7.2 對 GLM-OCR 本身

- **文字抽取的繁簡閘門仍在**，且**換精度救不回來**（bf16 仍錯）。
  若哪天要用它的文字，必須後處理正規化，或**只當第二意見**。
- **但它對「圖片定位」完全無關** —— 你原本擔心的閘門，在你要的這條路上**不存在**。

### 7.3 治理

- 全部 **G0 讀**：只讀本機 PDF、只寫 `~/models/glm-ocr/runs/` 與 `repair_agent_test/probes/`。
- **未動任何主線程式、未部署、未寫任何 writer。**
- 這批是**量測**，不是採用決定。

---

## 附：可重現指令

```bash
# 定位（torch-free，CPU）
~/models/venvs/omlxenv/bin/python repair_agent_test/probes/run_pp_doclayout_onnx.py \
    --image /tmp/page.png --threshold 0.3

# 定位量測（coverage / purity，對 pdftohtml 的 PDF 圖片物件）
~/models/venvs/omlxenv/bin/python repair_agent_test/probes/score_figure_localization.py \
    --paper PAPER.pdf --pages 6,9,11,12,13,16 --json out.json

# 純文字（GLM-OCR 本體）
~/models/venvs/mlxenv/bin/python -m mlx_vlm.generate \
    --model ~/models/glm-ocr/GLM-OCR-8bit --image PAGE.png \
    --prompt "Text Recognition:" --max-tokens 8192 --temperature 0
```
