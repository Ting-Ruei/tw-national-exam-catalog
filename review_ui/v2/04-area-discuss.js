/* 錯題討論區：三欄（左_list＝本場 5 題；中_main＝6 欄資訊；右_pdfFrame＝紙本）。`W`/`S` 依列表移動選取；`1..6` 聚焦 6 個 option、`7` 註解、`8` 紙本；`Enter` 儲存，`Shift+Enter` 換行；`E` 儲存。（regression 前 6 項，題//答。PAPER 6 項為 PAPER；NOTE 3 項，PAPER.noted(3) PAPER。） */
/* 錯題討論區的自有快取（以 event 為單位去重；同一 key 只保最新一筆）。
   `null`≠`''`：無 disputed 記 null、空字串記 ''，兩者都算「有」但不同寫。
   保準：同一 candidate_key 只取最新一筆 event。 */
const D = { rows: [], index: 0, byKey: new Map(), events: [], draft: new Map(),
            note: new Map(), loaded: false };

/* 三欄：左_list（題目欄，同 S.rows 序）｜中_main（6 欄資訊）｜右_pdfFrame（紙本，與左脫鉤）。
   鍵：`W`/`S` 走本區清單；`1..6` 聚焦六個 option；`7`=註解  `8`=紙本；`Enter` 儲存、`Shift+Enter` 換行。 */
async function renderDiscuss(force) {
  const list = $('discussList'), main = $('discussMain'), pdf = $('discussPdf');
  if (!list || !main || !pdf) return;
  if (force) { A.fetched = {}; D.loaded = false; }
  if (!D.loaded) {
    list.innerHTML = '<div class="empty">載入中…</div>';
    const query = {};
    if (S.scope) {
      if (S.scope.category) query.category = S.scope.category;
      if (S.scope.year) query.year = S.scope.year;
      if (S.scope.ordinal) query.ordinal = S.scope.ordinal;
      if (S.scope.subject) query.subject = S.scope.subject;
    }
    query.limit = '5000';
    const [payload, feedback] = await Promise.all([
      fetchAreaJson('/api/candidates', query),
      fetchAreaJson('/api/correction-feedback', { limit: '500' }),
    ]);
    if (!payload) {
      list.innerHTML = `<div class="empty">讀不到佇列（${esc(A.error['/api/candidates'] || '')}）</div>`;
      return;
    }
    for (const candidate of (payload.candidates || [])) {
      if (candidate && candidate.candidate_key) D.byKey.set(candidate.candidate_key, candidate);
    }
    // 與題目區同序（charter §）：列序＝`S.rows`（題目區可見列）為準；缺則退回 payload 序。
    const order = (S.rows && S.rows.length) ? S.rows.map((row) => row.candidate_key)
      : (payload.candidates || []).map((candidate) => candidate.candidate_key);
    D.rows = order.filter((key) => D.byKey.has(key));
    D.events = Array.isArray(feedback && feedback.events) ? feedback.events.slice().reverse() : [];
    D.loaded = true;
  }
  const total = D.rows.length;
  const events = D.events;
  if (!total) {
    list.innerHTML = '<div class="list-head">題目欄<b>0</b></div>';
    main.innerHTML = `<div class="empty-area">${events.length ? `本場記錄 ${events.length} 筆修正。`
      : '這個範圍還沒有手工修正紀錄。<br>先在題目審核區按 A/R/B/E/C 做決定。'}</div>`;
    pdf.innerHTML = '<div class="empty-area">（無）</div>';
    return;
  }
  list.innerHTML = `<div class="list-head">題目欄<b>${total}</b></div>`
    + D.rows.map((key, i) => {
      const candidate = D.byKey.get(key) || {};
      const kind = (D.events.find((e) => e && e.candidate_key === key) || {}).change_class || '';
      return `<div class="row${i === D.index ? ' on' : ''}" data-i="${i}">`
        + `<span class="n">${i + 1}</span>第 ${esc(candidate.question_number ?? '?')} 題`
        + `${kind ? ` <span class="kind">${esc(kind)}</span>` : ''}</div>`;
    }).join('');
  for (const node of list.querySelectorAll('[data-i]')) {
    node.onclick = () => { D.index = Number(node.dataset.i || 0); renderDiscuss(); };
  }
  const key = D.rows[Math.min(D.index, D.rows.length - 1)];
  main.innerHTML = discussCenterHtml(D.byKey.get(key) || {}, key) + discussSideHtml(total);
  pdf.innerHTML = discussPdfHtml(D.byKey.get(key) || {});
  bindDiscuss();
}

