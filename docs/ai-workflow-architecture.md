# AI 工作流與知識庫架構草案

本文件規劃 `tw-national-exam-catalog` 在 PDF 解析、題目入庫之後，如何支援相似題查詢、課本知識庫詳解、LLM 詳解、概念圖生成，以及把高成本 AI 產物可追溯地保存回資料庫。

目前定位是架構草案。官方題庫資料可作為公開題目資料庫整理；課本、講義與商業教材則預設為私有知識庫，不隨公開 repo 釋出。

## 目標需求

1. **題目關聯查詢**：使用者輸入或選擇某一題後，可以找到其他相關題目，例如同觀念、同陷阱、同科目、同章節、相似敘述或歷年重複考點。
2. **課本知識庫詳解**：把課本、講義或可授權教材建立成可引用的知識庫，用 evidence / citation 支援題目詳解。
3. **LLM 詳解**：使用本地或外部 LLM 產生詳解，但必須記錄模型、prompt、引用來源、成本與審核狀態，避免把幻覺當成真相。
4. **觀念圖生成**：針對題目涉及的觀念，先產生可審核的概念圖規格，再交由生圖模型產生圖片，並把規格與圖片都存回資料庫。

## 核心原則

- 官方題目、答案、課本原文、AI 生成內容要分層保存，不互相覆蓋。
- 任何昂貴推論都必須可快取、可追溯、可重跑、可比較。
- 向量檢索與 graph relation 都是衍生索引，不是唯一真相來源。
- 官方公開題目資料庫與課本知識庫是不同授權域，匯出時必須分開處理。
- 課本或講義若沒有公開授權，不能跟公開資料集一起釋出全文、圖片、表格或 OCR 內容。
- LLM 詳解一律視為候選內容，必須有 `review_status` 與來源記錄。
- 圖像生成先產生文字規格，再產生圖片；圖片本身也要有審核狀態。

## 專案決議：跨語言 RAG

國考題目主要是繁體中文，但未來參考書、guideline、review article 可能是英文。這會造成單純中文 query 對英文 chunk 的向量召回不穩，因此本專案採用「多語向量 + 查詢擴寫 + 雙向欄位」策略。

決議：

- Embedding model 必須優先選多語模型，不能只用中文或英文單語模型。
- 題目 embedding 保留原始繁中題文，不把原文覆蓋成英文。
- 知識庫 chunk 保留原文，若來源是英文，另外建立 `chunk_text_zh_summary` 或 `chunk_text_zh_terms` 作為檢索輔助欄位。
- 查詢時對同一題建立多個 query representation：
  - `original_zh`：原始中文題幹與選項。
  - `keyword_bilingual`：醫學名詞中英對照，例如「生化學 / biochemistry」、「寄生蟲 / parasite」。
  - `concept_summary_zh`：去除考題語氣後的中文觀念摘要。
  - `concept_summary_en`：必要時產生英文觀念摘要，用於查英文教材。
- 檢索流程採混合召回：中文題目向量、英文概念向量、BM25 關鍵字、中英術語表，各自召回候選，再用 reranker 或規則排序。
- 不把機器翻譯當作正式教材內容；翻譯只作為檢索輔助與引用定位。

實作上，`knowledge_chunks` 可以保留原文欄位，另加衍生欄位：

```sql
knowledge_chunks
  chunk_text_original
  original_language = zh-TW | en | mixed | other
  chunk_text_zh_summary
  chunk_text_en_summary
  bilingual_terms_json
```

引用詳解時必須引用原始 chunk，不引用衍生翻譯欄位作為最終證據。若詳解使用英文教材，輸出應標示其根據來自英文來源與頁碼 / chunk。

## 專案決議：公開內容邊界

本 repo 可以公開結構、索引、程式碼、官方公開考題的整理資訊、官方題目資料庫、類似題關係、人工或 LLM 生成詳解。官方題目資料庫可以包含題目的 OCR 文字、圖片、表格與答案整理。不能公開的是未授權課本、講義、題庫書或商業教材內容。

