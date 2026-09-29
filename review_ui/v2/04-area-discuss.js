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
  //: 這一區自己的範圍篩選（類科／年度／考次／科目）。四個層級，與題目區同一組語意：
  //: `''`＝全部（刻意的），`null`＝還沒選。**預設四層都是全部**，因為卡住的題散在整個題庫，
  //: 預設一個具體範圍會把其他範圍的卡住題藏起來，而這一區存在的理由就是要把卡住的題找出來。
  scope: { category: '', year: '', sitting: '', subject: '' },
  //: 樹是**卡住的那一群紙本**的分類樹，由伺服器的 `discuss_taxonomy()` 給（不是整份佇列）。
  tree: {},
  //: 這棵樹自己的卡住題數，與樹來自同一次量測。
  stuckTotal: 0,
  //: 這一輪伺服器回的符合數與回傳數。`filtered > returned` 時清單被上限截掉了，要說出來。
  filteredCount: null,
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
  //: The notes the reviewer typed in this pane, per key, kept so a re-render does not lose a
  //: sentence that has not been saved yet. The **recorded** note is read from the row itself
  //: (`review.notes`), which is the same field the question area shows - so there is one source for
  //: "what the last person said" rather than a second copy that can drift.
  noteDraft: new Map(),
  //: The reading size of the extracted text, in px. Persisted in `localStorage` because it is a
  //: preference about the reviewer's own eyes, not about a question.
  fontSize: 15,
  //: The image about to be pasted in, as a data URL, and where it will be placed.
  pendingCrop: '',
  cropPlacement: 'stem',
  cropOption: '',
};

// The reading size survives a reload. It is a preference about the reviewer's eyes, not about a
// question, so it belongs beside them rather than in the queue. Restored once, at load time, and
// clamped to the same range `setDiscussFont` enforces - a value from an older build (or a hand-edited
// `localStorage`) must not be able to render the pane unreadable.
try {
  const saved = Number(window.localStorage.getItem('v2.discuss.fontSize'));
  if (Number.isFinite(saved) && saved >= 11 && saved <= 26) D.fontSize = saved;
} catch (error) { /* 私密模式沒有 localStorage */ }

/* 重新載入這一區。`force`＝連資料一起重抓（剛寫入過東西）。 */
async function renderDiscuss(force) {
  const list = $('discussList'), main = $('discussMain'), pdf = $('discussPdf');
  if (!list || !main || !pdf) return;
  if (force) D.loaded = false;
  if (!D.loaded) {
    list.innerHTML = '<div class="empty">載入中…</div>';
    await loadDiscuss();
  }
  const total = D.rows.length;
  // 左欄是這一區的側邊資訊：先給四層篩選，再說「這裡有幾題、怎麼壞的」，再列題。統計本來被放在
  // 中欄卡片的最下面，要滾到最底才看得到，而且會被誤讀成「這一題的」數字——它其實是整個佇列的。
  const side = discussScopeHtml() + discussSideHtml();
  if (!total) {
    list.innerHTML = side
      // The scope picker is drawn even with no rows, so an empty result is something the reviewer
      // can widen rather than a dead end. The four selects are the only way back out of a filter.
      + '<div class="list-head">卡住的題<b>0</b></div>';
    main.innerHTML = '<div class="empty-area">這個範圍沒有卡住的題。<br>'
      + '（把上面四層放寬，或人按「阻擋」、管線／AI 退回的題會出現在這裡。）</div>';
    pdf.innerHTML = '<div class="empty-area">（無）</div>';
    bindDiscussScope();
    return;
  }
  D.index = Math.min(Math.max(0, D.index), total - 1);
  const scopeKey = `${D.scope.category}\u0000${D.scope.year}\u0000${D.scope.sitting}\u0000${D.scope.subject}`;
  const returned = D.rows.length;
  const filtered = D.filteredCount === null ? returned : D.filteredCount;
  list.innerHTML = side
    + `<div class="list-head">卡住的題<b>${filtered > returned ? `${returned} / ${filtered}` : filtered}</b>`
    + '<span class="hint">人阻擋或 AI／管線退回</span></div>'
    + (filtered > returned
      ? `<div class="hint" style="padding:0 10px 6px">這個範圍有 ${filtered} 題，清單只畫前 ${returned} 題。`
        + '收窄上面的篩選才看得到全部。</div>' : '')
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
  bindDiscussScope();
}

