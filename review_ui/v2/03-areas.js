/* 五區表、`A` 快取、`fetchAreaJson`、`renderArea`；首頁（counts 只讀伺服器數）。
   答案與原則兩區的實作在 `03-area-answer.js` 與 `03-area-principles.js`（載入序＝檔名序，兩者都在本檔之前）。 */
const AREA_BY_NAME = { home: 'home', question: 'question', answer: 'answer', discuss: 'discuss',
                       principles: 'principles' };
//: The hash prefix for each area. `審題` is the question area's own name and is accepted too, so a
//: link written either way works.
const AREA_PREFIX = { home: '首頁', question: '審題', answer: '答案', discuss: '錯題', principles: '原則' };
const PREFIX_AREA = { 首頁: 'home', 審題: 'question', 題目: 'question', 答案: 'answer', 錯題: 'discuss',
                      原則: 'principles' };
const AREA_LABEL = { home: '首頁', question: '題目審核區', answer: '答案審核區', discuss: '錯題討論區',
                     principles: '原則區' };

//: One fetch of each area's payload per session, invalidated when a decision could have changed it.
//: `questionStale` says the question area's own copied state (`S.verdict`/`S.notes`) was seeded before
//: a write that happened in another area, so returning to it has to re-read the scope's rows.
const A = { area: 'question', sheets: null, sheetIndex: 0, sheet: null, answerDraft: new Map(),
            noteDraft: new Map(), feedback: null, error: {}, rendered: {}, questionStale: false };

