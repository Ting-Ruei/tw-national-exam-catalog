# D14 blast radius：「修了會不會改變站上看到的東西？」

> **這份是量測，不是推論。** 起因：本輪修正 `option_alphabet` 的 union 後，
> 磁碟上的裁圖早就是**修後**的樣子（`q004`／`q038`），但用**現行程式**重跑同一篇卻得到 `[3,10,18,37]`。
> 這份文件把「誰在什麼時候產生了哪些裁圖」查清楚，以決定修正是**急救**還是**防未來的重建**。

---

## 0. 一句話

**union bug 在生產路徑上是「真的會壞、但被另一個修正的部分掩蓋」——
壞的是 `option_alphabet`（全卷 0 選項），掩蓋它的是 2026-09-25 才加的 `_option_grid_owner`。
兩者合起來，站上目前顯示的歸屬是對的；但這是巧合，不是保證。修正必須做。**

---

## 1. 為什麼磁碟上的裁圖是對的（追出來的事實）

| 時間 | 事件 | `option_alphabet` | `_option_grid_owner` | 這篇的 `figure_questions` |
|---|---|---|---|---|
| 2026-09-19 | `40f8244` 合併題庫管線（**就已含 union**） | union | **不存在** | — |
| 2026-09-21 | run `20260921-expansion` 打包這篇（`git_sha 7d4e087`） | union | 不存在 | **`[4,10,18,38]`（對）** |
| 2026-09-25 | 跨頁修復加入 `_option_grid_owner` | union | 存在（**站上現在的版本**） | **`[3,10,18,37]`（錯）** |
| 本輪 | 修 `option_alphabet` → 最常用 family | **最常用** | 存在 | **`[4,10,18,38]`（對）** |

**關鍵**：`7d4e087`（2026-09-21，打包當下）的 `option_alphabet` **也是 union**，
`with_options` 同樣是 **0/80**——**但它的 `figure_questions` 是對的 `[4,10,18,38]`**。

**為什麼？** 因為當時**還沒有 `_option_grid_owner`**。那條 2026-09-25 才加的規則，
判準是「上一題的選項 cell 只有標記、沒有文字」（`_marker_only`），
而 union 讓**每一題的 `options` 都是空的** → `any(not _marker_only(body) for body in [])` → **vacuous 為真**
→ 這條規則在「全卷 0 選項」的卷上**過度觸發**，把圖判給上一題。

→ **站上目前的錯誤歸屬，是「union bug」×「`_option_grid_owner` 的空序列 vacuous 通過」兩個缺陷相乘的結果。**
2026-09-21 打包的那批（磁碟上的裁圖）是在 `_option_grid_owner` 存在**之前**產生的，
所以它是對的——**不是因為 union 無害，而是因為它的搭檔還不存在。**

---

## 2. 量到的配置對照（`1001_醫事檢驗師_臨床生理學與病理學`，80 題）

| 配置 | `with_options` | `figure_questions` | 對錯 |
|---|---|---|---|
| `7d4e087`（union，無 `_option_grid_owner`） | 0/80 | `[4,10,18,38]` | 歸屬**對**（但選項全空） |
| 站上現在（union ＋ `_option_grid_owner`） | 0/80 | `[3,10,18,37]` | 歸屬**錯** |
| 本輪修正（最常用 family ＋ `_option_grid_owner`） | 79/80 | `[4,10,18,38]` | **都對** |

三列都是**實跑**，不是推論。模擬「站上配置」用的方法是把 `repair.option_alphabet` 暫時換回 union
（`{c for f in option_alphabet_families(text) for c in f}`），其餘用工作樹。

---

## 3. 所以要不要急救？（決策）

**要修，但不必恐慌性重切。** 理由分兩層：

### 3.1 站上「現在」顯示給設計者的東西

- **候選 `options`：不受影響。** 打包走 `golden_path → three_way.analyse_items → repair._segment_mixed`，
  **逐 family 試**，本來就不看 `option_alphabet`。實測站上這篇 `with_options=79/80`，正確。
