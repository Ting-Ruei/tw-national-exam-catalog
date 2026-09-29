/* 題目審核區：清單視窗（`LIST_WINDOW`）與 `W`/`S` 走法同一個 `S.view`（charter：導覽跟隨被畫出的清單）；左頁＝题目（6 個 option 以 1..6 對映），右頁＝紙本 PDF（與左頁脫鉤）。`A/R/B/E`＝審題；`1..6` 聚焦选项、`7` 註解、`8` 紙本；`Enter` 儲存、`Shift+Enter` 換行、`Esc` 關框。前 6 項的標籤用紙本的「題號（數字）」；答案卷若用 `1．A` 式標號 likewise 數字，字號不同即不同题。同紙題號重複時 `S.byKey` 以最後筆為準（實測：`S.rows` 79,090、`S.byKey` 79,088，重複 2 題：`305:22` 與 `305:0403`）。重複號的浮動標記專用於 **題目區**（`PAPER.*` 前綴）；答案卷僅 4 題 likewise 用 1..4，`5..8` 在答案卷無對應而直往跳之（浮動）。浮動對照前 6 項（數字）`document`;`PAPER[1-6]`（`PAPER[7]`/`PAPER[8]` 之浮動對照 *note* 記号（`PAPER.*` 前綴））。 */
const LIST_WINDOW = 400;

/* Which of the four states a question is in, and which one the reviewer is looking at.

   These are **states of the question**, not of the reviewer's session: 未看 means "no decision
   recorded", a flag means "a decision was recorded that says this needs another look". They are
   disjoint and together with 確認正常 they cover every row, so the chips partition the scope and
   the counts add up - which is what makes the number beside a chip trustworthy.

   需重看 and 阻擋 are **one** chip, because they are the same act - the reviewer saying "do not ship
   this as it stands" - and they are reviewed the same way, by reading the question again. Splitting
   them would make the reviewer choose a category before deciding what is wrong.

   **AI／管線退回 is a different chip, because it is a different author.** A `reset_review` event is
   not the reviewer's opinion: the pipeline changed the question's text (a repair) or the model
   raised a finding, and the question came *back*. The reviewer never flagged it; something else
   decided the reading it was approved under is no longer the reading on screen. Merging it with the
   reviewer's own blocks hid that distinction on the one screen where it matters most - the reviewer
   cannot tell whether a question is waiting because they said so or because a machine said so, and
   the second case needs a different question answered ("is the new text right?") than the first
   ("what is wrong?"). Measured on the served queue: 106 blocks, of which 9 are resets.

   The two chips stay in the same 不要出貨 family, so nothing about the walk changes - only the
   count and the filter. */
function stateOf(item) {
  const v = verdictOf(item.candidate_key);
  if (v === 'reset_review') return 'returned';
  if (v === 'needs_review' || v === 'block') return 'flagged';
  if (v) return 'done';
  return 'unseen';
}

/* 題組 is a property of the question, not of the reviewer: two or more questions printed under one
   stem. It is a *scope*, so it intersects the state chips rather than competing with them - a
   reviewer may want "the 題組 I have not looked at yet". */
const isGrouped = (item) => Number(item.group_size || 1) > 1;

/* Whether the pipeline left something unsettled about this question.
   Read from the row the server sent (`disputes`), not re-derived in the browser: the disputes are
   a measurement of the paper and a second implementation would be a second opinion about what is
   uncertain, which is the one thing that must agree with the queue. `null` is "none were computed"
   and `[]` never occurs - the builder writes `null` rather than an empty list so that "not looked
   at" stays distinguishable from "looked at, nothing found". */
const hasDispute = (item) => Array.isArray(item.candidate.disputes) && item.candidate.disputes.length > 0;

function viewMode() {
  const chosen = document.querySelector('input[name="view"]:checked');
  return (chosen && chosen.value) || 'all';
}

/* The rows the reviewer is looking at: what is drawn AND what is walked.

   These used to be two different sets, and that was a defect. The chips narrowed what was *drawn*
   while W/S stepped through the whole paper scope, justified by "a filter must not rearrange the list
   under the cursor". The justification was wrong in the way that matters: it kept the list from
   rearranging by making navigation ignore the list. Measured on the real queue, with 題組 selected
   and a group running 78-80, pressing S on 80 went to question 1 of the *next paper*, because the
   paper order said 80 is followed by the next paper while the list the reviewer was reading said 80
   is followed by the next group. The same happened under 有圖, so a reviewer stepping through the
   pictures was silently teleported out of the filter and could not tell which questions they had
   actually seen.

   The rule is the charter's: **導覽與內容必須來自同一個來源**. One array, `S.rows`, is both what the
   list renders and what `go()` indexes, so "the next question" is always "the next row on screen".

   `S.view` keeps the whole scope, because the counts above the chips and the taxonomy crumbs are
   statements about the scope rather than about the filter, and a chip whose number changed when
   another chip was selected would be unreadable.

   `anchor` is the candidate_key to keep under the cursor after the filter changes. `go()` passes the
   open question, so toggling a chip leaves the reviewer on the question they were reading whenever
   that question is still in the narrowed set, and otherwise the list opens at the nearest surviving
   row rather than at the top. */
function rebuildRows(anchor) {
  S.rows = visibleRows();
  S.viewPos = new Map(S.view.map((item, i) => [item.candidate_key, i]));
  const key = anchor !== undefined ? anchor
    : (S.rows[S.index] || {}).candidate_key;
  let at = key ? S.rows.findIndex((item) => item.candidate_key === key) : -1;
  if (at < 0 && key) {
    // The anchored question is not in the narrowed set. Open at the first row that follows it in
    // the paper, which keeps the reviewer's place in the scope instead of jumping them to the start.
    const was = S.viewPos.has(key) ? S.viewPos.get(key) : -1;
    at = was >= 0
      ? S.rows.findIndex((item) => (S.viewPos.get(item.candidate_key) ?? -1) > was)
      : 0;
    if (at < 0) at = Math.max(0, S.rows.length - 1);
  }
  S.index = S.rows.length ? Math.min(Math.max(at, 0), S.rows.length - 1) : 0;
  return S.rows.length > 0;
}

