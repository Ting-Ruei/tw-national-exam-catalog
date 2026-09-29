#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明原則區是一個獨立的區，而且它管的是**基本原則與代理的反問**。
 *
 * 使用者原文：「我已為原則區你會獨立做一頁UI來管理，結果你藏在錯題討論區，修理代理的反問
 * （4 題待答）這些也應該放在原則區UI來管理，而且是要叫得出原題PDF讓我了解情況的」。
 *
 * 這個檔案把每一條可觀察的行為各釘一次，不是「有沒有那個 div」：
 *   * 按上面的「原則區」真的切到 `#areaPrinciples`，而且只有它可見；
 *   * 原則與反問是**伺服器投影**畫的：畫面上的數量等於 `/api/discuss` 的 `principles.count` 與
 *     `repair_questions.open_count`，而那一頁真的打了那個端點（`limit=1` 不會把投影裁掉）；
 *   * 加一條原則真的 POST `/api/principles`（`action:'add'`），字**不重新載入**就出現在畫面上；
 *     右欄開著某一題時，那一筆會帶 `candidate_key`（原則因此說得出它在講哪一題）；
 *   * 「叫出原題」把那一題的官方 PDF 畫成 `iframe`（`src` 以 `/file?path=` 開頭、以 `#view=FitH`
 *     結尾），抬頭寫出科目／試卷／題號與 key；**沒有 key 的一律說「這條沒有指定題目」，而且不留
 *     下任何 iframe**（負向控制）；
 *   * 回答一則反問真的 POST `/api/repair-question`（`action:'answer'`），那一列不再是「待回答」；
 *   * 錯題討論區**不再**畫原則與反問（負向控制：舊的選擇器 `#discussMain .principles` 找不到東西）。
 *
 * 寫入用**隔離的伺服器**：自己的 `candidates.jsonl` 與自己的三條事件流（`--review-log` 指到 tmp，
 * 原則與反問的流就長在它旁邊）。所以這個 harness 就算真的按了「加入」「送出回答」，也一筆都不會
 * 碰到 live 的紀錄。
 *
 * 用法：node scripts/test_v2_principles_browser.mjs [base]
 *   給了 base 就對那台跑（呼叫端自己負責那是一台隔離的伺服器）。
 */
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const ROOT = path.resolve(decodeURIComponent(new URL('.', import.meta.url).pathname), '..');
const PORT = Number(process.env.PRINCIPLES_PORT || 8917);
const base = process.argv[2] || `http://127.0.0.1:${PORT}`;
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const debugPort = PORT + 1;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failures = 0;
function check(ok, label, detail) {
  console.log(`  ${ok ? 'ok ' : 'BAD'}  ${label}${detail ? `  ${detail}` : ''}`);
  if (!ok) failures += 1;
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

/* 題目 key 用真實的寫法：`moex:115090:<paper>:<...>:question:qNNN`。 */
const KEY_A = 'moex:115090:311:0704:1:question:q001';
const KEY_B = 'moex:115090:311:0704:1:question:q002';
/** 第三題：**沒有被退回過**，但它的 finding 與 KEY_A **同一種形狀**（同一個 detector、同一個
 *  欄位）。原則區的「類似題」清單因此有一個真的叫得出來的目標——而它是被**算**出來的，不是
 *  種子直接寫在那條原則裡的。 */
const KEY_C = 'moex:115090:311:0704:1:question:q003';

/** 一題有官方紙本（原則區要叫得出來的就是它）。 */
const QUESTION_A = {
  candidate_key: KEY_A,
  question_number: 1,
  stem: '下列何者為莢膜的主要成分？',
  options: [
    { key: 'A', text: '多醣體' },
    { key: 'B', text: '蛋白質' },
    { key: 'C', text: '脂質' },
    { key: 'D', text: '核酸' },
  ],
  answer: 'A',
  // 五個欄位是身分投影讀的（`queue_view.question_identity`）：缺了它，原則區那一列就說不出
  // 「哪一題」，而「去看這一題」也**不會跳**（跳到別的紙本比不跳更糟）。
  metadata: { normalized_category_name: '藥師(一)', normalized_subject_name: '微生物學',
              year: '115', exam_ordinal: '2', paper_id: '115090:311:0704' },
  source_files: {},
};
/** 另一題，沒有官方紙本路徑（紙本叫不出來時要說出來，不是畫一個空窗）。 */
const QUESTION_B = {
  candidate_key: KEY_B,
  question_number: 2,
  stem: '下列何者不是革蘭氏陽性菌？',
  options: [{ key: 'A', text: '金黃色葡萄球菌' }, { key: 'B', text: '大腸桿菌' }],
  answer: 'B',
  // 五個欄位是身分投影讀的（`queue_view.question_identity`）：缺了它，原則區那一列就說不出
  // 「哪一題」，而「去看這一題」也**不會跳**（跳到別的紙本比不跳更糟）。
  metadata: { normalized_category_name: '藥師(一)', normalized_subject_name: '微生物學',
              year: '115', exam_ordinal: '2', paper_id: '115090:311:0704' },
  source_files: {},
};
/** 第三題：有自己的紙本（「類似題」清單選到它時，右欄要真的換成**它**的紙本）。 */
const QUESTION_C = {
  candidate_key: KEY_C,
  question_number: 3,
  stem: '下列何者為細胞壁的主要成分？',
  options: [{ key: 'A', text: '肽聚醣' }, { key: 'B', text: '纖維素' }],
  answer: 'A',
  // 五個欄位是身分投影讀的（`queue_view.question_identity`）：缺了它，原則區那一列就說不出
  // 「哪一題」，而「去看這一題」也**不會跳**（跳到別的紙本比不跳更糟）。
  metadata: { normalized_category_name: '藥師(一)', normalized_subject_name: '微生物學',
              year: '115', exam_ordinal: '2', paper_id: '115090:311:0704' },
  source_files: {},
};

/** 那個把題目放進錯題討論區的重置事件——**機器依紙本改字**的那一種（`applied: 'field'`）。
 *  形狀就是修復管線寫的那一筆：帶 `crop`（模型看過的紙）、`changes`（整欄的兩邊）與
 *  `applied`（這一筆動的是哪一種）。 */
const RESET_EVENT = {
  action: 'reset_review', candidate_key: KEY_A, reviewer: 'repair_dispute_apply',
  repair_kind: 'content_change', applied: 'field',
  crop: 'review-ui/crops/q001-dispute.png',
  changes: [{
    field: 'stem', from: '莢膜的主要成分是？', to: '莢膜的主要成分為何？',
    stored: '莢膜的主要成分是？', page: '莢膜的主要成分為何？',
  }],
  notes: '依紙本改字：題幹照紙本換掉了', created_at: '2026-09-24T08:20:05',
};
/** 另一種退回：**沒有動到字**（管線退回：沒有 repair 前綴的寫入者、註解裡也沒有「修復」字樣，
 *  所以它落在「退回未審」那一桶）。同一格「AI已修改」裡必須有兩種讀法——這一筆不該拿到任何機器
 *  標籤，而上面那一筆必須拿到「依紙本改字」（負向控制）。 */
const PLAIN_RESET = {
  action: 'reset_review', candidate_key: KEY_B, reviewer: 'codex-pipeline',
  notes: '管線退回：字沒有被動過', created_at: '2026-09-24T08:20:30',
};
/** 第三種：**字形替換**（換掉的字不全在部首碼位區）。首頁三個數字裡的第二個。 */
const GLYPH_RESET = {
  action: 'reset_review', candidate_key: KEY_C, reviewer: 'repair_dispute_apply',
  repair_kind: 'content_change', applied: 'substitution',
  changes: [{ field: 'option A', from: 'ћ', to: '①' }], notes: '字形替換',
  created_at: '2026-09-24T08:20:40',
};
/** 一條策展的原則，帶著 evidence（它說得出自己在講哪一題）。 */
const PRINCIPLE = {
  schema: 'qbr_review_principle_v0.1', action: 'add', principle_id: 'p1',
  text: '中文詞中間不該有空格。', scope: 'question', reviewer: 'curated',
  evidence: [KEY_A], source: 'curated', created_at: '2026-09-24T08:21:00',
};
/** 另一條策展的原則，**沒有** evidence：一條沒有出處的原則，紙本叫不出來，這件事要被說出來。 */
const PRINCIPLE_NO_KEY = {
  schema: 'qbr_review_principle_v0.1', action: 'add', principle_id: 'p2',
  text: '選項的標號一律用半形大寫字母。', scope: 'question', reviewer: 'curated',
  created_at: '2026-09-24T08:22:00',
};
/* 反問先寫的是**已經回答**的那一則，投影因此把它排在前面（`reversed(order)`），而原則區要
   把「待回答」排前面——所以畫面上的第一列必須是 rq1，不是投影自己的順序。 */
const ASK_OPEN = {
  schema: 'qbr_repair_question_v0.1', action: 'ask', question_id: 'rq1',
  candidate_key: KEY_A, question: '這一題的 A 選項在紙本是「莢膜多醣體」還是「多醣體」？',
  reason: '模型讀到兩個版本', reviewer: 'repair_agent', created_at: '2026-09-24T09:00:00',
};
const ASK_ANSWERED = {
  schema: 'qbr_repair_question_v0.1', action: 'ask', question_id: 'rq2',
  candidate_key: KEY_B, question: '這一題的選項 B 在紙本上有沒有底線？',
  reason: '紙本截圖不清楚', reviewer: 'repair_agent', created_at: '2026-09-24T09:01:00',
};
const ANSWERED = {
  schema: 'qbr_repair_question_v0.1', action: 'answer', question_id: 'rq2',
  candidate_key: KEY_B, question: '', answer: '沒有底線，紙本只有逗號。',
  reviewer: 'local', created_at: '2026-09-24T09:02:00',
};

/** 模型意見：**模型看到的那張紙、它說哪裡、以及逐欄的兩邊**。
 *
 *  `question_ai_findings.jsonl` 就是錯題討論區每一列的 `qbr_ai_finding` 從哪裡來的那一份 store
 *  （`QbrAiFindingsStore`），原則區的證據卡讀的是**同一份**（`GET /api/findings`）。KEY_A 與
 *  KEY_C 形狀相同（同一個 detector、同一個欄位），所以「類似題」算得出 KEY_C。 */
function findingRecord(key, { number, kinds, fields, what, where }) {
  return {
    schema: 'qbr_ai_finding_v0.1', candidate_key: key, created_at: '2026-09-24T08:10:00',
    model: 'test-vision-model', population: 'dispute', prompt_version: 'pv-test',
    crop: 'review-ui/crops/q001-dispute.png', question_number: String(number),
    paper: '115090:311', subject: '微生物學',
    finding: { verdict: 'DISPUTED', what, where, fix: '照紙本改字' },
    evidence: { stem: '…', answer: 'A',
                disputes: kinds.map((kind) => ({ kind, note: kind, severity: 'review' })) },
    changes: fields.map((field) => ({ field, from: '莢膜的主要成分是？', to: '莢膜的主要成分為何？',
                                      stored: '莢膜的主要成分是？', page: '莢膜的主要成分為何？' })),
  };
}
const FINDING_A = findingRecord(KEY_A, { number: 1, kinds: ['substituted-glyph'],
                                         fields: ['stem'], what: 'STEM_GLYPH', where: '題幹第一個詞' });
const FINDING_C = findingRecord(KEY_C, { number: 3, kinds: ['substituted-glyph'],
                                         fields: ['option A'], what: 'STEM_GLYPH', where: '選項 A 的字' });

/** 一頁的最小 PDF：`/file` 真的送得出來，所以 iframe 指向的紙本是真的存在的一份文件。 */
const MINIMAL_PDF = `%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj
trailer<</Root 1 0 R>>
%%EOF
`;

/** 一張真的 1×1 PNG（`/file` 要送得出來、`<img>` 要載得到——壞連結的截圖不算證據）。 */
const PNG_1PX = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

function makeWorkdir() {
  // 只有兩題的小 candidates 檔，而不是 198 MB 的那一份：這裡驗的是原則區的行為，不是載入速度。
  const dir = process.env.PRINCIPLES_WORKDIR
    ? path.resolve(process.env.PRINCIPLES_WORKDIR)
    : fs.mkdtempSync(path.join(os.tmpdir(), 'principles-'));
  const ui = path.join(dir, 'review-ui');
  fs.mkdirSync(ui, { recursive: true });
  const paper = path.join(dir, 'paper.pdf');
  fs.writeFileSync(paper, MINIMAL_PDF, 'utf-8');
  // **紙本路徑放在 `metadata.question_pdf_relative`**：`candidate_payload` 的 `source_files`
  // 是從它推出來的（伺服器算的，不是 candidates.jsonl 裡寫的那一份），所以種在這裡才對得上
  // 題目區、錯題討論區與原則區讀的同一組欄位。
  QUESTION_A.metadata.question_pdf_relative = paper;
  QUESTION_C.metadata.question_pdf_relative = paper;
  // 答案卷是**另一份**檔案，而且兩題的 metadata 都指得出它：右欄下面那一塊只能是**題目卷**
  // （`question_pdf_relative`），讀錯欄位時下面那條負向控制就會抓到（`answer-sheet.pdf`）。
  const answerSheet = path.join(dir, 'answer-sheet.pdf');
  fs.writeFileSync(answerSheet, MINIMAL_PDF, 'utf-8');
  QUESTION_A.metadata.answer_pdf_relative = answerSheet;
  QUESTION_C.metadata.answer_pdf_relative = answerSheet;
  // 700 題填充題**排在前面**，讓這兩題落在 `/api/candidates` 預設 500 題的窗外：這一頁若用錯的
  // 參數（伺服器只認 `focusKey`），那一筆就不會回來——這一輪的缺陷（2026-09-24 實測）正是如此。
  const filler = [];
  for (let i = 1; i <= 700; i += 1) {
    filler.push({
      candidate_key: `moex:999000:100:0100:1:question:q${String(i).padStart(3, '0')}`,
      question_number: i, stem: `（填充題 ${i}）`, options: [], answer: 'A', metadata: {},
    });
  }
  fs.writeFileSync(path.join(ui, 'candidates.jsonl'),
    filler.concat([QUESTION_A, QUESTION_B, QUESTION_C]).map((q) => JSON.stringify(q)).join('\n') + '\n',
    'utf-8');
  // 範圍樹（`queue_index.json`）。題目區的範圍是靠它解析的（`scopePapers()`），所以「原則區的
  // 「去看這一題」跳回題目區那一題」在沒有這棵樹的佇列上根本無從證明——任何範圍都只會得到
  // 「這個範圍沒有題目」。形狀用**函式庫自己的** `review_queue.taxonomy_of` 產，不抄第二份
  // （那個檔案自己記著一個抄第二份的形狀把 live 佇列畫壞的歷史）。
  const taxonomy = [
    'import json, sys',
    'sys.path.insert(0, sys.argv[1])',
    'from qbr import review_queue',
    'rows = [{"paper": "paper", "category": "藥師(一)", "subject": "微生物學",',
    '         "year": "115", "ordinal": "2", "questions": 3}]',
    'json.dump({"papers": 1, "questions": 3, "taxonomy": review_queue.taxonomy_of(rows)},',
    '          open(sys.argv[2], "w"), ensure_ascii=False)',
  ].join('\n');
  spawnSync('python3', ['-c', taxonomy, path.join(ROOT, 'qbr', 'src'),
                        path.join(ui, 'queue_index.json')]);
  // 兩種退回**同一格**：依紙本改字（KEY_A）、字形替換（KEY_C）、以及沒有動到字的管線退回
  // （KEY_B）。畫面上的三種讀法與首頁的三個數字都要能從這一組種子推出來。
  fs.writeFileSync(path.join(ui, 'question_review_events.jsonl'),
    [RESET_EVENT, PLAIN_RESET, GLYPH_RESET].map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf-8');
  fs.writeFileSync(path.join(ui, 'question_review_principles.jsonl'),
    [PRINCIPLE, PRINCIPLE_NO_KEY].map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf-8');
  fs.writeFileSync(path.join(ui, 'question_repair_questions.jsonl'),
    [ASK_OPEN, ASK_ANSWERED, ANSWERED].map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf-8');
  // 模型意見：錯題討論區的每一列與原則區的證據卡讀的都是**這一份 store**（`QbrAiFindingsStore`）。
  fs.writeFileSync(path.join(ui, 'question_ai_findings.jsonl'),
    [FINDING_A, FINDING_C].map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf-8');
  // 截圖本身：一張真的 PNG，放在一個相對於工作目錄的 `review-ui/crops/`（`/file` 只送得出
  // 許可根裡面的檔案——呼叫端把工作目錄加成 `REVIEW_UI_ADDITIONAL_ASSET_ROOTS`）。
  fs.mkdirSync(path.join(ui, 'crops'), { recursive: true });
  fs.writeFileSync(path.join(ui, 'crops', 'q001-dispute.png'), Buffer.from(PNG_1PX, 'base64'));
  return { dir, ui };
}

async function waitForServer(timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await getJson(`${base}/api/pipeline`);
      return true;
    } catch { await sleep(400); }
  }
  return false;
}

async function main() {
  let server = null;
  let ui = null;
  if (!process.argv[2]) {
    const work = makeWorkdir();
    ui = work.ui;
    server = spawn('python3', [
      path.join(ROOT, 'scripts', 'serve_question_review_ui.py'),
      '--candidate-jsonl', path.join(ui, 'candidates.jsonl'),
      '--review-log', path.join(ui, 'question_review_events.jsonl'),
      '--host', '127.0.0.1', '--port', String(PORT),
    ], {
      cwd: ROOT,
      stdio: 'ignore',
      // `/file` 只送得出許可根裡面的檔案；紙本在 tmp 裡，所以把那個目錄加成一個根。
      env: { ...process.env, REVIEW_UI_ADDITIONAL_ASSET_ROOTS: work.dir },
    });
    server.on('error', (err) => console.error('無法啟動伺服器：', err.message));
  }
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-principles-'));
  let chrome = null;
  try {
    const up = await waitForServer();
    if (!up) {
      console.error('伺服器沒有起來。');
      process.exitCode = 1;
      return;
    }
    // 前提：這一輪的種子真的在（否則後面的斷言沒有意義）。
    const seeded = await getJson(`${base}/api/discuss?limit=1`);
    check((seeded.principles || {}).count === 2, '前提：種了兩條原則（一條帶 evidence、一條沒有）',
      String((seeded.principles || {}).count));
    check((seeded.repair_questions || {}).open_count === 1, '前提：種了一則待答的反問',
      String((seeded.repair_questions || {}).open_count));

    chrome = spawn(CHROME, [
      '--headless=new', '--disable-gpu', `--remote-debugging-port=${debugPort}`,
      `--user-data-dir=${profile}`, '--window-size=1700,1100', 'about:blank',
    ], { stdio: 'ignore' });

    let target;
    for (let i = 0; i < 60 && !target; i += 1) {
      await sleep(250);
      try {
        const list = await getJson(`http://127.0.0.1:${debugPort}/json/list`);
        target = list.find((t) => t.type === 'page');
      } catch { /* 還沒起來 */ }
    }
    if (!target) throw new Error('Chrome 沒有起來');

    const ws = new WebSocket(target.webSocketDebuggerUrl);
    let id = 0;
    const pending = new Map();
    const send = (method, params) => new Promise((resolve) => {
      const mid = ++id;
      pending.set(mid, resolve);
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
    await new Promise((resolve) => { ws.onopen = resolve; });
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
    };
    await send('Page.enable', {});
    await send('Runtime.enable', {});
    const exceptions = [];
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.method === 'Runtime.exceptionThrown') {
        exceptions.push(msg.params.exceptionDetails.text);
      }
    });
    const evaluate = async (expression) => {
      const result = await send('Runtime.evaluate',
        { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'page error');
      return result.result.value;
    };

    await send('Page.navigate', { url: `${base}/v2` });
    await sleep(5000);
    // 從現在起把這一頁打出去的每一個請求記下來（含 POST 的 body），並放一個記號：頁面若被
    // 重新載入，記號會消失——這是「不用重新載入就看到新原則」的量法。
    await evaluate(`(() => {
      window.__calls = [];
      const real = window.fetch;
      window.fetch = (...args) => {
        const init = args[1] || {};
        const entry = { url: String(args[0]), method: (init.method || 'GET').toUpperCase(),
                        body: init.body ? String(init.body) : '', response: null };
        window.__calls.push(entry);
        const done = real(...args);
        // 寫入的**回應**也記下來：這一頁寫完之後的下一份畫面就是從回應裡那份重算過的投影來的，
        // 所以「伺服器回了什麼」是這一段的證據，不能只有送出去的 body。
        if (entry.method !== 'GET') {
          done.then((res) => res.clone().json().then(
            (json) => { entry.response = json; }, () => { /* 不是 JSON 就算了 */ }));
        }
        return done;
      };
      window.__principlesMarker = 42;
      return true;
    })()`);

    // 這一行先講清楚「檔案有沒有被載入」：若伺服器的 `/v2/…` 路由表裡沒有這個檔，這一頁根本不會
    // 有 `renderPrinciples`，後面的每一條都會錯得很莫名其妙。
    const loaded = await evaluate(`({
      fn: typeof renderPrinciples,
      ids: ['principleList', 'principleMain', 'principlePdf']
        .filter((id) => !!document.getElementById(id)),
    })`);
    check(loaded.fn === 'function' && loaded.ids.length === 3,
      '伺服器送出了 /v2/03-area-principles.js（route map 有它），外殼的三個 id 也在',
      `${loaded.fn}／${JSON.stringify(loaded.ids)}`);

    // ---------------------------------------------------------------- 這一區在，而且只有它在
    const nav = await evaluate(`Array.from(document.querySelectorAll('.area-btn')).map((n) => n.dataset.area)`);
    check(nav.includes('principles'), '導覽列有「原則區」的按鈕', JSON.stringify(nav));
    await evaluate(`document.querySelector('.area-btn[data-area="principles"]').click(); true`);
    await sleep(2500);
    const shown = await evaluate(`({
      shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
      active: (document.querySelector('.area-btn.on') || {}).dataset.area,
      hash: decodeURIComponent(location.hash),
      counts: Array.from(document.querySelectorAll('#principleList .discuss-side .row'))
        .map((n) => n.textContent.trim()),
      jumps: document.querySelectorAll('#principleList [data-goto]').length,
      principles: document.querySelectorAll('#principleMain .principles .principle').length,
      questions: document.querySelectorAll('#principleMain .repair-qs .rq').length,
      heads: Array.from(document.querySelectorAll('#principleMain .ph-head')).map((n) => n.textContent.trim()),
    })`);
    check(shown.shown.length === 1 && shown.shown[0] === 'areaPrinciples',
      '按「原則區」真的切到 #areaPrinciples，而且只有它可見', JSON.stringify(shown.shown));
    check(shown.active === 'principles' && /^#原則/.test(shown.hash),
      '而且它在 hash 裡（重整回到同一區）', `${shown.active} / ${shown.hash}`);
    check(shown.jumps === 4, '左欄的跳躍清單涵蓋兩條反問與兩條原則', `${shown.jumps} 列`);

    // 版型：這一頁是**三欄並排**（清單｜內文｜紙本在右邊），不是把三塊疊成一欄。
    // 量的是實際的方框：HTML 的 class 與 CSS 的勾子若對不上（`.area.principles` vs
    // `.principles-area`），`grid-template-columns` 就不會生效，單欄的堆疊在 DOM 斷言裡看不出來。
    const layout = await evaluate(`(() => {
      const box = (id) => {
        const node = document.getElementById(id);
        const r = node.getBoundingClientRect();
        return { x: Math.round(r.left), y: Math.round(r.top),
                 w: Math.round(r.width), h: Math.round(r.height) };
      };
      return { cols: getComputedStyle(document.getElementById('areaPrinciples')).gridTemplateColumns,
               list: box('principleList'), main: box('principleMain'), pdf: box('principlePdf') };
    })()`);
    const stacked = !(Math.abs(layout.main.y - layout.list.y) <= 2
      && Math.abs(layout.pdf.y - layout.list.y) <= 2
      && layout.main.x >= layout.list.x + layout.list.w - 2
      && layout.pdf.x >= layout.main.x + layout.main.w - 2);
    check(!stacked, '三欄並排（清單｜內文｜紙本在右邊），不是疊成一欄',
      `grid ${layout.cols}／list ${layout.list.x},${layout.list.y} ${layout.list.w}×${layout.list.h}`
      + `／main ${layout.main.x},${layout.main.y} ${layout.main.w}×${layout.main.h}`
      + `／pdf ${layout.pdf.x},${layout.pdf.y} ${layout.pdf.w}×${layout.pdf.h}`);
    check(layout.list.w > 0 && layout.list.w === 238,
      '左欄用與錯題討論區同一組欄寬（238px 家族）', `${layout.list.w}px`);
    check(layout.pdf.w >= 300,
      '紙本欄真的是一欄，不是一條縫', `${layout.pdf.w}px`);

    // 鍵盤：這一區沒有自己的快捷鍵，所以 `E`／`S`／`A`／`B` 在這裡只是字母——不可以走去題目區的
    // 清單，也不可以寫出任何判決。
    const keys = await evaluate(`(() => {
      const before = { index: S.index, key: (S.rows[S.index] || {}).candidate_key };
      for (const key of ['e', 's', 'a', 'b', '1']) {
        document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
      }
      return { moved: S.index !== before.index,
               before: String(before.key || ''), after: String((S.rows[S.index] || {}).candidate_key || ''),
               posts: window.__calls.filter((c) => c.method === 'POST').length };
    })()`);
    check(keys.moved === false && keys.before === keys.after && keys.posts === 0,
      '原則區裡按 E／S／A／B 不會走去題目清單，也不會寫入任何判決',
      `${keys.before === keys.after ? '沒動' : `${keys.before} → ${keys.after}`}／POST ${keys.posts} 次`);

    // ---------------------------------------------------------------- 畫的是 /api/discuss 的投影
    const calls = await evaluate(`window.__calls`);
    const discussCalls = calls.filter((c) => c.url.includes('/api/discuss'));
    check(discussCalls.length >= 1, '這一頁真的打了 /api/discuss', JSON.stringify(discussCalls.map((c) => c.url)));
    check(discussCalls.every((c) => c.url.includes('limit=1')),
      '而且用 limit=1（只要那兩個投影，不要把候選題搬過來）', discussCalls.map((c) => c.url).join(' '));
    const api = await evaluate(`(async () => {
      const r = await fetch('/api/discuss?limit=1', { cache: 'no-store' });
      return r.json();
    })()`);
    const wantPrinciples = Number(api.principles.count);
    const wantOpen = Number(api.repair_questions.open_count);
    check(shown.principles === wantPrinciples && shown.questions === api.repair_questions.questions.length,
      '原則與反問的列數等於伺服器投影的數量',
      `畫 ${shown.principles}／${shown.questions}，投影 ${wantPrinciples}／${api.repair_questions.questions.length}`);
    check(shown.counts.some((t) => t.includes('基本原則') && t.includes(`${wantPrinciples} 條`)),
      '左欄印的「基本原則 n 條」就是 principles.count', JSON.stringify(shown.counts));
    check(shown.counts.some((t) => t.includes('反問') && t.includes(`${wantOpen} 題待答`)),
      '左欄印的「反問 n 題待答」就是 repair_questions.open_count', JSON.stringify(shown.counts));
    check(shown.heads.some((t) => t.includes(`${wantOpen} 題待答`)),
      '中欄的抬頭也印同一個 open_count（不自己數）', JSON.stringify(shown.heads));
    check(api.principles.count === api.principles.principles.length
          && api.repair_questions.count === api.repair_questions.questions.length,
      'limit=1 沒有把兩個投影裁掉（count 等於陣列長度）',
      `${api.principles.count}/${api.principles.principles.length} ・ `
      + `${api.repair_questions.count}/${api.repair_questions.questions.length}`);
    const order = await evaluate(`({
      questions: Array.from(document.querySelectorAll('#principleMain .rq')).map((n) => n.dataset.id),
      pendingText: Array.from(document.querySelectorAll('#principleMain .rq .rq-q'))
        .map((n) => n.textContent.trim().slice(0, 4)),
      sideFirst: (document.querySelector('#principleList [data-goto^="pq_"] .num') || {}).textContent,
    })`);
    check(order.questions[0] === 'rq1' && order.sideFirst === '待答',
      '待回答的反問排在最前面（投影自己是把已答的排前面）',
      `${JSON.stringify(order.questions)}／左欄第一列：${order.sideFirst}`);

    // -------------------------------------------- 負向控制：沒有指定題目的原則（先按這個）
    //
    // 一條沒有 evidence 的原則（種子裡的 p2，或人在右欄沒有開題目時打的那一種）紙本叫不出來。
    // 這時候要**說出來**，不是畫一個空窗——而且右欄不該留下任何 iframe。
    const keylessFirst = await evaluate(`(async () => {
      const button = document.querySelector('#principleMain .principle .pl-open[data-key=""]');
      if (!button) return { ok: false, why: '找不到沒有 key 的「叫出原題」' };
      button.click();
      await new Promise((r) => setTimeout(r, 1200));
      return { ok: true,
               empty: (document.querySelector('#principlePdf .empty-area') || {}).textContent.trim(),
               frames: document.querySelectorAll('#principlePdf iframe').length,
               head: document.querySelectorAll('#principlePdf .pdf-head').length };
    })()`);
    check(keylessFirst.ok === true, '沒有指定題目的原則也有一顆「叫出原題」', keylessFirst.why || '');
    check((keylessFirst.empty || '').includes('這條沒有指定題目'),
      '按下去說「這條沒有指定題目」，不是畫一個空窗', keylessFirst.empty);
    check(keylessFirst.frames === 0 && keylessFirst.head === 0,
      '而且那時候沒有 iframe（沒有紙本就不要假裝有）',
      `iframe ${keylessFirst.frames}／head ${keylessFirst.head}`);

    // ---------------------------------------------------------------- 新增原則（右欄沒有開題目）
    const added = await evaluate(`(async () => {
      document.getElementById('dpStart').click();
      await new Promise((r) => setTimeout(r, 400));
      const box = document.getElementById('dpNew');
      if (!box) return { ok: false, why: '沒有新增原則的框' };
      box.value = '選項文字不可以自己加標點。';
      box.dispatchEvent(new Event('input', { bubbles: true }));
      // 正在打一條原則時按到的 S／E：這裡是字母，不是走去題目清單、也不是判決。
      for (const key of ['s', 'e']) {
        box.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
      }
      document.getElementById('dpSave').click();
      await new Promise((r) => setTimeout(r, 2500));
      return { ok: true,
               texts: Array.from(document.querySelectorAll('#principleMain .principle .ptext'))
                 .map((n) => n.textContent.trim()),
               marker: window.__principlesMarker,
               side: (document.querySelector('#principleList .discuss-side .row .n') || {}).textContent,
               calls: window.__calls.filter((c) => c.method === 'POST')
                 .map((c) => ({ url: c.url, body: c.body, response: c.response })) };
    })()`);
    check(added.ok === true, '畫面上找得到「＋ 新增原則」並打得進字', added.why || '');
    const addCall = (added.calls || []).find((c) => c.url.includes('/api/principles'));
    check(!!addCall, '按「加入」真的 POST /api/principles', JSON.stringify((added.calls || []).map((c) => c.url)));
    let addBody = {};
    try { addBody = JSON.parse((addCall || {}).body || '{}'); } catch { addBody = {}; }
    check(addBody.action === 'add' && addBody.text === '選項文字不可以自己加標點。',
      '帶的是 action:add 與那句話', (addCall || {}).body || '');
    check((added.calls || []).length === 1,
      '打字期間按到的 S／E 沒有變成別的寫入（整段只有這一筆 POST）',
      JSON.stringify((added.calls || []).map((c) => c.url)));
    check(!('candidate_key' in addBody),
      '右欄沒有開題目時，不假裝它有一題（不帶 candidate_key）', (addCall || {}).body || '');
    const addResponse = (addCall || {}).response || null;
    check(!!addResponse && addResponse.ok === true && addResponse.count === 3
          && (addResponse.principles || []).some((p) => p.text === '選項文字不可以自己加標點。'),
      '寫入的回應本身就帶著重算過的投影（ok／count 2→3／那一條在裡面）',
      JSON.stringify({ ok: (addResponse || {}).ok, count: (addResponse || {}).count,
        removed: (addResponse || {}).removed, event: (addResponse || {}).event,
        principles: (addResponse || {}).principles }));
    check((added.texts || []).includes('選項文字不可以自己加標點。'),
      '新原則**不重新載入**就出現在畫面上', `${(added.texts || []).length} 條`);
    check(added.marker === 42, '整段過程沒有重新載入頁面', String(added.marker));
    check(String(added.side || '').includes('3 條'),
      '左欄的條數跟著更新（種子 2 ＋ 這一條）', String(added.side));

    // ---------------------------------------------------------------- 叫出原題（有 key）
    //
    // 種子裡的 p1 帶著 `evidence: [KEY_A]`。按它旁邊那顆：右欄要畫出那一題的官方紙本
    // （`/file?path=…#view=FitH`），抬頭要寫出科目／題號與 key。同一題按第二次**不再抓**
    // （記憶體裡有一份 key → row），所以兩次之間 `/api/candidates` 只會多一次呼叫。
    const opened = await evaluate(`(async () => {
      // 前提：這一題不在預設 500 題的窗裡（種子把它排在 700 題填充題後面），所以下面的成功
      // 只可能來自「伺服器把那一筆插回來」。
      const plain = await (await fetch('/api/candidates', { cache: 'no-store' })).json();
      const excluded = !(plain.candidates || []).some((c) => c.candidate_key === '${KEY_A}');
      const before = window.__calls.filter((c) => c.url.includes('/api/candidates')).length;
      const button = document.querySelector('#principleMain .principle .pl-open[data-key="${KEY_A}"]');
      if (!button) return { ok: false, why: '找不到帶 key 的「叫出原題」' };
      button.click();
      await new Promise((r) => setTimeout(r, 2500));
      const frame = document.querySelector('#principlePdf iframe.principle-frame');
      const src = frame ? frame.getAttribute('src') : '';
      let paper = null;
      if (frame) { try { const r = await fetch(src.split('#')[0], { cache: 'no-store' }); paper = r.status; } catch (e) { paper = String(e.message || e); } }
      const mine = window.__calls.filter((c) => c.url.includes('/api/candidates'));
      return { ok: true, src, paper, excluded, url: (mine[mine.length - 1] || {}).url || '',
               fetches: mine.length - before,
               head: (document.querySelector('#principlePdf .pdf-head') || {}).textContent || '',
               empty: (document.querySelector('#principlePdf .empty-area') || {}).textContent || '' };
    })()`);
    check(opened.ok === true, '一條帶 evidence 的原則有「叫出原題」', opened.why || '');
    check(opened.excluded === true,
      '前提：那一題不在預設清單的窗裡（所以「叫得出來」只能靠伺服器把它插回來）',
      String(opened.excluded));
    const src = String(opened.src || '');
    check(src.startsWith('/file?path='),
      '按下去右欄畫出紙本 iframe（src 以 /file?path= 開頭）', src.slice(0, 60));
    check(decodeURIComponent(String(opened.url || '')).includes(`focusKey=${KEY_A}`),
      '查的是伺服器認得的那個參數（focusKey，`candidate_key` 只有 /workflow 認）',
      String(opened.url).slice(0, 70));
    check(src.endsWith('#view=FitH'),
      '而且用 #view=FitH（與題目區、錯題討論區同一種讀法）', src.slice(-24));
    check(opened.paper === 200, '那一份紙本真的送得出來', String(opened.paper));
    check(String(opened.head || '').includes('微生物學') && String(opened.head).includes('第 1 題')
          && String(opened.head).includes(KEY_A),
      '抬頭寫出科目／題號與 key（叫得出原題，也說得出是哪一題）', String(opened.head).trim());
    check(opened.fetches === 1, '叫一題只向 /api/candidates 要一次', `${opened.fetches} 次`);

    // 換題目（key 變了）要把舊的紙本清掉：右欄現在指著 KEY_A，再按一次沒有 key 的那一條。
    const switched = await evaluate(`(async () => {
      const button = document.querySelector('#principleMain .principle .pl-open[data-key=""]');
      button.click();
      await new Promise((r) => setTimeout(r, 1200));
      // 這一刻才讀那句話——下面按回 KEY_A 之後它就換成 iframe 了。
      const gone = document.querySelectorAll('#principlePdf iframe').length === 0;
      const empty = ((document.querySelector('#principlePdf .empty-area') || {}).textContent || '').trim();
      const before = window.__calls.filter((c) => c.url.includes('/api/candidates')).length;
      document.querySelector('#principleMain .principle .pl-open[data-key="${KEY_A}"]').click();
      await new Promise((r) => setTimeout(r, 1500));
      const after = window.__calls.filter((c) => c.url.includes('/api/candidates')).length;
      return { gone, again: !!document.querySelector('#principlePdf iframe.principle-frame'),
               refetches: after - before, empty };
    })()`);
    check(switched.gone === true && (switched.empty || '').includes('這條沒有指定題目'),
      'key 換成「沒有指定題目」時，舊的 iframe 被清掉', switched.empty);
    check(switched.again === true && switched.refetches === 0,
      '再按回同一題時紙本回來了，而且不再向伺服器要一次（key → row 有留著）',
      `再抓 ${switched.refetches} 次`);

    // ---------------------------------------------------------------- 右欄開著題目時新增原則
    const withKey = await evaluate(`(async () => {
      const frame = document.querySelector('#principlePdf iframe.principle-frame');
      frame.dataset.keep = 'me';  // 重畫若把 iframe 換掉，這個記號會不見
      document.getElementById('dpStart').click();
      await new Promise((r) => setTimeout(r, 400));
      const box = document.getElementById('dpNew');
      box.value = '題組的共用題幹不可以重複抽取。';
      box.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('dpSave').click();
      await new Promise((r) => setTimeout(r, 2500));
      const after = document.querySelector('#principlePdf iframe.principle-frame');
      return { open: true,
               frame: !!(after && after.dataset.keep === 'me'),
               texts: Array.from(document.querySelectorAll('#principleMain .principle .ptext'))
                 .map((n) => n.textContent.trim()),
               posts: window.__calls.filter((c) => c.method === 'POST' && c.url.includes('/api/principles'))
                 .map((c) => JSON.parse(c.body || '{}')) };
    })()`);
    check(withKey.open === true, '先把某一題的紙本叫出來（右欄開著題目）', String(withKey.open));
    const keyedAdd = (withKey.posts || [])[withKey.posts.length - 1] || {};
    check(keyedAdd.action === 'add' && keyedAdd.text === '題組的共用題幹不可以重複抽取。',
      '第二條原則也是 action:add', JSON.stringify(keyedAdd));
    check(keyedAdd.candidate_key === KEY_A,
      '而且帶著右欄那一題的 candidate_key（這條原則因此說得出它在講哪一題）',
      String(keyedAdd.candidate_key));
    check((withKey.texts || []).includes('題組的共用題幹不可以重複抽取。'),
      '新原則出現在畫面上', `${(withKey.texts || []).length} 條`);
    check(withKey.frame === true,
      '重畫之後右欄的紙本還在（iframe 沒有被重建，滾動位置不會丢）', String(withKey.frame));

    // ---------------------------------------------------------------- 回答反問
    const answered = await evaluate(`(async () => {
      const box = document.querySelector('#principleMain .rq-a[data-id="rq1"]');
      const button = document.querySelector('#principleMain .rqSave[data-id="rq1"]');
      if (!box || !button) return { ok: false, why: '找不到 rq1 的回答框' };
      box.value = '紙本是「莢膜多醣體」，抽取掉字了。';
      box.dispatchEvent(new Event('input', { bubbles: true }));
      button.click();
      await new Promise((r) => setTimeout(r, 2500));
      const row = document.querySelector('#principleMain .rq[data-id="rq1"]');
      return { ok: true,
               state: (row.querySelector('.rq-q b') || {}).textContent,
               heads: Array.from(document.querySelectorAll('#principleMain .ph-head')).map((n) => n.textContent.trim()),
               side: Array.from(document.querySelectorAll('#principleList .discuss-side .row'))
                 .map((n) => n.textContent.trim()),
               posts: window.__calls.filter((c) => c.method === 'POST' && c.url.includes('/api/repair-question'))
                 .map((c) => JSON.parse(c.body || '{}')),
               responses: window.__calls.filter((c) => c.method === 'POST' && c.url.includes('/api/repair-question'))
                 .map((c) => c.response) };
    })()`);
    check(answered.ok === true, '反問有一顆送出的鍵與一個回答框', answered.why || '');
    const answerPost = (answered.posts || [])[0] || {};
    check(answerPost.action === 'answer' && answerPost.question_id === 'rq1'
          && answerPost.answer === '紙本是「莢膜多醣體」，抽取掉字了。',
      '按「送出回答」真的 POST /api/repair-question（action:answer）', JSON.stringify(answerPost));
    check(answered.state === '已回答', '那一列不再是「待回答」', String(answered.state));
    const answerResponse = (answered.responses || [])[0] || null;
    check(!!answerResponse && answerResponse.ok === true && answerResponse.open_count === 0
          && answerResponse.count === 2,
      '回答的回應也帶著重算過的投影（ok／open_count 1→0／count 2）',
      JSON.stringify({ ok: (answerResponse || {}).ok, count: (answerResponse || {}).count,
        open_count: (answerResponse || {}).open_count, event: (answerResponse || {}).event,
        questions: (answerResponse || {}).questions }));
    check(answered.heads.some((t) => t.includes('0 題待答')),
      '抬頭的待答數跟著變成 0（同一個 open_count）', JSON.stringify(answered.heads));
    check(answered.side.some((t) => t.includes('反問') && t.includes('0 題待答')),
      '左欄也是（不是各自算的）', JSON.stringify(answered.side));

    // ---------------------------------------------------------------- 模型與人各自看到什麼
    //
    // 使用者原文：「你看到認為對，我叫出PDF看不出所以然，我們還缺少了一個『在Jsonl資料庫中我們
    // 兩個到底看到了什麼』」。所以每一條原則（與每一則反問）的卡片上要畫**同一份 finding**：模型
    // 看過的那張紙、它說哪裡、以及逐欄的「抽取存的 → 紙本讀成的」。這裡量的是那張紙**真的載得到**
    // （壞連結的截圖不是證據），而且它與錯題討論區讀的是同一個 store（`/api/findings` 一次問完）。
    const evidence = await evaluate(`(async () => {
      const card = document.querySelector('#principleMain .principle[data-id="p1"]');
      const pe = card ? card.querySelector('.pe') : null;
      const img = pe ? pe.querySelector('img') : null;
      const src = img ? img.getAttribute('src') : '';
      let paper = null;
      if (src) { try { paper = (await fetch(src, { cache: 'no-store' })).status; } catch (e) { paper = String(e.message || e); } }
      return {
        has: !!pe,
        src,
        paper,
        caption: pe ? ((pe.querySelector('figcaption') || {}).textContent || '') : '',
        where: pe ? /模型說哪裡/.test(pe.textContent || '') : false,
        hint: pe ? ((pe.querySelector('.hint') || {}).textContent || '') : '',
        changes: Array.from(pe ? pe.querySelectorAll('.af-change') : []).map((n) => ({
          field: ((n.querySelector('code') || {}).textContent || ''),
          from: ((n.querySelector('.from') || {}).textContent || ''),
          to: ((n.querySelector('.to') || {}).textContent || ''),
        })),
        apply: pe ? pe.querySelectorAll('.af-apply').length : -1,
        keyless: (() => {
          const empty = document.querySelector('#principleMain .principle[data-id="p2"] .pe.none');
          return empty ? empty.textContent.trim() : '';
        })(),
        found: window.__calls.filter((c) => c.url.includes('/api/findings')).map((c) => c.url),
        rqEvidence: document.querySelectorAll('#principleMain .rq .pe').length,
      };
    })()`);
    check(evidence.has === true, '原則卡上有「模型與人各自看到什麼」那一塊', String(evidence.has));
    check(String(evidence.src).startsWith('/file?path=') && evidence.paper === 200,
      '模型看到的那張紙畫得出來，而且真的載得到（200）',
      `${String(evidence.src).slice(0, 40)}… / HTTP ${evidence.paper}`);
    check(/模型看到的紙本截圖/.test(evidence.caption), '而且寫出那是誰看到的紙', evidence.caption);
    check(evidence.where === true, '模型說「哪裡」也畫在同一塊裡');
    check(evidence.changes.length === 1 && evidence.changes[0].field === 'stem'
          && evidence.changes[0].from === '莢膜的主要成分是？'
          && evidence.changes[0].to === '莢膜的主要成分為何？',
      '逐欄畫出**抽取存的 → 紙本讀成的**（整欄的兩邊，不是折疊過的）',
      JSON.stringify(evidence.changes));
    check(evidence.apply === 0,
      '原則卡的證據沒有「帶入」鍵（那一頁沒有編輯框，畫一顆按不動的鍵比不畫更糟）',
      `af-apply ${evidence.apply}`);
    check(evidence.found.length === 1 && evidence.found[0].includes('keys='),
      '整頁的證據**一次**問完（一個 /api/findings，不是每張卡各問一次）',
      JSON.stringify(evidence.found));
    check(evidence.found[0].includes(encodeURIComponent(KEY_A)),
      '而且問的就是這一頁要的 key（原則的 evidence ＋ 反問的 candidate_key）',
      decodeURIComponent(evidence.found[0]).slice(-120));
    check(evidence.rqEvidence >= 1,
      '反問的卡片上也有同一塊（反問是**用 candidate_key** join 到同一份 finding）',
      `rq 的證據卡 ${evidence.rqEvidence} 塊`);
    check(String(evidence.keyless).includes('這條沒有指定題目'),
      '負向控制：沒有 evidence 的原則說出「這條沒有指定題目」，不是畫一個空窗', evidence.keyless);

    // ---------------------------------------------------------------- 核准／取消核准
    //
    // 一條原則進了提示詞，模型就會照著它改題目文字，所以「哪幾條是人點頭過的」必須寫下來
    // （append-only 的一筆事件），而畫面上的狀態必須是**伺服器重算過的投影**——按下去之後
    // 畫面說「已核准」，只可能是因為那筆事件真的寫進去了。
    const approved = await evaluate(`(async () => {
      const state = (id) => {
        const card = document.querySelector('#principleMain .principle[data-id="' + id + '"]');
        return card ? (card.querySelector('.pl-state') || {}).textContent.trim() : null;
      };
      const posts = () => window.__calls.filter((c) => c.method === 'POST' && c.url.includes('/api/principles'));
      const before = { p1: state('p1'), p2: state('p2'), posts: posts().length };
      document.querySelector('#principleMain .principle[data-id="p1"] .pl-approve').click();
      await new Promise((r) => setTimeout(r, 2000));
      const last = posts()[posts().length - 1] || {};
      const after = {
        p1: state('p1'), p2: state('p2'),
        body: JSON.parse(last.body || '{}'), response: last.response,
        head: Array.from(document.querySelectorAll('#principleMain .ph-head')).map((n) => n.textContent.trim()),
        side: Array.from(document.querySelectorAll('#principleList .discuss-side .row')).map((n) => n.textContent.trim()),
        marker: window.__principlesMarker,
        label: (() => { const b = document.querySelector('#principleMain .principle[data-id="p1"] .pl-approve'); return b ? b.textContent.trim() : ''; })(),
      };
      // 取消核准：同一顆按鈕的另一個方向，也是同一條 append-only 流上的一筆新事件。
      document.querySelector('#principleMain .principle[data-id="p1"] .pl-approve').click();
      await new Promise((r) => setTimeout(r, 2000));
      const back = {
        p1: state('p1'),
        actions: posts().slice(-2).map((c) => JSON.parse(c.body || '{}').action),
        total: posts().length - before.posts,
      };
      return { before, after, back };
    })()`);
    check(approved.before.p1 === '待你核准' && approved.before.p2 === '待你核准',
      '前提：兩條原則都還沒有人核准過', `${approved.before.p1}／${approved.before.p2}`);
    const approvePost = approved.after.body || {};
    check(approvePost.action === 'approve' && approvePost.principle_id === 'p1',
      '按「核准」真的 POST /api/principles（action:approve、指名那一條）',
      JSON.stringify(approvePost));
    const approveResponse = approved.after.response || null;
    check(!!approveResponse && approveResponse.ok === true && approveResponse.approved_count === 1
          && (approveResponse.principles || []).some((p) => p.principle_id === 'p1' && p.approved === true),
      '回應帶著重算過的投影（approved_count 0→1，那一條 approved:true）',
      JSON.stringify({ ok: (approveResponse || {}).ok, approved_count: (approveResponse || {}).approved_count,
        pending_count: (approveResponse || {}).pending_count }));
    check(approved.after.p1 === '已核准',
      '畫面上的那一條變成「已核准」——狀態來自伺服器的投影，不是這一頁自己翻的',
      String(approved.after.p1));
    check(approved.after.p2 === '待你核准' && approved.after.label === '取消核准',
      '沒被按到的那一條還是「待你核准」（負向控制），而按鈕換成「取消核准」',
      `${approved.after.p2}／${approved.after.label}`);
    check(approved.after.head.some((t) => t.includes('已核准 1') && t.includes('待你核准 3')),
      '抬頭的「已核准 X／待你核准 Y」跟著變（同一個折疊）', JSON.stringify(approved.after.head));
    check(approved.after.side.some((t) => t.includes('待你核准') && t.includes('3 條')),
      '左欄的「待你核准」也是同一個數字', JSON.stringify(approved.after.side));
    check(approved.after.marker === 42, '整段沒有重新載入頁面', String(approved.after.marker));
    check(approved.back.p1 === '待你核准' && JSON.stringify(approved.back.actions) === '["approve","unapprove"]'
          && approved.back.total === 2,
      '取消核准按得回去，而且是**兩筆**事件（append-only：核准不是改寫 add 那一行）',
      `${approved.back.p1}／${JSON.stringify(approved.back.actions)}／${approved.back.total} 筆`);

    // ---------------------------------------------------------------- 類似題（伺服器算的）
    //
    // 一條原則涵蓋哪些題目：清單由**伺服器**在整份 finding 上比對形狀算出來（瀏覽器掃不了 79k
    // 列），原則自己的 `evidence` 排在最前面。選一題就走既有的 `focusKey` 路徑叫出原題。
    const similar = await evaluate(`(async () => {
      const select = (id) => {
        const card = document.querySelector('#principleMain .principle[data-id="' + id + '"]');
        return card ? card.querySelector('.pl-similar-select') : null;
      };
      const node = select('p1');
      if (!node) return { ok: false, why: 'p1 沒有類似題的清單' };
      const fetches = () => window.__calls.filter((c) => c.url.includes('/api/principles/similar')).length;
      const before = fetches();
      const beforeOptions = Array.from(node.querySelectorAll('option')).map((o) => o.textContent.trim());
      // **點下去才算**：沒有打開過的 dropdown 不該已經打過伺服器。
      node.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 1800));
      const filled = select('p1');
      const options = Array.from(filled.querySelectorAll('option'))
        .map((o) => ({ value: o.value, text: o.textContent.trim() }));
      const holder = filled.closest('.pl-similar');
      const hint = holder ? (holder.querySelector('.pl-similar-hint') || {}).textContent || '' : '';
      const url = (window.__calls.filter((c) => c.url.includes('/api/principles/similar')).slice(-1)[0] || {}).url || '';
      const api = await (await fetch('/api/principles/similar?principle_id=p1&limit=25', { cache: 'no-store' })).json();
      const shape = (api.rows || []).find((r) => r.source === 'shape') || {};
      filled.value = shape.candidate_key || '';
      filled.dispatchEvent(new Event('change', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 2200));
      const frame = document.querySelector('#principlePdf iframe.principle-frame');
      // 負向控制：沒有 evidence 的原則，清單要說出「沒有可以比的形狀」。
      const empty = select('p2');
      empty.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 1200));
      const emptyOptions = Array.from(select('p2').querySelectorAll('option')).map((o) => o.textContent.trim());
      return { ok: true, before, beforeOptions, options, hint, url, api, shape,
               src: frame ? frame.getAttribute('src') : '',
               head: ((document.querySelector('#principlePdf .pdf-head') || {}).textContent || ''),
               emptyOptions };
    })()`);
    check(similar.ok === true, '一條帶 evidence 的原則有「類似題」的清單', similar.why || '');
    check(similar.before === 0 && (similar.beforeOptions || []).length === 1,
      '沒有打開過的清單只有一行提示（**沒有**先打伺服器）',
      `${similar.before} 次請求／${JSON.stringify(similar.beforeOptions)}`);
    check(String(similar.url).includes('principle_id=p1'),
      '點下去才問伺服器，而且問的是這一條原則', String(similar.url).slice(0, 60));
    const apiRows = (similar.api || {}).rows || [];
    check(apiRows.length >= 1 && apiRows[0].source === 'evidence' && apiRows[0].candidate_key === KEY_A,
      '伺服器把原則**自己的 evidence** 排在最前面（那是它說它在講哪一題）',
      JSON.stringify(apiRows.map((r) => [r.candidate_key, r.source])));
    check(apiRows.some((r) => r.candidate_key === KEY_C && r.source === 'shape'),
      '同形狀的題目是被**算**出來的（KEY_C 不在這條原則的 evidence 裡）',
      JSON.stringify(apiRows.map((r) => r.candidate_key)));
    check((similar.options || [])[1] && similar.options[1].value === KEY_A
          && /原則自己指出來的/.test(similar.options[1].text),
      '畫面上的第一列＝原則自己的那一題，而且標了「原則自己指出來的」',
      JSON.stringify((similar.options || []).slice(0, 3)));
    check((similar.options || []).some((o) => o.value === KEY_C && /同形狀/.test(o.text)),
      '清單裡也讀得出「同形狀」的那幾題', JSON.stringify((similar.options || []).map((o) => o.text)));
    const similarHint = String(similar.hint || '');
    const similarMatched = Number((similar.api || {}).matched || 0);
    check(similarHint.includes(`共 ${similarMatched} 題`),
      '旁邊說出總共幾題（上限截掉的數量看得見）', similarHint);
    check(String(similar.src).startsWith('/file?path=') && similar.head.includes(KEY_C),
      '選一題真的把**那一題**的紙本叫到右邊（走同一條 focusKey 路徑）',
      `${String(similar.src).slice(0, 30)}…／${similar.head.slice(0, 40)}`);
    check((similar.emptyOptions || []).length === 1 && /沒有可以比的形狀/.test(similar.emptyOptions[0]),
      '負向控制：沒有 evidence 的原則說出「沒有可以比的形狀」，不是列出一堆無關的題',
      JSON.stringify(similar.emptyOptions));

    // ---------------------------------------------------------------- 右欄切兩塊（反問要看著題目讀）
    //
    // owner（2026-09-24）：「那個原則區的 修理代理的反問 你好歹右邊上面三分之一顯示UI的題目畫面，
    // 右邊下面顯示PDF，不然我真的很難跟你對話」。一則反問問的是「這一題的 A 選項在紙本是…還是…」，
    // 而這一頁以前只畫得出紙本：人得離開原則區、回頭去找那一題，才知道自己被問什麼。
    //
    // 前提：這一刻右欄是**類似題那一段選的 KEY_C**，所以下面量到的每一樣都只可能是「按了反問
    // 旁邊那顆之後」才變成的——它同時是 focusKey 的負向控制。
    const split = await evaluate(`(async () => {
      const box = (sel) => { const n = document.querySelector(sel); return n ? n.getBoundingClientRect() : null; };
      const before = {
        key: ((document.querySelector('#principlePdf .pdf-head .pl-key') || {}).textContent || ''),
        text: ((document.querySelector('#principlePdf .qview') || {}).textContent || ''),
      };
      const calls = window.__calls.filter((c) => c.url.includes('/api/candidates')).length;
      const button = document.querySelector('#principleMain .repair-qs .rq-open[data-key="${KEY_A}"]');
      if (!button) return { ok: false, why: '找不到那則反問的「叫出原題」' };
      button.click();
      await new Promise((r) => setTimeout(r, 2500));
      const pane = document.querySelector('#principlePdf');
      const qview = pane.querySelector('.qview');
      const frame = pane.querySelector('iframe.principle-frame');
      const payload = await (await fetch('/api/candidates?focusKey=${KEY_A}', { cache: 'no-store' })).json();
      const row = (payload.candidates || []).find((c) => c.candidate_key === '${KEY_A}') || {};
      let head = null, paper = null;
      if (frame) {
        const r = await fetch(frame.getAttribute('src').split('#')[0], { cache: 'no-store' });
        head = r.headers.get('content-type'); paper = r.status;
      }
      const top = box('#principlePdf .qview');
      const bottom = box('#principlePdf iframe.principle-frame');
      // 「第一眼」＝沒有捲動的那一屏：題幹與選項都要在裡面。三分之一高很容易被別的東西擠掉，
      // 而擠掉的後果就是 owner 的原話（「不然我真的很難跟你對話」）。
      let firstSight = null;
      if (qview) {
        qview.scrollTop = 0;
        const within = qview.getBoundingClientRect();
        const fits = (node) => {
          if (!node) return false;
          const b = node.getBoundingClientRect();
          return b.bottom <= within.bottom + 1 && b.top >= within.top - 1;
        };
        const opts = Array.from(qview.querySelectorAll('.opt'));
        firstSight = { stem: fits(qview.querySelector('#pq_viewStem')),
                       first: opts.length > 0 && fits(opts[0]),
                       all: opts.length > 0 && fits(opts[opts.length - 1]),
                       height: Math.round(within.height),
                       content: Math.round(qview.scrollHeight),
                       blocks: Array.from(qview.children)
                         .map((n) => [String(n.className), Math.round(n.getBoundingClientRect().height)]) };
      }
      return { ok: true, before, firstSight,
        key: ((pane.querySelector('.pdf-head .pl-key') || {}).textContent || ''),
        src: frame ? frame.getAttribute('src') : '',
        paper, head,
        stem: row.stem || '', count: (row.options || []).length,
        qtext: qview ? qview.textContent : '',
        qstem: qview && qview.querySelector('#pq_viewStem') ? qview.querySelector('#pq_viewStem').textContent : '',
        opts: qview ? qview.querySelectorAll('.opt').length : 0,
        answers: qview ? qview.querySelectorAll('.opt.is-answer').length : 0,
        applyButtons: qview ? qview.querySelectorAll('.af-apply').length : 0,
        findings: qview ? qview.querySelectorAll('.ai-finding').length : 0,
        ratio: (top && bottom) ? top.height / (top.height + bottom.height) : null,
        heights: [top ? Math.round(top.height) : 0, bottom ? Math.round(bottom.height) : 0],
        again: window.__calls.filter((c) => c.url.includes('/api/candidates')).length - calls };
    })()`);
    check(split.ok === true, '那則反問旁邊有「叫出原題」', split.why || '');
    check(split.before.key === KEY_C && split.before.text.includes('細胞壁的主要成分'),
      '前提：按下去之前右欄指的是**另一題**（類似題那一段留下的 KEY_C），上面那塊也畫的是它',
      `${split.before.key}／${String(split.before.text).replace(/\s+/g, ' ').slice(0, 40)}`);
    check(split.key === KEY_A,
      '按反問旁邊那顆，右欄改成**反問問的那一題**（同一個 focusKey 帶著兩塊一起走）', String(split.key));
    check(String(split.stem).length > 0 && String(split.qtext).includes(split.stem),
      '上面那塊是那一題的**題目畫面**（題幹＝`/api/candidates` 那一筆的題幹，不是檔案原文）',
      `${split.stem}`);
    check(String(split.qstem).includes(split.stem),
      '題幹畫在同一個節點上（`#pq_viewStem`：同一支 `questionTextHtml`，不是第二套畫法）',
      String(split.qstem).slice(0, 40));
    check(split.opts === split.count && split.opts > 0 && split.answers === 1,
      '選項與答案也在裡面（跟題目區同一套：正確的那一項上色）',
      `畫出 ${split.opts} 項／payload ${split.count} 項／上色的 ${split.answers} 項`);
    check(split.applyButtons === 0,
      '「帶入修正」**不在**上面那塊（它寫的是題目區的編輯框；畫在別區會是死按鈕）',
      `${split.applyButtons} 顆`);
    check(split.findings === 0,
      '模型意見卡也不在上面那塊（同一頁左欄已經有一張一樣的，而這塊只有三分之一高）',
      `${split.findings} 張`);
    check(split.firstSight && split.firstSight.stem === true && split.firstSight.all === true,
      '第一眼（不捲動）就看得到題幹與**全部**選項（三分之一高的窗格裡，重複的東西都不畫）',
      `題幹 ${split.firstSight && split.firstSight.stem}／第一項 ${split.firstSight && split.firstSight.first}`
      + `／最後一項 ${split.firstSight && split.firstSight.all}`
      + `／可見 ${split.firstSight && split.firstSight.height}px／內容 ${split.firstSight && split.firstSight.content}px/`
      + `${JSON.stringify(split.firstSight && split.firstSight.blocks)}`);
    check(String(split.src).startsWith('/file?path=') && split.paper === 200
          && String(split.head).includes('application/pdf'),
      '下面是官方**題目**紙本（HTTP 200、`application/pdf`）',
      `${String(split.src).slice(0, 40)}…／${split.paper}／${split.head}`);
    check(!decodeURIComponent(String(split.src)).includes('answer-sheet.pdf'),
      '負向控制：不是答案卷（兩題的 metadata 都指得出答案卷，這一格只讀題目卷）',
      String(split.src).slice(0, 50));
    check(split.ratio !== null && Math.abs(split.ratio - 1 / 3) <= 0.08,
      '上面三分之一、下面三分之二（flex 1:2；改成等分時這條會量到 0.5 而失敗）',
      `上 ${split.heights[0]}px／下 ${split.heights[1]}px → ${Number(split.ratio || 0).toFixed(3)}`);
    check(split.again <= 1,
      '這一題最多問伺服器一次（不在記憶體裡時才抓，抓完就留著）',
      `${split.again} 次 /api/candidates`);

    // 第二次、第三次按同一則反問：題目已經在記憶體裡（`P.rows` 一份 key → row），所以**一次都不
    // 該再問**伺服器。owner 要的是「一邊讀反問一邊讀題目」，不是每按一次就打一次端點。
    const repeated = await evaluate(`(async () => {
      const calls = window.__calls.filter((c) => c.url.includes('/api/candidates')).length;
      for (let i = 0; i < 3; i += 1) {
        document.querySelector('#principleMain .repair-qs .rq-open[data-key="${KEY_A}"]').click();
        await new Promise((r) => setTimeout(r, 400));
      }
      const pane = document.querySelector('#principlePdf');
      const qview = pane.querySelector('.qview');
      const frame = pane.querySelector('iframe.principle-frame');
      const top = qview ? qview.getBoundingClientRect().height : 0;
      const bottom = frame ? frame.getBoundingClientRect().height : 0;
      return { calls: window.__calls.filter((c) => c.url.includes('/api/candidates')).length - calls,
               key: ((pane.querySelector('.pdf-head .pl-key') || {}).textContent || ''),
               frames: pane.querySelectorAll('iframe').length,
               ratio: (top && bottom) ? top / (top + bottom) : null };
    })()`);
    check(repeated.calls === 0 && repeated.frames === 1 && repeated.key === KEY_A,
      '同一則反問再按三次，**一次都不再問**伺服器（重畫沒有把兩塊弄掉、也沒有換題）',
      `${repeated.calls} 次 /api/candidates／${repeated.key}／${repeated.frames} 個 iframe`);
    check(repeated.ratio !== null && Math.abs(repeated.ratio - 1 / 3) <= 0.08,
      '重畫之後比例還是 1:2（兩塊不會因為再畫一次而塌掉）',
      Number(repeated.ratio || 0).toFixed(3));

    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('/tmp/v2_principles.png', Buffer.from(shot.data, 'base64'));
    console.log('  截圖：/tmp/v2_principles.png');

    // ---------------------------------------------------------------- 錯題討論區不再畫這些
    await evaluate(`document.querySelector('.area-btn[data-area="discuss"]').click(); true`);
    await sleep(3500);
    const discuss = await evaluate(`({
      shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
      cases: document.querySelectorAll('#discussMain .case').length,
      principles: document.querySelectorAll('#discussMain .principles').length,
      repairQs: document.querySelectorAll('#discussMain .repair-qs').length,
      agentZone: document.querySelectorAll('#discussMain .agent-zone').length,
      side: (document.querySelector('#discussSide') || {}).textContent || '',
      anyOpen: document.querySelectorAll('#discussMain .rq-open, #discussMain .pl-open').length,
      areaPrinciples: document.querySelectorAll('#areaPrinciples .principles').length,
      discussListW: Math.round(document.getElementById('discussList').getBoundingClientRect().width),
    })`);
    check(discuss.shown.length === 1 && discuss.shown[0] === 'areaDiscuss',
      '回到錯題討論區', JSON.stringify(discuss.shown));
    check(discuss.cases >= 1, '錯題討論區還是畫得出那一題', `${discuss.cases} 個個案`);
    check(discuss.principles === 0 && discuss.repairQs === 0 && discuss.agentZone === 0,
      '但錯題討論區**不再**畫原則／反問／代理工作區（同一條原則只有一個地方畫）',
      `principles ${discuss.principles}／repair-qs ${discuss.repairQs}／agent-zone ${discuss.agentZone}`);
    check(!discuss.side.includes('基本原則') && !discuss.side.includes('待回答反問'),
      '它的統計也不再列那兩個數字', discuss.side.replace(/\s+/g, ' ').slice(0, 60));
    check(discuss.areaPrinciples === 1,
      '同一時間只有原則區那一份還在（搬移，不是複製）', `#areaPrinciples .principles ${discuss.areaPrinciples}`);
    check(discuss.discussListW === layout.list.w,
      '兩區的左欄寬度相同（同一組欄寬算術，紙本才會一樣大）',
      `錯題 ${discuss.discussListW}px／原則 ${layout.list.w}px`);
    // ---------------------------------------------------------------- 機器改過字的標籤（三個地方）
    //
    // 使用者原文：「判讀 → 文字 跟 文字 →抽取檔要打通，並且改標籤送到「AI已解決」，我才能知道
    // 有沒有改過」。所以同一格裡要有**三種讀法**：依紙本改字、字形替換、以及沒有動到
    // 字的純退回。三個地方各看一次：題目區的清單列（`.who`）、錯題討論區的列（`.kind`）、首頁的三
    // 個數字（`.mach`）。它們讀的都是伺服器算好的 `review.applied_kind`。
    const kinds = await evaluate(`(() => {
      const rows = Array.from(document.querySelectorAll('#discussList .row'))
        .map((n) => ({
          num: ((n.querySelector('.num') || {}).textContent || '').trim(),
          kind: ((n.querySelector('.kind') || {}).textContent || '').trim(),
        })).filter((r) => r.num !== '');
      const meta = ((document.querySelector('#discussMain .meta') || {}).textContent || '').trim();
      const finding = document.querySelector('#discussMain .ai-finding');
      return { rows, meta,
               crop: !!document.querySelector('#discussMain .ai-finding .af-crop img'),
               changes: Array.from(document.querySelectorAll('#discussMain .ai-finding .af-change'))
                 .map((n) => ({ field: ((n.querySelector('code') || {}).textContent || ''),
                                from: ((n.querySelector('.from') || {}).textContent || ''),
                                to: ((n.querySelector('.to') || {}).textContent || ''),
                                apply: !!n.querySelector('.af-apply') })),
               hasFinding: !!finding };
    })()`);
    const kindByNumber = Object.fromEntries(kinds.rows.map((r) => [r.num, r.kind]));
    check(kinds.rows.length === 3, '三筆退回都在錯題討論區裡', JSON.stringify(kinds.rows));
    check(kindByNumber['1'] === '依紙本改字',
      '機器依紙本改過字的那一列，標籤就是「依紙本改字」', JSON.stringify(kinds.rows));
    check(kindByNumber['3'] === '字形替換',
      '字形替換的那一列也說得出自己', JSON.stringify(kinds.rows));
    check(kindByNumber['2'] === '退回未審' && kindByNumber['2'] !== kindByNumber['1'],
      '負向控制：沒有動到字的退回維持「退回未審」，而且與上面那一列讀起來不同',
      JSON.stringify(kindByNumber));
    check(/依紙本改字/.test(kinds.meta) && /機器改過字/.test(kinds.meta),
      '「為什麼在這裡」那句話指名動的是哪一種（要複核的方式三種不同）', kinds.meta.slice(0, 80));
    check(kinds.hasFinding === true && kinds.crop === true,
      '同一列上，模型看過的那張紙也畫得出來（沒看過的證據不算證據）', String(kinds.crop));
    check(kinds.changes.length === 1 && kinds.changes[0].to === '莢膜的主要成分為何？'
          && kinds.changes[0].apply === true,
      '逐欄的「抽取存的 → 紙本讀成的」也畫在討論區，而且留著「帶入」（修還是人的動作）',
      JSON.stringify(kinds.changes));

    // 題目區的清單列：同一份投影、同一組字。
    //
    // 這一頁的清單畫的是**目前的範圍**，而種子那三題不在預設 500 題的窗裡、也沒有範圍樹可以選到
    // 它們。所以這裡把討論區已經拿回來的**真實列**（伺服器的投影，就是題目區讀的同一份）放進清單
    // 畫一次：畫的是真的 `renderList`／`renderTextSide`，不是另寫一段假資料去證明自己。
    const questionLabels = await evaluate(`(() => {
      const draw = (key) => {
        const row = D.byKey.get(key);
        if (!row) return { who: null, hint: null, why: '討論區沒有這一列' };
        S.verdict = new Map([[key, 'reset_review']]);
        S.rows = [{ candidate_key: key, question_number: row.question_number,
                    stem_preview: row.stem, candidate: row }];
        S.index = 0;
        renderList();
        renderTextSide();
        return { who: ((document.querySelector('#listBody .row .who') || {}).textContent || '').trim(),
                 hint: ((document.getElementById('stateHint') || {}).textContent || '').trim(),
                 crop: !!document.querySelector('#textSide .ai-finding .af-crop img'),
                 changes: Array.from(document.querySelectorAll('#textSide .af-change'))
                   .map((n) => ({ to: ((n.querySelector('.af-to') || {}).textContent || '').trim(),
                                  apply: !!n.querySelector('.af-apply') })),
                 rows: document.querySelectorAll('#listBody .row').length };
      };
      return { changed: draw('${KEY_A}'), plain: draw('${KEY_B}'), normalised: draw('${KEY_C}') };
    })()`);
    if (!questionLabels.changed || questionLabels.changed.who === null) {
      console.log('    （題目區診斷）', JSON.stringify(questionLabels));
    }
    check(questionLabels.changed.rows === 1 && questionLabels.changed.who === '依紙本改字',
      '題目區的清單列上寫出是哪一種機器動作（讀的是同一份 `review.applied_kind`）',
      JSON.stringify(questionLabels.changed));
    check(questionLabels.normalised.who === '字形替換',
      '字形替換那一列在題目區也一樣讀得出來', JSON.stringify(questionLabels.normalised));
    check(questionLabels.plain.who === '',
      '負向控制：沒有動到字的那一列**一個字都不標**（標籤不是每一列都有的裝飾）',
      JSON.stringify(questionLabels.plain));
    check(/AI已修改/.test(questionLabels.changed.hint)
          && /依紙本改字/.test(questionLabels.changed.hint),
      '右欄的狀態句同時說出「誰退回」與「動的是哪一種」', questionLabels.changed.hint.slice(0, 80));
    check(/AI已修改/.test(questionLabels.plain.hint)
          && !/依紙本改字/.test(questionLabels.plain.hint),
      '負向控制：純退回的狀態句維持原本那一句，沒有多出機器標籤',
      questionLabels.plain.hint.slice(0, 80));
    // 題目區的文字面（`#textSide`）現在是**共用畫法**的輸出：模型看過的截圖、逐字差異、「帶入修正」
    // 按鈕一樣都不能少。這一組斷言與 `scripts/test_v2_dispute_browser.mjs` 量的是同一個東西，只是
    // 那支要靠外部伺服器，這裡自己帶種子。
    check(questionLabels.changed.crop === true && questionLabels.changed.changes.length === 1
          && questionLabels.changed.changes[0].to === '莢膜的主要成分為何？'
          && questionLabels.changed.changes[0].apply === true,
      '題目區的文字面照舊畫出截圖、逐字差異與「帶入修正」（畫法抽成共用函式沒有少東西）',
      JSON.stringify(questionLabels.changed.changes));
    check(questionLabels.plain.changes.length === 0 && questionLabels.plain.crop === false,
      '負向控制：沒有 finding 的那一題一個差異列都沒有（這條不是恆真）',
      JSON.stringify(questionLabels.plain.changes));

    // 「帶入修正」按得下去，而且只填編輯框、不送任何請求（`GOV-05`：修還是人的動作）。
    const applied = await evaluate(`(async () => {
      // 上面最後畫的是 KEY_C，所以這裡先畫回 KEY_A（有 finding、有差異列的那一題）。
      const row = D.byKey.get('${KEY_A}');
      S.verdict = new Map([['${KEY_A}', 'reset_review']]);
      S.rows = [{ candidate_key: '${KEY_A}', question_number: row.question_number,
                  stem_preview: row.stem, candidate: row }];
      S.index = 0;
      renderList();
      renderTextSide();
      const calls = window.__calls.length;
      const button = document.querySelector('#textSide .af-apply');
      if (!button) return { ok: false, why: '文字面上沒有「帶入修正」' };
      button.click();
      await new Promise((r) => setTimeout(r, 600));
      const node = document.getElementById('editStem');
      return { ok: true, value: node ? node.value : null,
               editing: S.editing,
               calls: window.__calls.length - calls };
    })()`);
    check(applied.ok === true && applied.value === '莢膜的主要成分為何？' && applied.editing === true,
      '按下去把紙本讀法帶進編輯框（並打開編輯模式）', JSON.stringify(applied));
    check(applied.calls === 0,
      '而且**沒有**送任何請求：寫下決定的還是按下「儲存修正」的人（GOV-05）',
      `${applied.calls} 個請求`);

    // 首頁：三個數字各是一種機器動作（在同一個「錯題討論區」卡上，卡片仍然是四張）。
    await evaluate(`document.querySelector('.area-btn[data-area="home"]').click(); true`);
    await sleep(2500);
    const home = await evaluate(`(() => {
      const cards = Array.from(document.querySelectorAll('#homeCards .card'));
      const discussCard = cards.find((c) => c.dataset.go === 'discuss');
      const mach = discussCard ? discussCard.querySelector('.mach') : null;
      return { count: cards.length,
               go: cards.map((c) => c.dataset.go),
               pills: mach ? Array.from(mach.querySelectorAll('b')).map((n) => n.textContent.trim()) : [],
               note: mach ? ((mach.querySelector('i') || {}).textContent || '').trim() : '',
               big: discussCard ? ((discussCard.querySelector('.big') || {}).textContent || '').trim() : '',
               calls: window.__calls.filter((c) => c.url.includes('/api/machine-activity')).map((c) => c.url) };
    })()`);
    check(home.count === 4 && JSON.stringify(home.go) === JSON.stringify(['question', 'answer', 'discuss', 'principles']),
      '首頁仍然是四張卡（三個數字在既有的卡片裡，不是第五張）',
      `${home.count}／${JSON.stringify(home.go)}`);
    // 三種**要讀的**修復（第四格「已還原」是另一種東西：那不是還沒讀的題，是機器收回自己改錯的字，
    // 它有自己的計數，但不屬於這一組）——所以這裡驗的是那三個數字在、而且與種子相符。
    const wantPills = ['依紙本改字 1', '字形替換 1', '正規化（部首碼位） 0'];
    check(wantPills.every((pill) => home.pills.includes(pill)),
      '三個數字各是一種機器動作，而且與這一組種子相符（依紙本 1／字形 1／正規化 0）',
      JSON.stringify(home.pills));
    check(/退回 2 題/.test(home.note),
      '分母也寫出來（退回幾題、其中幾題動過字）', home.note);
    check(home.calls.length >= 1, '那三個數字是伺服器算的（這一頁打了 /api/machine-activity）',
      JSON.stringify(home.calls));
    check(home.big !== '' && home.big !== '—', '卡上的大數字照舊還在', home.big);

    check(exceptions.length === 0, '整段過程沒有 console 例外（含重開那一頁）', exceptions.join(' / '));

    // ---------------------------------------------------------------- 開檔時 hash 就帶著 `#原則`
    //
    // 這一區的入口不只是頂端的按鈕：書籤與重整都會在**開檔時**就帶著 `#原則`（`boot()` 之後才
    // 決定去哪一區，而 `boot()` 自己會把 hash 寫成題目區的範圍）。這一條量的是那條路：不點任何
    // 按鈕，畫面要跟點按鈕時一樣——而且兩個數字要跟**當下**的投影一樣（剛才回答過一則反問，
    // 所以這裡是 0 題待答；讀到舊的就露餡了）。
    await send('Page.navigate', { url: `${base}/v2#%E5%8E%9F%E5%89%87` });
    await sleep(6000);
    const cold = await evaluate(`(async () => {
      const api = await (await fetch('/api/discuss?limit=1', { cache: 'no-store' })).json();
      const area = document.getElementById('areaPrinciples');
      const list = document.getElementById('principleList');
      return {
        onArea: !!(area && area.classList.contains('on')),
        shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
        head: decodeURIComponent(location.hash).split('/')[0],
        principles: document.querySelectorAll('#principleMain .principles .principle').length,
        questions: document.querySelectorAll('#principleMain .repair-qs .rq').length,
        jumps: document.querySelectorAll('#principleList [data-goto]').length,
        counts: Array.from(document.querySelectorAll('#principleList .discuss-side .row')).map((n) => n.textContent.trim()),
        heads: Array.from(document.querySelectorAll('#principleMain .ph-head')).map((n) => n.textContent.trim()),
        listText: (list ? list.textContent : '').replace(/\\s+/g, ' ').trim(),
        wantPrinciples: Number(api.principles.count),
        wantQuestions: (api.repair_questions.questions || []).length,
        wantOpen: Number(api.repair_questions.open_count),
      };
    })()`);
    check(cold.shown.length === 1 && cold.shown[0] === 'areaPrinciples' && cold.onArea === true,
      '開檔時 hash 已經帶著 `#原則` 就直接進原則區（書籤／重整不必先點按鈕）', JSON.stringify(cold.shown));
    check(cold.head === '#原則', '而且 boot 的範圍寫入沒有把那個模式蓋掉', cold.head);
    check(!cold.listText.includes('載入中'),
      '沒有停在「載入中…」（畫不出來要說出來，安靜地停在那裡與當掉沒有兩樣）',
      cold.listText.slice(0, 60));
    check(cold.principles === cold.wantPrinciples && cold.questions === cold.wantQuestions
          && cold.jumps === cold.wantPrinciples + cold.wantQuestions,
      '開檔畫出來的列數與點按鈕時同一份投影（同一條路，不是兩套）',
      `畫 ${cold.principles}／${cold.questions}／跳躍 ${cold.jumps}，投影 ${cold.wantPrinciples}／${cold.wantQuestions}`);
    check(cold.counts.some((t) => t.includes(`${cold.wantPrinciples} 條`))
          && cold.counts.some((t) => t.includes(`${cold.wantOpen} 題待答`)),
      '而且那兩個數字是當下的投影（剛才已經回答過一則反問）',
      `${JSON.stringify(cold.counts)}／open_count ${cold.wantOpen}`);

    // ---------------------------------------------------------------- 去看這一題
    //
    // 使用者原文（2026-09-25）：「我找不到『等你對那 72 題「紙本與抽取一致但仍阻擋」給一句原則』
    // 的那些題目」。原則區原本只有「叫出原題」——那條路把紙本叫到**右邊的窗格**，人還是停在原則區，
    // 看不到那一題的判讀狀態，也不能在那裡判它。這一顆按鈕要把他送回審題的地方，開在那一題上。
    //
    // 量的是**可觀察的落地**：題目區成為唯一可見的區、`#where` 與 `#viewStem` 是那一題的內容；
    // 並配一個負向控制——「去看」不是寫入，整段過程不可以有任何 POST，也不可以重新載入頁面
    // （否則「跳過去」與「重新整理到首頁」就分不出來了）。
    const jumped = await evaluate(`(() => {
      window.__jump = { posts: [], reloaded: false };
      const real = window.fetch;
      window.fetch = (...args) => {
        const url = String(args[0] && args[0].url ? args[0].url : args[0]);
        const init = args[1] || {};
        const method = String(init.method || (args[0] && args[0].method) || 'GET').toUpperCase();
        if (method === 'POST') window.__jump.posts.push(url);
        return real(...args);
      };
      const button = document.querySelector('#principleMain .rq-goto[data-key="${KEY_A}"]');
      if (!button) return { clicked: false };
      const info = { clicked: true, key: button.dataset.key,
                     text: (button.textContent || '').trim() };
      button.click();
      return info;
    })()`);
    check(jumped.clicked === true && jumped.key === KEY_A,
      '反問那一列有「去看這一題」，而且帶著那一題的 key', JSON.stringify(jumped));
    await sleep(3000);
    const landed = await evaluate(`(() => ({
      shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
      principlesOn: document.querySelector('#areaPrinciples').classList.contains('on'),
      where: (document.querySelector('#where') || {}).textContent || '',
      stem: (document.querySelector('#viewStem') || {}).textContent || '',
      hash: decodeURIComponent(location.hash),
      marker: typeof window.__jump === 'object',
      posts: (window.__jump || {}).posts || [],
      crumbs: (document.querySelector('#crumbs') || {}).textContent || '',
      scopeCount: (document.querySelector('#scopeCount') || {}).textContent || '',
    }))()`);
    check(landed.shown.length === 1 && landed.shown[0] === 'areaQuestion' && landed.principlesOn === false,
      '按下去切到題目審核區，原則區收起來', JSON.stringify({ shown: landed.shown, on: landed.principlesOn }));
    check(landed.where.includes('第 1 題') && landed.where.includes('微生物學')
          && landed.stem.includes('莢膜'),
      '而且開在**那一題**上（不是同一個範圍的第一題，也不是別的紙本）',
      `${landed.where} ／ ${landed.stem.slice(0, 20)}`);
    check(landed.crumbs.includes('藥師(一)') && landed.scopeCount.includes('1 卷'),
      '範圍真的載入了那一卷（`#scopeCount` 說得出卷與題數）',
      `${landed.crumbs} ／ ${landed.scopeCount}`);
    check(/微生物學/.test(landed.hash), '網址跟著那一題的範圍（重整回到同一個範圍）', landed.hash);
    check(landed.marker === true, '整段沒有重新載入頁面（是在頁內換區）', String(landed.marker));
    check((landed.posts || []).length === 0, '負向控制：去看不等於寫入（過程中沒有任何 POST）',
      JSON.stringify(landed.posts));

    ws.close();
    if (ui) {
      // 這一輪真的寫進了那一組隔離的流：證明寫入走的是 append-only 的端點，不是只有畫面在動。
      const principles = fs.readFileSync(path.join(ui, 'question_review_principles.jsonl'), 'utf-8')
        .split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
      const questions = fs.readFileSync(path.join(ui, 'question_repair_questions.jsonl'), 'utf-8')
        .split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
      check(principles.length === 6 && principles[0].action === 'add',
        '兩條新原則真的 append 進 question_review_principles.jsonl（原本的兩條沒有被改寫）',
        `${principles.length} 筆`);
      check(principles[4].action === 'approve' && principles[4].principle_id === 'p1'
            && principles[5].action === 'unapprove' && principles[5].principle_id === 'p1'
            && principles[4].created_at,
        '核准與取消核准也是**新的一行**（append-only：核准不是改寫 add 那一行，時間由寫入端蓋）',
        `${JSON.stringify(principles[4])} / ${JSON.stringify(principles[5])}`);
      check(principles[0].action === 'add' && principles[0].approved === undefined
            && principles[0].approved_by === undefined,
        '原本那一條 add 沒有任何核准欄位被寫回去（核准是折疊出來的，不是改寫歷史）',
        JSON.stringify(principles[0]).slice(0, 120));
      check(!principles[2].evidence,
        '右欄沒有開題目時加的那一條**沒有** evidence（不假裝它有一題）',
        JSON.stringify(principles[2].evidence || null));
      check(principles[3].evidence && principles[3].evidence[0] === KEY_A,
        '帶 candidate_key 的那一條存成 evidence（與策展那條路同一個欄位）',
        JSON.stringify(principles[3].evidence));
      check(questions.length === 4 && questions[3].action === 'answer',
        '回答真的 append 進 question_repair_questions.jsonl',
        `${questions.length} 筆`);
    }
    ws.close();
  } finally {
    if (chrome) chrome.kill();
    if (server) server.kill();
    await sleep(400);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome 還在寫 profile */ }
  }

  console.log(failures ? `\n${failures} 項不符` : '\n全部符合');
  process.exitCode = failures ? 1 : 0;
}

main().catch((error) => { console.error(error); process.exitCode = 2; });
