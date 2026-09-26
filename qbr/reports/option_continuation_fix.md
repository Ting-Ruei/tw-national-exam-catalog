# 選項續行被截斷：找到、修好、量到下降

> 這是本管線目前為止**最大的單一未標記缺陷**，也是驗收標準（`docs/skills/qbr-pipeline-status/SKILL.md`
> 第 6.1 節）第一次抓到的真缺陷。它現在已經修好，而且是**在出貨之前**修好的——不是先標記再讓人審。

## 一句話

紙本把一個選項印成兩列時，`segment_questions` 在折行處結束了那個選項，
把續行交給了題幹。出貨的選項因此停在句子中間，而題幹多了一句不屬於它的話。
**兩個引擎都把續行印出來，只有這一個迴圈把它丢掉。**

## 症狀（出貨佇列裡的樣子，不是假說）

```
1002_物理治療師_骨科疾病物理治療學 q10
  出貨 A : 薦髂關節疼痛可能對臀中肌（gluteus medius）造成反射性抑制（reflex inhibition），導致步態
  題幹   : 下列有關薦髂關節（sacroiliac joint）病變的敘述，何者正確？異常 iliac spine）均較右邊…
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ 這兩個字不屬於題幹
  紙本   : A.…導致步態 / 異常 / B.當發現左邊的前上腸骨棘…
```

`quality_status=pass`、`disputes=None`、`dispute_severity=None`。**審題的人看不到。**

## 根因（在 `repair.py` 裡讀出來的）

`segment_questions` 有一條折行測試，但**只給題幹的錨點用**（`_looks_like_a_wrap`，`repair.py:776`）。
選項**沒有**對應的測試：一旦選項已經開始，任何不是選項記號、也不是新錨點的行，
落到 `current["stem"].append(line)`。所以選項的續行被當成題幹的續文。

## 修法

`_continues_an_option` 是題幹那條的**同構**版本（`repair.py`）。一條行要算「某個選項的續行」，
必須同時滿足：

1. **已經有選項印出來過**（否則沒有選項可以續）。
2. **它不開頭**：整行沒有本卷字母表的任何記號，開頭也不是數字錨點（`^數字.`）。
3. **它不是版面裝飾**：頁尾、宣示的節標題、metadata、純標點都不屬於任何選項。
4. **它有文字**。

**刻意不是長度測試。** 題幹那條用 `<= 14`，因為題幹的折行是短語；選項的續行經常很長
（兩個引擎都看到的案例裡有一個 24 字的尾巴）。加長度上限會擋掉大多數真的損失。

續行用 `join_lines` 接回去——和題幹用**同一條**接字規則，所以中文折行不加空白。

## 量到的下降（同一個偵測器、同一批 160 卷、`seed 7`）

重測指令：

```sh
cd tw-national-exam-catalog/qbr
.venv/bin/python scripts/verify_option_continuation.py 160 7     # 已修
git stash push qbr/src/qbr/repair.py                             # 未修（負向對照）
.venv/bin/python scripts/verify_option_continuation.py 160 7
git stash pop
```

| | 未修 | 已修 |
|---|---|---|
| 讀取的題數 | 12,840 | 12,840 |
| 引擎 A 自己看到 | **658** | **0** |
| 引擎 B 自己看到 | 2,207 | 1,858 |
| **兩個引擎都看到（驗收數字）** | **344** | **1** |
| 受影響題數 | 235 | 1 |
| 受影響卷數 | 60 | 1 |

`loss_a` 從 658 到 0 不是換了一個數字，是**同一個引擎內部的兩種讀法從不一致變一致**
（切題器 `segment_best` 的產出 vs 逐欄讀取器 `continuation_losses`）。這是修復真的發生的第二個證據。

## 全庫的影響（984 卷 / 78,690 題，重跑管線後）

| | |
|---|---|
| 文字改變的題數 | **2,537**（3.2%） |
| 其中**長度不變、純搬移** | 2,429 |
| 全庫字元數 | 9,005,385 → 9,005,408（**+23**，是接字空白） |
| 已被人審過、且文字改變 | **2** 題 |

**文字是搬移，不是遺失。** 長度差的分布是 `0` 佔 2,429、`±1/±2` 佔 108，沒有大量減少。

## 已審過的 2 題：用 `reset_review`，不是改寫

| 題 | 原本決定 | 差異 |
|---|---|---|
| `moex:114020:305:0401:1:question:q018` | accept | 題幹尾 `…何者正確？生絞痛` → `…何者正確？`；選項 C/D 續行歸位 |
| `moex:115020:308:0502:1:question:q076` | accept | 題幹尾 `…最不適當？ intraepithelial neoplasia, CIN）` → `…最不適當？`；選項 B 續行歸位 |

