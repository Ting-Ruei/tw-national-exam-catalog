#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明五個區真的在，而且每一區做自己那件事。
 *
 * 使用者要的是 首頁／題目審核區／答案審核區／錯題討論區／原則區。這個檔案把每一區的
 * **可觀察行為**各釘一次——不是「按鈕存在」，而是「按下去畫面真的變成那一區、那一區真的
 * 讀到自己的資料、寫下去的東西真的進了 append-only 事件檔」：
 *
 *   首頁        四張卡各指向一個審核區，數字來自既有的伺服器端點，不是自己算的。
 *   題目審核區  基準線，開頁預設就在這裡（空 hash 不是首頁）。
 *   答案審核區  右欄是那一張卡的**答案卷 PDF**（不是題目卷）；每題的「阻擋」是一個開關
 *               （按了變紅、再按取消）；A-D 是同一組字母的四種寫法（單選／任一／任一+複選／
 *               複選），按字母只改草稿、「儲存答案修正」才送出；一題一個請求。
 *   錯題討論區  只放卡住的題（人阻擋或管線／AI 退回），不是整條佇列。
 *   原則區      自成一頁（基本原則與反問的介面），由 `scripts/test_v2_principles_browser.mjs`
 *               釘住；這裡只驗它是一個獨立的區。
 *
 * 還釘兩個容易被順手弄壞的契約：**換區不能清空另一區的 DOM**（`hidden` 是對的，
 * `innerHTML = ''` 是錯的），以及**同一份文件不重建 iframe**（重指 `src` 會把審題者捲到的
 * 位置丟掉）。字串標籤不是契約，`data-*` 與伺服器 payload 的數才是。
 *
 * **這一條測試自己起一個伺服器**：`scripts/serve_question_review_ui.py` ＋ 一組自己寫的
 * candidates 與事件檔（`makeWorkdir`）。因為答案審核區的驗收要**真的寫入**（每題阻擋、
 * 儲存答案修正、退回未審），而寫在 live 的佇列上就會在審題者的佇列裡留下沒有人按過的事件
 * ——它同時也會把 live 的事件檔弄髒，那是 append-only 的檔案。跑完（含失敗）都會收掉子行程。
 *
 * 用法：
 *   node scripts/test_v2_areas_browser.mjs
 *     自己起 scratch 伺服器，跑全部檢查（預設）。
 *   node scripts/test_v2_areas_browser.mjs http://127.0.0.1:8897
 *     改用外部伺服器，只跑唯讀檢查；寫入檢查需要自己的候選題與事件檔，會明講跳過。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const ROOT = path.resolve(decodeURIComponent(new URL('.', import.meta.url).pathname), '..');