可公開：

- 官方考試 catalog、下載索引、檔名規則與歷史演進紀錄。
- 官方公開題目與答案的資料庫整理，包含題目 OCR 文字、題目圖片、題目表格、答案、修改答案與結構化選項。
- 官方題目 PDF 的 MinerU 解析結果，包含文字、圖片、表格與 layout 資訊。
- 類似題關係，例如 `question_id -> related_question_id`、relation type、score、產生方式與審核狀態。
- 參考教材後自行撰寫的詳解。
- LLM 自行生成且經審核的詳解、觀念整理、概念圖 spec。
- 不含受版權保護原文的 citation metadata，例如書名、章節、頁碼、chunk id、hash。

不可公開：

- 未授權課本、講義、商業題庫書的全文、段落、圖片、表格或大段翻譯。
- 可以重建課本、講義或商業教材內容的密集摘錄、逐段摘要、逐頁 OCR 結果。
- 課本、講義或商業教材圖片的掃描檔、截圖、圖表重繪且實質近似者。
- 將私人教材 chunk embedding 連同可逆或可推回原文的文字欄位一起公開。

建議資料分層：

- `public/`：可公開 catalog、schema、程式碼、官方題目資料庫、官方題目 MinerU 解析結果、類似題關係、審核後詳解。
- `private_knowledge/`：本機教材全文、OCR、chunk、圖片與未授權衍生資料，永不 commit。
- `derived_public/`：不含原文的知識點、概念標籤、人工詳解、LLM 詳解與引用 metadata。

資料庫也應保存 `content_domain`、`visibility` 與 `license_status`，避免匯出時誤把私人教材資料發布。

建議授權域：

- `official_exam_public`：官方考題、答案、題目 OCR、題目圖片、題目表格，可公開。
- `derived_explanation_public`：人工或 LLM 生成詳解、類似題關係、概念標籤，可公開但需審核。
- `private_textbook`：課本、講義、商業教材全文、OCR、圖片、表格、chunk，不公開。
- `private_textbook_derived`：由私人教材產生、可能接近原文的摘要或翻譯，不公開。
- `public_metadata_only`：只含 citation metadata、hash、頁碼、章節，不含原文，可公開。

## 建議分層

```text
官方 catalog / PDF / MinerU output
        ↓
題庫主資料庫 PostgreSQL
        ↓
題目結構化資料：questions / options / answers / assets
        ↓
知識庫資料：textbook chunks / citations / embeddings
        ↓
檢索層：pgvector RAG / BM25 / reranker / graph relations
        ↓
生成層：local LLM / external LLM / image model
        ↓
審核與快取層：model_runs / generated_explanations / concept_maps
```

## 需求一：題目關聯查詢

題目關聯不建議只靠單一 embedding。建議採用混合檢索：

- 題幹 embedding：找語意相近題。
- 選項 embedding：找相同干擾選項或同類陷阱。
- metadata filter：科目、類科、年度、題號、答案、考試階段。
- canonical concept tag：如疾病、病機、藥物、檢驗方法、解剖位置。
- graph relation：人工或模型建立的題目之間關係。
- reranker：對候選題重新排序，避免只靠向量距離。

建議關係類型：

- `same_concept`：同觀念。
- `same_textbook_chunk`：引用同一課本段落。
- `similar_stem`：題幹相似。
- `similar_options`：選項或干擾項相似。
- `same_trap`：考相同陷阱。
- `prerequisite`：前置觀念。
- `contrast`：容易混淆或相反觀念。
- `duplicate_or_near_duplicate`：高度重複題。

資料表草案：

```sql
question_embeddings
  id
  question_id
  embedding_model
  embedding vector(...)
  text_scope = stem | options | full_question | explanation
  input_hash
  created_at

question_relations
  id
  question_id
  related_question_id
  relation_type
  score
  evidence_json
  generated_by
  review_status
  created_at
```