function visibleRows() {
  const mode = viewMode();
  const figures = $('figuresOnly').checked;
  return S.view.filter((item) => {
    if (figures && !item.has_figure) return false;
    // 題組 is checked *before* the state chips and is exclusive: selecting it asks for the grouped
    // questions whatever their state, because "show me the 題組" is a question about structure.
    if (mode === 'group') return isGrouped(item);
    if (mode === 'unseen') return stateOf(item) === 'unseen';
    if (mode === 'flagged') return stateOf(item) === 'flagged';
    if (mode === 'returned') return stateOf(item) === 'returned';
    // 爭議 is what the *pipeline* could not settle, not what the reviewer decided. It is a separate
    // axis from the state chips and deliberately so: a question can be a settled `確認正常` and still
    // carry a dispute the pipeline raised, and collapsing them would hide the machine's uncertainty
    // behind the human's decision.
    if (mode === 'disputed') return hasDispute(item);
    return true;
  });
}

/* The counts beside the chips, computed over the same set the list draws from, so a chip can never
   promise rows the list will not show. The figure toggle is part of that set, which is why the
   numbers follow it. */
function renderChips() {
  const figures = $('figuresOnly').checked;
  const base = figures ? S.view.filter((item) => item.has_figure) : S.view;
  const n = (predicate) => base.filter(predicate).length;
  $('nAll').textContent = n(() => true);
  $('nGroup').textContent = n(isGrouped);
  $('nUnseen').textContent = n((item) => stateOf(item) === 'unseen');
  $('nFlagged').textContent = n((item) => stateOf(item) === 'flagged');
  $('nReturned').textContent = n((item) => stateOf(item) === 'returned');
  $('nDisputed').textContent = n(hasDispute);
  document.querySelectorAll('#chips .chip').forEach((chip) => {
    chip.classList.toggle('on', chip.dataset.view === viewMode());
  });
}

function renderList() {
  const body = $('listBody');
  renderChips();
  // Draw `S.rows`, which is what `go()` walks. There is no index translation here any more: a
  // filtered row's position in the drawn array IS its position in the walked array, because they are
  // the same array. The previous version kept `S.view` as the walked set and mapped each drawn row
  // back to a position in it, which is precisely the seam the 78-80 defect lived in.
  const rows_ = S.rows;
  if (!rows_.length) { body.innerHTML = '<div class="empty">這個範圍沒有題目</div>'; return; }
  // Centre the window on the open question, so moving with W/S never walks off the drawn part.
  const half = Math.floor(LIST_WINDOW / 2);
  const from = Math.max(0, Math.min(S.index - half, Math.max(0, rows_.length - LIST_WINDOW)));
  const to = Math.min(rows_.length, from + LIST_WINDOW);
  const window_ = rows_.slice(from, to);
  body.innerHTML = (from > 0 ? `<div class="empty">… 上面還有 ${from} 題</div>` : '')
    + window_.map((item, offset) => {
    const position = from + offset;
    const v = verdictOf(item.candidate_key);
    const cls = ['row'];
    if (position === S.index) cls.push('active');
    if (v) cls.push('done');
    if (v === 'needs_review' || v === 'block') cls.push('flag');
    if (v === 'reset_review') cls.push('returned');
    return `<button class="${cls.join(' ')}" data-pos="${position}">
      <span class="mark"></span><span class="num">${esc(item.question_number)}</span>
      <span class="fig">${item.has_figure ? '▣' : ''}</span>
      <span class="grp">${isGrouped(item) ? '組' : ''}</span>
      <span>${esc(String(item.stem_preview || '').replace(/<[^>]*>/g, '').slice(0, 12))}</span></button>`;
  }).join('')
    + (to < rows_.length ? `<div class="empty">… 下面還有 ${rows_.length - to} 題</div>` : '');
  body.querySelectorAll('.row').forEach((node) => { node.onclick = () => go(Number(node.dataset.pos)); });
  const active = body.querySelector('.row.active');
  if (active) active.scrollIntoView({ block: 'nearest' });
  // The progress bar counts the scope, not the queue: "how much of the paper I am reading is
  // done" is the question a reviewer asks, and 21,150 would make every session look like zero.
  const done = S.view.filter((i) => verdictOf(i.candidate_key)).length;
  $('doneCount').textContent = done;
  $('totalCount').textContent = `／${S.view.length} 已過目`;
  $('doneBar').style.width = `${S.view.length ? (done / S.view.length) * 100 : 0}%`;
}

/* --------------------------------------------------- left: the extracted text */
/* The marks the paper defines, and the marks that are defects.

   A question may print its answer choices as *sets of its own sub-items*: the stem says
   `\ue000砂粒病毒（Arenavirus） \ue001漢他病毒（Hantavirus） …` and the options are `\ue18c\ue000\ue001`.
   The queue builder resolves those marks into the words the paper gives them, and this note
   shows the legend beside the question so a reviewer can check the substitution rather than
   trust it. Without the legend the reviewer sees words in the options with nothing on screen
   saying where they came from.

   A mark standing *inside a word* is a different thing: `轉氨\ue2c6` is 轉氨酶 and the text layer
   cannot spell it. That is a real defect at a known position, so it is reported as a defect -
   with its position and the word it stands in - and never guessed at. Showing nothing would let
   a reviewer approve a question that has an unreadable character in it. */
