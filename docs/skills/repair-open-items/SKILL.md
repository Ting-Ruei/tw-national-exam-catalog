---
name: repair-open-items
description: 這一輪維修改了什麼（每一項附量到的數字與量法），以及**還沒解決、等你裁決的問題**。要動工前先讀這裡的「還沒解決」，不要重新推導。
---

# 維修交接：2026-09-25 修理紀錄與 2026-09-26 治理／資料整理

業主 2026-09-25：「先把目前的修改狀況，以及還搞不定的問題整理成skill，明天再來討論。」

**分工（不要在這裡重複別人的事）**

| 想知道什麼 | 看哪裡 |
|---|---|
| 這一輪**改了什麼**、**還卡在哪裡**、每個數字的量法 | 本檔 |
| 「現在站在哪裡」的敘事（做完的、被罵的每一件事與理由） | [`review-platform-status`](../review-platform-status/SKILL.md) |
| 迴圈怎麼跑、面板、分流、常駐機部署 | [`operate-repair-agent-surface`](../operate-repair-agent-surface/SKILL.md) |
| 上下標／標記閘門的判準理由 | 同 `operate-repair-agent-surface` 的「上下標」一節 ＋ `qbr/src/qbr/dispute_apply/{text,page_read}.py` 的 docstring |

數字一律附「怎麼量的」。**沒量到的不要寫成事實**。

## 0-bis. 2026-09-29/30 佇列重建＋crops 事故修復（owner 已准「就做吧」）

owner 2026-09-29：「你幫我確定PR #4，然後依照你列的 你一句話就能動的 就做吧」——授權執行：
(a) 核定 PR #4 下落、(b) 常駐機佇列重建（79,090 題＋`question_page`）、(c) 站上討論區下線、
(d) 刷新鎖推站、(e) daemon 恢復與 occamy KV（此兩項**未核**，見 3.10／3.11）、
(f) `disputed_filter`／`startup_navigation` 處置（未決，見 3.12）。

### 這一輪做了什麼（每一項附量法）

| # | 做了什麼 | 檔案／指令 | 量到的結果（量法） |
|---|---|---|---|
| 1 | PR #4 核定：**已吸收進 main，無可搶救** | `git merge-base`＋golden 檔 diff | head `2e3fc873d` 不是 main 祖先，但其實質全在：`_SUP_LETTERS`／`_SUB_LETTERS`／`_OFFSET_WHITESPACE`／`_body_centre` 經 `d3b70e2`（超集，另加 `_OFFSET_BLOCKING_PUNCTUATION`＋`_BODY_SIZE_TOLERANCE`）。PR4 golden vs main golden **diff = 0 行**。判定評論：PR #4 issue comment 5891371863 |
| 2 | 交接紀錄成冊 | PR #16（`agent/handoff-record-20260929`，3ca3214） | 三份 skill 記錄（repair-open-items §6／design-repair-agent P.7／status 附錄）；**待 owner 合併** |
| 3 | 佇列重建（筆電、純確定性、**零模型呼叫**） | `batch_package.py --category <8 類> --work /tmp/qbr-rebuild` | 995 卷 glob 問世：**packaged 985**／blocked 10（S3 `options-not-four`／`answer-not-on-sheet`、S6 validator；與 0921 行為**逐卷相同**）。有產出的卷 **989 ＝ live 卷集**（差集雙向 = 0）。每卷 ~0.8s（log 計時） |
| 4 | 佇列合併（原地重建，承接自動） | `build_review_queue.py --work /tmp/qbr-rebuild --out data/review-queues/live` | **989 卷 79,090 題**、0 issue rows；**承接 128,980 筆**、orphaned 48（計數保留）；finding crops「missing 18,976（already present 1,231）」——這行後來變成 §0-bis 的第 4 列事故 |
| 5 | 站上部署＋重啟 | `QBR_STATION=192.168.10.70 deploy_station.sh --queue --restart` | 站上 candidates **79,090 列、100% 帶 `question_page`**（ssh python 逐列查 `metadata`）；人工事件 **20,329 完好**（備份 5 檔 → `backups/*.20260929-215837.bak`）；容器 Up (healthy)；`/v2` → 200 |
| 6 | 抽檢 6 題 | pymupdf 對官方 PDF | Q7/10/20/42/51/69（1152_醫事檢驗師_生物化學與臨床生化學）：宣稱頁 2/2/4/8/9/12 **逐題 token 命中**；Q42 題幹「超氧化物歧化酶…細胞色素氧化酶」與官方頁面文字逐字一致 |
| 7 | **事故：`--delete` 刪了站上 finding crops** | 見 `deploy_station.sh` 舊第 3 點 | 筆電重建後 crops **5,047** vs 站上 **21,543** 差額＝站上裁的證據圖；`--delete` 刪掉後 finding crops present **1,002**／missing **18,807**（ssh python 逐筆 os.path.exists） |
| 8 | **修復：純幾何重切** | `confirm_dispute.crop_for(pdf, qn, dpi=200)`（不需模型；量法：`extract_cells_a`→`band_rows`→`crop_rows`） | 重切 **16,443** 張（280 卷、20 分鐘、零失敗）；只增不刪 rsync 推回：站上 **present 19,809／missing 0**；crops 21,490 png |
| 9 | 防復發 | PR #17（`agent/station-queue-rebuild-20260929`，670e205） | `deploy_station.sh`：主同步 `--exclude=crops/`，另加一條**不帶 `--delete`** 的 crops rsync。合併前下一次重建會重演刪除 |
| 10 | 刷新鎖隨部署生效 | 容器重建（同一個 `--restart`） | `S.scopeRequest` 在 main 的 `01-core.js`；重啟後站上跑的就是新容器 |

