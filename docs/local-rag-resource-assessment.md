# 本地 RAG 知識庫資源評估

本文件評估在 `tw-national-exam-catalog` 本機環境建立 RAG 知識庫所需的硬體、軟體、資料庫、模型與儲存資源。

評估時間：2026-06-17

## 目前本機狀態

硬體與系統：

- CPU / SoC：Apple M4 Max
- CPU cores：14
- GPU：Apple GPU，系統回報為 AGXAcceleratorG16X
- GPU cores：32
- RAM：36 GB
- 可用磁碟：約 170 GiB

資料庫：

- PostgreSQL：Docker container `tw-national-exam-postgres`
- Image：`pgvector/pgvector:0.8.2-pg18`
- pgvector：0.8.2
- Port：`localhost:54329`
- 目前 DB 大小：約 29 MB
- PostgreSQL 預設設定：
  - `shared_buffers = 128MB`
  - `work_mem = 4MB`
  - `maintenance_work_mem = 64MB`

本地模型與套件：

- `ollama` 已安裝，但目前沒有已下載模型。
- `uv` 已安裝。
- 目前 system Python 沒有安裝 RAG 常用套件，例如 `torch`、`sentence_transformers`、`transformers`、`psycopg`、`pgvector`。
- 建議建立專案專用 `.venv`，不要污染 system Python。

目前資料量：

- 專案與本機工作資料夾合計：約 4.3 GB
- `國考題資料夾`：約 4.2 GB
- 官方 PDF：約 1.9 GB
- MinerU output：約 232 MB
- Registry：約 47 MB
- 已有 MinerU markdown：162 份
- 已有 MinerU markdown 總文字量：約 1.73 MB
- MinerU markdown 平均大小：約 10.7 KB / 份
- PostgreSQL 已入庫 official documents：8181
- 題目 PDF：3459
- 答案 PDF：3352
- 更正答案 PDF：1370
- 題目答案 paired rows：3459

## 資料規模估算

目前 MinerU 仍在跑，正式 paired-primary 任務總數為 6845 份。

以目前 162 份 markdown 平均 10.7 KB 估算：

- 6845 份 markdown 約 73 MB 原始 markdown 文字。
- MinerU output 圖片與中間檔目前平均約 1.4 MB / 份；若線性放大，完整 paired-primary output 可能接近 9-10 GB。
- 官方 PDF 已約 1.9 GB，已可接受。

若只針對結構化題目建立 RAG：

- 題目數量初估：3459 題本，每份約 80 題時約 276k 題；實際不同考試題數不同，應以解析後資料為準。
- 每題建立 `full_question` embedding，向量筆數可能在 200k-300k。
- 若每題另建 `stem`、`options`、`answer_explanation` 多種 scope，向量筆數可能變成 2-4 倍。

若針對課本知識庫建立 RAG：

- 需視教材數量與切 chunk 策略而定。
- 粗估每 1000 頁教材切成 3000-8000 chunks。
- 10 本中大型教材可能產生 30k-100k chunks。

## 向量儲存估算

向量儲存大致由 `維度 × 4 bytes × 筆數` 決定，還要加上 PostgreSQL row overhead 與 index overhead。

常見估算：

- 768 維、100k 筆：約 293 MB raw vector。
- 1024 維、100k 筆：約 391 MB raw vector。
- 1024 維、300k 筆：約 1.17 GB raw vector。
- HNSW index 可能再增加 1-3 倍空間，依參數而定。

以目前 170 GiB 可用磁碟來看：

- 題庫向量索引足夠。
- 加上多本教材知識庫也足夠。
- 最大壓力不在磁碟，而在 embedding 模型下載、批次推論時間、GPU/MinerU 併發與 PostgreSQL index build memory。

## 模型選擇

本地 RAG 至少需要兩類模型：

1. Embedding model：把題目與教材 chunk 轉成向量。
2. Reranker：把初步檢索結果重新排序，提高精準度。

因為題目多為繁體中文，但參考書與 guideline 可能是英文，embedding model 必須優先支援跨語言語意對齊。單語中文或單語英文模型不適合作為主索引。

可行 embedding 選項：

- `BAAI/bge-m3`
  - 優點：多語、長文本、支援 dense / sparse / multi-vector 思路，適合中英文混合與繁中醫學題。
  - 風險：模型較大，初次下載與本機推論成本較高。
- `Qwen3-Embedding-0.6B`
  - 優點：新一代多語 embedding，模型大小較適合本機試點。
  - 風險：需實測繁體中文與醫學術語表現。
- `mxbai-embed-large`
  - 優點：Ollama 生態常見，部署簡單。
  - 風險：中文醫學語境需實測，不宜直接假設效果。
- 小型 multilingual sentence-transformers
  - 優點：CPU 可跑、便宜、適合快速試點。
  - 風險：相似題品質可能不足，需要 reranker 補強。

建議策略：

1. 先用一個小型/中型 embedding model 跑 80 題或 1000 題 benchmark。
2. 建立人工評估集：每題標出 3-5 題理想相關題。
3. 比較 top-k 命中率、同科目偏差、同年度偏差、相同關鍵字但不同觀念的誤召回。
4. 再決定是否升級到較大 embedding 或 reranker。

跨語言 benchmark 應額外測：

