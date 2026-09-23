/* 錯題討論區：三欄（左_list＝卡住的題；中_main＝資訊／編輯／原則／代問；右_pdfFrame＝紙本）。
 *
 * 這一區**只放卡住的題**：人按過「阻擋」，或管線／AI 把它退回待複核。它不再是「整個佇列的
 * 另一種畫法」——那讓 79,090 題全部進來，真正卡住的那 300 題反而找不到。
 *
 * 三個面板各有它的來源，全部來自同一次 `/api/discuss`：
 *   左  卡住的題（伺服器用 `reviewStatus=discuss` 過濾，定義在 `DISCUSS_BUCKETS`）
 *   中  ①機器偵測（disputes）②AI 意見（finding，含模型看過的紙本截圖）③編輯框
 *       ④「基本原則」可加可減，會被編進修題／AI 審核的提示詞 ⑤AI 讀不懂時在這裡反問
 *   右  紙本原卷，永遠是題目卷，與左欄脫鉤
 *
 * 鍵：`W`/`S` 走本區清單；`1..6` 聚焦六個選項；`7` 註解  `8` 紙本；`Enter` 儲存、`Shift+Enter` 換行。 */
const D = {
  rows: [],            // candidate_key，依伺服器回的序（卡住的題）
  byKey: new Map(),
  index: 0,
  //: 編輯草稿（題幹與選項）。以 candidate_key 為鍵，切題再回來還在。
  draft: new Map(),
  //: 這一區自己的「已載入」狀態。`A.rendered` 由 `invalidateAreas()` 清，這裡只記這一區的資料。
  loaded: false,
  //: 基本原則與 AI 反問，都是 `/api/discuss` 一起帶回來的。
  principles: { principles: [], removed: [], count: 0 },
  questions: { questions: [], open_count: 0 },
  //: 目前哪一個面板在「新增原則」輸入模式；`null`＝沒有。
  principleDraft: false,
  //: 反問的回答草稿，以 question_id 為鍵。
  answerDraft: new Map(),
  //: A note of "I have looked at this question" per key, so the list can show progress without
  //: the server having to be asked again.
  seen: new Map(),
};

/* 重新載入這一區。`force`＝連資料一起重抓（剛寫入過東西）。 */
async function renderDiscuss(force) {
  const list = $('discussList'), main = $('discussMain'), pdf = $('discussPdf');
  if (!list || !main || !pdf) return;
  if (force) D.loaded = false;
  if (!D.loaded) {
    list.innerHTML = '<div class="empty">載入中…</div>';
    // 這一區沒有自己的 scope：卡住的題散在整個題庫，讓使用者先選科目只會把別的科目的卡住題藏起來。
    // 伺服器預設就會套 `reviewStatus=discuss`（見 `discuss_payload`），這裡只給上限。
    const payload = await fetchAreaJson('/api/discuss', { limit: '500' });
    if (!payload) {
      list.innerHTML = `<div class="empty">讀不到討論區（${esc(A.error['/api/discuss'] || '')}）</div>`;
      return;
    }
    D.byKey = new Map();
    D.rows = [];
    for (const candidate of (payload.candidates || [])) {
      if (candidate && candidate.candidate_key) {
        D.byKey.set(candidate.candidate_key, candidate);
        D.rows.push(candidate.candidate_key);
      }
    }
    D.principles = payload.principles || D.principles;
    D.questions = payload.repair_questions || D.questions;
    D.loaded = true;
  }
  const total = D.rows.length;
  if (!total) {
    list.innerHTML = '<div class="list-head">卡住的題<b>0</b></div>';
    main.innerHTML = '<div class="empty-area">這個佇列目前沒有卡住的題。<br>'
      + '（人按「阻擋」或管線／AI 退回的題會出現在這裡。）</div>';
    pdf.innerHTML = '<div class="empty-area">（無）</div>';
    return;
  }
  D.index = Math.min(Math.max(0, D.index), total - 1);
  list.innerHTML = `<div class="list-head">卡住的題<b>${total}</b></div>`
    + D.rows.map((key, i) => {
      const candidate = D.byKey.get(key) || {};
      const bucket = ((candidate.review || {}).queue_bucket) || '';
      const label = discussRowLabel(candidate);
      return `<div class="row${i === D.index ? ' active' : ''}${D.seen.get(key) ? ' done' : ''}" data-i="${i}">`
        + `<span class="num">${i + 1}</span>第 ${esc(candidate.question_number ?? '?')} 題`
        + `${label ? `<span class="kind">${esc(label)}</span>` : ''}</div>`;
    }).join('');
  for (const node of list.querySelectorAll('[data-i]')) {
    node.onclick = () => { D.index = Number(node.dataset.i || 0); renderDiscuss(); };
  }
  const key = D.rows[D.index];
  const candidate = D.byKey.get(key) || {};
  main.innerHTML = discussCenterHtml(candidate, key);
  pdf.innerHTML = discussPdfHtml(candidate);
  bindDiscuss(key);
}

