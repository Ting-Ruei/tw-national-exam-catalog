#!/usr/bin/env node
/* Drive the real v2 page in real Chrome (CDP) and click 只加註記 → type → 儲存.
 *
 * Why this and not a unit test: the defect was that the note box was in the DOM and *not visible*.
 * A test that reads the HTML cannot tell "the box exists" from "the reviewer can open it", so the
 * only proof is a browser pressing the button. This connects over the DevTools protocol, evaluates
 * the page's own functions in the page's own context, and reports what the screen shows.
 *
 * Usage: node scripts/test_v2_note_browser.mjs http://127.0.0.1:8799
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const base = process.argv[2] || 'http://127.0.0.1:8799';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9333;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-note-'));

const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--window-size=1600,1000', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

async function main() {
  let target;
  for (let i = 0; i < 40; i += 1) {
    try { target = (await getJson(`http://127.0.0.1:${port}/json/list`)).find((t) => t.type === 'page'); } catch { /* not up yet */ }
    if (target) break;
    await sleep(250);
  }
  if (!target) throw new Error('Chrome did not start');

  const WebSocket = (await import('node:worker_threads'), globalThis.WebSocket);
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  });
  await new Promise((r) => ws.addEventListener('open', r));
  const send = (method, params = {}) => new Promise((resolve) => {
    id += 1; pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: `${base}/v2` });
  await sleep(6000);

  const evaluate = async (expression) => {
    const res = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (res.result && res.result.exceptionDetails) throw new Error(JSON.stringify(res.result.exceptionDetails));
    return res.result && res.result.result ? res.result.result.value : undefined;
  };

  const report = [];
  const check = (name, ok, detail = '') => { report.push({ name, ok, detail }); };

  // 1. The page has a note control at all, and it is visible.
  check('有一顆「只加註記」按鈕',
    await evaluate(`!!document.getElementById('actNote')`));
  check('註記框預設是關的（不在畫面上）',
    await evaluate(`!document.getElementById('reasonBox').classList.contains('on')`));

  // 2. Pressing it (or the key) opens a box the reviewer can actually see and type in.
  await evaluate(`document.getElementById('actNote').click()`);
  await sleep(400);
  check('按下去之後註記框打開（display 不是 none）',
    await evaluate(`(() => { const b = document.getElementById('reasonBox');
      return b.classList.contains('on') && getComputedStyle(b).display !== 'none'; })()`));
  check('打開後游標在文字框裡（可以直接打字）',
    await evaluate(`document.activeElement === document.getElementById('reasonText')`));
  check('註記框說明它附在哪個決定上',
    (await evaluate(`document.getElementById('noteFor').textContent`) || '').length > 0,
    await evaluate(`document.getElementById('noteFor').textContent`));

  // 3. Type and save, through the page's own button handler.
  await evaluate(`(() => { const t = document.getElementById('reasonText');
    t.value = '紙本第 3 頁是「莢膜」，抽出成「莢膜」'; return true; })()`);
  await evaluate(`saveNote()`);
  await sleep(1500);

  check('儲存後註記框關起來',
    await evaluate(`!document.getElementById('reasonBox').classList.contains('on')`));
  const shown = await evaluate(`(document.body.innerText || '').includes('莢膜')`);
  check('註記顯示在題目旁邊（畫面上看得到）', shown);
  const hint = await evaluate(`document.getElementById('stateHint').textContent`);
  check('註記沒有把題目標成已決定', /只有註記|尚未決定/.test(hint || ''), hint);

  // 4. Reloading shows the note again — a note the reviewer cannot see again is one they write twice.
  await send('Page.navigate', { url: `${base}/v2` });
  await sleep(6000);
  const afterReload = await evaluate(
    `(() => { S.rows.length ? go(0) : null; return true; })()`);
  await sleep(1200);
  check('重載後註記還在（讀得回來）',
    await evaluate(`(document.body.innerText || '').includes('莢膜')`));

  // 5. And a decision still counts as a decision. `decide()` advances to the next question, so the
  //    state has to be read *back on the question that was decided* rather than off the screen - a
  //    hint that had moved on to the next row would be a false pass.
  const decidedKey = await evaluate(`S.rows[S.index].candidate_key`);
  await evaluate(`decide('accept')`);
  await sleep(1200);
  check('按「確認正常」後已離開（沒有原地不動）',
    (await evaluate(`S.rows[S.index].candidate_key`)) !== decidedKey);
  const backHint = await evaluate(
    `(() => { const at = S.rows.findIndex((r) => r.candidate_key === ${JSON.stringify(decidedKey)});
       if (at < 0) return '(不在清單裡)';
       go(at);
       return document.getElementById('stateHint').textContent; })()`);
  check('回頭看那題已標記「確認正常」', /確認正常/.test(backHint || ''), backHint);

  console.log('');
  let failed = 0;
  for (const r of report) {
    console.log(`  ${r.ok ? 'ok  ' : 'FAIL'} ${r.name}${r.detail ? `  ${r.detail}` : ''}`);
    if (!r.ok) failed += 1;
  }
  console.log('');
  console.log(failed ? `${failed} 項不符` : '全部符合');
  ws.close();
  chrome.kill();
  await sleep(500);
  fs.rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 200 });
  process.exit(failed ? 1 : 0);
}

main().catch(async (err) => {
  console.error(err);
  chrome.kill();
  await sleep(500);
  fs.rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 200 });
  process.exit(2);
});
