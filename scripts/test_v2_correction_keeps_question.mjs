#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明「儲存修正」之後**人還看得到那一題修成了什麼樣子**。
 *
 * owner 2026-09-25（原文）：「修正跟通過是不同的，為什麼AI給我建議，我用帶入修正、儲存修正，
 * 結果它就通過了(我就找不到了)，修正完我當然要看過當下的畫面才能確定有沒有改錯，你必須知道
 * **改動跟真實畫面顯示是不同的**。」
 *
 * 三件事要同時成立，缺一件人就不能驗：
 *   1. 儲存修正寫的是 `correct`，**不是** `accept`——修正不等於通過；
 *   2. 存完之後人**停在這一題**，畫面上顯示的是**伺服器存下來的那一份文字**（不是編輯框、也不
 *      是改動前的舊字），所以他當下就看得見自己改了什麼；
 *   3. 這一題之後**還找得到**（清單裡還在，不是被自己的修正踢出去）。
 *
 * 這個檔案在**隔離的伺服器**上跑（自己的 candidates、自己的事件檔、自己的 findings），所以：
 *   * 它真的按了「帶入修正」與「儲存修正」——驗的是寫入路徑與畫面，不是字串；
 *   * 它讀回 append-only 事件檔確認寫下的動作；
 *   * 它一筆都不會碰到 live 的佇列或事件檔。
 *
 * 用法：node scripts/test_v2_correction_keeps_question.mjs [workdir]
 *   不給 workdir 時自己在 tmp 生一個，跑完刪掉。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const ROOT = path.resolve(decodeURIComponent(new URL('.', import.meta.url).pathname), '..');
const PORT = Number(process.env.CORRECTION_KEEPS_PORT || 8917);
const base = `http://127.0.0.1:${PORT}`;
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

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

/* 兩題同一卷：第 1 題是被修正的那一題，第 2 題只是「下一題」，用來證明存完之後人**沒有**被帶走。

   佇列自己有一份 `queue_index.json`（分類樹），題目區的範圍是從它挑出卷的——沒有它 `boot()`
   會直接說「無法建立分類」，所以夾具要跟站上一樣帶一份最小的分類樹，卷名要與 `paperOf()`
   算出來的一致（官方 PDF 的檔名去掉 `.pdf`）。 */
const PLANE = '1152_測試科_藥理學';
const PAPER = {
  category_name: '測試科', normalized_category_name: '測試科',
  subject_name: '藥理學', normalized_subject_name: '藥理學',
  paper_id: '115090:311:0704', year: '115', exam_ordinal: '2',
  question_pdf_relative: `國考題資料夾/10_official_pdf/by_official/${PLANE}.pdf`,
};
const SOURCE_FILES = { official_pdf: `國考題資料夾/10_official_pdf/by_official/${PLANE}.pdf` };
const QUEUE_INDEX = {
  papers: 1, questions: 2, issues: 0, candidates_sha256: '', review_events_carried: {},
  review_events_orphaned: {},
  taxonomy: {
    測試科: {
      papers: 1,
      years: { 115: { sittings: { 2: { subjects: { 藥理學: { papers: [PLANE], questions: 2 } } } } } },
    },
  },
  order: [PLANE], per_paper: [], categories: ['測試科'], subjects: ['藥理學'], years: ['115'],
};
const KEY1 = 'moex:115090:311:0704:1:question:q001';
const KEY2 = 'moex:115090:311:0704:1:question:q002';
/* 紙本真的印的是 `5-HT1A`（下標被壓平），抽取檔也是；模型讀出紙本的形式，並留下機械差別。 */
const PAPER_STEM = '下列何者為 5-HT<sub>1A</sub> 受體致效劑？';
const STORED_STEM = '下列何者為 5-HT1A 受體致效劑？';
const candidates = [
  {
    candidate_key: KEY1, question_number: 1, stem: STORED_STEM,
    options: [{ key: 'A', text: 'buspirone' }, { key: 'B', text: 'sumatriptan' }, { key: 'C', text: 'fluoxetine' }, { key: 'D', text: 'haloperidol' }],
    answer: 'A', metadata: { ...PAPER }, source_files: { ...SOURCE_FILES },
  },
  {
    candidate_key: KEY2, question_number: 2, stem: '第 2 題的題幹。',
    options: [{ key: 'A', text: '一' }, { key: 'B', text: '二' }, { key: 'C', text: '三' }, { key: 'D', text: '四' }],
    answer: 'B', metadata: { ...PAPER }, source_files: { ...SOURCE_FILES },
  },
];
/* 模型意見：有 `changes`，所以題目區會畫出「帶入修正」那顆按鈕。 */
const FINDING = {
  candidate_key: KEY1, created_at: '2026-09-25T02:00:00',
  model: 'qwen3.8-flash-next', population: 'dispute', prompt_version: 'test',
  question_number: 1, paper: '115090:311:0704', subject: '藥理學',
  finding: {
    verdict: 'DEFECT', what: '下標被壓平', where: '題幹：5-HT1A 應為 5-HT1A（1A 是下標）',
    fix: '把 1A 標成下標', rule_worthy: true, confidence: 0.9,
  },
  changes: [{
    field: 'stem', stored: STORED_STEM, page: PAPER_STEM, from: STORED_STEM, to: PAPER_STEM,
  }],
};