/* 佇列桶 → 中文標籤。與伺服器 `review_projection` 的 `display_label` 同義，但這裡只認 bucket，
   因為 bucket 是穩定的鍵、label 是給人看的字；照 label 認會在改字時默默壞掉。 */
const DISCUSS_BUCKET_LABEL = {
  repair_pending: '修復後待複核', accepted_reaudit: '已通過後待複核',
  reset_review: '退回未審', never_reviewed: '未看過', reviewed: '已看過',
};
function bucketLabel(bucket) { return DISCUSS_BUCKET_LABEL[bucket] || ''; }

/* 一列在左欄怎麼標。人阻擋的題在伺服器的投影裡 bucket 是 `reviewed`（只有退回相關的狀態才
   自己一個桶），所以用 `action` 認它；退回的才用 bucket。兩者都不算時不標，勝過標一個錯的。 */
function discussRowLabel(candidate) {
  const review = candidate.review || {};
  if (review.action === 'block') return '人阻擋';
  return bucketLabel(review.queue_bucket || '');
}

/* 這一題為什麼卡住，用一句話說出來。
   人阻擋的題沒有寫原因（阻擋時不必填），所以「有人說它壞」本身就是唯一的線索；修復後退回的題
   則帶著 `repair_kind`。兩者要分開講，否則讀者會以為人寫了什麼。 */
function discussWhyHtml(candidate) {
  const review = candidate.review || {};
  const bucket = review.queue_bucket || '';
  const label = bucketLabel(bucket);
  if (review.action === 'block') {
    return `人按過「阻擋」${review.notes ? `：${esc(review.notes)}` : '（沒有寫原因）'}`;
  }
  if (review.reset_action || review.repair_kind) {
    return `${esc(label)}｜修復種類 ${esc(review.repair_kind || '未標')}`
      + `${review.previous_action ? `（原本是 ${esc(review.previous_action)}）` : ''}`;
  }
  return esc(label || '（狀態不明）');
}

/* 六個 option（最多 6 個；不足 6 個的題目只畫它有的）。
   值是「草稿優先、其次才是原始文字」：改到一半切走再回來，改動還在。 */
function discussOptions(candidate, key) {
  const draft = D.draft.get(key) || {};
  const options = candidate.options || [];
  return options.map((option, i) => {
    const value = draft['option_' + option.key] !== undefined
      ? draft['option_' + option.key] : (option.text || '');
    return `<div class="opt"><span class="k">${i + 1}／${esc(option.key)}</span>`
      + `<textarea id="dOpt_${esc(option.key)}" data-k="option_${esc(option.key)}">`
      + `${esc(value)}</textarea></div>`;
  }).join('');
}

function discussStem(candidate, key) {
  const draft = D.draft.get(key) || {};
  const value = draft._stem !== undefined ? draft._stem : (candidate.stem || '');
  return `<textarea id="dStem" data-k="_stem" style="min-height:64px">${esc(value)}</textarea>`;
}

/* ①機器偵測：disputes。畫在最前面，因為它決定下面每一行該怎麼讀。 */
function discussDisputesHtml(list) {
  if (!Array.isArray(list) || !list.length) return '<span class="hint">（沒有機器量到的爭議）</span>';
  return list.map((d) => {
    const badge = d.severity === 'blocker' ? 'blocker' : 'review';
    return `<div class="dispute ${badge}"><b>${esc(d.note || d.kind)}</b>`
      + `<span class="why">${esc(d.detail || '')}</span>`
      + `<code class="where">${esc(d.kind)}</code></div>`;
  }).join('');
}

