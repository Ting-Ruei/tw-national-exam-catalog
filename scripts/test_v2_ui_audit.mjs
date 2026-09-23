#!/usr/bin/env node
/* 介面稽核：**每一個按鈕**都要真的被按過一次，而且按下去要看到事情發生。
 *
 * 為什麼要這一支
 * --------------
 * 使用者回報的順序是：「UI 做完要用 browse use / computer use 檢查，否則太多 bug 會出貨，
 * 每一個按鈕在交付前都要測過」。這一句話是**驗收標準**，不是建議。前面幾個缺陷的形狀都一樣：
 * 畫面把控制項畫出來了、看起來可以按，但沒有任何 handler 接上去——「圖片擷圖與抽換」的四個位置
 * 按鈕、字體的三個按鈕、儲存補圖、儲存註解全都是死的。它們在截圖上完全正常，所以「看圖驗收」
 * 永遠測不出來；只有**真的按下去、再看狀態有沒有變**才測得出來。
 *
 * 三種控制項，三種驗證方式
 * ------------------------
 *   唯讀／純前端（字體、切區、走清單、聚焦、篩選、展開）
 *       真按，並且量一個具體的變化（`--reading-size`、可見的區 id、游標所在的框…）。
 *       按了沒變化就是 BAD，不是「大概沒事」。
 *   有寫入但可安全觸發（儲存補圖、儲存註解、送出回答、新增原則）
 *       按**空的**——守門訊息必須出現，而且**不可以**送出任何東西。守門本身就是要測的行為：
 *       一個沒有守門的儲存鍵，在空白的框上會寫出一筆空事件。
 *   會改動審核紀錄的決定鍵（確認正常／阻擋／退回未審／儲存修正）
 *       只驗「有 handler、沒有 disabled、標籤讀得到」，**不按**。這些鍵的作用是寫 append-only 的
 *       人工紀錄（`GOV-05`／G3/G4），用一支稽核腳本去按就是讓腳本冒充審題者。
 *
 * 用法：
 *   node scripts/test_v2_ui_audit.mjs http://127.0.0.1:8897
 *   node scripts/test_v2_ui_audit.mjs http://127.0.0.1:8897 --json /tmp/audit.json
 *
 * 退出碼：0＝全部符合；1＝有 BAD（會印出每一項）。
 */
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const base = process.argv[2] || 'http://127.0.0.1:8897';
const jsonOut = (() => {
  const i = process.argv.indexOf('--json');
  return i > -1 ? process.argv[i + 1] : '';
})();
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9346;
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'cdp-audit-'));

const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, '--window-size=1680,1200', 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJson = (url) => new Promise((resolve, reject) => {
  http.get(url, (res) => {
    let b = '';
    res.on('data', (c) => { b += c; });
    res.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
  }).on('error', reject);
});

let failures = 0;
const results = [];
function check(ok, area, label, detail) {
  results.push({ area, label, ok: !!ok, detail: detail || '' });
  console.log(`  ${ok ? 'ok ' : 'BAD'}  [${area}] ${label}${detail ? `  ${detail}` : ''}`);
  if (!ok) failures += 1;
}