第一階段可以只做 `question_embeddings` + pgvector nearest neighbor。第二階段再加 `question_relations` 與 reranker。

## 需求二：課本知識庫詳解

課本或講義應切成可引用 chunk，而不是整本直接丟入 prompt。

建議流程：

```text
教材 PDF / markdown
        ↓
OCR / parser
        ↓
章節與段落切分
        ↓
chunk 清理與 hash
        ↓
embedding
        ↓
citation-aware retrieval
        ↓
詳解生成
```

知識庫資料表草案：

```sql
knowledge_sources
  id
  source_type = textbook | lecture_note | guideline | article | other
  title
  edition
  author
  publisher
  license_status
  storage_backend
  source_asset_id
  notes

knowledge_chunks
  id
  source_id
  chapter
  section
  page_start
  page_end
  chunk_index
  chunk_text
  chunk_hash
  metadata_json
  review_status

knowledge_chunk_embeddings
  id
  chunk_id
  embedding_model
  embedding vector(...)
  input_hash
  created_at
```

詳解生成時，模型輸出必須包含：

- 使用到的 `knowledge_chunk_ids`。
- 每個關鍵結論對應的 citation。
- 答案選擇理由。
- 其他選項錯誤原因。
- 不確定或教材未明確支持的地方。

## 需求三：LLM 詳解

LLM 可以回答得比課本整理更流暢，但也可能幻覺。因此不應讓 LLM 直接寫入「正式詳解」，而是寫入候選詳解。

建議三段式：

```text
retrieve evidence
        ↓
draft explanation
        ↓
verify citation and answer consistency
        ↓
store candidate explanation
```

資料表草案：

```sql
model_runs
  id
  task_type = explanation | relation | concept_map_spec | verification | embedding | rerank
  provider = local | openai | anthropic | google | mistral | other
  model_name
  model_version
  prompt_version
  input_hash
  input_token_count
  output_token_count
  estimated_cost_usd
  status
  started_at
  finished_at
  error_message

generated_explanations
  id
  question_id
  answer_id
  model_run_id
  explanation_text
  explanation_json
  cited_chunk_ids
  confidence_score
  hallucination_risk
  review_status = generated | verified | rejected | published
  created_at
```

重要規則：

- 同一題、同一 prompt、同一 evidence hash、同一模型版本，預設不重跑。
- 先用便宜模型產生草稿，再用較強模型抽樣驗證。
- 詳解必須可比對答案表，若答案不一致，標成 `needs_review`。
- 沒有 citation 的內容只能標示為模型推論，不能標示為課本根據。

## 需求四：概念圖生成

生圖模型不應直接從題文自由發揮。建議先產生可審核的概念圖規格。

```text
question + textbook evidence
        ↓
concept extraction
        ↓
concept map spec
        ↓
human or verifier review
        ↓
image generation
        ↓
store image asset and provenance
```

概念圖規格範例：

```json
{
  "concept": "氣機升降出入",
  "nodes": ["氣", "升", "降", "出", "入", "臟腑功能"],
  "edges": [
    {"from": "氣", "to": "升", "relation": "運動形式"},
    {"from": "氣", "to": "降", "relation": "運動形式"}
  ],
  "must_include": ["升降出入", "臟腑氣機平衡"],
  "must_avoid": ["錯誤解剖圖", "未引用教材的病名"],
  "style": "medical education concept diagram"
}
```

資料表草案：

```sql
generated_concept_map_specs
  id
  question_id
  model_run_id
  concept_title
  spec_json
  cited_chunk_ids
  review_status
  created_at

generated_assets
  id
  model_run_id
  asset_id
  asset_role = concept_map | explanation_figure | thumbnail
  prompt_text
  prompt_json
  review_status
  created_at
```

## 本地模型與外部模型分工

建議先把大部分工作留在本地，外部模型只用於抽樣評估與高價值步驟。