/* ②AI 意見：finding。含模型看過的紙本截圖與機械 diff，因為「沒看過的證據不算證據」。 */
function discussFindingHtml(candidate) {
  const record = candidate.qbr_ai_finding;
  if (!record) return '<span class="hint">（這一題還沒有 AI 意見；常駐修理代理會排隊問它）</span>';
  const f = record.finding || {};
  const which = record.population === 'dispute' ? '爭議題對紙本確認'
    : record.population === 'corpus' ? '整庫掃描' : '人類阻擋後詢問';
  const parts = [];
  parts.push(`<div class="af-line"><b>判定：</b>${esc(f.verdict || '（無）')}`
    + `${f.what ? `／<code class="af-code">${esc(f.what)}</code>` : ''}`
    + `${f.rule_worthy === true ? '（被判斷為一類問題）' : ''}</div>`);
  if (f.where) parts.push(`<div class="af-line"><b>哪裡：</b>${esc(f.where)}</div>`);
  if (f.fix) parts.push(`<div class="af-line"><b>怎麼修：</b>${esc(f.fix)}</div>`);
  if (record.error) parts.push(`<div class="af-line bad"><b>讀取失敗：</b>${esc(record.error)}</div>`);
  const crop = record.crop
    ? `<figure class="af-crop"><img src="${esc(fileUrl(record.crop))}" alt="模型看到的紙本截圖"
         loading="lazy" onclick="window.open(this.src,'_blank')">
       <figcaption>模型看到的紙本截圖（${esc(record.model || '未知模型')}）</figcaption></figure>`
    : '<div class="af-line bad">這一筆意見沒有截圖，無法核對。</div>';
  const raw = (c) => (c.stored !== undefined && c.stored !== null ? c.stored : c.from);
  const rawTo = (c) => (c.page !== undefined && c.page !== null ? c.page : c.to);
  const changes = Array.isArray(record.changes) && record.changes.length
    ? '<div class="af-line"><b>機械比對（程式逐字相減）：</b></div>'
      + record.changes.map((c) => `<div class="af-change"><code>${esc(c.field)}</code>`
        + `<span class="from">${esc(String(raw(c) || '').slice(0, 160))}</span>`
        + '<span class="arrow">→</span>'
        + `<span class="to">${esc(String(rawTo(c) || '').slice(0, 160))}</span>`
        + `<button class="af-apply" data-field="${esc(c.field)}">帶入</button></div>`).join('')
    : '';
  return `<div class="ai-finding"><div class="af-head"><span>${esc(which)}</span>`
    + `<span class="af-hint">${esc(record.model || '未知模型')}`
    + `${record.prompt_version ? ` · 提示詞 ${esc(record.prompt_version)}` : ''}</span></div>`
    + `${crop}${parts.join('')}${changes}</div>`;
}

/* ④基本原則：可加可減，會被編進提示詞。 */
function discussPrinciplesHtml() {
  const rows = (D.principles.principles || []).map((p) => {
    return `<li class="principle" data-id="${esc(p.principle_id)}">`
      + `<span class="ptext">${esc(p.text)}</span>`
      + `<button class="ghost pl-remove" data-id="${esc(p.principle_id)}"
           title="移除（append-only：是加一筆 remove，不是刪掉歷史）">移除</button></li>`;
  }).join('');
  const input = D.principleDraft
    ? `<div class="principle-add"><textarea id="dpNew" placeholder="一句可以被機器遵守的原則，例如：中文詞中間不該有空格。Enter 加入、Shift+Enter 換行"></textarea>
       <button class="act" id="dpSave">加入</button>
       <button class="ghost" id="dpCancel">取消</button></div>`
    : '<button class="ghost" id="dpStart">＋ 新增原則</button>';
  return `<div class="principles"><div class="ph-head">基本原則（${(D.principles.principles || []).length}）`
    + '<span class="hint">會被編成修題／AI 審核的提示詞，讓模型不無限推論</span></div>'
    + `<ul class="pl-list">${rows || '<li class="hint">（還沒有原則）</li>'}</ul>${input}</div>`;
}

