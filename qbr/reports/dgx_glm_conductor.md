# DGX GLM-5.3 指揮者（leader）導入紀錄

日期：2026-10-01　授權：owner「不用等晚上，直接讓地端模型去做」（09-30）＋「我要選全做」（10-01：登記 lane＋指揮者換腦＋99 題小考）
範圍：G2（沙盒 store、AI 自有結果、程式與測試）；零佇列寫入、零人工事件、零落地變更。

## 1. 這份紀錄回答的問題

owner 要求 leader 與 worker 的**智能程度不同**，不是只要求快。本紀錄用同一批 99 題、
同一顆 GLM-5.3-Flash，量「給不給證據」對 leader 判斷品質的影響，並記下 worker 腦
（occamy）在 figure-missing 工單上的實測成本，供 5A 分流決策。

## 2. 端點量測（全部實測）

| 量測 | 框架 | 結果 |
|---|---|---|
| thinking off（`chat_template_kwargs.enable_thinking:false`） | vLLM/uvicorn（引入時） | 0.59 s／14 tok，JSON 一次到位 |
| thinking on | vLLM | 2.96 s／96 tok（≈32 tok/s） |
| 多模態（合成 PNG 文字圖，冷路徑） | vLLM | 8.6 s 逐一字讀對 |
| 升級後 thinking off | tensorfold（served id 不變） | 0.45 s／12 tok，`reasoning_tokens:0` |
| 升級後多模態（真題 dispute crop `q051`，sha8 `dfc89705`） | tensorfold | 3.04 s 讀出 `dD_u/dt = 49 e^{-0.34t}` 正確 |

- served id 升級前後都是 `GLM-5.3-Flash-EXL3` → pin 不用動；框架換了（vLLM→tensorfold，
  `/v1/models` 的 `owned_by` 可見），thinking 拼法重驗仍有效。
- **真缺陷第一次讀就抓到**：`1072_藥師(一)_藥劑學與生物藥劑學` q051 的公式欄，現行
  candidates 整段遺失；GLM 3.1 s 從原圖讀回升冪公式。這是 worker 抽取流的已知缺陷類。
- 認證＝任意 bearer；走 Tailscale `timsdgx:8888`（LAN `192.168.10.90:8888` 同源）。

## 3. 設計決策（防呆契約）

- **pin 必須等於 served id，不做自動 discovery**：`conductor_second_pass.py` 開跑前先
  `GET /v1/models` 對照，不符印出 ⚠ 並 **exit 2**——record 的 `served_model` 欄不可說謊，
  靜默替身是這個模組要防的失敗類。升級＝`QBR_DGX_MODEL=<served id>` 一行，不改碼。
- **lane 語意＝角色**：`dgx-flash` 這條 lane 描述「領導者」這個角色，重指同一 endpoint 的
  lane 不另開（兩個指同一處 lane 就是兩個可以不一致的地方）。
- **`QBR_CONDUCTOR_ENGINE` 可改派**：指揮者跑哪條 lane 是環境變數，呼叫路徑不寫死模型名。
- `judge_with_evidence()`：完整證據包＋裁片一次呼叫，沿用同一 TRUST/CARE/DOUBT 詞彙與
  `self_look` 欄；與 triage 版的差別只在「看不看證據」，方便同批對照。

## 4. 兩輪 99 題小考對照（核心證據）

輸入：`store/scans/20260930-c-audit/question_ai_findings.jsonl` 前 99 列（含 43 列
worker 標 DEFECT）；輸出：`store/scans/20261001-glm-conductor/`。

| 模式 | 結果 | 判讀秒數（合計） |
|---|---|---|
| triage-only（只看文字摘要） | **99/99 TRUST、0 次升級看圖**。43/43 DEFECT 列全 TRUST；3 個 worker 自己標存疑的列也被蓋章 | 306.4 s |
| evidence（完整證據包＋裁片一次給足） | **TRUST 97／DOUBT 2、degraded 0** | 384.1 s（牆上 384.5 s；≈3.9 s/題） |

triage-only 的橡皮圖章具體樣貌：q031 的理由含編造——「地端已自行還原為正確的化學式」，
紀錄裡沒有這件事；q072、q001 同樣未看證據就放行。

evidence 模式翻轉的 2 列（triage 全放行 → DOUBT），**兩列都抓到 worker 編造紙本內容**，
且其紙本讀法事後用原圖獨立驗證成立：

| 列 | worker 主張 | 裁片實際（原圖人工核讀） |
|---|---|---|
| `moex:115090:308:0504:1:question:q001` | 紙本是 "electroosmotic flow"，要求把抽取版「修正」過去 | **electroendosmotic flow**——worker 想把錯字「修」進題目 |
| `moex:115090:308:0504:1:question:q015` | 紙本是 "cysteinyllprotease"（雙 l），要求修正 | **cysteinyl protease**——紙本不存在雙 l，worker 純屬編造（此列連事前存疑清單都沒有，是 evidence 模式新抓到的） |

維持 TRUST 的 3 個存疑列裡，q031（`HC1↔HCl`）與 q072（`septta→septa`）的理由從「編造」
變成「與截圖一致」（例：q031「截圖確認選項 C 為 HCl（鹽酸），抽取版正確」）。
`self_look=True` 只出現在 2 列 DOUBT——需要自己再看一眼的正是它翻成 DOUBT 的那兩列。

**結論：同顆腦、同一批題，差別只有證據——leader 的智能梯度是「證據＋對照」做出來的，
不是換模型換出來的。** triage-only 不可用在稽核母體（已寫進 runner 的 `--with-evidence`
說明文字）。

## 5. 批次鏈脫絡（worker 側，同窗量到的）