- 中文題目能否召回英文教材 chunk。
- 中文醫學名詞與英文專有名詞是否能對齊。
- 英文縮寫、拉丁學名、藥名、檢驗項目是否能被穩定召回。
- 中文翻譯不同但英文概念相同時，是否能找到同一批知識來源。

若單一多語 embedding 表現不足，第二階段再加入：

- bilingual term dictionary：中英醫學名詞表。
- query expansion：同一題產生中文觀念摘要、英文觀念摘要與中英關鍵字。
- hybrid retrieval：pgvector + BM25 + metadata filter。
- reranker：對混合召回結果重新排序。

## 最小可行 RAG

目標：先完成需求一的基礎能力，也就是「輸入某一題，找相似題」。

需要資源：

- PostgreSQL + pgvector：已具備。
- 專案 `.venv`：需要建立。
- Python packages：
  - `psycopg[binary]`
  - `pgvector`
  - `numpy`
  - embedding runtime，例如 `sentence-transformers` 或 `fastembed`
- embedding model：先選一個本地多語模型。
- 資料表：
  - `question_embeddings`
  - 可選：`rag_index_runs`

工作流程：

```text
questions / options
        ↓
build embedding input text
        ↓
local embedding batch
        ↓
insert into question_embeddings
        ↓
pgvector top-k search
        ↓
evaluate related questions
```

建議先只做：

- `text_scope = full_question`
- 只針對已解析入庫的題目。
- 不先處理課本。
- 不先接外部 LLM。
- 先保留跨語言欄位設計，但第一輪只對中文題目建索引。

資源預估：

- 80 題試點：幾秒到數分鐘。
- 1000 題試點：數分鐘到十幾分鐘，視模型而定。
- 全題庫：可能數小時，建議離線背景跑。

## 可擴充 RAG

當最小 RAG 穩定後，再加：

- `stem` embedding
- `options` embedding
- `answer` / `explanation` embedding
- `knowledge_chunks` embedding
- BM25 / trigram keyword search
- reranker
- `question_relations`

建議檢索流程：

```text
query question
        ↓
metadata filter
        ↓
pgvector top 100
        ↓
BM25 top 100
        ↓
merge candidates
        ↓
rerank top 50
        ↓
return top 10 with evidence
```

## 與 MinerU 的併發

目前 MinerU 以 2 workers 執行，GPU 使用率可達高檔。若同時跑大型 embedding model，可能造成：

- GPU 記憶體壓力。
- MinerU 速度下降。
- 系統 memory pressure 增加。

建議：

- MinerU 大批次期間，RAG 只跑小規模 CPU embedding 或 DB schema 工作。
- 大量 embedding 安排在 MinerU 暫停或低負載時段。
- 背景 job 加上 concurrency limit。
- 寫入 `model_runs` / `rag_index_runs` 記錄資源與耗時。

## 建議 PostgreSQL 調整

目前設定足以做小規模測試。當向量筆數超過 50k-100k 後，建議：

- 提高 `maintenance_work_mem` 以加速 HNSW index build。
- 視容器可用記憶體調整 `shared_buffers`。
- 對 embedding table 分 scope 或分來源建 partial index。
- 先用 exact search / IVFFlat / HNSW 的小樣本比較，再決定索引策略。

暫時不要一開始就追求最佳索引。先建立可驗證 benchmark。

## 建議資料表

```sql
CREATE TABLE exam.question_embeddings (
    id BIGSERIAL PRIMARY KEY,
    question_id BIGINT NOT NULL REFERENCES exam.questions(id),
    embedding_model TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    text_scope TEXT NOT NULL,
    input_text TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    embedding vector(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (question_id, embedding_model, text_scope, input_hash)
);
```

注意：`vector(1024)` 要跟模型維度一致。若要同時比較 768 / 1024 / 1536 維模型，可以：

- 建多張表。
- 或用不同欄位。
- 或先選定單一模型，避免 schema 過早複雜化。

## 風險與解法

- 中文醫學語境 embedding 可能不準：建立人工 benchmark，不靠直覺。
- 單純向量搜尋會召回表面相似題：加入 metadata filter、BM25、reranker。
- 同一年度同科目題目可能過度聚集：評估時分年度檢查。
- 授權域混淆：官方題目資料庫可公開，包含題目 OCR 文字、圖片、表格；課本、講義、商業教材則不可公開全文、OCR、圖片、表格或可重建原文的衍生資料。
- 外部模型成本高：RAG 第一階段完全本地，不使用外部 LLM。
- MinerU 併發吃 GPU：embedding 批次排程避開 MinerU 高負載。

## 建議下一步

1. 建立本地 RAG Python 環境與 requirements。
2. 選 1 個 embedding model 做 80 題試點。
3. 新增 `question_embeddings` schema。
4. 寫入 80 題向量。
5. 實作 `find_related_questions.py`，輸入 question key，輸出 top-k 題目。
6. 人工檢查 top-k 結果，決定是否換模型或加入 reranker。

## 參考

- BGE M3 paper: `BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation`
- Qwen3 Embedding paper: `Qwen3 Embedding: Advancing Text Embedding and Reranking Through Foundation Models`
- pgvector: 本專案目前使用 `pgvector/pgvector:0.8.2-pg18`