function lostGlyphHtml(candidate) {
  const note = candidate.lost_glyph_note;
  const legend = candidate.subitem_legend;
  if (!note && !legend) return '';
  const rows = [];
  if (legend) {
    const pairs = Object.keys(legend).map((mark) =>
      `<code>${esc(mark)}</code> = ${esc(legend[mark])}`).join('　');
    rows.push(`<div class="gloss"><b>紙本自訂符號</b>${pairs}</div>`);
  }
  if (note) {
    rows.push(`<div class="gloss missing"><b>字形遺失（需對照紙本修正）</b>${esc(note)}</div>`);
  }
  return rows.join('');
}

/* The disputes: what the pipeline could not settle.

   Drawn **above** the stem, because it changes how everything below it should be read - a reviewer
   who reads the question first and the warning afterwards has already formed a judgement. Each
   dispute carries the reason and the address that makes it checkable, so the reviewer can go to
   the page and decide rather than trust the label.

   Nothing here is a decision. A dispute is the claim "this needs a person"; the person is the
   reviewer, and only their `確認正常` / `需重看` / `阻擋` writes a review event. */
function disputeHtml(candidate) {
  const found = candidate.disputes;
  if (!Array.isArray(found) || !found.length) return '';
  const order = { blocker: 0, review: 1, info: 2 };
  const sorted = found.slice().sort((a, b) =>
    (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
  const rows = sorted.map((d) => {
    const badge = d.severity === 'blocker' ? 'blocker' : 'review';
    const label = d.severity === 'blocker' ? '阻擋級' : '需確認';
    return `<div class="dispute ${badge}"><b>${esc(label)}｜${esc(d.note || d.kind)}</b>`
      + `<span class="why">${esc(d.detail || '')}</span>`
      + `<code class="where">${esc(d.kind)}</code></div>`;
  }).join('');
  return `<div class="disputes"><div class="disputes-head">系統無法判定（${sorted.length}）`
    + `<span class="hint">以下由紙本量測，不是模型的意見</span></div>${rows}</div>`;
}

/* What the qbr pipeline's model said about this question.

   This is **not** the SQL-era `ai_review` audit, which has a status and a recommended action. It is
   the loop's finding (`qbr/src/qbr/ai_findings.py`): `verdict` / `what` / `where` / `fix` /
   `rule_worthy` / `confidence`, with the model named and the prompt generation recorded. It is
   advisory only, so it is drawn as a note: nothing here is clickable and nothing changes the buttons.

   The distinction the reviewer needs to feel at a glance is **measurement vs opinion**. Above this
   box, disputes are the system's own paper measurements; here it is one model's reading of the same
   text, and it can be wrong. Naming the model and showing that a person's own block is what brought
   the question here is what lets a reader weigh it rather than obey it. */
function findingHtml(candidate) {
  const record = candidate.qbr_ai_finding;
  if (!record) return '';
  const f = record.finding || {};
  const verdict = f.verdict;
  const code = f.what;
  const where = f.where;
  const fix = f.fix;
  // A `NONE`/`OK` note is still shown, because "the model looked and found nothing" is a fact a
  // person should be able to see - it is what makes a silent class of questions visibly checked.
  const label = verdict === 'DEFECT' ? '模型認為有問題'
    : verdict === 'NOT_EXTRACTION' ? '模型認為不是抽取造成的'
      : verdict === 'OK' ? '模型沒發現異常' : '模型回答了，但無法歸類';
  const who = record.population === 'corpus'
    ? '整庫掃描（這一題沒有人類標記）'
    : record.population === 'dispute' ? '爭議題對紙本確認' : '人類阻擋後詢問';
  const parts = [];
  parts.push(`<div class="af-line"><b>哪裡：</b>${esc(where || '（沒說）')}</div>`);
  parts.push(`<div class="af-line"><b>怎麼修：</b>${esc(fix || '（沒說）')}</div>`);
  if (code) parts.push(`<div class="af-line"><code class="af-code">${esc(code)}</code>`
    + (f.rule_worthy === true ? ' 被判斷為一類（值得寫規則）'
      : f.rule_worthy === false ? ' 被判斷為個案' : '')
    + (typeof f.confidence === 'number' ? ` 信心 ${f.confidence}` : '') + '</div>');
  if (record.error) parts.push(`<div class="af-line"><b>讀取失敗：</b>${esc(record.error)}</div>`);
  // The screenshot the model was shown, so the reader can look at the same picture the note is about.
  // "Unseen evidence is not evidence": a note claiming the paper prints `長` is only worth reading
  // if the page it claims that about can be seen. `fileUrl` serves it by the queue-relative path.
  const crop = record.crop
    ? `<figure class="af-crop"><img src="${esc(fileUrl(record.crop))}"
         alt="模型看到的紙本截圖" loading="lazy"
         onclick="window.open(this.src,'_blank')">
       <figcaption>模型看到的紙本截圖</figcaption></figure>` : '';
  // The change row shows the **raw** stored/page pair, not the folded forms `compare` measured with.
  // The folded pair is how the subtraction decided *that* the two differ; it is not text the paper
  // prints. Showing the folded forms and then applying the raw one would make the panel and the
  // editor disagree about the same fix - the reviewer would approve one string and get another.
  const raw = (c) => (c.stored !== undefined && c.stored !== null ? c.stored : c.from);
  const rawTo = (c) => (c.page !== undefined && c.page !== null ? c.page : c.to);
  const changes = Array.isArray(record.changes) && record.changes.length
    ? `<div class="af-line"><b>機械比對（模型轉錄後，由程式逐字相減）：</b></div>`
      + record.changes.map((c) => `<div class="af-change"><code>${esc(c.field)}</code>`
        + `<span class="af-from">${esc(String(raw(c) || '').slice(0, 200))}</span>`
        + `<span class="af-arrow">→</span>`
        + `<span class="af-to">${esc(String(rawTo(c) || '').slice(0, 200))}</span>`
        + `<button class="af-apply" data-field="${esc(c.field)}">帶入修正</button></div>`).join('')
    : '';
  return `<div class="ai-finding"><div class="af-head"><span>模型意見（${esc(label)}）</span>`
    + `<span class="af-hint">${esc(record.model || '未知模型')} · ${esc(who)}`
    + `${record.prompt_version ? ` · 提示詞版本 ${esc(record.prompt_version)}` : ''}</span></div>`
    + `${crop}${parts.join('')}${changes}</div>`;
}

/* Carry one of the model's mechanical changes into the manual editor.

   (c) of the request was that a disputed question be **repairable, not merely reported** - and the
   repair has to stay a *human* action. So this does not write anything: it opens the existing edit
   form with the model's reading filled in, and the reviewer reads it, changes what they disagree
   with, and presses 儲存修正 themselves. `GOV-05` is unchanged: the model's output is advisory, and
   the event that records the fix is written by the person who checked it.

   The changes are applied one at a time, on the button's own field, so a reviewer who trusts the
   `⻑`→`長` fix does not also silently accept a stem rewrite they never read. */
/* The「帶入修正」buttons are delegated from the panel.

   `findingHtml` writes them into `innerHTML`, so there is no node to bind at parse time: a direct
   `onclick` in the template would have to name a global, and the panel is re-rendered on every move.
   One listener on the side panel handles every button and every future re-render. A change carries
   the **raw page reading** (`page`) rather than the folded comparison string, so what lands in the
   editor is the paper's own text - which is what a correction should store. */
function bindFindingButtons() {
  const side = $('textSide');
  if (!side || side.dataset.findingBound) return;
  side.dataset.findingBound = '1';
  side.addEventListener('click', (event) => {
    const button = event.target && event.target.closest && event.target.closest('.af-apply');
    if (button) applyFindingChange(button);
  });
}

function applyFindingChange(button) {
  const record = S.rows[S.index] && S.rows[S.index].candidate.qbr_ai_finding;
  if (!record || !Array.isArray(record.changes)) return;
  const field = button.dataset.field;
  const change = record.changes.find((c) => c.field === field);
  if (!change) return;
  if (!S.editing) setEditMode(true);
  if (field === 'stem') {
    const node = $('editStem');
    if (node) node.value = change.page || node.value;
  } else {
    const key = field.replace('option ', '');
    const node = document.getElementById(`editOpt_${key}`);
    if (node) node.value = change.page || node.value;
  }
  markDirty();
  toast(`已把「${field}」的紙本讀法帶入編輯框；請自己確認後再儲存修正`);
}

function renderTextSide() {
  const item = S.rows[S.index];
  // 一個篩選可以合法地剩下 0 列（例如「AI／管線退回」在這一卷沒有題目）。以前這條路沒有守
  // 衛，`S.rows[S.index]` 是 undefined，`item.candidate` 直接拋錯——而丟錯的地方在 refilter 裡，
  // 所以畫面會停在上一輪的內容、按什麼都沒反應。清單本身（renderList）已經有同樣的空集合
  // 守衛；這裡補上，讓兩個面板對「沒有題目」用同一種行為。
  if (!item) {
    $('where').innerHTML = '';
    $('stateHint').textContent = '';
    $('textSide').innerHTML = '<div class="empty">這個篩選在目前範圍內沒有題目。</div>';
    return;
  }
  // The question itself comes from `/api/candidates`, which is the endpoint that returns
  // `stem`, `options` and `answer`. `/api/workflow` returns only the queue's metadata - a
  // `stem_preview` of about twelve characters and no options at all - and its `selected`
  // field is null unless a single candidate was asked for. Reading the text from there left
  // every question looking like an empty stem, which is what the reviewer saw: the text pane
  // was never carrying the paper's text in the first place.
  const candidate = item.candidate || {};
  const metadata = candidate.metadata || {};
  // Which options are correct, read from `accepted_values` rather than split out of the answer
  // string.
  //
  // A corrections sheet re-issues a table, and it writes `答Ｂ或Ｃ或BC者均給分` - so an accepted
  // answer is joined with 「或」 and may name a combination. Splitting the printed answer on commas
  // therefore finds nothing, and for those questions NO option was highlighted at all: the reviewer
  // saw four identical rows and no key, which is indistinguishable from a missing answer. Measured
  // on the served queue: 393 questions carry a non-single answer, 266 of them name more than one
  // thing, and every one of the 32,350 rows carries `accepted_values` - so the field to read was
  // there the whole time and the display was parsing a sentence instead of reading a list.
  const accepted = new Set(
    ((candidate.answer_payload || {}).accepted_values || [])
      .map((v) => String(v).trim().toUpperCase()).filter(Boolean));
  // Fall back to the answer string only when the payload is absent, and accept BOTH separators:
  // a comma from a plain multi-answer sheet, 「或」 from a corrections sheet.
  if (!accepted.size) {
    String(candidate.answer || '').split(/[,，或]/)
      .map((v) => v.trim().toUpperCase()).filter(Boolean).forEach((v) => accepted.add(v));
  }
  const answer = accepted;
  const options = candidate.options || [];
  const v = verdictOf(item.candidate_key);
  const flags = (item.reason_codes || []).filter(Boolean);

  $('where').innerHTML = `<b>第 ${esc(item.question_number)} 題</b> · ${esc(item.category)} · ${esc(item.year)}年第${esc(item.ordinal)}次 · ${esc(item.subject)}`;
  const standing = verdictOf(item.candidate_key);
  const note = noteOf(item.candidate_key);
  // 被退回來的題目要說出**是誰退回、為什麼**，不然「AI／管線退回」只是一個標籤。
  // 原因就在清單自己帶的 `review.reset` 裡（退回事件的 notes／reset_notes 與 previous_action），
  // 不是另一支 API 才有的欄位。人按的 block／needs_review 沒有這塊：原因就是審題者自己。
  const resetEvent = (candidate.review || {}).reset || {};
  const returnedNote = standing === 'reset_review'
    ? [resetEvent.reset_notes || resetEvent.notes,
       resetEvent.previous_action ? `原為：${LABEL[resetEvent.previous_action] || resetEvent.previous_action}` : '']
        .filter(Boolean).join('｜')
    : '';
  $('stateHint').textContent = standing
    ? `已標記：${LABEL[standing] || standing}${returnedNote ? `（${returnedNote}）` : ''}`
    : (note ? '只有註記・尚未決定' : '');

  const edited = metadata.review_status === 'human_corrected' || (candidate.review?.has_correction);
  const editor = S.editing ? `<div class="edit on" id="editor">${editorHtml(candidate, options)}</div>` : '';

  $('textSide').innerHTML = `
    <div class="side-head">抽出文字${edited ? '（已人工修正）' : ''}<span class="hint">${flags.length ? flags.map(esc).join(' · ') : '與右側紙本對照'}</span></div>
    <div class="qnum">第 ${esc(item.question_number)} 題</div>
    ${disputeHtml(candidate)}
    ${findingHtml(candidate)}
    ${groupHtml(candidate)}
    <div class="stem" id="viewStem">${richText(candidate.stem || '（題幹空白）')}</div>
    ${lostGlyphHtml(candidate)}
    <div class="opts" id="viewOpts">${options.map((option) => `
      <div class="opt${answer.has(option.key) ? ' is-answer' : ''}">
        <span class="k">${esc(option.key)}</span>${optionCropHtml(candidate, option)}<span class="t">${richText(option.text)}</span>
      </div>`).join('')}</div>
    ${figureHtml(candidate)}
    ${note ? `<div class="noteShown">註記：${esc(note)}</div>` : ''}
    ${editor}
    ${S.editing ? '' : evidenceHtml(candidate, metadata)}`;

  if (S.editing) {
    $('editStem').value = candidate.stem || '';
    options.forEach((option) => {
      const node = document.getElementById(`editOpt_${option.key}`);
      if (node) node.value = option.text || '';
    });
    ['editStem', ...options.map((o) => `editOpt_${o.key}`)].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.oninput = markDirty;
    });
  }
  // The「帶入修正」buttons are inside the `innerHTML` just written, so they are bound by delegation
  // once, here, rather than by naming a global in the template.
  bindFindingButtons();
}

