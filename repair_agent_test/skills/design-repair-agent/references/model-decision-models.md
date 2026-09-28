# 決策模型（Decision Models）：Kev-4B 與 Laya / laya-mlx — 研究與本機實測

**性質**：**研究＋實測報告**，唯讀，不改主線。
**日期**：2026-09-27。
**觸發**：業主指示「搜尋並研究 `jaredpalmer/kev-4b` 跟 `laya-mlx`，這兩款是最新的決策模型，速度快，但是我不懂她該怎麼用」。
**紀律**：官網宣稱 vs **我實測到的**分開寫；未量的標「未量」。

---

## 0. 一句話回答「該怎麼用」

> **它們不是聊天模型，也不是 35B 的替代品。它們是「你當場定義一張選擇題，它在一次前向傳遞裡回你一個校準過的機率分布」——6 毫秒、不生成文字、所以不會幻覺。**

**關鍵**：它**不能**讀圖、不能解釋理由、不能多步推理。但我們專案**真正缺的那一項能力**——「**知道自己什麼時候不確定**」——**正是它的設計目的**。

**⚠️ 但有一個硬前提**：**零樣本幾乎等於瞎猜（我實測 45–50%，亂猜 25%）。要能用，必須用我們自己的資料微調。**

---

## 1. 兩者是什麼（官網事實）

| | **Kev-4B** (`jaredpalmer/kev-4b`) | **Laya** (`convaiinnovations/laya`) |
|---|---|---|
| 型態 | **LoRA adapter（r=16, 33.8M 可訓練參數）＋ pointer head**，掛在 `Qwen/Qwen3.5-4B-Base` 上 | **非自迴歸編碼器＋決策頭**（ModernBERT-large 421M / mmBERT-base 322M） |
| 輸出 | 每個問題一個**機率分布**，一次前向傳遞 | 同左 |
| 生成文字？ | ❌ 不生成 | ❌ 不生成（**所以「沒東西可以解析、沒東西可以幻覺」**） |
| 授權 | Apache-2.0（adapter＋head；base 也是 Apache-2.0） | Apache-2.0 |
| 熱度 | 9,662 downloads / 66 likes | **3,962 likes** |
| 最後更新 | 2026-09-24 | 2026-09-24 |
| 契約 | 服務 **TypeSafe 的 `/v1/systemone`** | 同契約（`laya-serve` **故意相容 TypeSafe**，換 base URL 即可） |
| 執行方式 | 需要 `github.com/jaredpalmer/kev` 的程式（**PyPI 上沒有 `kev` 套件**——我查到的 `kev` 是別人的專案） | **`pip install laya`**（PyPI 0.3.20）／**`pip install laya-mlx`**（Apple） |

### 1.1 問題型別（三家共用同一套「typed question」）

| 型別 | 意思 | 回什麼 |
|---|---|---|
| `choice` | 從 `criteria` 裡選一個 | 標籤 ＋ 每個選項的機率 |
| `score` | 有序等級 | 分數 ＋ 各等級機率 |
| `noul` | 是非題（no-utility / boolean） | 「是」的機率 |

**回答空間是「請求時才定義」的** → **新的檢查項目不需要重新訓練**（Laya README：`The answer space is defined at request time, so new schemas need no retraining`）。

---

## 2. 速度（官網 vs 我實測）

| 量測 | 官網（T4 GPU） | **我實測（M5 Max，MLX）** |
|---|---|---|
| 1 題 | 39.5 ms（英）／32.8 ms（多語） | **6 ms**（暖機後中位數，10 次） |
| 10 題一批 | 158.6 ms／72.3 ms | **23 ms** |
| 20 個檢查一批 | — | **473 ms** |
| 模型載入 | — | **約 22 秒**（第一次含下載） |
| 吞吐 | 103–332 題/秒（T4） | 全庫估算：**79,090 題 × 20 檢查 ≈ 10.4 小時**（單機、單程序） |

**已驗證的移植保真度**（`laya-mlx` 自帶 `validation.json` ＋ README）：

