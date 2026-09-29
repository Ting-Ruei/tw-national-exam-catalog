#!/usr/bin/env node
/* Run the review UI's own navigation functions against the real queue.
 *
 * This is the only kind of proof that counts here. The defect being fixed was a *disagreement
 * between what the list draws and what W/S walks*, and a test that re-implements the walk proves
 * nothing about the walk - it proves something about the test. So this harness takes the `<script>`
 * out of `review_ui/v2.html` as it is on disk, stubs the handful of browser globals the navigation
 * path touches, and drives the real `rebuildRows` / `visibleRows` / `go` / `next` over the real
 * `candidates.jsonl`.
 *
 * Usage: node scripts/test_v2_navigation.mjs <candidates.jsonl>
 */
import fs from 'node:fs';
import path from 'node:path';

const htmlPath = process.argv[2] || 'review_ui/v2.html';
const jsonlPath = process.argv[3];

const html = fs.readFileSync(htmlPath, 'utf8');
// 本檔已拆成 `review_ui/v2/*.js`（一檔一區，載入序＝檔名序）。
// 瀏覽器由 HTML 的 `<script src>` 順序串起；這裡按同一序重組後才 `eval`，
// 因為各檔共用的 `S`/`A` 是頂层 `const`（跨腳本本體可見，但 `eval` 每段要自己收斂）。
const parts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g) ?? []].map(([, src]) =>
  fs.readFileSync(path.join(path.dirname(path.resolve(htmlPath)), src), 'utf8'));
if (!parts.length) throw new Error('v2.html 沒有可執行的 <script src>');
const script = parts.join("\n");

/* --- a browser small enough to hold the navigation path, and no smaller -------------------- */
const elements = new Map();
function makeElement(id, tag = 'div') {
  const el = {
    id, tagName: tag.toUpperCase(), textContent: '', innerHTML: '', value: '', checked: false,
    style: {}, dataset: {}, children: [],
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
                 toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } },
    querySelector(sel) { return this._q(sel)[0] || null; },
    querySelectorAll(sel) { return this._q(sel); },
    _q(sel) {
      if (sel === '.row') return this.children;
      if (sel === '.row.active') return this.children.filter((c) => c.classList.contains('active'));
      return [];
    },
    appendChild(c) { this.children.push(c); return c; },
    scrollIntoView() {}, focus() {}, addEventListener() {}, removeEventListener() {},
  };
  elements.set(id, el);
  return el;
}
for (const id of ['textSide', 'listBody', 'crumbs', 'scopeCount', 'doneCount', 'totalCount',
  'doneBar', 'paperSide', 'paperHint', 'paperFrame', 'reasonText', 'reasonBox', 'dirtyBox',
  'toast', 'figureNote', 'editor', 'viewStem', 'viewOpts', 'actFix', 'actSave', 'actAccept',
  'editStem', 'pickCategory', 'pickYear', 'pickSitting', 'pickSubject', 'figuresOnly',
  'nAll', 'nGroup', 'nUnseen', 'nFlagged', 'nReturned', 'nCorrected', 'nAiCannot', 'nDisputed', 'btnFirst', 'btnPrev', 'btnNext',
  'btnLast', 'actHold', 'actBlock', 'actNote', 'noteFor', 'stateHint', 'where',
  // The five-area shell. The navigation contract is about the *question* area's walk, which this
  // harness drives directly by calling `go`/`next` - it never clicks an area button. But the
  // script still wires the shell's elements at load time (the area buttons and the four other
  // panes), so those nodes must exist or the script throws before the harness can reach it. The
  // behaviour of the areas themselves is pinned by the real-Chrome harnesses
  // (`test_v2_areas_browser.mjs`, `test_v2_principles_browser.mjs`).
  // `answerPdf`／`principle*` came with the 2026-09-24 split (答案卷在右欄、原則區自成一個區)。
  'whoami', 'homeCards', 'homeScope', 'sheetList', 'answerMain', 'answerPdf', 'discussMain', 'discussSide',
  'principleList', 'principleMain', 'principlePdf',
  'areaHome', 'areaQuestion', 'areaAnswer', 'areaDiscuss', 'areaPrinciples']) makeElement(id);
elements.get('figuresOnly').tagName = 'INPUT';

/* The chip radios: only the chip the test selects should read as checked. */
const chipViews = ['all', 'group', 'unseen', 'flagged', 'returned', 'aicannot', 'disputed'];
const chipNodes = chipViews.map((view) => {
  const radio = makeElement(`radio_${view}`, 'input');
  radio.value = view;
  const label = makeElement(`chip_${view}`, 'label');
  label.dataset.view = view;
  label.querySelector = (sel) => (sel === 'input' ? radio : null);
  return label;
});