/* 六欄：①機器 ②AI ③difference ④手動 ⑤human ⑥joint。`f.verdict` 可 null。 */
function discussCenterHtml(candidate, key) {
  const f = candidate.qbr_ai_finding || null;
  const draft = D.draft.get(key) || {};
  const noteVal = D.note.get(key) !== undefined ? D.note.get(key)
    : String((candidate.review || {}).notes || '');
  const options = candidate.options || [];
  const optText = (k) => (draft['option_' + k] !== undefined ? draft['option_' + k]
    : (options.find((o) => o.key === k) || {}).text || '');
  const same = !!f && f.verdict === 'OK';
  const opts = [1, 2, 3, 4, 5, 6].map((i) => '<div class="opt"><span class="k">' + i
    + '</span><textarea id="dOpt' + i + '" data-k="option_' + i + '" style="min-height:44px">'
    + esc(optText(String(i))) + '</textarea></div>').join('');
  const ev = D.events.find((e) => e && e.candidate_key === key) || {};
  return '<div class="case">'
    + '<h2>錯題 ' + esc(candidate.question_number ?? '?')
    + ' <span class="kind">' + esc(((candidate.metadata || {}).normalized_subject_name)
      || candidate.paper_id || '') + '</span></h2>'
    + '<div class="meta">' + esc(String((candidate.metadata || {}).source_profile || '').slice(0, 44))
    + ' ' + (candidate.review && candidate.review.action
      ? '人/' + esc(String(candidate.review.action).slice(0, 12)) : '（未決定）')
    + (D.events.length ? ` 经历 ${D.events.length} 次` : '') + '</div>'
    + '<div class="diff">① 機器偵測：' + discussDisputesHtml(candidate.disputes)
    + '　② AI 審：' + (f ? esc(f.verdict || '（無）') + '／' + esc(f.what || '') + '／' + esc(f.fix || '')
      : '（尚無）') + (same ? ' ※與②同內容→不重複（指紋）。' : '')
    + '　③ 前後對照：' + discussCaseHtml(ev)
    + '　④ 手動修改（本區可寫）：'
    + '<textarea id="dStem" data-k="_stem" placeholder="題目文字" style="min-height:44px">'
      + esc(draft._stem !== undefined ? draft._stem : (candidate.stem || '')) + '</textarea>'
    + opts
    + '　⑤ 註解（本區可寫）：<textarea id="dNote" placeholder="Enter 儲存、Shift+Enter 換行；可留空" '
      + 'style="min-height:44px">' + esc(noteVal) + '</textarea>'
    + '　⑥ 紙本如右。</div>'
    + '<div class="answer-bar"><button class="ghost" data-focus="1">1 A</button>'
    + '<button class="ghost" data-focus="2">2 B</button>'
    + '<button class="ghost" data-focus="3">3 C</button>'
    + '<button class="ghost" data-focus="4">4 D</button>'
    + '<button class="ghost" data-focus="5">5 E</button>'
    + '<button class="ghost" data-focus="6">6 F</button>'
    + '<button class="ghost" data-focus="7">7 註解</button>'
    + '<button class="ghost" data-focus="8">8 紙本</button>'
    + '<button class="act save" id="dSave">儲存 E</button>'
    + '<button class="ghost" id="dClear">清空</button></div>'
    + '</div>';
}

/* 一侧统计（引用 #discussSide 的 .n）：單筆→1 個 event；多筆→計 event 數。 */
function discussSideHtml(total) {
  const withAi = D.rows.filter((k) => (D.byKey.get(k) || {}).qbr_ai_finding).length;
  return '<div id="discussSide" class="discuss-side"><h3>統計</h3>'
    + '<div class="row"><span>本場個案</span><b class="n">' + (total || 0) + '</b></div>'
    + '<div class="row"><span>有 AI 判斷</span><b class="n">' + withAi + '</b></div>'
    + '<div class="row"><span>人工 event</span><b class="n">' + D.events.length + '</b></div></div>';
}

/* `null`=無；`''`=空。両者都算「有」，但不同寫，故分押顯示。 */
function discussDisputesHtml(list) {
  if (!list || !list.length) return '（空）';
  return list.map((d, i) => (i + 1) + ')' + esc(d.kind || '') + ':' + esc(d.detail || '')).join(' ');
}

