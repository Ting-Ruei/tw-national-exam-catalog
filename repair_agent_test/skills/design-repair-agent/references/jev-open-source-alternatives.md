# Jev 的開源替代品：線上調查 ＋ 本機實測

**性質**：**研究報告**，唯讀，不改主線。
**日期**：2026-09-27。
**觸發**：業主指示「幫我上網找有沒有 **Jev** 的開源替代品」。
**紀律**：**星數 ≠ 品質**（我先做了 spam 負對照）；官網宣稱 vs **我實測到的**分開寫；未量的標「未量」。

---

## 0. 先回答一件業主可能不知道的事

> **「Jev」不是一個開源專案，它是 TypeSafe AI 的封閉雲端服務**（System One 模型，`/v1/systemone`）。

所以「Jev 的開源替代品」＝**整個生態在複製「Jev 的介面模式」**（typed decisions ＋ 校準機率 ＋ 一次前向傳遞），
**不是複製 Jev 那個模型**（它的權重沒公開）。幾乎每個專案都自己聲明：

> 「**Independent project; not affiliated with Jev or TypeSafe.**」

**→ 這對我們是好消息**：我們不需要 Jev，我們需要的是「**同一種介面**」——而那個介面已經被多個開源實作標準化了，連**線路格式都相容**：

```
任何現有 Jev client 只要改一個環境變數（base URL）就能接開源版
```

---

## 1. ⚠️ 先過濾雜訊：GitHub 上大部分「高星」都是 SEO spam

我搜「jev」在 GitHub 的結果，**前 15 名有 10 個是同一種東西**：

| repo | ★ | 問題 |
|---|---:|---|
| `yibie/awesome-jev` | 1,797 | **只有 1 筆提交、沒有 license** |
| `heyjunpenn/awesome-jev` | 864 | 只有 1 筆提交 |
| `Anil-matcha/awesome-jev-by-typesafe` | 862 | 只有 1 筆提交 |
| `v-modal/awesome-jev-tools` | 726 | 只有 1 筆提交、沒有 license |
| `kydlikebtc/awesome-jev` | 495 | 只有 1 筆提交 |
| `cobanov/awesome-jev` | 413 | 同上 |
| `AbdelStark/awesome-typesafe-jev` | 529 | 同上 |
| `AnotiaWang/awesome-jev` | 504 | 同上 |
| `valentynkit/awesome-jev-typesafe` | 168 | 同上 |

**負對照證據**（我實測）：這些 repo 的提交數**都只有 1 頁**、**都在 2026-09-17 前後同時建立**、**大部分沒有 license**、內容是「**916 個開源專案**」「**1207 個公開資源**」這種**灌水標題**。

**→ 結論：`awesome-*` 系列一律跳過。** 星數在這一波裡完全不能當訊號。

**真正的訊號在「第三方客觀排行榜」**，不是星數。

---

## 2. 真正的訊號：兩個客觀排行榜

### 2.1 JevBench（Benchmark Heaven，36 個條目）

| system | score | intelligence | calibration | speed | cost |
|---|---:|---:|---:|---:|---:|
| **Jev 1.13.0（TypeSafe，封閉，#1）** | **75.4** | 90.4 | 82.7 | 83.3 | 52.0 |
| SemIf（Qwen3.5-4B，#2） | 74.7 | 85.9 | 72.6 | 83.7 | 59.5 |
| **decider-35b-a3b（#10/36）** | 68.9 | 86.3 | 71.5 | 80.8 | 45.3 |
| **decider-2b（#21/36）** | 64.6 | 73.8 | **46.6** | 83.2 | 61.0 |

### 2.2 Decision Index（32 個條目、132,422 requests、37 benchmarks）

| system | score | rank |
|---|---:|---:|
| **Jev（封閉）** | **59.5** | 1 |
| jevfire（**零訓練 wrapper 套在 27B 上**） | 55.7 | 2 |
| joshua-diffusion（同上） | 55.6 | 3 |
| **decider-35b-a3b（NVFP4）** | **54.3** | **4** |
| decider-2b | 44.0 | 14 |