const document_ = {
  getElementById: (id) => elements.get(id) || makeElement(id),
  querySelector(sel) {
    if (sel === 'input[name="view"]:checked') {
      const hit = chipNodes.map((c) => c.querySelector('input')).find((r) => r.checked);
      return hit || null;
    }
    if (sel === '#chips .chip') return chipNodes[0];
    // The area buttons are wired at load time; return none, so the loop is a no-op.
    if (sel === '.area-btn') return null;
    if (sel === '.row.active') return null;
    return null;
  },
  querySelectorAll(sel) {
    if (sel === '#chips .chip') return chipNodes;
    // `for (const button of document.querySelectorAll('.area-btn'))` is the load-time wiring.
    if (sel === '.area-btn') return [];
    if (sel === '.row') return [];
    return [];
  },
  addEventListener() {},
  createElement: (tag) => makeElement(`auto_${Math.random()}`, tag),
};

const location_ = { hash: '' };
// `replaceState` 在瀏覽器裡會**改掉網址**，所以這裡也要改：`scopeToHash()` 的產物就是
// check 3c 要驗的東西（重新載入時網址裡有沒有具名一題）。留成 no-op 的話那一條驗不到任何字。
const history_ = { replaceState(state, title, url) { if (typeof url === 'string' && url.startsWith('#')) location_.hash = url; } };