一則審核決定是**對當時那份讀法**所做的。讀法變了，舊決定不是錯，是對不同的文字所做的，
誠實的紀錄是一個新事件（`reset_review`），保留舊事件。用
`scripts/append_reset_review_events.py`（預設 dry-run、`--apply` 才寫、先備份、寫收據）。

## 三個負向對照（一個不能失敗的規則什麼都沒證明）

1. **舊碼上報 344**：`git stash` 掉 `repair.py` 再跑同一支腳本。
2. **新測試在舊碼上紅**：`test_a_wrapped_option_keeps_its_continuation` 在未修版本 assert 失敗。
3. **文字守恆**：見上表，字元數幾乎不變。

## 量測工具自己錯了兩次（最值得記的一段）

`verify_option_continuation.py` 的前兩版都給出**看起來很乾淨**的假答案：

| 版本 | 測什麼 | 為何是假的 |
|---|---|---|
| v1 | 對每個引擎自己的讀數都算 loss | 出貨欄位由 A 產生，再用 A 的讀數驗 A 的輸出——**同義反覆**。修好後回報 0，看起來像成功 |
| v2 | 「B 報的漏字有沒有出現在 A 的讀數裡」 | A 的讀數有整張紙的文字，幾乎永遠為真 → **已在修好的版本上回報 1,784** |
| v3（現行） | A 把**欄位＋漏字印成連續一段** | 兩個引擎獨立同意紙本在那裡續下去，而出貨欄位停住 |

v2 的 1,784 出現在**已修**版本上，這是抓到它的方式。
**一個壞掉的量測工具，看起來和一個壞掉的產品一模一樣。**

## 剩下的 1 筆不是缺陷，是升級

`1042_醫師(二)_醫學(六)` q43：兩個引擎讀到**同一段文字**，但對**哪個選項擁有它**不一致
（A 把 `，骨盆腔及主動脈旁淋巴結摘` 算作 B 的續行，B 的讀數把它放在下一列而歸給 C）。
這是「歸屬有爭議」，不是「被丢掉」。**留 1 筆是正確的；歸零才可疑。**

## 三段順序的更正

第 6.1 節原本說「先偵測、再標記、再驗收」。做下去發現：**當修復等於偵測時，不需要新的 dispute kind**。
選項不再被截斷，就沒有東西可以標記。`option-continuation-loss` 這個 kind **刻意沒有加**——
加一個永遠不會出現的 kind 就是死程式碼（`engine-disagreement` 就是前例：kind 存在、
`engine_counts` 從未被傳入、佇列裡 0 筆）。

驗收數字仍然要報（344 → 1），因為它是**可下降的數字**，不是一個說法。

## 檔案

| 檔案 | 角色 |
|---|---|
| `qbr/src/qbr/repair.py` | `_continues_an_option`（新）、`segment_questions` 的路由（改） |
| `qbr/src/qbr/continuation.py` | `confirm_against`（新，取代 v2 的假檢查） |
| `qbr/scripts/verify_option_continuation.py` | 驗收標準的可執行版本 |
| `qbr/tests/test_text_repair.py` | 3 條新測試，都在舊碼上紅 |
| `qbr/data/review-queues/pre-wrapfix-20260921/` | 修復前的佇列（備份，可逐題比對） |
| `qbr/data/review-queues/live/` | 修復後的佇列（8774 現正服務此份） |

## 重跑管線（讓修復出現在 UI 上）

```sh
cd tw-national-exam-catalog/qbr
for c in 醫事檢驗師 醫事放射師 物理治療師 藥師 "藥師(一)" "藥師(二)" "醫師(一)" "醫師(二)"; do
  d=$(echo "$c" | tr -d '()')
  .venv/bin/python scripts/batch_package.py --category "$c" --work "/tmp/qbr-rebuild/$d"
done
.venv/bin/python scripts/build_review_queue.py \
  --work /tmp/qbr-rebuild/{醫事檢驗師,醫事放射師,物理治療師,藥師,藥師一,藥師二,醫師一,醫師二} \
  --out /tmp/qbr-rebuild-queue --carry-from qbr/data/review-queues/live
```

> **`--carry-from` 要指向佇列的**根目錄**，不是它的 `review-ui/` 子目錄。**
> 修復前的備份留著，是為了能逐題比對「搬移前後的文字」，不是為了回退。