/* 用這一區目前的篩選向伺服器要資料。

   篩選是**伺服器的**，不是拿回 500 題後在瀏覽器過濾：卡住的題散在整個題庫，而伺服器才能看到
   全部。四個層級與題目區同名（`category`/`year`/`ordinal`/`subject`），所以兩個區域的網址與
   語意一致。"全部" 的那一層**不送參數**，而不是送空字串——伺服器把缺參數讀成"不篩"，送空字串
   多一層翻譯。 */
async function loadDiscuss() {
  const params = { limit: '500' };
  if (D.scope.category) params.category = D.scope.category;
  if (D.scope.year) params.year = D.scope.year;
  if (D.scope.sitting) params.ordinal = D.scope.sitting;
  if (D.scope.subject) params.subject = D.scope.subject;
  const payload = await fetchAreaJson('/api/discuss', params);
  if (!payload) {
    $('discussList').innerHTML = `<div class="empty">讀不到討論區（${esc(A.error['/api/discuss'] || '')}）</div>`;
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
  // The tree and its count come back on **every** response and are always the unfiltered stuck
  // population (see `discuss_taxonomy`). Assigning them here rather than only on the first load is
  // what keeps the pickers complete: the same tree arrives whatever filter is in force, so choosing
  // a subject cannot collapse the other subjects out of the picker.
  if (payload.taxonomy) D.tree = payload.taxonomy;
  if (payload.stuck_total !== undefined) D.stuckTotal = Number(payload.stuck_total) || 0;
  D.filteredCount = payload.filtered_count === undefined || payload.filtered_count === null
    ? null : Number(payload.filtered_count);
  D.principles = payload.principles || D.principles;
  D.questions = payload.repair_questions || D.questions;
  D.loaded = true;
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

/* 中欄：依序是「為什麼卡住 → ① 機器 → ② AI → ③ 原題 → ④ 擷圖／抽換 → ⑤ 手動修改 → ⑥ 註解 → ⑦ 原則 → ⑧ 反問」。
   順序就是閱讀順序：先知道它為什麼在這裡，再看機器量到什麼、模型說了什麼，然後**讀一遍原題**，
   把缺的圖補上，最後才動手改文字、寫下「這題錯在哪」的註解，以及從它歸納出來的原則。

   「讀一遍原題」與「註解」是使用者的回報裡具體缺的兩塊：
   「我需要的是可以看到原題，可以有圖片擷圖與抽換的區域，可以調整字體的區域以及把我的做法
   給 AI 參考的註解區，這些你都沒有做到」。這四項現在各有自己的一段，而且在同一個滾動流裡。 */
function discussCenterHtml(candidate, key) {
  const number = candidate.question_number ?? '?';
  const subject = ((candidate.metadata || {}).normalized_subject_name)
    || (candidate.metadata || {}).group_name || candidate.paper_id || '';
  return '<div class="case" id="discussCase" style="--reading-size:' + D.fontSize + 'px">'
    + `<h2>第 ${esc(number)} 題 <span class="kind">${esc(subject)}</span>`
    + discussFontHtml() + '</h2>'
    + `<div class="meta">為什麼在這裡：${discussWhyHtml(candidate)}</div>`
    + `<div class="diff"><b>① 機器偵測</b>${discussDisputesHtml(candidate.disputes)}</div>`
    + `<div class="diff" style="margin-top:10px"><b>② AI 意見</b>${discussFindingHtml(candidate)}</div>`
    + '<div class="orig"><b>③ 原題（照紙本讀一遍）</b>'
    + discussOriginalHtml(candidate) + '</div>'
    + discussCropHtml(candidate)
    + '<div class="edit-block"><b>⑤ 手動修改（照紙本打，錯字才改）</b>'
    + '<label>題幹</label>' + discussStem(candidate, key)
    + '<label>選項</label>' + discussOptions(candidate, key)
    + '<div class="dirty" id="dDirty" style="display:none">已改動，按「儲存修正」寫入。</div>'
    + '</div>'
    + discussNoteHtml(candidate)
    + discussPrinciplesHtml()
    + discussQuestionsHtml()
    + '<div class="answer-bar">'
    + '<button class="ghost" data-focus="8">8 紙本</button>'
    + '<button class="act" id="dSave">儲存修正 E</button>'
    + '<button class="ghost" id="dHold">退回未審</button>'
    + '<button class="ghost" id="dClear">清空草稿</button></div>'
    + '</div>';
}

/* 字體調整。

   使用者回報要「可以調整字體的區域」。這不是裝飾：左邊的抽取文字要跟右邊的 PDF 對照，
   而瀏覽器內建的 PDF viewer 有自己的縮放（52%），所以兩邊的字級必須各自可調，
   否則把一邊調到看得到細節的時候另一邊就不再對得上。

   大小是一個 CSS 變數，不是八個字級——一條規則，一個地方。`15px` 是題目區題幹的基準，
   所以按兩次「+」是審題者自己的 1.25 倍，而不是另一套基準。 */
function discussFontHtml() {
  const size = D.fontSize;
  return `<span class="fontctl" title="抽取文字的字級（不影響右邊的 PDF）">`
    + `<button type="button" data-font="down" title="縮小">A−</button>`
    + `<span>${size}px</span>`
    + `<button type="button" data-font="up" title="放大">A＋</button>`
    + `<button type="button" data-font="reset" title="回到預設">重設</button></span>`;
}

/* ③ 原題：這題**照紙本讀一遍**的樣子。

   跟題目審核區讀的是同一個 `stem`／`options`，也走同一個 `richText()`（所以上下標真的是上下標），
   但**不是編輯框**：使用者的回報是「可以看到原題」。編輯框裡的東西是將要寫入的修正，
   把它當作原題讀，就分不出「紙本這樣印」與「我把它改成這樣」——而這兩件事正是這一區要累積的知識。
   所以原題是一個獨立的區塊，在編輯框之前。 */
function discussOriginalHtml(candidate) {
  const options = candidate.options || [];
  const answer = new Set(
    ((candidate.answer_payload || {}).accepted_values || [])
      .map((v) => String(v).trim().toUpperCase()).filter(Boolean));
  if (!answer.size) {
    String(candidate.answer || '').split(/[,，或]/).map((v) => v.trim().toUpperCase())
      .filter(Boolean).forEach((v) => answer.add(v));
  }
  const shared = candidate.shared_stem;
  const size = Number(candidate.group_size || 1);
  const groupNote = size > 1
    ? `<div class="hint">題組 ${size} 題${shared ? '｜共用題幹如下' : ''}</div>` : '';
  return groupNote
    + (shared ? `<div class="shared-stem"><div class="shared-head">共用題幹</div>`
        + `<div class="shared-body">${richText(shared)}</div></div>` : '')
    + `<div class="stem">${richText(candidate.stem || '（題幹空白）')}</div>`
    + `<div class="opts">${options.map((option) => `
        <div class="opt${answer.has(option.key) ? ' is-answer' : ''}">
          <span class="k">${esc(option.key)}</span><span class="t">${richText(option.text)}</span>
        </div>`).join('') || '<span class="hint">（沒有抽到選項）</span>'}</div>`
    + `<div class="hint" style="margin-top:8px">答案：${esc(candidate.answer || '—')}</div>`;
}

/* ④ 擷圖／抽換。

   這一段是使用者的回報裡最明確的缺項：「可以有圖片擷圖與抽換的區域」。舊的 `/legacy` 有完整的
   「補圖與綁定」（貼上人工修正圖片、選題幹／A–D／表格／題組共用、補圖說明、補圖註記、
   取代既有圖片），v2 把它整段丟掉——而「機器裁切不完整」是實測會發生的事，沒有這一段就無從修。

   寫入走既有 `/api/manual-asset`，不是新路徑：它已經會把圖存成檔案、把 `asset_ref`
   嵌進 correction 的 `stem`／`options`／`answer`（見 `save_manual_image_asset`），
   而 correction 是 append-only 的事件。這一區只提供介面。 */
function discussCropHtml(candidate) {
  const refs = (candidate.image_refs || []).filter((r) => r && typeof r === 'object');
  const existing = refs.length
    ? `<div class="crop-list">這一題目前有 ${refs.length} 張圖：`
      + refs.map((r) => `<a href="${esc(fileUrl(r.path))}" target="_blank" rel="noopener">`
          + `${esc(r.asset_role || 'image')}</a>`).join('、') + '</div>'
    : '<div class="crop-list">這一題目前沒有圖。若紙本有圖而抽取沒有，在這裡貼上。</div>';
  return '<div class="crop-block"><b>④ 擷圖／抽換</b>'
    + '<label style="display:block;font-size:11.5px;color:var(--muted);margin:6px 0 4px">'
    + '在 PDF 或截圖工具框好範圍，複製後貼上（⌘V），或選圖檔。</label>'
    + '<div class="drop" id="dCropDrop">貼上圖片（⌘V），或把圖檔拖進來、<label style="display:inline">'
    + '<input type="file" id="dCropFile" accept="image/*" style="display:none">'
    + '<u style="cursor:pointer">選擇檔案</u></label>。</div>'
    + '<img class="preview" id="dCropPreview" style="display:none" alt="待補的圖">'
    + '<div class="place" id="dCropPlace">'
    + ['stem', 'A', 'B', 'C', 'D', 'table', 'group'].map((p) =>
        `<button type="button" data-place="${p}">${p === 'stem' ? '題幹' : p === 'table' ? '表格'
          : p === 'group' ? '題組共用' : `選項 ${p}`}</button>`).join('')
    + '</div>'
    + '<input type="text" id="dCropCaption" placeholder="補圖說明，例如：第 53 題結構圖，人工裁切補上">'
    + '<input type="text" id="dCropNotes" placeholder="補圖註記，例如：MinerU 原圖裁切不完整">'
    + '<div class="rowbtn"><button class="ghost" id="dCropSave">儲存補圖</button>'
    + '<label class="hint"><input type="checkbox" id="dCropReplace"> 取代既有圖片</label>'
    + '<span class="hint" id="dCropStatus"></span></div>'
    + existing + '</div>';
}

/* ⑥ 註解：給 AI 參考、也給下一個審題者參考的一段話。

   使用者的回報：「基本原則的上面應該要有一個註解，這樣我才能說哪裡錯了或是我改了哪裡」。
   所以它就在 基本原則 之上——先說這題，再說從它歸納出來的通則。

   它寫的是 `comment` 事件，與題目區的「只加註記 C」是同一種寫法，理由也一樣：
   註解是**關於**一題的話，不是對它的判決。伺服器的 `_reaffirm_standing_action`
   會在存註解時重申底下那個決定，所以在這裡寫字不會把已經通過的題目踢回未審。 */
function discussNoteHtml(candidate) {
  // The note the question area would show for this question, from the same field it reads
  // (`review.notes` → `item.note` → `S.notes` → the 註記 box). Consistency here is not cosmetic: a
  // reviewer who annotated a question in the 題目審核區 and then opens it in 錯題討論區 must see the
  // same sentence in both, or the two areas disagree about what the last person said.
  //
  // It is drawn as **the previous note**, not pre-filled into the box, because the box is a draft of
  // the *next* note: putting the old text in it and saving would append a duplicate of a note that
  // is already recorded.
  const previous = String((candidate.review || {}).notes || '').trim();
  return '<div class="note-block">'
    + '<label>⑥ 註解（哪裡錯了／我改了什麼；會存進 append-only 紀錄）</label>'
    + '<textarea id="dNote" placeholder="例如：題幹的 1,25-雙羥維生素D 在紙本是 1,25-(OH)<sub>2</sub>D；'
    + '我改成紙本的拼法，AI 再看時請照這個。">'
    + `${esc(D.noteDraft.get(candidate.candidate_key) ?? '')}</textarea>`
    + '<div class="rowbtn" style="display:flex;gap:6px;align-items:center;margin-top:6px">'
    + '<button class="act" id="dNoteSave">儲存註解</button>'
    + '<span class="hint">不是判決：不會把題目算成已過目，也不會蓋掉已有決定。</span></div>'
    + (previous
        ? `<div class="note-hist"><div class="n"><span class="when">已存</span>`
          + `${esc(previous)}</div></div>`
        : '')
    + '</div>';
}

/* 右欄：紙本原卷（題目卷），與左欄脫鉤。 */
function discussPdfHtml(candidate) {
  const pdf = (candidate.source_files || {}).official_pdf
    || (candidate.metadata || {}).question_pdf_relative;
  const head = `<div class="pdf-head">紙本（題目卷）　第 ${esc(candidate.question_number ?? '?')} 題`
    + `<span class="hint">${esc(candidate.paper_id || '')}</span>`
    + '<span class="spacer"></span>'
    + '<span class="hint">用滾輪找第 ' + esc(candidate.question_number ?? '?') + ' 題（要擷圖就框好按 ⌘C 再貼到左邊）</span></div>';
  // The frame is **not** re-created when the paper has not changed: re-assigning `src` - even to the
  // same file - reloads the viewer and throws the scroll position away, which is the jump the
  // question area documented and this pane has to respect for the same reason (the reviewer scrolls
  // once to the questions and keeps it there while stepping through them).
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

/* Every category's bucket merged into one, so a level below 全部類科 can still be chosen.

   The shared helpers (`availableSittings`/`availableSubjects`/`countPapers`/`countQuestions`) take
   **one** category's bucket, because that is what the question area asks them. But the 錯題討論區
   defaults to 全部類科 on purpose (stuck questions are scattered across the whole corpus, and
   hiding 90% of them by default was the reason this area was rebuilt). With no single bucket,
   `bucket` was `undefined` and the year/sitting/subject pickers came out empty — a filter row where
   only the first level works.

   So 全部類科 gets a *merged* bucket: same shape, counts added, subject `papers` concatenated. The
   helpers walk it unchanged, which is the point — this aggregates the tree, it does not re-define
   it, and it does not touch `treeFrom` (the queue builder's browser fallback). */
function mergedBucket(tree) {
  const merged = { years: {}, papers: 0, questions: 0 };
  for (const bucket of Object.values(tree || {})) {
    merged.papers += bucket.papers || 0;
    merged.questions += bucket.questions || 0;
    for (const [year, yearBucket] of Object.entries(bucket.years || {})) {
      const mergedYear = (merged.years[year] = merged.years[year]
        || { sittings: {}, papers: 0, questions: 0 });
      mergedYear.papers += yearBucket.papers || 0;
      mergedYear.questions += yearBucket.questions || 0;
      for (const [sitting, sittingBucket] of Object.entries(yearBucket.sittings || {})) {
        const mergedSitting = (mergedYear.sittings[sitting] = mergedYear.sittings[sitting]
          || { subjects: {}, papers: 0, questions: 0 });
        mergedSitting.papers += sittingBucket.papers || 0;
        mergedSitting.questions += sittingBucket.questions || 0;
        for (const [subject, leaf] of Object.entries(sittingBucket.subjects || {})) {
          const mergedLeaf = (mergedSitting.subjects[subject] = mergedSitting.subjects[subject]
            || { papers: [], questions: 0 });
          mergedLeaf.papers.push(...(leaf.papers || []));
          mergedLeaf.questions += leaf.questions || 0;
        }
      }
    }
  }
  return merged;
}

/* 這一區的四層篩選：類科／年度／考次／科目。

   使用者回報 #2：「要有篩選，比較好審核」。這一區原本沒有篩選，理由是「卡住的題散在整個題庫」
   ——那個顧慮是對的，但結論錯了：**預設全部**（四層都 `''`）就不會把任何卡住的題藏起來，而想
   收窄的人可以收窄。

   四層的值、下拉的內容、以及「改一層不動其他層」的契約，全部重用題目區的 **純函式**：
   `availableSittings`／`availableSubjects`／`countPapers`／`countQuestions`／`resolveLevel`。
   不重寫一份，因為重寫的那份會與題目區不一致——而一致性正是使用者要的。

   樹是**卡住的那一群**的分類樹（伺服器的 `discuss_taxonomy()`），所以每一個選項至少有一題卡住；
   整份佇列的樹會提供 0 題的分支（實測：`藥師` 卡住 0 題）。 */
function discussScopeHtml() {
  const tree = D.tree || {};
  const categories = Object.keys(tree).sort();
  // 全部類科 is a real bucket here (the merge), not `undefined`: otherwise the three levels below it
  // would offer nothing and only the top filter would work.
  const bucket = D.scope.category ? tree[D.scope.category] : mergedBucket(tree);
  // Each level is reconciled against what is actually on offer, in order, with the **same**
  // `resolveLevel` the question area uses. Without it a value that stops existing (a subject that
  // is not in the newly chosen category) would stay in `D.scope`, be sent to the server, and filter
  // the list down to nothing while the select showed a different option - the two disagreeing about
  // what is being asked. `''` (全部) survives; a stale specific value falls back to 全部.
  D.scope.category = categories.includes(D.scope.category) ? D.scope.category : '';
  const years = bucket ? Object.keys(bucket.years).filter((y) => /^\d+$/.test(y))
    .sort((a, b) => Number(b) - Number(a)) : [];
  D.scope.year = resolveLevel(D.scope.year, years);
  const sittings = availableSittings(bucket, D.scope.year);
  D.scope.sitting = resolveLevel(D.scope.sitting, sittings);
  const subjects = availableSubjects(bucket, D.scope.year, D.scope.sitting);
  D.scope.subject = resolveLevel(D.scope.subject, subjects);
  const papers = countPapers(bucket, D.scope.year, D.scope.sitting, D.scope.subject);
  const questions = countQuestions(bucket, D.scope.year, D.scope.sitting, D.scope.subject);
  const crumbs = [D.scope.category, D.scope.year && `${D.scope.year} 年`,
                  D.scope.sitting && `第 ${D.scope.sitting} 次`, D.scope.subject].filter(Boolean);
  return '<div class="discuss-scope">'
    + '<div class="ds-head">篩選<span class="hint">四層可各自設定，改一層不會動其他層</span></div>'
    + `<select id="dPickCategory" title="類科">`
    + (categories.length > 1 ? '<option value="">全部類科</option>' : '')
    + categories.map((c) => `<option value="${esc(c)}"${c === D.scope.category ? ' selected' : ''}>`
        + `${esc(c)}（${tree[c].papers} 卷）</option>`).join('') + '</select>'
    + `<select id="dPickYear" title="年度">`
    + (years.length > 1 ? '<option value="">全部年度</option>' : '')
    + years.map((y) => `<option value="${y}"${y === D.scope.year ? ' selected' : ''}>`
        + `${y} 年（${bucket.years[y].papers} 卷）</option>`).join('') + '</select>'
    + `<select id="dPickSitting" title="考次">`
    + (sittings.length > 1 ? '<option value="">全部考次</option>' : '')
    + sittings.map((n) => `<option value="${esc(n)}"${n === D.scope.sitting ? ' selected' : ''}>`
        + `第 ${esc(n)} 次（${countPapers(bucket, D.scope.year, n, '')} 卷）</option>`).join('') + '</select>'
    + `<select id="dPickSubject" title="科目">`
    + (subjects.length > 1 ? '<option value="">全部科目</option>' : '')
    + subjects.map((name) => `<option value="${esc(name)}"${name === D.scope.subject ? ' selected' : ''}>`
        + `${esc(name)}（${countQuestions(bucket, D.scope.year, D.scope.sitting, name)} 題）</option>`).join('')
    + '</select>'
    + `<div class="ds-crumbs">${crumbs.map((part) => `<span class="crumb on">${esc(part)}</span>`).join('')
        || '<span class="crumb">全部</span>'}</div>`
    + `<div class="ds-count">這個範圍 <b>${questions}</b> 題卡住／<b>${papers}</b> 卷`
    + `<span class="hint">（整個討論區 ${D.stuckTotal} 題）</span></div></div>`;
}

/* 篩選的綁定：每個 `onchange` **只寫自己那一格**，再重畫選單與重抓清單。

   這與題目區是同一條契約（見 `buildScope`）：選了類科不可以把年度／考次／科目清成「還沒選」而讓
   `resolveLevel` 去挑一個具體值。使用者的原話是「每次都跳來跳去」——那一條在題目區已經修過，
   這一區沿用同一條。 */
function bindDiscussScope() {
  const category = $('dPickCategory');
  if (category) category.onchange = () => { D.scope.category = category.value; discussReload(); };
  const year = $('dPickYear');
  if (year) year.onchange = () => { D.scope.year = year.value; discussReload(); };
  const sitting = $('dPickSitting');
  if (sitting) sitting.onchange = () => { D.scope.sitting = sitting.value; discussReload(); };
  const subject = $('dPickSubject');
  if (subject) subject.onchange = () => { D.scope.subject = subject.value; discussReload(); };
}

/* 篩選改變＝重新問伺服器。清單是伺服器過濾的，不是拿回 500 題在瀏覽器過濾。

   `D.index` 歸零：新的範圍是不同的問題集，指著舊集合的第 37 題沒有意義。
   樹（`D.tree`）**不動**，它每一輪都一樣，所以選單不會因為篩選而塌陷。 */
async function discussReload() {
  D.index = 0;
  D.loaded = false;
  await renderDiscuss();
}
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
  // 字體：一個 CSS 變數，套在整個 .case 上。不呼叫 renderDiscuss()——重畫會丟掉還沒存檔的
  // 編輯草稿與游標位置；字級只是看的人的眼睛，不該讓題目重新載入。
  for (const button of document.querySelectorAll('#discussMain [data-font]')) {
    button.onclick = () => setDiscussFont(button.dataset.font);
  }
  // `8 紙本`：把焦點送進右側的 PDF 框，與 `1..6` 聚焦選項同一排鍵。
  for (const button of document.querySelectorAll('#discussMain [data-focus]')) {
    button.onclick = () => focusDiscuss(Number(button.dataset.focus));
  }
  bindDiscussCrop(key);
  const noteSave = $('dNoteSave');
  if (noteSave) noteSave.onclick = () => saveDiscussNote(key);
  const noteBox = $('dNote');
  if (noteBox) {
    noteBox.oninput = () => D.noteDraft.set(key, noteBox.value);
    noteBox.onkeydown = (e) => {
      // Enter 存、Shift+Enter 換行——與題目區的註記框同一條規則（見 `noteKeydown`）。
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveDiscussNote(key); }
    };
  }
}

/* 字體大小：一次一階，限制在 11–26px。下限是為了還讀得到，上限是為了還對得上紙本；
   超出這個範圍就不是調整字體，是把畫面拉壞。

   游標位置要保住：只改 CSS 變數、不重畫 DOM，所以正在打字的人不會被打斷。 */
const DISCUSS_FONT_MIN = 11;
const DISCUSS_FONT_MAX = 26;
function setDiscussFont(direction) {
  if (direction === 'reset') D.fontSize = 15;
  else if (direction === 'up') D.fontSize = Math.min(DISCUSS_FONT_MAX, D.fontSize + 1);
  else if (direction === 'down') D.fontSize = Math.max(DISCUSS_FONT_MIN, D.fontSize - 1);
  const pane = $('discussCase');
  if (pane) pane.style.setProperty('--reading-size', D.fontSize + 'px');
  const readout = document.querySelector('#discussMain .fontctl span');
  if (readout) readout.textContent = D.fontSize + 'px';
  try { window.localStorage.setItem('v2.discuss.fontSize', String(D.fontSize)); } catch (error) { /* 私密模式 */ }
}

/* ④ 擷圖／抽換的綁定。

   三種輸入都收：剪貼簿（⌘V，這是主要用法——在 PDF 上框好就複製）、拖進來、選檔。
   三者最後都走同一個 `pendingCrop`，所以「貼上」「拖入」「選檔」不可能各自實作出不同的結果。

   位置按鈕決定 `placement`／`target_option`，與伺服器 `save_manual_image_asset` 的參數同名，
   不另外發明一套詞。選項的位置會轉成 `option`＋目標字母（伺服器要的是這兩個）。 */
function bindDiscussCrop(key) {
  const drop = $('dCropDrop');
  const file = $('dCropFile');
  if (drop) {
    // 貼上：只有在這一區、且焦點不在輸入框時才算。貼進註解框的文字不該被當成圖。
    drop.onclick = () => { if (file) file.click(); };
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('hot'); };
    drop.ondragleave = () => drop.classList.remove('hot');
    drop.ondrop = (e) => {
      e.preventDefault(); drop.classList.remove('hot');
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) readDiscussCrop(f);
    };
  }
  if (file) file.onchange = () => { if (file.files && file.files[0]) readDiscussCrop(file.files[0]); file.value = ''; };
  for (const button of document.querySelectorAll('#discussMain [data-place]')) {
    button.onclick = () => {
      const place = button.dataset.place;
      D.cropPlacement = /^[A-D]$/.test(place) ? 'option' : (place === 'stem' ? 'stem' : place);
      D.cropOption = /^[A-D]$/.test(place) ? place : '';
      for (const other of document.querySelectorAll('#discussMain [data-place]')) {
        other.classList.toggle('on', other === button);
      }
    };
  }
  const save = $('dCropSave');
  if (save) save.onclick = () => saveDiscussCrop(key);
  restoreDiscussCropPreview();
}