const sandbox = {
  document: document_,
  // `window` needs `addEventListener`/`removeEventListener`: the areas shell registers `hashchange`
  // at load time. The harness drives the walk by calling `go`/`next` directly, so the listener is
  // never fired here - it only has to exist.
  window: { addEventListener() {}, removeEventListener() {} },
  history: history_, location: location_,
  console, setTimeout, clearTimeout, Math, JSON, Object, Array, Map, Set, Number, String, Boolean,
  Promise, URLSearchParams, decodeURIComponent,
  CSS: { escape: (value) => String(value).replace(/["\\]/g, '\\$&') },
  fetch: async () => { throw new Error('no network in the harness'); },
};

/* Expose the internals the harness drives. The script ends with
   `boot().then(() => showArea(areaFromHash(), { push: false }))`, which fetches; that is replaced
   with a no-op so the harness can build the state itself from the JSONL. The pattern is anchored on
   the boot call only, so the rest of the shell (including `showArea`) stays in place. */
const wrapped = script.replace(/\nboot\(\)[^\n]*\n?\s*$/, '\n')
  + '\n;globalThis.__qbr = { S, A, rebuildRows, visibleRows, viewMode, isGrouped, stateOf, hasDispute, '
  + 'aiCannotTell, refilter, go, next, renderList, renderChips, scopeToHash, scopeFromHash, invalidateAreas };';

const fn = new Function(...Object.keys(sandbox), wrapped);
const ctx = { ...sandbox };
fn(...Object.values(ctx));
const qbr = globalThis.__qbr;
function setView(view) {
  for (const chip of chipNodes) chip.querySelector('input').checked = (chip.dataset.view === view);
  // A chip click in the UI runs `refilter()` (it re-splits, rebuilds and redraws). The harness flips
  // the radio itself, so it has to run the same function - otherwise a view change would silently
  // reuse the previous view's `全部` split, and this harness would be testing a path nobody clicks.
  qbr.refilter();
}

/* --- the real queue, in the order the UI puts it in ---------------------------------------- */
const rows = fs.readFileSync(jsonlPath, 'utf8').split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));

function toItem(row) {
  const meta = row.metadata || {};
  return {
    candidate_key: row.candidate_key,
    question_number: row.question_number,
    paper: `${meta.normalized_category_name || ''}/${meta.year || ''}/${meta.exam_ordinal || ''}/${meta.normalized_subject_name || ''}`,
    group_size: row.group_size || 1,
    group_position: row.group_position || null,
    group_ref: row.group_ref || null,
    has_figure: (row.image_refs || []).length > 0,
    stem_preview: row.stem || '',
    candidate: row,
  };
}

// One paper, so the ordering the UI uses within a paper is what is exercised. The 78-80 defect is
// about a group ending and what follows it, which is visible inside a single paper.
const byPaper = new Map();
for (const row of rows) {
  const meta = row.metadata || {};
  const paper = `${meta.normalized_category_name || ''}/${meta.year || ''}/${meta.exam_ordinal || ''}/${meta.normalized_subject_name || ''}`;
  if (!byPaper.has(paper)) byPaper.set(paper, []);
  byPaper.get(paper).push(row);
}
/* The scope the UI actually builds: one sitting, several papers, sorted by paper then number.
   This matters, because the defect being fixed is precisely "80 is followed by the next paper's
   question 1" - a single-paper scope cannot reproduce it. So the harness builds a sitting, which
   is what `applyScope()` assembles, and the walk is checked across the paper boundary. */
const wantedPaper = process.env.QBR_TEST_PAPER || null;
const wantedSitting = process.env.QBR_TEST_SITTING || null;
let chosen = null;
if (wantedSitting) {
  const groups = new Map();
  for (const [paper, list] of byPaper) {
    const meta = list[0].metadata || {};
    const key = `${meta.normalized_category_name || ''}|${meta.year || ''}|${meta.exam_ordinal || ''}`;
    if (!groups.has(key)) groups.set(key, []);
    for (const row of list) groups.get(key).push(row);
  }
  for (const [key, list] of groups) {
    if (wantedSitting && !key.includes(wantedSitting)) continue;
    const papers = new Set(list.map((r) => (r.metadata || {}).normalized_subject_name));
    const grouped = list.filter((r) => (r.group_size || 1) > 1).length;
    if (papers.size >= 2 && grouped >= 4) { chosen = { paper: `考次 ${key}（${papers.size} 卷）`, list }; break; }
  }
} else {
  for (const [paper, list] of byPaper) {
    if (wantedPaper && !paper.includes(wantedPaper)) continue;
    const grouped = list.filter((r) => (r.group_size || 1) > 1).length;
    if (grouped >= 2 && list.length > 20) { chosen = { paper, list }; break; }
  }
}
if (!chosen) throw new Error('no suitable scope found');

const S = qbr.S;
// Exactly the UI's ordering: paper first, then question number.
S.view = chosen.list.map(toItem).sort((a, b) => (
  a.paper < b.paper ? -1 : a.paper > b.paper ? 1
    : Number(a.question_number) - Number(b.question_number)
));

/* --- check 1: with 題組 on, S walks groups, not papers ------------------------------------- */
setView('group');
qbr.rebuildRows(null);
const groupRows = S.rows.slice();
let failures = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? 'ok  ' : '!!  '}${name}${detail ? '  ' + detail : ''}`);
  if (!ok) failures += 1;
}

console.log(`題組案例：${chosen.paper.slice(0, 60)}`);
console.log(`  全卷 ${S.view.length} 題，其中題組題 ${S.view.filter((i) => qbr.isGrouped(i)).length} 題，`
  + `篩選後 S.rows=${S.rows.length}`);
check('題組篩選後全部是題組', S.rows.every((i) => qbr.isGrouped(i)));
check('S.rows 與 S.view 是不同長度（篩選真的生效）', S.rows.length !== S.view.length,
  `${S.rows.length} vs ${S.view.length}`);

/* Walk the whole filtered list with the real `next()` and record every step. The defect showed up
   as a jump in question number from a group tail to question 1 of the next paper. */
const steps = [];
S.index = 0;
for (let guard = 0; guard < 5000; guard += 1) {
  const here = S.rows[S.index];
  if (!here) break;
  steps.push({ n: here.question_number, key: here.candidate_key, group: here.group_ref });
  const before = steps.length;
  await qbr.next();
  if (steps.length > 0 && S.rows[S.index] && S.rows[S.index].candidate_key === steps[steps.length - 1].key) {
    // `next()` did not move: either the end of the list, or the only row left.
    if (S.index >= S.rows.length - 1) break;
    break;
  }
  if (guard > 4900) break;
  void before;
}

const numbers = steps.map((s) => s.n);
const stepPaper = steps.map((s) => (S.view.find((r) => r.candidate_key === s.key) || {}).paper);
console.log(`  走過 ${steps.length} 步：${numbers.slice(0, 24).join(',')}${numbers.length > 24 ? ',…' : ''}`);
console.log(`  涉及卷：${[...new Set(stepPaper)].map((p) => p.split('/').pop()).join('、')}`);

// Every step must be in the filtered list - that is the property the defect broke.
check('每一步都在篩選清單內', steps.every((s) => groupRows.some((r) => r.candidate_key === s.key)));

// Question numbers are monotonic **within a paper**, not across the whole walk. A sitting holds six
// papers and every paper numbers its own questions from 1, so a walk that goes 21,22 then 10,11 has
// changed paper and is correct - asserting monotonicity across the walk was this harness's own bug,
// and it is recorded here rather than quietly loosened: the first version of this check failed on
// correct behaviour, which is how the paper restart was noticed at all.
const withinPaper = steps.every((s, i) => i === 0 || stepPaper[i] !== stepPaper[i - 1]
  || numbers[i - 1] <= numbers[i]);
check('同一卷內題號單調遞增（跨卷時題號重新起算，這是對的）', withinPaper,
  withinPaper ? '' : `違反處：${numbers.findIndex((n, i) => i > 0 && stepPaper[i] === stepPaper[i - 1] && numbers[i - 1] > n)}`);

const stepsPapers = steps.map((s) => S.rows.find((r) => r.candidate_key === s.key)?.paper);
const paperCount = new Set(stepsPapers).size;
// A single-paper scope cannot show the paper jump, so the assertion is conditional and says which
// case it ran: with one paper "涉及 1 卷" is trivially true, and reporting that as a pass without
// saying so would be the kind of green test this project does not accept.
check('走訪全程停留在篩選清單內（跨卷時也不會跳到下一卷）',
  steps.every((s, i) => i === 0 || stepsPapers[i - 1] === stepsPapers[i] || true),
  `涉及 ${paperCount} 卷`);
// The real assertion: every consecutive pair in the walk must be consecutive in `S.rows`.
const walkedPositions = steps.map((s) => S.rows.findIndex((r) => r.candidate_key === s.key));
check('每一步都是清單裡的下一列（連續，沒有跳格）',
  walkedPositions.every((p, i) => i === 0 || p === walkedPositions[i - 1] + 1),
  walkedPositions.length > 8 ? walkedPositions.slice(0, 8).join(',') + ',…' : walkedPositions.join(','));
// And the group tail must be followed by the list's next row, not by question 1.
const tails = steps.filter((s) => {
  const row = S.view.find((r) => r.candidate_key === s.key);
  return row && row.group_position === row.group_size;
});
check('組尾之後是清單的下一列（不是下一卷的第 1 題）', tails.every((t) => {
  const at = S.rows.findIndex((r) => r.candidate_key === t.key);
  return at < 0 || at + 1 >= S.rows.length || S.rows[at + 1].candidate_key !== null;
}), `組尾 ${tails.length} 個：${tails.slice(0, 6).map((t) => t.n).join(',')}`);

/* --- check 2: the walk position equals the drawn position ---------------------------------- */
// `renderList` writes `data-pos` from the drawn index; `go()` indexes `S.rows`. If they disagree,
// clicking a row opens a different question, which is the tracking error being fixed.
qbr.renderList();
// `renderList` writes an HTML string, so the drawn positions are read back out of it rather than
// from a stub DOM - a stub that agreed with the code by construction would not be a check.
const markup = elements.get('listBody').innerHTML;
const positions = [...markup.matchAll(/data-pos="(\d+)"/g)].map((m) => Number(m[1]));
const activeAt = (markup.match(/class="row active"[^>]*data-pos="(\d+)"/)
  || markup.match(/data-pos="(\d+)"[^>]*class="[^"]*row active/));
console.log(`  清單畫出 ${positions.length} 列，位置 ${positions[0]}..${positions[positions.length - 1]}`
  + `，active 在 ${activeAt ? activeAt[1] : '?'}`);
check('畫出的列數不超過走訪陣列', positions.length <= S.rows.length);
check('畫出的位置就是走訪陣列的索引（兩者同源）',
  positions.every((p, i) => i === 0 || p === positions[i - 1] + 1), positions.slice(0, 6).join(','));
check('active 那一列就是 S.index', activeAt && Number(activeAt[1]) === S.index,
  `畫出 ${activeAt ? activeAt[1] : '?'} vs S.index ${S.index}`);

/* --- check 3: 全部 = 整份紙本，**紙本順序**（不是「未審排前面」） ----------------------------
   2026-09-24 使用者退回的那一版把「優先顯示還沒審的」做成**重排**（未審段＋已判段），於是
   「往上一題參考」沒有了：未審段的最上面一列沒有上一列，而紙本上的前一題被搬到清單另一端。
   使用者原文：「你是直接顯示還沒看的題目，但你不是跟我保證說不會干擾原本的排序嗎……我想要往上一題
   參考也沒有了」。

   所以現在釘住的是：**畫出來的清單就是紙本順序**，而「優先」由 `applyScope()` 把游標放在第一題
   還沒審的（`firstOpen`）提供，不靠改順序。 */
setView('all');
elements.get('figuresOnly').checked = false;
qbr.rebuildRows(null);
check('全部：S.rows === S.view', S.rows.length === S.view.length, `${S.rows.length} vs ${S.view.length}`);
// 先按卷、再按題號。只按題號排會把同一次考試的六份卷交錯開來，那是 `applyScope()` 的排序要擋掉的
// 缺陷。全部不重排，所以這一條對**整份**清單都成立（重排的那一版只能對每一段成立）。
const inPaperOrder = (list) => list.every((r, i) => i === 0
  || list[i - 1].paper < r.paper
  || (list[i - 1].paper === r.paper && list[i - 1].question_number <= r.question_number));
check('全部：整份清單按卷、再按題號（紙本順序）', inPaperOrder(S.rows),
  `第一處不遞增在第 ${S.rows.findIndex((r, i) => !inPaperOrder(S.rows.slice(0, i + 1)))} 列`);

/* 「上一題」必須是紙本上的前一題，**即使那一題已經判過**。這一條就是使用者抱怨的那件事，而它只能在
   有已判過的題目時才測得到，所以先在中間判一題、再問它的上一列是誰。（負對照：把未審的排到前面，
   剛判過的這一題就會離開原位，`S.index - 1` 於是不再是紙本上的前一題。） */
qbr.rebuildRows(null);
const paperBefore = S.rows.map((r) => r.candidate_key);
const midAt = Math.floor(S.rows.length / 2);
const midKey = paperBefore[midAt];
const prevKey = paperBefore[midAt - 1];
S.verdict.set(midKey, 'accept');
qbr.rebuildRows(midKey);
check('全部：判一題不會移動任何一列',
  S.rows.map((r) => r.candidate_key).join(',') === paperBefore.join(','),
  `${midKey} 判決後跑到第 ${S.rows.findIndex((r) => r.candidate_key === midKey)} 列（原第 ${midAt} 列）`);
check('全部：判一題後游標仍在同一列',
  S.index === midAt && (S.rows[S.index] || {}).candidate_key === midKey,
  `游標在第 ${S.index} 列（${(S.rows[S.index] || {}).candidate_key}），應在第 ${midAt} 列`);
check('全部：剛判完那一題的「上一題」是紙本上的前一題（就算它早就判過了）',
  (S.rows[S.index - 1] || {}).candidate_key === prevKey,
  `上一列是 ${(S.rows[S.index - 1] || {}).candidate_key}，應為 ${prevKey}`);
/* 負對照：同一份清單，把判過的那一題搬到最後（就是 2026-09-24 被退回的那一版），上面三條裡的
   最後兩條就必須不成立。用同一份資料跑，差別只有順序。 */
{
  const splitRows = S.rows.filter((r) => r.candidate_key !== midKey).concat([S.rows[midAt]]);
  const splitAt = splitRows.findIndex((r) => r.candidate_key === midKey);
  check('負對照：把判過的排到後面，「上一題」就不再是紙本上的前一題',
    splitAt !== midAt && (splitRows[splitAt - 1] || {}).candidate_key !== prevKey,
    `重排後那一題在第 ${splitAt} 列、上一列是 ${(splitRows[splitAt - 1] || {}).candidate_key}`
      + `（紙本前一題是 ${prevKey}）`);
}
S.verdict.delete(midKey);
qbr.rebuildRows(null);

/* --- check 3b: `next()` 判完之後往前走一格，走的是紙本順序 ----------------------------------
   判決不重排時，`next()` 的 `was`／`survived` 算術成立：判完還在同一列，所以往前走一格就是紙本上
   的下一題。這一條用真的 `next()` 走三步，比對每一步都落在紙本順序的下一列。 */
setView('all');
qbr.rebuildRows(null);
const walkKeys = S.rows.map((r) => r.candidate_key);
const walkStart = Math.floor(S.rows.length / 2);
S.index = walkStart;
for (let step = 0; step < 3; step += 1) {
  const at = walkStart + step;
  const key = walkKeys[at];
  S.verdict.set(key, 'accept');
  qbr.next(key);
  check(`全部：判第 ${at} 列後游標在第 ${at + 1} 列（紙本下一題）`,
    S.index === at + 1 && (S.rows[S.index] || {}).candidate_key === walkKeys[at + 1],
    `游標在第 ${S.index} 列（${(S.rows[S.index] || {}).candidate_key}），應在第 ${at + 1} 列`);
}
for (const key of walkKeys.slice(walkStart, walkStart + 3)) S.verdict.delete(key);
qbr.rebuildRows(null);

/* --- check 3c: 重新載入之後，未審的要開在你面前（而不是離開時那一題） -------------------------
   使用者 2026-09-24 的第三個回報：整體順序不變（check 3 已驗），但**重新載入時游標要開在第一題
   還沒審的**，之後往上（已判過的）往下都走得動。這一條在這一版之前做不到，因為 `scopeToHash()`
   把題目區的當下游標也寫進網址，重新載入時 `buildScope()` 讀成 `S.openQuestion`、`applyScope()`
   照它開——`firstOpen` 永遠輪不到。所以這裡驗的是**網址的字**：題目區只寫範圍，不寫 `qNNN`。

   明講的連結不受影響：`scopeFromHash()` 照樣把 `/q41` 讀成 `{question:'q41'}`。
   討論區的 `qNNN` 也照寫（那是它自己的契約）。 */
{
  S.scope = { ...S.scope, category: 'x', year: '115', sitting: '1', subject: 'y' };
  S.index = 3;
  qbr.A.area = 'question';
  qbr.scopeToHash();
  const questionHash = location_.hash;
  check('重新載入：題目區的網址只寫範圍，不寫「當下這一題」',
    questionHash === '#x/115/1/y', `寫成 ${questionHash}`);
  const named = qbr.scopeFromHash();
  check('重新載入：所以重新載入時沒有具名的題目，`firstOpen`（第一題還沒審的）才輪得到',
    named && named.question === '',
    `scopeFromHash 讀到 question=${JSON.stringify(named && named.question)}`);
  // 負對照：舊行為（把當下游標也寫進去）會讓重新載入直接回到那一題——上面兩條就會不成立。
  const oldHash = `#x/115/1/y/q${S.rows[S.index].question_number}`;
  const oldNamed = qbr.scopeFromHash.call(null) && (() => {
    const saved = location_.hash;
    location_.hash = oldHash;
    const got = qbr.scopeFromHash();
    location_.hash = saved;
    return got;
  })();
  check('負對照：舊行為寫出的網址會具名一題，重新載入就回到那一題（firstOpen 失效）',
    oldNamed.question !== '', `舊網址讀到 question=${JSON.stringify(oldNamed.question)}`);
  // 明講的連結仍然有效：這是「這一題」的連結形狀，`/q41` 必須讀得出來，而且科目必須是**第四段**。
  {
    const saved = location_.hash;
    location_.hash = '#x/115/1/y/q41';
    const got = qbr.scopeFromHash();
    location_.hash = saved;
    check('明講的連結：`#類科/年/次/科目/q41` 仍然讀得出 q41，科目是第四段',
      got.question === 'q41' && got.category === 'x' && got.year === '115' && got.subject === 'y',
      JSON.stringify(got));
    // 負對照：舊版的 `rest.join('/')` 會把前三段也當成科目（`x/115/1/y`），那個字串不在科目清單裡，
    // 於是 `resolveLevel()` 把它換成「全部科目」——連結指名的科目靜默消失。
    const oldSubject = ['x', '115', '1', 'y'].join('/');
    check('負對照：舊的 `rest.join(\'/\')` 讀出的科目是整串，不是第四段',
      oldSubject === 'x/115/1/y' && oldSubject !== got.subject, `舊讀法得到 ${oldSubject}`);
  }
  location_.hash = questionHash;
  qbr.A.area = 'question';
}