const EXTERNAL_BASE = process.argv[2] || '';
const PORT = Number(process.env.AREAS_PORT || 8908);
const DEBUG_PORT = Number(process.env.AREAS_DEBUG_PORT || 9338);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failures = 0;
function check(ok, label, detail) {
  console.log(`  ${ok ? 'ok ' : 'BAD'}  ${label}${detail ? `  ${detail}` : ''}`);
  if (!ok) failures += 1;
}
function skip(label, why) {
  console.log(`  --  ${label}（跳過：${why}）`);
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

/* ------------------------------------------------------------------ 這一條測試自己的佇列

   一份小 candidates ＋ 兩張答案卡。它驗的是「答案審核區怎麼讀、怎麼寫」，不是真實語料的量，
   所以資料只要**每一種形狀各一題**：單選、多選、`#`（MOD 未決定）、已記錄的人工修正、
   題號缺一題（缺題保留）、以及一題被阻擋（它屬於錯題討論區，不屬於答案卡）。

   紙本與答案卷是**兩個不同的檔**（`question_pdf_relative` vs `answer_pdf_primary_relative`），
   這樣「右欄顯示的是答案卷」才驗得出來——舊版拿題目卷當對照的錯誤會直接被這一條抓住。 */
const SUBJECT_A = '測試科目A';
const SUBJECT_B = '測試科目B';
const PAPER_A = '1151_測試科目A';
const PAPER_B = '1151_測試科目B';

function candidateMeta(subject, subjectCode, role, paper) {
  return {
    normalized_category_name: '測試類科',
    normalized_subject_name: subject,
    year: '115', exam_ordinal: '1',
    exam_code: '115010', category_code: 'C999', subject_code: subjectCode,
    // 題目卷的檔名就是導覽樹裡的卷名：`paperOf()`＝題目 PDF 去副檔名的 basename，樹裡的
    // `papers: [...]` 必須用同一個字串，否則這一卷的列會被 `loadScopeRows` 全部濾掉。
    question_pdf_relative: `papers/${paper}.pdf`,
    // A 卷走 `answer_pdf_primary_relative`：伺服器會把它填進答案卡的 `source_files.official_pdf`。
    // B 卷刻意**只**有 `answer_pdf_relative`——那是線上佇列的形狀（實測線上 113 張答案卡的
    // `source_files` 四欄全是 null，路徑只在 metadata 裡），用來釘住答案區讀 metadata 的退路。
    ...(paper === PAPER_B
      ? { answer_pdf_relative: `papers/${paper}_答案卷.pdf` }
      : { answer_pdf_primary_relative: `papers/${paper}_答案卷.pdf` }),
    answer_role_primary: role,
  };
}

function candidate(subject, subjectCode, role, paper, number, answer, extra = {}) {
  const key = `t:115010:${subjectCode}:${role}:q${String(number).padStart(3, '0')}`;
  return {
    candidate_key: key,
    question_number: number,
    stem: `測試題 ${number}${extra.stemNote ? `（${extra.stemNote}）` : ''}`,
    options: (extra.options || ['A', 'B', 'C', 'D']).map((optionKey) => ({
      key: optionKey,
      text: `選項 ${optionKey} 的文字`,
      ...(extra.optionImage && optionKey === 'A'
        ? { image: { exists: true, path: `crops/${subjectCode}-A.png` } } : {}),
    })),
    answer,
    ...(extra.answerImage ? { answer_image_refs: [{ exists: true, path: `crops/${subjectCode}-ans.png`, caption: '答案補圖' }] } : {}),
    metadata: candidateMeta(subject, subjectCode, role, paper),
    answer_payload: { answer_source_registry_key: `t:115010:${subjectCode}`, raw_answer: answer },
  };
}

/** 這一題的人工修正（答案關卡）——「已經有人改過的那一題」。 */
const RECORDED_CORRECTION = {
  action: 'correct',
  reviewer: 'local',
  corrected_answer: 'A|C',
  created_at: '2026-09-24T10:05:00',
};

function makeWorkdir() {
  const dir = process.env.AREAS_WORKDIR
    ? path.resolve(process.env.AREAS_WORKDIR)
    : fs.mkdtempSync(path.join(os.tmpdir(), 'v2-areas-'));
  const ui = path.join(dir, 'review-ui');
  fs.mkdirSync(ui, { recursive: true });

  const rows = [
    candidate(SUBJECT_A, '901', 'answer', PAPER_A, 1, 'B', { answerImage: true }),
    // 這一題已經有人改過：修正字串就存在 `answer_review.correction`（伺服器存的是**字串**）。
    candidate(SUBJECT_A, '901', 'answer', PAPER_A, 2, 'A'),
    // 題號 3 故意不存在 → 那一列是「缺題保留」，不是別人的答案。
    candidate(SUBJECT_A, '901', 'answer', PAPER_A, 4, 'D'),
    // 題目被阻擋 → 它屬於錯題討論區；答案卡不該收它。
    candidate(SUBJECT_A, '901', 'answer', PAPER_A, 5, 'C', { stemNote: '這一題被阻擋' }),
    candidate(SUBJECT_B, '902', 'correction', PAPER_B, 1, 'A|C', { stemNote: 'MOD 多答案' }),
    candidate(SUBJECT_B, '902', 'correction', PAPER_B, 2, '#', { stemNote: 'MOD 未決定' }),
    candidate(SUBJECT_B, '902', 'correction', PAPER_B, 3, 'B'),
  ];
  fs.writeFileSync(path.join(dir, 'candidates.jsonl'),
    rows.map((row) => JSON.stringify(row)).join('\n') + '\n', 'utf-8');

  const accepted = rows.filter((row) => !row.stem.includes('被阻擋'));
  const questionEvents = accepted.map((row) => ({
    action: 'accept', candidate_key: row.candidate_key, reviewer: 'local',
    created_at: '2026-09-24T10:00:00',
  }));
  const blocked = rows.find((row) => row.stem.includes('被阻擋'));
  questionEvents.push({
    action: 'block', candidate_key: blocked.candidate_key, reviewer: 'local',
    notes: '這一題的題幹壞掉，退回修', created_at: '2026-09-24T10:01:00',
  });
  fs.writeFileSync(path.join(ui, 'question_review_events.jsonl'),
    questionEvents.map((row) => JSON.stringify(row)).join('\n') + '\n', 'utf-8');

  const corrected = rows.find((row) => row.question_number === 2 && row.metadata.subject_code === '901');
  fs.writeFileSync(path.join(ui, 'answer_review_events.jsonl'),
    JSON.stringify({ ...RECORDED_CORRECTION, candidate_key: corrected.candidate_key }) + '\n', 'utf-8');

  // 導覽樹是 `queue_index.json`（伺服器端點讀它，不在瀏覽器算）。
  const taxonomy = {
    測試類科: {
      years: {
        115: {
          sittings: {
            1: {
              subjects: {
                [SUBJECT_A]: { papers: [PAPER_A], questions: 4 },
                [SUBJECT_B]: { papers: [PAPER_B], questions: 3 },
              },
              papers: 2, questions: 7,
            },
          },
          papers: 2, questions: 7,
        },
      },
      papers: 2, questions: 7,
    },
  };
  fs.writeFileSync(path.join(dir, 'queue_index.json'), JSON.stringify({ taxonomy }), 'utf-8');
  return { dir, ui, correctedKey: corrected.candidate_key, blockedKey: blocked.candidate_key };
}

async function waitForServer(base, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { await getJson(`${base}/api/pipeline`); return true; } catch { await sleep(400); }
  }
  return false;
}

/* ------------------------------------------------------------------ 檢查 */