**這兩張表最重要的兩個發現**：

1. **差距是存在的但不大**：最好的開源訓練模型（decider-35b-a3b）在 Decision Index 排 **第 4/32**，只輸 Jev 5.2 分。
2. **⭐ 第 2、3 名是「零訓練 wrapper」**——這對我們的架構有**直接的用途**（見 §5）。

---

## 3. 🎯 我本機實測：最值得試的是 `Jev-Style`（0.8B, MLX）

### 為什麼選它

| 需求 | Jev-Style 0.8B v3 |
|---|---|
| **Apple Silicon / MLX** | ✅ 原生 MLX（8-bit 0.80 GB／bf16 1.50 GB） |
| **繁中** | ✅ 標示支援 **zh**（51 語言） |
| **不需訓練就能用** | ✅ 已微調好（不像 Laya/Kev 是 base） |
| **授權** | ✅ Apache-2.0 |
| **長文** | ✅ **25,600 tokens** |
| **選項數上限** | ✅ **無 26 字母上限**（實測過 77 選項） |
| **安裝** | ✅ `pip install "jev-style[mlx]"`（PyPI 0.3.0，**我實裝成功**） |
| 自稱基準 | 準確率 **82.3%**（vs base 65.9%）、**ECE 0.017**（base 0.065）、M1 Max 77 ms |
| 第三方基準（自述） | Banking77 **68.2%** vs 最佳官方 Laya **49.2%**；MASSIVE 37 語言 **65.5%** vs Laya 36.1% |

### 我實測到的（M5 Max，MLX 8-bit）

| 量測 | **Jev-Style 0.8B v3** | Laya 多語 | Laya 英 | 亂猜 |
|---|---:|---:|---:|---:|
| **20 個格式檢查 × 5 案例** | **16/20 = 80%** | 9/20 = 45% | 10/20 = 50% | 25% |
| 需不需要看圖（5 題） | **4/5** | 5/5（但 `noul` 方向全反） | — | — |
| **`noul` 型（4 題）** | **3/4 ✅ 正常** | **0/5 完全反向** | — | — |
| 中文醫學分類（3 題） | **3/3** | — | — | — |
| 速度（暖機後中位數） | **10 ms** | 6 ms | 6 ms | — |
| 50 次呼叫 | **8 ms/次** | — | — | — |
| 全庫估算（79,090 題） | **約 11 分鐘** | — | — | — |
| 載入（已快取） | **0.7 秒** | 22 秒 | 22 秒 | — |

**逐項對照（同一題、同一問句）**：

| 檢查 | Jev-Style | Laya 多語 |
|---|---|---|
| `sub_sup`（上下標） | **5/5 ✅** | 0/5 ❌ |
| `formula`（要不要計算） | **5/5 ✅** | 5/5 ✅ |
| `fig_ref`（有沒有提到圖表） | **4/5** | 2/5 ❌ |
| `opt_count`（幾個選項） | 2/5 ❌ | 1/5 ❌ |

### ⚠️ 但它也有一個**反向偏誤**（我實測到）

抽取忠實度任務（「這兩段文字是否完全相同」）：

| 案例 | Jev-Style | Laya |
|---|---|---|
| **完全相同** | A ✅ | diff ❌ |
| 完全不同 | B ✅ | B ✅ |
| 差在標點 | **A ❌** | B ✅ |
| 差一個字（抗癲癇→抗癲癲） | **A ❌**（信心 0.9468！） | B ✅ |
| 差全形半形 | **A ❌**（信心 0.9637！） | B ✅ |
| 差在題號 | **A ❌** | B ✅ |
| **合計** | **2/6** | **5/6** |