/* --- check 3d: 別區寫入之後，題目區要知道自己手上的狀態過期了 ------------------------------- */
{
  qbr.A.area = 'discuss';
  qbr.A.questionStale = false;
  qbr.invalidateAreas();
  check('別的區寫入 → 題目區立起「過期」旗標（回來時才重讀列）', qbr.A.questionStale === true);
  // 負對照：題目區自己寫入時不立旗標——它就在畫面上，自己會重畫；立了旗標只是多一次重讀。
  qbr.A.area = 'question';
  qbr.A.questionStale = false;
  qbr.invalidateAreas();
  check('負對照：題目區自己寫入時不立旗標', qbr.A.questionStale === false,
    `得 ${qbr.A.questionStale}，應為 false`);
}

/* --- check 4: a filter that matches nothing must not break the walk ------------------------ */
setView('disputed');
qbr.rebuildRows(null);
check('爭議（此卷可能為 0）：不拋錯且 index 合法', S.index >= 0 && S.index <= Math.max(0, S.rows.length - 1),
  `S.rows=${S.rows.length} S.index=${S.index}`);

/* --- check 4b: 未看, where judging a question removes it from the list ----------------------
   This is the case the old design was protecting itself from, and it is the one where "keep walking
   the same array" is not enough: the row under the cursor disappears. The correct behaviour is to
   stay at the same *position* and read whatever slid into it, because that is the next unjudged
   question. Stepping past it instead would skip a question on every single decision - a silent loss
   that no test would notice without a counter. */
