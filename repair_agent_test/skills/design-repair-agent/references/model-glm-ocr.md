# GLM-OCR — 模型參考（可用候選，尚未部署）

> 調查日 **2026-09-27**。資料來源：Hugging Face 模型卡／`config.json`／檔案清單、arXiv 技術報告
> （`2603.10910`）、HF API 搜尋結果。**本機尚未下載、尚未對本案語料實測。**
> 本檔只記「量到的事實」與「我的判斷（標明非事實）」。

## 1. 一句話

**`zai-org/GLM-OCR`：0.9B 參數的專用文件 OCR 模型。** 不是通用聊天模型，
是**轉錄引擎**——看文件圖，吐出文字／公式／表格／結構化 JSON。

---

## 2. 硬事實

| 項目 | 值 | 來源 |
|---|---|---|
| 模型 id | `zai-org/GLM-OCR` | HF |
| 授權 | **MIT**（模型本身）；完整管線含 `PP-DocLayoutV3`（Apache 2.0） | README |
| pipeline tag | `image-to-text`，`library_name: transformers` | HF API |
| 架構 | `GlmOcrForConditionalGeneration`，`model_type: glm_ocr` | `config.json` |
| 參數量 | **0.9B**（CogViT 視覺編碼器 **0.4B** ＋ GLM 語言解碼器 **0.5B**） | README／arXiv |
| 權重檔 | `model.safetensors` **2.65 GB**（bf16） | HF blobs |
| 上下文 | `max_position_embeddings` **131,072** | `config.json` |
| 語言 | **zh, en, fr, es, ru, de, ja, ko**（**繁中未明列**，`zh` 應涵蓋但**未量**） | README |
| 建立／最後更新 | 2026-01-30 建立；2026-09-11 更新 | HF API |
| 熱度 | downloads **1,759,378**；likes **2,088** | HF API |
| 技術報告 | arXiv `2603.10910`「GLM-OCR Technical Report」 | README |
| 官方 SDK | `github.com/zai-org/GLM-OCR`（**建議文件解析走 SDK**，它整合 PP-DocLayoutV3） | README |

**架構細節（`config.json`）**
- 視覺：`hidden_size 1024`、`depth 24`、`image_size 336`、`patch_size 14`、`spatial_merge_size 2`、`out_hidden_size 1536`。
- 文字：`hidden_size 1536`、`num_hidden_layers 16`、`num_attention_heads 16`、`num_key_value_heads 8`、`vocab_size 59392`、`num_nextn_predict_layers 1`（**MTP：每步預測多個 token**）。
- `transformers_version: 5.17.0`。

---

## 3. 公佈的分數（**別人的基準，不是我們的語料**）

| Benchmark | 分數 | 備註 |
|---|---:|---|
| **OmniDocBench V1.5** | **94.62** | 官方稱**總排名第一** |
| olmOCR-bench overall | 75.2 | **排除 Headers & Footers**；用 ZAI 雲端 API 評 |
| olmOCR-bench `table_tests` | 77.6 | |
| olmOCR-bench `multi_column` | 76.7 | |
| olmOCR-bench `arxiv_math` | 80.7 | |
| olmOCR-bench `old_scans` | **37.6** | ⚠️ **老掃描最弱** |
| olmOCR-bench `old_scans_math` | 68.3 | |
| MDPBench overall | 67.3 | |
| MDPBench `digital` | 77.9 | 原生數位檔最好 |
| MDPBench `photographed` | 63.7 | |
| MDPBench `latin` / `en` / `de` | 78.7 / 84.5 / 82.7 | |

**速度（官方，單 replica、單併發、其自家硬體）**：**PDF 1.86 頁/秒**；**圖片 0.67 張/秒**。

---

## 4. 使用方式（**重要限制：Prompt Limited**）

模型卡標題就寫 **「Prompt Limited」**——它**不吃自由格式指令**，只支援兩類提示詞：

**(1) 文件解析（Document Parsing）** —— 三個固定提示詞：
```
"Text Recognition:"     → 純文字轉錄
"Formula Recognition:"  → 公式
"Table Recognition:"    → 表格
```

