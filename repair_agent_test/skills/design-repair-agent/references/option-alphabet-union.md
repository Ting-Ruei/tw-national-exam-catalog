# `option_alphabet` 的 union bug —「全卷 0 選項」的真根因

> **這份文件取代的不是別的檔案，而是一個錯誤的優先序。**
> D14 原案要修 `extract.py::image_bytes_of`；那份原案的前提（「內嵌圖 `xref=0` 拿不到 bytes」）
> 是探針假象。真根因在這裡。D14 原文與更正見
> [`vector-figure-gap.md`](vector-figure-gap.md) §8 與 [`qa-log.md`](qa-log.md) Q16。

**一句話**：`repair.option_alphabet` 回傳了**所有** private-use family 的**聯集**；
一份論文若印兩個 family，聯集排序後會把**真正的選項家族**排在 `B`/`E` 的位置，
`skeleton` 找 `opt:A` 就找不到 → **整卷每一題的選項都不見**。

---

## 1. 機制（純紙張性質，讀一行程式就懂）

```python
# qbr/src/qbr/repair.py（修正前）
return {code for family in families for code in family}      # union of every family
```

```python
# qbr/src/qbr/reflow.py::cells_with_pages（line 264）
alphabet = tuple(alphabet) or tuple(sorted(repair.option_alphabet(...)))
```

```python
# qbr/src/qbr/reflow.py::line_table（line 200）
labels = {code: canon.LABELS[index] for index, code in enumerate(alphabet) ...}
# canon.LABELS = ("A", "B", "C", "D", "E", "F")
```

```python
# qbr/src/qbr/reflow.py::skeleton（line 814）
first_option = next(cell for cell in window if option_cells[cell] == "A")   # 找不到就沒有選項
```

實例（`1001_醫事檢驗師_臨床生理學與病理學`）：

- `\ue000`-`\ue003`：Q65 的**四個子項**（`\ue000細菌 \ue001白血球 \ue002紅血球 \ue003葡荀糖`）
- `\ue18c`-`\ue18f`：**每一題的四個選項**

`sorted(union)` ＝ `e000,e001,e002,e003,e18c,…` → `\ue18c` 被標成 **`E`**，不是 `A`。
**全卷 80 題 options 全空。**

---

## 2. 量到的規模（不是抽樣，是全語料）

| 量測 | 數 |
|---|---|
| 全語料論文（有文字層，`10_official_pdf/by_official_catalog/*/*/*/*.pdf`） | **8,349** |
| 印兩個以上 family、且 union 把 option family 誤標 | **667** |
| 其中 union 真的讓**全卷 0 選項** | **658** |
| 修正後（最常用 family）取回完整選項 | **649** |
| 修正後**變差**的篇數 | **0** |
| 手工參考庫 recall（`tw-national-exam-medtech-v2026.08.04`） | 447/462 → **449/462** |

受影響類科（667 篇）：社會工作師 192、營養師 153、諮商心理師 123、臨床心理師 60、
法醫師 58、公共衛生師 30、中醫師 19、中醫師(二) 15、醫師(二) 9、中醫師(一) 3、
語言治療師 2、醫事檢驗師 2、聽力師 1。

**上一輪我報的「26 篇」是錯的**（只掃了 8 類科）。全語料是 **667 篇**。

---

## 3. 為什麼「最常用 family」是對的（不是猜的）

- 選項標籤**每題印一次**（四題各一次）；子項標籤**只在題目問到它們時才印**。
- ⇒ 選項家族的**總數必然較大**。這是**紙張的性質**，不是語意推論。
- 量測佐證：334 篇兩 family 的論文裡，**最常用 family 的第一個碼永遠是 `0xe18c`**；
  union **從未**比最常用 family 多拿到選項（`union_better = 0`）。

---

## 4. 修法（一處）

```python
# qbr/src/qbr/repair.py::option_alphabet
families = option_alphabet_families(text)
return set(families[0]) if families else set()
```

- **唯一呼叫者** `reflow.cells_with_pages` 不需改（它本來就吃 `option_alphabet` 的回傳）。
- `option_alphabet_families` **不動**：`repair.split_at_bullets`（`repair.py:1124` 附近）
  仍需**逐 family 試**（多選題的題幹用子項家族寫選項）。
- `packaging`（`golden_path.py → three_way.analyse_items → repair._segment_mixed`）走的是
  **逐 family 試**的路，**本來就不受 union 影響**，所以 shipped candidates 沒壞。

---

## 5. 負對照（設計者紀律：檢查要能證明自己會說謊）

`qbr/tests/test_reflow.py::test_a_paper_with_two_private_use_families_labels_its_options`

- **在舊 union 行為下必失敗**（斷言 `canon.LABELS[union.index(0xE18C)] == "E"`，即選項被標成 `E`）。
- **在新行為下通過**（alphabet == `(0xE18C, 0xE18D, 0xE18E, 0xE18F)`）。
- 我兩種行為都實測過：`1 failed`（舊）→ `1 passed`（新）。

`test_the_second_family_is_still_kept_for_the_question_that_is_printed_with_it`
釘住「第二個 family 仍交給 bullet splitter」，避免修正把多選題功能一併拿掉。

---

## 6. 重現

```sh
cd tw-national-exam-catalog
# 全語料盤點：667 篇誤標（負對照 union_better 必須為 0）
qbr/.venv/bin/python repair_agent_test/probes/census_option_alphabet_union.py \
    --out-dir /tmp/option-alphabet-union
# 667 篇上量固定效果：658 篇 union 下 0 選項、649 篇修後全取回、0 篇變差
qbr/.venv/bin/python repair_agent_test/probes/measure_option_alphabet_union_impact.py \
    --mislabeled /tmp/option-alphabet-union/mislabeled.json \
    --out /tmp/option-alphabet-union/impact.json
# 單元測試（56 passed）
qbr/.venv/bin/python -m pytest qbr/tests/test_reflow.py -q
# 全套（749 passed；20 個 failure 是 HEAD 既有，依賴未提交的跨頁/表格 WIP）
qbr/.venv/bin/python -m pytest qbr/tests/ -q
```

PR：`agent/fix-option-alphabet-union-20260927`（只含 3 檔）。

---

## 7. 這一課

1. **「設計者指定的修法」也要先驗前提。** D14 看起來很具體，但前提是**我的探針自己造出來的**；
   `image_bytes_of` 連引入時都沒有呼叫者。**照原案改會「綠燈、零改變」。**
2. **每個檢查都要負對照。** 如果 inline 探針當年問「同一個 bbox 用**生產路徑**裁得到嗎」，
   就不會得出「151 張 100% 失敗」。
3. **設計者說 D14「影響所有線路」是對的**——只是被指到了死碼。真兇
   （667 篇／13 類科）確實跨全語料。
4. **`leftmost_cells` 的跨頁 WIP 仍未提交**（G1 缺口），本次未觸碰。

---

## 8. 未解（與 union 無關，本輪新發現）

修正後仍有 **11 篇**「top alphabet 正確、但 `skeleton` 找到 0 題」——
題號自成一格（`35` 與題幹是**兩個 cell**），`_QUESTION_LEAD` 接不上。
例：`1021_營養師_膳食療養學`（`reflow.skeleton` 只找到 5 列）。
**這是獨立的題號偵測問題，不是 union bug。**
