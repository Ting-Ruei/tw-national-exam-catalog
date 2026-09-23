/* 核心：全域狀態、列窗（`S.view`）、分欄導覽、麵包屑、範圍連動。單一律由 `v2.html` 載入。 */
const S = {
  items: [],           // every question in the queue, in paper order
  view: [],            // the questions of the current scope, before the chips narrow them
  rows: [],            // what the list DRAWS and what W/S WALK - always the same array
  viewPos: new Map(),  // candidate_key -> position in `view`, so the nearest row is O(1)
  byKey: new Map(), data: null, index: 0, verdict: new Map(), notes: new Map(),
  editing: false, draft: null, paperPath: null,
  //: Which question the open note box belongs to, or `null` when it is closed.
  //:
  //: The box used to be a bare `<textarea>` that was never cleared, and `decide()` read it
  //: unconditionally. So the note written on one question stayed in the box, and the next decision -
  //: made on a different question with the box shut - was recorded **with the previous question's
  //: note attached to it**. Measured in a browser: a note typed on q001 was written as q002's block
  //: reason. The note is a property of the question it was written on, so the open box remembers
  //: which key it belongs to and `decide()` refuses to attach text the reviewer did not type here.
  noteKey: null,
  // `null` is "not chosen yet" and `''` is "all, chosen deliberately". They are different states
  // and conflating them made every 全部 option unreachable: `refreshScope` saw `''` was not among
  // the values and replaced it with the first one, so choosing 全部年度 snapped back to the newest
  // year on the next redraw.
  scope: { category: null, year: null, sitting: null, subject: null },
  scopeTotal: 0,
  tree: null,
};
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fileUrl = (path) => path ? `/file?path=${encodeURIComponent(path)}` : '';

/* Text that carries the paper's own inline markup, rendered as markup - but only the markup the
   paper is allowed to carry.

   The queue's text keeps the inline markup the extractor read off the page (`α<sub>1</sub>`,
   `e<sup>-0.35t</sup>`, `R<sub>1</sub>`, `<sup>99m</sup>Tc`), and escaping it whole made the reviewer
   read the tags themselves: v2 drew the literal characters `<sub>1</sub>` right beside the PDF that
   prints a subscript. Measured on the served queue: **6,652 rows** carry `<sub>`/`<sup>`
   (`<sub>2</sub>` 2,563, `<sup>99m</sup>` 1,594, …, plus `<sup>®</sup>` from the builder). The
   legacy consoles normalised this and v2 dropped it when it replaced them, so the regression is
   exactly "improve by replacing" done halfway.

   This is a **whitelist, not a pass-through**. Only `sub`, `sup`, `u`, `b`, `i` and a bare `<br>`
   survive; every attribute is discarded because the un-escaping never matches one. Everything else
   that looked like a tag stays escaped. The text comes from an official PDF, but it arrives over
   the network and `innerHTML` is `innerHTML`: a `<img onerror=...>` in a stem would be an
   execution, not a rendering. Negative controls: `tests/test_review_ui_rich_text.py`.

   Unbalanced tags are repaired (opens appended with their closers) because a dropped `</sub>` in
   one option would otherwise shrink every character after it - a silent, whole-question styling
   defect that no reviewer would report as a bug in the text. */
const RICH_TAGS = ['sub', 'sup', 'u', 'b', 'i'];
function richText(value) {
  let out = esc(value)
    .replace(/&lt;(\/?)(sub|sup|u|b|i)&gt;/g, (match, slash, tag) => `<${slash}${tag}>`)
    .replace(/&lt;br\s*\/?&gt;/g, '<br>');
  for (const tag of RICH_TAGS) {
    const opens = (out.match(new RegExp(`<${tag}>`, 'g')) || []).length;
    const closes = (out.match(new RegExp(`</${tag}>`, 'g')) || []).length;
    // An unmatched opener is closed at the end, so a dropped `</sub>` cannot style the rest of the
    // question. An unmatched closer is **put back into the escaped form**, not deleted: it is text
    // the whitelist did not accept, so it must stay text. A `<sub class="big">1</sub>` has its
    // opener refused (attributes are never un-escaped) and its closer refused with it, which is what
    // lets the pair read as literal text instead of half-rendering.
    if (opens > closes) out += `</${tag}>`.repeat(opens - closes);
    else if (closes > opens) {
      let extra = closes - opens;
      out = out.replace(new RegExp(`</${tag}>`, 'g'), (m) => (extra > 0 ? (extra -= 1, `&lt;/${tag}&gt;`) : m));
    }
  }
  return out;
}

