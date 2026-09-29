# 官方來源增量掃描與審核邊界

Status: local deterministic design; no external runtime is configured.

官方 PDF 的增量掃描可以在本機建立新的 source manifest、candidate package 與 review bundle。
本流程不把下載、解析、審核或發布委託給外部 worker、workflow service 或資料庫 writer。

## Current boundary

```text
official source -> frozen manifest -> deterministic extraction -> gates
               -> immutable candidate package -> local v2 review UI -> append-only events
```

- 每次 run 使用新的 output directory 與 run id。
- source、candidate、review bundle 與 manifest 都要記錄 hashes。
- PDF 邊界、頁數、文字、字型、幾何、題號與 package integrity 由 deterministic checks
  驗證；語意解釋不是硬編碼規則。
- 人工 reviewer 才能 accept、block、needs-review 或 correction。
- AI output（若當次任務核准 local model）只能作 advisory，不能建立正式 decision。
- 沒有 external UI、external database、remote sync、scheduled production execution 或
  publish/apply command。

## Failure and resume

任何階段失敗都應保留該 run 的 evidence，建立新的 run 修正輸入或 parser 後重跑；不得覆寫
append-only events 或把未驗證的 bundle 當成正式 package。缺少 source、manifest、review-store
owner 或新的 task contract 時，停止而不是猜測 fallback。

## Related entrypoints

- 主線：`qbr/AGENTS.md`
- review：`docs/skills/run-question-review-loop/SKILL.md`
- status：`docs/skills/qbr-pipeline-status/SKILL.md`
- 本機工作流：`docs/local-review-workflow.md`
