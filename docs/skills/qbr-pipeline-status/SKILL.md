---
name: qbr-pipeline-status
description: The measured state of the qbr question-bank pipeline as of 2026-09-20 — what is stable, what is not, what is blocked, and which decisions are still open. Use when asked how far extraction/figure/group/AI-review work has got, when deciding what to build next, when a queue shows empty options or missing pictures, or before trusting a "two engines agree" claim. Includes the commands to re-measure, because every number here decays.
---

# qbr 管線現況（量測快照）

`tw-national-exam-catalog/qbr/`。每一條數字都是量出來的，日期 **2026-09-20**。
**重測指令在第 7 節**——這份文件會過期，指令不會。

**先讀 [`../../qbr/AGENTS.md`](../../../qbr/AGENTS.md) 的鐵則，再讀這份。**
這份是**現況**，不是規範；衝突時以 AGENTS.md 與 charter 為準。

## 0. 一句話

**骨架在 78,690 題上穩定；圖片與 AI 審核在新管線上各缺一整個階段。**

| 面向 | 現況 |
|---|---|
| 抽取（切題／選項／答案） | **穩定**（78,690 題，8 類科，S3 只擋 11 卷） |
| 圖片截圖 | **舊佇列穩定（已驗收）；新佇列完全沒有** |
| 題組 | **穩定**（908 組／1,967 題，三項完整性檢查全過） |
| AI 審核呈現 | **新管線 0 題；舊管線有 543,944 筆但不在 v2 顯示** |
| 驗收標準「兩引擎一致」 | **文件這樣寫，程式沒有這樣做**（見第 6 節） |

---

## 1. 抽取引擎：七階段

```text
S0_intake   凍結 Q / ANS / MOD（digest）
S1_triage   PDF 便宜分類
S2_dual     雙引擎讀取（A=PyMuPDF、B=poppler）+ 遮罩 + 分段
S3_gate     這卷能不能發布（確定性，無模型）
S4_records  一題一筆，答案合併
S5_package  不可變封裝 + lineage
S6_verify   把封裝對回紙本
```

### 目前（984 卷／78,690 題／8 類科）

| 階段 | 通過 | 未通過 |
|---|---|---|
| S0 intake | 566 | 0 |
| S1 triage | 566 | 0 |
| S2 | 566 | 0 |
| **S3 gate** | **555（publish）** | **11（quarantine）** |
| S4 records | 555 | 0 |
| S5 package | 555 | 0 |
| **S6 verify** | **554** | **4** |

**已被擋下的 15 卷**（`/tmp/qbr-expand/*/package_report.jsonl`）：

| 症狀 | 卷數 | 位置 |
|---|---|---|
| `count-mismatch`（讀到的題數 ≠ 答案卷） | **6** | **全部在 醫師(二)** |
| `options-not-four` + `answer-not-on-sheet` | 4 | 醫師(一)、醫事放射師 |
| `options-not-four` 單獨 | 2 | 物理治療師、醫事放射師 |
| `replacement-codepoints` | 1 | 醫事放射師 |
| `S6 catalog_package_validator` | **4** | 醫師(一)、藥師、醫事檢驗師×2 |

**那 4 卷 S6 不是建置缺陷**：catalog validator 要求
`metadata.review_status=accepted`，而封裝寫的是 `machine_verified_pending_human`。
**沒有任何真人審過**，所以 validator 拒絕——這是 `REV-04 / REL-01 / GOV-05` 的治理缺口，不是讀錯。

### 已穩定（不要動）

- **切題邊界**：後繼號碼；折行不是新題（全庫 89,463 個 anchor 只有 2 個符合折行條件）。
- **選項歸屬**：私用區字母表、逐家族試切、拒絕為預設。
- **答案**：只從官方答案卷（`_ANS`）讀，修正表（`_MOD`）疊在上面。**送分／`A或D` 都正確**（387 題 `is_special_correction`）。
- **字形遺失**：只報位址、不補字。
- **`⻑`→`長`**（`substituted-ideograph`）：CJK Radicals Supplement 的區塊測試；NFKC **折不回**，必須用碼位區間判。在沒看過的新科目上仍然成立（新科目 0.28% 反而略高，因為 `年⻑者`）。
- **上下標**：用 A 的字級（`size`）判方向，B 沒有 `size`。

