/* 答案審核區。原本長在 `03-areas.js` 裡（那一檔現在只留外殼：區表、`showArea`、首頁）。
   拆出來的理由是可平行修改與可讀性：這一區要加答案卷 PDF、每題阻擋、多選答案，
   而外殼不該跟著動。載入序＝檔名序，`03-area-answer.js` 在 `03-areas.js` 之前。 */
/* --------------------------------------------------------------- 答案審核區

   The gate is the server's, and this area only mirrors it: `/api/answer-candidates` returns
   questions whose question review is `accept`/`unblock`, and `/api/answer-review` refuses to pass an
   item whose question has not been accepted. Both are read from the same place, so a reviewer never
   sees an answer card the server would reject.

   The area is a **sheet** at a time, because that is the unit the paper is: one answer PDF covers
   one subject of one sitting, and the answers are read off it row by row. Reading it question by
   question would mean re-finding the sheet on every step.

   The reviewer's own words for this rebuild: 「答案審核區我強烈建議你參考舊 UI，右邊必須要有答案
   PDF 讓我對照，並且除了整份，可以對各題打「阻擋」，阻擋按鈕按了變紅色，再按一下可以取消，ABCD
   也要這種形式，按了表示標準答案但有可能是多答案，這時候按一下又可以取消，變成不是單選而是多選
   的彈性」. So: the paper on the right (`renderAnswerPdf`), a 阻擋 switch per row that is red while
   it is on, and a letter editor whose *shape* is switchable (單選／任一／任一+複選／複選) instead of
   one fixed answer field.

   A correction is written through `/api/answer-review` (a switch) or `/api/answer-review-batch`
   (the sheet bar) with the reviewer's own `corrected_answer`, which is what makes the server record
   a `question_correction_feedback_events` row with `scope=answer` and `change_class=answer_rule`
   (see `_record_answer_correction_feedback`). That row is the lesson the 錯題討論區 shows - so this
   area does not have to learn anything itself. */

/* ------------------------------------------------------------------ 一個字母集合，四種寫法

   The storage format is the server's, not this file's: the pipeline's only parser splits on `|`
   (任一) and keeps `+` as one value (複選), so `A|C` and `A+C` are different claims about the same
   two letters (「這兩個都可以」 vs 「兩個都要」). That is why the modes are *buttons on one letter
   set* rather than four separate editors: the letters are the fact, the joiner is how the fact is
   spelled. Ported from the legacy console (`parseAnswerSelection`/`formatAnswerSelection` there),
   because the format is what the pipeline reads. */
const ANSWER_MODES = [
  ['single', '單選'],
  ['any', '任一'],
  ['any_combo', '任一+複選'],
  ['all', '複選'],
];
//: What each mode does to the letters, said in the reviewer's terms - shown as the button's tooltip
//: and spelled out in words under the sheet head, because 「複選＋」 alone does not say `A+C`.
const ANSWER_MODE_HINT = {
  single: '單選：只留一個字母；再點別的字母會取代它',
  any: '任一：多個可接受的答案，存成 A|C',
  any_combo: '任一+複選：展開所有可能組合，ABC 存成 A|B|C|AB|AC|BC|ABC',
  all: '複選：需同時符合，存成 A+C',
};
//: The three documents a sheet can show, in the order the legacy 答案核對 pane offered them.
const ANSWER_PDF_KINDS = [
  ['official_pdf', '官方 PDF'],
  ['mineru_layout_pdf', 'MinerU layout'],
  ['mineru_origin_pdf', 'MinerU origin'],
];
//: Which document the right pane is showing, and **which file has actually been assigned to the
//: iframe**. They are different states: `path` is what the viewer already holds, so re-assigning it
//: (even to the same file) would reload the document and throw the reviewer's scroll away.
const ANSWER_PANE = { kind: 'official_pdf', path: null, sheetKey: '' };

function parseAnswerSelection(value, preferredMode = '') {
  const text = String(value ?? '').trim().toUpperCase();
  const letters = [];
  for (const letter of text.match(/[A-D]/g) || []) {
    if (!letters.includes(letter)) letters.push(letter);
  }
  let mode = preferredMode || 'single';
  if (text.includes('+')) mode = 'all';
  else if (letters.length > 1 && letters.some((letter) => text.includes(letters.join('')))) mode = 'any_combo';
  else if (text.includes('|') || letters.length > 1) mode = 'any';
  return { letters, mode, raw: text };
}

