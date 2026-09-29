#!/usr/bin/env node
/* 用真的鍵盤打字進註記框，證明「打得出字、Enter 存、Shift+Enter 換行」。
 *
 * 為什麼不能只設 `reasonText.value = '...'`：使用者回報的正是「打不進去」。直接給 value 是
 * 繞過瀏覽器的輸入路徑——被 CSS、disabled、focus 或 keydown 攔掉都照樣過關。所以這裡送真的
 * `Input.dispatchKeyEvent`，每個字元都經過瀏覽器。
 *
 * Usage: node scripts/test_v2_note_typing.mjs http://127.0.0.1:8799
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const base = process.argv[2] || 'http://127.0.0.1:8799';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9334;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-type-'));

const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--window-size=1600,1000', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJson = (url) => new Promise((resolve, reject) => {
  http.get(url, (res) => { let b = ''; res.on('data', (c) => { b += c; });
    res.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } }); }).on('error', reject);
});

async function main() {
  let target;
  for (let i = 0; i < 40; i += 1) {
    try { target = (await getJson(`http://127.0.0.1:${port}/json/list`)).find((t) => t.type === 'page'); } catch { /* not up */ }
    if (target) break;
    await sleep(250);
  }
  if (!target) throw new Error('Chrome did not start');

  const WebSocket = (await import('node:worker_threads'), globalThis.WebSocket);
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.addEventListener('message', (e) => { const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  await new Promise((r) => ws.addEventListener('open', r));
  const send = (method, params = {}) => new Promise((resolve) => {
    id += 1; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });

  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate', { url: `${base}/v2` });
  await sleep(6000);

  const evaluate = async (expression) => {
    const res = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (res.result && res.result.exceptionDetails) throw new Error(JSON.stringify(res.result.exceptionDetails));
    return res.result && res.result.result ? res.result.result.value : undefined;
  };

  // 打「真的」鍵：rawKeyDown + char + keyUp。用 rawKeyDown 而不是 keyDown，因為 keyDown 帶 text
  // 自己會插入一次字元，再送 char 就變成兩個字——第一版 harness 就是這樣打出「紙紙本本」。
  const typeText = async (text) => {
    for (const ch of text) {
      await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: ch });
      await send('Input.dispatchKeyEvent', { type: 'char', text: ch, unmodifiedText: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
      await sleep(12);
    }
  };
  const pressKey = async (key, code, modifiers = 0) => {
    await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key, code, windowsVirtualKeyCode: code === 'Enter' ? 13 : 0, modifiers });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: code === 'Enter' ? 13 : 0, modifiers });
    await sleep(250);
  };

  const report = [];
  const check = (name, ok, detail = '') => report.push({ name, ok, detail });

  // 先跳到一個**還沒有註記**的題目。註記框在已有註記的題目上會把舊註記讀進來（那是對的：
  // 人想接續修改），但這個測試在量「打字」本身，所以要在空白框上量。
  const freshRow = await evaluate(`(() => {
    const at = S.rows.findIndex((r) => !S.notes.has(r.candidate_key));
    if (at >= 0) go(at);
    return at; })()`);
  await sleep(600);
  check('找到一題沒有註記的題目來測打字', freshRow >= 0, String(freshRow));
  check('這一題的註記框確實是空的（不是載入舊註記）',
    await evaluate(`document.getElementById('reasonText').value === '' ||
      !document.getElementById('reasonBox').classList.contains('on')`));

  await evaluate(`document.getElementById('actNote').click()`);
  await sleep(400);

  // 1. 真的打字，字要進到框裡。
  await typeText('紙本第3頁為莢膜');
  const typed = await evaluate(`document.getElementById('reasonText').value`);
  check('真的用鍵盤打字，字進得去註記框', typed === '紙本第3頁為莢膜', JSON.stringify(typed));

  // 2. Shift+Enter 換行。
  await typeText('，抽出成莢膜');
  await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, modifiers: 8 });
  await send('Input.dispatchKeyEvent', { type: 'char', text: '\r', modifiers: 8 });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, modifiers: 8 });
  await sleep(300);
  await typeText('第二行');
  const multiline = await evaluate(`document.getElementById('reasonText').value`);
  check('Shift+Enter 是換行，不是儲存', multiline.split('\n').length === 2, JSON.stringify(multiline));

  // 3. 只是 Enter 就儲存。
  const before = await evaluate(`document.getElementById('reasonBox').classList.contains('on')`);
  await pressKey('Enter', 'Enter');
  await sleep(1500);
  const after = await evaluate(`document.getElementById('reasonBox').classList.contains('on')`);
  check('（前置）Enter 前註記框是開的', before === true);
  check('Enter 儲存並關閉註記框', after === false);
  const saved = await evaluate(`(document.body.innerText || '').includes('莢膜')`);
  check('儲存的註記顯示在畫面上', saved);

  // 4. 使用者要求的第二種存法：滑鼠停在按鈕上，再按一次「只加註記」就存起來。
  await evaluate(`document.getElementById('actNote').click()`);
  await sleep(400);
  await typeText('再按一次也要存');
  await evaluate(`document.getElementById('actNote').click()`);
  await sleep(1500);
  check('再按一次「只加註記」也會儲存',
    await evaluate(`!document.getElementById('reasonBox').classList.contains('on')`));
  check('第二次的註記也看得到',
    await evaluate(`(document.body.innerText || '').includes('再按一次也要存')`));

  // 5. 空白的框按第二次只是收起，不該跳錯誤（「我只是想關掉」不是失敗）。
  await evaluate(`document.getElementById('actNote').click()`);
  await sleep(400);
  await evaluate(`document.getElementById('actNote').click()`);
  await sleep(600);
  check('空框再按一次只是關閉，沒有錯誤訊息',
    await evaluate(`!document.getElementById('reasonBox').classList.contains('on')
      && !/沒有寫任何註記/.test(document.getElementById('toast').textContent || '')`));

  console.log('');
  let failed = 0;
  for (const r of report) {
    console.log(`${r.ok ? ' ok ' : 'FAIL'}  ${r.name}${r.ok ? '' : `  ← ${r.detail}`}`);
    if (!r.ok) failed += 1;
  }
  console.log(`\n${report.length - failed}/${report.length} 通過`);
  try { chrome.kill(); } catch { /* fine */ }
  process.exit(failed ? 1 : 0);
}

main().catch((error) => { console.error(error); try { chrome.kill(); } catch { /* fine */ } process.exit(2); });
