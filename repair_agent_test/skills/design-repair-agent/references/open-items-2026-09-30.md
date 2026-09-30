# 開放項目回報 — 2026-09-30 深夜批次（離席授權 G0–G2；設計者未在場）

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

## 2. 等裁決清單（今晚動不了／不該動的）

### 2.1 學習語料權威（Q56，跨窗口）
筆電 `qbr/data/review-queues/live/` 是快照（本回合未 sync）；佇列權威在站上 `tim@192.168.10.70:~/qbr-review/queue/`。
要決定：哪一台是學習語料的單一權威、另一邊是快照還是分岔。

### 2.2 A/B 噪音底量到「單輪兩引擎確認不可重現」→ 批次規則要不要改成共識制
同 prompt 同腦兩輪跑同一 5 題樣本：
- **交集（key→同一欄、同一修法）＝0**。A 輪落 q033 選項 C「佈↔布」（兩引擎確認）；B 輪落 q033 **選項 A** GABA→`GABA<sub>A</sub>`——**同一張裁片，A 輪說選項 A 是普通文字、B 輪說是下標**（直接矛盾）。
- 穩定的部分：q044／q034 兩輪都判「不需要修」；q056 兩輪都沒草案（B 輪連結語都沒有——新收集器已明標）。
- 含義：**草案不能以單輪 session 的「兩引擎確認」當終證**；要嘛 `A∩B` 同修才提（成本×2，2,634 題≈254 h），要嘛只跑 deterministic 類＋模型留人抽核。**選哪個＝你的裁決**（3.4 工具兩種都支援）。

### 2.3 4.1 落地（G3）
沙盒已有 **7,584 筆 deterministic 草稿**（全庫）＋**3 筆模型試跑草案**（A/B 兩輪互不共識、甚至同裁片同選項矛盾——§2.2，全留人工）。落地路＝`apply_text_corrections` 正式檔（帶 append-only reset_review）。
要決定：①核可哪些類先落（建議 deterministic 類先行——它有磁碟 vs 紙本的逐碼點證據；模型草案這兩筆剛好互相矛盾，全部留人工）；②哪個維護窗口、一次單版本線。

### 2.4 2,634 題模型工單的批次時程
單題量到 2.9 分（試跑兩輪一致）⇒ 全跑≈127 h。可切：先 reapply 368（帳本已確認過的）／先 figure 634／fresh 1,632 最後。
要決定：分幾個夜窗、每窗限量、要不要共識制（見 2.2）。

### 2.5 藥師(二) 全批稽核——**先卡在 crop 對位缺陷，全批等修後裁決**
Bounded 樣本已跑：`--limit 50 --skip-confirmed --apply --model occamy-6bit`，findings 只寫沙盒
`store/scans/20260930-4.2/question_ai_findings.sample.jsonl`；**249 秒跑完 50 題**（平均 2.5 s/題——這條 audit 讀法比 runner 便宜兩個數量級）；結果欄位：50 題裡讀不到 0、不一致 29、一致 21。
- **量到的缺陷（程式形的，不是腦的）**：29 個 diff 列裡 **21 個＝crop 張冠李戴**——`from`（抽取值，candidates）與 `to`（紙本轉錄，crop）是**整篇不同的題目**（例：q001 抽取＝nitrofurantoin 題、crop 轉錄出「出生10天女嬰 meningitis」題）。實證：finding 的 crop 路徑＝`crops/1152_藥師(二)_藥學(五)_q001-dispute.png`——**dispute 時代的舊 crop 檔**；1.1 重建後題號↔內容重新洗牌，這條線的 crop 綁定只用「考卷＋題號」，**沒有綁內容 hash ⇒ 靜默對到自己舊題**。**未修前，category-scan 的 diff 絕不可信**（4.2 全批 5,040 題就此擋下）。
- **8 個列是真的（價值的證據）**：q003 題幹抽取**斷字**「白血球增多→白多」（lost_glyph 類，嚴重）；q004「AST∕ALT」vs「AST/ALT」、q014「2~4」vs「2∼4」（寬度家族＝我的 3.2 鏡頭已罩住的那類）。⇒ 修好對位後，這條 audit 的價值＝**找斷字／缺字**這種 deterministic 鏡頭抓不到的類。

### 2.6 5.1 路由切換（G3，單獨窗口）
root serve v2、舊頁/舊資料遷 `/v1/*`、PWA redirect 過渡。要排獨立維護窗口（charter §7；不與 refs 重建共窗）。

### 2.7 壓縮 pass 1 之後的 zero-hit 條目
合併後家裡 active 18→7 條，全部仍 zero-hit（命中量測 3.2 才開始）。要決定：留觀一輪還是先退場沒用到的。

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