/* The figure crops, shown beside the text they were cut for.
   An image question extracts as four empty options, and until now that is all the reviewer saw:
   a stem and four blank rows, with no way to tell "the extraction lost the options" from "the
   options are pictures". The crop is the measurement that tells them apart, and it was being
   written to disk and never displayed - so the reviewer could not check the one stage that
   claims to have read the picture. Showing it is not a convenience; without it the claim is
   unverifiable. */
/* One option's picture, placed in the option's own row.
   The reference bank files these as `option_image` with an `option_key`, and the answer slot shows
   the picture, because for these questions the option *is* a picture - four chemical structures
   with empty bodies, where the answer cannot be checked without seeing which structure the key
   names. A crop of the whole question cannot do that: it puts all four structures in one image and
   the reviewer has to work out which quarter the answer refers to. Each option's crop is bound to
   its key by measurement, so this row shows the structure the key names and nothing else. */
function optionCropHtml(candidate, option) {
  const refs = (candidate.image_refs || []).filter((ref) => ref && typeof ref === 'object'
    && ref.exists !== false && ref.asset_role === 'option-image' && ref.option_key === option.key);
  if (!refs.length) return '';
  return refs.map((ref) => `<img class="opt-img" src="${esc(fileUrl(ref.path))}"
    onclick="window.open(this.src,'_blank')"
    alt="${esc(ref.description || ('選項 ' + option.key + ' 的圖'))}"
    title="${esc(ref.source === 'image-object' ? '取自圖物件本身' : '取自頁面區域')}" loading="lazy">`).join('');
}