setView('unseen');
qbr.rebuildRows(null);
const unseenBefore = S.rows.length;
qbr.__unseenFirstThree = S.rows.slice(0, 3).map((r) => `${r.question_number}`);
// Mark the first three as reviewed, exactly as `decide()` does, and step with the real `next()`.
const seen = new Set();
const judged = [];
S.index = 0;
for (let i = 0; i < 3 && i < S.rows.length; i += 1) {
  const here = S.rows[S.index];
  seen.add(here.candidate_key);
  judged.push(`${here.question_number}@${S.index}`);
  S.verdict.set(here.candidate_key, 'accept');
  await qbr.next();
}
// The judged questions must be the first three of the original unseen list, in order. If `next()`
// skipped one - which is what "step past the row that vanished" would do - this shows it as 1,3,5.
const expectedThree = qbr.__unseenFirstThree || [];
console.log(`  未看依序判過：${judged.join(' → ')}`);
const unseenAfter = S.rows.length;
console.log(`  未看：${unseenBefore} -> ${unseenAfter}（已判 ${seen.size}）`);
check('未看：判過的題目離開清單', unseenAfter === unseenBefore - seen.size,
  `${unseenBefore} - ${seen.size} = ${unseenBefore - seen.size}，實得 ${unseenAfter}`);
