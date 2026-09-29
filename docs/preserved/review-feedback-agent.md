# 審題回饋 Agent（review_feedback）— 保留現況與待決設計

狀態：**保留（preserved），尚未實作完成，待另案設計**
建立日期：2026-09-23（Asia/Taipei）
裁決者：owner

本文件不是規範，也不是現行流程說明。它凍結「目前這條線留下了什麼」，
讓未來的設計討論從事實開始，而不是從重建記憶開始。

---

## 1. 為什麼保留這條線

Owner 的原始意圖（2026-09-23 口述）：

> 我在前端審核題目，他在後端用本地 AI 修復問題，找出規則，以及自我進化，
> 用後端長時間運行節省花費並換取題目的可用性，未來需要人工介入的題目會越來越少。

這是一個**後端常駐 agent** 的構想，不是一支腳本。判斷「沒有做好」的理由是
有證據的：**後端沒有任何 agent 迴圈**。全 repo 搜尋
`feedback_agent` / `_feedback_worker` / `guardrail_worker` 的
`def` 與 `thread` —— **零命中**。

現在實際存在的程式碼，是這個構想的**回收端**（把人類修正變成可分析的事件），
而不是**進化端**（讓 AI 長時間跑、收斂、產出規則並自我驗證）。

---

## 2. 目前實際存在什麼（實測）

### 2.1 `scripts/review_feedback.py`（444 行，純函式模組）

定位：把一筆人類修正或三證據修正，轉成**不可變的 before/after 封包**。

公開介面：

| 函式 | 作用 |
|---|---|
| `question_snapshot(value)` | 抽出「審題者看得見的欄位」快照；每欄有長度上限 |
| `apply_correction(before, correction)` | 在記憶體中算出修正後長相，**不回寫題庫** |
| `diff_snapshots(before, after)` | 產生逐欄差異 |
| `classify_change(...)` | 判定變更類別（含單字元變更的特殊處理） |
| `build_feedback_event(...)` | 產出不可變回饋事件 |
| `build_ai_task(event)` | 產出**送給模型的邊界封包**（見 2.3） |
| `build_guardrail_candidate(...)` | 產出護欄候選骨架 |

**純度已實測**：無 `open(` / `.write` / `psycopg` / `requests` / `subprocess`；
只 import `copy, difflib, hashlib, json, datetime, typing`。
所以它**不可能**改題目、不可能碰資料庫、不可能呼叫模型。docstring 的自述為真。

兩個拒收例外是設計防線，不是雜訊：

- `FeedbackContractError` — 事件無法安全表達
- `NoVisibleChange` — 人類按了修正但前後看不出差異 → **拒收**，不製造噪音

### 2.2 資料流（現況）

```
人類在審題介面按「修正」
        │
        ▼
review_state.py ──呼叫──► review_feedback.py
   │                         (純函式，只算不寫)
   ▼
question_correction_feedback_events.jsonl   ← append-only
   （或 SQL 表 exam.question_guardrail_candidates）
```

實際存在的伺服器端方法（`qbr/src/qbr/review_ui/review_state.py`）：

| 行 | 方法 |
|---|---|
| 660 | `_ensure_ai_feedback_schema` |
| 5428 | `_insert_sql_ai_feedback_event` |
| 5528 | `_insert_sql_correction_feedback_event` |
| 6037 | `_raw_candidate_for_feedback` |
| 6042 | `_feedback_before_snapshot` |
| 6063 | `_append_correction_feedback` |
| 6113 | `_record_question_correction_feedback` |
| 6161 | `_record_answer_correction_feedback` |
| 6719 | `append_ai_feedback` |

SQL 表：`exam.question_guardrail_candidates`，含 `status`、`created_at`、
`feedback_id` 索引；狀態欄位另見 `guardrail_active`、
`guardrail_owner_approval_required`。

### 2.3 `build_ai_task()` 送出去的封包（這是設計意圖的化石）

```python
"task_type": "guardrail_candidate_review",
"instruction": (
    "判斷這次前後差異是否可形成可重用護欄。只能提出候選，不得直接修改題目、"
    "不得宣告 active、不得寫檔。若證據不足，回 no_generalization。"
),
"output_shape": {
    "status": "candidate|no_generalization|unclear",
    "guardrail_type": "exact_ocr_rule|notation_rule|format_rule|crop_strategy|"
                      "answer_rule|parser_route|skill_note|no_generalization",
    "confidence": "number 0..1",
    "rule_proposal": "object or null; observed/proposed only",
    "skill_update_candidate": "object or null; prose patch only",
    "positive_examples": "array of feedback ids or bounded examples",
    "negative_controls": "array",
    "do_not_generalize": "array",
    "rationale": "short observable explanation",
}
```