### 這一輪的取捨

- **重建在筆電跑、站上只收佇列**：golden path/batch_package 是純 PDF 計算；站上只需停機 rsync＋重啟的時間。
- **刪後的證據圖用「重新裁切」而非「還原備份」**：站上備份目錄無 crops、無 Time Machine snapshot；crop_for 是同一個受測定義的重推導（200 dpi 同參），不是猜想。
- **crops 後續一律只增不刪**：刪除權交給未來真正的重建判準，不給 rsync。

### 站上狀態（2026-09-30 清晨量）

- `qbr-review-ui` Up since 2026-09-29 21:58（healthy）；daemon/掃描行程 **0**（`ps` 計數）；station disk 142Gi free。
- 事件流：question_review_events 20,329／question_ai_findings 107,991（rsync 排除清單保住；方向＝站上是家）。
- PR 開著：#16（交接紀錄）、#17（crops 增量 rsync）——**都待 owner 合併**。
- 筆電本地unittest discover OK（skipped=8）；qbr pytest 788 passed／18 skipped（merge 前量）。

---

## 0. 2026-09-26 治理與資料整理階段交接

### 已確認的 interim 決定

- Mac Studio 保留 `Review UI v2` 所需的 QBR code、queue 與 assets；不放完整國考資料根或 catalog PostgreSQL。三個本機國考資料根是不同且完整的集合，不可當成重複鏡像處理。跨設備量測與其餘儲存清理見[傘層資料稽核](../../../../docs/project-data-and-mac-studio-audit-2026-09-26.md)。
- catalog PostgreSQL 已退役：只在 MBP 保留 reference copy 查閱，Mac Studio 不需要。
- QBR Agent 暫行可在選定流程中設定自己的 AI `pass`／`return`／`block` 狀態，並更新自己的 AI 結果、保留修訂與來源。人工 accept/block 事件仍 append-only，Agent 不得寫入或修改；AI 狀態不等於人工裁決或正式 package 發布。網站 production 閘門維持原狀。
- 暫行權限不指定新模型、provider、計算節點或資料傳送範圍。完整 QBR 流程之後由 owner 重新設計，屆時此 interim 規則由新設計取代。

### Catalog 權責內的後續工作

1. 與 owner 討論 Review UI v2 code/schema 的 PR、測試與 Mac Studio LAN 更新閘門；網站 production gate 不隨之放寬。
2. 討論 parser／candidate 內容改變後，逐題影響分析與 `reset_review` 還要保留哪些要求。
3. 在完整流程重設時，定義簡單清楚的 AI 狀態／結果資料契約（actor、source/input hash、版本與修訂脈絡）及 Review UI 投影；人工事件 API／按鈕仍只能記錄人工決定。
4. 把既有 AGENTS、governance、skills 中仍過度阻擋自動化的規則收斂到單一權威，不再複製多套閘門文字。

**目前沒有 AI 狀態 writer 的實作授權。** 現行 Review UI `/api/review` 與人工判決按鈕仍代表人工操作，Agent 不得藉此寫入 AI 狀態。這次只記錄 interim 權限及待設計項目，沒有變更 UI、schema、live queue 或部署。

