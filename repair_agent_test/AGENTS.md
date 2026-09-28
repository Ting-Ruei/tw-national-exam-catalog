# repair_agent_test — agent 入口（設計／沙盒層）

## ⭐ 要動工前只讀這兩份（2026-09-28）

| 問題 | 開這份 |
|---|---|
| **自進步 Agent 要怎麼寫** | [`skills/design-repair-agent/references/pi-agent-design.md`](skills/design-repair-agent/references/pi-agent-design.md)（**底層＝Pi SDK**；為什麼不救 `repair_agent.py`；Pi `0.87.1` 實測接點；互動 UI 規格；負對照驗收表） |
| **還有什麼要做、先做哪個** | [`skills/design-repair-agent/SKILL.md`](skills/design-repair-agent/SKILL.md) **§9 是唯一權威清單**（§9.6＝設計者已下令的五件） |

> ❌ **不要回頭去救 `qbr/scripts/repair_agent.py`。** 設計者一開始就指定**以 Pi 為底層**；
> 那份 Python 檔是**零 caller ＋ 一跑就 crash**（`args.principles_for_prompt` → `AttributeError`，
> 且 `learned=None` 使經驗檔從未進提示詞）。**它只當 Python 參考。** 詳見 `pi-agent-design.md` §0。

## 🔴 開工第一件事（設計者已決，2026-09-27）

> **「站上是家。」** 筆電的 `qbr/data/review-queues/live/` 是**會過期的快照**。
>
> ```bash
> repair_agent_test/scripts/sync_from_station.sh            # 開工：撈回最新狀態
> repair_agent_test/scripts/sync_from_station.sh --status    # 只比對，不搬
> scripts/deploy_station.sh --restart                         # 改完：推回去（--restart 不能省）
> ```
>
> **不先撈就會有溝通問題。** 協定首次跑就抓到 4 份檔案全在漂移：
> `question_ai_findings.jsonl` 站上 **107,991** vs 筆電 **86,319**；
> `figure_ownership.json` 站上 **2,702 行** vs 筆電 **30 行**；
> `candidates.jsonl` 站上 09-26 11:52 vs 筆電 **09-24 21:07（修復前）**。
> 詳見 [`SKILL.md`](skills/design-repair-agent/SKILL.md) §9.0 與 [`qa-log.md`](skills/design-repair-agent/references/qa-log.md) Q14。

## 我現在應該看哪一份？

**看 `skills/design-repair-agent/SKILL.md`。** 它的 **§9 是唯一權威的待決清單**（已決已做／已決未做／等你／建議順序），
開頭還有「怎麼讀」表與實測報告對照表。**剛才的實驗都在那個 skill 的 `references/` 裡。**

| 你想知道 | 直接開 |
|---|---|
| **⭐ 自進步 Agent（以 Pi 為底層）要怎麼寫** | [`skills/design-repair-agent/references/pi-agent-design.md`](skills/design-repair-agent/references/pi-agent-design.md) |
| 還有什麼要決策、先做哪個 | [`skills/design-repair-agent/SKILL.md`](skills/design-repair-agent/SKILL.md) §9 |
| **D4 向量圖缺口量到什麼** | [`skills/design-repair-agent/references/vector-figure-gap.md`](skills/design-repair-agent/references/vector-figure-gap.md)（**§8 更正**：內嵌圖「5.5%」是探針假象） |
| **全卷 0 選項的真根因（alphabet union）** | [`skills/design-repair-agent/references/option-alphabet-union.md`](skills/design-repair-agent/references/option-alphabet-union.md) |
| 逐字問答紀錄 | [`skills/design-repair-agent/references/qa-log.md`](skills/design-repair-agent/references/qa-log.md) |

**這一層的性質（待確認）：** 目前是**設計與量測層**。產物是「設計決定與量測結論」，
不是主線程式。**要做主線的事（如修 `option_alphabet`）就開 `qbr/` 的 PR，不在這裡改**
—— 本輪已開 `agent/fix-option-alphabet-union-20260927`（只含 3 檔）。