/* 本区自記的 event 前綴 `@`＝時間。array=按 event 計。*/
function discussCaseHtml(event) {
  if (!event || !event.diff || !event.diff.length) return '（無）';
  const lines = event.diff.map((row) => esc(row.field || row.path || '?') + ':'
    + '<span class="from">' + esc(row.before === undefined ? '' : String(row.before)).slice(0, 120)
    + '</span> → <span class="to">'
    + esc(row.after === undefined ? '' : String(row.after)).slice(0, 120) + '</span>').join(' ｜ ');
  // 類型名兩個地方都讀它：標題的 chip 與側欄的列（`classify_change` 算出來的，不是人敲）。
  return `<span class="kind">${esc(event.change_class || '（未分類）')}</span>`
    + esc((event.changed_fields || []).join(',') || '（無）') + ' ' + lines
    + ' @' + esc(String(event.created_at || '').slice(5, 16))
    + ' #' + esc(String(event.feedback_id || '').slice(-6))
    + `（${esc(event.change_class || '（未分類）')}）`;
}

/* 只有六個 option（<=6）；focus 記号 1..6；7=note 8=pdf。*/
function discussPdfHtml(candidate) {
  const ref = (candidate.assets || []).find((a) => a && a.type === 'pdf') || {};
  const img = (candidate.assets || []).filter((a) => a && a.type === 'image');
  const url = ref.path ? fileUrl(ref.path) : '';
  return '<div class="pdf-head">紙本 ' + esc(candidate.paper_id || '') + '（題目卷'
    + (img.length ? ' · 圖 ' + img.length : '') + '）</div>'
    + (url ? '<iframe id="dPdf" class="discuss-frame" title="paper" src="' + esc(url) + '#view=FitH"></iframe>'
      : '<div class="empty-area">（無紙本 URL）</div>')
    + img.map((im) => '<img src="' + esc(fileUrl(im.path || '')) + '" alt="">').join('');
}

/* 按号对焦（數字鍵 1..8）。`Enter` 在 1..6 儲存、7 亦儲存；8=紙本。*/
function focusDiscuss(n) {
  const id = (n === 7) ? 'dNote' : (n === 8) ? 'dPdf' : ('dOpt' + n);
  const el = $(id) || document.getElementById(id);
  if (el && el.focus) el.focus();
}

function bindDiscuss() {
  const key = D.rows[Math.min(D.index, Math.max(0, D.rows.length - 1))];
  const save = () => saveDiscuss(key);
  for (const id of ['dStem', 'dOpt1', 'dOpt2', 'dOpt3', 'dOpt4', 'dOpt5', 'dOpt6', 'dNote']) {
    const el = $(id);
    if (el) el.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } };
  }
  for (const b of document.querySelectorAll('#areaDiscuss [data-focus]')) {
    b.onclick = () => focusDiscuss(Number(b.dataset.focus || 0));
  }
  const s = $('dSave'); if (s) s.onclick = save;
  const c = $('dClear');
  if (c) c.onclick = () => { D.draft.delete(key); D.note.delete(key); D.loaded = false; renderDiscuss(); };
}

async function saveDiscuss(key) {
  if (!key) return;
  const g = (id) => { const el = $(id); return el ? el.value : undefined; };
  const body = { candidate_key: key, reviewer: 'local', source: 'linear_v2' };
  const note = g('dNote');
  if (note !== undefined && note !== '') body.note = note;
  const suggest = {};
  const stem = g('dStem');
  if (stem !== undefined && stem !== '') suggest._stem = stem;
  for (const i of [1, 2, 3, 4, 5, 6]) {
    const v = g('dOpt' + i);
    if (v !== undefined && v !== '') suggest['option_' + i] = v;
  }
  if (Object.keys(suggest).length) { body.action = 'correct'; body.suggested = suggest; }
  else if (body.note !== undefined) body.action = 'comment';   // comment 的 note 必非空
  else body.action = 'needs_review';
  try {
    const response = await fetch('/api/review', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
    if (body.suggested) D.draft.set(key, body.suggested);
    if (body.note !== undefined) D.note.set(key, body.note);
    D.loaded = false; A.fetched = {};
    invalidateAreas(); renderArea(A.area);
    toast('已存 ' + String(key).slice(-6) + '（' + String(body.action || '?') + '）');
    renderDiscuss();
  } catch (error) {
    toast('寫入失敗 ' + String(error.message || error), true);
  }
}