跨專案資料放置、Mac Studio Docker／checkout／tmp 與本機 MAX 395+ 殘留項目屬於傘層清理責任；本技能只追 catalog 的 QBR／Review UI／治理項目，不重複維護那些盤點。完整路由見[全域 project hierarchy 技能](../../../../skills/repair-project-hierarchy/SKILL.md)。本檔較早的 station／repair 敘述是有日期的工作紀錄，繼續操作前須重讀當前狀態，不得當成即時服務狀態。

---

## 1. 這一輪改了什麼

| # | 改的東西 | 檔案 | 量到的結果（量法） |
|---|---|---|---|
| 1 | 全庫套用斜體 `<i>` | `qbr/scripts/scan_italic_marks.py`（2026-09-25 早前） | 候選檔 **3,383 題帶 `<i>`／7,423 欄**；**3,383 筆** `reviewer=repair_italic_markup` 事件。判準：紙本字型量到斜體片段 ＋ 那一欄裡「沒被標記蓋住」的出現**剛好一次**（`e`／`s`／`K` 這種一個字的片段做不到就留給人） |
| 2 | 斜體**歸屬**檢查＋修正 | 同上（`--verify`／`--revert`、`line_owner`、`place_in_owner`） | 站上 79,090 列判準分布：`line-text-not-in-any-field` **7,781**、`line-matches-the-field` **185**、`line-belongs-to-another-field` **53**（`owner`：`stem` 52、`option A` 1） |
| 3 | **`--revert` 的真事故（已修＋已排還原）** | 同上 | 第一次跑只把標記**拿掉、0 個放回** ⇒ **53 個紙本真的有的斜體被刪**，摘要卻印「歸屬錯的已修：53」。修法：**放不回去就不要拿掉**；位置改由紙本決定（那一列的位置＋列內位移＝owner 欄裡唯一的位置）。負對照 3 條（見 §5） |
| 4 | 上下標／標記閘門的判準 | `qbr/src/qbr/dispute_apply/{text,page_read}.py`、`qbr/scripts/apply_{dispute,experience}_repairs.py` | 舊判準「**原文**含標記就整欄拒絕」改成量**判讀的標記組成**；`markup_fidelity_complaints` 跳過 `deterministic_form` 認得的機械還原。站得住腳的退件：**31 欄／18 題 → 64 欄／43 題**（量法：每題最新一筆判讀逐欄重跑兩個判準，再要求 `orchestration.verdict == TRUST`） |
| 5 | 圖版那一趟**進迴圈**（⑤b）＋判準兩半 | `qbr/scripts/{crop_run_figures.py,repair_daemon.sh}` | 判準＝**紙本量得到的**（這一題區域連一個圖物件都量不到 ⇒ 移除，不縫一張）＋**他明說的**（`ask_about_blocks.read_figure_directive` → 封閉集合 `no-figure`／`extra-crop` ⇒ 不放圖；`wrong-region`／`keep` ⇒ 重切不刪；`unclear` ⇒ 不猜）。常駐機 plist：`ProgramArguments` = `bash -lc 'exec …/repair_daemon.sh loop'`、`StartInterval 600`、`QUEUE=/Users/tim/qbr-review/queue` |
| 6 | 圖版：**正式佇列已套用** | 同上 | `figure_ownership.json` `stamp 20260925-221847`：`directive-asked` **107**、`dropped-by-note` **43**、`dropped-no-figure` **1**、`widened` **45**、`whole-question` **7**、`inside` **12**、`rows_changed` **37**（迴圈自己跑的那一輪，22:18） |
| 7 | 兩個真 bug（都留了負對照） | `crop_run_figures.py` | ① `human_notes()` 的形狀是 `(created_at, notes)`（**時間在前**），`directive_of` 寫成 `text, at = note` ⇒ 引擎收到**時間戳**當成他的話 ⇒ 6 題全回 `unclear`、`quote` 照抄時間戳。② 表格裁切也是 `figure_ownership.json` 的 `figure-crop`，不能被一句「沒有圖」一起刪 |
| 8 | 他看得到 | `qbr/scripts/report_repair_progress.py` | ⑥ 的摘要新增「人說沒有圖 ⇒ 不放圖 N／重切 N」＋逐題**他的原話**（以前只寫在 `figure_ownership.json`——人不會打開的檔案） |

