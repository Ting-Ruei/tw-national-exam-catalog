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
  //: 範圍讀取的**世代**。每開一個範圍就加一，讀完之後才准寫畫面；晚回來的舊回應比對不到自己的
  //: 世代，就什麼都不做。加這個欄位之前的行為量得到：開 A 範圍、還沒回來就換 B 範圍，B 先回來、
  //: A 後回來 ⇒ 清單是 A 的題目而麵包屑是 B 的範圍（`applyScope()` 的註解說的那一類缺陷）。
  scopeRequest: 0,
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
// 2026-09-25：`reset_review` 那一籤由「AI已解決」改名為「AI已修改」。owner 原文：「可以改成AI已修改，
// 但是「還原」這種事情不是修改」——「已解決」把「有人動過它」讀成了「它沒問題了」，而這一格裡還有
// 沒被動過字的純退回；改成「已修改」之後，撤回（`withdrawn`）也不再屬於這一籤（見 `rowReviewAction`）。
const LABEL = { accept: '確認正常', needs_review: '需重看', block: '阻擋', correct: '已修正', comment: '只加註記', reset_review: 'AI已修改' };
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
  // **人的決定優先於「這一題的字被改過」**（owner 2026-09-29：「我當時的決定（accept 就是通過，
  // block 就是阻擋）」）。
  //
  // 這不是措辭問題，是 635 列搬家。量法：拿站上快照（`question_review_events.jsonl`，20,329 事件／
  // 13,780 題）先跑伺服器的投影組成真正送進瀏覽器的那份 `review`，再把兩版規則逐列跑一次 ——
  // `action=accept` ＋ 有（非撤回）correction 的 **560** 列、`action=block` 的 **74** 列、
  // `action=needs_review` 的 **1** 列；correction 優先的那一版把它們全畫成「已修正」（那一格共 648 列），
  // 這一版畫成那個人當時的決定（「已修正」只剩 13 列）。
  //
  // 為什麼 action 先問：清單這一格要回答的是「我對這一題做過什麼」。`correct` 說的是「這一題的字被改過」，
  // 那是同一列的另一件事，它不會消失（`review.correction` 與詳情區的 `applied_kind` 都還留著），
  // 但它不可以蓋掉那個人自己已經下過的判斷。
  const action = String(review.action || '');
  if (action === 'accept' || action === 'needs_review' || action === 'block') return action;
  // 沒有上面那三個人的決定時，這一列的事實才是「字被改過」。為什麼要讀 `correction` 而不是只讀
  // `review.action`：append-only 的日誌裡，2026-09-25 之前的修正事件被寫成 `reviewed`（`correct` 被前
  // 一個決定覆寫，那是已修掉的缺陷），但那筆修正的內容還在這一列上（站上
  // `moex:107100:305:33:1:question:q076`：`action: "reviewed"` ＋ `has_correction: true`）。只讀 action，
  // 畫面上那一題就是「已看過」，而「已修正」那一格是 0 ——「修正完之後找不到那一題」正是使用者回報的缺陷。
  //
  // **機器把自己那一筆收回時，這一列不再是「已修正」**（2026-09-25 量到，同一天修）：撤回的事件仍然帶著
  // `correction`，那是紀錄、不是畫面（伺服器的投影是 `applied_kind: "withdrawn"`，且依 2026-09-24 的規則
  // 不再把 correction 疊回列上）。撤回之後沒有人再看過的那幾題回到「未看」，詳情區仍由 `applied_kind`
  // 顯示「已還原（機器改錯）」。
  const withdrawn = String(review.applied_kind || review.applied || '').toLowerCase() === 'withdrawn';
  if (!withdrawn && review.correction && typeof review.correction === 'object') return 'correct';
  // 撤回不是人的決定，也不是「AI 改了這一題」（owner 2026-09-25：「「還原」這種事情不是修改」）。
  // 伺服器不再把撤回投影成待複核，所以這一列剩下的 `action` 若是機器自己寫的 `reset_review`，它就只是
  // 紀錄——這一列沒有人做過決定，讀成未看。
  if (withdrawn && action === 'reset_review') return '';
  return action;
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
  const parts = raw.split('/');
  // **A mode prefix is not a category.**（2026-09-24 修正）
  //
  // `#錯題/藥師/115/1/藥物治療學/q41` 的第一段是**模式**，不是類科。舊版把整串從第一段開始
  // 當成 scope，於是「錯題」被拿去查類科樹查不到 → 這條連結被丟掉 → 範圍回落到預設紙本。
  // 而 `scopeToHash()` 又把 `S.scope.category` 寫回第一段，所以模式一旦寫進 hash 就會被
  // 下一輪的 scope 覆寫掉——`showArea` 永遠讀不到它。兩個缺陷疊在一起，結果就是
  // **重新載入一定回到題目區**。
  //
  // 剝掉前綴之後，兩者共用同一個 `#模式/類科/年/次/科目/qNNN` 形狀，各自讀它認得的部分：
  // 模式讀第一段（`areaFromHash`），範圍讀其餘四段（這裡）。
  const hasMode = Object.prototype.hasOwnProperty.call(PREFIX_AREA, parts[0]);
  const rest = hasMode ? parts.slice(1) : parts;
  if (hasMode && !rest.length) return null;   // `#錯題` 只有模式，沒有範圍：不覆寫現有範圍
  const [category, year, sitting, ...tail] = rest;
  if (category === undefined) return null;
  // A trailing `q41` names a question, so a link can point at one question of one paper. It is
  // the unit an argument about a question is conducted in.
  //
  // **科目是第四段以後，不是整串。**（2026-09-24 修正）舊版寫 `rest.join('/')`，於是
  // `#藥師(一)/115/2/藥學(三)` 讀到的科目是 `藥師(一)/115/2/藥學(三)`——那個字串不在科目清單裡，
  // `resolveLevel()` 就把它換成「全部科目」，連結指名的科目**靜默消失**（畫面仍開得起來，只是多出
  // 一卷）。`tail` 本來就已經解構出來了，只是沒被用到。
  let subject = tail.join('/');
  let question = '';
  const hit = subject.match(/\/(q\d+)$/);
  if (hit) { question = hit[1]; subject = subject.slice(0, -hit[0].length); }
  return { category, year: year || '', sitting: sitting || '', subject, question };
}

