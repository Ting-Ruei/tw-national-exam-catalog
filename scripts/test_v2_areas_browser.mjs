#!/usr/bin/env node
/* 用真的 Chrome 開真的 v2 頁，證明四個區真的在，而且每個區做自己那件事。
 *
 * 使用者要的是 首頁／題目審核區／答案審核區／錯題討論區。這個檔案把每一區的**可觀察行為**
 * 各釘一次——不是「按鈕存在」，而是「按下去畫面真的變成那一區、那一區真的讀到自己的資料」：
 *
 *   首頁        三個區各有一張卡，數字來自三個既有的伺服器端點，不是自己算的。
 *   題目審核區  基準線，開頁預設就在這裡（空 hash 不是首頁）。
 *   答案審核區  `/api/answer-candidates` 的每一張答案卡都列得出來；那一區寫入答案是走
 *               `answer-review-batch`，所以「按選項」只能改草稿、不能自己送。
 *   錯題討論區  `/api/correction-feedback` 的每個個案都列得出來，change_class 要看得見。
 *
 * 還釘一個容易被順手弄壞的契約：**換區不能清空另一區的 DOM**。答案區正在打字的註記，
 * 繞過首頁再回來必須還在。`hidden` 是對的，`innerHTML = ''` 是錯的。
 *
 * 用法：node scripts/test_v2_areas_browser.mjs http://127.0.0.1:8897
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const base = process.argv[2] || 'http://127.0.0.1:8897';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9338;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-areas-'));

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
  const goto = async (area) => {
    await evaluate(`document.querySelector('.area-btn[data-area="${area}"]').click(); true`);
    await sleep(1600);
  };

  // ---------------------------------------------------------------- 四個區都在，預設是題目審核區
  const nav = await evaluate(`
    Array.from(document.querySelectorAll('.area-btn')).map((n) => n.dataset.area)`);
  check(JSON.stringify(nav) === JSON.stringify(['home', 'question', 'answer', 'discuss']),
    '導覽列是 首頁／題目審核區／答案審核區／錯題討論區', JSON.stringify(nav));
  const initial = await evaluate(`({
    active: document.querySelector('.area-btn.on').dataset.area,
    shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
    hash: location.hash,
  })`);
  check(initial.active === 'question', '開頁預設停在題目審核區', initial.active);
  check(initial.shown.length === 1 && initial.shown[0] === 'areaQuestion',
    '而且只有題目審核區是可見的', JSON.stringify(initial.shown));

  // ---------------------------------------------------------------- 首頁
  await goto('home');
  const home = await evaluate(`({
    shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
    cards: Array.from(document.querySelectorAll('#homeCards .card')).map((n) => n.dataset.go),
    bigs: Array.from(document.querySelectorAll('#homeCards .big')).map((n) => n.textContent.trim()),
    hash: location.hash,
  })`);
  check(home.shown.length === 1 && home.shown[0] === 'areaHome', '按首頁真的切到首頁', JSON.stringify(home.shown));
  check(JSON.stringify(home.cards) === JSON.stringify(['question', 'answer', 'discuss']),
    '首頁三張卡各指向一個審核區', JSON.stringify(home.cards));
  check(home.bigs.every((b) => b !== '載入中…'), '首頁的數字是把端點的數讀出來印的', JSON.stringify(home.bigs));
  check(/^#首頁/.test(decodeURIComponent(home.hash)), '首頁在 hash 裡，重整會回到首頁', home.hash);

  // 首頁和大每一個區一樣：**畫一次**。繞去題目區再回來不該再打一次三個端點，也不該重建 DOM。
  // （以前 `showArea` 直接呼叫 `renderHome`，繞開 `A.rendered`，每次切回都重抓。）
  const homeReentry = await evaluate(`(async () => {
    const realFetch = window.fetch;
    let api = 0;
    window.fetch = (...args) => { if (String(args[0]).includes('/api/')) api += 1; return realFetch(...args); };
    document.querySelector('.area-btn[data-area="question"]').click();
    await new Promise((r) => setTimeout(r, 400));
    document.querySelector('.area-btn[data-area="home"]').click();
    await new Promise((r) => setTimeout(r, 1200));
    window.fetch = realFetch;
    return { api, cards: document.querySelectorAll('#homeCards .card').length };
  })()`);
  check(homeReentry.cards === 3, '再進首頁時卡片還在（不是被重畫掉）', String(homeReentry.cards));
  check(homeReentry.api === 0, '再進首頁不再重打三個端點（首頁也畫一次）',
    `${homeReentry.api} 次 /api 呼叫`);

  // ---------------------------------------------------------------- 答案審核區
  await goto('answer');
  const answer = await evaluate(`(async () => {
    for (let i = 0; i < 40 && document.querySelectorAll('#sheetList .sheet-row').length === 0; i += 1) {
      await new Promise((r) => setTimeout(r, 200));
    }
    const rows = Array.from(document.querySelectorAll('#sheetList .sheet-row'));
    return {
      shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
      sheets: rows.length,
      first: rows.length ? rows[0].textContent.trim().slice(0, 40) : '',
      table: document.querySelectorAll('#answerMain .atable tbody tr').length,
      hash: location.hash,
    };
  })()`);
  check(answer.shown.length === 1 && answer.shown[0] === 'areaAnswer', '按答案審核區真的切過去', JSON.stringify(answer.shown));
  check(answer.sheets >= 1, '答案卡列得出來', `${answer.sheets} 張：${answer.first}`);
  check(answer.table >= 1, '選一張答案卡會把它的每一列畫成表', `${answer.table} 列`);
  check(/^#答案/.test(decodeURIComponent(answer.hash)), '答案審核區在 hash 裡', answer.hash);

  // 一個關鍵的形狀：點答案選項只會改草稿，不會自己送決定。用真的點擊量。
  const draft = await evaluate(`(async () => {
    const button = document.querySelector('#answerMain [data-answer]');
    if (!button) return { clicked: false };
    const posts = [];
    const realFetch = window.fetch;
    window.fetch = (...args) => { posts.push(String(args[0])); return realFetch(...args); };
    const shownBefore = document.querySelector('#answerMain .ans-current').textContent.trim();
    button.click();
    await new Promise((r) => setTimeout(r, 300));
    const shownAfter = document.querySelector('#answerMain .ans-current').textContent.trim();
    window.fetch = realFetch;
    return { clicked: true, shownBefore, shownAfter, chosen: button.dataset.answer,
             posts: posts.filter((u) => /answer-review/.test(u)) };
  })()`);
  check(draft.clicked && draft.shownAfter === draft.chosen,
    '點一個答案選項會把它顯示成草稿', `${draft.shownBefore} → ${draft.shownAfter}`);
  check(draft.posts && draft.posts.length === 0,
    '但點選項不會自己送出任何審核（要按下面那排）', JSON.stringify(draft.posts));

  // ---------------------------------------------------------------- 換區不清空（隱藏，不是清空）
  const preserved = await evaluate(`(async () => {
    const note = document.querySelector('#answerMain textarea[data-note]');
    if (!note) return { has: false };
    note.value = '這一區的註記不該被換區清掉';
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

  // ---------------------------------------------------------------- 錯題討論區
  //
  // 這一區的契約在這一輪被**換掉**（不是修補）：它以前畫的是 `question_correction_feedback`
  // 的每個個案（人工修正的前後差異），而那讓「真正卡住的題」找不到。現在它只放卡住的題
  // ——人按過「阻擋」，或管線／AI 退回待複核——且每一題都給得起修的能力（編輯框、紙本、
  // AI 意見與它的截圖），以及兩個只有人能寫的面板：基本原則與代理的反問。
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
      principles: document.querySelectorAll('#discussMain .principles').length,
      repairQs: document.querySelectorAll('#discussMain .repair-qs').length,
      pdf: document.querySelectorAll('#discussPdf').length,
      emptyNote: (document.querySelector('#discussMain .empty-area') || {}).textContent || '',
      side: (document.querySelector('#discussSide .n') || {}).textContent || '',
      hash: location.hash,
    };
  })()`);
  check(discuss.shown.length === 1 && discuss.shown[0] === 'areaDiscuss', '按錯題討論區真的切過去', JSON.stringify(discuss.shown));
  // 有案子就驗案子；沒有案子就驗「沒有案子」有被說出來。兩者都要能通過。
  if (discuss.cases > 0) {
    console.log(`  討論區有 ${discuss.cases} 題卡住（列表 ${discuss.listRows} 列）`);
    check(discuss.listRows >= discuss.cases, '左欄的列數涵蓋中間的每個個案', `${discuss.listRows} 列`);
    check(discuss.stemBoxes === 1, '每一題都給得起題幹編輯框（這一區能修，不只是報告）', `${discuss.stemBoxes} 個`);
    check(discuss.optionBoxes >= 1, '選項也有編輯框', `${discuss.optionBoxes} 個`);
  } else {
    console.log('  討論區 0 題卡住（這個佇列沒有阻擋或退回）');
    check(discuss.emptyNote.trim().length > 0,
      '沒有卡住的題時會說出來，不是一片空白', discuss.emptyNote.trim().slice(0, 40));
  }
  check(discuss.principles === 1, '基本原則面板在（可加可減，會編進提示詞）');
  check(discuss.repairQs === 1, '修理代理的反問面板在（模型讀不懂時反問這裡）');
  check(discuss.pdf === 1, '右欄紙本在');
  check(discuss.side !== '', '側欄印出這一區的統計', `卡住 ${discuss.side} 題`);
  check(/^#錯題/.test(decodeURIComponent(discuss.hash)), '錯題討論區在 hash 裡', discuss.hash);

  // 討論區**只**放卡住的題。這一條是這一輪的核心修正：以前它畫整個可見佇列（79,090 題），
  // 真正卡住的幾百題反而找不到。用伺服器端的數量對照畫出來的列數。
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
    // 上限 500；伺服器回多少就畫多少（不套範圍，所以不該被 scope 砍掉）。
    check(discussScope.drawnRows >= Math.min(discussScope.serverRows, 500) - 5,
      '畫出來的列數跟著伺服器回的卡住題數', `${discussScope.drawnRows} vs ${discussScope.serverRows}`);
  }

  // ---------------------------------------------------------------- 題目審核區不能被弄壞
  await goto('question');
  const back = await evaluate(`({
    shown: Array.from(document.querySelectorAll('.area.on')).map((n) => n.id),
    rows: document.querySelectorAll('#listBody .row').length,
    hash: location.hash,
  })`);
  check(back.shown.length === 1 && back.shown[0] === 'areaQuestion',
    '回到題目審核區，而且只有它可見', JSON.stringify(back.shown));
  check(back.rows >= 1, '題目清單還是在（基準線沒被換區破壞）', `${back.rows} 列`);
  check(!/首頁|答案|錯題/.test(decodeURIComponent(back.hash)),
    '回到題目審核區，hash 回到沒有區前綴的舊寫法（書籤契約）', back.hash);

  await send('Page.captureScreenshot', {}).then((r) => {
    if (r && r.data) fs.writeFileSync('/tmp/v2_areas.png', Buffer.from(r.data, 'base64'));
  });
  console.log('  截圖：/tmp/v2_areas.png');
  console.log(failures ? `\n${failures} 項不符` : '\n全部符合');
  ws.close();
  chrome.kill();
  process.exit(failures ? 1 : 0);
}

main().catch((error) => { console.error(error); chrome.kill(); process.exit(2); });