/* 讀一張圖成 data URL。`FileReader` 是唯一一條瀏覽器給的路，不經過伺服器——圖還沒決定要不要留。 */
function readDiscussCrop(file) {
  if (!file || !String(file.type || '').startsWith('image/')) {
    toast('那不是圖片檔', true);
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    D.pendingCrop = String(reader.result || '');
    restoreDiscussCropPreview();
    const status = $('dCropStatus');
    if (status) status.textContent = `已讀到圖（${Math.round(D.pendingCrop.length / 1365)} KB），確認位置後按「儲存補圖」。`;
  };
  reader.onerror = () => toast('讀圖失敗', true);
  reader.readAsDataURL(file);
}

function restoreDiscussCropPreview() {
  const preview = $('dCropPreview');
  if (!preview) return;
  if (D.pendingCrop) { preview.src = D.pendingCrop; preview.style.display = 'block'; }
  else { preview.removeAttribute('src'); preview.style.display = 'none'; }
}

/* 貼上的事件掛在 document 上，因為焦點可能在區塊外的任何地方（PDF iframe 拿不到鍵盤）。
   只認這一區、且焦點不在輸入框的貼上——否則在註解框貼一段文字會變成補圖。 */
document.addEventListener('paste', (event) => {
  if (A.area !== 'discuss') return;
  const target = event.target;
  if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')) return;
  const items = Array.from((event.clipboardData || {}).items || []);
  const image = items.find((item) => String(item.type || '').startsWith('image/'));
  if (!image) return;
  event.preventDefault();
  readDiscussCrop(image.getAsFile());
});