- **`image_refs`（裁圖歸屬）：站上是 2026-09-21 打包的那批，在 `_option_grid_owner` 加入之前產生，
  所以是對的。** 站上 `q004`／`q038` 確實存在（已 SSH 確認）。
- **也就是說：設計者此刻打開 v2 看到的這 11 篇，資料是對的。**

### 3.2 但下一次「重切」就會壞

一旦有人對這 11 篇跑 `crop_run_figures.py --queue`（或任何吃 `cells_with_pages` 預設值的步驟），
**站上配置（union ＋ `_option_grid_owner`）就會把 q004 → q003、q038 → q037**。
這正是「量測與結論」與「實際服務」分歧的地方，也正是為什麼修正不能等。

### 3.3 佇列裡有多少篇

站上 989 篇中，屬於本輪量到「誤標」的 667 篇者有 **11 篇**（見 §4 清單）。
**不在盤上的 656 篇**，會在未來擴充佇列時才變成問題。

---

## 4. 受影響的站上論文（11 篇）

| 類科 | 卷 |
|---|---|
| 醫事檢驗師 | `1001_臨床生理學與病理學`、`1001_臨床血清免疫學與臨床病毒學` |
| 醫師(二) | `1001`／`1002`／`1011`／`1012`／`1021`／`1022`／`1031`／`1032`／`1041_醫學(三)` |

（全語料受影響 667 篇／13 類科；站上佇列涵蓋其中 11 篇。）

---

## 5. 重現

```sh
cd tw-national-exam-catalog
# 站上配置 = 工作樹的 _option_grid_owner ＋ union alphabet（見 §2 的模擬方法）
# 全語料盤點
qbr/.venv/bin/python repair_agent_test/probes/census_option_alphabet_union.py --out-dir /tmp/option-alphabet-union
# 667 篇的取回量
qbr/.venv/bin/python repair_agent_test/probes/measure_option_alphabet_union_impact.py
# 歷史配置（無 _option_grid_owner）可用 worktree 比較
git worktree add /tmp/wt 7d4e087
```

---

## 6. 這一課

1. **「站上顯示是對的」不代表「程式是對的」。** 這裡顯示是對的，
   只因為**產生那些檔案的時間點早於另一個缺陷**。把「檔案對」當成「驗收通過」就會漏掉這個。
2. **兩個缺陷可以互相掩蓋。** union 把選項清空，`_option_grid_owner` 的空序列 vacuous 通過把圖判錯——
   **單看任一個都不會發現**。這正是「每個檢查要有負對照」的延伸：**檢查之間也會互相掩蓋。**
3. **「重切」是一條獨立的路。** 修正前，任何重切都會把對的裁圖變錯；修正後才安全。
   → **修正的價值不在「修好現在」，而在「讓下一次重建不會壞」。**
4. **`_option_grid_owner` 的第三條件（`any(not _marker_only(body) for body in option_texts(…))`）
   在空序列上是 vacuous 為真** —— 這是它自己的負對照缺口，應另立一條（見 §7）。

---

## 7. 衍生的獨立問題（未修，另記）

- **`_option_grid_owner` 條件 3 在「上一題沒有 options」時 vacuous 通過。**
  程式是 `if any(not _marker_only(body) for body in option_texts(items[upper[0]])): return None`。
  當 `option_texts` 是**空序列**（union 讓全卷 options 全空）時 `any([]) == False`，
  於是**不** `return None` → 這條規則就**宣稱**圖是上一題的。
  但「上一題根本沒有 options」與「上一題的選項只有標記」是**兩件不同的事**：
  前者是資料缺失，後者是紙張性質。**規則把前者當成後者。**
  負對照應該是：*上一題沒有 options 時，這條規則必須拒絕回答，而不是預設通過。*
  **本輪只量到現象（站上 11 篇），未改此規則。**
  （這正是 §6 第 2 點「檢查之間也會互相掩蓋」的具體長相。）