- stage 4A fresh A：1,632 題＝5 草案／1,627 拒收／0 failed（42 分）。
- stage 4B fresh B：4 草案／1,628 拒收／0 failed（25 分）。
- stage 4c compare：交集 2、**同修 1、共識率 0.5**——保險版雙輪機制第一次在真批次跑通。
- stage 5A figure-missing（634 題）實測：occamy **9.4 分/題（前 38 分只完成 1 題＋1 題 ≥29 分
  靜默），探針重跑同型題 5.1 分/題、無掛死但無 per-item timeout** ⇒ 兩輪 ≈100 小時；
  agent-session 形態已由 owner 裁決停跑（§6）。

## 6. 分流決策（owner 2026-10-01 已裁）

**裁決：5A/5B/5c 以 agent-session 形態停跑**。實測依據：

| 腦 | figure-missing 單題（agent session） | 兩輪 634 題估計 |
|---|---|---|
| occamy-6bit | 9.4 分/題（前 38 分完成 1 題＋1 題 ≥29 分靜默）；探針重跑 5.1 分/題、無掛死 | ≈100 小時 |
| DGX GLM（`REPAIR_AGENT_MODEL=dgx-flash/…`） | **>15 分未完成（探針 900 s 逾時）** | 更慢 |

換腦救不了這個形態：agent session 的成本在多輪工具徘徊（q031 探進 MinerU model.json），
不在單次推理。而 deterministic 掃描**早就命名了問題與裁片 pattern**——缺的只是「看一次
證據、做一次決策」。

**裁決的替代形態＝`figure_missing_second_pass.py`（證據包生產者，已建＋已驗）**：
吃同一份 `wo-4.1-figure-missing.jsonl`（不重掃），每列一次呼叫：題面＋image_refs 現況
＋磁碟裁片清單＋裁片影像 → decision `insert/none/uncertain`＋refs＋where＋basis。
防呆沿用已驗證模式：refs ⊆ 磁碟清單由 runner 重驗（虛構檔名剔除並記 `ref_unverified`）、
答不可用即 degraded 不預設 insert、queue 零寫入、pin 不符拒跑。

**實測**：真題冒煙 3 列＝3 insert、0 degraded、**3.1–3.5 s/題**（q031 的 basis 從影像讀出
「腹部解剖實照、兩箭號指腹膜構造，與『圖中箭號所指』對應」）——同工作量的形態成本
從 **5–9 分/題降到 ~3.4 s/題（約 100 倍）**；634 題×2 輪 ≈ 72 分。

完整兩輪與重決一致性（§6.1）：

### §6.1 figure-missing 生產者 A/B 輪結果

| 輪 | 634 列決策 | degraded | 判讀合計／牆上時鐘 | 速率 |
|---|---|---|---|---|
| A | insert 626／none 8 | 0 | 4,252.8 s／4,257.8 s（71 分） | ≈6.7 s/題 |
| B | insert 626／none 8 | 0 | 2,096.9 s／2,100.8 s（35 分） | ≈5.1 s/題 |

**`--compare`（同 (decision, refs集合)）：both 634／same 634／rate 1.0**——兩輪零分歧，
8 列 none 是同一批（溫度 0；同一份掃描工單）。對照 agent-session 形態：fresh 工單兩輪的
共識率 0.5（2 交集 1 同修），且成本是兩輪 ≈100 小時 vs 本形態 106 分。

品質抽查（A 輪）：

- **虛構檔名 0、insert 空引用 0**——626 列 refs 全部落磁碟真實檔名（`ref_unverified`
  檢查零觸發）；每列送 1 張裁片。
- 我另用原圖**獨立核讀**過一列（`moex:106020:308:11:1:question:q018`）：GLM 的 basis 寫
  「三條 Lead II 心電圖節律帶（HR 145/85）」——`HR=145` 與 `HR=85` 兩數值都真的印在
  裁片上（`1061_醫事檢驗師_臨床生理學與病理學/q018_embedded-image.png`）。視覺證據是
  真看了，不是文字改寫。
- 8 列 none 都是「**掃描器誤報被 leader 用眼睛擋下**」的樣本：裁片屬於別題或題組共用圖
  （例：q018 的附圖其實是第 19 題的白血病例圖；q013 的 CT 是第 14–16 題共用情境圖）。
  deterministic 掃描只知「有裁片＋refs 空」，圖屬不屬於這題只有看圖才知道——這是
  leader／worker 兩層智能分工的實錘。

智能梯度：worker（occamy）維持量大批次的生產層；leader（GLM+證據）做「需要看證據才能
判斷」的決策層——本次 owner 選項一的正是把兩層各留在各自強的地方。

## 7. 程式與測試

分支 `agent/dgx-glm-conductor-20261001`（自 origin/main，PR #18 merge 點 `cbd1aaa`），
commit `d2c2883`：`engines.py`（dgx-flash 重指 GLM＋served_id probe）、`orchestrator.py`
（`CONDUCTOR_ENGINE`／`conductor_endpoint()`／`judge_with_evidence()`／
`parse_figure_decision()`／`propose_figure_refs()`）、`scripts/conductor_second_pass.py`、
`scripts/figure_missing_second_pass.py`、tests（stub server probe 組＋6 個 second-pass 測試
＋7 個 figure 生產者測試：insert 零隊列寫入／虛構 ref 剔除 degrades/degraded 不預設 insert
/none 清空 refs/pin 不符拒跑且零呼叫/裁片缺席跳過模型/compare 比例）。
Worktree suite **810 passed／38 skipped**。沙盒 suite（repair_agent_test/agent）77 pass／0 fail。