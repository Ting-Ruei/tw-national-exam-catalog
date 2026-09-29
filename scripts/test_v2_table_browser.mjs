#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明「文字型表格」在畫面上是**紙本的截圖**，不是壓平的一串字。

 * 使用者原文：「叫你這種文字型表格要用截圖來顯示，聽不懂嗎」（2026-09-24）。原本的樣子是抽取檔的
 * 字（`劑型  給藥途徑  劑量（mg）  AUC (μg．h/mL) 錠劑  口服  100  40…`）——欄位之間只剩兩個空格，
 * 人得自己數字數；紙本上那張表機器已經依讀法裁下來了，卻沒有人畫它。
 *
 * 這一支量的是**畫面**，不是原始碼。同一個 base 上四種形狀都量（三支本機佇列＋live 鏡像各跑一次）：
 *
 *   1. 有 `paper-table` 裁切的那一題，題幹那一格**不再**是壓平的表格文字（這是使用者抱怨的那一件事，
 *      所以它是一個會在今天以前的程式上失敗的檢查）。
 *   2. 表格的位置上畫的是那張截圖，而且截圖**真的載得到**（HTTP 200、image/*）——看不到的證據
 *      不是證據。
 *   3. 抽取到的原字還在，收在截圖底下的「文字版」（`details[data-view="table-text"]`），而且是
 *      **題目自己那串字**（原樣，逐字；判準是它必須是那一列 stem 的後綴——重排過的版本不會是）。
 *   4. 那張截圖只畫一次（下方「圖片（機器裁切）」那塊不再重複同一張）。
 *   5. 控制組之一：**沒有** `paper-table` 裁切的題目，題幹文字必須與伺服器送來的一字不差。
 *   6. 控制組之二：有裁切但讀法引的行在題幹裡**找不到**（舊讀法留下的裁切），整段照原本畫、截圖
 *      補在後面，而且不可以畫出一個空的「文字版」。
 *   7. 題幹**整段就是那張表**（切點在最前面）時，題幹那一格留空，截圖與文字版帶著全部內容。
 *
 * 用法：
 *   node scripts/test_v2_table_browser.mjs http://127.0.0.1:8899
 *
 * 備註：本機佇列若不在專案樹裡，伺服器要能讀到那些 PNG——`/file` 只允許 `ASSET_ROOT` 底下
 * （見 `review_ui/paths.py`），所以另外開一個 `REVIEW_UI_ADDITIONAL_ASSET_ROOTS=<佇列根>`。
 * 退出碼：0＝全部符合；1＝有 BAD。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const base = process.argv[2] || 'http://127.0.0.1:8899';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9337;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-table-'));

const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--window-size=1700,1100', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(url) {
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

const squash = (value) => String(value || '').replace(/\s+/g, '');

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
  const pageErrors = [];
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.method === 'Runtime.exceptionThrown') {
      pageErrors.push((msg.params.exceptionDetails || {}).text || 'exception');
    } else if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      pageErrors.push((msg.params.args || []).map((a) => a.value || a.description || '').join(' '));
    }
  });
  await send('Page.navigate', { url: `${base}/v2` });
  await sleep(4500);

  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'page error');
    return result.result.value;
  };

  // 畫面上那一題是**頁面自己說**的那一題（不是這一支挑的），它的完整列再跟伺服器要一次：
  // 判準必須是伺服器送來的字，不是這一支自己抄的一份。
  const shown = await evaluate(`(() => {
    const item = S.rows[S.index];
    return item ? item.candidate_key : '';
  })()`);
  if (!shown) throw new Error('畫面上沒有題目');
  const row = await evaluate(`(async () => {
    const payload = await (await fetch('/api/candidates?focusKey=' + encodeURIComponent(${JSON.stringify(shown)}))).json();
    const list = Array.isArray(payload) ? payload : (payload.candidates || payload.rows || []);
    return list.find((r) => r.candidate_key === ${JSON.stringify(shown)}) || null;
  })()`);
  if (!row) throw new Error(`/api/candidates 沒有回來 ${shown}`);
  const tableRefs = (row.image_refs || []).filter((ref) => ref && ref.label === 'paper-table'
    && ref.asset_role !== 'option-image');
  console.log(`  題目 ${shown}；${tableRefs.length ? '有' : '沒有'} paper-table 裁切`
    + `；image_refs ${(row.image_refs || []).length} 筆`);

  const drawn = await evaluate(`(() => {
    const side = document.getElementById('textSide');
    const stem = document.getElementById('viewStem');
    const block = side.querySelector('.paper-table');
    const details = side.querySelector('details[data-view="table-text"]');
    return {
      stemText: stem ? stem.textContent : null,
      sideText: side.textContent,
      tableImgs: Array.from(side.querySelectorAll('.paper-table img')).map((n) => n.src),
      belowImgs: Array.from(side.querySelectorAll('.crops img')).map((n) => n.src),
      block: !!block,
      details: !!details,
      detailsOpen: details ? details.open : null,
      detailsRaw: details && details.querySelector('.paper-table-raw')
        ? details.querySelector('.paper-table-raw').textContent : null,
      detailsSummary: details && details.querySelector('summary')
        ? details.querySelector('summary').textContent : null,
      tableRefPaths: ${JSON.stringify((row.image_refs || []).map((r) => String(r.path || '')))},
    };
  })()`);

  if (!tableRefs.length) {
    /* 控制組：沒有紙本表格的題目，題幹照原本那樣畫（一個字都不可以少）。 */
    check(drawn.block === false, '沒有紙本表格的題目不會多出表格區塊');
    check(squash(drawn.stemText) === squash(row.stem),
      '沒有紙本表格的題目，題幹文字與伺服器送來的一字不差', `${squash(drawn.stemText).length} 字`);
    check(!/paper-table/.test(drawn.sideText), '這一題的畫面上沒有表格裁切');
  } else if (!drawn.details) {
    /* 有裁切，但讀法引的行在這一列的題幹裡找不到（裁切是另一份讀法留下的）：整段照原本畫，
       截圖補在後面，而且不可以畫一個空的「文字版」。這是這支的第一個後備，實測的資料是
       `/tmp/qbr-table-proof-fallback`（把裁切的 `table_lines` 換成另一張表的行）。 */
    check(drawn.block, '切不出表格時仍然畫出表格那一塊');
    check(squash(drawn.stemText) === squash(row.stem),
      '切不出表格時，題幹整段照原本畫（一個字都不少）', `${squash(drawn.stemText).length} 字`);
    check(drawn.tableImgs.length === tableRefs.length,
      '表格的位置上畫的是那張截圖', `${drawn.tableImgs.length} 張`);
    if (drawn.tableImgs.length) {
      const head = await getHead(drawn.tableImgs[0]);
      check(head.status === 200 && /image\//.test(head.type),
        '截圖真的載得到（不是壞連結）', `HTTP ${head.status} ${head.type}`);
    }
    check(!/文字版/.test(drawn.sideText), '沒有畫出一個空的「文字版」');
  } else {
    /* 使用者抱怨的那一件事：壓平的表格文字不可以再當成題幹畫出來。 */
    check(drawn.details, '抽取到的原字收在「文字版」裡（還是看得到）');
    check(/文字版/.test(String(drawn.detailsSummary || '')),
      '那個開關寫的是「文字版」', String(drawn.detailsSummary || ''));
    const raw = String(drawn.detailsRaw || '');
    // 逐字：那段字必須是這一列 stem 的**後綴**。重排、接成一行、或換成模型讀法的版本都不會是後綴。
    const verbatim = !!drawn.details && raw.length > 0 && row.stem.endsWith(raw);
    check(verbatim, '文字版是題目自己那串抽取文字（原樣，逐字）', `${raw.length} 字`);
    // 題幹那一格＝這一列 stem 減掉表格那一段：散文一個字都不可以少，表格那一段一個字都不可以留。
    const expectedProse = verbatim ? row.stem.slice(0, row.stem.length - raw.length) : row.stem;
    check(squash(drawn.stemText) === squash(expectedProse),
      '題幹那一格只剩散文（表格那一段已讓位給截圖）',
      `題幹 ${squash(drawn.stemText).length} 字／整列 ${squash(row.stem).length} 字`);
    // 讀法引的資料列（紙本與抽取檔逐字相同的那幾列）不可以還躺在題幹裡。
    const leaked = (tableRefs[0].table_lines || []).slice(1)
      .filter((line) => squash(drawn.stemText).includes(squash(line)));
    check(leaked.length === 0, '表格的資料列不再是題幹文字', leaked.join('／') || '沒有漏');

    check(drawn.tableImgs.length === tableRefs.length,
      '表格的位置上畫的是那張截圖', `${drawn.tableImgs.length} 張`);
    if (drawn.tableImgs.length) {
      const head = await getHead(drawn.tableImgs[0]);
      check(head.status === 200 && /image\//.test(head.type),
        '截圖真的載得到（不是壞連結）', `HTTP ${head.status} ${head.type}`);
    }
    // 只畫一次：下方「圖片（機器裁切）」那塊不可以再出現同一張。
    const dupes = drawn.belowImgs.filter((src) => /paper-table/.test(src));
    check(dupes.length === 0, '同一張表格截圖沒有被畫第二次', `下方那塊 ${drawn.belowImgs.length} 張`);

    check(drawn.detailsOpen === false, '「文字版」預設收起來（第一眼看到的是截圖）');
    if (drawn.details) {
      const opened = await evaluate(`(async () => {
        const details = document.querySelector('#textSide details[data-view="table-text"]');
        details.open = true;
        await new Promise((r) => setTimeout(r, 150));
        const box = details.querySelector('.paper-table-raw');
        return box ? box.getBoundingClientRect().height : 0;
      })()`);
      check(opened > 0, '打開「文字版」真的看得到那段字', `${Math.round(opened)} px`);
    }
  }

  check(pageErrors.length === 0, '整個過程沒有 console 例外', pageErrors.join(' | '));

  await send('Page.captureScreenshot', {}).then((r) => {
    if (r && r.data) fs.writeFileSync('/tmp/v2_table.png', Buffer.from(r.data, 'base64'));
  });
  console.log('  截圖：/tmp/v2_table.png');
  console.log(failures ? `\n${failures} 項不符` : '\n全部符合');
  ws.close();
  chrome.kill();
  process.exit(failures ? 1 : 0);
}

main().catch((error) => { console.error(error); chrome.kill(); process.exit(2); });