function makeWorkdir() {
  const dir = process.env.CORRECTION_KEEPS_WORKDIR
    ? path.resolve(process.env.CORRECTION_KEEPS_WORKDIR)
    : fs.mkdtempSync(path.join(os.tmpdir(), 'correction-keeps-'));
  const ui = path.join(dir, 'review-ui');
  fs.mkdirSync(ui, { recursive: true });
  fs.writeFileSync(path.join(ui, 'candidates.jsonl'),
    candidates.map((c) => JSON.stringify(c)).join('\n') + '\n', 'utf-8');
  fs.writeFileSync(path.join(ui, 'question_review_events.jsonl'), '', 'utf-8');
  fs.writeFileSync(path.join(ui, 'question_ai_findings.jsonl'), JSON.stringify(FINDING) + '\n', 'utf-8');
  fs.writeFileSync(path.join(ui, 'queue_index.json'), JSON.stringify(QUEUE_INDEX), 'utf-8');
  return { dir, ui };
}

async function waitForServer(timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { await getJson(`${base}/api/pipeline`); return true; } catch { await sleep(400); }
  }
  return false;
}

async function main() {
  const { ui } = makeWorkdir();
  const server = spawn('python3', [
    path.join(ROOT, 'scripts', 'serve_question_review_ui.py'),
    '--candidate-jsonl', path.join(ui, 'candidates.jsonl'),
    '--review-log', path.join(ui, 'question_review_events.jsonl'),
    '--host', '127.0.0.1', '--port', String(PORT),
  ], { cwd: ROOT, stdio: 'ignore' });
  server.on('error', (err) => console.error('無法啟動伺服器：', err.message));

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-correction-keeps-'));
  const debugPort = PORT + 1;
  let chrome;
  try {
    const up = await waitForServer();
    if (!up) { console.error('伺服器沒有起來；跳過。'); process.exitCode = 1; return; }

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
      if (msg.id && pending.has(msg.id)) {
        const resolve = pending.get(msg.id);
        pending.delete(msg.id);
        resolve(msg.result);
      }
    };
    await send('Page.enable', {});
    await send('Runtime.enable', {});
    const exceptions = [];
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.method === 'Runtime.exceptionThrown') exceptions.push(msg.params.exceptionDetails.text);
    });
    const evaluate = async (expression) => {
      const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
      return r.result.value;
    };

    await send('Page.navigate', { url: `${base}/v2` });
    await sleep(4500);

    /* 使用者的實際順序：在「未看」底下走題 → 按 E 開編輯框 → 帶入修正 → 儲存修正。
       在「未看」底下做，因為那正是「我就找不到了」發生的地方。 */
    const before = await evaluate(`(() => {
      const chip = document.querySelector('#chips .chip[data-view="unseen"]');
      if (chip) chip.click();
      const rows = Array.from(document.querySelectorAll('#listBody .row'));
      return { rows: rows.length, label: (rows[0] || {}).textContent || '' };
    })()`);
    await sleep(1200);
    const opened = await evaluate(`(async () => {
      const rows = Array.from(document.querySelectorAll('#listBody .row'));
      if (!rows.length) return { ok: false, why: '清單是空的' };
      rows[0].click();
      await new Promise((r) => setTimeout(r, 900));
      document.getElementById('actFix').click();      // E：開編輯框
      await new Promise((r) => setTimeout(r, 600));
      const apply = document.querySelector('#textSide .af-apply');
      if (!apply) return { ok: false, why: '找不到「帶入修正」' };
      apply.click();                                  // 把紙本讀法帶進編輯框
      await new Promise((r) => setTimeout(r, 400));
      return { ok: true, editor: document.getElementById('editStem').value };
    })()`);
    check(opened.ok, '畫面上找得到那一題、開得了編輯框、按得到「帶入修正」', opened.why || '');
    check(opened.ok && opened.editor.includes('<sub>'),
      '「帶入修正」把紙本的讀法（含 <sub>）帶進編輯框', opened.editor || '');

    const after = await evaluate(`(async () => {
      document.getElementById('actSave').click();     // E：儲存修正
      await new Promise((r) => setTimeout(r, 3000));
      const rows = Array.from(document.querySelectorAll('#listBody .row'));
      const stem = document.getElementById('viewStem');
      return {
        hint: document.getElementById('stateHint').textContent,
        where: document.getElementById('where').textContent,
        stemHtml: stem ? stem.innerHTML : null,
        stemText: stem ? stem.innerText : null,
        textSide: document.getElementById('textSide').innerText.slice(0, 400),
        rows: rows.length,
        active: (document.querySelector('#listBody .row.active') || {}).textContent || '',
        firstRow: (rows[0] || {}).textContent || '',
        index: typeof S !== 'undefined' ? S.index : null,
        verdict: typeof S !== 'undefined' ? S.verdict.get('${KEY1}') : null,
      };
    })()`);

    /* 找得到：修正過的題目要有自己的一格（chip）。存完之後從「未看」切到「已修正」，那一題要
       回到清單上——這一段是「我就找不到了」那個症狀的正面驗收。 */
    const chip = await evaluate(`(async () => {
      const node = document.querySelector('#chips .chip[data-view="corrected"]');
      if (!node) return { ok: false, why: '沒有「已修正」chip' };
      const count = (document.getElementById('nCorrected') || {}).textContent;
      node.click();
      await new Promise((r) => setTimeout(r, 1200));
      const rows = Array.from(document.querySelectorAll('#listBody .row'));
      return {
        ok: true, count,
        rows: rows.length,
        keys: typeof S !== 'undefined' ? S.rows.map((i) => i.candidate_key) : [],
        where: document.getElementById('where').textContent,
      };
    })()`);

    const events = fs.readFileSync(path.join(ui, 'question_review_events.jsonl'), 'utf-8')
      .split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
    const mine = events.filter((e) => e.candidate_key === KEY1);
    const saved = mine[mine.length - 1] || {};

    console.log('\n  觀測（現在的行為）：');
    console.log(`    事件 action=${JSON.stringify(saved.action)}  correction=${saved.correction ? '有' : '沒有'}`);
    console.log(`    清單列數=${after.rows}  游標那一列=${JSON.stringify(after.active)}`);
    console.log(`    S.verdict[第1題]=${JSON.stringify(after.verdict)}  狀態提示=${JSON.stringify(after.hint)}`);
    console.log(`    畫面題幹（HTML）=${JSON.stringify(after.stemHtml)}`);
    console.log(`    「已修正」chip：count=${JSON.stringify(chip.count)} 列數=${chip.rows} 哪裡=${JSON.stringify((chip.where || '').trim())}`);
    console.log(`    例外=${JSON.stringify(exceptions)}`);
    console.log('');

    const storedStem = String((saved.correction || {}).stem || '');
    const stripped = (s) => String(s || '').replace(/<[^>]*>/g, '');
    check(mine.length === 1 && saved.action === 'correct',
      '儲存修正寫下的是 correct 這一筆事件', `action=${JSON.stringify(saved.action)}`);
    check(!events.some((e) => e.action === 'accept'),
      '儲存修正**沒有**寫下 accept：修正不等於通過');
    check(Boolean(saved.correction && String(saved.correction.stem || '').includes('<sub>')),
      '修正的內容真的進了事件（含 <sub>）', JSON.stringify((saved.correction || {}).stem || '').slice(0, 60));
    check(after.index === 0 && after.where.includes('第 1 題'),
      '存完之後人停在這一題（沒有被帶到下一題）', after.where.trim());
    /* 這一條是整個缺陷的重點：**畫面上的字要等於伺服器存下來的那一份**，不是「有出現 5-HT」。
       所以先比對「拿掉標記後的字」與事件裡的 correction，再要求畫面帶著人打的那個標記
       （`<sub>`）——只有兩邊都成立，「改動」與「真實畫面顯示」才是同一件事。 */
    check(stripped(after.stemText) === stripped(storedStem)
      && String(after.stemHtml || '').includes('<sub>'),
      '畫面上顯示的是存下來的那一份文字（含 <sub> 標記）',
      `畫面=${JSON.stringify(after.stemHtml)} 存下來=${JSON.stringify(storedStem)}`);
    check(after.rows === 2,
      '這一題還留在清單裡（找得到）', `${after.rows} 列`);
    check(chip.ok && chip.count === '1' && chip.rows === 1 && chip.keys[0] === KEY1,
      '「已修正」chip 找得到剛修正的那一題',
      chip.ok ? `count=${chip.count} 列數=${chip.rows} keys=${JSON.stringify(chip.keys)}` : chip.why);
  } catch (error) {
    console.error('harness 失敗：', error.message || error);
    process.exitCode = 1;
  } finally {
    try { if (chrome) chrome.kill(); } catch { /* 已經結束 */ }
    try { server.kill(); } catch { /* 已經結束 */ }
    if (!process.env.CORRECTION_KEEPS_WORKDIR) {
      try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* 留著 */ }
    }
  }
  console.log(failures ? `\n${failures} 條不符合` : '\n全部符合');
  if (failures) process.exitCode = 1;
}

main();
