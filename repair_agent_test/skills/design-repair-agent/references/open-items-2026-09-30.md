# 開放項目回報 — 2026-09-30 深夜批次（離席授權 G0–G2；設計者未在場）

> **狀態更新（同夜）**：設計者已在 chat 逐項裁決（qa-log **Q57**、SKILL **§9.10**＝結構化版）。
> 本檔 §2 的各項隨裁決更新如下——**只剩排程類待設計者**（2,634 夜窗、4.2 全批夜窗、5.1 窗口）。
> 學習語料正本＝Mac Studio（Q57#1）；批次採**保險版**兩輪共識（Q57#2）；zero-hit 規則**已退場**
> （compress-2，只剩 p18 active；可逆）；字形落地走**決策表**（已備好，見 §2.3）。

業主 2026-09-30 晚授權原話：「同意後面的TODO直接做完；需要測試或決策等我有空再來跟你討論」。
本檔＝這一輪做完什麼＋**等你裁決**的所有事。**一切 G3 都沒做**（沒動題目正式檔、沒動人工事件、
沒動站上／路由；草案與稽核全在沙盒 `repair_agent_test/agent/store/`）。

| 想知道什麼 | 看哪裡 |
|---|---|
| 每一段的完整設計與量法 | [`workplan-2026-09-30.md`](../workplan-2026-09-30.md)（3.1–3.5、4.1、4.2 結果段） |
| 問答與決策逐字 | [`references/qa-log.md`](references/qa-log.md)（Q44–Q56） |
| 迴路面／常駐機操作 | [`operate-repair-agent-surface`](../../../../docs/skills/operate-repair-agent-surface/SKILL.md) |

## 1. 這一輪改了什麼（全部有量到的證據）

| # | 改的東西 | 檔案 | 量到的結果（量法） |
|---|---|---|---|
| 1 | 3.2 五鏡頭掃描器＋真掃描 | `qbr/scripts/scan_rule_hits.py`、`qbr/tests/test_scan_rule_hits.py` | 79,090 列 live 快照、5.0 s：figure-ref-missing **634**（10 帳本已修）、superscript-family **2,000**（帳本 `<sub>/<sup>` 已修 361 鍵）、apostrophe-width **615**、rare-codepoint **3,974**；帳本 correction 對磁碟未折入＝**5,384 欄／3,239 題**。負控制：`moex:107100:305:33:1:question:q051`（1.1 已修好）在 figure 命中缺席 |
| 2 | 3.3 事件消費者（缺④） | `agent/lib/consumer.mjs`、`agent/consumer.mjs`、`agent/test_consumer.mjs` | 真腦隔離驗收 60 s：設計者 block「MAOA、MAOB 下標問題」→ **草案落成 1 筆**（p18、crop 帶 SHA-256）；人工帳本/candidates 位元組不變；機器 block 不開工（負控制） |
| 3 | 3.4 工單＋runner＋A/B 指標 | `agent/lib/workorder.mjs`、`agent/runner.mjs`、`agent/test_workorder.mjs` | G3 程式閘（`--apply/--land/--write-queue/--import`→exit 2）真拒；兩輪真腦試跑 5 題（A：1 草／4 拒；B：2 草／3 拒；failed 0；單題 2.9 分）；`--compare A B`＝交集 **0**（見 §2.2） |
| 4 | 4.1 deterministic 草稿 | `qbr/scripts/build_repair_drafts.py`、`qbr/tests/test_repair_drafts.py` | **7,584 行草案**追加進沙盒（康熙部首 12,298＋修飾字母 2,125＋相容漢字 308 個碼點變更；1,131 欄無 deterministic 標準形→不猜、留 advisory）；2 秒、零模型呼叫；冪等重跑＝跳過 |
| 5 | 4.1 模型工單（產品，未跑） | `agent/store/workorders/wo-4.1-{superscript-fresh,superscript-reapply,figure-missing}.jsonl` | **1,632／368／634 題**（每題一列、逐欄 evidence 合併） |
| 6 | 4.2 藥師(二) deterministic 量測 | (唯讀)＋`store/scans/20260930-3.2/pharmacist2_keys.json` | 現行母體 **5,040 題**（qa-log 舊估 4,410）；五鏡頭＝2/62/7/223；帳本未折入 102 題 |

**測試**：沙盒 `./run_tests.sh` **75 pass／0 fail**；qbr venv `test_repair_drafts.py` 5＋`test_scan_rule_hits.py` 8＋`test_apply_text_corrections.py` 27＝**40 passed**。分支 `agent/repair-agent-sandbox-20260930`已推（commits `53af28d`→…→最新，PR #18 隨動）。