/* ⑤AI 反問：模型讀不懂時，反問這裡，人回答。未回答的排最前面。 */
function discussQuestionsHtml() {
  const rows = D.questions.questions || [];
  const body = rows.length ? rows.map((row) => {
    const mine = D.answerDraft.get(row.question_id);
    const answer = row.answer_text || mine || '';
    return `<li class="rq${row.open ? ' open' : ''}" data-id="${esc(row.question_id)}">`
      + `<div class="rq-q"><b>${row.open ? '待回答' : '已回答'}</b>`
      + `${row.candidate_key ? ` <code>${esc(String(row.candidate_key).slice(-18))}</code>` : ''}`
      + `　${esc(row.question || '')}</div>`
      + `${row.reason ? `<div class="hint">為什麼問：${esc(row.reason)}</div>` : ''}`
      + `<textarea class="rq-a" data-id="${esc(row.question_id)}" placeholder="回答（Enter 送出、Shift+Enter 換行）">${esc(answer)}</textarea>`
      + `<button class="act rqSave" data-id="${esc(row.question_id)}"${row.answer_text ? ' disabled' : ''}>送出回答</button>`
      + (row.answer_text ? `<span class="hint">已回覆於 ${esc(String(row.answer_at || '').slice(5, 16))}</span>` : '')
      + '</li>';
  }).join('') : '<li class="hint">（修理代理沒有卡住的地方。）</li>';
  return `<div class="repair-qs"><div class="ph-head">修理代理的反問（${D.questions.open_count || 0} 題待答）`
    + '<span class="hint">模型讀不懂時會停在這裡，而不是猜</span></div>'
    + `<ul class="rq-list">${body}</ul></div>`;
}

/* 中欄：依序是「為什麼卡住 → ① 機器 → ② AI → ③ 編輯 → ④ 原則 → ⑤ 反問」。
   順序就是閱讀順序：先知道它為什麼在這裡，再看機器量到什麼、模型說了什麼，然後才是自己的編輯。 */
function discussCenterHtml(candidate, key) {
  const number = candidate.question_number ?? '?';
  const subject = ((candidate.metadata || {}).normalized_subject_name)
    || (candidate.metadata || {}).group_name || candidate.paper_id || '';
  return '<div class="case">'
    + `<h2>第 ${esc(number)} 題 <span class="kind">${esc(subject)}</span></h2>`
    + `<div class="meta">為什麼在這裡：${discussWhyHtml(candidate)}</div>`
    + `<div class="diff"><b>① 機器偵測</b>${discussDisputesHtml(candidate.disputes)}</div>`
    + `<div class="diff" style="margin-top:10px"><b>② AI 意見</b>${discussFindingHtml(candidate)}</div>`
    + '<div class="edit-block"><b>③ 手動修改（照紙本打，錯字才改）</b>'
    + '<label>題幹</label>' + discussStem(candidate, key)
    + '<label>選項</label>' + discussOptions(candidate, key)
    + '<div class="dirty" id="dDirty" style="display:none">已改動，按「儲存修正」寫入。</div>'
    + '</div>'
    + discussPrinciplesHtml()
    + discussQuestionsHtml()
    + discussSideHtml()
    + '<div class="answer-bar">'
    + '<button class="ghost" data-focus="8">8 紙本</button>'
    + '<button class="act" id="dSave">儲存修正 E</button>'
    + '<button class="ghost" id="dHold">退回未審</button>'
    + '<button class="ghost" id="dClear">清空草稿</button></div>'
    + '</div>';
}

/* 右欄：紙本原卷（題目卷），與左欄脫鉤。 */
function discussPdfHtml(candidate) {
  const pdf = (candidate.source_files || {}).official_pdf
    || (candidate.metadata || {}).question_pdf_relative;
  const head = `<div class="pdf-head">紙本 第 ${esc(candidate.question_number ?? '?')} 題`
    + `<span class="hint">${esc(candidate.paper_id || '')}</span></div>`;
  const body = pdf
    ? `<iframe id="dPdf" class="discuss-frame" title="官方題目 PDF"
         src="${esc(fileUrl(pdf))}#view=FitH"></iframe>`
    : '<div class="empty-area">這一題沒有官方 PDF 路徑。請不要憑抽出文字判斷。</div>';
  return head + body;
}