**站上部署**（本機＝站上 md5 對照過）：`repair_daemon.sh`、`crop_run_figures.py`、`ask_about_blocks.py`、`vision.py`、`report_repair_progress.py`、`dispute_apply/{text,page_read}.py`、`apply_{dispute,experience}_repairs.py`。`scan_italic_marks.py` 由兩個排隊視窗各自 scp 一次。

**測試**：`qbr/tests/` **761 passed／17 skipped**（2026-09-25 22:2x）。圖版 42、斜體 17、報告 5。

---

## 2. 站上狀態（原紀錄；本次稽核補記於下）

- **迴圈在跑**（`repair_daemon.sh loop`，`repair_daemon-20260925-205958.log`），第 1 輪已結束 ⇒ **⑤b 已自動跑過**（§1 第 6 列的數字就是它的）。
- **人在審題**（事件尾端 `reviewer: "local"` 的 `accept`，22:19 台北）⇒ 任何改 `candidates.jsonl` 的視窗都要排隊、且知道「服務開著時檔案與清單會短暫不一致」是已知未解（§3.7）。
- **本次稽核結果**：daemon 最後一輪已完成（最近的圖版摘要 `20260925-223603`：不放圖 0、重切／加寬 45、整題縫圖 7、本來就在自己列 12；log 記錄輪次於 `2026-09-25T14:36:26Z` 結束）。依 owner 選擇，完成後已 `launchctl bootout gui/$(id -u)/com.qbr.repair-daemon`；`launchctl print` 回報 service not found，且未見 repair writer process。Review UI 未停。`bg_3` 的獨立視窗仍因 3600 秒 timeout 無完成摘要，不能據此判定它有無另外寫入。
- **斜體視窗狀態**：還原與放置視窗（bg_4、bg_1）已在等待時取消，未執行站上還原或欄位位移；站上新版 `scan_italic_marks.py` 是否部署仍未驗證。
- **斜體事故還原（G4）**：owner 核准只還原 `current == event.to` 的 51 筆、不移欄；q041／q053 `option A` 各一筆不匹配紀錄略過。2026-09-26T11:52:50 套用至 station queue：19 列、追加 19 筆 append-only `reset_review`（共 51 changes），其餘 79,071 列 byte-identical；完整 queue reload 成功，19 題均待人工複核。備份：`/Users/tim/qbr-review/queue/review-ui/candidates.jsonl.before-italic-restore-italic-restore-20260926T115250`；新 queue SHA-256 `7cfb26734f2f063f69f85be524f2c9fab9396e77eb97a27f82eaa8bd85b95b75`。未搬欄；兩筆 mismatch 仍保留。
- bg_6 那一趟（判準部署＋套用）已結束：`apply_dispute_repairs --page-read --apply` 追加 **23 筆** `reset_review` ＋ **2 筆** withdrawn；`apply_experience_repairs` 那一半**寫了 0 筆機器修復**（見 §3.5）。

---

## 3. 還搞不定的問題（每一項：症狀／量到的事實／現在的行為／明天要決定的）

### 3.1 斜體：51 筆匹配欄位已還原；欄位歸屬與兩筆 mismatch 留待處理
- **症狀**：他問「斜體有些是誤植，你怎麼解決」，而第一次的 `--revert` 把 53 個**正確的**斜體刪掉了。
- **量到的事實**：53 個片段的 `owner` 指到別欄（`stem` 52／`option A` 1），舊碼在那種情況仍然把標記拿掉；19 筆事件的 `changes` 全是「`<i>X</i> Y` → `X Y`」。
- **現在的行為／已執行**：owner 核准「只還原 51 筆匹配欄位、不移欄；兩筆 mismatch 略過」。2026-09-26T11:52:50 已更新 station queue 並完成 full reload；19 題保持 `repair_pending` 待人工複核。q041／q053 `option A` 的兩筆不匹配紀錄未套用。
- **後續處理**：不再自動套用斜體 `--revert` 或搬欄。q041／q053 的兩筆 mismatch 僅在能唯一定位原始紙本、另行核准後處理；目前留待人工複核。