function figureHtml(candidate) {
  const refs = (candidate.image_refs || []).filter((ref) => ref && typeof ref === 'object'
    && ref.exists !== false && ref.asset_role !== 'option-image');
  const optionImages = (candidate.options || []).filter((o) => o && o.image && typeof o.image === 'object' && o.image.exists !== false);
  const all = refs.concat(optionImages.map((o) => o.image));
  if (!all.length) return '';
  return `<div class="crops"><div class="crops-head">圖片（機器裁切，僅供對照）<span class="hint">${all.length} 張</span></div>
    ${all.map((ref) => `<figure class="crop">
      <img src="${esc(fileUrl(ref.path))}" alt="${esc(ref.description || ref.raw_ref || '裁切圖')}" loading="lazy">
      <figcaption>${esc(ref.description || ref.label || '')}</figcaption>
    </figure>`).join('')}</div>`;
}

/* The shared stem of a question group.
   A paper prints one setup paragraph and then two or more questions that all ask about it, and the
   later ones say so only as 「承上題」 or as the paper's own 「依序回答下列三題」. Showing such a question
   on its own is showing a question that cannot be read: `承上題，達穩定狀態之平均血中濃度約為多少mg/L？`
   needs the paragraph above it. So the group's head stem is carried onto every member and drawn
   above the member's own stem, marked as the shared part rather than pasted into the question and
   pretending the paper printed it there.

   The head gets a header too, and it does not repeat its own stem. It used to get nothing at all,
   which left the reviewer unable to tell that the question they were reading was the start of a
   group - so they would judge it, move on, and never see that two more questions depended on it.
   The header is the smallest thing that says so. */