> **2026-09-27 設計者已給出架構方向**（三個 agent＋兩條迴路；見
> [`architecture-redesign.md`](skills/design-repair-agent/references/architecture-redesign.md)）。
> **D1–D14 尚未全部裁決**（G3 拆解、第一個實驗、指導者模型、向量圖缺口、「90-95%」現況、
> 答案修正監控、`review_state.py` 拆分、Block 分岔、**Laya 路由器（D9）**、**決策模型 vs 35B 對照（D10）**、
> **zero-training wrapper（D11）**、**decider-2b-vision（D12）**、**MoE「做事能力」比對（D13）**、
> ~~**修 `no-xref` 分支（D14）**~~ → **D14 已結案（不是修 `no-xref`，而是修 `option_alphabet`；見 Q16）**）。
> **裁決前仍在量測層，不動主線。**
>
> **本輪已完成**：**D14 動工時，D14 自己的前提被推翻**（見 Q16）。
> 原案要修的 `extract.py::image_bytes_of` 是**死碼**（production 零呼叫者），
> 修它不會改變任何一張裁圖。真根因是 **`repair.option_alphabet` 取了 union**：
> **667 篇論文被誤標、658 篇全卷 0 選項**；修正後 **649 篇取回、0 篇變差**，
> 參考庫 recall **447→449**（正好修回 q4/q38）。
> 完整：[`references/option-alphabet-union.md`](skills/design-repair-agent/references/option-alphabet-union.md)。
>
> **協定違規自首**：先前有 4 件事已發生但沒寫進 `qa-log.md`（**D1／D2／D3 的裁決**＋**comment→block 的修復**）。
> 已補記為 **Q12**。（comment→block 的修復**已部署且 hash 已對過**，但**站上 0 筆新人類 comment**＝設計者還沒重試）
>
> **第二輪已定案的三件**：① 指導者＝本 session 的 `deepseek-v4.1-flash`；② **4bit 版剔除（無 vision）**；
> ③ **Mac Studio 只當 Agent 中樞、不當算力中樞**（算力：現在＝M5 Max MBP；未來＝DGX Spark ×2／AMD AI MAX+ 395）。
>
> **稱呼更正**：本層文件先前寫「業主」，**一律改稱「設計者」**（設計者的正式稱呼）。

> **沙盒 → 主線的整合是「取代」，不是「分岔」。** 同 `../../pi_test/AGENTS.md` 的紀律。

### 實驗進行中的註記規則（設計者 2026-09-27 已決，Q15）

> 「實驗階段，可以讓上層或是會影響到的層級，`AGENT.md` 可以做註記（說明這件事正在做），
> 但是**功能要推成正式才大改**。」

- **實驗階段**：只往 `AGENTS.md` **加註記**（說明哪件事正在做、影響範圍）；**不改功能行為**。
- **推成正式**：才做正式變更（大改、PR 合併、部署）。
- **進行中的實驗註記**：
  - **D14**（修 `qbr/src/qbr/extract.py::image_bytes_of` 的 `no-xref`）——**設計者已准開 PR**。影響：所有抽圖出口（跨頁／內嵌表／向量圖）。
  - **A1**（`golden_path` 匯出加頁碼）——**設計者已准做**。零語意風險。
  - **站上同步協定**（§9.0）——已成為**硬規則**（開工先撈）。

## 你在哪裡開 pi？

| 起點 | 工作範圍 |
|---|---|
| 傘層 `ai_learning_platform/` | 跨所有子專案 |
| `tw-national-exam-catalog/` | 題目匯入、審核、優化這一條線 |
| **`repair_agent_test/`（本層）** | **只做修理代理的設計與量測**。不修 `qbr/`、`review_ui/` 主線；不部署 |

## 先讀什麼