- FP16 與上游 PyTorch MPS FP32 在 **63/63** 個決策分布的 argmax 上一致
- 最大校準機率差 **0.0054443**
- 100 次重複呼叫：有限、確定性、清快取後 MLX active-memory 成長 **0 bytes**

**✅ 我獨立驗證了確定性**：同一題同一問題連續 3 次，輸出**小數 6 位完全相同**（`0.868300`）。

---

## 3. ⚠️ 我實測到的四個缺陷（官網有的有寫、有的沒寫）

### 3.1 `noul` 型**完全反了**（最嚴重）

我在**多語版**上測（中文）：

| 案例 | `noul` 機率 | 模型說 | 正確答案 |
|---|---:|---|---|
| 「根據**下圖**的劑量反應曲線…」 | **0.2404** | **無圖** ❌ | 有圖 |
| 「下列何者為心肌梗塞最典型的檢驗指標？(A) Troponin I…」 | **0.6549** | **有圖** ❌ | 無圖 |
| 「**下表**為各藥物的半衰期…」 | 0.4559 | 無圖 ❌ | 有表 |
| 「關於**圖形理論**（graph theory）的敘述…」 | 0.3896 | 無圖 ✅ | 無圖 |
| 「請參閱附圖。」 | 0.7869 | 有圖 ✅ | 有圖 |

→ **完全反向**。**官網 README 有記載這個 bug**：