function toast(message, bad) {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast' + (bad ? ' bad' : '');
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = 'toast off'; }, bad ? 4200 : 2000);
}

const verdictOf = (key) => S.verdict.get(key) || '';
// `comment` is a note, not a decision, so it is deliberately **not** in the verdict map: it must not
// make a question count as 已過目. Notes are kept beside it, because a note the reviewer cannot see
// again is a note they will write twice - or stop writing.
const noteOf = (key) => S.notes.get(key) || '';
const LABEL = { accept: '確認正常', needs_review: '需重看', block: '阻擋', correct: '已修正', comment: '只加註記', reset_review: 'AI／管線退回' };
//: The one place that says whether an action is a *verdict on* a question or a *note about* it. The
//: server applies the same distinction (a note reaffirms the decision it is attached to rather than
//: replacing it); here it decides whether the question counts as 已過目.
const NOTE_ACTIONS = new Set(['comment']);

/* ------------------------------------------------------------------- list */
/* --------------------------------------------------------------- the scope
   A queue of 21,150 questions across five categories is not reviewable as one list: the reviewer
   reads a paper, and a paper is one subject of one sitting. The three pickers narrow the list to
   the paper being read, and they are a *tree* rather than three independent filters, because the
   facets are not independent - choosing 115 for 醫事檢驗師 and then a 藥師(一) subject would give
   an empty list, and a control that leads nowhere is worse than no control.

   The taxonomy is computed by the queue builder and served from `/api/queue_index`, so the
   browser never has to read 21,150 rows to answer "what subjects are in here". */
/* One candidate row, in the shape the list and the panes read.
   Built in one place because two callers need it - the first load and every scope change - and two
   copies of this would drift. The paper is the unit a reviewer reads, and it is named by its PDF:
   two subjects of one sitting are two papers, and one subject of two sittings is two papers. */
function toItem(candidate) {
  const metadata = candidate.metadata || {};
  return {
    candidate_key: candidate.candidate_key,
    question_number: candidate.question_number,
    stem_preview: candidate.stem,
    paper: paperOf(candidate),
    // Read straight off the row. `normalized_*` is the name the queue builder normalised for
    // matching, and it is present on every row measured (1,000/1,000 across a sitting), so there is
    // nothing to fall back to and no second opinion about what the category is.
    category: metadata.normalized_category_name || metadata.category_name || '',
    subject: metadata.normalized_subject_name || metadata.canonical_subject_name || '',
    // Whether the question carries a machine crop. Read from the row the server sent, because a
    // crop that exists on disk but is not on the row cannot be shown, and "is there a figure"
    // has to be answered by the data rather than by a second guess at the text.
    has_figure: (candidate.image_refs || []).some(
      (ref) => ref && typeof ref === 'object' && ref.exists !== false),
    // A 題組 is several questions under one printed stem. `group_size` is written by the queue
    // builder (`qbr.groups`) and carried on the row, so the list reads the paper's own structure
    // rather than re-deriving it from text - a second derivation would be a second opinion about
    // where the group starts, which is the one thing that must not differ from the queue.
    group_size: Number(candidate.group_size || 1),
    group_position: candidate.group_position,
    group_ref: candidate.group_ref || '',
    year: metadata.exam_year || metadata.year || yearOf(candidate),
    ordinal: metadata.exam_ordinal,
    // The review state of this question, read from **its own row** rather than from the 2,000-row
    // `/api/workflow` window. That window was the only source before, and it is capped: measured
    // on the served queue it described 2,000 of 79,090 questions, so a question reviewed yesterday
    // showed as 未看 again the moment the page was reloaded - `S.verdict` was never seeded from disk
    // at all. Reading the row is also what makes the list correct on an unseen queue: it is the
    // queue's own statement about the question, not a second opinion about it.
    human_action: rowReviewAction(candidate) || 'unreviewed',
    note: String((candidate.review || {}).notes || ''),
    reason_codes: [],
    candidate,
  };
}

