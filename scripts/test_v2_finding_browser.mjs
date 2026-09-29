#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明「模型對這一題的意見」真的畫在畫面上。
 *
 * 為什麼不是讀 HTML 的單元測試：單元測試只證明檔案裡有 `findingHtml` 這幾個字。這裡要證明的是
 * **審題者在畫面上看得見模型說了什麼、是哪個模型、以及這一題是怎麼到模型面前的**——也就是
 * 使用者要的「（b）把它截圖給 27B-splash 確認、（c）爭議題直接送模型修、不是只回報」的
 * 顯示端前提：如果 61,000 筆 finding 沒有出現在 UI 上，整條迴圈的產物就沒有人看得到。
 *
 * 用法：node scripts/test_v2_finding_browser.mjs http://127.0.0.1:8897
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const base = process.argv[2] || 'http://127.0.0.1:8897';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9335;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-finding-'));

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
  await send('Page.navigate', { url: `${base}/v2` });
  await sleep(4500);

  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'page error');
    return result.result.value;
  };

  // 這條路徑一定要有 finding：呼叫端傳的是只有 20 題、每題都有 finding 的小佇列。
  const shown = await evaluate(`
    Array.from(document.querySelectorAll('#textSide .ai-finding')).map((n) => ({
      head: (n.querySelector('.af-head') || {}).textContent || '',
      text: n.textContent || '',
    }))`);
  check(shown.length >= 1, '畫面上有「模型意見」區塊', `找到 ${shown.length} 個`);

  const text = shown.map((s) => s.text).join('\n');
  check(/模型意見/.test(text), '區塊標題寫出這是模型意見');
  check(/哪裡：/.test(text), '說出「哪裡」');
  check(/怎麼修：/.test(text), '說出「怎麼修」');
  check(/splash|mtplx|Qwen|model|模型/.test(text), '寫出是哪個模型回答的（可查證）', '');

  // 顯示端最容易被做錯的一點：模型意見與紙本量測要分得開。disputes 區塊是量測，
  // ai-finding 是意見——兩個同時存在時，頁面上要有兩種不同的標題。
  const headers = await evaluate(`
    Array.from(document.querySelectorAll('#textSide .disputes-head, #textSide .af-head'))
      .map((n) => n.textContent.trim())`);
  console.log('  面板標題：', JSON.stringify(headers));
  const afHead = headers.filter((h) => /模型意見/.test(h));
  check(afHead.length >= 1, '模型意見有自己的標題（不是借用爭議的標題）', `${afHead.length}`);

  // 意見不是決定：畫面在顯示 finding 之後，按鈕仍然只有人類的四個動作。
  const actions = await evaluate(`
    Array.from(document.querySelectorAll('.actions .act')).map((b) => b.textContent.trim())`);
  check(actions.some((a) => /確認正常/.test(a)), '人類動作「確認正常」還在');
  check(actions.some((a) => /阻擋/.test(a)), '人類動作「阻擋」還在');
  check(!actions.some((a) => /套用模型|採用模型|讓模型決定/.test(a)),
    '沒有「讓模型決定」這種按鈕（GOV-05：模型只能 advisory）');

  await send('Page.captureScreenshot', {}).then((r) => {
    if (r && r.data) fs.writeFileSync('/tmp/v2_finding.png', Buffer.from(r.data, 'base64'));
  });
  console.log('  截圖：/tmp/v2_finding.png');
  console.log(failures ? `\n${failures} 項不符` : '\n全部符合');
  ws.close();
  chrome.kill();
  process.exit(failures ? 1 : 0);
}

main().catch((error) => { console.error(error); chrome.kill(); process.exit(2); });