## 2. 等裁決清單（隨 Q57 更新：**只剩排程類待設計者**）

### 2.1 學習語料權威（Q56，跨窗口）——**已裁決（Q57#1）**
**設計者裁定：Mac Studio 是正本**（未來部署都在這台）；MacBook Pro 是開發端，「有什麼好的進展就要推進 Mac Studio」。
→ 落實：佇列／事件／AI 結果以站上為權威；筆電沙盒的進展照 deploy 閘（`deploy-qbr-review`）推上站上。同步方向＝**筆電→站上**，不反向覆寫。

### 2.2 A/B 噪音底量到「單輪兩引擎確認不可重現」→ **已裁決＝保險版（Q57#2）**
設計者裁定：**每題兩輪、2 輪同修才提草案**；「之後慢慢調整提示詞可能可以縮減成 1 輪……開發階段要嚴謹」。
量到的噪音底（留作未來縮回 1 輪時的基準線）：交集（key→同一欄、同一修法）＝0；A 輪落 q033 選項 C「佈↔布」（兩引擎確認）、B 輪落 q033 **選項 A** GABA→`GABA<sub>A</sub>`——同一張裁片、同 prompt，兩輪結論相反；穩定負例：q044/q034 兩輪都判「不需要修」。

### 2.3 4.1 落地（G3）——改走「字形決策表」一次放行（Q57#3）
設計者裁定：**不逐筆核**。先做一張「某字形→某字」的決定表，表點頭後即放行；一題有多種字也照表放行。
- **決策表已備好**：`store/scans/20260930-4.1/glyph-decision-table.jsonl` = **170 個對應**（康熙部首字形 118、修飾字母 ᵐ/ᵢ/ᵣ… 25、相容漢字 列→列 等 27），覆蓋 **3,452 題**、7,584 欄。前幾名：`ᵐ→m`（861 題）、`⽤→用`（725 題）、`⾎→血`（498 題）、`⽣→生`（598 題）、`⼀→一`（577 題）。
- **點選 UI 已建好（2026-09-30 深夜，commit `9d6ae27`）**：`repair_agent_test/agent/ui/glyph_server.py`，設計者網址 **http://100.96.207.80:8791/**（Tailscale；與 8790 判讀介面同一進場）。逐列同意／退回＋全部同意／清空；送出＝append `glyph_approvals v1`（同一編號以最後一次送出為準）；伺服器端對表驗碼，from/to 漂移拒收。**頁面無「自動開跑」**：runner 批次永遠等你另行點頭。瀏覽器實點驗證過（冒煙紀錄已刪，檔案空著等你的第一筆）。
- **待設計者**：把表過一次（或「全整同意、例外圈出」）→ 核可後落地（G3，單獨窗口，`apply_text_corrections`＋append-only reset_review）。

- **第一筆決定已記錄（2026-09-30 16:29，1 筆 156 決定：**同意 156／退回 0**）**：**全部康熙部首＋相容漢字（中文字形）＝已核可**；**14 列修飾字母（ᵐ→m 等）設計者刻意未決**——「肉眼看不出來，只有大小有差」。落地只依已核可的 156 列；未決列完全不動。
- **修飾字母 14 列＝另案：記法轉換（設計者提問後查實，Q58 補充）**：`ᵐ` 折到 `m` **不是外觀中性**——ᵐ 的字形本來就渲染成上標樣，只折碼位會把「上標感」抹掉（例：核醫 `⁹⁹ᵐTc`、紙本 `75mg/m²`）。正確的最終形＝**折＋記法**：字母歸位＋`<sup>/<sub>` 記進標記層（設計者的慣例 `H<sub>2</sub>O<sub>2</sub>`）；deterministic 折回**不補**標記（查實：7,584 行草稿零標記）——位置由稽核/模型層逐處確認後補。⇒ 這 14 列（1,162 題、~2,095 碼點）另案設計「折＋補位一起落地」的流程。
**英文字 14 列的決策**：**這一頁不用再決**（已按 §2.3 重分類鎖定，commit `ffd708a`）——頁面上呈灰底「另案」，沒有同意／退回鈕，誤點送出也會被伺服器 409 拒收。它的核可直接歸 §2.8 的正式核可面（屆時附**渲染的前後對照**，一次批准轉換規則，不逐題看）。