function groupHtml(candidate) {
  const size = Number(candidate.group_size || 1);
  if (size < 2) return '';
  const shared = candidate.shared_stem;
  const position = candidate.group_position;
  const where = (position === undefined || position === null)
    ? `題組 ${size} 題` : `題組 ${Number(position) + 1}／${size}`;
  if (!shared) {
    // The head: it holds the shared stem, so the note points forward instead of repeating it.
    return `<div class="shared-stem head"><div class="shared-head">${esc(where)}`
      + `<span class="hint">本題以下是同一題組，共同題幹就是本題題幹</span></div></div>`;
  }
  return `<div class="shared-stem"><div class="shared-head">共用題幹（${esc(where)}）`
    + `<span class="hint">本題延續上方題組</span></div>`
    + `<div class="shared-body">${richText(shared)}</div></div>`;
}

function editorHtml(candidate, options) {
  return `
    <label>題幹（直接照紙本打，錯字才改）</label>
    <textarea id="editStem"></textarea>
    <label>選項</label>
    ${options.map((option) => `<div class="opt-edit"><span class="k">${esc(option.key)}</span><textarea id="editOpt_${esc(option.key)}" style="min-height:52px"></textarea></div>`).join('')}
    <div class="dirty" id="dirtyBox" style="display:none">已改動，按「儲存修正」寫入。</div>`;
}

function evidenceHtml(candidate, metadata) {
  const rows = [
    ['答案', candidate.answer || '—'],
    ['可給分的選項', ((candidate.answer_payload || {}).accepted_values || []).join('、') || '—'],
    ['答案來源', metadata.answer_authority_source === 'answer' ? '官方答案卷' : metadata.answer_authority_source === 'corrected' ? '官方更正答案卷' : (metadata.answer_authority_source || '—')],
    ['parser 狀態', metadata.parser_status || '—'],
    ['審核狀態', metadata.review_status === 'human_corrected' ? '已人工修正' : (metadata.review_status || '—')],
  ];
  return `<details class="evidence" style="margin-top:24px;border-top:1px solid var(--line);padding-top:12px">
    <summary style="cursor:pointer;color:var(--muted);font-size:12.5px">來源與血統</summary>
    <div style="margin-top:10px;display:flex;flex-direction:column;gap:6px">
      ${rows.map(([k, v]) => `<div style="display:flex;gap:10px;font-size:12.5px"><span style="flex:none;width:88px;color:var(--muted)">${esc(k)}</span><span style="word-break:break-word">${esc(v)}</span></div>`).join('')}
    </div></details>`;
}

/* ------------------------------------------- right: the paper, as a document to read */
/* The reviewer scrolls this and finds the question by eye. Locating a question in a
   printed paper is exactly what a person does instantly and a machine does badly.

   The pane is deliberately *disconnected* from the question list. Switching questions must
   not touch it: the reviewer scrolls the paper once, to where the questions are, and keeps
   it there while moving through them. Re-assigning the iframe's src - even to the same file
   - reloads the document and throws the scroll position away, which is what made an earlier
   version jump back to page 1 on every keypress. So the src is set only when the document
   itself changes, and otherwise left alone entirely. */
function renderPaperSide() {
  const candidate = (S.rows[S.index] || {}).candidate || {};
  const pdf = (candidate.source_files || {}).official_pdf || (candidate.metadata || {}).question_pdf_relative;

  if (!pdf) {
    S.paperPath = null;
    $('paperSide').innerHTML = `<div class="side-head">紙本原卷</div>
      <div class="paper-missing">這一題沒有對應的官方 PDF 路徑，無法對照。<br>請不要憑抽出文字判斷。</div>`;
    return;
  }
  if (pdf === S.paperPath && $('paperFrame')) {
    // Same paper as the previous question: leave the viewer exactly where it is.
    const hint = $('paperHint');
    if (hint) hint.textContent = `用滾輪找第 ${candidate.question_number || '?'} 題`;
    return;
  }
  S.paperPath = pdf;
  $('paperSide').innerHTML = `<div class="side-head">紙本原卷<span class="hint" id="paperHint">用滾輪找第 ${esc(candidate.question_number || '?')} 題</span></div>
    <iframe id="paperFrame" class="paper-frame" src="${esc(fileUrl(pdf))}#view=FitH" title="官方題目 PDF"></iframe>`;
}

/* ---------------------------------------------------------------- navigation */
async function go(index) {
  // Bound by `S.rows`, not by `S.view`: the question the reviewer can reach is the question they can
  // see, and with a chip on those are different sets. This is the other half of the 78-80 fix - the
  // bounds, the drawing and the stepping all read the same array.
  if (index < 0 || index >= S.rows.length) return;
  S.index = index;
  scopeToHash();
  S.editing = false;
  S.draft = null;
  renderList();
  renderTextSide();
  renderPaperSide();
  setEditMode(false);
  $('textSide').scrollTop = 0;
  // Nothing is fetched here, and that is the fix.
  //
  // This used to ask `/api/workflow?candidate_key=...` on every step and re-render the panel with
  // the result. Measured, that endpoint returns **1.2 MB and takes 0.7 s** per call, because it
  // ships the whole queue index (2,000 `queue_items`) to describe one question - and the field it
  // set, `S.data`, was never read by anything. So every W/S press paid three costs for nothing:
  // a 1.2 MB download, a 0.7 s wait, and a re-render that replaced the panel's innerHTML and
  // therefore re-created its `<img>` elements, which throws away the browser's decoded copy and
  // re-fetches every crop. On a chemical-structure paper - four option pictures per question plus
  // the question figure - that is the delay that was reported.
  //
  // Everything the panel draws arrived with `/api/candidates`: stem, options, answer,
  // `image_refs`, and the row's own `review.action`. Nothing here needs a per-question request.
}

