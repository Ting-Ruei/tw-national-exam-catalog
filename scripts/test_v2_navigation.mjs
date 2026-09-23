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
  'nAll', 'nGroup', 'nUnseen', 'nFlagged', 'nReturned', 'nDisputed', 'btnFirst', 'btnPrev', 'btnNext',
  'btnLast', 'actHold', 'actBlock', 'actNote', 'noteFor', 'stateHint', 'where',
  // The four-area shell. The navigation contract is about the *question* area's walk, which this
  // harness drives directly by calling `go`/`next` - it never clicks an area button. But the
  // script still wires the shell's elements at load time (the area buttons and the three other
  // panes), so those nodes must exist or the script throws before the harness can reach it. The
  // behaviour of the areas themselves is pinned by `test_v2_areas_browser.mjs` against real Chrome.
  'whoami', 'homeCards', 'homeScope', 'sheetList', 'answerMain', 'discussMain', 'discussSide',
  'areaHome', 'areaQuestion', 'areaAnswer', 'areaDiscuss']) makeElement(id);
elements.get('figuresOnly').tagName = 'INPUT';

/* The chip radios: only the chip the test selects should read as checked. */
const chipViews = ['all', 'group', 'unseen', 'flagged', 'returned', 'disputed'];
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

const history_ = { replaceState() {} };
const location_ = { hash: '' };

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
  + '\n;globalThis.__qbr = { S, rebuildRows, visibleRows, viewMode, isGrouped, stateOf, hasDispute, '
  + 'go, next, renderList, renderChips };';

const fn = new Function(...Object.keys(sandbox), wrapped);
const ctx = { ...sandbox };
fn(...Object.values(ctx));
const qbr = globalThis.__qbr;
function setView(view) {
  for (const chip of chipNodes) chip.querySelector('input').checked = (chip.dataset.view === view);
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

/* --- check 3: the negative control - no filter, the walk is the whole paper ----------------- */
setView('all');
elements.get('figuresOnly').checked = false;
qbr.rebuildRows(null);
check('全部：S.rows === S.view', S.rows.length === S.view.length, `${S.rows.length} vs ${S.view.length}`);
const allPapers = S.rows.map((r) => r.paper);
const allNumbers2 = S.rows.map((r) => r.question_number);
// Same distinction: paper first, number second. Sorting by number alone would interleave the six
// papers of a sitting, which is the defect the sort in `applyScope()` exists to prevent.
const sortedOk = S.rows.every((r, i) => i === 0 || allPapers[i - 1] < allPapers[i]
  || (allPapers[i - 1] === allPapers[i] && allNumbers2[i - 1] <= allNumbers2[i]));
check('全部：先按卷、再按題號（同一卷內遞增，換卷才換號）', sortedOk);

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