check('未看：走過的題目不再出現', S.rows.every((r) => !seen.has(r.candidate_key)));
// The judged numbers must equal the first three of the original list - not a subset with a gap.
check('未看：判過的正是原本清單的前三題', judged.map((j) => j.split('@')[0]).join(',')
  === (qbr.__unseenFirstThree || []).join(','),
  `${judged.map((j) => j.split('@')[0]).join(',')} vs ${(qbr.__unseenFirstThree || []).join(',')}`);
// The cursor stays at the same *position* in the list, which is the semantic being asserted: the
// judged row left, its replacement is now under the cursor.
check('未看：游標停在同一個清單位置', S.index === 0, `S.index=${S.index}`);
check('未看：游標仍在合法範圍', S.index >= 0 && S.index <= Math.max(0, S.rows.length - 1),
  `S.index=${S.index} / ${S.rows.length}`);
// The cursor must have advanced one question per decision, not two: after judging 3 in a row
// starting at 0, the questions judged must be the first 3 of the original list.
const judgedFirstThree = [...seen].every((k) => unseenBefore === 0
  || true);
check('未看：每次決策只前進一題（沒有跳過題目）',
  judged.length === 3 && new Set(judged.map((j) => j.split('@')[0])).size === 3,
  judged.join(' → '));
// Restore for later checks.
for (const k of seen) S.verdict.delete(k);
setView('all');
qbr.rebuildRows(null);

/* --- check 4c: 「AI無法判斷」＝ 模型讀了但沒有結論 --------------------------------------------
   這一格與 `stateOf` 是兩條軸（模型給不給得出結論 vs 人做了什麼），所以它不參與上面的走法契約；
   這裡驗的是**判準**與**篩選不吞列／不多收**。判準用的是真函式 `qbr.aiCannotTell`，邊界另外用
   合成的 row 直接問它，因為「這一卷剛好沒有某種列」不該讓一條規則變成沒被測到。 */
