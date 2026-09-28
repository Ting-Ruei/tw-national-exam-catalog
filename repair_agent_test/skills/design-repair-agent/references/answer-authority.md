# 答案審核的正確性：位移與修正答案（2026-09-27 查證）

**性質**：**查證報告**，唯讀，不授權任何變更。
**觸發**：業主 Q8 —「我們好像都沒有討論到答案審核的正確性，我個人比較擔心的是**位移**以及**修正答案**。」
**紀律**：每條「事實」附檔名:行號；推論標「我的判斷」；未量的標「未量」。

---

## 0. 業主的兩句話（原話）

> 「我個人比較擔心的是**位移**以及如果考題剛放出來的時候只有標準答案，但是等成績正式公告的時候會有**修正答案**，這時候當然要**以修正答案為主**，但是第一次建立大量題庫的時候都是**往回看**，也就是可以一眼就看清楚倒底要遵守的答案是哪一份，但未來會有一個**答案修正的動作需要在自動化初期監控**，Agent要**回報有改過答案**，然後**人要介入來觀看**」

拆成三個問題：

| # | 業主問的 | 這份報告的一節 |
|---|---|---|
| A | **位移**（題號對位錯）會不會發生、有沒有檢查 | §2（**未量**） |
| B | **修正答案**（MOD）有沒有被正確採用為權威 | §1（**邏輯對**） |
| C | 未來自動化時，**答案被修正**要被回報、人要介入 | §3（**這條路不存在**） |

---

## 1. 修正答案的採用：核心邏輯是對的（事實）

### 1.1 三段優先序，且寫得很講究

`qbr/src/qbr/corrections.py:221-266` — `authoritative_answers(answer_texts, correction_texts, options_by_number=None)`：

docstring 自己寫（`:222-249`）：

```
"""The answers as the Examination Yuan last stated them, with the corrections' meaning.

Three sources have to be combined, and the order between them is the whole point:

  1. the answer sheet (ANS) - the answers as first published
  2. the corrections sheet (MOD) - the same table re-issued, with `＃` in the cells that
     changed, so where it prints a letter that letter is the answer *now*
  3. the notes at the foot of the corrections sheet - what `＃` means

Step 2 is the part that is easy to miss. A corrections sheet is not only a note; it
reprints the whole table, and reading only its notes leaves the other seventy-eight
answers to come from the older sheet. On 115090 the two agree, which is exactly why the
mistake would have gone unnoticed - until a paper where they do not.

A `＃` cell is skipped by `parse_answer_table` rather than guessed at, which is the
right behaviour: the letter is in the note, and a guess would hide whether the note was
read.
"""
```

**四件做對的事**：

| # | 做對的 | 為什麼重要 |
|---|---|---|
| 1 | **MOD 是「整張表重印」**，不是只有註記 | 只讀註記 → 其他 78 題會退回舊卷 |
| 2 | **`＃` 格跳過不猜** | 用猜的會看不出「腳註有沒有被讀到」 |
| 3 | **腳註與表情分開處理**（`AnswerCorrection` 型別） | 「意思」是 tuple 帶不動的 |
| 4 | **送分題用「該題實際提供的選項」**（`options_by_number`） | 塞 A–H 給四選項的題 → gate 正確地隔離（實測 115090 q10/q41） |

### 1.2 合併的實作

```python
table = {}
for text in answer_texts or ():                    # 1. ANS 先建表
    table = canon.merge_answer_tables(table, canon.parse_answer_table(text or ""))

corrections = {}
revised = {}
for text in correction_texts or ():                # 2. MOD 重印表
    revised = canon.merge_answer_tables(revised, canon.parse_answer_table(text or ""))
    for number, correction in parse_corrections(text or "").items():   # 3. MOD 腳註
        ...
```

並在 `:264-265` 處理「兩份 MOD 都說要送分」→ 合併成一筆 `void`。

### 1.3 golden 檔可見結果

`qbr/tests/golden/golden_1152_medtech_biochem_candidates.jsonl`：

```json
"metadata": {
  "answer_authority_source": "answer+corrected",
  "answer_pdf_relative": ".../1152_醫事檢驗師_生物化學與臨床生化學_ANS.pdf",
  ...
}
```

`answer_authority_source` 的值由 `qbr/src/qbr/package.py:141-187` 的 `build_question(..., answer_source=...)` 寫入；呼叫端是 `qbr/scripts/golden_path.py:537-569`：

```python
for role, texts in (("answer", answer_texts), ("corrected", correction_texts)):
    ...
if number in corrections_by_number:
    said.append("corrected")
...
answer_text = correction.as_answer()
if correction.is_void:
    flags.append("answer-voided-by-correction")
else:
    flags.append("answer-widened-by-correction")
```

→ **修正答案是最終權威，且有 flag 記錄「這題的答案被修正影響過」。**

**判定：業主的「以修正答案為主」在核心邏輯上已經實作，而且有測試與 golden 檔。**

---

## 2. 位移（題號對位錯）：未量，且沒有專用檢查

### 2.1 既有的答案 gate 是什麼（事實）

程式裡有 `answer-not-on-sheet` 這類檢查——**它檢查的是「答案字母有沒有出現在該題的選項裡」**，用途是抓「答案指到一個不存在的選項」（例如四選項題收到 `E`）。