- **落地已執行（2026-09-30 傍晚，owner「依照妳說的4 1」＝核准這一窗）**：`apply_glyph_table.py`＋`apply_text_corrections.py` 全批 9,870 列折疊（**0 refusal**）、核可字形殘留 0、refs 未動、帳本 20,329→30,199（站端 append 接收）。**範圍量的更正**：表的 `question_keys` 只是取樣（12,571 碼位）；字級統一＝整佇列 42,063 碼位／9,870 列——owner 的映射裁決本來就是字級（「一題多種字也根據做完的決定放行」），實際逐映射數字在 manifest（`review-ui/glyph_events_20260930.jsonl` ＋ workplan 結果段）。落地後夜窗批次讀到的是**已折**的文字（減少字形類誤差報告）。

### 2.3b 8791 字形頁與 14 列英文字的現狀

- **156 列已落地**；頁面載入即還原最新決定（不再重置為 0）、14 列灰底鎖定「另案」。**這一頁的任務已完**：可退役（owner 一句話即停 `glyph-ui-8791`）；決定紀錄在 `store/scans/20260930-4.1/glyph-approvals.jsonl`＋落地 manifest，永久可查。
- **14 列修飾字母（1,162 題）的核可面照 §2.8 另設**；落地等待「折＋`<sup>/<sub>` 補位」成套設計。


### 2.4 2,634 題模型工單的批次時程——**規則已裁決（保險版），夜窗排程待你**
保險版（Q57#2）＝每題兩輪共識 ⇒ 單題 2.9 分×2 ⇒ **全批≈254 h 串行**。可切：先 reapply 368（帳本已確認過的）／figure 634 ／fresh 1,632 最後。
**要決定（只剩排程）**：分幾個夜窗、每窗限量。工具已就緒（runner＋交集報告），排程一到即可開跑。
**試跑（2026-09-30 深夜，reapply 前 20 題，commit `7b75e6b`）**：A/B 兩輪拒收率 0.95/0.95、19/20 兩輪同因＝「帳本已有人的 correction（重折用）」→ **reapply 批的主體其實是 deterministic 折回，不是模型工單**；兩輪唯一共同落稿的 q070（option A/B/C → `T<sub>H</sub>1/2/3`）共識 **3/3＝1.0**。試跑還修了 `--compare` 的量測缺陷（交集鍵沒帶欄位、一題多份草案會量錯——q070 被量成 0）。⇒ **建議**：reapply 368 走 deterministic 折回＋人工核可（零模型），2,634 ≈ 只剩 fresh 1,632＋figure 634 的模型批；**等你裁決**。

### 2.5 藥師(二) 全批稽核——**工具已修（Q57#5），全批時程等你排**
~~樣本抓到 crop 對位缺陷~~ → **根因＝「鄰題圖取代一致判讀」的取代規則**（不是 crop 綁定——離線復刻證明帶與圖都對位正確；是 `reference_read` 把「解釋更多」量成「欄位不同」，鄰題轉錄處處不同 ⇒ 取代一致判讀）。**已修**（取代閘＝第一次判讀真盲：▢ 或沒切成圖；負控制進單元測），**複驗 12 題真腦：整篇張冠李戴＝0**，
真 diff 4 筆有修復價值（斜體 `<i>`、`m<sup>2</sup>` 上標攤平 ×2、題幹「76」被吞）。詳見 workplan 4.2 修復段。
- findings（樣本＋複驗）在沙盒 `store/scans/20260930-4.2/`。
- **要決定（只剩排程）**：全批 5,040 題 ≈ 6-7 小時串行（一個夜窗装得下）；跑不跑、哪個夜窗＝你排。
- **稽核目標重定向（2026-09-30 深夜）**：scan 加了 `--only-touched`（最新人工 block／accept 才算看過；commit `9d6ae27`）。量到：**醫事檢驗師 15,280 題、人工決定過 825**；**藥師(一) 6,000 題、人工決定過 5,999**；**藥師(二) 5,040 題、人工決定過＝0**（「暫緩藥師(二)、只進有人決定過的」自然為空）。第一目標＝醫事檢驗師的 825 題；藥師(一) 5,999 題 ≈ 7-8 小時＝夜窗事。
- **bounded 第一片已跑（同夜）**：醫檢 touched 前 99 題＝**43 不一致／56 一致／0 讀不到**（404 s；104 個 diff 欄位：斜體遺失 84＝大宗、上下標攤平 ~10、寬度家族 ~6、存疑 3 筆模型可能弄反留人工）。續跑同一命令＋`--skip-confirmed`；剩醫檢 726 題 ≈ 50 分鐘。詳 workplan「⭐ 工作項 c 實跑」。