function scopeToHash() {
  const { category, year, sitting, subject } = S.scope;
  const parts = [category, year, sitting, subject].map((v) => encodeURIComponent(v || ''));
  // **The mode prefix is written back.**（2026-09-24 修正）
  //
  // 舊版只寫 scope，不寫模式，所以 `history.replaceState` 會把 `#錯題/...` 覆寫成 `#類科/...`。
  // 啟動時 `buildScope()` 先跑（它寫 hash），`showArea(areaFromHash())` 才讀——讀到的已經
  // 不是 `錯題` 了。模式因此永遠不可能從 hash 復原，不論重新載入或按上一頁。
  //
  // 討論區的當前題目也寫進來（`#錯題/.../q41`），因為它是這一區的「當下位置」：重新載入
  // 回到第 0 題等於把審到一半的位置丟掉。題目區**不寫**（見下面 `here` 的理由），但兩者共用
  // 同一個形狀，各自解讀——`scopeFromHash` 讀其餘四段，`areaFromHash` 讀第一段。
  const mode = A.area === 'question' ? '' : (AREA_PREFIX[A.area] || '');
  const head = mode ? `${encodeURIComponent(mode)}/` : '';
  // `D.rows` holds **candidate keys, not rows** (see `loadDiscuss`: `D.rows.push(candidate.candidate_key)`).
  // Reading `.question_number` off it gives `undefined`, which is how `#錯題/.../qundefined` got written
  // into the address bar. The number lives on the candidate, so the key has to be resolved first.
  const discussNumber = A.area === 'discuss' ? (D.byKey.get(D.rows[D.index]) || {}).question_number : null;
  // **題目區不寫「當下這一題」（2026-09-24）。**
  //
  // 舊版把題目區的當下游標也寫進 hash，於是**重新載入一定會回到離開時那一題**——`buildScope()`
  // 把它讀成 `S.openQuestion`，`applyScope()` 就照它開，`firstOpen`（第一題還沒審的）永遠輪不到。
  // 也就是說「未審優先」在畫面上從來沒發生過：使用者按 F5，看到的還是他離開時那一題，而那一題通常
  // 已經判完了。使用者原文：「如果有還沒審的題目，在 F5 刷新的情況下，優先顯示在面前……但是你現在
  // 只是退回原本的樣子」。
  //
  // 所以位置在這裡不寫了：重新載入 → hash 只有範圍 → `applyScope()` 開在**第一題還沒審的**，
  // 而整體順序仍是紙本順序，往上（已判過的）往下都走得動。**明講的連結不受影響**：
  // `scopeFromHash()` 照樣讀 `/qNNN`，所以手寫或貼上的 `#類科/年/次/科目/q41` 仍然開在 q41。
  // 討論區的 `qNNN` 也照寫（那是它自己的「當下位置」契約，重新載入回到第 0 題會丟掉審到一半的位置）。
  const here = A.area === 'discuss'
    ? (discussNumber === null || discussNumber === undefined ? '' : `q${discussNumber}`)
    : '';
  // A question the queue did not carry has no number: write the scope without it rather than `qundefined`.
  history.replaceState(null, '', `#${head}${parts.join('/')}${here ? '/' + here : ''}`);
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

/* 換篩選（晶片、只看有圖）：重建清單 → 重畫三個面板。

   **這是一個頂層函式，不是 `refreshScope()` 裡的閉包**，理由是可驗證性：晶片是使用者實際按的東西，
   而「按了晶片之後 `全部` 的分段有沒有重算」是這次改動唯一會壞掉的地方。寫成閉包的話，只有真的
   打開 Chrome 按下去才測得到；寫成頂層函式，`scripts/test_v2_navigation.mjs` 就能直接呼叫**同一個**
   函式（它本來就已經把 `v2/*.js` 原封不動載進來跑），不必在測試裡另外拼一條「像晶片的路徑」——
   那會變成第二個實作，而兩個做同一件事的東西就是兩個可以不一致的地方。

   它同時做四件事，因為它們是同一件事的四個面：清單要重畫（`renderList`）、右邊兩個面板可能因為
   `S.index` 移動而指向別的題目（`renderTextSide`、`renderPaperSide`），而範圍變了要寫回網址
   （`scopeToHash`）。少做任何一件，畫面就會出現「清單說一題、面板說另一題」。 */
function refilter() {
  rebuildRows();
  renderList();
  renderTextSide();
  renderPaperSide();
  scopeToHash();
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
  //
  // 這裡只接線：工作內容在頂層的 `refilter()`，理由寫在那裡（測試要能直接呼叫同一個函式）。
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
   really holds - not the number that happened to fit.

   **這一段是共用的**（`applyScope()` 開一個範圍，`refreshScopeRows()` 重讀同一個範圍），理由與
   `refilter()` 寫成頂層函式相同：兩份「怎麼把伺服器的列變成 S.view」就是兩個會不一致的地方，
   而這裡算的正好是**這一行已經判過了沒、它的註記是什麼**——最不該有兩種答案的東西。
   回傳 false 代表讀失敗（`S.view` 維持原狀，畫面由上層決定怎麼說）。 */
async function loadScopeRows(papers, requestId) {
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
    // 世代比對，**在寫任何狀態之前**：這一批回應回來時若已經開了別的範圍，它們就不再是畫面上
    // 那個範圍的答案。少了這一行的量測結果：開 A、還沒回來就換 B，B 先回來、A 後回來，清單變成
    // A 的題目而麵包屑寫 B（`S.scopeRequest` 的註解記了同一件事）。
    if (requestId === undefined || requestId !== S.scopeRequest) return false;
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
    // 同一個世代比對：舊範圍的失敗不可以蓋掉新範圍的畫面（那會是「讀不到這個範圍」壓在上一個
    // 範圍的題目上，或蓋掉新範圍剛讀回來的列）。
    if (requestId === undefined || requestId !== S.scopeRequest) return false;
    S.view = [];
    $('textSide').innerHTML = `<div class="empty">無法讀取這個範圍：${esc(error.message || error)}</div>`;
    return false;
  }
  return true;
}

/* 開一個範圍：讀它的列、把游標放在**第一題還沒審的**（或有具名的那一題）。

   重新載入時「未審的要開在你面前」之所以成立，是因為題目區不再把當下游標寫進網址（見
   `scopeToHash()`）：`S.openQuestion` 只在網址明講 `/qNNN` 時才有值，所以平常這一條會走
   `firstOpen`。 */
async function applyScope() {
  // 開一個範圍＝新的一個世代（見 `S.scopeRequest`）：這一刻之後回來的舊回應都不准再寫畫面。
  const requestId = ++S.scopeRequest;
  S.index = 0;
  S.editing = false;
  scopeToHash();
  // 舊範圍的列在讀取期間不可以留在 `S.rows` 裡：畫面上已經寫「載入中…」，但留著的列還是可以被
  // `W`／`S` 走到、被決定送出，而它們是上一個範圍的題目。跟下面那個 `!papers.length` 分支清的是
  // 同一件事，只是發生得早一點。
  S.view = [];
  S.rows = [];
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
  if (!await loadScopeRows(papers, requestId)) return;
  // 讀完才畫計數。上面那一次 `renderCrumbs()` 是為了在等待時先把範圍名畫出來（`S.view` 還是空的），
  // 所以它畫出來的「0 題」必須在這裡被真的數字蓋掉——2026-09-24 把讀取拆成 `loadScopeRows()` 時
  // 漏了這一行，畫面上會一直寫「0 卷 · 0 題」，而清單裡明明有 80 列。
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

/* 從別的區寫入之後回到題目區：重讀同一個範圍的列，**游標留在同一題**。

   為什麼需要它：一筆寫入的真相在伺服器上（那一列的 `review` 投影就是「判過了沒、註記是什麼」），
   而題目區的 `S.verdict`／`S.notes` 是進這個範圍時種下的。在討論區寫一筆註解、再切回題目區，
   畫面就會拿著**寫入前**的註記與狀態——使用者形容的「後面覺得我做了，前面覺得後面都沒做」。
   （`showArea()` 在 `A.questionStale` 時呼叫這裡；旗標由 `invalidateAreas()` 在別區寫入時立起。）

   游標留在同一題的理由與 `refilter()` 相同：切一個區回來就被彈到別的地方，等於把審到一半的位置
   丟掉。找不到原來那一題（被別的寫入移出這個範圍）才回到第 0 列。 */
async function refreshScopeRows() {
  const key = (S.rows[S.index] || {}).candidate_key;
  const papers = scopePapers();
  if (!papers || !papers.length) return;
  // 重讀也是新的一個世代：重讀期間如果有人換了範圍，兩邊的回應只能有一個寫畫面。
  const requestId = ++S.scopeRequest;
  if (!await loadScopeRows(papers, requestId)) return;
  if (!rebuildRows(null)) return;
  const at = key ? S.rows.findIndex((item) => item.candidate_key === key) : -1;
  await go(at >= 0 ? at : 0);
  renderCrumbs();
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
