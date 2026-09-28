# 跨頁 ＋ 有圖 ⇒ 截圖幾乎必錯：根因與量測

> 設計者問題（Q13）：「如果題目有跨頁並且有圖片，則幾乎都會錯誤，這是什麼原因」
> 對應 [`qa-log.md`](qa-log.md) **Q13**；探針 `probes/measure_crosspage_figure.py`；
> 量測結果 `~/models/glm-ocr/runs/crosspage-figure-live.json`（筆電＝修復前）
> 與 `...-station.json`（站上＝修復後）。

## 0. 一句話

**一張裁切 ＝ 一頁上的「一個矩形」。跨頁題目的圖印在「文字列所在那一頁之外」**
（上一頁頁尾／下一頁頁首），而舊碼是**從題目的文字列**去畫那個矩形 → **框到文字、框不到圖**。

## 1. 為什麼「跨頁＋有圖」是必錯組合（不是機率）

舊 `vision.py::figure_questions` 對**續頁**的規則（docstring 記 `1112_藥師(一)_藥理學與藥物化學` Q80）：

```
if page != first_page:
    box = (box[0], 0.0, box[2], box[3])      # 上緣拉到頁頂，為了抓印在頁首的圖
```

這一條**單獨看是對的**。但 `crop_run_figures.py::question_region` 對**最後一頁**的規則相反：

```
elif index == len(pages) - 1:
    out[page] = (-inf, hi)                    # 頁首到這一題的最後一列 → 又把頁首的圖切掉
```

兩條規則互相抵消：**跨頁題的圖在頁首 → 一張抓、一張丟 → 幾乎必錯。**
非跨頁題不走這條路 → **這就是「只有跨頁才會錯」的全部來源。這條路徑上沒有隨機性。**

`question_region` 的 docstring 逐字記著設計者 2026-09-25 的回報，含案例：

> `q019` 的列在第 2、3 頁，紙本第 3 頁那張圖在 **y 28.1–327.4**，而截圖是 **y 331.6–386.0**
> （＝這一題在第 3 頁的**文字**）；`q055`／`q040`／`q060`／`q036` 同一類。

**圖與框完全不重疊**——錯誤形狀是「框到答案列、圖在框外」，不是「框稍微偏」。

## 2. 活案例（親眼驗證）

`1152_物理治療師_物理治療基礎學(包括解剖學、生理學、肌動學與生物力學)` **q78**
（題幹第 15 頁頁尾 y702.8；**兩張吸塵器姿勢照片在第 16 頁頁首** y28.8–285.8，
7 條 JPEG 掃描帶 xref 50–56；選項 A–D 在 y298.4–390.4）：

| | 裁切框 | 內容 |
|---|---|---|
| 修復前 | `[39.2, 298.4, 539.5, 390.4]` | **只有選項文字，圖完全不見**（62,621 B，1391×257）|
| 修復後（站上）| `[45.4, 28.8, 494.6, 285.8]` | **就是那兩張照片**（808,349 B，1249×715）|

`figure_questions` **自己的** entry box 是 `(39.229, 0.0, 539.5, 390.398)`——**本來是對的**；
是後面的 ownership pass 把它切壞的。

## 3. 修復與指紋

修復在 **2026-09-25**（`vision.py` 21:42、站上重切 18:36–22:36）。
判別指紋：**裁切上緣 = 0.0 的張數**。

| 指標 | 筆電（修復前，09-24 21:07） | 站上（修復後，09-25 22:28） |
|---|---|---|
| 從**頁頂起**（`box[1]==0.0`） | **699** | **2** |
| 有 `pages` 欄（縫頁） | 0 | 669 |
| 有 `widened` 欄 | 0 | 661 |
| 有 `whole_picture` 欄 | 0 | 278 |
| 裁切總數 | 4,590 | 4,480 |

## 4. 修復後還剩什麼（同一把尺，`--candidates` 換成站上那份）

| 指標 | 跨頁 | 不跨頁 |
|---|---|---|
| 把圖切掉 `cuts_a_figure` | **0.47%** (8/1700) | 1.22% (34/2780) |
| 框到別題文字 `contains_other_question` | 0.41% (7/1700) | 0.76% (21/2780) |
| 標成圖、框內無圖 `has_no_figure` | 0.35% (6/1700) | 0.65% (18/2780) |

→ **修復後「跨頁⇒幾乎都會錯」不再成立；跨頁反而比不跨頁好。**

殘留的主要是**兩種別的形狀**（都非跨頁特有）：

1. **向量圖**（化學結構式是線條指令不是點陣圖）→ `get_images()` 回 0。
   親眼見 `1131_藥師(一)_藥理學與藥物化學` p11：Q55／Q56 兩張結構式在裁切框裡、`get_images: 0`。
   **＝ D4 缺陷 A，1.3%。** 見 [`vector-figure-gap.md`](vector-figure-gap.md)。
2. **「選項 D」裁在續頁頁首**（20 筆，**全是 `選項 D`**）→ 選項自己的圖印在下一頁頁首，是正常版面。

負對照（必須為 0）：**不跨頁卻從頁頂起 = 0**（修復前後皆 0）；不跨頁卻在續頁 = 20
（就是上面那 20 筆「選項 D」，屬正常版面而非缺陷）。

## 5. 這一課（最該記下的）

**筆電的 `qbr/data/review-queues/live/` 是 2026-09-24 的舊快照，不是權威；權威在站上。**
（review events：站上 **20,322** vs 筆電 5,232。）在筆電上量，量到的是**修復前**的世界。
若沒有去比對站上，就會把一個**已經修好的 bug** 當成現在還開著報出去。→ SKILL.md §9.4 **A6**。

## 6. 重現指令

```bash
# 站上是權威：先抓下來
scp -i ~/.ssh/ai_learning_platform_deploy tim@100.96.146.93:~/qbr-review/queue/review-ui/candidates.jsonl /tmp/stationq/candidates.jsonl

# 同一支探針，量兩份（筆電＝修復前／站上＝修復後）
cd tw-national-exam-catalog
qbr/.venv/bin/python repair_agent_test/probes/measure_crosspage_figure.py \
    --candidates qbr/data/review-queues/live/review-ui/candidates.jsonl \
    --root . --out ~/models/glm-ocr/runs/crosspage-figure-live.json
qbr/.venv/bin/python repair_agent_test/probes/measure_crosspage_figure.py \
    --candidates /tmp/stationq/candidates.jsonl \
    --root . --out ~/models/glm-ocr/runs/crosspage-figure-station.json
```

## 7. 未量／待決

- **修復尚未進 git**：`qbr/src/qbr/vision.py` 與 `qbr/scripts/crop_run_figures.py` 是**未提交的工作樹改動**
  （共 1,875 行）。已部署到站上，但**不在任何 commit** → 下一個人 clone 會拿到舊碼。**G1 缺口。**
- 站上 42 筆 `cuts_a_figure` 只抽看 2 筆（皆為向量圖或正常版面）；**其餘未逐一眼視**。
- 24 筆 `has_no_figure` 只驗 2 筆（皆為 D4 向量圖缺口）；**其餘未量**。