**→ 兩個模型的偏誤剛好相反**：Laya 傾向說「不一樣」，Jev-Style 傾向說「一樣」——**而 Jev-Style 錯的時候信心還很高（0.95）**。**這是最危險的失敗模式：自信地錯。**

**→ 可用結論：這個具體任務（逐字比對）兩個都不能用。** 但**格式檢查**（§上面 80%）Jev-Style 明顯可用。

---

## 4. 候選清單（按「我們能不能用」排序）

| # | 專案 | 型態 | 語言 | Apple/MLX | 授權 | 證據等級 | 我的評語 |
|---|---|---|---|---|---|---|---|
| **1** | **`chaoliangUNSW/Jev-Style-0.8B-Decision-v3`** | **已微調** 0.8B | **zh＋51 語言** | ✅ **MLX 原生** | Apache-2.0 | **我實測 80%** | **最適合我們**：小、快、中文、免訓練 |
| 2 | `Mapika/decider`（2b/4b/35b-a3b/0.8b/vision） | **已微調** | 英文為主 | ⚠️ MPS 有（PR #2）非 MLX | Apache-2.0 | **JevBench #21/#10、Decision Index #4** | **最工程化**：六個尺寸＋**有 vision 版**＋**你自己可重訓**（含 teacher 資料與 recipe） |
| 3 | `TheoLeeCJ/SemIf`（前 OpenJev） | **零訓練 wrapper** | 任意（跟 base 走） | ✅ **MLX backend**＋MPS | MIT | **JevBench #2（74.7）** | **可用我們的 35B 直接變決策模型** |
| 4 | `nokia-applied-research/AnyJev` | **零訓練 wrapper** | 任意 | ⚠️ 未確認 MLX | Apache-2.0 | PyPI 有、36 頁提交 | 「**不改一個權重**就能把任何 LLM 變 Jev 式」 |
| 5 | `ollaya-dev/ollaya` | **runner／daemon**（Rust） | — | ⚠️ 未知 | Apache-2.0 | 140 頁提交、網站＋docs | 「**像 Ollama 一樣跑決策模型**」；`winnow:e4b` 自稱 0.722 vs Jev 0.738 |
| 6 | `aac6fef/laya-mlx` | Laya 的 MLX 轉換 | 英／多語 | ✅ MLX | Apache-2.0 | **我實測 45–50%** | **base，零樣本不可用**；但**確定性最好**（小數 6 位不變） |
| 7 | `ZLHAOOO/laya-mlx-zh` | Laya **中文微調** | **zh** | ✅ MLX | Apache-2.0 | **未測**（0 downloads） | 中文專門，但**沒人下載過**，證據薄弱 |
| 8 | `convaiinnovations/laya` | base | 多語 | ⚠️ 需裝 `laya-mlx` | Apache-2.0 | 官方、3,962 likes | 上游；**零樣本不行**（官方自己承認） |
| — | `jaredpalmer/kev` | 家族＋訓練碼 | 英 | ❌ **Mac 慢**（DeltaNet 無 MPS kernel） | Apache-2.0 | 7,305★ | 要 clone repo、`@qwen3` 標籤才有低延遲 |
| — | ~~`awesome-jev` 系列（9 個）~~ | — | — | — | — | ❌ **SEO spam** | **跳過** |
| — | ~~`feder-cr/jev`（889★）~~ | 1B yes/no | 英 | ❌ | MIT | ❌ **只有 1 筆提交**（`Co-Authored-By: Claude 5.5`） | **AI 生成的空殼** |
| — | ~~`ikermoel/open-alternative-jev`、`poorjev`、`Foq`、`zev-rs`（★0–55）~~ | — | — | — | — | ❌ 幾乎無提交 | 跳過 |

---

## 5. ⭐ 我認為對我們**最有價值**的一條線（業主可能還沒注意到）

**「零訓練 wrapper」這一類（SemIf／AnyJev／jevfire／joshua-diffusion）**：