function formatAnswerSelection(letters, mode) {
  const unique = [];
  for (const letter of letters || []) {
    const normalized = String(letter || '').toUpperCase();
    if (/^[A-D]$/.test(normalized) && !unique.includes(normalized)) unique.push(normalized);
  }
  if (!unique.length) return '';
  if (mode === 'all') return unique.join('+');
  if (mode === 'any_combo' && unique.length > 1) return answerCombinationValues(unique).join('|');
  if (mode === 'any') return unique.join('|');
  return unique[unique.length - 1];
}

function answerCombinationValues(letters) {
  const unique = [];
  for (const letter of letters || []) {
    const normalized = String(letter || '').toUpperCase();
    if (/^[A-D]$/.test(normalized) && !unique.includes(normalized)) unique.push(normalized);
  }
  const combinations = [];
  const collect = (start, size, current) => {
    if (current.length === size) { combinations.push(current.join('')); return; }
    for (let index = start; index < unique.length; index += 1) collect(index + 1, size, [...current, unique[index]]);
  };
  for (let size = 1; size <= unique.length; size += 1) collect(0, size, []);
  return combinations;
}

/* ------------------------------------------------------------------ 一張答案卡 */

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
      renderAnswerPdf(null);
      return;
    }
    A.sheets = payload.candidates || [];
    // 寫入之後要停在**同一張卡**，不是同一個序號：伺服器把還沒審完的答案卡排前面（
    // `filtered_answer_payloads_sql` 的 sort），所以一張審完就會換位置，用序號還原等於跳到別張。
    if (ANSWER_PANE.sheetKey) {
      const hit = A.sheets.findIndex((sheet) => sheet.sheet_key === ANSWER_PANE.sheetKey);
      if (hit >= 0) A.sheetIndex = hit;
    }
  }
  if (!A.sheets.length) {
    list.innerHTML = '<div class="empty">這一卷沒有可審的答案卡。答案卡只收「題目已通過」的題目。</div>';
    main.innerHTML = '<div class="empty">沒有可審的答案。</div>';
    renderAnswerPdf(null);
    return;
  }
  if (A.sheetIndex >= A.sheets.length) A.sheetIndex = 0;
  list.innerHTML = A.sheets.map((sheet, index) => {
    const meta = sheet.metadata || {};
    const done = Number(sheet.reviewed_count || 0);
    const count = Number(sheet.reviewable_question_count ?? sheet.question_count ?? 0);
    return `<button class="sheet-row${index === A.sheetIndex ? ' active' : ''}${done && done >= count ? ' done' : ''}" data-sheet="${index}" data-key="${esc(sheet.sheet_key || '')}">
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
  if (!sheet) { main.innerHTML = '<div class="empty">沒有這一張答案卡。</div>'; renderAnswerPdf(null); return; }
  ANSWER_PANE.sheetKey = sheet.sheet_key || '';
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
    ${answerSummaryHtml(sheet)}
    ${answerBarHtml(true)}
    <table class="atable"><thead><tr>
      <th style="width:56px">題</th><th style="width:232px">答案</th><th style="width:104px">狀態</th>
      <th>題幹</th><th style="width:112px">疑點</th><th style="width:76px">阻擋</th>
    </tr></thead><tbody>${body}</tbody></table>
    ${answerBarHtml(false)}`;
  renderAnswerPdf(sheet);
  bindAnswerSheet();
}

/* 這張卡的數字**全部**來自 payload（`answer_sheet_payload` 算的），這裡一個都不自己算：
   前端算的數字與它指到的那一頁不一致時，比沒有數字更糟。舊 UI 的七格就是這七個。 */
function answerSummaryHtml(sheet) {
  const items = [
    ['total', '總題數', sheet.question_count],
    ['reviewable', '可核題數', sheet.reviewable_question_count],
    ['placeholder', '缺題保留', sheet.placeholder_count],
    ['reviewed', '已核答案', sheet.reviewed_count],
    ['accepted', '答案通過', sheet.accepted_count],
    ['corrected', '人工修正', sheet.corrected_count],
    ['attention', 'MOD 需確認', sheet.answer_attention_count],
  ];
  return `<div class="answer-sub" style="display:flex;flex-wrap:wrap;gap:3px 16px;margin-bottom:4px">`
    + items.map(([key, label, value]) => `<span class="ans-count" data-count="${key}">`
      + `<span class="hint">${label} </span><b>${Number(value || 0)}</b></span>`).join('')
    + '</div>'
    + `<p class="answer-sub" style="margin:0 0 12px">多答案點選規則：單選＝只留一個字母；`
    + `複選＝<code>A+C</code>（兩個都要）；任一＝<code>A|C</code>（兩個都可以）；`
    + `任一+複選＝展開所有組合，例如 ABC 存成 <code>A|B|C|AB|AC|BC|ABC</code>。`
    + 'MOD 若仍是 <code>#</code> 或空白，整份答案不可通過。</p>';
}

/* 整份的四個動作。舊 UI 在同一個畫面上下各放一列（上面讀完就按、下面捲到底也按得到），
   這裡照做：兩列是同一個建構子，`top` 只把 sticky 關掉（第一列在捲動區頂端，sticky 沒有意義）。 */
function answerBarHtml(top) {
  return `<div class="answer-bar"${top ? ' style="position:static;background:none;margin-top:0;margin-bottom:8px"' : ''}>
    <button class="ans-btn" data-sheet-action="accept">整份通過</button>
    <button class="ans-cancel" data-sheet-action="block" style="color:var(--red);border-color:#f0c7c3">整份阻擋</button>
    <button class="ans-btn" data-sheet-action="correct">儲存答案修正</button>
    <button class="ans-btn" data-sheet-action="needs_review">保留疑問</button>
    <span class="hint" data-saved></span>
  </div>`;
}

function answerRowHtml(row) {
  if (row.is_placeholder) {
    return `<tr class="blocked"><td class="qnum">${esc(row.question_number)}</td>
      <td class="stem" colspan="5">${esc(row.placeholder_reason || '題目尚未通過審核')}</td></tr>`;
  }
  const key = row.candidate_key || '';
  const review = row.answer_review || {};
  const hint = row.answer_hint || {};
  const question = row.question_review || {};
  const blocked = review.action === 'block';
  const done = review.status === 'reviewed';
  const questionAction = question.action || question.status || 'unreviewed';
  const occurrences = Number(row.question_number_occurrence || 1);
  // 「疑點」是伺服器 issue 清單裡屬於答案的那些（severity/issue_code），不是這裡判的。
  const issues = (row.answer_issues || [])
    .map((issue) => `${issue.severity || '?'}/${issue.issue_code || '?'}`).join('、');
  const stem = String(row.stem || '').slice(0, 240);
  return `<tr class="${blocked ? 'blocked' : done ? 'done' : ''}" data-key="${esc(key)}">
    <td class="qnum">${esc(row.question_number)}${occurrences > 1 ? `<span class="hint"> occ ${occurrences}</span>` : ''}</td>
    <td class="ans">${answerEditorHtml(row)}
      <textarea class="ans-note" data-note="${esc(key)}" placeholder="註記（可留空）">${esc(A.noteDraft.get(key) ?? review.notes ?? '')}</textarea>
      ${answerFiguresHtml(row)}</td>
    <td><span class="hint">審題 ${esc(questionAction)}</span>
      <div>${done ? `<span class="hint">答案 ${esc(LABEL[review.action] || review.action || '已審')}</span>` : '<span class="hint">答案 未審</span>'}</div>
      ${hint.severity === 'warning' ? `<span class="ans-warn">${esc(hint.message || '答案有疑慮')}</span>` : ''}</td>
    <td class="stem">${richText(stem)}
      <div class="opts">${(row.options || []).map((o) => `${esc(o.key)}. ${richText(String(o.text || '').slice(0, 60))}`).join('　')}</div>
      ${optionFiguresHtml(row)}</td>
    <td class="hint">${esc(issues)}</td>
    <td><button class="ans-block${blocked ? ' on' : ''}" data-block="${esc(key)}"
      title="這一題的答案有問題：按了變紅，再按一下取消（回到未審）">阻擋</button></td>
  </tr>`;
}

/* 一題的答案編輯器：現值、字母、模式、清空／全選／取消審核。

   字母鈕與模式鈕都是**開關**，所以它們畫的是「現在有效的狀態」：有草稿看草稿，沒有草稿看
   已經記錄的修正，都沒有才看 parser 的答案（`answerSelection`）。 */
function answerEditorHtml(row) {
  const key = row.candidate_key || '';
  const selection = answerSelection(row);
  const text = formatAnswerSelection(selection.letters, selection.mode);
  const chips = answerLetters(row).map((letter) =>
    `<button class="ans-btn${selection.letters.includes(letter) ? ' on' : ''}" data-answer="${esc(letter)}"
      data-key="${esc(key)}" title="點一下把 ${esc(letter)} 加進標準答案；再點一下取消">${esc(letter)}</button>`).join(' ');
  const modes = ANSWER_MODES.map(([mode, label]) =>
    `<button class="ans-mode${selection.mode === mode ? ' on' : ''}" data-mode="${esc(mode)}"
      data-key="${esc(key)}" title="${esc(ANSWER_MODE_HINT[mode] || '')}">${label}</button>`).join('');
  return `<span class="ans-current">${esc(text || '空白')}</span>
    <div>${chips}</div>
    <div class="ans-modes">${modes}</div>
    <div class="ans-modes">
      <button class="ans-cancel" data-clear="${esc(key)}" title="清空這一題的草稿字母，不會送出">清空</button>
      <button class="ans-cancel" data-select-all="${esc(key)}" title="四個字母都選">全選</button>
      <button class="ans-cancel" data-cancel-review="${esc(key)}" title="清掉草稿並取消這一題的答案審核（回到未審）">取消審核</button>
    </div>`;
}

function answerLetters(row) {
  const present = (row.options || [])
    .map((option) => String(option.key || '').trim().toUpperCase())
    .filter((key) => /^[A-D]$/.test(key));
  const wanted = new Set(present.length ? present : ['A', 'B', 'C', 'D']);
  for (const letter of answerSelection(row).letters) wanted.add(letter);
  return ['A', 'B', 'C', 'D'].filter((letter) => wanted.has(letter));
}

/* 這一題「現在的答案」是哪一個字串：草稿 → 已記錄的修正 → parser 的答案。
   `review.correction` 是**字串**（伺服器把 `corrected_answer` 原樣存成 JSON 字串；見
   `answer_review` 投影），不是物件——舊版讀 `review.correction?.answer`，所以那永遠是
   `undefined`，人工修正從來沒被顯示過。 */
function answerEffectiveText(row) {
  const review = row.answer_review || {};
  const correction = review.correction;
  if (correction !== undefined && correction !== null && String(correction) !== '') return String(correction);
  return String(row.answer ?? '');
}

function answerSelection(row) {
  const draft = A.answerDraft.get(row.candidate_key);
  if (draft) return draft;
  return parseAnswerSelection(answerEffectiveText(row));
}

function answerFiguresHtml(row) {
  const refs = (row.answer_image_refs || []).filter((ref) => ref && typeof ref === 'object' && ref.exists
    && (ref.path || ref.path_relative));
  if (!refs.length) return '';
  return '<div class="ans-figs" style="margin-top:5px">' + refs.map((ref, index) => {
    const path = ref.path || ref.path_relative;
    const label = ref.caption || ref.raw_ref || `答案補圖 ${index + 1}`;
    return `<a href="${esc(fileUrl(path))}" target="_blank" rel="noopener">`
      + `<img src="${esc(fileUrl(path))}" alt="${esc(label)}" title="${esc(label)}" style="max-width:100%;max-height:130px;border:1px solid var(--line);border-radius:6px"></a>`;
  }).join(' ') + '</div>';
}

function optionFiguresHtml(row) {
  const figures = (row.options || []).filter((option) => option.image && option.image.exists
    && (option.image.path || option.image.path_relative));
  if (!figures.length) return '';
  return '<div class="ans-figs" style="margin-top:4px">' + figures.map((option) => {
    const path = option.image.path || option.image.path_relative;
    return `<a href="${esc(fileUrl(path))}" target="_blank" rel="noopener">`
      + `<img src="${esc(fileUrl(path))}" alt="${esc(option.key)} 的圖" title="${esc(option.key)}"`
      + ' style="max-height:80px;border:1px solid var(--line);border-radius:4px"></a>';
  }).join(' ') + '</div>';
}

/* ------------------------------------------------------------------ 右欄：答案卷

   The reviewer compares the extracted answer against the paper, so the paper has to be *here* -
   the legacy console switched its right pane to the answer PDF in answer mode, and v2 shipped
   without it.

   The frame is **not** re-created while the document is the same (the same rule the question area
   and the discussion area follow): re-assigning `src` - even to the same file - reloads the viewer
   and throws the reviewer's scroll position away. So the head (counters, source toggles) is drawn
   into its own node and the iframe is only replaced when the file behind it changes. */
function renderAnswerPdf(sheet) {
  const pane = $('answerPdf');
  if (!pane) return;
  const pdf = sheet ? answerPdfPath(sheet, ANSWER_PANE.kind) : '';
  const head = answerPdfHeadHtml(sheet, pdf);
  if (pdf === ANSWER_PANE.path && $('answerFrame')) {
    // Same document, or none before and none now: leave the viewer exactly where it is.
    const headNode = $('answerPdfHead');
    if (headNode) headNode.innerHTML = head;
    bindAnswerPdf(pane);
    return;
  }
  ANSWER_PANE.path = pdf || '';
  pane.innerHTML = `<div class="pdf-head" id="answerPdfHead">${head}</div>`
    + (pdf
      ? `<iframe id="answerFrame" class="answer-frame" title="答案卷 PDF"
           src="${esc(fileUrl(pdf))}#view=FitH"></iframe>`
      : '<div class="empty-area">這一張答案卡沒有答案卷 PDF 路徑。<br>'
        + '沒有紙本就不要憑抽出文字判斷答案。</div>');
  bindAnswerPdf(pane);
}

/* 哪一個檔在這一刻該顯示。缺的那一種退回有的一個（舊 UI 的 `pdfPathFor` 同一個鏈），
   因為空的 iframe 不能對照任何東西；但缺的那個選項按鈕是 disable 的，不假裝它存在。

   **官方答案卷路徑來自 metadata**。`answer_pdf_primary_relative` identifies the authoritative
   sheet for this question; `answer_source_documents` preserves exact answer/correction roles,
   registry keys, paths and source checksums when both contribute. The UI reads the selected path
   and never reconstructs a sibling filename; the server owns that mapping. */
const ANSWER_METADATA_PATH_KEYS = ['answer_pdf_primary_relative', 'answer_pdf_relative',
                                   'corrected_answer_pdf_relative', 'answer_pdf_primary'];

function answerMetadataPath(sheet) {
  const metadata = (sheet && sheet.metadata) || {};
  for (const key of ANSWER_METADATA_PATH_KEYS) {
    const value = metadata[key];
    if (value) return String(value);
  }
  return '';
}

/* 這一張卡「有沒有這一種檔案」。metadata 的那一個路徑是**官方答案卷**，所以它只算在
   `official_pdf` 這一種上，不會讓版面／原始 PDF 的按鈕假裝有檔。 */
function answerPdfKindPath(sheet, kind) {
  const files = (sheet && sheet.source_files) || {};
  return files[kind] || (kind === 'official_pdf' ? answerMetadataPath(sheet) : '');
}

function answerPdfPath(sheet, kind) {
  const files = (sheet && sheet.source_files) || {};
  return answerPdfKindPath(sheet, kind) || files.official_pdf
    || files.mineru_layout_pdf || files.mineru_origin_pdf || '';
}

function answerPdfHeadHtml(sheet, pdf) {
  if (!sheet) return '<span class="hint">沒有這一張答案卡。</span>';
  const toggles = ANSWER_PDF_KINDS.map(([kind, label]) => {
    const path = answerPdfKindPath(sheet, kind);
    return `<button class="ans-mode${ANSWER_PANE.kind === kind ? ' on' : ''}" data-pdf-kind="${esc(kind)}"
      ${path ? '' : 'disabled'} title="${path ? esc(path) : '這張卡沒有這一種檔案'}">${esc(label)}</button>`;
  }).join(' ');
  const name = pdf ? String(pdf).split('/').pop() : '';
  return '<b>答案卷</b>'
    + `<span class="hint">來源檔案 ${esc(name || '（無）')}</span>`
    + `<span class="hint">已審 ${Number(sheet.reviewed_count || 0)}／${Number(sheet.reviewable_question_count ?? 0)}</span>`
    + (pdf ? `<a class="hint" id="answerPdfOpen" href="${esc(fileUrl(pdf))}" target="_blank" rel="noopener">另開</a>` : '')
    + `<span class="spacer"></span>${toggles}`
    + `<span class="hint" style="width:100%">${pdf ? `<code>${esc(pdf)}</code>`
      : '這一張答案卡沒有答案卷 PDF 路徑，右欄無法對照。'}</span>`;
}

function bindAnswerPdf(pane) {
  for (const button of pane.querySelectorAll('[data-pdf-kind]')) {
    button.onclick = () => {
      ANSWER_PANE.kind = button.dataset.pdfKind || 'official_pdf';
      renderAnswerPdf(A.sheets ? A.sheets[A.sheetIndex] : null);
    };
  }
}

/* ------------------------------------------------------------------ 綁定 */

function bindAnswerSheet() {
  const main = $('answerMain');
  // A typed note is kept in memory as it is typed, so switching to another sheet and back - or
  // switching to 首頁 - does not throw away a note the reviewer was in the middle of writing. It is
  // a draft, not a decision: nothing is sent until a button is pressed.
  for (const node of main.querySelectorAll('[data-note]')) {
    node.addEventListener('input', () => A.noteDraft.set(node.dataset.note, node.value));
  }
  // Clicking a letter **drafts** the answer; it does not save. A stray click on a 4-row table must
  // not silently rewrite an answer, so the change is only sent by one of the buttons in the sheet
  // bar. The 阻擋 switch below is the single exception, because a switch is not a draft.
  for (const button of main.querySelectorAll('[data-answer]')) {
    button.onclick = () => answerToggleLetter(button.dataset.key, button.dataset.answer);
  }
  for (const button of main.querySelectorAll('[data-mode]')) {
    button.onclick = () => answerSetMode(button.dataset.key, button.dataset.mode);
  }
  for (const button of main.querySelectorAll('[data-clear]')) {
    button.onclick = () => answerClearDraft(button.dataset.clear);
  }
  for (const button of main.querySelectorAll('[data-select-all]')) {
    button.onclick = () => answerSelectAll(button.dataset.selectAll);
  }
  for (const button of main.querySelectorAll('[data-cancel-review]')) {
    button.onclick = () => answerCancelReview(button.dataset.cancelReview);
  }
  for (const button of main.querySelectorAll('[data-block]')) {
    button.onclick = () => answerToggleBlock(button.dataset.block);
  }
  for (const button of main.querySelectorAll('[data-sheet-action]')) {
    button.onclick = () => answerSheetAction(button.dataset.sheetAction);
  }
}

function answerSheetRow(key) {
  const sheet = A.sheets ? A.sheets[A.sheetIndex] : null;
  if (!sheet || !key) return null;
  return (sheet.rows || []).find((row) => row.candidate_key === key) || null;
}

/* 把一列的答案編輯器重畫成它現在有效的狀態。**只動這一列的文字與 class**，不換 DOM：
   換掉 cell 的 innerHTML 會把註記框（焦點、正在打的字）一起換掉，也會把捲動位置拉走。 */
function paintAnswerRow(key) {
  const row = answerSheetRow(key);
  const main = $('answerMain');
  if (!row || !main) return;
  const tr = main.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
  if (!tr) return;
  const selection = answerSelection(row);
  const text = formatAnswerSelection(selection.letters, selection.mode);
  const now = tr.querySelector('.ans-current');
  if (now) now.textContent = text || '空白';
  for (const chip of tr.querySelectorAll('[data-answer]')) {
    chip.classList.toggle('on', selection.letters.includes(chip.dataset.answer));
  }
  for (const mode of tr.querySelectorAll('[data-mode]')) {
    mode.classList.toggle('on', mode.dataset.mode === selection.mode);
  }
}

function answerToggleLetter(key, letter) {
  const row = answerSheetRow(key);
  if (!row || !letter) return;
  const selection = answerSelection(row);
  const letters = new Set(selection.letters);
  if (selection.mode === 'single') {
    // 單選：按另一個字母是**取代**，按同一個字母是**取消**。使用者要的是「按了表示標準答案，
    // 按一下又可以取消，變成不是單選而是多選的彈性」——所以「取消」在單選裡也要成立，否則
    // 選了就不能收回，只能靠清空。
    if (letters.has(letter)) letters.delete(letter);
    else { letters.clear(); letters.add(letter); }
  } else if (letters.has(letter)) {
    letters.delete(letter);
  } else {
    letters.add(letter);
  }
  A.answerDraft.set(key, { letters: [...letters], mode: selection.mode });
  paintAnswerRow(key);
}

function answerSetMode(key, mode) {
  const row = answerSheetRow(key);
  if (!row || !mode) return;
  const selection = answerSelection(row);
  let letters = [...selection.letters];
  // 切成單選時只留最後一個：否則畫面上會亮著兩個字母、現值卻只有一個（`formatAnswerSelection`
  // 在單選模式回傳最後一個）——文字與開關狀態必須是同一個事實。
  if (mode === 'single' && letters.length > 1) letters = letters.slice(-1);
  A.answerDraft.set(key, { letters, mode });
  paintAnswerRow(key);
}

function answerClearDraft(key) {
  const row = answerSheetRow(key);
  if (!row) return;
  A.answerDraft.set(key, { letters: [], mode: answerSelection(row).mode });
  paintAnswerRow(key);
}

function answerSelectAll(key) {
  const row = answerSheetRow(key);
  if (!row) return;
  const mode = answerSelection(row).mode === 'single' ? 'any_combo' : answerSelection(row).mode;
  A.answerDraft.set(key, { letters: answerLetters(row), mode });
  paintAnswerRow(key);
}

function answerNoteValue(key, row) {
  const main = $('answerMain');
  const box = main ? main.querySelector(`textarea[data-note="${CSS.escape(key)}"]`) : null;
  if (box) { A.noteDraft.set(key, box.value); return box.value; }
  return A.noteDraft.get(key) ?? (row.answer_review || {}).notes ?? '';
}

function answerSourceKey(row) {
  return (row.answer_payload || {}).answer_source_registry_key || row.answer_source_registry_key || '';
}

function setAnswerSaved(text) {
  for (const node of document.querySelectorAll('#answerMain [data-saved]')) node.textContent = text;
}

/* 一題一筆寫入。`/api/answer-review` 收**一個**事件，所以這是「這一題」的動作，不會順手
   寫到別題——舊版只有整份的按鈕，按一次 79 題一起被記成同一個決定。 */
async function postAnswerReview(payload) {
  const response = await fetch('/api/answer-review', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({ ok: false, error: '回應不是 JSON' }));
  return data;
}

/* 每題的「阻擋」是一個**開關**：按了變紅（`action=block`），再按一下取消（`action=unreviewed`，
   那是伺服器自己的取消路徑，這一題回到未審）。開關不是草稿，所以它**立刻寫入**，一次一題，
   不經過整份那一排按鈕。 */
async function answerToggleBlock(key) {
  const row = answerSheetRow(key);
  if (!row) return;
  const sheet = A.sheets[A.sheetIndex] || {};
  const blocked = ((row.answer_review || {}).action || '') === 'block';
  const action = blocked ? 'unreviewed' : 'block';
  const data = await postAnswerReview({
    action,
    candidate_key: key,
    reviewer: 'local',
    notes: answerNoteValue(key, row),
    // `reviewed_answer` 是「紙本上是什麼」，所以它來自 row，不是來自草稿；省略它會存成
    // `{"answer": null}`，把這一題被拿來對照的答案悄悄清掉。
    reviewed_answer: { answer: row.answer ?? null },
    answer_source_registry_key: answerSourceKey(row),
    sheet_key: sheet.sheet_key || '',
  });
  if (!data.ok) {
    setAnswerSaved(`阻擋寫入失敗：${data.error || ''}`);
    toast(`阻擋寫入失敗：${data.error || ''}`, true);
    return;
  }
  toast(blocked ? `第 ${row.question_number} 題已取消阻擋（回到未審）` : `第 ${row.question_number} 題已阻擋`);
  await afterWrite();
}

/* 每題的「取消審核」：清掉草稿，並把這一題的審核退回未審（`unreviewed`）。
   沒有已記錄的審核時，只清草稿並說出來——送一筆沒有內容的取消是假的事件。 */
async function answerCancelReview(key) {
  const row = answerSheetRow(key);
  if (!row) return;
  const sheet = A.sheets[A.sheetIndex] || {};
  const review = row.answer_review || {};
  const hadDraft = A.answerDraft.delete(key);
  paintAnswerRow(key);
  if (review.status !== 'reviewed') {
    setAnswerSaved(`第 ${row.question_number} 題沒有已記錄的審核，只清掉草稿（沒有送出）。`);
    return;
  }
  const data = await postAnswerReview({
    action: 'unreviewed',
    candidate_key: key,
    reviewer: 'local',
    notes: '',
    reviewed_answer: { answer: row.answer ?? null },
    answer_source_registry_key: answerSourceKey(row),
    sheet_key: sheet.sheet_key || '',
  });
  if (!data.ok) {
    setAnswerSaved(`取消審核失敗：${data.error || ''}`);
    toast(`取消審核失敗：${data.error || ''}`, true);
    return;
  }
  toast(`第 ${row.question_number} 題已回到未審${hadDraft ? '（草稿也清掉了）' : ''}`);
  await afterWrite();
}

/* 整份的四個動作。**一個動作一個請求群**：`/api/answer-review-batch` 把一個 `action` 蓋在
   每一個 entry 上，所以每一列按它自己賺到的動作分組送出（見下）。 */
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
  const unresolved = [];
  for (const row of sheet.rows || []) {
    if (row.is_placeholder || !row.candidate_key) continue;
    const hasDraft = A.answerDraft.has(row.candidate_key);
    const draft = hasDraft ? A.answerDraft.get(row.candidate_key) : null;
    const drafted = draft ? formatAnswerSelection(draft.letters, draft.mode) : '';
    const review = row.answer_review || {};
    const hint = row.answer_hint || {};
    const note = notes[row.candidate_key] ?? A.noteDraft.get(row.candidate_key) ?? review.notes ?? '';
    const already = review.status === 'reviewed';
    let rowAction = action;
    if (hasDraft && drafted !== String(row.answer ?? '')) rowAction = 'correct';
    else if (!hasDraft && action === 'correct') continue;
    else if (action === 'accept' && already && rowAction === review.action && !note) continue;
    if (hint.needs_manual_choice) {
      // 「MOD 多答案」的題目必須由人看紙本點選；仍是 `#` 或空白時伺服器也會擋（
      // `needs_manual_answer_review`），所以在送出**之前**先說出是哪幾題，而不是等一個 409。
      const value = String(hasDraft ? drafted : answerEffectiveText(row)).trim();
      if (value === '' || value === '#') unresolved.push(row.question_number);
    }
    const entry = {
      candidate_key: row.candidate_key, action: rowAction, notes: note, reviewer: 'local',
      // `reviewed_answer` is what the server stores as "the answer this reviewer accepted"; omitting it
      // would store `{"answer": null}` and silently blank the answer the sheet is being judged
      // against. It is sent from the row, not from the draft, because it means "what was on the paper".
      reviewed_answer: { answer: row.answer ?? null },
      answer_source_registry_key: answerSourceKey(row),
      sheet_key: sheet.sheet_key || '',
      sheet_action: action,
      // 舊 UI 只在畫面警告，旗標沒送；送出去之後伺服器的 `answer-review-batch` 那道
      // 「MOD 仍有 # 或空白不可通過」的守門才會真的生效。
      needs_manual_answer_review: Boolean(hint.needs_manual_choice),
    };
    if (hasDraft) entry.corrected_answer = drafted;
    entries.push(entry);
  }
  if ((action === 'accept' || action === 'unblock') && unresolved.length) {
    const numbers = unresolved.slice(0, 12).map((number) => `第 ${number} 題`).join('、');
    const message = `MOD 多答案仍有 # 或空白：${numbers}${unresolved.length > 12 ? '…' : ''}。請先看答案卷 PDF 點選答案。`;
    setAnswerSaved(message);
    toast(message, true);
    return;
  }
  if (!entries.length) {
    setAnswerSaved('沒有要寫入的項目（沒有改答案、也沒有註記）。');
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
  if (failure) {
    setAnswerSaved(`寫入失敗：${failure.error || ''}`);
    toast(`答案審核寫入失敗：${failure.error || ''}`, true);
    return;
  }
  setAnswerSaved(`已寫入 ${savedTotal} 筆。`);
  toast(`已寫入 ${savedTotal} 筆答案審核`);
  A.answerDraft.clear();
  A.noteDraft.clear();
  await afterWrite();
}