function focusDiscuss(n) {
  if (n === 8) { const el = $('dPdf'); if (el && el.focus) el.focus(); return; }
  // 1..6 聚焦第 n 個選項的編輯框。以**順序**而不是選項字母對映，因為紙本用的是題號數字，
  // 選項字母（A/B/C/D）只是抽取時給的鍵；兩者不必相同。
  const boxes = document.querySelectorAll('#discussMain .opt textarea');
  const el = boxes[n - 1];
  if (el && el.focus) el.focus();
}

/* 側欄統計。每一個數字都從同一次 `/api/discuss` 的資料算出來，不另外打端點。 */
function discussSideHtml() {
  const total = D.rows.length;
  const withAi = D.rows.filter((k) => (D.byKey.get(k) || {}).qbr_ai_finding).length;
  const blocked = D.rows.filter((k) => (((D.byKey.get(k) || {}).review || {}).action) === 'block').length;
  return '<div id="discussSide" class="discuss-side"><h3>統計</h3>'
    + `<div class="row"><span>卡住的題</span><b class="n">${total}</b></div>`
    + `<div class="row"><span>人阻擋</span><b class="n">${blocked}</b></div>`
    + `<div class="row"><span>有 AI 意見</span><b class="n">${withAi}</b></div>`
    + `<div class="row"><span>基本原則</span><b class="n">${(D.principles.principles || []).length}</b></div>`
    + `<div class="row"><span>待回答反問</span><b class="n">${D.questions.open_count || 0}</b></div></div>`;
}

/* 綁定：編輯框的 Enter 儲存、選項聚焦、原則新增／移除、反問回答、以及 finding 的「帶入」。 */
function bindDiscuss(key) {
  const dirty = () => { const d = $('dDirty'); if (d) d.style.display = 'block'; };
  for (const node of document.querySelectorAll('#discussMain textarea[data-k]')) {
    node.oninput = dirty;
    node.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveDiscuss(key); } };
  }
  const save = $('dSave'); if (save) save.onclick = () => saveDiscuss(key);
  const clear = $('dClear');
  if (clear) clear.onclick = () => { D.draft.delete(key); renderDiscuss(); };
  const hold = $('dHold');
  if (hold) hold.onclick = () => resetDiscuss(key);
  // 反問的回答：Enter 送出、Shift+Enter 換行。以 `data-id`（question_id）認，不以行序。
  for (const node of document.querySelectorAll('#discussMain .rq-a')) {
    node.oninput = () => D.answerDraft.set(node.dataset.id, node.value);
    node.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); answerRepairQuestion(node.dataset.id); }
    };
  }
  for (const button of document.querySelectorAll('#discussMain .rqSave')) {
    button.onclick = () => answerRepairQuestion(button.dataset.id);
  }
  const start = $('dpStart');
  if (start) start.onclick = () => { D.principleDraft = true; renderDiscuss(); };
  const cancel = $('dpCancel');
  if (cancel) cancel.onclick = () => { D.principleDraft = false; renderDiscuss(); };
  const dpSave = $('dpSave');
  if (dpSave) dpSave.onclick = addPrinciple;
  const newBox = $('dpNew');
  if (newBox) newBox.onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); addPrinciple(); }
  };
  for (const button of document.querySelectorAll('#discussMain .pl-remove')) {
    button.onclick = () => removePrinciple(button.dataset.id);
  }
  // 模型機械比對的「帶入」：只把那一欄的紙本讀法填進編輯框，不自己寫入任何東西。
  for (const button of document.querySelectorAll('#discussMain .af-apply')) {
    button.onclick = () => applyFindingChange(button);
  }
}

/* 把 model 的一條機械 diff 帶進編輯框。**不寫入**——人讀過再按儲存。 */
function applyFindingChange(button) {
  const key = D.rows[D.index];
  const record = (D.byKey.get(key) || {}).qbr_ai_finding;
  if (!record || !Array.isArray(record.changes)) return;
  const field = button.dataset.field;
  const change = record.changes.find((c) => c.field === field);
  if (!change) return;
  const page = change.page !== undefined ? change.page : change.to;
  const node = field === 'stem' ? $('dStem') : document.getElementById(`dOpt_${field.replace('option ', '')}`);
  if (!node) return;
  node.value = page || node.value;
  const draft = D.draft.get(key) || {};
  draft[field === 'stem' ? '_stem' : 'option_' + field.replace('option ', '')] = node.value;
  D.draft.set(key, draft);
  const d = $('dDirty'); if (d) d.style.display = 'block';
  toast(`已把「${field}」的紙本讀法帶入；請自己確認後再按儲存修正`);
}