> **它們不訓練任何東西。它們把「任何現成的 LLM」（就是我們已經有的 35B！）變成 Jev 式的決策模型——直接從字母 logits 讀出選項機率，不做解碼、不做 JSON 解析。**

**證據**：Decision Index 的 **第 2、3 名就是這類工具套在 27B 上**（55.7／55.6），**贏過所有開源訓練模型**。

**為什麼這對我們重要**：

| 我們現況 | 有了 wrapper 之後 |
|---|---|
| 35B 產出**文字**，我們再解析 | 35B 直接產出**選項機率**（**沒有東西可以幻覺**） |
| 「這題有錯嗎」靠文字判斷＋解析 | 變成 **`choice`，回機率** |
| **沒有可信的「該不該信任」訊號** | **拿到校準機率**（我們現在最缺的） |
| 要微調（D9）才能用 Laya | **不用微調就能把現有 35B 決策化** |
| SemIf 已支援 **MLX backend** 與 MPS | 可直接在 M5 Max 跑 |

**→ 這是 D9（Laya 微調）之外的一條更便宜的路：先量「現成 35B ＋ wrapper」能做到多少，再決定要不要微調。**

---

## 6. 對我們專案的具體建議（我的判斷）

| 優先 | 動作 | 理由 |
|---|---|---|
| **1** | **量「現成 35B ＋ zero-training wrapper」**（SemIf／AnyJev）在**我們真正任務**上的表現 | **不用訓練**、**沒有幻覺空間**、Decision Index 前 3 名有兩個是這條路。**這是 D10 的具體化** |
| **2** | **用 Jev-Style 0.8B 做格式檢查的快速篩**（我實測 **80%**、**10 ms**、**繁中可用**） | **0.8 GB、MLX、免訓練**，比 Laya（45%）好一倍 |
| **3** | **decider-2b-vision 值得一看**（我尚未測） | **唯一「決策模型 ＋ 讀圖」的組合**——直接對應我們的圖片定位／歸屬線 |
| **4** | **decider 的 recipe 可自訓**（含 teacher 資料、資料註冊表、訓練腳本） | **D9 真的要做時，用它的 recipe 比從零開始便宜** |
| 5 | `ollaya` 當**本地 runner**（TypeSafe 線路格式） | 若日後要接既有 Jev client，改一個環境變數即可 |
| 6 | Kev / `laya` base / `awesome-*` 系列 | **先跳過** |

### ⚠️ 三個必須提醒的坑

1. **Jev-Style 的「完全相同」偏誤**：它傾向說「一樣」，而且**錯的時候信心 0.95**。**任何用它做的判斷都必須有負對照集**（我這次就是靠負對照才發現的）。
2. **這些模型的信心是「對它們自己的資料」校準的**，不是對我們的。decider 官方明說：「**Check it on your own labels before using confidence for routing.**」
3. **`laya-mlx-zh`（中文版）0 downloads**——**沒有人驗證過**。不要因為它寫「中文」就相信；要自己量。

---

## 7. 誠實清單（我沒做的）

- **`decider-2b`／`decider-2b-vision` 我沒有實測**（只讀了 model card 與第三方排行榜）
- **SemIf／AnyJev 的 MLX wrapper 我沒有實測**（只確認 SemIf 有 MLX backend 文件）
- **`laya-mlx-zh`（中文微調）我沒有實測**（0 downloads，無法判斷）
- **`winnow:e4b`（ollaya 推薦）我沒有實測**（需要 Ollama 式下載，未跑）
- **沒有量「這些模型在我們 79,090 題真題上」的準確率**——上面所有數字都是我的**小型人工案例**，不是真題抽樣
- **沒有量「模型間的一致性」**（Jev-Style vs 35B vs Laya 是否同判斷）
- **沒有驗證第三方排行榜本身的方法論**（我只讀了它們的排名表，沒有跑它們的 harness）
- **Jev 本身無法測**（封閉服務、需 waitlist）