### 待優化

| 項目 | 為什麼 |
|---|---|
| `count-mismatch` 6 卷 | 同一症狀集中在 醫師(二)，值得單獨一輪 |
| 「兩引擎一致」沒有被執行 | 見第 6 節——這是目前最大的缺口 |
| 提示詞第 4 條（「以『的』結尾」） | **會過度觸發**：正常問句也被判缺陷（兩引擎都誤報） |
| `queue_index.json` 的原始 `categories` | 半形／全形變體未折疊（UI 不受影響） |

---

## 2. 圖片截圖

### 舊佇列（429 卷／33,150 題）：**已驗收，穩定**

| 項目 | 數字 |
|---|---|
| 有圖的題 | 1,169（選項圖 264） |
| 裁切檔 | 270 |
| HTTP 取回並**用 PIL 真的解碼** | **2,015 / 2,015，0 失敗** |
| 指向不存在的檔／絕對路徑 | 0 / 0 |
| 外部標準（手工題庫） | 召回 **97.1%**、漏 13、多 5 |

驗收方式是「**看不到的證據不是證據**」：HTTP 200 不算，要用 PIL 解碼。

### 新佇列（984 卷／78,690 題）：**完全沒有圖**

```
image_refs   : 0 / 78,690
stem_image   : 0 / 78,690
crops/       : 這個目錄不存在
```

**原因不是讀不到，是那一步沒被呼叫**：`batch_package.py` / `batch_run.py` 從不呼叫
`crop_run_figures.py`（這是設計上的第二階段，不是封裝的一部分）。
另外 `qbr/src/qbr/package.py:210` 把 `has_visual_asset` **寫死成 `False`**。

### 為什麼這件事嚴重

**437 題 blocker 正是要看圖才能審的題**：

| 症狀 | 題數 | 真正是什麼 |
|---|---|---|
| `option-shape`「四個選項都沒有文字」 | 326 | 選項是化學結構圖 |
| `option-shape`「選項數 0」+ `dangling-answer` | 111 | 整組選項是圖 |

打開新版 v2，這些題**只會看到空白選項**，而且沒有任何欄位說它本來有圖。
`disputes.py` 的 `empty-option` 抑制邏輯（「251/274 是對的，因為選項就是圖」）**失去依據**，
因為它要讀 `image_refs[].asset_role == 'option-image'`。

**修它的前提**：先確認圖片綁定在哪一層（`crop_run_figures` 或 exporter），
否則是在錯的樓層加欄位。**未決定。**

---

## 3. 題組

### 現況：**穩定**

| 項目 | 數字 |
|---|---|
| 組數 | **908** |
| 題數 | **1,967**（2 題組 769、3 題組 127、4 題組 12） |
| `shared_stem` 缺少的組 | **0** |
| `group_size` 與實際成員數不符的組 | **0** |
| `group_position` 不連續的組 | **0**（0-based，0…n-1；**0 是組頭**，不是缺號） |
| 以續接詞開頭但沒有 `group_ref` | **0 / 828** |
| 宣告題組但沒有 `bound` | **0 / 112** |

### 兩個訊號（都用紙本自己的話）

| 訊號 | 組數 | 題數 |
|---|---|---|
| `marker:承上題` | 794 | 1,615 |
| `declared:3`（依序回答下列三題） | 101 | 303 |
| `declared:4` | 11 | 44 |
| `marker:上述` | 2 | 5 |

