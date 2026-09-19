# 把考題管線併入主線（`pi_test/question_bank_rebuild/` → `tw-national-exam-catalog/qbr/`）

## 這一件事本來以為是搬家

管線在沙盒裡已經證明過：一卷官方 PDF 走到一個平台可驗的封裝，七個階段全過，兩個引擎一致。
合併的計畫因此寫得很簡單 —— 照原樣 `rsync`，保留 `{src,scripts,tests}` 的兄弟幾何，讓所有
`sys.path` 的 `__file__` 相對解析自己成立，路徑編輯數 = 0。

**路徑編輯數確實是 0。而搬家本身抓出三個缺陷，其中兩個是搬家引進的。**

這份報告只記「合併」這一件事，不重複管線本身的量測（那些在 `docs/` 與其他 `reports/` 裡）。

---

## 缺陷一：資產路徑是絕對路徑（搬遷前就存在的潛伏缺陷）

`data/sample_manifest.jsonl` 的 30 列，每一列的 `raw_copy` 都寫成
`/Users/tim/.../pi_test/question_bank_rebuild/data/raw/...`。

只要管線留在沙盒，這樣寫**完全正確**。搬進 catalog 之後，30 列指向一棵已經不放管線的樹。

**為什麼 198 個測試沒抓到**：大多數測試不讀 manifest。實際結果是 3 個測試 `FileNotFoundError`、
198 個通過。一個只在「寫下它的那個目錄」裡正確的路徑不是路徑，是巧合。

**修法**（`src/qbr/manifests.py`）：

* `build_sample_set.py` 改寫相對路徑（相對 manifest 自己所在目錄）。
* 讀取一律經 `manifests.load()`，以 manifest 所在目錄解析，**不經 `os.getcwd()`**。
  從 repo 根目錄、從 `/tmp`、從伺服器行程裡讀，答案都一樣。
* 已經是絕對路徑的列**刻意不順手修好**：修好會讓一份仍然寫著絕對路徑的 manifest 看起來正常，
  而那份 manifest 本身就是缺陷。讓它繼續可用但明顯不對，比悄悄改正確安全。

同時 `build_sample_set.py` 裡的 `_WORKSPACE = dirname(dirname(PKG_ROOT))` 也是同一類錯誤：
用「往上數幾層」找 corpus。**但這裡必須說清楚：它搬家後仍然算出正確答案** ——
`pi_test/question_bank_rebuild` 與 `tw-national-exam-catalog/qbr` 剛好同深度，兩個寫法給同一個路徑。
（已驗證，不是推論。）

所以這**不是**缺陷修復，是**移除一個巧合**。數層是對「程式碼剛好住哪」的陳述；
**搜尋**才是對 repository 的陳述，而後者才是真的。
兩者今天一致；第三個位置就會讓它們不一致，而不一致的方式是「corpus 讀不到但沒有錯誤」。
改成向上找 `tw-national-exam-catalog/`，找不到再退回 package 的兄弟目錄。

**測試**：`tests/test_manifests.py`（4 條），含「同一份 manifest 在三個不同工作目錄下讀到同一個檔」。

---

## 缺陷二：每個題目的主鍵多了一個 `:question`（**搬家引進**）

catalog 把 registry key 連 role 一起拼：`moex:115090:308:0504:1:question`。
`package.py` 原本無條件再補 `:question`，於是題目鍵變成

```
moex:115090:308:0504:1:question:question:q001
```

**80/80 題全中**，`answer_source_registry_key` 也是 `...:answer:answer`。

**為什麼這不是命名問題**：這個鍵就是 `question_review_events.jsonl` 裡人工決定掛上去的那個鍵。
主鍵一變，審核紀錄就對不回題目 —— 也就是「審核紀錄不得被重建弄丟」那條規則的同一種傷害，
只是換了一個入口。

**修法**：`paper.py` 新增 `paper_key()`，先剝掉 role 後綴再拼。
`question_key("...:question", 7) == question_key("...", 7)`。

---

## 缺陷三：更正答案卷構不到（**搬家引進，且方向危險**）