/* 儲存修正：走既有的 `/api/review` `correct` 事件，不新增寫入路徑。
   這一區的價值是「能修」，而能修的前提是修正紀錄與題目區寫的是**同一種**事件。 */
async function saveDiscuss(key) {
  if (!key) return;
  const candidate = D.byKey.get(key) || {};
  const stem = ($('dStem') || {}).value;
  const options = (candidate.options || []).map((option) => ({
    key: option.key,
    text: (document.getElementById(`dOpt_${option.key}`) || {}).value ?? option.text,
    image: option.image ?? null,
  }));
  if (stem === undefined) return;
  if (stem === (candidate.stem || '')
      && options.every((o, i) => o.text === ((candidate.options || [])[i] || {}).text)) {
    toast('沒有改動', true);
    return;
  }
  try {
    const response = await fetch('/api/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate_key: key, action: 'correct',
        correction: { stem, options, answer: candidate.answer || '' },
        notes: '錯題討論區依紙本修正。', reviewer: 'local', source: 'linear_v2_discuss',
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    D.draft.delete(key);
    D.seen.set(key, true);
    invalidateAreas();
    toast(`第 ${candidate.question_number} 題：修正已儲存`);
    renderDiscuss(true);
  } catch (error) {
    toast(`修正儲存失敗：${error.message || error}`, true);
  }
}

/* 退回未審：寫一筆 `reset_review`，讓題目回到待審。這是人對這一題的最後決定，不是 agent。
   這裡**只由人按**；常駐代理永遠停在 G2，不碰這個按鈕的路徑。 */
async function resetDiscuss(key) {
  if (!key) return;
  const candidate = D.byKey.get(key) || {};
  try {
    const response = await fetch('/api/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate_key: key, action: 'reset_review',
        notes: '錯題討論區：退回待審。', reviewer: 'local', source: 'linear_v2_discuss',
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    invalidateAreas();
    toast(`第 ${candidate.question_number} 題：已退回未審`);
    renderDiscuss(true);
  } catch (error) {
    toast(`退回失敗：${error.message || error}`, true);
  }
}

/* 新增一條基本原則。 */
async function addPrinciple() {
  const box = $('dpNew');
  const text = box ? box.value.trim() : '';
  if (!text) { toast('原則是空的', true); return; }
  try {
    const response = await fetch('/api/principles', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'add', text, reviewer: 'local' }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    D.principles = data;
    D.principleDraft = false;
    renderDiscuss();
    toast('原則已加入；下一次修題會帶進提示詞');
  } catch (error) {
    toast(`加入失敗：${error.message || error}`, true);
  }
}

/* 移除一條基本原則：append-only，所以是送一格 `remove`，不是刪檔。 */
async function removePrinciple(principleId) {
  if (!principleId) return;
  try {
    const response = await fetch('/api/principles', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'remove', principle_id: principleId, reviewer: 'local' }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    D.principles = data;
    renderDiscuss();
    toast('原則已移除（歷史留著，只是不再生效）');
  } catch (error) {
    toast(`移除失敗：${error.message || error}`, true);
  }
}

/* 回答修理代理的反問。 */
async function answerRepairQuestion(questionId) {
  if (!questionId) return;
  const box = document.querySelector(`#discussMain .rq-a[data-id="${questionId}"]`);
  const answer = box ? box.value.trim() : (D.answerDraft.get(questionId) || '');
  if (!answer) { toast('回答是空的', true); return; }
  try {
    const response = await fetch('/api/repair-question', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'answer', question_id: questionId, answer, reviewer: 'local' }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    D.questions = data;
    D.answerDraft.delete(questionId);
    renderDiscuss();
    toast('已回答；代理下一輪會讀到');
  } catch (error) {
    toast(`回答失敗：${error.message || error}`, true);
  }
}