| 你要做什麼 | 讀這份 |
|---|---|
| 設計／討論修理代理（Pi 指揮 ornith 看題找錯） | [`skills/design-repair-agent/SKILL.md`](skills/design-repair-agent/SKILL.md) |
| **逐字問答紀錄（append-only，每次問答後立刻更新）** | [`skills/design-repair-agent/references/qa-log.md`](skills/design-repair-agent/references/qa-log.md) |
| **⭐ 自進步 Agent 施工圖（以 Pi 為底層）** | [`skills/design-repair-agent/references/pi-agent-design.md`](skills/design-repair-agent/references/pi-agent-design.md) |
| Phoenix Agent 課程與實作的細節抄本 | [`skills/design-repair-agent/references/phoenix-agent-notes.md`](skills/design-repair-agent/references/phoenix-agent-notes.md) |
| **可用模型參考（GLM-OCR 等）** | [`skills/design-repair-agent/references/model-glm-ocr.md`](skills/design-repair-agent/references/model-glm-ocr.md) |
| **可用模型：GLM-OCR 本機實測報告** | [`skills/design-repair-agent/references/model-glm-ocr-measurement.md`](skills/design-repair-agent/references/model-glm-ocr-measurement.md) |
| **圖片定位實測（PP-DocLayout-V3）** | [`skills/design-repair-agent/references/model-glm-ocr-localization.md`](skills/design-repair-agent/references/model-glm-ocr-localization.md) |
| **圖片歸屬與切範圍實測** | [`skills/design-repair-agent/references/model-glm-ocr-attribution.md`](skills/design-repair-agent/references/model-glm-ocr-attribution.md) |
| **掃描件歸屬實測（含 GT 盲點量測）** | [`skills/design-repair-agent/references/model-scanned-attribution.md`](skills/design-repair-agent/references/model-scanned-attribution.md) |
| **考題修復 Agent 架構（重新設計，設計者 2026-09-27 提案）** | [`skills/design-repair-agent/references/architecture-redesign.md`](skills/design-repair-agent/references/architecture-redesign.md) |
| **答案審核的正確性（位移、修正答案、回報缺口）** | [`skills/design-repair-agent/references/answer-authority.md`](skills/design-repair-agent/references/answer-authority.md) |
| **決策模型研究與實測（Kev-4B／Laya，Q9）** | [`skills/design-repair-agent/references/model-decision-models.md`](skills/design-repair-agent/references/model-decision-models.md)（**6 ms 是真的、零樣本可用是假的（45–50% vs 亂猜 25%）**；定位＝路由＋不確定性，不是判讀） |
| **Jev 的開源替代品（Q10）** | [`skills/design-repair-agent/references/jev-open-source-alternatives.md`](skills/design-repair-agent/references/jev-open-source-alternatives.md)（**Jev 是 TypeSafe 的封閉服務**；實測 **Jev-Style 0.8B = 80%** vs Laya 45%；**最值得＝零訓練 wrapper**；**9 個 `awesome-*` 是 SEO spam**） |
| **D4 向量圖缺口實測（Q11，✅ 已量完）** | [`skills/design-repair-agent/references/vector-figure-gap.md`](skills/design-repair-agent/references/vector-figure-gap.md)（**向量圖 19 頁／1.3%**；⚠️ **§8 更正**：「內嵌圖 `xref=0` 5.5%」是**探針假象**，`image_bytes_of` 是死碼） |
| **跨頁＋有圖的截圖為何必錯（Q13）** | [`skills/design-repair-agent/references/crosspage-crop.md`](skills/design-repair-agent/references/crosspage-crop.md)（**一張裁切＝一頁一個矩形**；跨頁題的圖印在文字列所在頁之外；**已修，699→2**） |
| **A2 模型比對 harness（Q17，✅ 已完成）** | [`skills/design-repair-agent/references/a2-model-comparison.md`](skills/design-repair-agent/references/a2-model-comparison.md)（正解＝設計者的 `correct` 事件；**純文字天花板 41.1%／58.9% 的改動資訊不在輸入裡**；`control` 恰好 0.0%） |
| **A2.1 餵圖之後的天花板（Q18，✅ D13 的答案）** | [`skills/design-repair-agent/references/a2.1-vision-ceiling.md`](skills/design-repair-agent/references/a2.1-vision-ceiling.md)（**有圖：occamy 53.3%（可達 78.4%）＞ ornith 47.8%；無圖負對照 27.8%／18.9%** → **進步是圖不是提示詞**） |
| **【Q19/Q20】設計者停下討論：互動 UI、分群、模型選型、提示詞逐字** | [`skills/design-repair-agent/references/qa-log.md`](skills/design-repair-agent/references/qa-log.md) Q19–Q20（**兩模型同架構差在量化**；**79,090 題兩線索表決：92.8% 可快速瀏覽、7.2% 要指導**；**判讀應複用既有 `ai_feedback`，不新開流**） |
| **【Q21】全庫跑過但只跑文字、2,858 張圖題是假票** | [`qa-log.md`](skills/design-repair-agent/references/qa-log.md) Q21（**findings 覆蓋 79,088/79,090，但含圖的 findings = 0**；ABCD 齊全率 99.43%、**0 題缺 key**；**452 題有空選項**） |
| **【Q22–Q24】prompt ablation：拿掉規則分數上升** | [`qa-log.md`](skills/design-repair-agent/references/qa-log.md) Q22–Q24 ＋ `a2/ablate_prompt.py`（**`image_cue` 0.50 ＞ `image` 0.44 ＞ `image_text` 0.32 ＞ `full` 0.11**；**缺陷清單貢獻 0**；**「N 張圖」這句話值 +17.8pp 假分數**；**「非決定性 9.4%」是錯的，真因是漏了 23 字元 trailing cue**） |
| **「文字型表格」怎麼處理（Q14）** | [`skills/design-repair-agent/references/text-table-gap.md`](skills/design-repair-agent/references/text-table-gap.md)（**60 題回報表格→只 32 有裁切**；缺口 18 跨頁＋4 內嵌圖＋6 無圖；⚠️ **§8 更正**：不再說「同一條路的三個出口」） |
| **全卷 0 選項的真根因（Q16，`option_alphabet` union）** | [`skills/design-repair-agent/references/option-alphabet-union.md`](skills/design-repair-agent/references/option-alphabet-union.md)（**667 篇誤標／658 篇全卷 0 選項／修正後 649 篇取回、0 篇變差**；PR `agent/fix-option-alphabet-union-20260927`） |
| 主線題庫建置 | [`../qbr/AGENTS.md`](../qbr/AGENTS.md) |
| 審題介面 | [`../review_ui/AGENTS.md`](../review_ui/AGENTS.md) |
| 治理與 G0–G4 | [`../docs/governance/README.md`](../docs/governance/README.md) |

