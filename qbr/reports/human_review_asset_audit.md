# 審核資產盤點 + 用戶目錄（human-review asset audit）

來源：`data/db_snapshot/`（read-only 複製，見 `provenance.json`）
實測環境：MacBook 本機 docker；來源容器 `tw-exam-repat-final-postgres`（pg18+pgvector, 127.0.0.1:54329, 庫 `tw_national_exam_dev`, 結構 `exam` / `exam_staging`）
讀取方式：`pg_dump --data-only -Fc`，連線期間設定 `PGOPTIONS='-c default_transaction_read_only=on'`；未對來源庫執行任何寫操作（INSERT/UPDATE/DELETE/DDL 皆無）。
校驗副本：還原至一個全新容器 `exam_copy_probe`（127.0.0.1:55499），`pg_restore --disable-triggers`，行數與來源庫一一對照（見下表）。

## 1. 賬目（row counts，來源 vs 副本，全部相符）

| 表 | 來源 | 副本 |
|---|---|---|
| exam.question_candidates（人為審核所及之候） | 35,496 | 35,496 |
| exam.question_review_events（人事之法） | 46,350 | 46,350 |
| exam.answer_review_events | 20,658 | 20,658 |
| exam.questions / question_options / answers | 15,440 / 61,760 / 15,429 | 同 |
| exam_staging.formal_questions | 2,480 | 2,480 |

未複製（過大，按需再取）：exam.question_ai_review_events（546,108 行 / 1,176 MB）、exam.question_candidates 全表（191,817 行 / 662 MB，僅取其與人為審核相關者）。

## 2. 八柱（何者被人，人何以校正 —— 校正之頻）

對 18,958 個「至少被人工校正一次」的候補記錄，比對其 `normalized_candidate_json`（機器所產）
與最後一次 `corrected_candidate_json`（人所校正），統計其被校正之欄位（top-level keys）：

| 欄位（被校正者） | 校正次數 | 占比 | 對應缺陷類別 |
|---|---|---|---|
| options | 9,687 | 51.1% | 選項合并／選項缺失（最大宗） |
| stem | 7,238 | 38.2% | 題幹斷裂 |
| stem_image | 2,002 | 10.6% | 圖畫裁切（題幹圖） |
| answer_image_refs | 1,999 | 10.6% | 圖畫裁切（答案圖） |
| visual_review | 1,515 | 8.0% | 圖畫複核 |
| group_ref | 1,489 | 7.9% | 組題（題组） |
| image_refs | 622 | 3.3% | 圖畫 |
| **answer** | **22** | **0.1%** | **答案（正解）——幾乎不錯** |

注： percentages 以 18,958 為基數計算，故各（人多）有不必，凡（數）皆如。

## 3. 人為介入之度（審核行為分布）

| action | 次數 | 審核員數 |
|---|---|---|
| accept | 22,271 | 8 |
| unreviewed | 18,087 | 2 |
| block | 1,626 | 20 |
| needs_review | 1,432 | 8 |
| reviewed | 1,316 | 2 |
| reset_review | 932 | 14 |
| confirm_group | 367 | 2 |
| human_review_pdf_visual | 94 | 1 |

| review_status × quality_status | 件數 |
|---|---|
| accepted × pass | 19,253 |
| unreviewed × pass | 14,842 |
| unreviewed × needs_review | 490 |
| accepted × needs_review | 303 |
| unreviewed × blocked | 167 |
| needs_review × needs_review | 146 |
| blocked × blocked | 104 |
| accepted × blocked | 91 |

## 4. 型別（question_type）與器（parser_version）

- question_type：multiple_choice 35,392；unknown 104（**無申論題／非選擇題之記錄**）
- parser_version：`moex_mineru_candidate_v0.4` 31,696；v0.5 1,200；v0.6 1,027；v0.11 928；v0.8 400
  → 全部出自 MinerU 產線（已決定量之器具），故 gold set 之於此刻即可與本產之新產品比對。
- candidate_key 格式：`moex:<年月+類別>:<科目>:<序號>:<節>:question:q<題號>`（如 `moex:101030:902:0805:1:question:q008`）
