# docs/ 索引

先看這裡，再看個別文件。這一層把 active contract 與考古材料分開；未列在 active
入口的文件不能授權部署、writer、模型或 production 操作。

## Active 路由

```text
考題建立（官方 PDF → 可驗 package） -> qbr/
人工審題（人做決定、append-only record） -> review_ui/ + 當次指定的 review store
catalog／registry／發布治理 -> 本目錄與 catalogs/
```

目前沒有指定的外部 AI 計算節點；catalog PostgreSQL 已退役，僅在 MacBook Pro 保留
reference copy。Mac Studio `192.168.10.70` 仍運行獨立的 QBR Review UI v2 LAN 服務，
以 QBR queue／append-only review events 為工作資料；網站 production 是另一個責任面。
模型操作只能使用當次任務明確核准的本機 endpoint；新節點或新模型必須另立
owner-approved contract。網站與 Review UI 的變更閘門需分別依 owner 裁決更新，治理文件尚待討論。

| 你要做什麼 | 讀這份 |
|---|---|
| 建立考題（官方 PDF → package） | [`../qbr/AGENTS.md`](../qbr/AGENTS.md) ＋ [build skill](skills/build-exam-question-bank/SKILL.md) |
| 審完一批題，跑 advisory、建候選規則、重掃 | [review-loop skill](skills/run-question-review-loop/SKILL.md) |
| 管線狀態 | [qbr-pipeline-status](skills/qbr-pipeline-status/SKILL.md) |
| 現在站在哪裡、哪些做完哪些沒做完、被罵的每一件事與量到的根因、待決策清單 | [review-platform-status](skills/review-platform-status/SKILL.md) |
| 修抽取／切題缺陷 | [repair-qbr-extraction](skills/repair-qbr-extraction/SKILL.md) |
| 題目課綱分類 | [classify-exam-curriculum](skills/classify-exam-curriculum/SKILL.md) |
| 官方 PDF 結構、圖與選項裁切 | [extract-exam-paper-structure](skills/extract-exam-paper-structure/SKILL.md) |
| 題庫候選 advisory 與安全修補 | [national-exam-ai-audit](skills/national-exam-ai-audit/SKILL.md) |
| 審題介面 | [`../review_ui/AGENTS.md`](../review_ui/AGENTS.md) ＋ [review-ui-v2](skills/review-ui-v2/SKILL.md) |
| 修審題介面的缺陷、新增面板／控制項 | [repair-review-ui-v2](skills/repair-review-ui-v2/SKILL.md) |
| 本機 review 服務 | [deploy-qbr-review](skills/deploy-qbr-review/SKILL.md) |
| 本機模型 | [`../../skills/operate-local-open-models/SKILL.md`](../../skills/operate-local-open-models/SKILL.md) |
| 專案治理 | [`governance/README.md`](governance/README.md) |

## Catalog 與資料契約

- [`source-policy.md`](source-policy.md)：官方來源與目錄規則。
- [`known-issues.md`](known-issues.md)：目錄品質問題。
- [`publication-roadmap.md`](publication-roadmap.md)：package 發布策略。
- [`external-data-location.md`](external-data-location.md)：大型資料根位置。
- [`30-normalized-items-storage-policy.md`](30-normalized-items-storage-policy.md)：衍生資料政策。
- [`question-bank-release-validation-flow.md`](question-bank-release-validation-flow.md)：發布閘門。
- [`ROUTE_HISTORY.md`](ROUTE_HISTORY.md)：v2 取代舊介面的原因。

## 非 active 材料

舊的遠端 worker、主機部署、SQL staging、模型 benchmark 與 migration 文件保留作考古，
但不再是現行規範，也不代表目前存在任何主機或服務。不要從它們恢復 endpoint、writer、
資料庫 owner 或 deployment command。未來若需要該能力，必須以新任務重新定義契約，而不是
沿用舊文件。

`qbr/PROPOSED_WORKFLOW.md` 與 `qbr/reports/` 是 qbr 的測量證據；只有 qbr 的現行
`AGENTS.md`、active skills 與 charter 能裁決目前工作。

管線決策的逐輪量測史見 [`../qbr/ENGINE_STRATEGY.md`](../qbr/ENGINE_STRATEGY.md)；
該文件含被後續實測推翻的早期提案，**不是**現行操作入口。

### 凍結區（preserved/）

[`preserved/`](preserved/) 放「已知不再適用、但刻意保留」的紀錄。這些文件描述**過去存在
什麼**，不授權現在做任何事。

- [`preserved/ai395-retirement.md`](preserved/ai395-retirement.md)：`ai395` 系列
  （51 個 tracked 檔案）已由 owner 裁定不再適用於現行架構。含完整清單，以及哪些設計資產
  未來可能值得借鏡、哪些嚴禁復活。
- [`preserved/review-feedback-agent.md`](preserved/review-feedback-agent.md)：審題回饋
  agent（`scripts/review_feedback.py`）的凍結現況與**待決設計問題**。此線尚未實作完成，
  保留供 owner 另案設計；其中的 `build_ai_task()` 契約是設計意圖的化石。

## 資料邊界

官方 PDF、MinerU output、candidate JSONL、review events、manual assets、資料庫與 index
是本機／外部 artifact；Git 只保留程式、契約、技能、manifest 摘要與小型 fixture。人工審核事件
append-only；QBR Agent 可在指定流程內維護自己的 AI 結果與流程狀態，但不能取代或改寫人工決定。
