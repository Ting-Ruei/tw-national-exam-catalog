#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明「在錯題討論區寫一則註解」不會把這一題踢出清單。
 *
 * 缺陷（2026-09-23，用真的 append_review + 折疊路徑量到）：錯題討論區的成員資格來自
 * 「這題最新的狀態是一個待複核的重置」。註解是 `action=comment`，而 `_reaffirm_standing_action`
 * 只會重申 `STANDING_ACTIONS` 裡的決定——`reset_review` 不在裡面。所以寫下「我為什麼卡住」
 * 的那則註解，會把這題從「卡住的清單」裡拿掉。**你愈解釋，它愈消失。**
 *
 * 這個檔案在**隔離的伺服器**上跑（自己的 candidates 與自己的事件檔），所以：
 *   * 它真的按了「儲存註解」——驗的是寫入路徑，不是畫面上的字；
 *   * 它讀回來確認那一題還在清單裡；
 *   * 它一筆都不會碰到 live 的事件檔。
 *
 * 用法：node scripts/test_v2_note_keeps_question.mjs [workdir]
 *   不給 workdir 時自己在 tmp 生一個，跑完刪掉。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const ROOT = path.resolve(decodeURIComponent(new URL('.', import.meta.url).pathname), '..');
const PORT = Number(process.env.NOTE_KEEPS_PORT || 8907);
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

/** 一題，處在「修復後待複核」——也就是會出現在錯題討論區的那種狀態。 */
const KEY = 'moex:115090:311:0704:1:question:q001';
const CANDIDATE = {
  candidate_key: KEY,
  question_number: 1,
  stem: '下列何者為莢膜的主要成分？',
  options: [
    { key: 'A', text: '多醣體' },
    { key: 'B', text: '蛋白質' },
    { key: 'C', text: '脂質' },
    { key: 'D', text: '核酸' },
  ],
  answer: 'A',
  metadata: { category_name: '測試科', subject_name: '微生物學', paper_id: '115090:311:0704' },
};
/** 那個把題目放進討論區的重置事件（修復管線寫的那一種）。 */
const RESET_EVENT = {
  action: 'reset_review',
  candidate_key: KEY,
  reviewer: 'codex-repair',
  repair_kind: 'backfill_repair',
  notes: '修復後待複核',
  created_at: '2026-09-23T08:20:05',
};