`1152_醫事檢驗師_生物化學與臨床生化學` 的 **Q10 與 Q41 是送分**。

* 註冊表 manifest 對這份卷只有 `:question` 與 `:answer` 兩列，**沒有 `:correction` 列**。
* 更正答案卷 `..._MOD.pdf` 一直在磁碟上。
* `sheet_paths()` 把「註冊表命中」當成「目錄清單的**替代**」，而不是「第一順位來源」。
  於是部分命中（2/3 卷）就停止了搜尋，`resolution: registry-manifest`、`corrected_present: false`。
* 結果：送分被publish 成 `A` 與 `D`。

**方向為什麼重要**：送分的意思是四個選項全部接受。發布成單一字母，等於把每一位答 B、C、D 的
考生都判錯。而且**兩個引擎抓不到**，因為它們讀的是同一份找不到的更正答案卷 —— 這正好是
「雙引擎一致」這道防線的盲區：一致的錯等於一致。

**修法**：目錄掃描跑第二次，**只補註冊表沒回答的角色**，且**永不覆蓋**註冊表給的路徑
（註冊表是註冊表，目錄是一個剛好同名的檔案；覆蓋會讓「查到的」與「猜到的」再也分不出來）。
`how` 記成 `registry-manifest+directory-correction`。

**測試**：`tests/test_merge_golden.py::test_a_partial_registry_hit_still_reaches_the_correction_sheet`
用一個**只回兩卷的假註冊表**對真實目錄 —— 用假的是刻意的：真註冊表的內容會變，
而「部分命中不得停止搜尋」是規則。

---

## 一個不是缺陷的差異：`HbA₁c` → `HbA1c`

逐欄比對時 `HbA₁c` 差了一個字。查紙張：`HbA` 是 11.03pt，後面 `1c` 是 5.51pt 下標 —— 紙上確實有下標。

但它不是搬遷造成的。`extract.py` 早有明文：舊規則是「有一個字能轉就轉」，`1c`→`₁c` 讓 `HbA₁c` 正確，
**同一條規則也把 `41.` 變成 `⁴¹.`、把 240 個普通數字變成上標**。改成「全部字元都必須能轉」之後，
那 240 個錯誤消失，代價是 `HbA₁c` 退成 `HbA1c`。這是已知的少量英文上下標問題（記錄在案），
不是可拿掉的限制：改回去就是拿 240 個錯誤換一個下標。

**所以 golden 固定的是「不得半轉換」，不是「必須有 `₁`」**。若哪天有辦法兩者兼得
（例如用幾何而不是字元集分辨），那時要**改斷言**，而不是讓輸出默默變成另一種。

---

## 驗收：欄位對欄位，不是測試全綠

用同一卷、同一組參數在兩個位置各跑一次完整七階段，逐欄比對：

| 比對 | 結果 |
|---|---|
| 合併後的 catalog 位置 vs 現在的沙盒 | **逐欄完全相同**（80 題） |
| 合併後 vs 舊的 pinned `qbr-golden-001` 產物 | 只有 `HbA₁c` 一個字（上節）與 `metadata` 時間欄位 |

`qbr-golden-001` 是**舊產物**，不是標準；真正的標準是「同一份碼在兩個位置必須給同一個答案」，
那個比對是逐欄相同的。

Golden 檔已固定為 `tests/golden/golden_1152_medtech_biochem_candidates.jsonl`（80 題、188K），
並有一條測試用 grep 守住「不得含絕對路徑或機器位址」。

---

## 測試

* 合併後位置、自帶 venv：**212 passed**。
* 從 `/tmp` 執行同一套測試：**212 passed**（路徑解析不依賴工作目錄）。

## 未解

1. **`.gitignore` 要納入 `qbr/data/`**（corpus 副本 6.3M 不進版控）。
2. **`qbr/venv` 是新建的**，依賴清單 `requirements/qbr.txt` 是下界不是凍結。
3. `read_the_registry.py` 自己吃不到新的 registry CSV 格式（`load_csv_rows` 拋錯），
   與合併無關，是既有缺陷。