這段是**最有價值的遺產**：它已經把「AI 只能提案、不能啟用」、
「必須附負控制」、「證據不足必須說 no_generalization」寫進契約。
未來設計不必重新發明這些約束。

### 2.4 這條線**沒有**的部分（缺口清單）

| 缺口 | 證據 |
|---|---|
| 沒有後端常駐 worker / 迴圈 | 搜尋 `def`/`thread` 零命中 |
| 沒有模型呼叫的實作 | 模組純函式；`build_ai_task` 只**產出封包**，不送出 |
| 沒有把候選升級成規則的自動路徑 | 只有 `owner_approval_required` 欄位 |
| 沒有「自我進化」的驗證機制 | 無正／負控制自動回歸 |
| **這條流沒有任何真實資料** | 筆電與常駐機的 `question_correction_feedback_events.jsonl` **都不存在** |
| 沒有 skill | `docs/skills/` 下無對應 skill |

最後一項要說清楚：**程式碼已就緒，但從未被實際使用過**。
所以任何「它表現如何」的討論，目前都沒有資料可依據。

---

## 3. 與 `ai395` 的關係（重要，避免混淆）

`scripts/ai395_feedback.py` 只有 8 行，內容是：

```python
from review_feedback import *  # noqa: F401,F403
```

它是一個**相容層**，用途是讓封存的舊 fixture 走同一個實作，而不是生出第二份
契約。`ai395` 系列已於 2026-09-23 由 owner 裁定不再適用於現行架構
（見 [`docs/preserved/ai395-retirement.md`](ai395-retirement.md)）。

**結論：`review_feedback.py` 不屬於 ai395。** 它是審題介面伺服器
（`review_state.py`）的直接硬依賴，服務的是現在運作中的審核流程。
`ai395_feedback.py` 才是 ai395 的殘留，隨 ai395 一起退場。

---

## 4. 保留清單（不動、不刪、不重構）

- `scripts/review_feedback.py` — 主實作，純函式，444 行
- `qbr/src/qbr/review_ui/review_state.py` — 伺服器端寫入路徑
  （行號見 2.2，**實測於 2026-09-23 review-ui server 拆分之後**；
  搜尋方法名比依賴行號穩定）
- `qbr/src/qbr/review_ui/events.py` — `load_correction_feedback_events`（行 101）、
  `load_correction_feedback_rows`（行 125）
- SQL 表 `exam.question_guardrail_candidates`
- `scripts/ai395_feedback.py` — 僅作相容層保留，**不新增依賴**

---

## 5. 待決設計問題（留給下一次討論）

以下每一題都需要 owner 決定，本文件**不做結論**：

1. **進化端放哪裡？** 現行架構沒有外部 AI 計算節點。Charter 要求未來任何 AI
   worker 都必須先定義 producer、權限、artifact store、版本與 checksum 契約。
   這個 agent 是否就是第一個正式申請的 AI worker？

2. **「長時間運行」的邊界在哪？** 常駐 agent 要讀多少題、跑多久、
   用什麼模型？（Charter 允許 Query Embedding 例外，但不允許 unrestricted LLM。）

3. **規則從哪裡寫回去？** 候選變 active 之後，規則要落在
   `qbr/prompts/`、`docs/skills/`、還是程式碼？Charter 說
   「凡是讀出文字的意義，都是提示詞，不是腳本」——這條線的產出屬於哪一類？

4. **負控制怎麼自動化？** `build_ai_task` 已要求 `negative_controls`，
   但誰來生成、誰來執行、失敗時怎麼辦？

5. **「人工介入越來越少」如何量測？** 需要一個可觀測指標（例如每千題人工
   修正數隨時間下降）。沒有這個，就無法區分「agent 有效」與「問題變簡單」。

6. **現有回饋流是否要啟用？** 目前零資料。要先確認
   `question_correction_feedback_events.jsonl` 的產生條件是否正確，
   再談 AI 分析。

---

## 6. 考古提示

`ai395` 系列另有一份完整的 open-model review 實作計畫
（`docs/ai395-open-model-review-implementation-plan.md`）。若未來設計這個 agent，
該文件可能有可複用的**契約設計**（provider registry、route registry、
budget policy、invalidation matrix）。但 ai395 的**部署與執行**已不適用，
不可直接復活。判斷依據見 [`docs/preserved/ai395-retirement.md`](ai395-retirement.md)。