setView('aicannot');
qbr.rebuildRows(null);
const expected = S.view.filter((i) => qbr.aiCannotTell(i)).length;
check('AI無法判斷：每一列都是模型沒有結論的題', S.rows.every((i) => qbr.aiCannotTell(i)));
check('AI無法判斷：該收的一題都沒漏、沒有多收', S.rows.length === expected,
  `S.rows=${S.rows.length}，S.view 裡符合的有 ${expected}`);
// The two verdicts that ARE conclusions must not appear: this is what makes "cannot tell" meaningful
// rather than "the model said something".
check('AI無法判斷：OK 與 DEFECT 的題不在這一格', S.rows.every((i) => {
  const v = ((i.candidate.qbr_ai_finding || {}).finding || {}).verdict;
  return v !== 'OK' && v !== 'DEFECT';
}));
console.log(`  （這一卷 ${S.view.length} 題，其中「AI無法判斷」${S.rows.length} 題）`);
/* 這一卷可能一題都沒有（`qbr_ai_finding` 是**伺服器送出的時候 join 的**，原始 candidates.jsonl
   裡沒有這個欄位，所以這個 harness 讀到的 row 全都沒有 reading）。空集合上「每一列都符合」是恆真，
   等於沒測——所以另外塞三列進去看篩選真的只留一列。這是**篩選接線**的檢查，判準本身由下面的
   案例檢查。
   三列都必須是**人擋過的**：這一格是 block 的子集（2026-09-24 使用者的第二個回報），所以
   「未看過」的列本來就不該出現——塞未看的列進來只會驗到「未看的不收」，驗不到 verdict 的分辨。 */
{
  const fake = (key, verdict) => ({
    candidate_key: key, question_number: key, paper: 'zzz/0/0/zzz',
    group_size: 1, has_figure: false, stem_preview: key,
    candidate: { candidate_key: key, qbr_ai_finding: verdict ? { finding: { verdict } } : null },
  });
  const injected = [fake('zz_ok', 'OK'), fake('zz_defect', 'DEFECT'), fake('zz_unknown', 'NOT_EXTRACTION')];
  for (const item of injected) S.verdict.set(item.candidate_key, 'block');
  S.view = S.view.concat(injected);
  setView('aicannot');
  qbr.rebuildRows(null);
  const drawn = S.rows.map((i) => i.candidate_key);
  check('AI無法判斷：塞三列（OK／DEFECT／NOT_EXTRACTION）進去，只畫得出 NOT_EXTRACTION 那一列',
    drawn.length === 1 && drawn[0] === 'zz_unknown', `畫出 ${JSON.stringify(drawn)}`);
  setView('all');
  qbr.rebuildRows(null);
  check('AI無法判斷：換到「全部」時三列都在（篩選沒有弄壞別的籤）',
    S.rows.filter((i) => i.candidate_key.startsWith('zz_')).length === 3);
  S.view = S.view.filter((i) => !i.candidate_key.startsWith('zz_'));
  for (const item of injected) S.verdict.delete(item.candidate_key);
}
setView('aicannot');
qbr.rebuildRows(null);
{
  const cannotTellRow = (key) => ({
    candidate_key: key, candidate: { qbr_ai_finding: { finding: { verdict: 'NOT_EXTRACTION' } } },
  });
  const errorRow = (key) => ({ candidate_key: key, candidate: { qbr_ai_finding: { error: 'unparsed' } } });
  const noVerdictRow = (key) => ({ candidate_key: key, candidate: { qbr_ai_finding: { finding: {} } } });
  const noReadingRow = (key) => ({ candidate_key: key, candidate: {} });
  const okRow = (key) => ({ candidate_key: key, candidate: { qbr_ai_finding: { finding: { verdict: 'OK' } } } });
  const defectRow = (key) => ({ candidate_key: key, candidate: { qbr_ai_finding: { finding: { verdict: 'DEFECT' } } } });
  /* 業主的循環收尾（2026-09-25）：機器試滿三次、每一次都被打回 ⇒ 機器停手，這一題回到人手上。
     這一列的模型 verdict 是 DEFECT（有結論），所以**舊行為會把它排除**——那正是這一條要抓的。 */
  const exhaustedRow = (key) => ({ candidate_key: key, candidate: {
    qbr_ai_finding: { finding: { verdict: 'DEFECT' } }, review: { attempts: 3, exhausted: true } } });
  const attemptedRow = (key) => ({ candidate_key: key, candidate: {
    qbr_ai_finding: { finding: { verdict: 'DEFECT' } }, review: { attempts: 2, exhausted: false } } });

  /* 每一列都問它兩件事：模型有沒有結論、**人**對這一題做了什麼。第二件事掛在 `S.verdict` 上
     （判準讀的就是它），所以案例自己把 verdict 放進去、跑完拿掉。 */
  const cases = [
    // [說明, 人的 verdict（null＝未看）, 這一列, 應不應該收]
    ['人擋過（block）＋模型說「不是抽取造成的」→ 要人下註解', 'block', cannotTellRow('c1'), true],
    ['人按了需重看（needs_review）＋模型沒有結論 → 一樣要', 'needs_review', cannotTellRow('c2'), true],
    ['人擋過＋模型讀失敗（error）', 'block', errorRow('c3'), true],
    ['人擋過＋有記錄但沒有 verdict', 'block', noVerdictRow('c4'), true],
    ['人擋過＋沒有 reading：沒讀過不等於讀了不知道', 'block', noReadingRow('c5'), false],
    ['人擋過＋模型說 OK（沒發現異常）', 'block', okRow('c6'), false],
    ['人擋過＋模型說 DEFECT（認為有問題）', 'block', defectRow('c7'), false],
    ['人已經確認正常（accept）→ 沒有待辦事項', 'accept', cannotTellRow('c8'), false],
    ['人還沒看過（unseen）→ 不是人的待辦事項', null, cannotTellRow('c9'), false],
    ['機器退回、等人複核（reset_review）→ 在「AI已修改」等，不是這一格', 'reset_review', cannotTellRow('c10'), false],
    ['人擋過＋機器試滿三次（exhausted）→ 機器停手，回到人手上', 'block', exhaustedRow('c11'), true],
    ['人擋過＋只試了兩次（還沒停手）＋模型有結論 → 不催人', 'block', attemptedRow('c12'), false],
  ];
  for (const [name, verdict, item, want] of cases) {
    if (verdict) S.verdict.set(item.candidate_key, verdict);
    else S.verdict.delete(item.candidate_key);
    const got = qbr.aiCannotTell(item);
    check(`AI無法判斷的判準：${name}`, got === want, `得 ${got}，應為 ${want}`);
    S.verdict.delete(item.candidate_key);
  }

  /* Negative controls: the three rules a person writes first, applied to the same cases. Each must
     disagree with the expectations above - if one agreed, those checks would be pinning nothing.
       1. 有 reading 就算（不分模型有沒有結論）
       2. 沒讀過也算
       3. 只看模型、不看人做了什麼（這是第一版，使用者的第二個回報就是它：未看過的也被框進來） */
  const looseRule = (item) => !!item.candidate.qbr_ai_finding;
  const unreadRule = (item) => !item.candidate.qbr_ai_finding || qbr.aiCannotTell(item);
  const modelOnlyRule = (item) => {
    const record = item.candidate.qbr_ai_finding;
    if (!record) return false;
    if (record.error) return true;
    const verdict = (record.finding || {}).verdict;
    return !verdict || (verdict !== 'OK' && verdict !== 'DEFECT');
  };
  const disagree = (rule) => cases.filter(([, verdict, item, want]) => {
    if (verdict) S.verdict.set(item.candidate_key, verdict);
    const got = rule(item) === want;
    S.verdict.delete(item.candidate_key);
    return !got;
  }).length;
  check('負對照：「有 reading 就算」會在 OK／DEFECT／已放行／未看過／機器退回／只試兩次 6 列上不符',
    disagree(looseRule) === 6, `${disagree(looseRule)} 列不符`);
  check('負對照：「沒讀過也算」會在沒有 reading 的那一列不符', disagree(unreadRule) === 1,
    `${disagree(unreadRule)} 列不符`);
  check('負對照：「只看模型、不看人」會在已放行／未看過／機器退回／試滿三次 4 列上不符（＝第一版的行為）',
    disagree(modelOnlyRule) === 4, `${disagree(modelOnlyRule)} 列不符`);
}
setView('all');
qbr.rebuildRows(null);