**第三個訊號（幾何間距）已量測並否決**：組間 104.9 pt vs 非組間 99.8 pt，
掃過 20–300 pt 的最佳精確率 **6.9%**（13.4 個誤報換 1 個真組）。
留在 `groups.py` 的 `GROUP_GAP_RATIO` 是**記錄，不是規則**，沒有任何地方使用它。

### 限制（誠實）

**沒有 marker、也沒有宣告的組，偵測不到。** 這不是實作偷懶，是上面那個量測的結論：
間距分不出來。所以「共用題幹」這個類別只覆蓋紙本**明說**的那些。
另有一批 486 題題幹 <12 字且不在題組（例如「下列何者不是酮體？」）——**尚未確認**
是正常短題，還是依賴前題的未偵測題組。

---

## 4. 後續題目審核的自動化（AI 審核）

### 答案：**在新管線裡從未呈現過。**

| | 新管線（qbr / v2） | 舊制度 |
|---|---|---|
| `ai_review` 欄位 | **78,690 題全部 `None`** | — |
| `v2.html`（基準線）提到 `ai_review` | **0 次** | — |
| `mobile.html`（v1 參考） | — | **10 次** |
| 事件量 | **0** | SQL `exam.question_ai_review_events` **543,944 筆**（2026-06-20 → 08-03） |

舊 SQL 的來源是 codex 系列稽核（`ai:codex-subagent:gpt-5.6-luna` 328,583 筆等），
那是**舊路線**，依決定不混入新管線。

### 新管線量過什麼（`reports/ai_first_pass_triage.md`）

視覺重讀當 triage 的**精確率只有 12–16%**：單引擎旗標 42 題只有 5 題值得 block；
兩引擎一致 19 題只有 3 題。**決定不接成 triage 佇列。**
根本限制：**一個讀錯的模型與一個真的錯字，在單次轉錄裡無法區分。**

模型在本專案**唯一已被證明可靠的工作**：**回答關於它看到的圖的封閉問題**
（例：「紙本印的是 `長` 還是 `⻑`？」）。

### 引擎現況

| 引擎 | 端點 | 狀態 |
|---|---|---|
| `qwen3.8-flash-next` | DGX `192.168.10.90:8888` | **可用**（讀取主力：0.44 s/題，85%） |
| `qwen3.8-27B` | `127.0.0.1:8082` | **已上線但沒載模型**（`/v1/models` 回空 `data`） |
| `ornith-1.5-mtplx-35b` | `127.0.0.1:18120` | 量測時用過 |

**已知未修**：`qbr/scripts/compare_engines.py` 的 `QWEN38_MODEL` 預設是 repo-id，
但 server 服務的是檔案系統路徑 → **HTTP 400**。

---

## 5. 待決策議題

按「擋住什麼」排序：

| # | 議題 | 擋住什麼 |
|---|---|---|
| 1 | **新佇列補圖**（wire `crop_run_figures` + 修 `has_visual_asset`） | 437 題 blocker 不可審；要先決定綁定在哪一層 |
| 2 | **驗收標準到底是什麼**（第 6 節） | 「兩引擎一致」沒被執行；在改正之前，無法說某卷「已驗收」 |
| 3 | **S6 治理缺口**：`review_status=accepted` 由誰決定 | 4 卷永遠發布不了；這是 GOV-05 的政策題，不是程式題 |
| 4 | **醫師(二) 6 卷 `count-mismatch`** | 同一症狀；值得單獨一輪 |
| 5 | **佇列持久化**：`qbr/data/review-queues/live` 被 `.gitignore` 第 27 行忽略 | 能過重開機，**不在版控**；長期備份未決定 |
| 6 | 486 題短文不在題組 | 是否為未偵測的共用題幹 |
| 7 | 提示詞第 4 條重寫 | 缺陷偵測誤報 |
| 8 | `compare_engines.py` 模型 id | 兩引擎比較工具跑不動 |

**已決定（不要重開）**：