### 2.6 5.1 路由切換（G3，單獨窗口）——**已執行完畢（Q57#6「d 現在改」；commit `2f78aef`，PR #18 隨動）**
設計者裁定：**v2＝正式版，正式輸入不帶 /v2 標籤**。**已上站**（2026-09-30 深夜 `deploy_station.sh --restart`）：站上 http://192.168.10.70:8765/ **現在直接就是 v2**（root bytes＝v2.html，cmp 相等）；`/v2`、`/v2/`→302 `/`；`/workflow`→302 `/v1/workflow`；`/legacy`→302 `/v1/legacy`；`/v1/workflow`、`/v1/legacy`＝200（bytes 與舊頁相同）；`/mobile/sw.js`、`/v2/01-core.js`＝200（PWA／區檔契約不動）。302 而非 301（過渡，撤銷不必清快取）；路由映射單一來源（`/v2`、`/workflow` 不再有第二份供應來源——2026-09-24 兩份清單不同步的教訓）。驗證：隔離伺服器（8899）＋站上各一輪全綠，`test_v2_navigation.mjs` 全部符合，`/api/candidates` 200（79,090 題），qbr 全量 806 passed／18 skipped。證據段：workplan「⭐ 第 5 批結果」。

### 2.7 zero-hit 條目——**已執行退場（Q57#7；compress-2）**
設計者裁定「退場，之後有新的問題用新的決策方式重新建立」。
已照做：`rules/retire_rules.mjs --ids p1,p3,p4,p5,p7,p11`（sha `5b16908753f16181`→`08c65ad93e3e8b30`，version＝`stream-import-357ca0e6+compress-2`，`retired_by: designer-2026-09-30-chat(Q57#7)`）；**active 剩 p18**（試跑裡它自己記了 2 次命中）；`PROMPT_VERSION` → `…+compress-2`；受影響的 tool-seam 測改用 p18 當正例、p1 當負例，全套回 75/0。**可逆**：每筆帶 lineage，一行撤銷即可。
⚠️ **要你知情的一個不對稱**：命中計數器今天才開始走（2026-09-30），p3「判斷要看紙本截圖、而且要看對那一題」這類其實**每一題的判斷都依賴**，只是沒有「被引用」的紀錄。退場後若判斷品質掉，第一件事就是把 `retired_by: designer…` 撤銷。

### 2.8 「未來核可面」的方向（Q58#4；設計者已表態，契約待設計）
設計者方向（原話）：**未來若有新的文字要核可，應該會在新的討論區，而不是現在暫時的 UI**（沙盒的全域環境還沒測試好，無法融入現在的 Review UI）。據此：
- `8791` 字形頁＝**workplan 4.1 這張表的專用一次性核准面**：表點完即退役，不做常駐。
- 下一張表（例如斜體遺失 84 處、上下標攤平 ~10 處的 deterministic 修補核可）沿用「表 → 設計者一次點頭」的**形**，但**面**要等契約設計。
- 待設計（owner 決定）：核可面進 Review UI 主場的契約——charter 申明 Review UI 程式與部署閘門仍待 owner 討論；沙盒全域環境未融入。**在契約定案前，任何新文字核可不再開新的臨時頁**：要嘛等正式面定案，要嘛沿用「8791 同型頁換表換資料」——你選。

## 3. 事故與取捨（你可以推翻）

- **本回合初把草案產生器寫成 tmp+move 洗檔**：草案檔是 append-only 共享檔，洗檔=毀別人的行——改成追加＋(key,field,fix) 冪等跳過，單元測試記負控制。
- **第一批 variant B 試跑被 300 秒 deadline 殺掉**（工具層預設值，不是 runner）；該次留下半成品 session 但**零草案、零報告**（重跑的 B 是完整輪）。工作樹無殘留。
- **--report 是「讀回統計」而非「跑完印報告」**：先撞了它（退出 0、沒跑），去掉才開跑——文件沒寫錯，是我讀錯。
- **模型類工作全部串行**（同一台 MTPLX 引擎）：A/B/稽核樣本一次只跑一個批次。
- 藥師(二)母體 5,040 vs 舊估 4,410：以掃描器自己的考別選題為準逐次量，不沿用舊數。

## 4. 驗證指令（你回來照這個順序看）

```bash
cd "/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog/repair_agent_test/agent"
./run_tests.sh                       # 75 pass / 0 fail
node runner.mjs --workorder store/workorders/wo-20260930-superscript-sample.jsonl \
                 --compare A B       # 交集 / 拒收率 / 欄位填全率（讀 store/workorders/report.jsonl）
cd ../../qbr && .venv/bin/python -m pytest tests/test_repair_drafts.py tests/test_scan_rule_hits.py -q
# 沙盒草案總覽（應 7,585 行＝7,584 deterministic＋1 模型〔試跑 A 的佈〕；B 輪 2 筆在其後追加）
wc -l ../repair_agent_test/agent/store/repair_drafts.jsonl
```

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->