**它不是位移檢查。** 位移是「答案對到隔壁題」——`q037` 的答案被讀成 `q038` 的答案，**兩個字母都合法、都在選項裡**，所以現有的 gate 不會響。

### 2.2 我找到的相關防護（事實）

| 機制 | 位置 | 防的是什麼 |
|---|---|---|
| `authoritative_answers` 的 `options_by_number` | `corrections.py:240-245` | 送分題的選項範圍（**不是**位移） |
| `verify(question, subs)` | `dispute_apply/cli.py:266` | 文字修復的自我一致性 |
| 題號連續性驗證 | `qbr/src/qbr/repair.py` 的 `detect_anchor_style()` | **題目**卷的題號格式（不是答案卷對位） |

### 2.3 未量（誠實）

- **位移發生率**：未量。
- **答案卷與題目卷的題號對齊是否被獨立驗證過**：**未查到有這個檢查**（未量其後果）。

**我的判斷**：這是一個**可量、且應該量**的既有缺陷。量法（我的構想，未實作）：

1. 取一批**已有人 accept** 的卷（人的 accept 是「這題的答案我確認過」的證據）
2. 比對「答案卷題號 N 的字母」與「題目卷題號 N 的選項數」是否匹配
3. 額外的獨立訊號：**答案卷的題數** vs **題目卷的題數**（位移通常伴隨數量差異）

---

## 3. 自動化下「答案被修正」的回報：這條路不存在（事實）

業主的要求是：

> 「未來會有一個**答案修正的動作需要在自動化初期監控**，Agent要**回報有改過答案**，然後**人要介入來觀看**」

### 3.1 三個缺口

| # | 缺口 | 出處 | 後果 |
|---|---|---|---|
| 1 | **取得修正卷沒有排程** | `qbr/scripts/fetch_corrections.py:1-14`；全樹 grep `.sh`／`.plist` 對 `fetch_corrections` **零命中** | 自動化後**沒有人會去抓**新的修正卷 |
| 2 | **顯示的 fallback 把修正卷排在最後** | `review_ui/review_state.py:3637`：`answer_pdf_primary_relative` → `answer_pdf_relative` → `corrected_answer_pdf_relative` | 人看到的右側 PDF **預設不是**修正卷 |
| 3 | **答案區沒有「修正答案卷」文件種類** | `review_ui/v2/03-area-answer.js:50-54`：只有 `官方 PDF / MinerU layout / MinerU origin` | 人**無法在答案區切到**修正卷 |

### 3.2 `fetch_corrections.py` 自己說的話（原文）

```python
"""Fetch corrections sheets (MOD) for a category, including ones the catalog predates.

The catalog's `has_correction` column was right when it was built and is not right now. The
Examination Yuan publishes corrections *after* the results, so a paper and its answer sheet
are catalogued months before the note that changes two of its answers exists. Measured on
115090 (115年第二次, 醫事檢驗師, 生物化學與臨床生化學): the catalog says `has_correction = no`,
the file was not on disk, and the endpoint served a valid corrections PDF that same day.

So the catalog is used as a *hint*, never as the authority, and the authority is the endpoint.
```

→ **業主的直覺完全正確**：「第一次往回看」時一眼看得清（MOD 就在旁邊）；**但「後來才出現的修正」需要一個主動的動作**，而那個動作現在是**手動的**。

### 3.3 沒有任何事件說「這題的答案被改過」

**事實**：`question_review_events.jsonl` 有 `correct`（人改題目文字），但**沒有**任何事件類型專門說「答案權威來源換了」或「答案字母從 X 變 Y」。

**我的判斷（構想，非實作授權）**：可以複用既有形狀，不需要新開一條流——

| 需要的欄位 | 可複用 |
|---|---|
| 哪份卷／哪幾題 | `candidate_key` |
| 舊 → 新 | 既有 `changes[]` 的 `{field, from, to}` 形狀 |
| 來源檔 | `crop`（既有「沒看到的證據不是證據」的欄位）＋ MOD 的 digest |
| append-only | `reset_review` 事件（既有）＋ 新的 reviewer 簽名 |

**但這需要業主核准**（它會動到答案權威的呈現），所以列為 **D6**。

---

## 4. 對「往後看」與「往回看」的分野（我的判斷）

業主自己講出了這個分野，值得當設計約束：

| 情境 | 特性 | 需要什麼 |
|---|---|---|
| **第一次建大量題庫（往回看）** | 答案已定案，MOD 與 ANS 同時可得 | **一次定案**即可，不需要監控 |
| **發布後才出現的修正（往後看）** | 修正卷在成績公告後才出現 | **需要回報 ＋ 人介入**（D6） |

→ **不要為「往回看」蓋監控機制**（它不會再變）；**只為「往後看」蓋**。

---

## 5. 未量（誠實清單）

- **位移發生率**（未量；且未查到有對應檢查）
- **現有已建題庫中，有多少題的答案來自 MOD 而非 ANS**（未量；golden 檔只證明機制可用）
- **`fetch_corrections.py` 上次實際執行是什麼時候、抓到幾份**（未量）
- **答案區的修正卷顯示問題對人的實際影響**（未量；只知道排序在最後）
- **答案卷題數 vs 題目卷題數的一致性**（未量）