### 3.2 早期卷：紙本讀不到 ⇒ 被丟到「AI 無法判斷」
- **量到的事實**：反問理由分布（`question_repair_questions.jsonl` 每題最新 `reason`，378 題）：CARE／DOUBT **190**（100–104 卷佔 47%，卷 100 就 42）、紙本與抽取一致人仍阻擋 90、**紙本讀不到 `no-rows`/`unparsed` 27（85% 在 100–104；卷 100 有 17、卷 101 有 6）**。
- **現在的行為**：整欄改寫要「第二次判讀」同意才動；讀不到就是留給人。
- **明天要決定**：要不要開一條**整頁判讀**的退路（把整頁給引擎、讓它自己找出這一題的文字），代價是每一題都貴、而且要有人檢查。

### 3.3 判讀可以改題目、卻沒有套用：**420 題**
- **量到的事實**：`apply_experience_repairs --queue … --apply` 的輸出：待辦 **495 題**（人阻擋、反問尚未回答），其中有判讀的 **440 題**，其中**判讀可以改題目的 420 題**；那一趟「可以自動套用的：**0 題**」。
- **現在的行為**：經驗類別裡只有 `lost_glyph`／`typographic_variant`／`compatibility_fold` 是「確定性、不需確認」可以自動套；`markup_only` 等要兩題確認；**每一題都還在人手上**。
- **明天要決定**：420 題裡哪一類可以自動？門檻（`確認門檻 2 題`）要放寬還是要更多證據？——這一項最接近他抱怨的「明明一樣的邏輯，為什麼沒做」。

### 3.4 `MAX_ATTEMPTS=3` 是「題目一輩子」而不是「依改動種類」
- **量到的事實**：站上「**已退滿上限而不再修：11 題**」（分布在各卷）；`qbr/src/qbr/dispute_apply/withdrawals.py`。
- **明天要決定**：改成「每一種改動種類各自算」還是維持現狀（只有 11 題）。

### 3.5 `apply_experience_repairs` 那一趟寫了 0 筆，為什麼
- **量到的事實**：同一次的輸出是 `經驗：{'lost_glyph': 2, 'markup_only': 189, 'typographic_variant': 2}` 而「可以自動套用的：0 題」。
- **假設（未驗證）**：`markup_only` 的那 189 個候選在 20:37／20:59 的斜體套用裡已經被標好了，所以這一趟沒有新的可套。**明天要量的**：`field_state` 對這 189 個候選現在的說法（`marked`／`unmarked`）——如果還是 `unmarked`，那是判準不一致，不是「已經做過」。

### 3.6 「範圍錯」但紙本量不到圖物件的那一類
- **量到的事實**：`--human-flagged` 只在有人留話的題目上跑；他點名「沒有圖／多截」的題現在有兩條路（量不到圖物件／他明說了）。**沒有他留話、又量不到圖物件**的題目仍然走「整題縫一張」（站上那一輪 `whole-question` **7**）。
- **明天要決定**：整題縫一張要不要改成「不縫，標 `ownership: unverified` 讓他自己說」。

### 3.7 圖被移除**在 UI 上看不到理由**
- **量到的事實**：`figure_ownership.json` 的 `directives`／`records` 有他的原話，⑥ 的摘要也印了；但審題介面只看到「圖不見了」，沒有那一句說明（UI 不讀 `figure_ownership.json`）。
- **明天要決定**：要不要在題目卡片上顯示「這一題的圖是 2026-09-25 由機器依你的話移除（原話：…）」。

### 3.8 斜體：7,781 個片段無法驗證（不推論）
- **量到的事實**：`line-text-not-in-any-field` **7,781**（紙本那一列的文字在抽取欄位裡找不到 ⇒ 抽取器拆行或改了字，`--verify` 不推論）。
- **明天要決定**：要不要用**第二個證據**（判讀端已經自己寫的 `<i>`、或字型位置與欄位的座標對齊）去驗這一群，還是就讓它們留著。

### 3.9 已知未解（不是這一輪造成的）
- 重建候選檔時服務仍開著 ⇒ 列表與檔案短暫不一致（`qbr/reports/review_record_safety.md`、「Pause the service during a rebuild」）。今天三個排隊視窗也會經過這條路。
- `qbr/scripts/repair_daemon.sh` 的 `busy()` 把常駐迴圈自己算進去 ⇒ 排隊視窗在迴圈活著時空轉（今天實測：30 分鐘上限）。**要改的是 `busy()` 的定義，不是加長等待。**