function makeWorkdir() {
  // 用一個只有一題的小 candidates 檔，而不是 198 MB 的那一份：這一條驗的是「註解與隊列的關係」，
  // 不是載入速度。真實語料的量測在 tests/test_review_ui_note.py 與 live 伺服器上做。
  const dir = process.env.NOTE_KEEPS_WORKDIR
    ? path.resolve(process.env.NOTE_KEEPS_WORKDIR)
    : fs.mkdtempSync(path.join(os.tmpdir(), 'note-keeps-'));
  const ui = path.join(dir, 'review-ui');
  fs.mkdirSync(ui, { recursive: true });
  fs.writeFileSync(path.join(ui, 'candidates.jsonl'),
    JSON.stringify(CANDIDATE) + '\n', 'utf-8');
  fs.writeFileSync(path.join(ui, 'question_review_events.jsonl'),
    JSON.stringify(RESET_EVENT) + '\n', 'utf-8');
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
  const { ui } = makeWorkdir();
  const server = spawn('python3', [
    path.join(ROOT, 'scripts', 'serve_question_review_ui.py'),
    '--candidate-jsonl', path.join(ui, 'candidates.jsonl'),
    '--review-log', path.join(ui, 'question_review_events.jsonl'),
    '--host', '127.0.0.1', '--port', String(PORT),
  ], { cwd: ROOT, stdio: 'ignore' });
  server.on('error', (err) => console.error('無法啟動伺服器：', err.message));

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-note-keeps-'));
  const debugPort = PORT + 1;
  let chrome;
  try {
    const up = await waitForServer();
    if (!up) {
      console.error('伺服器沒有起來；跳過。');
      process.exitCode = 1;
      return;
    }

    // 前提：這一題一開始真的在討論區裡（否則後面的斷言沒有意義）。
    const before = await getJson(`${base}/api/discuss`);
    const beforeKeys = (before.candidates || []).map((c) => c.candidate_key);
    check(beforeKeys.includes(KEY), '前提：這一題一開始在錯題討論區裡',
      `${beforeKeys.length} 題`);

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
      if (msg.method === 'Runtime.exceptionThrown') {
        exceptions.push(msg.params.exceptionDetails.text);
      }
    });
    const evaluate = async (expression) => {
      const r = await send('Runtime.evaluate',
        { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
      return r.result.value;
    };

    await send('Page.navigate', { url: `${base}/v2` });
    await sleep(4500);
    await evaluate(`document.querySelector('[data-area="discuss"]').click()`);
    await sleep(3500);

    // 真的在畫面上找那一題、把那則註解打進去、按「儲存註解」。
    const typed = await evaluate(`(async () => {
      const rows = Array.from(document.querySelectorAll('#discussList [data-i]'));
      if (!rows.length) return { ok: false, why: '清單是空的' };
      rows[0].click();
      await new Promise((r) => setTimeout(r, 1500));
      const box = document.getElementById('dNote');
      const save = document.getElementById('dNoteSave');
      if (!box || !save) return { ok: false, why: '找不到註解框或儲存鍵' };
      box.value = '我把 C 選項改成莢膜多醣，因為紙本第 3 頁寫的是莢膜';
      box.dispatchEvent(new Event('input', { bubbles: true }));
      save.click();
      await new Promise((r) => setTimeout(r, 2500));
      return { ok: true, rows: rows.length };
    })()`);
    check(typed.ok, '畫面上真的找得到那一題、打得進註解、按得到儲存', typed.why || `${typed.rows} 列`);

    // 伺服器端：註解真的寫進 append-only 事件檔了。
    const logPath = path.join(ui, 'question_review_events.jsonl');
    const lines = fs.readFileSync(logPath, 'utf-8').split('\n').filter((l) => l.trim());
    const events = lines.map((l) => JSON.parse(l));
    const notes = events.filter((e) => String(e.notes || '').includes('莢膜多醣'));
    check(notes.length === 1, '註解真的寫進 append-only 事件檔了', `${events.length} 筆事件`);
    check(events[0].action === 'reset_review', '原本那筆重置事件沒有被改寫（append-only）');

    // 這條是缺陷本身：寫完註解之後，那一題**還在**討論區嗎？
    const after = await getJson(`${base}/api/discuss`);
    const afterKeys = (after.candidates || []).map((c) => c.candidate_key);
    check(afterKeys.includes(KEY),
      '寫完註解後，那一題還在錯題討論區裡（這正是壞掉的那一條）',
      `寫之前 ${beforeKeys.length} 題，寫之後 ${afterKeys.length} 題`);

    const row = (after.candidates || []).find((c) => c.candidate_key === KEY) || {};
    const review = row.review || {};
    check(review.queue_bucket === 'repair_pending',
      '而且它還被歸在「修復後待複核」', String(review.queue_bucket));
    check(String(review.notes || '').includes('莢膜多醣'),
      '畫面上顯示的是我寫的那則註解', String(review.notes || '').slice(0, 24));

    check(exceptions.length === 0, '整段過程沒有 console 例外', exceptions.join(' / '));

    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('/tmp/note-keeps.png', Buffer.from(shot.data, 'base64'));
    console.log(`\n  截圖：/tmp/note-keeps.png`);
    ws.close();
  } finally {
    if (chrome) chrome.kill();
    server.kill();
    await sleep(400);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome 還在寫 profile */ }
  }

  console.log(failures ? `\n  有 ${failures} 項 BAD` : '\n  全部符合');
  process.exitCode = failures ? 1 : 0;
}

main().catch((err) => {
  console.error('稽核失敗：', err);
  process.exitCode = 1;
});