function areaFromHash() {
  const raw = decodeURIComponent((location.hash || '').replace(/^#/, ''));
  if (!raw) return 'question';  // the baseline is the question area; an empty hash is not the home page
  const head = raw.split('/')[0];
  const known = PREFIX_AREA[head];
  if (known) return known;
  // 一個**看起來像模式**但沒對上的前綴，比一個空的 hash 更危險：整個 hash 被當成範圍，
  // 靜默開在題目區，而 `boot()` 接著會把它改寫成那一個範圍——於是「打錯字」表現成
  // 「一切正常但你看的不是你要的那一區」（2026-09-24 我自己用 `#principles` 撞上，
  // 花了一輪才看出來；應用程式寫的是 `#原則`）。
  //
  // 判準故意很窄：只有**純英數**的前綴才可能是模式名的誤寫——類科名是中文
  // （`#藥師(一)/115/2/…`），所以這條規則碰不到任何合法的範圍寫法。不新增英文別名：
  // 同一件事兩種拼法就是兩個會不一致的地方，而英文拼法從來沒有發布過，沒有書籤在依賴它。
  if (/^[A-Za-z][A-Za-z0-9_-]*$/.test(head) && !S.scope.category) {
    toast(`#${head} 不是一個區；模式前綴是 ${Object.values(AREA_PREFIX).join('、')}。`, true);
  }
  return 'question';
}

function showArea(area, { push = true } = {}) {
  const next = AREA_BY_NAME[area] ? area : 'question';
  A.area = next;
  for (const button of document.querySelectorAll('.area-btn')) {
    button.classList.toggle('on', button.dataset.area === next);
  }
  // A mode that is not chosen is **hidden**, never emptied: the answer area keeps the sheet the
  // reviewer was reading and the note they were typing, and a trip through 首頁 must not discard it.
  const nodes = { home: 'areaHome', question: 'areaQuestion', answer: 'areaAnswer', discuss: 'areaDiscuss',
                  principles: 'areaPrinciples' };
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
  // 回到題目區時，如果別的區寫過東西，就重讀這個範圍的列（游標留在同一題）。
  // 「別的區寫過」是 `invalidateAreas()` 立的旗標——沒有它，討論區剛加的註解在題目區看不到。
  if (next === 'question' && A.questionStale) {
    A.questionStale = false;
    refreshScopeRows().catch(() => {});
  }
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
  else if (area === 'principles') renderPrinciples();
  else if (area === 'home') renderHome();
}

//: Called by anything that writes. Every other area's eligible set is derived from decisions taken
//: somewhere else - a question decision changes what the answer area may review and what the
//: discussion area lists; a principle or an answer to a counter-question changes what the next round
//: of the agent reads - so **a write invalidates all of them**. This is the single invalidation point
//: (charter: 導覽與內容必須來自同一個來源); a write that does not call it leaves one pane showing the
//: state before the write, which is exactly the 「前面說我沒做、後面說我做了」 the reviewer reported.
function invalidateAreas() {
  A.rendered = {};
  A.sheets = null;
  A.feedback = null;
  // 寫入發生在**別的區**時，題目區手上的 `S.verdict`／`S.notes` 已經是寫入前的值——它不會重讀，
  // 因為它的 DOM 是保留的。所以立一個旗標，`showArea()` 回到題目區時用它決定要不要重讀
  // （`refreshScopeRows()`：重讀列，但游標留在同一題）。
  if (A.area !== 'question') A.questionStale = true;
}

/* 寫入之後**唯一**的收尾：作廢所有區的快取，然後把你正在看的那一區重畫一次。

   以前每一區自己寫「`invalidateAreas()` ＋ `await renderXxx()`」——同一個動作四份實作，漏掉任何一份，
   那一區就會停在寫入前的畫面（使用者回報的「各說各話」）。現在只有這一個入口。

   重畫是**無損**的，因為草稿不在 DOM 裡：答案區的字母與註記在 `A.answerDraft`／`A.noteDraft`，
   題目區的註解在 `S.notes`，所以 `innerHTML` 重來一次不會把正在打的字弄丟（這一條有瀏覽器測試
   `scripts/test_v2_areas_browser.mjs` 釘住：半打的註記要能穿過一次區間切換）。 */
async function afterWrite() {
  const area = A.area;
  invalidateAreas();
  await renderArea(area, { force: true });
}

/* --------------------------------------------------------------- 首頁
   The home page answers one question - "what is left" - from three endpoints that already exist.
   It computes nothing of its own: every number here is the server's count of the same thing the
   area it links to will show, because a dashboard that disagrees with the page behind it is worse
   than no dashboard. */
async function renderHome() {
  const cards = $('homeCards');
  cards.innerHTML = '<div class="empty">載入中…</div>';
  const [questions, answers, feedback, discuss, machine] = await Promise.all([
    fetchAreaJson('/api/candidates', { _count: 1 }),
    fetchAreaJson('/api/answer-candidates', { limit: 1 }),
    fetchAreaJson('/api/correction-feedback', { limit: 500 }),
    fetchAreaJson('/api/discuss', { limit: 1 }),
    fetchAreaJson('/api/machine-activity'),
  ]);
  const total = questions ? Number(questions.total_count ?? questions.filtered_count ?? 0) : null;
  const answered = questions ? Number(questions.reviewed_count ?? 0) : null;
  const eligible = answers ? Number(answers.eligible_count ?? 0) : null;
  const answerReviewed = answers ? Number(answers.reviewed_count ?? 0) : null;
  const cases = feedback && Array.isArray(feedback.events) ? feedback.events.length : null;
  // 原則區的數字與那一頁自己讀的是**同一個投影**（同一個 `/api/discuss` 回應裡的兩個區塊），
  // 不是另外算的——首頁與它指到的那一頁不一致比沒有首頁更糟。
  const principleCount = discuss && discuss.principles ? Number(discuss.principles.count ?? 0) : null;
  const openQuestions = discuss && discuss.repair_questions
    ? Number(discuss.repair_questions.open_count ?? 0) : null;
  // 機器改過字的題目：每一格是**一種**機器動作，所以「有沒有改過、是怎麼改的」在首頁就看得出來。
  // 數字來自 `/api/machine-activity`（伺服器在同一份事件流上算的），這一頁一個數字都不自己算；
  // **字也只在一份**（`APPLIED_LABEL`，02-area-question.js）：首頁與每一列的說法從此不可能不一致
  // ——同一個東西寫兩次，就是兩個可以不一致的地方。
  const activityHtml = machine
    ? '<div class="mach"><span class="mach-hint">機器改過字</span>'
      // `entry` **就是**那個字（`APPLIED_LABEL` 的值），不是一個帶 `label` 的物件。寫成
      // `entry.label` 的下場是首頁四個數字的名字全部變成 `undefined`——而數字是對的，所以
      // 它看起來只像「標籤怪怪的」。抓到它的是
      // `scripts/test_v2_principles_browser.mjs`（2026-09-24：`["undefined 1","undefined 1",…]`）。
      + Object.entries(APPLIED_LABEL)
        .map(([kind, label]) => `<b>${label} ${Number(machine[kind] ?? 0)}</b>`).join('')
      + `<i>退回 ${Number(machine.total ?? 0)} 題；其餘退回沒有動到字</i></div>`
    : '';
  const card = (title, big, note, area, action, extra = '') => `
    <div class="card" data-go="${area}">
      <h2>${esc(title)}</h2>
      <span class="big">${big === null ? '—' : big}</span>
      <p>${esc(note)}</p>
      ${extra}
      <span class="go">${esc(action)} →</span>
    </div>`;
  cards.innerHTML = [
    card('題目審核區', total === null ? null : `${answered ?? 0}／${total}`,
         '一題一題讀紙本對照抽取文字。還沒過目的題目會擋住它的答案。', 'question', '開始審題'),
    card('答案審核區', eligible === null ? null : `${answerReviewed ?? 0}／${eligible}`,
         '只審已通過題目的答案卡。沒通過的題目不能審答案，伺服器會直接拒繪。', 'answer', '審答案'),
    card('錯題討論區', cases === null ? null : `${cases}`,
         '人工修過的個案，依「修改的類型」排列：這一區是把修正累積成可學的東西的地方。',
         'discuss', '看個案', activityHtml),
    card('原則區', principleCount === null ? null : `${principleCount} 條・${openQuestions ?? 0} 題待答`,
         '基本原則與修理代理的反問。每一條都叫得出原題紙本，讓你知道那條規則是在講哪一題。',
         'principles', '管原則'),
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