**(2) 資訊抽取（Information Extraction）** —— 提示詞須為**嚴格 JSON schema**，輸出必須完全符合該 schema。
模型卡範例（身分證欄位）用**簡體中文**寫提示詞。

⚠️ **它不會聽「請找出這題的錯誤」這種話。** 那不是它的工作。

### 部署支援

| 途徑 | 狀態 | 本機現況 |
|---|---|---|
| **llama.cpp / GGUF** | ✅ `llama-server -hf ggml-org/GLM-OCR-GGUF` | **本機沒裝 llama.cpp** |
| **MLX（Apple Silicon）** | ✅ `mlx-community/GLM-OCR-{4,5,6,8}bit`、`bf16`、`EZCon/*` | **本機無 `mlx_vlm`**（只有 `mlx 0.32.1`） |
| **Ollama** | README 聲稱支援，但 `registry.ollama.ai/v2/library/glm-ocr` 回 **404**（官方 registry 無此 tag） | 本機 `ollama 0.34.0`（client 0.34.4） |
| vLLM / SGLang / Transformers | ✅ 官方支援 | 本機未裝 |

**GGUF 體積（`ggml-org/GLM-OCR-GGUF`）**：`f16` **1,785.8 MB**／`Q8_0` **950.4 MB**／`mmproj` **484.4 MB**。
（另有 `mradermacher/GLM-OCR-GGUF`、`unsloth/GLM-OCR`、`nopesadly/GLM-OCR-Q4_K_M.gguf` 等社群版本。）
**MLX 4bit**：`model.safetensors` **1,247.2 MB** ＋ tokenizer 6.8 MB ≈ **1.25 GB**。

**本機硬體**：Apple **M5 Max / 128 GB RAM**。→ 這個模型**輕到可在筆電常駐**（與 ornith-35b 並存無壓力）。

---

## 5. 與本案的關係（**我的判斷，非事實**）

### 5.1 它不能做什麼（先講清楚，避免期待錯位）

- **它不知道答案對不對、命題對不對。** 它是**轉錄器**，不是**判讀器**。本案「我找到錯、模型說沒對」
  的缺口（`ANSWER_DISAGREES` 被移除的那塊）**它一點也幫不上**。
- **它不吃自由提示詞。** 現行 `ai_findings.py::build_prompt` 那種長篇「你是審題助手…」**它讀不懂**。
  所以它**不能取代 ornith 的判讀角色**。
- **`old_scans` 只有 37.6。** 台灣國考早年 PDF 常是掃描件 → **這一項是本案最該先量的風險**。

### 5.2 它可能補上的位置（值得試）

| 本案的痛點 | GLM-OCR 可能的作用 |
|---|---|
| **抽取階段**（PDF → 文字層）過去靠 MinerU，現在走「確定性路徑」 | 提供**第三個獨立讀法**，直接對中 `qbr/AGENTS.md`「**兩引擎一致才是驗收**」 |
| `vision.py` 的**圖／表裁切**判讀 | `"Table Recognition:"` 直出結構化表格；現在表格截圖只 15 張、圖片 3,283 張 |
| 掃描老題的文字層品質 | 若在本案語料上 `old_scans` 表現可接受，可補現行路徑讀不動的頁 |
| 判讀成本 | 0.9B、1,247 MB（4bit），**可高併發**；ornith-35b 貴得多 |

### 5.3 我的建議順序（都是 G0 讀、可逆）

1. **先量它在「我們的」語料上的表現**，不要信公佈分數——依 `qbr/AGENTS.md` 的紀律，
   **別人的 benchmark 不是驗收**。
2. **量的題材要含負對照**：數位原生題、老掃描題、含表格題、含圖題各若干；
   負對照＝「已知正確答案的那一頁」。