function readEvents(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf-8').split('\n').filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

/** 一次抓下頁面上所有 `/api/` 請求，跑完 `body` 內的動作後還原。 */
function pageScript(body) {
  return `(async () => {
    const calls = [];
    const realFetch = window.fetch;
    window.fetch = (...args) => {
      const url = String(args[0]);
      if (url.indexOf('/api/') >= 0) {
        calls.push({ url: url, body: args[1] && args[1].body ? String(args[1].body) : '' });
      }
      return realFetch(...args);
    };
    let out;
    try { out = await (async () => { ${body} })(); }
    finally { window.fetch = realFetch; }
    return { calls: calls, out: out === undefined ? null : out };
  })()`;
}

async function main() {
  const fixture = EXTERNAL_BASE ? null : makeWorkdir();
  const base = EXTERNAL_BASE || `http://127.0.0.1:${PORT}`;
  const writable = !EXTERNAL_BASE;
  let server = null;
  let chrome = null;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-areas-'));
  try {
    if (!EXTERNAL_BASE) {
      server = spawn('python3', [
        path.join(ROOT, 'scripts', 'serve_question_review_ui.py'),
        '--candidate-jsonl', path.join(fixture.dir, 'candidates.jsonl'),
        '--review-log', path.join(fixture.ui, 'question_review_events.jsonl'),
        '--host', '127.0.0.1', '--port', String(PORT),
      ], { cwd: ROOT, stdio: 'ignore' });
      server.on('error', (error) => console.error('無法啟動伺服器：', error.message));
      if (!await waitForServer(base)) {
        throw new Error(`scratch 伺服器起不來（${base}）`);
      }
    }

    chrome = spawn(CHROME, [
      '--headless=new', '--disable-gpu', `--remote-debugging-port=${DEBUG_PORT}`,
      `--user-data-dir=${profile}`, '--window-size=1700,1100', 'about:blank',
    ], { stdio: 'ignore' });

    let target;
    for (let i = 0; i < 40 && !target; i += 1) {
      await sleep(250);
      try {
        const list = await getJson(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
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
    await new Promise((r) => { ws.onopen = r; });
    const exceptions = [];
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
      if (msg.method === 'Runtime.exceptionThrown') exceptions.push(msg.params.exceptionDetails.text);
    };
    await send('Page.enable', {});
    await send('Runtime.enable', {});
    await send('Page.navigate', { url: `${base}/v2` });
    await sleep(4500);

    const evaluate = async (expression) => {
      const result = await send('Runtime.evaluate', {
        expression, awaitPromise: true, returnByValue: true,
      });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'page error');
      return result.result.value;
    };
    const goto = async (area) => {
      await evaluate(`document.querySelector('.area-btn[data-area="${area}"]').click(); true`);
      await sleep(1600);
    };

    // ------------------------------------------------------------ 五個區都在，預設是題目審核區
    const nav = await evaluate(`
      Array.from(document.querySelectorAll('.area-btn')).map((n) => n.dataset.area)`);
    check(JSON.stringify(nav) === JSON.stringify(['home', 'question', 'answer', 'discuss', 'principles']),
      '導覽列是五個區（首頁／題目／答案／討論／原則）', JSON.stringify(nav));
    const initial = await evaluate(`({
      active: document.querySelector('.area-btn.on').dataset.area,
      shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
      hash: location.hash,
    })`);
    check(initial.active === 'question', '開頁預設停在題目審核區', initial.active);
    check(initial.shown.length === 1 && initial.shown[0] === 'areaQuestion',
      '而且只有題目審核區是可見的', JSON.stringify(initial.shown));

    // ------------------------------------------------------------ 首頁
    await goto('home');
    const home = await evaluate(`(async () => {
      const discuss = await (await fetch('/api/discuss?limit=1', { cache: 'no-store' })).json();
      const cards = Array.from(document.querySelectorAll('#homeCards .card'));
      const principles = cards.find((card) => card.dataset.go === 'principles');
      return {
        shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
        cards: cards.map((n) => n.dataset.go),
        bigs: cards.map((n) => n.querySelector('.big').textContent.trim()),
        principlesBig: principles ? principles.querySelector('.big').textContent : '',
        serverCount: String((discuss.principles || {}).count),
        serverOpen: String((discuss.repair_questions || {}).open_count),
        hash: location.hash,
      };
    })()`);
    check(home.shown.length === 1 && home.shown[0] === 'areaHome', '按首頁真的切到首頁', JSON.stringify(home.shown));
    check(JSON.stringify(home.cards) === JSON.stringify(['question', 'answer', 'discuss', 'principles']),
      '首頁四張卡各指向一個審核區', JSON.stringify(home.cards));
    check(home.bigs.every((b) => b !== '載入中…'), '首頁的數字是把端點的數讀出來印的', JSON.stringify(home.bigs));
    check(home.principlesBig.includes(home.serverCount) && home.principlesBig.includes(home.serverOpen),
      '原則卡的數字就是同一份 /api/discuss 裡的兩個數（不是自己算的）',
      `${home.principlesBig} ← ${home.serverCount} 條／${home.serverOpen} 題`);
    check(/^#首頁/.test(decodeURIComponent(home.hash)), '首頁在 hash 裡，重整會回到首頁', home.hash);

    // 首頁和大每一個區一樣：**畫一次**。繞去題目區再回來不該再打一次三個端點，也不該重建 DOM。
    const homeReentry = await evaluate(pageScript(`
      document.querySelector('.area-btn[data-area="question"]').click();
      await new Promise((r) => setTimeout(r, 500));
      document.querySelector('.area-btn[data-area="home"]').click();
      await new Promise((r) => setTimeout(r, 1200));
      return { cards: document.querySelectorAll('#homeCards .card').length };
    `));
    check(homeReentry.out.cards === 4, '再進首頁時卡片還在（不是被重畫掉）', String(homeReentry.out.cards));
    check(homeReentry.calls.length === 0, '再進首頁不再重打端點（首頁也畫一次）',
      `${homeReentry.calls.length} 次 /api 呼叫`);

    // ------------------------------------------------------------ 答案審核區
    await goto('answer');
    // 選一張卡：用 payload 裡的 `sheet_key`（不是螢幕上的字）找到左欄那一列。
    const selectSheet = async (subject) => evaluate(`(async () => {
      for (let i = 0; i < 60 && !document.querySelector('#sheetList [data-sheet]'); i += 1) {
        await new Promise((r) => setTimeout(r, 150));
      }
      const payload = await (await fetch('/api/answer-candidates?limit=200', { cache: 'no-store' })).json();
      const sheet = (payload.candidates || []).find((s) => (s.metadata || {}).normalized_subject_name === ${JSON.stringify(subject)});
      if (!sheet) return { ok: false, why: 'payload 裡沒有這張卡' };
      const row = document.querySelector('#sheetList [data-sheet][data-key="' + CSS.escape(sheet.sheet_key) + '"]');
      if (!row) return { ok: false, why: '左欄沒有這一列' };
      row.click();
      await new Promise((r) => setTimeout(r, 500));
      return {
        ok: true, sheet_key: sheet.sheet_key,
        question_pdf: ((sheet.question_source_files || {}).official_pdf || ''),
        answer_pdf: ((sheet.source_files || {}).official_pdf || ''),
        metadata_pdf: ((sheet.metadata || {}).answer_pdf_relative || ''),
        frame_src: (document.querySelector('#answerFrame') || {}).src || '',
        head_text: ((document.querySelector('#answerPdfHead') || {}).textContent || '').trim(),
        counts: {
          total: String(sheet.question_count || 0), reviewable: String(sheet.reviewable_question_count || 0),
          placeholder: String(sheet.placeholder_count || 0), reviewed: String(sheet.reviewed_count || 0),
          accepted: String(sheet.accepted_count || 0), corrected: String(sheet.corrected_count || 0),
          attention: String(sheet.answer_attention_count || 0),
        },
        rows: (sheet.rows || []).map((r) => ({ n: r.question_number, key: r.candidate_key,
          answer: r.answer, correction: (r.answer_review || {}).correction,
          needsManual: Boolean((r.answer_hint || {}).needs_manual_choice) })),
      };
    })()`);
    const sheetA = await selectSheet(SUBJECT_A);
    const sheetB = await selectSheet(SUBJECT_B);
    await selectSheet(SUBJECT_A);
    check(sheetA.ok && sheetB.ok, '兩張答案卡都列得出來、點得到',
      `${sheetA.why || sheetA.sheet_key} / ${sheetB.why || sheetB.sheet_key}`);
    // 線上佇列的形狀：答案卷的路徑**不在** `source_files`，只在 `metadata.answer_pdf_relative`。
    // 舊版只讀 `source_files`，於是真實資料上右欄永遠是一句「沒有答案卷 PDF 路徑」——而使用者的
    // 第一句要求就是「右邊必須要有答案PDF讓我對照」。這一組就是那個行為的負向控制。
    check(sheetB.answer_pdf === '' && sheetB.metadata_pdf !== '',
      '（前提）B 卷的答案卷路徑只在 metadata 裡，`source_files` 是空的',
      `source_files=${sheetB.answer_pdf || '(空)'}／metadata=${sheetB.metadata_pdf || '(空)'}`);
    check(sheetB.frame_src !== '' && decodeURIComponent(sheetB.frame_src).includes(sheetB.metadata_pdf)
      && sheetB.head_text.includes(sheetB.metadata_pdf.split('/').pop()),
      '答案卷路徑只在 metadata 時，右欄照樣開那一份 PDF（不是一句「沒有路徑」）',
      sheetB.frame_src ? decodeURIComponent(sheetB.frame_src).slice(-70) : '(沒有 iframe)');

    const basic = await evaluate(`({
      shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
      table: document.querySelectorAll('#answerMain .atable tbody tr').length,
      hash: location.hash,
    })`);
    check(basic.shown.length === 1 && basic.shown[0] === 'areaAnswer', '按答案審核區真的切過去', JSON.stringify(basic.shown));
    check(basic.table >= 3, '選一張答案卡會把它的每一列畫成表（含缺題保留那一列）', `${basic.table} 列`);
    check(/^#答案/.test(decodeURIComponent(basic.hash)), '答案審核區在 hash 裡', basic.hash);

    // 右欄必須是**這一張卡的答案卷**，不是題目卷（舊版右欄是空的）。
    const pdfPane = await evaluate(`({
      hasFrame: Boolean(document.querySelector('#answerPdf iframe')),
      src: (document.querySelector('#answerFrame') || {}).getAttribute ? document.querySelector('#answerFrame').getAttribute('src') : '',
      title: (document.querySelector('#answerFrame') || {}).title || '',
      head: (document.querySelector('#answerPdfHead') || {}).textContent || '',
    })`);
    const answerPdf = sheetA.answer_pdf;
    const questionPdf = sheetA.question_pdf;
    check(pdfPane.hasFrame && pdfPane.src.includes('/file?path=' + encodeURIComponent(answerPdf)),
      '右欄的 iframe 指到這一張卡的答案卷 PDF',
      pdfPane.src.slice(0, 90));
    check(!questionPdf || !pdfPane.src.includes(encodeURIComponent(questionPdf)),
      '（負向控制）右欄**不是**題目卷 PDF', questionPdf);
    check(pdfPane.head.includes(String(sheetA.counts.reviewed)),
      '右欄的抬頭說得出這一張卡審到哪裡', pdfPane.head.trim().slice(0, 60));

    // 每張卡的數字就是 payload 的數字，不自己算。
    const counters = await evaluate(`(async () => {
      const payload = await (await fetch('/api/answer-candidates?limit=200', { cache: 'no-store' })).json();
      const sheet = (payload.candidates || []).find((s) => s.sheet_key === ${JSON.stringify(sheetA.sheet_key)});
      const drawn = {};
      for (const node of document.querySelectorAll('#answerMain [data-count]')) {
        drawn[node.dataset.count] = (node.querySelector('b') || {}).textContent || '';
      }
      return { drawn: drawn, payload: {
        total: String(sheet.question_count || 0), reviewable: String(sheet.reviewable_question_count || 0),
        placeholder: String(sheet.placeholder_count || 0), reviewed: String(sheet.reviewed_count || 0),
        accepted: String(sheet.accepted_count || 0), corrected: String(sheet.corrected_count || 0),
        attention: String(sheet.answer_attention_count || 0),
      } };
    })()`);
    const counterNames = Object.keys(counters.payload);
    check(counterNames.length === 7 && counterNames.every((name) => counters.drawn[name] === counters.payload[name]),
      '七個數字（總題數／可核／缺題保留／已核／通過／人工修正／MOD 需確認）都是 payload 的數',
      JSON.stringify(counters.drawn));
    check(counters.drawn.placeholder === '1' && counters.drawn.corrected === '1',
      '缺題保留與人工修正各有一題（資料裡真的一題缺號、一題已修正）',
      `缺 ${counters.drawn.placeholder}／修正 ${counters.drawn.corrected}`);

    // 已經記錄的修正要看得見：`answer_review.correction` 是**字串** `A|C`。
    // 舊版讀 `review.correction?.answer`（永遠 undefined），所以那一題只顯示 parser 的 `A`。
    const correction = await evaluate(`(async () => {
      const payload = await (await fetch('/api/answer-candidates?limit=200', { cache: 'no-store' })).json();
      const sheet = (payload.candidates || []).find((s) => s.sheet_key === ${JSON.stringify(sheetA.sheet_key)});
      const row = (sheet.rows || []).find((r) => r.candidate_key === ${JSON.stringify(fixture ? fixture.correctedKey : '')});
      if (!row) return { ok: false, server: null };
      const server = (row.answer_review || {}).correction;
      const tr = document.querySelector('#answerMain tr[data-key="' + CSS.escape(row.candidate_key) + '"]');
      return {
        ok: true, server: server,
        shown: tr.querySelector('.ans-current').textContent.trim(),
        on: Array.from(tr.querySelectorAll('[data-answer]')).filter((b) => b.classList.contains('on')).map((b) => b.dataset.answer),
        mode: Array.from(tr.querySelectorAll('[data-mode]')).filter((b) => b.classList.contains('on')).map((b) => b.dataset.mode),
      };
    })()`);
    check(correction.ok && correction.server === 'A|C',
      '伺服器上那一題的修正是一個字串 A|C', String(correction.server));
    if (correction.ok) {
      check(correction.shown === 'A|C',
        '畫面上顯示的是**已記錄的修正**，不是 parser 的原始答案',
        `${correction.shown}（舊版會是 A）`);
      check(JSON.stringify(correction.on) === JSON.stringify(['A', 'C']),
        '而且被修正到的字母是亮的（A、C 亮，B、D 不亮）', JSON.stringify(correction.on));
      check(correction.mode.includes('any'),
        '模式也從那個字串讀出來（`A|C`＝任一）', JSON.stringify(correction.mode));
    }

    // 點字母只改草稿。用真的點擊量：**零個** POST。
    const draft = await evaluate(pageScript(`
      const tr = document.querySelectorAll('#answerMain tr[data-key]')[0];
      const button = Array.from(tr.querySelectorAll('[data-answer]')).find((b) => !b.classList.contains('on'));
      if (!button) return { clicked: false };
      const shownBefore = tr.querySelector('.ans-current').textContent.trim();
      button.click();
      await new Promise((r) => setTimeout(r, 300));
      return { clicked: true, shownBefore: shownBefore,
               shownAfter: tr.querySelector('.ans-current').textContent.trim(),
               chosen: button.dataset.answer, on: button.classList.contains('on') };
    `));
    const writes = draft.calls.filter((call) => /answer-review/.test(call.url));
    check(draft.out.clicked && draft.out.shownAfter === draft.out.chosen && draft.out.on,
      '點一個還沒選的字母會把它顯示成草稿、而且變亮',
      `${draft.out.shownBefore} → ${draft.out.shownAfter}`);
    check(writes.length === 0,
      '但點字母不會自己送出任何審核（要按下面那排）', JSON.stringify(writes.map((c) => c.url)));

    // 再點同一個字母＝取消。舊版只會把草稿換成那一個字母，取消不掉。
    const untoggle = await evaluate(pageScript(`
      const tr = document.querySelectorAll('#answerMain tr[data-key]')[0];
      const button = Array.from(tr.querySelectorAll('[data-answer]')).find((b) => b.classList.contains('on'));
      const chosen = button.dataset.answer;
      button.click();
      await new Promise((r) => setTimeout(r, 300));
      return { chosen: chosen, stillOn: button.classList.contains('on'),
               shown: tr.querySelector('.ans-current').textContent.trim() };
    `));
    check(!untoggle.out.stillOn && untoggle.out.shown !== untoggle.out.chosen,
      '再點一次同一個字母＝把它從草稿裡拿掉',
      `${untoggle.out.chosen} → ${untoggle.out.shown}`);

    // 複選／任一：同一組字母的兩種寫法，而且「儲存答案修正」送出**一筆**、只為這一題。
    const saveCorrected = async (mode) => evaluate(pageScript(`
      const tr = document.querySelectorAll('#answerMain tr[data-key]')[0];
      const key = tr.dataset.key;
      const click = (selector) => tr.querySelector(selector).click();
      click('[data-clear]');
      await new Promise((r) => setTimeout(r, 100));
      click('[data-mode="${mode}"]');
      await new Promise((r) => setTimeout(r, 100));
      for (const letter of ['A', 'C']) {
        Array.from(tr.querySelectorAll('[data-answer]')).find((b) => b.dataset.answer === letter).click();
        await new Promise((r) => setTimeout(r, 100));
      }
      const drafted = tr.querySelector('.ans-current').textContent.trim();
      document.querySelector('[data-sheet-action="correct"]').click();
      await new Promise((r) => setTimeout(r, 2500));
      return { key: key, drafted: drafted };
    `));
    //: 上面那兩次修正寫的是哪一題；阻擋那一組要驗的是**另一題**（否則「只寫這一題」驗不出來）。
    let correctedKey = '';
    if (writable) {
      const compound = await saveCorrected('all');
      correctedKey = compound.out.key;
      const batches = compound.calls.filter((call) => /answer-review-batch/.test(call.url));
      const compoundBody = batches.length ? JSON.parse(batches[0].body) : null;
      check(compound.out.drafted === 'A+C', '複選模式點 A、C 之後草稿是 A+C', compound.out.drafted);
      check(batches.length === 1, '「儲存答案修正」只送一筆批次請求', `${batches.length} 筆`);
      check(compoundBody && compoundBody.action === 'correct', '那一筆的動作是 correct',
        compoundBody ? compoundBody.action : '(沒有請求)');
      check(compoundBody && compoundBody.entries.length === 1
        && compoundBody.entries[0].candidate_key === compound.out.key
        && compoundBody.entries[0].corrected_answer === 'A+C',
        '而且只有這一題、修正值是 A+C',
        compoundBody ? JSON.stringify(compoundBody.entries.map((e) => [e.candidate_key, e.corrected_answer])) : '');

      const anyMode = await saveCorrected('any');
      const anyBatches = anyMode.calls.filter((call) => /answer-review-batch/.test(call.url));
      const anyBody = anyBatches.length ? JSON.parse(anyBatches[0].body) : null;
      check(anyMode.out.drafted === 'A|C', '任一模式點 A、C 之後草稿是 A|C', anyMode.out.drafted);
      check(anyBatches.length === 1 && anyBody.entries.length === 1
        && anyBody.entries[0].corrected_answer === 'A|C',
        '「儲存答案修正」照樣只寫這一題，修正值是 A|C',
        anyBody ? JSON.stringify(anyBody.entries.map((e) => [e.candidate_key, e.corrected_answer])) : '');

      // 寫進去的東西真的進了 append-only 事件檔（不是只有畫面變了）。
      //
      // 驗的是 `corrected_answer`，不是 `action`：伺服器會把這一筆的動作改寫成**站得住的**
      // 那個動作（`_reaffirm_standing_action`：沒有既有決定時 `correct` → `reviewed`），所以
      // 「這一筆是不是一次修正」的證據是它帶的修正值，不是它的 action。整份事件檔的總帳
      // （幾筆、哪幾題）在下面所有寫入做完之後一次驗，那才是「沒有別題被順手寫進去」。
      const log = readEvents(path.join(fixture.ui, 'answer_review_events.jsonl'));
      const mine = log.filter((event) => event.candidate_key === correctedKey);
      const values = mine.map((event) => event.corrected_answer);
      check(values.filter((value) => value === 'A+C').length === 1
        && values.filter((value) => value === 'A|C').length === 1,
        '兩次修正都寫進 append-only 的事件檔（值就是畫面上的 A+C 與 A|C）',
        mine.map((event) => `${event.action}:${event.corrected_answer}`).join(' '));
    } else {
      skip('複選／任一的送出與事件檔', '外部 base：寫入檢查要自己的佇列');
    }

    // 每題的「阻擋」是一個開關：按一次寫這一題，再按一次取消（回到未審）。
    // 用最後一列（上面那兩次修正動的是第一列，這裡要看另一題才驗得出「只寫這一題」）。
    const blockRowIndex = await evaluate(
      `document.querySelectorAll('#answerMain tr[data-key]').length - 1`);
    const blockKeyRow = () => evaluate(`
      (() => {
        const tr = document.querySelectorAll('#answerMain tr[data-key]')[${blockRowIndex}];
        return { key: tr.dataset.key, blocked: tr.querySelector('[data-block]').classList.contains('on') };
      })()`);
    const beforeBlock = await blockKeyRow();
    check(beforeBlock.key !== correctedKey, '（前提）阻擋測的是另一題',
      `${beforeBlock.key} ≠ ${correctedKey || '(沒有寫入)'}`);
    const blocked = await evaluate(pageScript(`
      const key = ${JSON.stringify(beforeBlock.key)};
      const btn = () => document.querySelector('#answerMain tr[data-key="' + CSS.escape(key) + '"] [data-block]');
      btn().click();
      for (let i = 0; i < 30 && !btn().classList.contains('on'); i += 1) {
        await new Promise((r) => setTimeout(r, 250));
      }
      return { on: btn().classList.contains('on'),
               off: Array.from(document.querySelectorAll('#answerMain [data-block]')).filter((b) => !b.classList.contains('on')).length };
    `));
    const blockPosts = blocked.calls.filter((call) => /answer-review$/.test(call.url));
    const blockBody = blockPosts.length ? JSON.parse(blockPosts[0].body) : null;
    check(blockPosts.length === 1, '按「阻擋」只送一筆請求（不是整份）', `${blockPosts.length} 筆`);
    check(blockBody && blockBody.action === 'block' && blockBody.candidate_key === beforeBlock.key,
      '那一筆就是這一題的 block', blockBody ? `${blockBody.action}／${blockBody.candidate_key}` : '(沒有請求)');
    check(blocked.out.on, '按完那一列的阻擋鈕變紅（class on）', String(blocked.out.on));
    if (writable) {
      const log = readEvents(path.join(fixture.ui, 'answer_review_events.jsonl'));
      const blocks = log.filter((event) => event.action === 'block');
      check(blocks.length === 1 && blocks[0].candidate_key === beforeBlock.key,
        'append-only 事件檔裡只有這一題被阻擋',
        blocks.map((event) => event.candidate_key).join(' '));
    }

    // 同一份文件不重建 iframe：審題者捲到的位置要活過一次寫入（再按一次就是取消阻擋）。
    const keptFrame = await evaluate(pageScript(`
      const key = ${JSON.stringify(beforeBlock.key)};
      const btn = () => document.querySelector('#answerMain tr[data-key="' + CSS.escape(key) + '"] [data-block]');
      const frame = document.querySelector('#answerFrame');
      frame.dataset.scrollProbe = 'kept';
      btn().click();
      for (let i = 0; i < 30 && btn().classList.contains('on'); i += 1) {
        await new Promise((r) => setTimeout(r, 250));
      }
      await new Promise((r) => setTimeout(r, 800));
      const again = document.querySelector('#answerFrame');
      return { same: again === frame, probe: again ? again.dataset.scrollProbe || '' : '',
               on: btn().classList.contains('on') };
    `));
    const unblockBody = keptFrame.calls.filter((call) => /answer-review$/.test(call.url))
      .map((call) => JSON.parse(call.body));
    check(unblockBody.length === 1 && unblockBody[0].action === 'unreviewed'
      && unblockBody[0].candidate_key === beforeBlock.key,
      '再按一次同一題＝取消阻擋（unreviewed），還是只寫這一題',
      JSON.stringify(unblockBody.map((body) => [body.action, body.candidate_key])));
    check(keptFrame.out.same && keptFrame.out.probe === 'kept',
      '這一段過程裡同一份 PDF 的 iframe 沒有被重建（捲動位置是契約）',
      keptFrame.out.same ? '同一個節點' : '被重建了');
    const afterUnblock = await blockKeyRow();
    check(!afterUnblock.blocked, '取消之後那一列的阻擋鈕不再變紅', String(afterUnblock.blocked));
    if (writable) {
      const log = readEvents(path.join(fixture.ui, 'answer_review_events.jsonl'));
      const mine = log.filter((event) => event.candidate_key === beforeBlock.key);
      check(mine.length === 2 && mine[1].action === 'unreviewed',
        '取消也寫進事件檔（append-only，兩筆都在）',
        mine.map((event) => event.action).join(' '));
      // 整份事件檔的總帳：這一輪的五筆就是全部，沒有別題被順手寫進去。
      //
      // 五筆＝佇列裡原本就記著的那一筆修正（另一題）＋ 上面那一次修正的兩筆 ＋ 阻擋／取消兩筆。
      // 數「哪幾題各有幾筆」而不是數 action，理由同上：action 是伺服器的投影，題數與筆數才是
      // append-only 契約。
      const whole = readEvents(path.join(fixture.ui, 'answer_review_events.jsonl'));
      const byKey = new Map();
      for (const event of whole) byKey.set(event.candidate_key, (byKey.get(event.candidate_key) || 0) + 1);
      const seeded = whole.filter((event) => event.candidate_key !== beforeBlock.key
        && event.candidate_key !== correctedKey);
      check(whole.length === 5 && byKey.size === 3
        && byKey.get(correctedKey) === 2 && byKey.get(beforeBlock.key) === 2
        && seeded.length === 1 && seeded[0].corrected_answer === 'A|C',
        '整份事件檔就是那五筆（原本那一筆修正 ＋ 這次修正兩筆 ＋ 阻擋／取消兩筆），沒有別題被動到',
        whole.map((event) => `${event.candidate_key}:${event.action}`).join(' '));
    }

    // MOD 的 `#`／空白要在**送出之前**講出來，而不是等伺服器 409。
    await selectSheet(SUBJECT_B);
    const guarded = await evaluate(pageScript(`
      document.querySelector('[data-sheet-action="accept"]').click();
      await new Promise((r) => setTimeout(r, 1200));
      return Array.from(document.querySelectorAll('#answerMain [data-saved]')).map((n) => n.textContent).join(' ');
    `));
    const guardPosts = guarded.calls.filter((call) => /answer-review/.test(call.url));
    check(guardPosts.length === 0 && guarded.out.trim().length > 0,
      'MOD 還有 `#` 時「整份通過」不會送出，而且說得出是哪幾題',
      guarded.out.trim().slice(0, 60));

    // 換區不清空（隱藏，不是清空）
    await selectSheet(SUBJECT_A);
    const preserved = await evaluate(`(async () => {
      const note = document.querySelector('#answerMain textarea[data-note]');
      if (!note) return { has: false };
      note.value = '這一區的註記不該被換區清掉';
      note.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('.area-btn[data-area="home"]').click();
      await new Promise((r) => setTimeout(r, 1200));
      document.querySelector('.area-btn[data-area="answer"]').click();
      await new Promise((r) => setTimeout(r, 900));
      const again = document.querySelector('#answerMain textarea[data-note]');
      return { has: true, value: again ? again.value : null,
               html: document.querySelector('#answerMain').innerHTML.length };
    })()`);
    check(preserved.has, '答案列有可打字的註記框');
    if (preserved.has) {
      check(preserved.value === '這一區的註記不該被換區清掉',
        '繞去首頁再回來，打到一半的註記還在（隱藏不是清空）', String(preserved.value));
    }

    // ------------------------------------------------------------ 錯題討論區
    //
    // 這一區只放卡住的題——人按過「阻擋」，或管線／AI 退回待複核——且每一題都給得起修的能力
    // （編輯框、紙本、AI 意見與它的截圖）。基本原則與代理反問已經自成一個區（原則區），
    // 由 `scripts/test_v2_principles_browser.mjs` 釘住。
    await goto('discuss');
    const discuss = await evaluate(`(async () => {
      for (let i = 0; i < 60 && document.querySelectorAll('#discussMain .case').length === 0
              && !document.querySelector('#discussMain .empty-area') && !document.querySelector('#discussMain .empty') ; i += 1) {
        await new Promise((r) => setTimeout(r, 200));
      }
      const listRows = Array.from(document.querySelectorAll('#discussList .row'));
      return {
        shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
        cases: document.querySelectorAll('#discussMain .case').length,
        listRows: listRows.length,
        firstRow: listRows.length ? listRows[0].textContent.trim().slice(0, 30) : '',
        stemBoxes: document.querySelectorAll('#discussMain #dStem').length,
        optionBoxes: document.querySelectorAll('#discussMain .opt textarea').length,
        pdf: document.querySelectorAll('#discussPdf').length,
        emptyNote: (document.querySelector('#discussMain .empty-area') || {}).textContent || '',
        side: (document.querySelector('#discussSide .n') || {}).textContent || '',
        hash: location.hash,
      };
    })()`);
    check(discuss.shown.length === 1 && discuss.shown[0] === 'areaDiscuss', '按錯題討論區真的切過去', JSON.stringify(discuss.shown));
    check(discuss.cases >= 1, '被阻擋的那一題在討論區裡（這一區只放卡住的題）',
      `${discuss.cases} 題卡住（列表 ${discuss.listRows} 列）`);
    if (discuss.cases > 0) {
      check(discuss.listRows >= discuss.cases, '左欄的列數涵蓋中間的每個個案', `${discuss.listRows} 列`);
      check(discuss.stemBoxes === 1, '每一題都給得起題幹編輯框（這一區能修，不只是報告）', `${discuss.stemBoxes} 個`);
      check(discuss.optionBoxes >= 1, '選項也有編輯框', `${discuss.optionBoxes} 個`);
    }
    check(discuss.pdf === 1, '右欄紙本在');
    check(discuss.side !== '', '側欄印出這一區的統計', `卡住 ${discuss.side} 題`);
    check(/^#錯題/.test(decodeURIComponent(discuss.hash)), '錯題討論區在 hash 裡', discuss.hash);

    const discussScope = await evaluate(`(async () => {
      const api = await (await fetch('/api/discuss?limit=500', { cache: 'no-store' })).json();
      return {
        serverRows: (api.candidates || []).length,
        drawnRows: document.querySelectorAll('#discussList .row').length,
        buckets: api.buckets || [],
        anyUnstuck: (api.candidates || []).some((c) => {
          const r = c.review || {};
          return !(r.action === 'block' || r.is_repair_pending || r.is_accepted_reaudit_pending
                   || (r.is_reset_unreviewed && !r.is_repair_pending && !r.is_accepted_reaudit_pending));
        }),
      };
    })()`);
    check(!discussScope.anyUnstuck, '討論區只放卡住的題（沒有把整條佇列拉進來）',
      JSON.stringify(discussScope.buckets));
    if (discussScope.serverRows > 0) {
      check(discussScope.drawnRows >= Math.min(discussScope.serverRows, 500) - 5,
        '畫出來的列數跟著伺服器回的卡住題數', `${discussScope.drawnRows} vs ${discussScope.serverRows}`);
    }

    // ------------------------------------------------------------ 原則區自成一頁
    await goto('principles');
    const principles = await evaluate(`({
      shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
      hash: location.hash,
    })`);
    check(principles.shown.length === 1 && principles.shown[0] === 'areaPrinciples',
      '原則區是一個獨立的區（不再藏在錯題討論區裡）', JSON.stringify(principles.shown));
    check(/^#原則/.test(decodeURIComponent(principles.hash)), '原則區在 hash 裡', principles.hash);

    // ------------------------------------------------------------ 題目審核區不能被弄壞
    await goto('question');
    const back = await evaluate(`({
      shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
      rows: document.querySelectorAll('#listBody .row').length,
      hash: location.hash,
    })`);
    check(back.shown.length === 1 && back.shown[0] === 'areaQuestion',
      '回到題目審核區，而且只有它可見', JSON.stringify(back.shown));
    check(back.rows >= 1, '題目清單還是在（基準線沒被換區破壞）', `${back.rows} 列`);
    check(!/首頁|答案|錯題|原則/.test(decodeURIComponent(back.hash)),
      '回到題目審核區，hash 回到沒有區前綴的舊寫法（書籤契約）', back.hash);

    check(exceptions.length === 0, '整段過程沒有 console 例外', exceptions.join(' / '));

    await send('Page.captureScreenshot', {}).then((r) => {
      if (r && r.data) fs.writeFileSync('/tmp/v2_areas.png', Buffer.from(r.data, 'base64'));
    });
    console.log('  截圖：/tmp/v2_areas.png');
    console.log(failures ? `\n${failures} 項不符` : '\n全部符合');
    ws.close();
  } finally {
    if (chrome) chrome.kill();
    if (server) server.kill();
    await sleep(300);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome 還在寫 profile */ }
    if (fixture && !process.env.AREAS_WORKDIR) {
      try { fs.rmSync(fixture.dir, { recursive: true, force: true }); } catch { /* 伺服器剛關 */ }
    }
  }
  process.exit(failures ? 1 : 0);
}

main().catch((error) => {
  console.error('稽核失敗：', error);
  process.exit(2);
});