/* What a queue row says about its own review state. `review.action` is what the server projects
   from the append-only event log, so it is the same value `S.verdict` gets when a decision is made
   in this session - and reading it back is what lets a reload resume instead of restarting. A row
   with no human event carries `null`, which is 未看. */
function rowReviewAction(candidate) {
  const review = candidate && candidate.review;
  if (!review || typeof review !== 'object') return '';
  // A reset is a recorded decision that says "look again", and it outranks an older action: the
  // events are append-only, so after a repair the old `accept` is still on the row and the newest
  // statement about the question is the reset.
  if (review.is_reset_unreviewed) return 'reset_review';
  return String(review.action || '');
}

/* The paper's identity: the official PDF's file name without its extension. */
function paperOf(candidate) {
  const pdf = (candidate.source_files || {}).official_pdf
    || (candidate.metadata || {}).question_pdf_relative || '';
  const name = String(pdf).split('/').pop() || '';
  return name.replace(/\.pdf$/i, '');
}

/* `115090` is year 115, sitting 2 - the exam code carries both. */
function yearOf(candidate) {
  const code = String((candidate.metadata || {}).exam_code || '');
  return /^\d{5}/.test(code) ? Number(code.slice(0, 3)) : '';
}

/* One category's name, with the brackets folded so two spellings are one entry.

   The catalog spells `藥師（一）` with full-width brackets in some years and `藥師(一)` in others,
   and they are **one** category: measured on the live queue, the index's own `categories` list holds
   all ten spellings and `queue_index.json`'s `taxonomy` holds four categories after folding. The
   server folds with `review_queue._fold_category`; this is the same replacement on the client, so a
   `category` filter the picker writes matches the folded key the server serves. Without it the
   browser would show six categories where there are four and split 藥師(一) into two choices. */
function foldCategory(name) {
  return String(name || '').replace(/（/g, '(').replace(/）/g, ')');
}

/* The same tree the queue builder computes, rebuilt in the browser. Only reached when the index
   is unavailable; the two must agree, and a disagreement is a reason to look at the index. */
function treeFrom(items) {
  const tree = {};
  items.forEach((item) => {
    const category = foldCategory(item.category) || '(未分類)';
    const year = String(item.year || '(未知)');
    const subject = item.subject || item.paper || '(未知)';
    const bucket = (tree[category] = tree[category] || { years: {}, papers: 0, questions: 0 });
    bucket.papers += 1; bucket.questions += 1;
    const yearBucket = (bucket.years[year] = bucket.years[year] || { sittings: {}, papers: 0, questions: 0 });
    yearBucket.papers += 1; yearBucket.questions += 1;
    const sitting = String(item.ordinal || '');
    const sittingBucket = (yearBucket.sittings[sitting] = yearBucket.sittings[sitting]
      || { subjects: {}, papers: 0, questions: 0 });
    sittingBucket.papers += 1; sittingBucket.questions += 1;
    const subjectBucket = (sittingBucket.subjects[subject] = sittingBucket.subjects[subject]
      || { papers: [], questions: 0 });
    if (!subjectBucket.papers.includes(item.paper)) subjectBucket.papers.push(item.paper);
    subjectBucket.questions += 1;
  });
  return tree;
}

/* The scope in the address bar, so a paper can be linked to and reopened.

   `#類科/年/次/科目` - the same four things the pickers set. This is not a convenience: a review
   session is about one paper, and a reviewer who has to find the same paper again by hand every
   time will not come back to it. It also makes a scope reproducible, which is what an audit of a
   decision needs. */