function closeEditor() {
  S.editing = false;
  $('actFix').classList.remove('on');
  $('actSave').style.display = 'none';
  $('actAccept').style.display = '';
}

function setEditMode(on) {
  if (on) {
    // The editor and the note are two different things to write about a question, and the pane can
    // only show one at a time - so opening the editor closes the note.
    closeNote();
    S.editing = true;
    $('actFix').classList.add('on');
    $('actSave').style.display = '';
    $('actAccept').style.display = 'none';
  } else {
    closeEditor();
    closeNote();
  }
  renderTextSide();
}

/* The note, opened deliberately by `只加註記` rather than shown under every question.

   It used to be in the DOM and never visible: `reasonBox` only ever had its `on` class *removed*,
   never added, so `reasonText.value` was empty on every decision and every note was silently
   dropped. A field nobody can see is a field nobody fills in. Opening it on demand also keeps the
   fast pass fast - the reviewer who has nothing to add never has their eye drawn to a box. */
function closeNote() {
  $('reasonBox').classList.remove('on');
  $('actNote').classList.remove('on');
  // Clear the text and forget the owner. Not clearing it was the whole defect: the box is shared by
  // every question, so a note left in it was silently re-attached to whichever question was decided
  // next. Forgetting the owner too, so `decide()` cannot read a stale box as this question's note.
  $('reasonText').value = '';
  $('reasonText').disabled = false;
  $('noteFor').textContent = '';
  S.noteKey = null;
}

/* 註記框裡的 Enter 是「存」，Shift+Enter 才是換行。

   原本只有 Cmd/Ctrl+Enter 會存，而 `Enter` 什麼都不做——那是一個看不出來的鍵：畫面把框畫出來、
   游標也放進去了，看起來就是在等你打字後按 Enter，結果按下去沒有任何事發生。使用者回報
   「註記目前無法輸入」就是這個。一個只在複合鍵上才會動的儲存鍵，等於沒有儲存鍵。

   按鍵要在 `keydown` 就攔下來（不是等瀏覽器把換行插進去再補救），否則 Enter 會先插入一個
   換行字元，再被存成一份多一個空行的註記。 */
function noteKeydown(event) {
  if (event.key !== 'Enter') return;
  if (event.shiftKey) return;            // Shift+Enter 交還給瀏覽器：使用者要的是下一行
  event.preventDefault();
  saveNote();
}

function toggleNote(force) {
  const item = S.rows[S.index];
  // 已經開著的時候再按一次註記（或再按 C）＝存起來。
  // 「再按一次同一個按鈕存」是使用者要求的第二種存法：滑鼠已經在按鈕上了，不該逼人回頭去按 Enter。
  // 但如果框是空的，那只是關掉，不要跳「沒有寫任何註記」的錯誤——那會讓「我只是想收起框」變成一個失敗。
  if (force === undefined && $('reasonBox').classList.contains('on')) {
    if ($('reasonText').value.trim()) { saveNote(); return; }
    closeNote(); return renderTextSide();
  }
  const open = force !== undefined ? force : !$('reasonBox').classList.contains('on');
  if (!open) { closeNote(); return renderTextSide(); }
  // `closeEditor`, not `setEditMode(false)`: the latter also closes the note, which would undo the
  // box this function is about to open.
  closeEditor();
  $('reasonBox').classList.add('on');
  $('actNote').classList.add('on');
  $('reasonText').disabled = false;
  const existing = item ? noteOf(item.candidate_key) : '';
  $('reasonText').value = existing;
  // Remember whose box this is. `decide()` uses this to refuse stale text.
  S.noteKey = item ? item.candidate_key : null;
  // Say what the note attaches to, because the same box is used before and after a decision and
  // those are different acts: before, the note explains the decision about to be made; after, it
  // must not look like the reviewer is changing their mind.
  const standing = item ? verdictOf(item.candidate_key) : '';
  $('noteFor').textContent = standing
    ? `這題已標記「${LABEL[standing] || standing}」；這裡寫的註記會保留那個標記，不會改掉它。Enter 儲存、Shift+Enter 換行。`
    : '這題還沒決定；儲存註記不會把它算成已過目。Enter 儲存、Shift+Enter 換行。';
  $('reasonText').focus();
  renderTextSide();
}

/* Save a note. It is a `comment` event: recorded, append-only, and not a verdict - the question
   stays 未看 until the reviewer actually decides. The server reaffirms whatever decision is already
   on the question, so annotating after `確認正常` cannot withdraw it. */
