#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明「爭議題的紙本截圖」與「帶入修正」真的在畫面上。
 *
 * 使用者要的（b）是「把有疑義的題目截圖給 27B-splash 確認」，（c）是「爭議題直接送模型修，
 * 不是只回報」。兩者在顯示端各有一個前提，這個檔案各釘一次：
 *
 *   （b）模型看到的**同一張圖**要能被審題者看到。看不到的證據不是證據——一筆說「紙本印的是
 *        長」的 finding，如果那張紙打不開，審題者只能相信它。
 *   （c）修**要能帶進編輯框**，但不能自己送出去。按鈕把紙本讀法填進人工編輯框，寫下決定的還是
 *        按下「儲存修正」的人（`GOV-05`）。
 *
 * 用法：node scripts/test_v2_dispute_browser.mjs http://127.0.0.1:8899
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const base = process.argv[2] || 'http://127.0.0.1:8899';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9336;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-dispute-'));

const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--window-size=1700,1100', 'about:blank',
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

function getHead(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      res.resume();
      resolve({ status: res.statusCode, type: res.headers['content-type'] || '' });
    }).on('error', reject);
  });
}

let failures = 0;
function check(ok, label, detail) {
  console.log(`  ${ok ? 'ok ' : 'BAD'}  ${label}${detail ? `  ${detail}` : ''}`);
  if (!ok) failures += 1;
}

async function main() {
  let target;
  for (let i = 0; i < 40 && !target; i += 1) {
    await sleep(250);
    try {
      const list = await getJson(`http://127.0.0.1:${port}/json/list`);
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
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg.result); pending.delete(msg.id); }
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

  // 這條路徑一定要有 dispute population 的 finding（含 crop 與 changes）。
  const crops = await evaluate(`
    Array.from(document.querySelectorAll('#textSide .af-crop img')).map((n) => n.src)`);
  check(crops.length >= 1, '畫面上有「模型看到的紙本截圖」', `找到 ${crops.length} 張`);

  // （b）的實質檢查：那張圖**真的載得到**，不是一個壞掉的連結。
  if (crops.length) {
    const head = await getHead(crops[0]);
    check(head.status === 200, '截圖真的載得到（不是壞連結）', `HTTP ${head.status} ${head.type}`);
    check(/image\//.test(head.type), '而且回的是圖片', head.type);
  } else {
    check(false, '截圖真的載得到（不是壞連結）', '沒有圖可測');
  }

  const changes = await evaluate(`
    Array.from(document.querySelectorAll('#textSide .af-change')).map((n) => ({
      field: (n.querySelector('code') || {}).textContent || '',
      from: (n.querySelector('.af-from') || {}).textContent || '',
      to: (n.querySelector('.af-to') || {}).textContent || '',
      button: !!n.querySelector('.af-apply'),
    }))`);
  check(changes.length >= 1, '畫面上有「機械比對」的逐字差異', `${changes.length} 筆`);
  check(changes.every((c) => c.to.length > 0), '每一筆都寫出紙本讀法（to）');
  check(changes.every((c) => c.button), '每一筆差異旁都有「帶入修正」');

  // （c）的實質檢查：真的按下去，編輯框真的被填成紙本讀法，而且**沒有**送出任何決定。
  const applied = await evaluate(`(async () => {
    const before = document.querySelectorAll('.actions .act.is-on, .actions .act.saved').length;
    const button = document.querySelector('#textSide .af-apply');
    if (!button) return { clicked: false };
    const change = document.querySelector('#textSide .af-change');
    const field = button.dataset.field;
    const page = change.querySelector('.af-to').textContent;
    button.click();
    await new Promise((r) => setTimeout(r, 400));
    const id = field === 'stem' ? 'editStem' : ('editOpt_' + field.replace('option ', ''));
    const node = document.getElementById(id);
    return {
      clicked: true, field, page,
      editorOpen: !!document.querySelector('.edit.on'),
      value: node ? node.value : null,
      after: document.querySelectorAll('.actions .act.is-on, .actions .act.saved').length,
      before,
    };
  })()`);
  check(applied.clicked, '「帶入修正」按得下去');
  if (applied.clicked) {
    check(applied.editorOpen, '按下去會打開人工編輯框（修正還是人的動作）');
    check(applied.value && applied.value.includes(applied.page),
      '編輯框被填成紙本讀法', `field=${applied.field}`);
    check(applied.after === applied.before,
      '按下去沒有自動送出任何決定（GOV-05）', `${applied.before} → ${applied.after}`);
  }

  // 意見與量測分得開：同一頁上 dispute（紙本量測）與 ai-finding（模型意見）各有標題。
  const headers = await evaluate(`
    Array.from(document.querySelectorAll('#textSide .disputes-head, #textSide .af-head'))
      .map((n) => n.textContent.trim())`);
  console.log('  面板標題：', JSON.stringify(headers));
  check(headers.some((h) => /模型意見/.test(h)), '模型意見有自己的標題');

  await send('Page.captureScreenshot', {}).then((r) => {
    if (r && r.data) fs.writeFileSync('/tmp/v2_dispute.png', Buffer.from(r.data, 'base64'));
  });
  console.log('  截圖：/tmp/v2_dispute.png');
  console.log(failures ? `\n${failures} 項不符` : '\n全部符合');
  ws.close();
  chrome.kill();
  process.exit(failures ? 1 : 0);
}

main().catch((error) => { console.error(error); chrome.kill(); process.exit(2); });