## 治理（本案不得繞）

- **G2 以上需 owner 逐次核准。** 本層預設**只讀 ＋ advisory**（寫 finding 已是 G2 的邊界）。
- **AI finding 與 human review event 永遠分開、append-only**；不得冒充人類審核者。
- **題目內容不出內網**；模型只能用本機且當次任務明確核准的 endpoint。

## 邊界（不要做）

- 不在這裡改 `qbr/` 或 `review_ui/` 的主線程式；要改主線請開主線的 PR。
  （**注意**：量測用 `qbr/.venv/bin/python`（有 PyMuPDF）；`~/models/venvs/omlxenv` **沒有** PyMuPDF。
  讀 `qbr/src` 的程式當「受測對象」可以，**不改它**。）
- 不在這裡跑 `apply_dispute_repairs --apply`（G3）。
- 不在這裡建新子專案（需 owner 同意）。
- 不寫「沒量到」的數字當事實。
- **不用外部 LLM**（`llm-share` 等）：`QBR_ALLOW_EXTERNAL_LLM` 預設關，題目內容不出內網，需設計者當次明確核准。

- **不用外部 LLM**（`llm-share` 等）：`QBR_ALLOW_EXTERNAL_LLM` 預設關，題目內容不出內網。
  **但設計者 2026-09-27 指出 `llm-share`＝LiteLLM＝同一份 Ollama cloud 訂閱** → **可把工作分層**：
  **A 層（只送提示詞、格式、錯誤摘要、程式碼，不含題目原文）→ 可走外部**；
  **B 層（題目原文、圖）→ 只能內網**。（判定仍以「內容」而非「哪一家雲」為準。）

## 地端模型（本案可自行開關，設計者已授權測試）

```bash
~/models/ornith/bin/ornith start        # MTPLX 35B → 127.0.0.1:18120（載入快）
~/models/ornith/bin/ornith stop
~/models/occamy/bin/occamy start fit6   # 6bit 含 vision → 18130（約 88 tok/s）✔ 候選
~/models/occamy/bin/occamy start abl    # 無審查 GGUF（IQ4_XS 17G，含 vision）→ 18131 ⚠ 須明示，不與 fit6 同時開
~/models/occamy/bin/occamy stop
```

- **不用 `fit4`**（18G，**無 vision**）——判斷需要帶視覺。
- `qwen38-splash`（8088）**只測不主用**（耗資源，且現在是 down）。
- **算力不建在這台 Mac Studio 上**（它要跑考題網站）——它只是 **Agent 中樞**。