function scopeFromHash() {
  const raw = decodeURIComponent((location.hash || '').replace(/^#/, ''));
  if (!raw) return null;
  const [category, year, sitting, ...rest] = raw.split('/');
  if (!category) return null;
  // A trailing `q41` names a question, so a link can point at one question of one paper. It is
  // the unit an argument about a question is conducted in.
  let subject = rest.join('/');
  let question = '';
  const hit = subject.match(/\/(q\d+)$/);
  if (hit) { question = hit[1]; subject = subject.slice(0, -hit[0].length); }
  return { category, year: year || '', sitting: sitting || '', subject, question };
}

function scopeToHash() {
  const { category, year, sitting, subject } = S.scope;
  const number = S.rows[S.index] ? `q${S.rows[S.index].question_number}` : '';
  const parts = [category, year, sitting, subject].map((v) => encodeURIComponent(v || ''));
  history.replaceState(null, '', `#${parts.join('/')}${number ? '/' + number : ''}`);
}

function buildScope(tree) {
  S.tree = tree || null;
  const category = $('pickCategory');
  const categories = Object.keys(tree || {}).sort();
  // A scope in the address bar wins over the default, and an unusable one is ignored rather than
  // applied - a stale link must land on a paper, not on an empty list.
  // An empty category in a link means 全部類科, which is a real choice the pickers offer, so it is
  // accepted here; only a category the queue does not hold is refused. Validating with
  // `categories.includes()` refused the empty one too and discarded the whole link, which is why
  // every wide scope opened on the default paper instead of the one the link named.
  const wanted = scopeFromHash();
  if (wanted && (wanted.category === '' || categories.includes(wanted.category))) {
    S.openQuestion = wanted.question || '';
    S.scope = { ...S.scope, ...wanted };
  }
  category.innerHTML = categories.length > 1
    ? '<option value="">全部類科</option>' + categories.map((c) => `<option value="${esc(c)}">${esc(c)}（${tree[c].papers} 卷）</option>`).join('')
    : categories.map((c) => `<option value="${esc(c)}">${esc(c)}（${tree[c].papers} 卷）</option>`).join('');
  // A queue that holds one category should open on it rather than on "all", because "all" is a
  // choice the reviewer would only ever make by accident.
  S.scope.category = resolveLevel(S.scope.category, categories);
  category.value = S.scope.category;
  // Changing a level **does not touch the levels below it**.
  //
  // It used to null all three of them here (`S.scope.year = S.scope.sitting = S.scope.subject =
  // null`), which made `resolveLevel` re-pick the first offered value for each - so choosing a
  // category silently replaced a 全部考次 the reviewer had chosen with a specific sitting, and a
  // 全部科目 with a specific subject. Measured in a browser: setting 考次 back to 全部考次 moved
  // 科目 from `''` to `藥學(一)(包括藥理學與藥物化學)` in the same event. The reviewer loses the
  // filter they set, and the only way to notice is to open the picker and see it moved.
  //
  // The values are left as they are and `refreshScope` resolves each of them **against what is
  // actually offered under the new parent**: a value that still exists is kept exactly, and one
  // that does not is replaced by 全部 (see `resolveLevel`), never by a sibling the reviewer never
  // asked for.
  category.onchange = () => {
    S.scope.category = category.value;
    refreshScope();
  };
  refreshScope();
}

/* The years of the chosen category, and the sittings of the chosen year.
   The sitting is its own level and not part of the year, because the same subject is set twice a
   year and the two settings are two different papers: 1151 and 1152 of 藥師(一) 藥學(二) share a
   subject name and share nothing else. A reviewer comparing a question against the paper it came
   from needs to be able to say *which sitting*, and before this there was no way to say it. */
function refreshScope() {
  const tree = S.tree || {};
  const bucket = tree[S.scope.category];
  const year = $('pickYear'), sitting = $('pickSitting'), subject = $('pickSubject');

  const years = bucket ? Object.keys(bucket.years).filter((y) => /^\d+$/.test(y))
    .sort((a, b) => Number(b) - Number(a)) : [];
  year.innerHTML = (years.length ? '<option value="">全部年度</option>' : '<option value="">—</option>')
    + years.map((y) => `<option value="${y}">${y} 年（${bucket.years[y].papers} 卷）</option>`).join('');
  S.scope.year = resolveLevel(S.scope.year, years);
  year.value = S.scope.year;
  year.onchange = () => { S.scope.year = year.value; refreshScope(); };

  const sittings = availableSittings(bucket, S.scope.year);
  sitting.innerHTML = (sittings.length > 1 ? '<option value="">全部考次</option>' : '<option value="">—</option>')
    + sittings.map((n) => `<option value="${esc(n)}">第 ${esc(n)} 次（${countPapers(bucket, S.scope.year, n, '')} 卷）</option>`).join('');
  S.scope.sitting = resolveLevel(S.scope.sitting, sittings);
  sitting.value = S.scope.sitting;
  sitting.onchange = () => { S.scope.sitting = sitting.value; refreshScope(); };

  const subjects = availableSubjects(bucket, S.scope.year, S.scope.sitting);
  subject.innerHTML = (subjects.length > 1 ? '<option value="">全部科目</option>' : '<option value="">—</option>')
    + subjects.map((name) => `<option value="${esc(name)}">${esc(name)}（${countQuestions(bucket, S.scope.year, S.scope.sitting, name)} 題）</option>`).join('');
  S.scope.subject = resolveLevel(S.scope.subject, subjects);
  subject.value = S.scope.subject;
  subject.onchange = () => { S.scope.subject = subject.value; applyScope(); };
  // Re-drawing the list is all the toggle does - it must not reload the scope. The anchor is the
  // open question, so switching a filter off returns to the same question rather than to a re-fetched
  // one, and switching it on opens on the nearest row it keeps.
  //
  // The panels are re-rendered as well, because `rebuildRows` can move `S.index` - a question that
  // the new filter excludes is not the question shown beside the list - and a cursor that says one
  // thing while the panel says another is the tracking error this whole change removes.
  const refilter = () => {
    rebuildRows();
    renderList();
    renderTextSide();
    renderPaperSide();
    scopeToHash();
  };
  $('figuresOnly').onchange = refilter;
  document.querySelectorAll('#chips .chip').forEach((chip) => {
    chip.onclick = () => {
      const radio = chip.querySelector('input');
      if (radio) radio.checked = true;
      refilter();
    };
  });

  applyScope();
}

/* Where a paper sits in the tree: its category, year, sitting and subject, all four of them.
   Read from the taxonomy rather than taken from the current scope, because the scope may be wider
   than one paper ("all subjects", "all sittings") and a request has to be narrower than the scope,
   not equal to it. Returns null for a paper the tree does not hold, which is a paper that cannot
   be requested at all. */
function whereOfPaper(paper) {
  const tree = S.tree || {};
  for (const [category, bucket] of Object.entries(tree)) {
    for (const [year, yearBucket] of Object.entries(bucket.years || {})) {
      for (const [sitting, sittingBucket] of Object.entries(yearBucket.sittings || {})) {
        for (const [subject, entry] of Object.entries(sittingBucket.subjects || {})) {
          if (entry.papers.includes(paper)) return { category, year, ordinal: sitting, subject };
        }
      }
    }
  }
  return null;
}

/* The values a level offers, gathered from the whole subtree the levels above it select.

   A level with "all" chosen above it has no single bucket to read, and reading from one bucket
   anyway is why choosing 全部年度 emptied the 考次 picker and choosing 全部考次 emptied the 科目
   one: the code asked `years['']` for its sittings, got nothing, and offered nothing. The values
   below an "all" are the union of the values below every branch of it. */
function walkSittings(bucket, year) {
  const years = year ? [year] : Object.keys((bucket && bucket.years) || {});
  const out = [];
  for (const name of years) {
    const yearBucket = bucket.years[name];
    if (!yearBucket) continue;
    for (const [sitting, sittingBucket] of Object.entries(yearBucket.sittings || {})) {
      out.push({ year: name, sitting, sittingBucket });
    }
  }
  return out;
}

function availableSittings(bucket, year) {
  if (!bucket) return [];
  return [...new Set(walkSittings(bucket, year).map((entry) => entry.sitting))].sort();
}

function availableSubjects(bucket, year, sitting) {
  if (!bucket) return [];
  const names = [];
  for (const entry of walkSittings(bucket, year)) {
    if (sitting && entry.sitting !== sitting) continue;
    names.push(...Object.keys(entry.sittingBucket.subjects || {}));
  }
  return [...new Set(names)].sort();
}

/* A value's size within the subtree the levels above it select. Shown beside each option so a
   reviewer can see how big a choice is before making it - and so a level whose list is empty is
   visibly empty rather than silently empty. */
function countPapers(bucket, year, sitting, subject) {
  let total = 0;
  for (const entry of walkSittings(bucket, year)) {
    if (sitting && entry.sitting !== sitting) continue;
    for (const [name, subjectBucket] of Object.entries(entry.sittingBucket.subjects || {})) {
      if (subject && name !== subject) continue;
      total += subjectBucket.papers.length;
    }
  }
  return total;
}

function countQuestions(bucket, year, sitting, subject) {
  let total = 0;
  for (const entry of walkSittings(bucket, year)) {
    if (sitting && entry.sitting !== sitting) continue;
    for (const [name, subjectBucket] of Object.entries(entry.sittingBucket.subjects || {})) {
      if (subject && name !== subject) continue;
      total += subjectBucket.questions;
    }
  }
  return total;
}

/* What a level's value should be, given the values it offers.

   Three states, and they are genuinely different:
     `null`  - not chosen yet, so take the first offered value (the newest year, the first subject)
     `''`    - "all", chosen deliberately; the pickers offer it and it must survive a redraw
     a value - kept if the level offers it, replaced if not, so a stale link lands somewhere real

   Collapsing `''` into "invalid" is what made every 全部 option unreachable: choosing 全部年度 set
   the picker to `''`, the next redraw saw `''` was not among the years and replaced it with the
   newest one, and the same thing happened to a link that named only a category.

   A value that is **no longer offered** falls back to `''` (全部) when 全部 is on offer, not to the
   first value. The distinction is who chose it: `null` is the app choosing a sensible default, so
   the first value is right; a value that exists and then stops existing was chosen by the reviewer,
   and quietly replacing it with a *different* specific value is the jump that made the pickers
   untrustworthy. 全部 is the one replacement that does not claim a choice nobody made. */
function resolveLevel(value, offered) {
  if (value === null || value === undefined) return offered[0] ?? '';
  if (value === '') return offered.length > 1 ? '' : (offered[0] ?? '');
  if (offered.includes(value)) return value;
  return offered.length > 1 ? '' : (offered[0] ?? '');
}

/* Every paper the scope names, honouring "all" at each level.

   The levels are a tree - category, year, sitting, subject - and `''` at a level means that level
   was left on 全部. Walking the tree with that meaning is the only way 全部 can work: the pickers
   offer it, and a scope that names it must resolve to every paper below it rather than to none. */
function scopePapers() {
  const tree = S.tree || {};
  const papers = [];
  const categories = S.scope.category ? [S.scope.category] : Object.keys(tree);
  for (const category of categories) {
    const bucket = tree[category];
    if (!bucket) continue;
    const years = S.scope.year ? [S.scope.year] : Object.keys(bucket.years);
    for (const year of years) {
      const yearBucket = bucket.years[year];
      if (!yearBucket) continue;
      const sittings = S.scope.sitting ? [S.scope.sitting]
        : Object.keys(yearBucket.sittings || {});
      for (const sitting of sittings) {
        const sittingBucket = (yearBucket.sittings || {})[sitting];
        if (!sittingBucket) continue;
        const subjects = S.scope.subject === '' ? Object.keys(sittingBucket.subjects || {})
          : [S.scope.subject];
        for (const subject of subjects) {
          const entry = (sittingBucket.subjects || {})[subject];
          if (entry) papers.push(...entry.papers);
        }
      }
    }
  }
  return papers.length ? papers : null;
}

/* Load the questions of the current scope from the server, one scope at a time.

   The whole queue is 32,350 questions and the endpoint refuses to return more than a thousand of
   them in one response, so "load everything, then filter in the browser" cannot work: the browser
   held the first thousand questions of the queue - which are 1152 藥師(一) and 1152 醫事檢驗師 -
   while the scope opened on 107 年 藥師 藥事行政與法規, which is not among them. The list showed
   "這個範圍沒有題目" for a paper that is in the queue, and the twelve crops in the loaded thousand
   were unreachable because the questions carrying them were never on screen.

   So the filter is the server's, which is where the whole queue is, and the browser asks for the
   scope it is showing. The response also carries `filtered_count`, which is the number the scope
   really holds - not the number that happened to fit. */
async function applyScope() {
  S.index = 0;
  S.editing = false;
  scopeToHash();
  renderCrumbs();
  const papers = scopePapers();
  if (!papers || !papers.length) {
    S.view = [];
    // `S.rows` is cleared with it. It is a separate array now, so emptying only `S.view` would leave
    // the previous scope's rows drawn and walkable - the list would show one paper while the crumbs
    // named another, which is the class of defect this whole change is about.
    S.rows = [];
    S.index = 0;
    renderList();
    $('textSide').innerHTML = '<div class="empty">這個範圍沒有題目</div>';
    return;
  }
  $('listBody').innerHTML = '<div class="empty">載入中…</div>';
  const wanted = new Set(papers);
  try {
    // The fetch is per sitting, not per scope and not per paper.
    //
    // Per scope is wrong: the endpoint caps a response at a thousand rows and the widest scopes are
    // far past that - 藥師(一) is 6,000 questions and 醫事檢驗師 is 15,120 - so "全部類科" would
    // silently show a fraction of itself.
    //
    // Per paper is also wrong, and it is what the previous version did: 419 requests to read one
    // category, each a separate round trip, when the data is already grouped.
    //
    // A sitting is the natural unit. Measured over the whole queue, the 220 (category, year,
    // sitting) groups hold at most 480 questions - 醫事檢驗師 115 第2次, six papers - which is
    // comfortably inside the cap.
    //
    // They are fetched **together, not one after another**. Measured against the station: a sitting
    // is 0.02 s of server time but a wide scope is 32 of them, and awaiting each in turn cost
    // 13.7 s to open 物理治療師 because the round trips added up instead of overlapping. The server
    // is threaded and the connections are now kept alive, so issuing the 32 at once costs about what
    // the slowest one costs. The results are reassembled in paper order below, so the order the
    // requests happen to finish in cannot change what the reviewer sees.
    const requests = [];
    const seen = new Set();
    for (const paper of papers) {
      const where = whereOfPaper(paper);
      if (!where) continue;
      const key = `${where.category}\u0000${where.year}\u0000${where.ordinal}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const query = new URLSearchParams({
        category: where.category, year: where.year, ordinal: where.ordinal, limit: '1000',
      });
      requests.push({ where, query });
    }
    const responses = await Promise.all(requests.map(async ({ where, query }) => {
      const response = await fetch(`/api/candidates?${query}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}（${where.year} 年第${where.ordinal}次）`);
      return response.json();
    }));
    const rows = [];
    for (const data of responses) {
      if (!data.candidates) throw new Error(data.error || '伺服器沒有回傳 candidates');
      // The server says how many rows matched and how many it sent. If they differ, the answer was
      // truncated, and a truncated answer that looks complete is exactly the defect this rewrote
      // away - so it is reported rather than swallowed. Measured: the largest sitting is 480
      // questions against a cap of 1,000, so this must never fire; if it ever does, the cap has
      // moved and the paging has to come back.
      if (data.filtered_count > data.candidates.length) {
        throw new Error(`某個考次有 ${data.filtered_count} 題，伺服器只回了 ${data.candidates.length} 題（上限 1000）。`);
      }
      // Keep only the papers this scope names; the sitting may hold others (a subject filter
      // narrows within one sitting of one year).
      for (const candidate of data.candidates) {
        if (wanted.has(paperOf(candidate))) rows.push(candidate);
      }
    }
    // Order by paper, then by question number. Sorting by number alone interleaves the six papers
    // of a sitting - question 1 of each, then question 2 of each - and a reviewer reads one paper
    // from its first question to its last, so the paper has to be the primary key.
    S.view = rows.map(toItem).sort((a, b) => (
      a.paper < b.paper ? -1 : a.paper > b.paper ? 1
        : Number(a.question_number) - Number(b.question_number)
    ));
    // Seed the session's verdict map from what the queue says each question already is. Without
    // this a reload - or a scope change onto a paper read an hour ago - drew every row as 未看 and
    // opened on question 1, so the reviewer could not tell what they had already done. Only rows in
    // *this* scope are seeded, which is correct: the map is a cache of the row's own state, and
    // every row carries it.
    for (const item of S.view) {
      const action = rowReviewAction(item.candidate);
      // A note never makes a question 已過目: it is something written *about* a question, and the
      // reviewer still has to decide. That is why it is kept in its own map rather than in
      // `S.verdict` - if it went in there, annotating a question during a fast first pass would
      // silently mark it read and the walk would skip it.
      if (action && !NOTE_ACTIONS.has(action)) S.verdict.set(item.candidate_key, action);
      else S.verdict.delete(item.candidate_key);
      if (item.note) S.notes.set(item.candidate_key, item.note);
      else S.notes.delete(item.candidate_key);
    }
    S.scopeTotal = S.view.length;
  } catch (error) {
    S.view = [];
    $('textSide').innerHTML = `<div class="empty">無法讀取這個範圍：${esc(error.message || error)}</div>`;
  }
  renderCrumbs();
  // `S.rows` is built here, once per scope, and every later filter change rebuilds it from `S.view`
  // with `rebuildRows()`. Building it before the first `go()` matters: `go()` indexes `S.rows`, so a
  // scope that rendered the list without it would draw nothing and be unable to move.
  if (!rebuildRows(null)) {
    renderList();
    $('textSide').innerHTML = '<div class="empty">這個範圍沒有題目</div>';
    return;
  }
  renderList();
  // Open on the question the link named, else on the first one not yet reviewed, so a second
  // session resumes where the first stopped instead of at question 1. The search runs over `S.rows`,
  // which is what the reviewer will actually see, so a link to a question the active filter hides
  // opens on the nearest visible row instead of opening on nothing.
  const named = S.openQuestion
    ? S.rows.findIndex((item) => `q${item.question_number}` === S.openQuestion) : -1;
  S.openQuestion = '';
  const firstOpen = S.rows.findIndex((item) => !verdictOf(item.candidate_key));
  await go(named >= 0 ? named : (firstOpen >= 0 ? firstOpen : 0));
}

function renderCrumbs() {
  const parts = [S.scope.category, S.scope.year && `${S.scope.year} 年`,
                 S.scope.sitting && `第 ${S.scope.sitting} 次`, S.scope.subject].filter(Boolean);
  $('crumbs').innerHTML = parts.map((part, i) => `<span class="crumb${i === parts.length - 1 ? ' on' : ''}">${esc(part)}</span>`).join('')
    || '<span class="crumb">全部</span>';
  const papers = new Set(S.view.map((item) => item.paper)).size;
  const done = S.view.filter((item) => verdictOf(item.candidate_key)).length;
  const shown = S.view.length && S.scopeTotal > S.view.length
    ? `${S.view.length} / ${S.scopeTotal} 題` : `${S.view.length} 題`;
  $('scopeCount').textContent = `${papers} 卷 · ${shown} · 已過目 ${done}`;
}

/* How many rows the list draws at once. A scope can be 15,120 questions - "全部類科" of
   醫事檢驗師 - and a list of 15,120 buttons is 15,120 nodes the browser builds before it can paint
   anything. The window follows the cursor, so the rows around the open question are always there
   and the count above the list is the whole scope, not the window. */