async function saveNote() {
  const item = S.rows[S.index];
  if (!item) return;
  const notes = $('reasonText').value.trim();
  if (!notes) return toast('沒有寫任何註記', true);
  try {
    const response = await fetch('/api/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate_key: item.candidate_key, action: 'comment', notes,
        reviewer: 'local', source: 'linear_v2',
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    S.notes.set(item.candidate_key, notes);
    closeNote();
    toast(`第 ${item.question_number} 題：註記已存`);
    renderTextSide();
    renderList();
  } catch (error) {
    toast(`寫入失敗：${error.message || error}`, true);
  }
}

function markDirty() {
  const box = $('dirtyBox');
  if (box) box.style.display = '';
}

/* ---------------------------------------------------------------- decisions */
async function decide(action) {
  const item = S.rows[S.index];
  if (!item) return;
  // `需重看` and `阻擋` are recorded **without** a reason. Requiring one made the reviewer stop and
  // type before every flag, and a fast first pass is the whole point of the linear walk: the reason
  // is written when the reviewer knows it, not as a toll on the way past. If the note box is open,
  // whatever is typed goes with the decision; otherwise the decision is recorded bare, which is the
  // common case.
  //
  // Read the box **only when it is open and belongs to this question**. Reading it unconditionally
  // was the defect: the box is shared by every question and was not cleared, so the note typed on
  // the previous question became the next question's reason. A note is written against the question
  // it was typed on, and an empty box is a decision with no note.
  const noteHere = $('reasonBox').classList.contains('on') && S.noteKey === item.candidate_key;
  const notes = noteHere ? $('reasonText').value.trim() : '';
  const body = { candidate_key: item.candidate_key, action, notes, reviewer: 'local', source: 'linear_v2' };
  try {
    const response = await fetch('/api/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    S.verdict.set(item.candidate_key, action);
    if (notes) S.notes.set(item.candidate_key, notes);
    // A question decision changes what the answer area is allowed to show (its eligible set is
    // "questions whose question review is accept/unblock"), so the other panes are now stale. This
    // is the one place a question decision is written, hence the one place to say so.
    invalidateAreas();
    renderList();
    closeNote();
    toast(`第 ${item.question_number} 題：${LABEL[action]}`);
    await next();
  } catch (error) {
    toast(`寫入失敗：${error.message || error}`, true);
  }
}

/* Save the correction. It is stored as an append-only `correct` event carrying the edited
   text, so what the reviewer typed can be diffed against what the parser produced; the
   decision itself stays `reviewed`, waiting for an explicit accept. */
async function saveCorrection() {
  const item = S.rows[S.index];
  const candidate = (item || {}).candidate || {};
  if (!item) return;
  const stem = $('editStem').value;
  const options = (candidate.options || []).map((option) => ({
    key: option.key,
    text: document.getElementById(`editOpt_${option.key}`)?.value ?? option.text,
    image: option.image ?? null,
  }));
  // The same ownership rule as `decide()`: the box is the current question's only when it is open
  // and remembers this key. Opening the 修正 editor closes the note box, so this is normally empty
  // and the default below applies - but it must not pick up a sibling question's stale text either.
  const notes = ($('reasonBox').classList.contains('on') && S.noteKey === item.candidate_key)
    ? $('reasonText').value.trim() : '';
  if (stem === (candidate.stem || '') && options.every((o, i) =>
      o.text === ((candidate.options || [])[i] || {}).text)) {
    toast('沒有改動', true);
    return;
  }
  try {
    const response = await fetch('/api/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate_key: item.candidate_key, action: 'correct',
        correction: { stem, options, answer: candidate.answer || '' },
        notes: notes || '人工依紙本修正抽出文字。',
        reviewer: 'local', source: 'linear_v2',
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    S.verdict.set(item.candidate_key, 'correct');
    // A correction is what the 錯題討論區 exists to show, and it is recorded server-side as a
    // `question_correction_feedback_events` row. Mark the panes stale so the new case appears without
    // a reload.
    invalidateAreas();
    $('reasonText').value = '';
    toast(`第 ${item.question_number} 題：修正已儲存`);
    setEditMode(false);
    await next();
  } catch (error) {
    toast(`修正儲存失敗：${error.message || error}`, true);
  }
}

/* Step one row forward **in the list**, not in the paper.

   The two are different as soon as a chip is on, and that difference is the defect this replaces:
   with 題組 selected and a group running 78-80, pressing S on 80 used to jump to question 1 of the
   next paper, because the paper order said "80 is followed by the next paper's question 1" while the
   list the reviewer was reading said "80 is followed by the next group". The reviewer navigates the
   list they are looking at, so the list is what is walked.

   A decision can also change the list, and this is the case the old design was trying to avoid: under
   未看, judging a question removes it from 未看. Rather than refusing to rearrange - which is what made
   navigation ignore the filter - the list is rebuilt and the cursor is kept on **the row that took the
   judged question's place**. That is the correct next stop: the judged row is gone, so what is now
   under the cursor is the next question of this filter. Stepping past it instead would skip one
   question on every decision, which is the opposite failure and just as silent. */
async function next() {
  const currentKey = (S.rows[S.index] || {}).candidate_key;
  const was = currentKey ? S.rows.findIndex((i) => i.candidate_key === currentKey) : -1;
  rebuildRows(currentKey);
  // If the judged question is still in the list, this decision did not remove it, so step past it.
  // If it is gone, `rebuildRows` has already put its replacement under the cursor.
  const survived = was >= 0 && (S.rows[was] || {}).candidate_key === currentKey;
  const target = survived ? was + 1 : S.index;
  if (target < S.rows.length) { await go(target); return; }
  renderList();
  renderTextSide();
}

/* ==================================================================== 四個區
   The request named four areas: 首頁 / 題目審核區 / 答案審核區 / 錯題討論區. They are one page with a
   mode switch, not four pages, because they are four readings of **one** queue: the answer area's
   eligible set is defined by the question area's decisions, and the discussion area reads the
   corrections those two produced. Four pages would mean four loaders of the same 198 MB
   `candidates.jsonl`, and four chances for them to disagree about what is in the queue.

   What each area is allowed to do is the whole point of separating them:

     題目審核區   reads the paper beside the extracted text. Decisions: 確認／修正／需重看／阻擋.
     答案審核區   reads the answer sheet only, and **only for questions already accepted** - the
                 server refuses otherwise (`/api/answer-review` answers 409 with the question's
                 action), because reviewing the answer of a question nobody accepted is reviewing
                 something that may not survive.
     錯題討論區   reads what a person changed and what class of change it was. It writes nothing:
                 the lesson is already recorded by the correction itself (see below), and this area
                 only makes it visible.

   The mode is in the hash (`#答案/…`) so each area is linkable and a reload reopens it. The bare
   `#類科/年/次/科目` form keeps meaning the question area, because those bookmarks exist. */