本地適合：

- PDF / OCR 後處理。
- 題目分類與規則型 parser。
- embedding 建立。
- 相似題初篩。
- 簡短摘要與 tag 建議。
- 低成本批次初稿。

外部模型適合：

- 困難題詳解。
- 多證據整合。
- citation consistency verification。
- 跨教材概念整理。
- 高品質概念圖 prompt / spec 產生。

## 成本控管策略

每個 AI 任務都要先估 token，再排入工作佇列。

建議分級：

- `local_free`：本地 embedding、parser、tag、檢索。
- `cheap_api`：低價外部模型，用於大量草稿或分類。
- `standard_api`：一般詳解與 verifier。
- `premium_api`：疑難題、爭議題、發布前抽樣審核。
- `image_api`：只有通過文字 spec 審核後才生圖。

所有外部 API 任務都應記錄：

- provider / model。
- prompt version。
- input / output token count。
- estimated cost。
- cache key。
- retry count。
- error message。

成本上限建議：

- 每日 budget。
- 每批次 budget。
- 每題最高成本。
- 每模型最高成本。
- `dry_run` 模式只估算，不送 API。

## 自動化工作流

建議用一個穩定的 job queue，而不是手工跑單一腳本。

```text
jobs table
  id
  job_type
  target_type
  target_id
  priority
  status
  input_hash
  config_json
  scheduled_at
  started_at
  finished_at
  error_message
```

常見 job：

- `embed_question`
- `embed_knowledge_chunk`
- `find_related_questions`
- `generate_explanation`
- `verify_explanation`
- `extract_concepts`
- `generate_concept_map_spec`
- `generate_concept_map_image`

執行策略：

1. 先建立 job，但不執行昂貴模型。
2. dry-run 估算 token 與費用。
3. 使用者確認 budget 後批次執行。
4. 每個 job 寫入 `model_runs`。
5. 結果寫回對應衍生資料表。
6. 失敗 job 保留錯誤，可重試。

## 推薦實作順序

第一階段：低成本本地可完成

1. 建立 `question_embeddings` 與本地 embedding pipeline。
2. 用 pgvector 做相似題查詢。
3. 建立 `question_relations`，先存向量相似與 metadata relation。
4. 做一個查詢腳本：輸入 question id，回傳 top-k 相關題。

第二階段：課本知識庫

1. 建立 `knowledge_sources` 與 `knowledge_chunks`。
2. 匯入一小本或一章可用教材。
3. 建立 chunk embedding。
4. 用題目找相關課本 chunk。
5. 回傳 citation，不先生成完整詳解。

第三階段：詳解生成

1. 建立 `model_runs` 與 `generated_explanations`。
2. 本地或低價模型先產生草稿。
3. 抽樣用外部較強模型驗證。
4. 建立 review workflow。

第四階段：概念圖

1. 建立 concept map spec。
2. 只針對已審核的 spec 生圖。
3. 圖片 asset 與 prompt 版本化保存。

## 已知限制與解法

- LLM 可能幻覺：用 citation-first、verifier、review status 控制。
- GraphRAG 不會自動變準：先從少量高品質 relation 做起。
- 課本授權可能受限：公開 repo 不保存未授權全文，只保留私有本機資料與 hash。
- 生圖可能畫錯醫學概念：先審文字 spec，再生圖。
- token 成本會膨脹：所有任務先 dry-run 估價，使用 cache key 避免重跑。
- 本地模型品質可能不足：本地做便宜初篩，外部模型只做少量高價值驗證。

## 與現有專案的關係

目前已有：

- PostgreSQL 18 + pgvector 開發環境。
- 官方 PDF asset index。
- 題目與答案 PDF pairing index。
- MinerU markdown / image output workflow。
- 初步題目、選項、答案解析入庫測試。

下一個最小可行步驟是建立 `question_embeddings`，先完成需求一的基礎能力：相似題查詢。
