# 台灣國考題目錄資料專案

本專案整理考選部官方試題與答案的目錄、可驗題目 package，以及人工審題介面。
目前只保留兩條 active track：

| 軌道 | 責任 | 入口 |
|---|---|---|
| `qbr/` | 官方 PDF → 可驗 package；讀語料、不寫資料庫 | [`qbr/AGENTS.md`](qbr/AGENTS.md) |
| `review_ui/` | 人工審題；`/v2` 是基準線 | [`review_ui/AGENTS.md`](review_ui/AGENTS.md) |

目前沒有指定的外部 AI 節點、遠端 review writer 或 production runtime。模型只能使用
當次任務明確核准的本機 endpoint；未來若需要外部節點，必須以全新任務建立資料、權限、
artifact、驗證與 rollback 契約，不得從舊部署文件恢復依賴。

## 治理與邊界

- 跨專案最高規範：[`../docs/ARCHITECTURE_CHARTER.md`](../docs/ARCHITECTURE_CHARTER.md)。
- 本 repo 的治理權威：[`docs/governance/README.md`](docs/governance/README.md)。
- QBR Agent 可在指定流程內更新自己的 AI 結果及 AI `pass`／`return`／`block` 狀態；這不等於人工 accept/block 或正式核准。人工決定事件維持 append-only，Agent 不得寫入或修改。
- 官方 PDF、MinerU output、candidate JSONL、review events、manual assets 與其他大型衍生物
  放在本機資料根，不進 Git。
- `qbr/` 不修改題庫資料庫；catalog 是題目真相的 owner。
- `review_ui/v2.html` 取用 qbr package，review record 由當次明確指定的 JSONL store 保存。

## 結構

```text
qbr/                 官方 PDF → 可驗封裝的主線
review_ui/           v2 審題介面；v1-reference 僅維持舊書籤
catalogs/            目錄與發布用 metadata
schemas/              package 與資料契約草案
scripts/              下載、解析、驗證與本機 review 工具
configs/              不含秘密的本機設定範例
docs/                 契約、技能與治理文件
國考題資料夾/         本機 corpus／derived artifacts，不進 Git
```

## qbr：建立題目 package

先讀 [`qbr/AGENTS.md`](qbr/AGENTS.md) 與
[`docs/skills/build-exam-question-bank/SKILL.md`](docs/skills/build-exam-question-bank/SKILL.md)。
典型流程只讀官方來源並輸出可驗 package：

```bash
cd qbr
.venv/bin/python scripts/golden_path.py run \
  --registry-key moex:{exam}:{category}:{subject}:{set} \
  --year <year> --ordinal <ordinal> --category <category> --subject <subject> \
  --asset-root "../國考題資料夾" --out /tmp/qbr-run \
  --package-version <package-version>
.venv/bin/python -m pytest tests/ -q
```

腳本只量紙張的可重現性質（頁數、字數、字型、墨跡、幾何）。文字意義與模型意見留在
prompt／advisory，不把每個案例再編成規則。

模型操作規則見傘層
[`skills/operate-local-open-models/SKILL.md`](../skills/operate-local-open-models/SKILL.md)。
所有 endpoint 必須是本機且由當次任務指定；共享 code 不得寫死遠端主機。

## review UI：人工決定

`review_ui/v2.html` 是目前基準線。建立 review queue 後以本機服務檢視：

```bash
scripts/review_run.sh <review-workdir> 8774
# http://127.0.0.1:8774/v2
```

`W`／`S` 導覽、`A` accept、`R` needs review、`B` block、`E` 修正。審核事件 append-only；
重建 queue 前先讀 [`docs/skills/review-ui-v2/SKILL.md`](docs/skills/review-ui-v2/SKILL.md)，
不可把舊 SQL 或舊 deployment copy 當成 writer authority。

## 歷史與未啟用內容

舊的主機部署、遠端 worker、SQL staging 與模型 benchmark 只可作考古，不屬於 active
入口，也沒有任何 current runtime 授權。它們不得被複製回目前流程；若未來要恢復某項能力，
先建立新的 owner-approved contract，再新增對應實作。

## 資料與發布

大型 PDF、圖片、OCR markdown、資料庫與 index 都放在本機或明確的 package/artifact store；
Git 只追蹤程式、契約、技能、manifest 摘要與小型測試 fixture。發布前必須有 checksum、
package version、來源定位、驗證結果與人工 promotion evidence。