- **只審新管線的題，不與舊 SQL 合併。**（舊制度不混入。）
- **不把視覺重讀接成 triage 佇列。** 精確率 12–16%。
- **`--carry-from` 必須指向佇列根目錄**，不是 `review-ui/`；指錯會靜默漏帶。
- 改進用**取代**，不是分岔；**儲存的字永不重寫**（偵測，不是轉換）。

---

## 6. 最重要的更正：驗收標準沒有被執行

`qbr/AGENTS.md` 第 40 行與多份文件都寫：

> 驗收標準是**兩個引擎一致**，不是測試全綠。

**但實際跑的程式沒有做這件事。**

| 檢查 | 結果 |
|---|---|
| `golden_path.py` 呼叫 `extract_lines_b` 或 `extract.compare` | **0 次** |
| `batch_run.py` / `batch_package.py` 同上 | **0 次** |
| `S2_dual` 的 `ok` 是什麼 | **分段連續性**（anchor／gaps），不是 A/B 一致 |
| `disputes.of_question(engine_counts=...)` | **從未被傳入** → `engine-disagreement` 是**死碼** |
| `/tmp/qbr-expand` 的 `package_report` 有 A/B 欄位嗎 | **沒有** |

`stage_dual` 確實**讀了兩個引擎**（`analyse_items` 讀 `rows_a`、`rows_b`），
但 B 只被當成**分段的額外候選視圖**（`extra_views`），**不是**被拿來跟 A 逐項比對。

### 我自己量的（40 卷隨機抽樣，遮罩後、Level-0 修復後都一樣）

| 分類 | 卷數 |
|---|---|
| `TEXT_DISAGREEMENT` | **31** |
| `STRUCTURAL_DISAGREEMENT` | **9** |
| `EXACT_AGREEMENT` / `SAFE_NORMALIZED_AGREEMENT` | **0** |

（40 卷，`random.seed(42)` 且 `glob` 先排序；未排序會跳成 32/8。內容 sha12 = `4b7def38762c`。）

`content_similarity`：min 0.98158、median 0.99723、max 1.00000（≥0.999 只有 9/40）。

**兩引擎讀出的題數相同：38/40。** 2 卷差很多：

```
1011_藥師_藥事行政與法規 : A=50  B=3
1032_醫師(二)_醫學(六)   : A=80  B=2
```

### 所以文件裡那個「3,516/3,516 兩引擎一致」是什麼

- **3,516 = 語料裡的主考卷總數**（不含 `_ANS` / `_MOD`），不是已打包的卷數（984）。
- 產生它的輸出檔（`qbr/data/survey*`）**不在版控、也不在磁碟上**，我**無法重現**。
- 如果「一致」指的是 `EXACT_AGREEMENT`，抽樣是 **0/40**，不是 100%。
  如果指的是「題數相同」，抽樣是 **38/40**，也不是 100%。

**我不是在說那個量測造假，是在說它無法被重跑驗證，而且它命的指標不是出貨路徑在算的指標。**
**在把標準寫成可執行之前，「已驗收」不等於已驗收。**

---

## 7. 如何重測（這份文件會過期，指令不會）

```sh
cd tw-national-exam-catalog/qbr
```

### 佇列現況

```sh
Q=qbr/data/review-queues/live/review-ui          # 現行 live 佇列
python3 - <<'PY'
import json, collections
Q="qbr/data/review-queues/live/review-ui"
n=0; opt=collections.Counter(); d=collections.Counter(); sev=collections.Counter()
g=collections.Counter(); img=ai=0
for line in open(Q+"/candidates.jsonl", encoding="utf-8"):
    r=json.loads(line); n+=1
    opt[len(r.get("options") or [])]+=1
    if r.get("image_refs"): img+=1
    if r.get("ai_review"): ai+=1
    if r.get("group_ref"): g[r.get("group_size")]+=1
    sev[r.get("dispute_severity")]+=1
    for x in (r.get("disputes") or []): d[x.get("kind")]+=1
print("題",n,"| 選項數",dict(opt),"| 有圖",img,"| ai_review",ai)
print("disputes",dict(d)); print("severity",dict(sev)); print("題組",dict(g))
PY
```