> 「**`noul` can follow its option labels instead of the state**, most strongly on this English checkpoint. `noul` renders its two options as `false:` / `true:`, and here that label pair can dominate the answer, returning a confident "no" for clearly positive input ([#156](https://github.com/NandhaKishorM/laya/issues/156)).」

**官網建議解法（我實測有效）**：改成**兩選項 `choice`，用中性 key**：

```python
{"type": "choice", "instructions": "…？",
 "criteria": {"A": "是的，…", "B": "否，…"}}
```

我實測（多語版、中文）：明確有圖 → A（0.8123）✅；明確無圖 → B（0.2528 是）✅。**方向對了。**

**→ 可用結論：本專案不要用 `noul`，一律用兩選項 `choice` ＋中性 key。**

### 3.2 `action.act_probability` 永遠是 1.0（無訊號）

我實測**每一個**回答的 `action.act_probability` 都是 **1.0**。

**官網 README 也承認**：

> 「**`action.act_probability` carries no usable signal yet** ([#185](...)). It reads 1.0 for almost every input, and its raw logits run against correctness (AUROC 0.30 on 396 labelled decisions). **Gate on `confidence` instead**, which reaches an AUROC of 0.77 on the same items.」

**→ 可用結論：要判斷「該不該信任」，讀 `confidence`，不要讀 `act_probability`。**

### 3.3 ⚠️ 「已出廠的溫度被夾住」警告（官網沒提，我實測到）

`laya-mlx` 載入時吐出：

```
RuntimeWarning: laya-mlx: this checkpoint ships temperatures outside [0.5, 5] which would distort
confidence; clamping choice:11+=0.1006. Treat confidence from the affected buckets as uncalibrated.
```

**→ 11 個以上選項的 `choice` 題，信心要當「未校準」看待。** 我們若要問「選項數」這類問題（4–5 個選項）不受影響，但**不要設計超過 10 個選項的 `choice`**。

### 3.4 **位置／標籤偏誤**（我實測到）

| checkpoint | 語言 | 偏誤 |
|---|---|---|
| 英文 | 英文 | `fig_ref`（有沒有提到圖表）**5/5 全答 A** |
| 多語 | 中文 | `sub_sup`（有沒有上下標）**5/5 全答 A** |

→ **單一選項被系統性偏好。** 官網「Honest Limits」有一條相關但不同：`noul` 會被 `true:`/`false:` 標籤帶走；我這裡量到的是 `choice` 也有。

**→ 可用結論：一定要做「亂猜 baseline」對照**（例如全答 B 會得幾 %），否則會把偏誤當能力。

---

## 4. 🚨 最關鍵的實測：**用我們真正的任務，零樣本幾乎是瞎猜**

我拿**本專案真正要它判斷的東西**測（抽取文字是否忠實、有沒有上下標、幾個選項、要不要計算、有沒有提到圖表）：

### 4.1 任務一：抽取文字 vs 紙本是否忠實（多語版，中文）

| 案例 | 模型答 | 信心 | 正確？ |
|---|---|---:|---|
| **完全相同** | diff | 0.6322 | ❌ **把一樣的說成不一樣** |
| 完全不同 | diff | 0.5291 | ✅ |
| 差在標點 | diff | 0.4252 | ✅ |
| 差一個字（抗癲癇→抗癲癲） | diff | 0.3326 | ✅ |
| 差全形半形 | diff | 0.0714 | ✅ |
| 差在題號 | diff | 0.1827 | ✅ |

**5/6**——但**唯一錯的那個是「完全相同」**，也就是說**它有一種「全部答 diff」的傾向**。加上信心只有 0.07–0.63（**低於可信門檻**），**這個任務不能靠它**。

### 4.2 任務二：20 個格式檢查 × 5 個案例（全部改成 choice＋中性 key）

| | 多語版（中文） | 英文版（英文） |
|---|---:|---:|
| **答對率** | **9/20 = 45%** | **10/20 = 50%** |
| 亂猜 baseline（全答 B） | 5/20 = 25% | 5/20 = 25% |

**→ 零樣本 ≈ 稍優於瞎猜。** 這**不是語言的問題**（中英一樣爛），**是能力問題**。

**官網自己寫得很誠實**：

> 「**Base checkpoints are near chance on typed-decisions zero-shot** — 0.362 here and 0.352 for multilingual, against a 0.318 random and a 0.461 majority-class baseline. **The 0.766 belongs to the checkpoint fine-tuned on that benchmark's own training split.** Laya is a **fast base to specialise, not a zero-shot decision engine**.」

> 「**Ships over-confident**: Refitting one temperature per (question type, option count) moves mean ECE **0.466 → 0.081** … **Do this on your own data before trusting the probabilities.**」

**→ 可用結論：零樣本不可用。要能用，必須用自己的資料微調＋重新擬合溫度。**

---

## 5. Kev-4B 在 Apple Silicon 上的特別警告（官網事實）

> 「**Slow on a Mac.** The DeltaNet kernels have **no MPS implementation**; PyTorch falls back to reference code. A five-question request that takes **0.17 s** on the Qwen3 Kev-4B takes **0.78 s** here in bf16 on an M5. On CUDA with `flash-linear-attention` installed it is fast. **Use `jaredpalmer/kev-4b@qwen3` for低延遲 on Apple Silicon until an MLX path exists.**」

| 事實 | 影響 |
|---|---|
| Qwen3.5 base 是 **24 層 Gated DeltaNet（線性注意力）＋ 8 層全注意力** | DeltaNet 在 MPS 沒有 kernel |
| 需要 `transformers >= 5.17` ＋ `peft >= 0.21` | M5 已有 transformers 5.17.0（`~/models/venvs/mlxenv`） |
| 服務：`uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8008` | 需要 clone repo，**PyPI 沒有** |
| 4B bf16 服務需 ~9 GB GPU 記憶體 | 訓練在 1×H100 花了 56 分鐘 |
| 有人做了 **8-bit MLX 轉換**：`RoderickQiu/kev-4b-mlx-8bit`（160 downloads，**未實測**） | 可能是 Apple 上的可行路徑 |

**→ 我的判斷：Kev-4B 在我們現有設備上不是即戰力**（M5 Max 慢、要自架 repo、沒有 MLX 官方路徑）。**先不投入。**

---

## 6. 所以「該怎麼用」——三個真正可行的用法

### 用法 A：**便宜的守門員（gatekeeper）**

在送 35B 之前，用 Laya 做**確定性的粗篩**。我實測到的**可靠**項目（客觀、可驗）：

| 檢查 | 實測狀況 |
|---|---|
| 選項數（4 vs 3） | 英文 4/5 ✅、多語 1/5 ❌ **不穩** |

**⚠️ 誠實說：連「數有幾個選項」都不穩（多語版全錯）。所以用法 A 目前也不成立，除非微調。**

### 用法 B（**推薦**）：**微調成「這題需不需要人看」的路由器**

這是**它真正被設計來做的事**，而且**我們的資料剛好就是訓練資料**：

| 我們的資料 | 數量 | 用途 |
|---|---|---|
| `question_review_events.jsonl` 的 `accept` / `block` | **4,790 題**（accept 4,568／block 396） | **標籤** |
| `question_ai_findings.jsonl` | 86,264 行 / 524 MB | **特徵**（模型看過什麼） |
| `question_repair_questions.jsonl`（反問＋回答） | 53 行 | 輔助訊號 |

**作法**（官網有完整流程）：

1. 用「題目文字（或管線輸出）」當 `state`，用「人 accept 了沒」當標籤
2. 微調 `laya-multilingual`（**繁中 → 必須用多語版，不要用英文版**）
3. **重新擬合溫度**（官網：ECE 0.466 → 0.081 全靠這步）
4. 得到一個 **6 ms** 的「這題要不要進人佇列」機率

**為什麼這比現況好**：我們現在**沒有**「該不該信任機器判斷」的可靠訊號（`ANSWER_DISAGREES` 與人一致率 8% 被移除了）。Laya 的 `confidence` 有 AUROC 0.77，**而且它回的是機率不是文字**。

### 用法 C：**標籤分岔器**（直接對應我們 D8 的 Block 分岔）

我們的 D8 是「把 Block 分成『改錯了』vs『方向對還沒對』」。這種**分類**正是決策模型的形狀：

```python
{"block_intent": {"type":"choice", "instructions":"這個人的退回是什麼意思？",
  "criteria": {"A":"他認為改的東西是錯的", "B":"他認為方向對但還沒改對",
               "C":"他無法決定", "D":"他說的是別的欄位"}}}
```

**但同樣前提：要先用既有 396 題 block ＋你的註解微調。**

---

## 7. ⚠️ 它**不能**做什麼（避免誤用）

| 需要的能力 | Laya/Kev | 為什麼 |
|---|---|---|
| 讀圖（我們的首要目標：圖片定位） | ❌ **完全不能** | 它是**純文字** `state`。圖片定位還是要靠 `PP-DocLayout-V3` |
| 解釋「為什麼我覺得這題有錯」 | ❌ | 不生成文字 |
| 判斷題意／語意正確性 | ❌ | 這是知識密集任務；Kev 自己承認 MMLU 0.70 vs Jev 0.90 |
| 代替 35B 做多步推理 | ❌ | 單次前向傳遞 |
| 處理超過 10 個選項的 choice | ⚠️ | 出廠溫度被夾（§3.3） |
| 長文件（>1024 tokens） | ⚠️ | 需 `max_len=8192`（多語版），且官網警告**超過約 4000 tokens 後結果不穩**（20 題只對 8–17 題） |

### 與我們架構的關係（我的判斷）

```
圖片定位 → PP-DocLayout-V3（已實測 0.992）        ← 不是 Laya
逐題判讀 → 35B MoE（ornith/occamy，未實測）        ← 仍需要
快速路由 → Laya 微調後（6ms/題）                   ← 這是它的位置
誠實的不確定性 → Laya 的 confidence（AUROC 0.77）  ← 我們現在缺的正是這個
```

**→ Laya 填的是「路由與不確定性」，不是「判讀」。它是第三個角色，不是取代任何一個。**

---

## 8. 治理與資料邊界（重要，對我們有利）

| 項目 | 狀況 |
|---|---|
| 授權 | 兩者都 **Apache-2.0** → **可商用** |
| 執行位置 | **完全本機**（`laya-mlx` 是 MLX；Kev 是 PyTorch／MPS） → **題目內容不出內網** ✅ |
| 模型大小 | Laya 英 808 MB／多語 647 MB（`laya-mlx`）→ **比 35B 小兩個數量級** |
| 額外服務 | `laya-serve` 綁 `0.0.0.0` 且**預設無認證**，除非設 `LAYA_API_KEY` → **部署要注意** |

**→ 對「Mac Studio 只當 Agent 中樞、算力在別處」的架構是好事**：Laya 太小了，可以**直接在中樞上跑**，不用佔用 M5 Max／DGX spark。

---

## 9. 本機已就緒的環境（實測事實）

| 項目 | 狀態 |
|---|---|
| 硬體 | **Apple M5 Max**，macOS 26.6.2 |
| `mlx` | 0.32.2（`~/models/venvs/mlxenv`，另有 mlx-lm 0.31.3／mlx-vlm 0.7.1） |
| 新建 venv | **`~/models/venvs/layaenv`（Python 3.13）＋ `laya-mlx 0.2.0`** ✅ 已裝好可跑 |
| 已下載模型 | `aac6fef/laya-mlx`（842 MB）＋ `aac6fef/laya-multilingual-mlx`（644 MB）在 HF cache |
| `uv` | `/opt/homebrew/bin/uv` ✅ |
| HF token | `~/.cache/huggingface/token` 存在 ✅ |
| 磁碟 | 4.2 TiB 可用（充足） |

**執行方式**（已驗證可用）：

```bash
# 已建好的 venv
~/models/venvs/layaenv/bin/python -c "
import laya_mlx as laya
agent = laya.load('aac6fef/laya-multilingual-mlx')   # 繁中請用多語版
r = agent.predict('題目文字', {'檢查':{'type':'choice','instructions':'…？','criteria':{'A':'是…','B':'否…'}}})
print(r['answers'])
"
```

**⚠️ 若要用 `pip install laya`（原版，非 MLX）**：官網警告 `transformers` 匯入時會探測 TensorFlow，若裝了 TF 可能**卡死** → 用 `USE_TF=0`。

---

## 10. 我的判斷（非事實）

1. **「速度快」是真的（6 ms/題），但「零樣本可用」是假的（45–50%）。** 這兩件事必須分開講，否則會誤用。
2. **它最適合我們的位置不是「判讀」，是「路由 ＋ 誠實的不確定性」**——而後者正是**我們量到目前最缺的那一項**（`ANSWER_DISAGREES` 一致率 8% 被移除、找不到可信的「該不該信任」訊號）。
3. **我們的資料天然是它的訓練資料**（4,790 題 accept/block ＋ 524 MB findings）。這是**少見的「模型與資料互相匹配」**。
4. **微調與擬合溫度是必要條件，不是選項。** 官網：ECE 0.466 → 0.081 全靠擬合；0.362 → 0.766 全靠微調。
5. **Kev-4B 在 Apple Silicon 不是即戰力**（DeltaNet 無 MPS kernel、要自架 repo、`@qwen3` 標籤才有低延遲）。**先不投入**；若要試，先試 `RoderickQiu/kev-4b-mlx-8bit`。
6. **它不能取代 PP-DocLayout-V3（圖片定位）也不能取代 35B（判讀）。** 它填中間。
7. **不要用 `noul`**（會反向）；**不要用超過 10 個選項的 `choice`**（溫度被夾）；**不要讀 `act_probability`**（永遠 1.0）。

---

## 11. 未量（誠實清單）

- **微調之後的實際準確率**（未量；官網的 0.766 來自它自己的 benchmark 訓練集）
- **我們的 accept/block 標籤當訓練資料的可行性**（未量：標籤品質、類別不平衡 4,568:396）
- **繁中長文件的實際表現**（未量；官網只給出「超過約 4000 tokens 後不穩」）
- **`RoderickQiu/kev-4b-mlx-8bit` 是否可用**（未測）
- **`laya-typed-decisions-mlx` 的三個 subfolder checkpoint**（未測）
- **`laya-serve` 在本機的 HTTP 行為**（未測）
- **與 35B 的一致性比較**（未量；「同一題 Laya 與 35B 是否同判斷」尚未測）
- **`score` 型（有序）的表現**（未量；官網說這是最弱的 primitive，SST-5 只有 0.372）