3. **量繁中**：公佈語言清單**沒有繁體中文**；這是本案的母語題材，**必須自己量**。
4. 只有在 (1)–(3) 通過後，才討論要不要把「GLM-OCR 當第三個讀法」寫進抽取或驗收路徑。
5. **部署最小路徑**：MLX 4bit（1.25 GB，Apple Silicon 原生）或 llama.cpp `Q8_0 + mmproj`（約 1.43 GB）。
   **兩者本機都還沒裝** → 若要試，需先安裝（屬環境變更，需你點頭）。

### 5.4 治理對接

- **MIT 授權、完全地端**：**不觸發**「題目內容不出內網」的紅線。
- 它若進本案，身分應是**讀取工具（G0）**，不是 writer。**不得**讓它直接改題目（那是 G3）。
- `operate-local-open-models/SKILL.md` 目前只列 MTPLX／mlx-vlm 兩個引擎；**要把 GLM-OCR 列入，
  必須先補：端點、model、認證、資料邊界、rollback**（那份 skill 明文要求）。

---

## 6. 尚未量（**不要當已知**）

- 在**台灣國考 PDF** 上的繁中準確率。→ **已於 2026-09-27 量測，見 [`model-glm-ocr-measurement.md`](model-glm-ocr-measurement.md)：
  漢字數對得上紙本，但有**系統性繁簡／異體字混淆**（`爲`→`為` 31 次、`氩`→`氫` 9 次）。**這是採用閘門。**
- 老掃描件（`old_scans`）在本案語料的實際表現。→ **已量 1 頁**：無文字層仍讀得出來；與 MinerU **漢字一致率 1.0（413/413）**。
- 表格／公式／選項欄位的**結構化輸出**是否穩定到可對差。
- 1,247 MB MLX 4bit 與 2.65 GB bf16 的**品質落差**。
- **繁中＋全形標點＋選項編號 `(A)(B)(C)(D)`** 的表現（本案的關鍵格式）。
- 併發上限與吞吐（官方數字是單 replica 單併發）。
- 與 ornith-35b 在本案題目上的**一致性**（這才是「兩引擎」能不能成立的關鍵）。

## 6.1 本機狀態（2026-09-27）

**已安裝、已實測**，細節見 [`model-glm-ocr-measurement.md`](model-glm-ocr-measurement.md)：

| 項目 | 值 |
|---|---|
| 模型 | `mlx-community/GLM-OCR-4bit` @ `~/models/glm-ocr/GLM-OCR-4bit`（**1.2 GB**） |
| Runtime | `~/models/venvs/mlxenv/bin/python -m mlx_vlm`（**mlx_vlm 0.7.1 原生支援 `glm_ocr`，未新裝任何東西**） |
| 速度 | **4.3–6.2 秒/頁**（M5 Max，逐頁單呼叫）—— 官方 1.86 頁/秒是並行 SDK，不可直接比 |
| 準確度 | 漢字數幾乎精確等於紙本；**兩引擎（vs MinerU）漢字一致率 1.0** |
| ⚠️ 閘門 | **系統性繁簡／異體字混淆**：`爲`→`為`（31 次）、`氩`→`氫`（9 次）、`试`→`試`… |
| 副作用 | 輸出 **LaTeX**（`$\varepsilon$`、`$\textcircled{1}$`）；排版壓縮（`(A)(B)(C)(D)` 併行） |

**最重要結論**：它的輸出**不可以當 ground truth**（會製造假異體字缺陷，而 `apply_dispute_repairs` 會自動套用那一類），
**只能當「第二意見」**——它與 MinerU **不一致的地方**才值得人看。

---

## 7. 快速指令（**未執行，僅備忘**）

```bash
# MLX（需先：uv pip install mlx-vlm 或 pip install mlx-vlm）
mlx_vlm.generate --model mlx-community/GLM-OCR-4bit --image page.png --prompt "Text Recognition:"

# llama.cpp（需先安裝 llama.cpp）
llama-server -hf ggml-org/GLM-OCR-GGUF
```

**注意**：模型卡明講 **Prompt Limited**，提示詞只有
`Text Recognition:`／`Formula Recognition:`／`Table Recognition:` 與嚴格 JSON schema 兩類。
不要用自由指令測試它，那測的是「有沒有照你話做」，不是「它讀得多準」。