/* 儲存補圖。走既有的 `/api/manual-asset`，事件是 append-only 的 correction。 */
async function saveDiscussCrop(key) {
  if (!key) return;
  const status = $('dCropStatus');
  if (!D.pendingCrop) {
    if (status) status.textContent = '請先貼上或選擇一張圖。';
    toast('還沒有圖', true);
    return;
  }
  if (D.cropPlacement === 'option' && !D.cropOption) {
    if (status) status.textContent = '請先選擇要補到哪一個選項。';
    return;
  }
  const payload = {
    candidate_key: key,
    data_url: D.pendingCrop,
    reviewer: 'local',
    notes: ($('dCropNotes') || {}).value || '',
    caption: ($('dCropCaption') || {}).value || '',
    placement: D.cropPlacement,
    target_option: D.cropOption,
    replace_existing: !!($('dCropReplace') || {}).checked,
  };
  if (status) status.textContent = '補圖寫入中…';
  try {
    const response = await fetch('/api/manual-asset', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    D.pendingCrop = '';
    D.seen.set(key, true);
    invalidateAreas();
    toast('補圖已寫入（append-only 的 correction）');
    renderDiscuss(true);
  } catch (error) {
    if (status) status.textContent = `補圖失敗：${error.message || error}`;
    toast(`補圖失敗：${error.message || error}`, true);
  }
}

/* ⑥ 註解。與題目區的「只加註記 C」寫的是同一種 `comment` 事件，所以兩邊的註解會互相看得到
   （兩邊都讀同一份 append-only 事件流投影出來的 `review.notes`，不是各自另存一份）。 */
async function saveDiscussNote(key) {
  if (!key) return;
  const box = $('dNote');
  const notes = box ? box.value.trim() : '';
  if (!notes) { toast('沒有寫任何註解', true); return; }
  try {
    const response = await fetch('/api/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate_key: key, action: 'comment', notes, reviewer: 'local',
        source: 'linear_v2_discuss',
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    D.noteDraft.delete(key);
    toast('註解已存（append-only；底下已有的決定不變）');
    renderDiscuss(true);
  } catch (error) {
    toast(`註解儲存失敗：${error.message || error}`, true);
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