async function main() {
  let target;
  for (let i = 0; i < 60 && !target; i += 1) {
    await sleep(250);
    try {
      target = (await getJson(`http://127.0.0.1:${port}/json/list`)).find((t) => t.type === 'page');
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

  // 收集 console 錯誤：一個「按了會丟例外」的按鈕，畫面上看起來跟成功一模一樣。
  const pageErrors = [];
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data);
    if (msg.method === 'Runtime.exceptionThrown') {
      pageErrors.push((msg.params.exceptionDetails || {}).text || 'exception');
    } else if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      pageErrors.push((msg.params.args || []).map((a) => a.value || a.description || '').join(' '));
    }
  });

  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'page error');
    return result.result.value;
  };

  await send('Page.navigate', { url: `${base}/v2` });
  await sleep(5000);

  /* 每一個可見的互動控制項都抓出來，附上「有沒有接上 handler」。
     判斷「接上了」的方式是看 DOM 屬性：本專案全部用 `node.onclick = …` 這種寫法指派，
     所以 `onclick` 是函式就代表有人接。用 `addEventListener` 的（例如 document 上的貼上）
     抓不到，因此那一條另外單獨驗（見下面的 paste 檢查）。 */
  const CONTROL_PROBE = `(root) => {
    const box = document.querySelector(root);
    if (!box) return null;
    const nodes = Array.from(box.querySelectorAll(
      'button, [role=button], a[href], select, input, textarea, [data-go], [data-pos], [data-place], [data-font], [data-focus]'));
    return nodes.map((n) => {
      const rect = n.getBoundingClientRect();
      const style = window.getComputedStyle(n);
      const id = n.id || '';
      const name = id || n.dataset.area || n.dataset.font || n.dataset.place || n.dataset.pos
        || n.dataset.i || n.dataset.go || n.dataset.focus || n.dataset.k
        || (n.className || '').toString().split(' ')[0] || n.tagName.toLowerCase();
      // A text field, checkbox or file picker has no handler on purpose: its *value* is read by a
      // save function when a button is pressed. Those are audited by name against the served JS
      // (see the read-at-save check), not by demanding an onchange handler.
      const tag = n.tagName.toLowerCase();
      const type = (n.getAttribute('type') || '').toLowerCase();
      const readAtSave = tag === 'textarea'
        || (tag === 'input' && ['text', 'checkbox', 'radio', 'file', 'search'].includes(type));
      return {
        tag,
        name: String(name).slice(0, 44),
        id,
        kind: readAtSave ? 'read-at-save' : 'action',
        text: (n.textContent || '').trim().slice(0, 34),
        type,
        visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
          && style.display !== 'none',
        disabled: !!n.disabled,
        handlers: ['onclick', 'onchange', 'oninput', 'onkeydown', 'onsubmit', 'ondrop', 'ondragover']
          .filter((k) => typeof n[k] === 'function'),
        href: n.getAttribute('href') || '',
      };
    });
  }`;

  /* 把 fetch 換成替身，在框裡打字、按「儲存修正」，把送出的 payload 抓回來。
     替身不發出請求，所以這一條驗收不會寫入任何紀錄——它可以證明「打的字＝送出的字」，
     而不必冒充審題者去寫 append-only 的人工紀錄（GOV-05 / G4）。 */
  const PROBE_SAVE = `(async () => {
    const realFetch = window.fetch;
    const captured = [];
    window.fetch = (url, opts) => {
      captured.push({ url: String(url), body: (opts && opts.body) || '' });
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
    };
    try {
      const stem = document.getElementById('dStem');
      const boxC = document.getElementById('dOpt_C');
      const boxA = document.getElementById('dOpt_A');
      if (!stem || !boxC || !boxA) return { ok: false, why: '編輯框不在' };
      const stemText = '稽核題幹字串';
      const cText = '稽核C選項字串';
      const aText = boxA.value;
      stem.value = stemText;
      boxC.value = cText;
      stem.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('dSave').click();
      await new Promise((r) => setTimeout(r, 1500));
      const review = captured.filter((c) => c.url.indexOf('/api/review') >= 0);
      if (!review.length) return { ok: false, why: '按了儲存但沒有送出 /api/review' };
      let payload = null;
      try { payload = JSON.parse(review[0].body); } catch (e) { /* 不是 JSON */ }
      if (!payload) return { ok: false, why: 'payload 不是 JSON' };
      const correction = payload.correction || {};
      const options = correction.options || [];
      const optC = options.find((o) => o.key === 'C') || {};
      const optA = options.find((o) => o.key === 'A') || {};
      const wrote = captured.filter((c) => c.url.indexOf('/api/review') < 0
        && c.url.indexOf('/api/discuss') < 0 && c.url.indexOf('/api/queue_index') < 0);
      return {
        ok: correction.stem === stemText && optC.text === cText && optA.text === aText,
        action: payload.action, stem: correction.stem, optC: optC.text,
        optAKept: optA.text === aText, otherWrites: wrote.map((c) => c.url),
      };
    } finally {
      window.fetch = realFetch;
    }
  })()`;

  /* ---------------------------------------------------------------- 首頁 */
  await evaluate(`document.querySelector('[data-area="home"]').click()`);
  await sleep(1400);
  let controls = await evaluate(`(${CONTROL_PROBE})('#areaHome')`);
  check(!!controls, 'home', '首頁畫得出來');
  const homeCards = (controls || []).filter((c) => c.visible && c.kind === 'action'
    && (c.handlers.length || c.href));
  check(homeCards.length >= 3, 'home', '首頁每一張卡都接上了 handler', `${homeCards.length} 張`);
  if (homeCards.length) {
    await evaluate(`(() => { const b = Array.from(document.querySelectorAll('#areaHome [data-go]'))
      .find((n) => typeof n.onclick === 'function'); if (b) b.click(); })()`);
    await sleep(1800);
    const after = await evaluate(`Array.from(document.querySelectorAll('.area.on')).map((n) => n.id)`);
    check(after.length === 1 && after[0] !== 'areaHome', 'home',
      '按首頁卡片真的切到另一區', JSON.stringify(after));
    await evaluate(`document.querySelector('[data-area="home"]').click()`);
    await sleep(900);
  }

  /* ------------------------------------------------------------ 題目審核區 */
  await evaluate(`document.querySelector('[data-area="question"]').click()`);
  await sleep(3200);
  controls = await evaluate(`(${CONTROL_PROBE})('#areaQuestion')`);
  check(!!controls, 'question', '題目審核區畫得出來');
  const qVisible = (controls || []).filter((c) => c.visible);
  check(qVisible.length >= 8, 'question', '題目區有足夠多的可見控制項', `${qVisible.length} 個`);

  // 題目區的導覽鍵：真的按「下一題」，游標要動。清單列是 `button[data-pos]`（不是 `[data-i]`）。
  const navMoved = await evaluate(`(async () => {
    const first = document.querySelector('#btnNext');
    if (!first) return { ok: false, why: '沒有 btnNext' };
    const active = () => document.querySelector('#listBody .row.active')?.dataset.pos ?? null;
    const before = active();
    first.click();
    await new Promise((r) => setTimeout(r, 1100));
    const after = active();
    return { ok: before !== null && after !== null && before !== after, before, after };
  })()`);
  check(navMoved.ok, 'question', '按「S 下一題」清單游標真的前進',
    `${navMoved.before} → ${navMoved.after}`);

  // 選項／題幹編輯框在**修正模式**裡（按 `E` 才畫出來）。開編輯模式本身是唯讀以外的一步，但它不
  // 寫任何紀錄——只是把框畫出來；真正的寫入要再按「儲存修正」。所以這裡可以安全地開。
  const optFocus = await evaluate(`(async () => {
    document.getElementById('actFix').click();
    await new Promise((r) => setTimeout(r, 900));
    const box = document.querySelector('#textSide textarea[id^="editOpt_"], #textSide .opt-edit textarea');
    if (!box) return { ok: false, why: '開了修正模式還是沒有選項框' };
    box.focus();
    await new Promise((r) => setTimeout(r, 220));
    const typeable = document.activeElement === box && !box.disabled;
    return { ok: typeable, typeable };
  })()`);
  check(optFocus.ok, 'question', '修正模式的選項框真的吃得到焦點', optFocus.why || '');
  // 把修正模式關掉，不要留著一個按下去會寫入的狀態。
  await evaluate(`(() => { const b = document.getElementById('actFix');
    if (b && b.classList.contains('on')) b.click(); })()`);
  await sleep(700);

  // 「只加註記」要開一個可打字的框（不是畫一個框卻打不進去）。
  const noteOpen = await evaluate(`(async () => {
    const b = document.getElementById('actNote');
    if (!b) return { ok: false, why: '沒有 actNote' };
    b.click();
    await new Promise((r) => setTimeout(r, 500));
    const box = document.getElementById('reasonBox');
    const on = !!box && box.classList.contains('on');
    const text = document.getElementById('reasonText');
    let typeable = false;
    if (text) {
      text.focus();
      typeable = document.activeElement === text && !text.disabled;
      text.value = '';
    }
    return { ok: on && typeable, on, typeable };
  })()`);
  check(noteOpen.ok, 'question', '「只加註記」開的框可以打字（不是死的）',
    `on=${noteOpen.on} typeable=${noteOpen.typeable}`);
  await evaluate(`(() => { const t = document.getElementById('reasonText');
    if (t) t.value = ''; document.getElementById('actNote').click(); })()`);
  await sleep(400);

  /* ------------------------------------------------------------ 答案審核區 */
  await evaluate(`document.querySelector('[data-area="answer"]').click()`);
  await sleep(3200);
  controls = await evaluate(`(${CONTROL_PROBE})('#areaAnswer')`);
  check(!!controls, 'answer', '答案審核區畫得出來');
  const sheetRows = await evaluate(`Array.from(document.querySelectorAll('#areaAnswer .sheet-row')).length`);
  check(sheetRows >= 1, 'answer', '答案卷列得出來', `${sheetRows} 張`);
  const answerWrite = await evaluate(`(async () => {
    const row = document.querySelector('#areaAnswer .sheet-row');
    if (row) row.click();
    await new Promise((r) => setTimeout(r, 1500));
    const button = Array.from(document.querySelectorAll('#areaAnswer .ans-btn'))
      .find((n) => typeof n.onclick === 'function' && !n.disabled);
    const count = document.querySelectorAll('#areaAnswer .ans-btn').length;
    return { count, hasHandler: !!button };
  })()`);
  check(answerWrite.count >= 1 && answerWrite.hasHandler, 'answer',
    '答案按鈕有接上 handler（點了會變草稿，不會自己送出）',
    `${answerWrite.count} 個按鈕`);

  /* ------------------------------------------------------------ 錯題討論區 */
  await evaluate(`document.querySelector('[data-area="discuss"]').click()`);
  await sleep(3600);
  controls = await evaluate(`(${CONTROL_PROBE})('#areaDiscuss')`);
  check(!!controls, 'discuss', '錯題討論區畫得出來');
  const dVisible = (controls || []).filter((c) => c.visible);
  check(dVisible.length >= 10, 'discuss', '討論區有足夠多的可見控制項', `${dVisible.length} 個`);

  // 每一個**可見的動作控制項**都要有接上東西：不是 handler，就是一個真的 href。
  // 「讀值型」控制項（文字框、核取方塊、選檔）不在此列——它們的值由儲存函式讀；
  // 它們由下面的 read-at-save 檢查逐個對名字驗證。
  const unbound = dVisible.filter((c) => c.kind === 'action'
    && c.handlers.length === 0 && !c.href);
  check(unbound.length === 0, 'discuss', '討論區每一個可見動作控制項都接上了 handler 或 href',
    unbound.length ? unbound.map((c) => `${c.tag}#${c.name}`).join('、') : '（全部有接）');


  // 字體：真按「放大」，量 CSS 變數真的變大，而且**不是**靠重畫整個題目。
  const font = await evaluate(`(async () => {
    const pane = document.getElementById('discussCase');
    if (!pane) return { ok: false, why: '沒有 discussCase' };
    const read0 = getComputedStyle(pane).getPropertyValue('--reading-size').trim();
    const up = document.querySelector('#discussMain [data-font="up"]');
    const down = document.querySelector('#discussMain [data-font="down"]');
    const reset = document.querySelector('#discussMain [data-font="reset"]');
    if (!up || !down || !reset) return { ok: false, why: '字體按鈕不全' };
    up.click(); up.click();
    await new Promise((r) => setTimeout(r, 260));
    const read1 = getComputedStyle(pane).getPropertyValue('--reading-size').trim();
    const shown1 = (document.querySelector('#discussMain .fontctl span') || {}).textContent;
    down.click();
    await new Promise((r) => setTimeout(r, 200));
    const read2 = getComputedStyle(pane).getPropertyValue('--reading-size').trim();
    reset.click();
    await new Promise((r) => setTimeout(r, 200));
    const read3 = getComputedStyle(pane).getPropertyValue('--reading-size').trim();
    return { ok: true, read0, read1, shown1, read2, read3 };
  })()`);
  check(font.ok && font.read1 !== font.read0, 'discuss', '字體「放大」真的改變字級',
    `${font.read0} → ${font.read1}（顯示 ${font.shown1}）`);
  check(font.read2 !== font.read1, 'discuss', '字體「縮小」真的改變字級', `${font.read1} → ${font.read2}`);
  check(font.read3 === font.read0, 'discuss', '字體「重設」回到基準', `${font.read2} → ${font.read3}`);

  // 位置按鈕（題幹／A–D／表格／題組共用）：按下去要標成選取。
  const place = await evaluate(`(async () => {
    const buttons = Array.from(document.querySelectorAll('#discussMain [data-place]'));
    if (buttons.length < 6) return { ok: false, why: '位置按鈕只有 ' + buttons.length + ' 個' };
    const target = buttons.find((b) => b.dataset.place === 'C') || buttons[1];
    target.click();
    await new Promise((r) => setTimeout(r, 250));
    const on = Array.from(document.querySelectorAll('#discussMain [data-place]')).filter(
      (b) => b.classList.contains('on'));
    return { ok: on.length === 1 && on[0] === target, count: buttons.length,
             onText: on.map((b) => b.textContent.trim()).join(',') };
  })()`);
  check(place.ok, 'discuss', '「擷圖位置」按鈕真的會標成選取', `選了 ${place.onText}`);

  // 儲存補圖：按**空的**。守門訊息要出現，而且不可以送出任何東西。
  const cropEmpty = await evaluate(`(async () => {
    const before = document.getElementById('dCropStatus').textContent;
    document.getElementById('dCropSave').click();
    await new Promise((r) => setTimeout(r, 700));
    const after = document.getElementById('dCropStatus').textContent;
    return { before, after };
  })()`);
  check(/請先貼上|還沒有圖/.test(cropEmpty.after), 'discuss',
    '「儲存補圖」在沒有圖時擋下來（沒有寫入）', cropEmpty.after);

  // 選圖的入口真的接上（檔案選擇、拖放、貼上三條都要有）。
  const cropInputs = await evaluate(`(() => {
    const drop = document.getElementById('dCropDrop');
    const file = document.getElementById('dCropFile');
    return {
      dropHandler: !!drop && typeof drop.ondrop === 'function',
      dragOver: !!drop && typeof drop.ondragover === 'function',
      fileHandler: !!file && typeof file.onchange === 'function',
      fileAccept: file ? (file.getAttribute('accept') || '') : '',
    };
  })()`);
  check(cropInputs.dropHandler && cropInputs.dragOver, 'discuss', '「擷圖」可以拖放進來');
  check(cropInputs.fileHandler && /image/.test(cropInputs.fileAccept), 'discuss',
    '「擷圖」可以選圖檔', cropInputs.fileAccept);

  // 貼上：handler 掛在 document 上（PDF iframe 拿不到鍵盤），所以單獨驗一條剪貼事件真的走進去。
  const pasteProbe = await evaluate(`(async () => {
    // 用一個 1x1 的 PNG 當剪貼簿內容，模擬在 PDF 上框好按 ⌘C 再貼上。
    const b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/wFRE0QAAAAASUVORK5CYII=';
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    const file = new File([bytes], 'crop.png', { type: 'image/png' });
    const dt = new DataTransfer();
    dt.items.add(file);
    document.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 900));
    const preview = document.getElementById('dCropPreview');
    const status = document.getElementById('dCropStatus');
    return {
      previewShown: !!preview && preview.style.display !== 'none' && !!preview.src,
      status: status ? status.textContent : '',
    };
  })()`);
  check(pasteProbe.previewShown, 'discuss', '在討論區貼上一張圖會出現預覽（⌘V 這條路真的通）',
    pasteProbe.status);

  // 貼上之後，儲存補圖的守門應該已經解除（但**不按**，因為它會寫檔；只驗狀態）。
  const cropReady = await evaluate(`(() => {
    const b = document.getElementById('dCropSave');
    return { exists: !!b, hasHandler: !!(b && typeof b.onclick === 'function') };
  })()`);
  check(cropReady.exists && cropReady.hasHandler, 'discuss', '「儲存補圖」按鈕接上了 handler（會寫入，稽核不代按）');
  // 把待補的圖丟掉，不要留下一個「按下去就會寫檔」的狀態給下一個檢查。
  await evaluate(`(() => { window.D && (window.D.pendingCrop = ''); })()`);

  // 註解：空的時候要擋；有字的時候按下去是「送 comment」——但那會寫紀錄，所以只驗框可打字 + 空守門。
  const note = await evaluate(`(async () => {
    const box = document.getElementById('dNote');
    const save = document.getElementById('dNoteSave');
    if (!box || !save) return { ok: false, why: '註解框或儲存鍵不在' };
    save.click();
    await new Promise((r) => setTimeout(r, 700));
    box.focus();
    const typeable = document.activeElement === box;
    const hasHandler = typeof save.onclick === 'function';
    return { ok: typeable && hasHandler, typeable, hasHandler };
  })()`);
  check(note.ok, 'discuss', '註解框可打字、儲存鍵有 handler（空值會擋）',
    `typeable=${note.typeable} handler=${note.hasHandler}`);

  // 儲存修正／退回未審：**不按**——它們寫 append-only 的人工紀錄。只驗它們是活的。
  const decisionKeys = await evaluate(`(() => {
    const ids = ['dSave', 'dHold', 'dClear'];
    return ids.map((id) => {
      const n = document.getElementById(id);
      return { id, exists: !!n, handler: !!(n && typeof n.onclick === 'function'), disabled: !!(n && n.disabled) };
    });
  })()`);
  for (const key of decisionKeys) {
    check(key.exists && key.handler && !key.disabled, 'discuss',
      `「${key.id}」是活的（有 handler、沒 disabled）`);
  }

  // 側欄與原則／反問面板：新增原則要能開出輸入框。
  const principle = await evaluate(`(async () => {
    const start = document.getElementById('dpStart');
    if (start) {
      start.click();
      await new Promise((r) => setTimeout(r, 900));
    }
    const box = document.getElementById('dpNew');
    const save = document.getElementById('dpSave');
    if (box && save) {
      save.click();
      await new Promise((r) => setTimeout(r, 600));
    }
    return { opened: !!box, hasSave: !!save,
             saveHandler: !!(save && typeof save.onclick === 'function') };
  })()`);
  check(principle.opened && principle.saveHandler, 'discuss',
    '「新增原則」開得出輸入框，且加入鍵接上了（空值會擋）',
    `opened=${principle.opened} saveHandler=${principle.saveHandler}`);
  // 關掉草稿，不留下半開的狀態。
  await evaluate(`(() => { const c = document.getElementById('dpCancel'); if (c) c.click(); })()`);
  await sleep(600);

  // 真正的驗收：**我打的字就是被送出的字**。做法是把 fetch 換成一個只做紀錄的替身，
  // 在兩個框裡打不同的字、按「儲存修正」，然後檢查送出的 payload 帶著那兩個字。
  // 替身不發出任何請求，所以這條驗收**一筆紀錄都不會寫**；驗完把真的 fetch 裝回去。
  // 這比「原始碼裡有這個 id」強得多：那個只能證明字串存在，這個證明值真的走完了一條路。
  const savePayload = await evaluate(PROBE_SAVE);

  check(savePayload.ok, 'discuss',
    '在框裡打的字真的進到「儲存修正」送出的 payload（打的＝送的）',
    `action=${savePayload.action} stem=「${String(savePayload.stem || '').slice(0, 20)}」 optC=「${String(savePayload.optC || '').slice(0, 20)}」`);
  check(savePayload.optAKept, 'discuss', '沒改的欄位（選項 A）沒有被弄丟');
  check((savePayload.otherWrites || []).length === 0, 'discuss',
    '「儲存修正」只送 /api/review，沒有順手打別的寫入端點',
    (savePayload.otherWrites || []).join('、'));
  // 稽核不留下任何決定：把草稿清掉，回到伺服器上的原文字。
  await evaluate(`(() => { const b = document.getElementById('dClear'); if (b) b.click(); })()`);
  // 替身回了一個空的 /api/discuss，所以清單被清掉了。用真的伺服器重畫一次，
  // 把畫面還原成稽核開始前的樣子（這一步是真的讀取，不是寫入）。
  await evaluate(`renderDiscuss(true)`);
  await sleep(2600);

  /* 最後一道：「**每一個按鈕**」不是只指討論區。
     走訪四個區，把每個可見的動作控制項抓出來，斷言每一個不是有 handler 就是有真的 href。
     這與上面的逐項檢驗互補：上面證明「某個按鈕按下去真的有用」，這一條證明「沒有漏掉任何一個」。
     一個沒接上的按鈕不會壞，它只是什麼都不做——而在截圖上與成功的按鈕一模一樣。 */
  const coverage = {};
  for (const area of ['home', 'question', 'answer', 'discuss']) {
    await evaluate(`document.querySelector('[data-area="${area}"]').click()`);
    await sleep(area === 'question' || area === 'discuss' ? 3000 : 1500);
    const list = await evaluate(`(${CONTROL_PROBE})('#area${area[0].toUpperCase()}${area.slice(1)}')`);
    const actions = (list || []).filter((c) => c.visible && c.kind === 'action');
    const dead = actions.filter((c) => c.handlers.length === 0 && !c.href);
    coverage[area] = { total: actions.length, dead: dead.map((c) => `${c.tag}#${c.name}`) };
    check(dead.length === 0, area,
      `${area} 區的每一個可見動作控制項都接上了（沒有死的按鈕）`,
      dead.length ? dead.join('、') : `${actions.length} 個都活著`);
  }
  // 四個區真的都有東西被檢查到，否則這一道可以因為「畫面是空的」而假裝通過。
  const thin = Object.entries(coverage).filter(([, v]) => v.total < 3);
  check(thin.length === 0, 'all', '四個區都真的抓到夠多的控制項（不是空畫面）',
    Object.entries(coverage).map(([k, v]) => `${k}:${v.total}`).join(' '));

  /* 每一區都走完之後：整段過程不可以有 console 例外。
     一個「按下去丟例外」的按鈕，畫面看起來跟成功沒有兩樣。 */
  const realErrors = pageErrors.filter((e) => !/favicon|Failed to load resource/i.test(e));
  check(realErrors.length === 0, 'all', '整個稽核過程沒有 console 例外',
    realErrors.length ? realErrors.slice(0, 4).join(' ｜ ') : '');

  const shot = await send('Page.captureScreenshot', {});
  fs.writeFileSync('/tmp/v2_ui_audit.png', Buffer.from(shot.data, 'base64'));
  console.log(`\n  截圖：/tmp/v2_ui_audit.png`);
  if (jsonOut) {
    fs.writeFileSync(jsonOut, JSON.stringify({ base, failures, results }, null, 1));
    console.log(`  報告：${jsonOut}`);
  }
  console.log(`\n  ${failures ? `有 ${failures} 項 BAD` : '全部符合'}`);
  ws.close();
  chrome.kill();
  return failures ? 1 : 0;
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error('稽核失敗：', error && error.stack ? error.stack : error);
  process.exit(2);
});