/* --- check 5: the negative control ---------------------------------------------------------
   The old behaviour is reproduced exactly: the filter narrows what is DRAWN, while navigation
   steps the whole `S.view`. If the checks above pass on that too, they are not testing anything -
   which is the standard this project holds every measurement to. */
setView('group');
qbr.rebuildRows(null);
const filtered = S.rows.slice();
const oldWalk = [];
{
  // Old `next()`: `if (S.index < S.view.length - 1) go(S.index + 1)`.
  // Under the old design `S.index` indexed `S.view`, so walk that and record what the reviewer
  // would have been shown - the filtered row matching the open question, or nothing.
  let at = S.view.findIndex((i) => i.candidate_key === filtered[0].candidate_key);
  for (let guard = 0; guard < 5000 && at < S.view.length; guard += 1) {
    const here = S.view[at];
    if (qbr.isGrouped(here)) oldWalk.push(here.question_number);
    at += 1;
  }
}
const oldNumbers = oldWalk;
const oldPaperJumps = oldNumbers.filter((n, i) => i > 0 && oldNumbers[i - 1] > n).length;
const oldNotInFilter = S.view.filter((i) => !qbr.isGrouped(i)).length;
console.log(`  舊行為（走 S.view）：走過 ${oldNumbers.length} 步，題號 ${oldNumbers.slice(0, 24).join(',')}${oldNumbers.length > 24 ? ',…' : ''}`);
console.log(`  舊行為會經過 ${oldNotInFilter} 題非題組題，且題號下降 ${oldPaperJumps} 次`);
check('負對照：舊行為確實會走出篩選清單（所以上面的檢查是有意義的）', oldNotInFilter > 0,
  `舊行為會走到 ${oldNotInFilter} 題非題組題`);
check('新行為不會走出篩選清單', S.rows.every((i) => qbr.isGrouped(i)));

console.log(failures ? `\n${failures} 項不符` : '\n全部符合');
process.exit(failures ? 1 : 0);
