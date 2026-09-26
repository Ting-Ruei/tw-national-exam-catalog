/* 四區表、`A` 快取、`fetchAreaJson`、`renderArea`；首頁（ counts 只讀伺服器數）＋答案（单卷，`limit` 精簡）。 */
const AREA_BY_NAME = { home: 'home', question: 'question', answer: 'answer', discuss: 'discuss' };
//: The hash prefix for each area. `審題` is the question area's own name and is accepted too, so a
//: link written either way works.
const AREA_PREFIX = { home: '首頁', question: '審題', answer: '答案', discuss: '錯題' };
const PREFIX_AREA = { 首頁: 'home', 審題: 'question', 題目: 'question', 答案: 'answer', 錯題: 'discuss' };
const AREA_LABEL = { home: '首頁', question: '題目審核區', answer: '答案審核區', discuss: '錯題討論區' };

//: One fetch of each area's payload per session, invalidated when a decision could have changed it.
const A = { area: 'question', sheets: null, sheetIndex: 0, sheet: null, answerDraft: new Map(),
            noteDraft: new Map(), feedback: null, error: {}, rendered: {} };

function areaFromHash() {
  const raw = decodeURIComponent((location.hash || '').replace(/^#/, ''));
  if (!raw) return 'question';  // the baseline is the question area; an empty hash is not the home page
  const head = raw.split('/')[0];
  return PREFIX_AREA[head] || 'question';
}

function showArea(area, { push = true } = {}) {
  const next = AREA_BY_NAME[area] ? area : 'question';
  A.area = next;
  for (const button of document.querySelectorAll('.area-btn')) {
    button.classList.toggle('on', button.dataset.area === next);
  }
  // A mode that is not chosen is **hidden**, never emptied: the answer area keeps the sheet the
  // reviewer was reading and the note they were typing, and a trip through 首頁 must not discard it.
  const nodes = { home: 'areaHome', question: 'areaQuestion', answer: 'areaAnswer', discuss: 'areaDiscuss' };
  for (const [name, id] of Object.entries(nodes)) {
    const node = $(id);
    if (node) node.classList.toggle('on', name === next);
  }
  // The hash carries a mode prefix whenever we are not in the question area, so a reload reopens
  // the same area. The suffix (the scope, and an optional `qNNN`) is preserved as it is: the answer
  // and discussion areas have no scope of their own, and the question area's existing
  // `#類科/年/次/科目/qNNN` spelling must survive a trip out and back.
  const raw = decodeURIComponent((location.hash || '').replace(/^#/, ''));
  const head = raw.split('/')[0];
  const rest = PREFIX_AREA[head] ? raw.split('/').slice(1).join('/') : raw;
  const wanted = next === 'question' ? `#${rest}` : `#${AREA_PREFIX[next]}${rest ? '/' + rest : ''}`;
  // `replaceState`, not a push: the back button should leave the area, not walk through renders of it.
  if (location.hash !== wanted) history.replaceState(null, '', wanted);
  if (next !== 'question') renderArea(next);
  if (push) $('whoami').textContent = `${AREA_LABEL[next]} · ${S.rows.length} 題`;
}

for (const button of document.querySelectorAll('.area-btn')) {
  button.onclick = () => showArea(button.dataset.area);
}
window.addEventListener('hashchange', () => {
  const area = areaFromHash();
  if (area !== A.area) showArea(area, { push: false });
  else if (area === 'question') applyScope();
});

function renderArea(area, { force = false } = {}) {
  // Render **once** per area. Re-entering a pane must not rebuild its DOM: the reviewer's half-typed
  // answer note lives in that DOM and there is no other copy of it. The pane's data is invalidated
  // only by something that actually changed it (a decision, an answer write), via `invalidateAreas()`.
  if (A.rendered[area] && !force) return;
  A.rendered[area] = true;
  if (area === 'answer') renderAnswers();
  else if (area === 'discuss') renderDiscuss();
  else if (area === 'home') renderHome();
}

//: Called by anything that writes. The answer area's eligible set and the discussion area's case
//: list are both derived from decisions taken in the question area, so a question decision makes
//: all three panes stale.
function invalidateAreas() {
  A.rendered = {};
  A.sheets = null;
  A.feedback = null;
}

/* --------------------------------------------------------------- 首頁
   The home page answers one question - "what is left" - from three endpoints that already exist.
   It computes nothing of its own: every number here is the server's count of the same thing the
   area it links to will show, because a dashboard that disagrees with the page behind it is worse
   than no dashboard. */
async function renderHome() {
  const cards = $('homeCards');
  cards.innerHTML = '<div class="empty">載入中…</div>';
  const [questions, answers, feedback] = await Promise.all([
    fetchAreaJson('/api/candidates', { _count: 1 }),
    fetchAreaJson('/api/answer-candidates', { limit: 1 }),
    fetchAreaJson('/api/correction-feedback', { limit: 500 }),
  ]);
  const total = questions ? Number(questions.total_count ?? questions.filtered_count ?? 0) : null;
  const answered = questions ? Number(questions.reviewed_count ?? 0) : null;
  const eligible = answers ? Number(answers.eligible_count ?? 0) : null;
  const answerReviewed = answers ? Number(answers.reviewed_count ?? 0) : null;
  const cases = feedback && Array.isArray(feedback.events) ? feedback.events.length : null;
  const card = (title, big, note, area, action) => `
    <div class="card" data-go="${area}">
      <h2>${esc(title)}</h2>
      <span class="big">${big === null ? '—' : big}</span>
      <p>${esc(note)}</p>
      <span class="go">${esc(action)} →</span>
    </div>`;
  cards.innerHTML = [
    card('題目審核區', total === null ? null : `${answered ?? 0}／${total}`,
         '一題一題讀紙本對照抽取文字。還沒過目的題目會擋住它的答案。', 'question', '開始審題'),
    card('答案審核區', eligible === null ? null : `${answerReviewed ?? 0}／${eligible}`,
         '只審已通過題目的答案卡。沒通過的題目不能審答案，伺服器會直接拒繪。', 'answer', '審答案'),
    card('錯題討論區', cases === null ? null : `${cases}`,
         '人工修過的個案，依「修改的類型」排列：這一區是把修正累積成可學的東西的地方。', 'discuss', '看個案'),
  ].join('');
  for (const node of cards.querySelectorAll('[data-go]')) {
    node.onclick = () => showArea(node.dataset.go);
  }
  const scope = $('homeScope');
  const crumbs = S.scope.category ? ['', S.scope.year, S.scope.sitting, S.scope.subject]
    .filter((v, i) => i === 0 || v) : [];
  const where = [S.scope.category || '（未選）', S.scope.year || '', S.scope.sitting || '',
                 S.scope.subject || ''].filter(Boolean).join(' · ');
  scope.innerHTML = [
    `<li>目前範圍：<b>${esc(where || '（未選）')}</b></li>`,
    `<li>這個範圍的題目：<b>${S.rows.length}</b> 題（清單正在顯示的數）</li>`,
    `<li>分類樹是伺服器從 <code>queue_index.json</code> 給的，不是這裡算的。</li>`,
  ].join('');
}

/* --------------------------------------------------------------- 共用的小工具 */
async function fetchAreaJson(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, value);
  }
  try {
    const response = await fetch(`${path}?${query}`, { cache: 'no-store' });
    if (!response.ok) {
      A.error[path] = `HTTP ${response.status}`;
      return null;
    }
    delete A.error[path];
    return await response.json();
  } catch (error) {
    A.error[path] = String(error.message || error);
    return null;
  }
}

/* --------------------------------------------------------------- 答案審核區

   The gate is the server's, and this area only mirrors it: `/api/answer-candidates` returns
   questions whose question review is `accept`/`unblock`, and `/api/answer-review` refuses to pass an
   item whose question has not been accepted. Both are read from the same place, so a reviewer never
   sees an answer card the server would reject.

   The area is a **sheet** at a time, because that is the unit the paper is: one answer PDF covers
   one subject of one sitting, and the answers are read off it row by row. Reading it question by
   question would mean re-finding the sheet on every step.

   A correction is written through `/api/answer-review` with the reviewer's own `corrected_answer`,
   which is what makes the server record a `question_correction_feedback_events` row with
   `scope=answer` and `change_class=answer_rule` (see `_record_answer_correction_feedback`). That row
   is the lesson the 錯題討論區 shows - so this area does not have to learn anything itself. */
async function renderAnswers() {
  const list = $('sheetList');
  const main = $('answerMain');
  if (!A.sheets) {
    list.innerHTML = '<div class="empty">載入中…</div>';
    // 一巻為一單位：一份答案卷的列 ≤ 題數（實測最大 100）。`200` 覆蓋單巻且把承載壓到
// 約 1/5（實測 `limit=1000` 為 7.8 MB、`limit=60` 為 6.0 MB → 差在首屏的列數，
// 故 200 是「一巻＋同考次相隣巻」的寬度；多過無益，少則斷導覽（charter §1 第 4 項）。
    const payload = await fetchAreaJson('/api/answer-candidates', { limit: 200 });
    if (!payload) {
      list.innerHTML = `<div class="empty">讀不到答案候選（${esc(A.error['/api/answer-candidates'] || '')}）</div>`;
      return;
    }
    A.sheets = payload.candidates || [];
  }
  if (!A.sheets.length) {
    list.innerHTML = '<div class="empty">這一卷沒有可審的答案卡。答案卡只收「題目已通過」的題目。</div>';
    main.innerHTML = '<div class="empty">沒有可審的答案。</div>';
    return;
  }
  if (A.sheetIndex >= A.sheets.length) A.sheetIndex = 0;
  list.innerHTML = A.sheets.map((sheet, index) => {
    const meta = sheet.metadata || {};
    const done = Number(sheet.reviewed_count || 0);
    const count = Number(sheet.reviewable_question_count ?? sheet.question_count ?? 0);
    return `<button class="sheet-row${index === A.sheetIndex ? ' active' : ''}${done && done >= count ? ' done' : ''}" data-sheet="${index}">
      <span class="t">${esc(meta.normalized_subject_name || sheet.sheet_key || '(未知科目)')}</span>
      <span class="m">${esc([meta.normalized_category_name, meta.year && `${meta.year} 年`,
        meta.exam_ordinal && `第 ${meta.exam_ordinal} 次`, `${done}／${count}`].filter(Boolean).join(' · '))}</span>
    </button>`;
  }).join('');
  for (const row of list.querySelectorAll('[data-sheet]')) {
    row.onclick = () => { A.sheetIndex = Number(row.dataset.sheet); renderAnswers(); };
  }
  renderAnswerSheet();
}

function renderAnswerSheet() {
  const main = $('answerMain');
  const sheet = A.sheets[A.sheetIndex];
  if (!sheet) { main.innerHTML = '<div class="empty">沒有這一張答案卡。</div>'; return; }
  const meta = sheet.metadata || {};
  const rows = sheet.rows || [];
  const head = `
    <h1>${esc(meta.normalized_subject_name || '(未知科目)')}</h1>
    <p class="answer-sub">${esc([meta.normalized_category_name, meta.year && `${meta.year} 年`,
      meta.exam_ordinal && `第 ${meta.exam_ordinal} 次`, sheet.answer_role_label &&
      `來源：${sheet.answer_role_label}`].filter(Boolean).join(' · '))}
      ｜ 已審 ${Number(sheet.reviewed_count || 0)}／${Number(sheet.reviewable_question_count ?? rows.length)}</p>`;
  const body = rows.map((row) => answerRowHtml(row)).join('');
  main.innerHTML = `${head}
    <table class="atable"><thead><tr>
      <th style="width:52px">題</th><th>題幹</th><th style="width:210px">答案</th><th style="width:120px">審核</th>
    </tr></thead><tbody>${body}</tbody></table>
    <div class="answer-bar">
      <button class="ans-btn" data-sheet-action="accept">整份通過</button>
      <button class="ans-btn" data-sheet-action="needs_review">整份保留疑問</button>
      <button class="ans-btn" data-sheet-action="block">整份阻擋</button>
      <span class="hint" id="answerSaved"></span>
    </div>`;
  bindAnswerSheet();
}

function answerRowHtml(row) {
  if (row.is_placeholder) {
    return `<tr class="blocked"><td class="qnum">${esc(row.question_number)}</td>
      <td class="stem" colspan="3">${esc(row.placeholder_reason || '題目尚未通過審核')}</td></tr>`;
  }
  const review = row.answer_review || {};
  const hint = row.answer_hint || {};
  const done = review.status === 'reviewed';
  const blocked = review.action === 'block';
  const options = (row.options || []).map((option) =>
    `<span class="ans-btn${String(review.correction?.answer ?? row.answer ?? '') === option.key ? ' on' : ''}"
       data-answer="${esc(option.key)}" data-key="${esc(row.candidate_key)}">${esc(option.key)}</span>`).join(' ');
  const stem = String(row.stem || '').slice(0, 240);
  return `<tr class="${blocked ? 'blocked' : done ? 'done' : ''}" data-key="${esc(row.candidate_key)}">
    <td class="qnum">${esc(row.question_number)}</td>
    <td class="stem">${richText(stem)}
      <div class="opts">${(row.options || []).map((o) => `${esc(o.key)}. ${richText(String(o.text || '').slice(0, 60))}`).join('　')}</div></td>
    <td class="ans"><span class="ans-current" id="ansNow_${esc(row.question_number)}">${esc(review.correction?.answer ?? row.answer ?? '—')}</span>
      <div>${options}</div>
      ${hint.severity === 'warning' ? `<span class="ans-warn">${esc(hint.message || '答案有疑慮')}</span>` : ''}</td>
    <td>${done ? `<span class="hint">${esc(LABEL[review.action] || review.action || '已審')}</span>` : '<span class="hint">未審</span>'}
      <textarea class="ans-note" data-note="${esc(row.candidate_key)}" placeholder="註記（可留空）">${esc(A.noteDraft.get(row.candidate_key) ?? review.notes ?? '')}</textarea></td>
  </tr>`;
}

function bindAnswerSheet() {
  const main = $('answerMain');
  // A typed note is kept in memory as it is typed, so switching to another sheet and back - or
  // switching to 首頁 - does not throw away a note the reviewer was in the middle of writing. It is
  // a draft, not a decision: nothing is sent until one of the buttons below is pressed.
  for (const node of main.querySelectorAll('[data-note]')) {
    node.addEventListener('input', () => A.noteDraft.set(node.dataset.note, node.value));
  }
  // Clicking an option **drafts** the answer; it does not save. A stray click on a 4-row table must
  // not silently rewrite an answer, so the change is only sent by one of the buttons below, which is
  // also where the note and the reviewer are read from.
  for (const button of main.querySelectorAll('[data-answer]')) {
    button.onclick = () => {
      const key = button.dataset.key;
      A.answerDraft.set(key, button.dataset.answer);
      const row = main.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
      if (row) {
        const now = row.querySelector('.ans-current');
        if (now) now.textContent = button.dataset.answer;
        for (const sibling of row.querySelectorAll('[data-answer]')) {
          sibling.classList.toggle('on', sibling === button);
        }
      }
    };
  }
  for (const button of main.querySelectorAll('[data-sheet-action]')) {
    button.onclick = () => answerSheetAction(button.dataset.sheetAction);
  }
}

async function answerSheetAction(action) {
  const sheet = A.sheets[A.sheetIndex];
  if (!sheet) return;
  const main = $('answerMain');
  const notes = {};
  for (const node of main.querySelectorAll('[data-note]')) {
    notes[node.dataset.note] = node.value;
    A.noteDraft.set(node.dataset.note, node.value);
  }
  // One request per row that has something to say. `needs_review` and `block` are per row as well,
  // because an answer sheet's defects are per answer - one wrong key does not make the other 79
  // wrong, and marking them so would be recording a decision nobody made.
  const entries = [];
  for (const row of sheet.rows || []) {
    if (row.is_placeholder || !row.candidate_key) continue;
    const drafted = A.answerDraft.get(row.candidate_key);
    const review = row.answer_review || {};
    const note = notes[row.candidate_key] ?? A.noteDraft.get(row.candidate_key) ?? review.notes ?? '';
    const already = review.status === 'reviewed';
    let rowAction = action;
    if (drafted && drafted !== String(row.answer ?? '')) rowAction = 'correct';
    else if (!drafted && action === 'correct') continue;
    else if (action === 'accept' && already && rowAction === review.action && !note) continue;
    const entry = {
      candidate_key: row.candidate_key, action: rowAction, notes: note, reviewer: 'local',
      // `reviewed_answer` is what the server stores as "the answer this reviewer accepted"; omitting it
      // would store `{"answer": null}` and silently blank the answer the sheet is being judged
      // against. It is sent from the row, not from the draft, because it means "what was on the paper".
      reviewed_answer: { answer: row.answer ?? null },
      answer_source_registry_key: (row.answer_payload || {}).answer_source_registry_key
        || row.answer_source_registry_key || '',
      sheet_key: sheet.sheet_key || '',
      sheet_action: action,
    };
    if (drafted) entry.corrected_answer = drafted;
    entries.push(entry);
  }
  if (!entries.length) {
    const saved = $('answerSaved');
    if (saved) saved.textContent = '沒有要寫入的項目（沒有改答案、也沒有註記）。';
    return;
  }
  // `/api/answer-review-batch` takes **one** action for the whole request and ignores a per-entry
  // `action` (see the handler: `action = payload.get("action")` then every event is stamped with
  // it). So the rows are grouped by the action they actually earned and sent as one request per
  // group. Sending one batch with a per-row action would have silently written the sheet's action
  // onto the rows that disagreed with it - i.e. recorded 79 decisions nobody made.
  const groups = new Map();
  for (const entry of entries) {
    const key = entry.action;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(entry);
  }
  let savedTotal = 0;
  let failure = null;
  for (const [groupAction, groupEntries] of groups) {
    const response = await fetch('/api/answer-review-batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: groupAction, entries: groupEntries, reviewer: 'local',
                             sheet_key: sheet.sheet_key || '', sheet_action: action }),
    });
    const data = await response.json().catch(() => ({ ok: false, error: '回應不是 JSON' }));
    if (!data.ok) { failure = data; break; }
    savedTotal += Number(data.saved_count ?? groupEntries.length);
  }
  const saved = $('answerSaved');
  if (failure) {
    if (saved) saved.textContent = `寫入失敗：${failure.error || ''}`;
    toast(`答案審核寫入失敗：${failure.error || ''}`, true);
    return;
  }
  if (saved) saved.textContent = `已寫入 ${savedTotal} 筆。`;
  toast(`已寫入 ${savedTotal} 筆答案審核`);
  A.answerDraft.clear();
  A.noteDraft.clear();
  invalidateAreas();
  await renderAnswers();
}