### 跨引擎一致性（本文件第 6 節的量測）

```sh
.venv/bin/python - <<'PY'
import sys, json, glob, os, random, collections
sys.path.insert(0, "src")
from qbr import extract, repair
ROOT = "/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog"
dirs = sorted(d for d in glob.glob("/tmp/qbr-expand/*/*") if os.path.exists(d + "/package/manifest.json"))
random.seed(42); res = collections.Counter()
for d in random.sample(dirs, 40):
    rel = json.load(open(d + "/package/manifest.json", encoding="utf-8"))["provenance"]["source_sheets"]["question"]["relative"]
    pdf = os.path.join(ROOT, rel)
    if not os.path.exists(pdf): continue
    ta = repair.text_from_rows(repair.mask_chrome(extract.extract_lines_a(pdf))[0])
    tb = repair.text_from_rows(repair.mask_chrome(extract.extract_lines_b(pdf))[0])
    res[extract.compare(ta, tb)["classification"]] += 1
print(dict(res))     # 預期：TEXT_DISAGREEMENT 31、STRUCTURAL_DISAGREEMENT 9、EXACT 0
PY
```

> **`sorted()` 不能省。** `glob` 的順序由檔案系統決定，不加 `sorted()` 時同一份種子
> 抽到不同的 40 卷，結果會在 31/9 與 32/8 之間跳動。我第一次就是這樣得到兩個不同的數字。
> **一個會跳動的數字不是量測。**

### 圖片（新佇列應為 0——若不再是 0，這份文件已過期）

```sh
ls qbr/data/review-queues/live/review-ui/crops 2>/dev/null || echo "沒有 crops/：圖未綁定"
grep -c '"image_refs": \[{' qbr/data/review-queues/live/review-ui/candidates.jsonl || true
```

### 被擋的卷

```sh
python3 - <<'PY'
import json, glob
for f in glob.glob("/tmp/qbr-expand/*/package_report.jsonl"):
    for l in open(f, encoding="utf-8"):
        r = json.loads(l)
        if r.get("status") == "blocked":
            print(r["name"][:44], "|", r.get("stdout_tail"))
PY
```

### 測試

```sh
.venv/bin/python -m pytest tests/ -q      # 現況：237 passed, 0 skipped
```

> 17 個測試靠 `/tmp/qbr-golden-001`、`/tmp/qbr-golden-002` 的真實產物。這兩個目錄被清空後，
> `skipif` 會把「讀不到證據」變成「不必驗」（總數 237 不變，**靜默**）。
> 重建方式見 `build-exam-question-bank` skill；**測試變少要當成缺陷，不是正常。**

### 服務

```sh
# 8774 = 新管線（jsonl 後端）。埠是契約，不要改。
python3 scripts/serve_question_review_ui.py \
  --candidate-jsonl qbr/data/review-queues/live/review-ui/candidates.jsonl \
  --issue-csv      qbr/data/review-queues/live/review-ui/issues.csv \
  --review-log     qbr/data/review-queues/live/review-ui/question_review_events.jsonl \
  --review-backend jsonl --host 127.0.0.1 --port 8774
```

`v2` 有「**爭議**」晶片（客戶端過濾，`data-view="disputed"`），數字就在晶片上——
這是「只看有問題的」入口，不需要模型。

---

## 8. 讀這份文件的三個陷阱

1. **「S2 通過」不等於「兩個引擎一致」。** 它等於分段連續性。看第 6 節。
2. **「有圖」要看 `image_refs`，不要看 `has_visual_asset`。** 後者寫死 `False`，永遠是假的。
3. **`group_position` 從 0 開始。** `0` 是組頭，不是缺號；用 `range(1, …)` 檢查會得到
   908 組全部「不連續」的假缺陷（我犯過）。