### 3.10 站上 daemon 恢復（未核，不啟動）
- **量到的事實**：站上 daemon/掃描行程 **0**（`ps` 計數，2026-09-29）；plist 狀態合併後未驗證。
- **啟動前必須**：lane 指向 occamy（不是 DGX）、`QBR_ALLOW_EXTERNAL_LLM` 未設定（charter）。
- **要 owner 決**：何時重啟、lane 參數。

### 3.11 occamy KV 擴容（未核，擱置）
- **量到的事實**：重建（batch_package/golden_path）是純確定性，**不需要模型**；KV 65536 現值對佇列重建無影響。
- **硬理由只剩**：reflow 全卷預算 156k → `CTX_MLX=131072 occamy restart fit6`。**要 owner 決**是否值得。

### 3.12 兩支未接手的測試檔（未決）
- `tests/test_review_ui_disputed_filter.py`、`tests/test_review_ui_startup_navigation.py`（pytest 風格，unittest discover 從未收）。**要 owner 決**：重寫成 unittest 還是放棄。

### 3.13 站上錯題討論區下線（5.3 決定「廢掉、沙盒穩定後補回」；介面移除待一句話）
- **量到的事實**：v2.html 第 733 行 `data-area="discuss"` 按鈕（v2.html L733）＋ `#areaDiscuss` 區塊（L829）＋ `04-area-discuss.js` `<script>`；下線＝拿掉這三處＋`03-areas.js` 四個註冊表的 `discuss` 項＋再度 `--restart`（站上 G2/G3 已得「就做吧」的方向，但**移除本身 owner 尚未點頭**——P.3 保留的問題）。
- **要 owner 決**：現在就拿掉，或沙盒穩定後再說。

---

## 4. 這一輪我做的取捨（你可以推翻）

1. **他的話是權威**：`no-figure`／`extra-crop` ⇒ **不放圖**，不量測、不縫一張。理由：紙本量不出「這張圖該不該交給審題者」。
2. **標籤是封閉集合**（5 個）＋**同一句話只問一次**（快取在 `figure_ownership.json` 的 `directives`）＋讀不到最多試 3 次；只有 `--human-flagged` 那一趟問（整本那趟是量測，不是問句）。
3. **拿掉而沒有目的地＝不做**（斜體歸屬）。有疑慮的片段留在 `--verify` 的明細裡給人。
4. **判準只能有一份**：標記組成的 regex／機械還原表搬到 `dispute_apply/text.py`，`apply_experience_repairs.py` 的副本刪掉。
5. **`unclear` 不猜**：寧可不動。
6. **位置由紙本決定，不由「出現幾次」決定**：同一段字在 owner 欄出現兩次以上不再等於「放不回去」。

---

## 5. 驗證指令（明天照這個順序看）

```bash
# 本機
cd "/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog"
qbr/.venv/bin/python -m pytest qbr/tests/ -q                 # 761 passed / 17 skipped
qbr/.venv/bin/python -m pytest qbr/tests/test_italic_marks.py -q     # 17（含 3 條歸屬負對照）
qbr/.venv/bin/python -m pytest qbr/tests/test_figure_crop_ownership.py -q   # 42（含 NoteDirectiveTests）
bash -n qbr/scripts/repair_daemon.sh

# 站上：還原與位置修正有沒有落地（§3.1）
ssh macstudio '/usr/bin/grep -c "還原 2026-09-25 21:50" \
    /Users/tim/qbr-review/queue/review-ui/question_review_events.jsonl'      # 期望 19
ssh macstudio 'md5 -q /Users/tim/qbr-review/code/qbr/scripts/scan_italic_marks.py'  # 要＝本機

# 站上：圖版那一趟（他的話）
ssh macstudio '/usr/bin/python3 -c "
import json; p=json.load(open(\"/Users/tim/qbr-review/queue/review-ui/figure_ownership.json\"));
print(p[\"stamp\"], {k:v for k,v in p[\"stats\"].items() if v})"'

# 站上：迴圈有沒有在跑、⑤b 有沒有跑
ssh macstudio 'launchctl list | /usr/bin/grep qbr.repair-daemon'
ssh macstudio '/usr/bin/tail -40 /Users/tim/qbr-review/code/qbr/runs/repair_daemon-*.log | /usr/bin/grep -A3 "圖片歸屬"'
```

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

> **這是本子專屬技能**：只服務這個子專案。其他子專案要用同一件事時，先確認是不是該變成全域共通技能。

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 不確定從哪開始：[`project_map`](../../../../project_map) 是整棵樹的可點擊地圖
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->
