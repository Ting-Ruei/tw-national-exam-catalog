#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明左邊那兩個 chip 真的分開、數字真的不同。
 *
 * 為什麼不是讀 HTML 的單元測試：單元測試只證明檔案裡有那幾個字。這裡要證明的是
 * **審題者在畫面上看得見兩個 chip，而且按下去會篩出不同的題目**——也就是使用者要的
 * 「我才能知道哪些是 AI 退回來的、哪些是我自己擋的」。
 *
 * Usage: node scripts/test_v2_returned_chip_browser.mjs http://127.0.0.1:8799
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const base = process.argv[2] || 'http://127.0.0.1:8799';
// 一個同時有人擋與機器退回的卷。scope 由 URL hash 決定，所以直接開到那卷，
// 不依賴瀏覽器上一輪存下來的偏好（那會讓驅動程式跑到另一卷、兩個 chip 都是 0）。
const paper = process.argv[3] || '';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9334;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-chip-'));

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
  await send('Page.navigate', { url: `${base}/v2${paper ? `#${paper}` : ''}` });
  await sleep(4500);

  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'page error');
    return result.result.value;
  };

  const chips = await evaluate(`
    Array.from(document.querySelectorAll('#chips .chip')).map((c) => ({
      view: c.dataset.view, label: c.textContent.trim(),
      count: (c.querySelector('.n') || {}).textContent || '',
    }))`);
  console.log('  chip 清單：');
  for (const chip of chips) console.log(`    ${chip.view}\t${chip.label}\t${chip.count}`);
  const byView = Object.fromEntries(chips.map((c) => [c.view, c]));

  check(!!byView.flagged, '有「我擋的・需重看」chip', byView.flagged?.label);
  check(!!byView.returned, '有「AI・管線退回」chip', byView.returned?.label);
  check(byView.flagged?.label !== byView.returned?.label, '兩個 chip 的字不一樣');
  check(byView.flagged?.count !== byView.returned?.count || (byView.flagged?.count === '0'),
    '兩個 chip 的數字不同（或都真的沒有）', `${byView.flagged?.count} vs ${byView.returned?.count}`);

  // 真的按下去，看篩出來的題目屬於哪一種。觸發 chip 自己的 `onclick`（v2.html 就是在那裡綁的），
  // 不是對 input 送 change——送 change 不會重建清單，會得到上一輪的列。
  const clickAndRead = async (view) => {
    await evaluate(`
      (() => { const c = document.querySelector('#chips .chip[data-view="${view}"]');
               c.onclick(); })()`);
    await sleep(1200);
    return evaluate(`
      Array.from(document.querySelectorAll('#listBody .row')).map((r) => ({
        text: r.textContent.trim().slice(0, 24),
        cls: r.className,
      }))`);
  };

  const flaggedRows = await clickAndRead('flagged');
  const returnedRows = await clickAndRead('returned');
  console.log(`  「我擋的」篩出 ${flaggedRows.length} 列，第一列 class=${flaggedRows[0]?.cls || '（無）'}`);
  console.log(`  「退回」篩出 ${returnedRows.length} 列，第一列 class=${returnedRows[0]?.cls || '（無）'}`);

  check(flaggedRows.every((r) => r.cls.includes('flag')), '人擋的列都有 flag 記號');
  check(returnedRows.every((r) => r.cls.includes('returned')), '退回的列都有 returned 記號');
  check(!returnedRows.some((r) => r.cls.includes('flag')), '退回的列沒有被畫成人擋的顏色');
  if (flaggedRows.length && returnedRows.length) {
    // 負對照的核心：如果兩個 chip 其實是同一個，篩出來的清單會一模一樣。
    const a = flaggedRows.map((r) => r.text).join('|');
    const b = returnedRows.map((r) => r.text).join('|');
    check(a !== b, '兩個 chip 篩出的清單不是同一份');
  }

  // 退回的題目要說出為什麼，否則只是一個標籤。
  if (returnedRows.length) {
    const hint = await evaluate(`document.querySelector('#stateHint').textContent`);
    console.log(`  明細提示：${hint}`);
    check(hint.includes('AI／管線退回'), '退回的題目標成 AI／管線退回', hint.slice(0, 60));
    check(hint.includes('原為') || hint.includes('修復') || hint.includes('退回'),
      '退回的題目說得出原因', hint.slice(0, 80));
  }
  const hintFlagged = await (async () => {
    await clickAndRead('flagged');
    return evaluate(`document.querySelector('#stateHint').textContent`);
  })();
  console.log(`  人擋的明細提示：${hintFlagged}`);
  check(hintFlagged.includes('阻擋') || hintFlagged.includes('需重看'),
    '人擋的題目標成阻擋／需重看');

  ws.close();
  chrome.kill();
  fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5 });
  console.log(failures ? `\n${failures} 項不符` : '\n全部符合');
  process.exit(failures ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  chrome.kill();
  process.exit(1);
});
