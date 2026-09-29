/* 題目審核區：清單視窗（`LIST_WINDOW`）與 `W`/`S` 走法同一個 `S.view`（charter：導覽跟隨被畫出的清單）；左頁＝题目（6 個 option 以 1..6 對映），右頁＝紙本 PDF（與左頁脫鉤）。`A/R/B/E`＝審題；`1..6` 聚焦选项、`7` 註解、`8` 紙本；`Enter` 儲存、`Shift+Enter` 換行、`Esc` 關框。前 6 項的標籤用紙本的「題號（數字）」；答案卷若用 `1．A` 式標號 likewise 數字，字號不同即不同题。同紙題號重複時 `S.byKey` 以最後筆為準（實測：`S.rows` 79,090、`S.byKey` 79,088，重複 2 題：`305:22` 與 `305:0403`）。重複號的浮動標記專用於 **題目區**（`PAPER.*` 前綴）；答案卷僅 4 題 likewise 用 1..4，`5..8` 在答案卷無對應而直往跳之（浮動）。浮動對照前 6 項（數字）`document`;`PAPER[1-6]`（`PAPER[7]`/`PAPER[8]` 之浮動對照 *note* 記号（`PAPER.*` 前綴））。 */
const LIST_WINDOW = 400;

/* Which state a question is in, and which one the reviewer is looking at.

   These are **states of the question**, not of the reviewer's session: 未看 means "no decision
   recorded", a flag means "a decision was recorded that says this needs another look". They are
   disjoint and together with 確認正常 they cover every row, so the chips partition the scope and
   the counts add up - which is what makes the number beside a chip trustworthy.

   `需重看` and `阻擋` are **one** chip (2026-09-24 改名為「block」), because they are the same act -
   the reviewer saying "do not ship this as it stands" - and they are reviewed the same way, by
   reading the question again. Splitting them would make the reviewer choose a category before
   deciding what is wrong.

   **The `reset_review` state is a different chip, because it is a different author** (2026-09-24
   改名為「AI已解決」，2026-09-25 再改名為「AI已修改」). A `reset_review` event is not the reviewer's
   opinion: the pipeline changed the
   question's text (a repair) or the model raised a finding, and the question came *back*. The
   reviewer never flagged it; something else decided the reading it was approved under is no longer
   the reading on screen. Merging it with the reviewer's own blocks hid that distinction on the one
   screen where it matters most - the reviewer cannot tell whether a question is waiting because they
   said so or because a machine said so, and the second case needs a different question answered
   ("is the new text right?") than the first ("what is wrong?"). Measured on the served queue: 106
   blocks, of which 9 are resets.

   **撤回（`applied_kind: withdrawn`）不在這一格**（2026-09-25，owner 原文：「「還原」這種事情不是
   修改」）：機器把自己改錯的字收回之後，這一列的文字回到紙本，等的是那個人自己的下一個決定，不是
   複核「新文字對不對」。判準在伺服器（`queue_view.review_projection` 的 `reset_waiting`）與
   `rowReviewAction`，不是這裡。

   A third chip (「AI無法判斷」) is the **model's** axis rather than the reviewer's - see
   `aiCannotTell()` below. The first two are states of the question's record; that one asks what the
   model was able to conclude, so a row can be in it *and* in `block` at the same time.

   **已修正 is its own state, not 已過目** (2026-09-25, 業主回報). A `correct` action says the text was
   wrong and the reviewer replaced it; it does *not* say the question is right (`correct` is not in
   `QUESTION_READY_ACTIONS`, so it never marks a question ready). Drawing it as an ordinary done row
   was the defect: the reviewer saved a correction, the row lost its mark, and it could not be found
   under any chip - "修正完它就通過了，我就找不到了". It keeps its own mark colour and its own chip,
   and the same row is findable again by the reviewer who wrote it.

   The state chips stay in the same 不要出貨 family, so nothing about the walk changes - only the
   count and the filter. */
function stateOf(item) {
  const v = verdictOf(item.candidate_key);
  if (v === 'reset_review') return 'returned';
  if (v === 'needs_review' || v === 'block') return 'flagged';
  if (v === 'correct') return 'corrected';
  if (v) return 'done';
  return 'unseen';
}

/* 機器動過的字：同一格「AI已修改」底下其實有**三種不同的東西**（2026-09-24；那一格 2026-09-25 前
   叫「AI已解決」）。

   使用者原文：「判讀 → 文字 跟 文字 →抽取檔要打通，並且改標籤送到「AI已解決」，我才能知道有沒有
   改過」。一筆 `reset_review` 只說「這一題退回來了」，沒說**別人用什麼方式動了它的字**。三種要
   分開，因為要複核的東西不同：

     * `field`         整欄依紙本換掉。要複核的是**這一欄的文字是不是紙本上的那一欄**——讀紙本、
                       讀抽取檔，兩份整欄對照。
     * `normalisation` 被換掉的字**全部**落在部首碼位區（U+2E80–U+2EFF／U+2F00–U+2FDF），例如
                       `⺟`→`母`。同一個字換一種碼位寫法，讀法沒有變。
     * `glyph`         換掉的字**不是**全部落在那一區：字形本身被改過（例如 `ћ`→`①`）。這一種要
                       逐字看紙本。

   分類**不在這裡算**：`applied_kind` 由伺服器依紙本／抽取兩邊**實際的字**算出來
   （`queue_view.machine_applied_kind`），送在每一列的 `review` 裡。在瀏覽器再實作一次就是第二個
   意見，而這裡要的正是它與判讀那一邊一致。

   沒有 `applied` 的一律是空字串——那是最常見的一種（管線退回、模型提問）：字沒有被動過，所以
   不該有標籤，畫面維持原本那一格。

   `withdrawn`（2026-09-24 加）不在上面那一組裡：它**不是**那三種要讀的修復，而是機器把自己改過的
   字收回（`queue_view.WITHDRAWN_KIND`）。它仍然要有自己的字——與「沒改過」同一個樣子的話，人就
   看不出機器曾經改錯又被收回。 */
const APPLIED_LABEL = {
  field: '依紙本改字',
  glyph: '字形替換',
  normalisation: '正規化（部首碼位）',
  withdrawn: '已還原（機器改錯）',
};
function machineAppliedLabel(review) {
  return APPLIED_LABEL[(review || {}).applied_kind] || '';
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

/* 「AI無法判斷」：**人擋過的**題目裡，模型讀過但**沒有下「有問題／沒問題」的結論**的那些。

   使用者要的第三格（原文：「新增一個「AI無法判斷」，我就可以為這些下註解」）。它與 stateOf 是
   兩條不同的軸：`stateOf` 說的是**人**對這一題做了什麼（未看／block／退回／已判），這一格說的
   是**模型**給不給得出結論。所以一題可以同時是「block」與「AI無法判斷」——那正是要看的組合。

   判準寫在這一題自己的 `qbr_ai_finding`（伺服器已經送在每一個 candidate 上，題目區右欄的
   「② AI 意見」用的就是同一筆），不是重新推導：

     `NOT_EXTRACTION`  模型說「不是抽取造成的」＝要人判。`confirm_dispute.py` 就是這樣稱呼它的
                       （"that is exactly NOT_EXTRACTION, and it is the human-judgment case"）。
     `error` / 沒有 verdict  那一筆呼叫沒有讀出結論，同樣是「模型沒能判斷」。
     `OK` / `DEFECT`   模型有結論（沒發現異常／認為有問題），不屬於這一格。

   **只有人擋過的（`block`／`需重看`）算。** 這一格是**人的**註解工作清單，而它的存在理由是人機
   分工：人擋下來的＝要修的工作，機器看紙本、能修就修（修過的落在那題的「AI已修改」），
   修不了或說不出所以然的才回到人手上。所以：

   * 人已經放行的（`done`）不算——沒有待辦事項。第一版漏了這一條，使用者原文：「你的 AI無法判斷
     裡面，有我已經審核的問題啊，我都通過了你是要判斷什麼」。
   * **人還沒看過的（`unseen`）也不算**——人還沒表達意見，機器說不出所以然並不是人的待辦事項，
     而且把整個未掃描的佇列算進來會讓這個數字等於全部。使用者原文：「你把人工看過的放行，怎麼把
     人工未看過也框入……我才說後面的 agent 循環是僅針對 block 去看」。
   * 機器已經動過的（`returned`＝修復後待複核）不算——那一題在「AI已修改」等的是「新文字對不對」，
     不是「哪裡有問題」。

   結果：這一格是 `block` 的子集（實測站上：藥師(一) 115 第2次 5 → 4，少掉的那一列是機器退回的）。
   模型的話在每一題的右欄仍然看得到，這一格只決定要不要催人。 */
function aiCannotTell(item) {
  /* **退滿三次＝機器不再試了**（業主 2026-09-25 的循環上限）。原文：「循環三次之後才送入 AI無法
     判斷」。判準是伺服器量到的次數（`review.exhausted`／`review.attempts`，
     `withdrawals.rejection_counts` 那一個定義），不在瀏覽器再數一次——數兩次就會有兩個答案。
     這一條放在 `qbr_ai_finding` 之前：人可能三次都被打回、而這一題當下的判讀是 OK 或根本沒有紀錄，
     那時機器仍然已經停手，這一題仍然要回到人手上。 */
  if ((item.candidate.review || {}).exhausted) return true;
  const record = item.candidate.qbr_ai_finding;
  if (!record) return false;
  if (stateOf(item) !== 'flagged') return false;
  if (record.error) return true;
  const verdict = (record.finding || {}).verdict;
  return !verdict || (verdict !== 'OK' && verdict !== 'DEFECT');
}

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
    if (mode === 'corrected') return stateOf(item) === 'corrected';
    // 「AI無法判斷」與 stateOf 是兩條軸：它問的是模型給不給得出結論，不是人做了什麼。
    if (mode === 'aicannot') return aiCannotTell(item);
    // 爭議 is what the *pipeline* could not settle, not what the reviewer decided. It is a separate
    // axis from the state chips and deliberately so: a question can be a settled `確認正常` and still
    // carry a dispute the pipeline raised, and collapsing them would hide the machine's uncertainty
    // behind the human's decision.
    if (mode === 'disputed') return hasDispute(item);
    return true;
  });
}

/* ------------------------------------------------ 全部：紙本順序，游標停在還沒審的第一題

   使用者要的是「優先顯示還沒有審核的」，理由他也說了（原文）：「不然我如果照順序審核，會刷不到
   應該看的」。**但「優先」不等於「重排」**，而 2026-09-24 的第一版把兩者當成同一件事：

   * 畫出來的清單被分成「未審段＋已判段」。於是 `W` 在未審段的最上面一列**沒有上一列**——
     使用者原文：「我想要往上一題參考也沒有了」；他要的是紙本上的前一題（那一題通常已經判過），
     而重排把它放到清單的另一端去了。
   * 「還沒審的排前面」也就等於「紙本順序不見了」：相鄰兩列不再是相鄰兩題，`題組` 的共用題幹
     與前一題的線索都對不上。

   所以現在只有一句話：**`S.rows` 就是紙本順序**（與 `S.view` 同一個來源、同一種排序），
   `W`／`S` 是紙本上的前一題／下一題。「優先」由兩件不改變順序的事提供：

   * `applyScope()` 開一個範圍時把游標放在**第一題還沒審的**（`firstOpen`），所以打開就看到
     該動的地方，不必從 q1 開始翻；
   * `未看` 這個籤只留還沒審的題目——要「只看還沒審的」是一個篩選，不是一種排序。

   判決本來就不會移動任何一列（現在更是如此：沒有任何東西會重排），所以 `next()` 的
   `was`／`survived` 算術成立：判完還在同一列，往前走一格，按 `W` 回到剛看的那一題。 */

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
  $('nCorrected').textContent = n((item) => stateOf(item) === 'corrected');
  $('nAiCannot').textContent = n(aiCannotTell);
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
    // 已修正 不是 已過目（見 `stateOf`）：它有自己的底色，因為「像通過」正是業主回報的那個缺陷。
    if (v === 'correct') cls.push('corrected');
    else if (v) cls.push('done');
    if (v === 'needs_review' || v === 'block') cls.push('flag');
    if (v === 'reset_review') cls.push('returned');
    // **退回來的題目要說出是哪一種退回**：一筆 reset 只說「有人動過」，這裡說出動的是什麼
    // （依紙本改字／字形替換／正規化（部首碼位）），沒有 `applied` 的一律空的——那代表字沒有被
    // 動過，標籤不該出現。`review` 是伺服器送的那一份投影，不是這一頁猜的。
    // 人自己改的那一筆（`correct`）同理：它也是「這一題的字被動過」，只是動手的是人，所以在同一
    // 格說出來，人不必展開 chip 就知道這一列發生過什麼。
    const applied = v === 'reset_review'
      ? machineAppliedLabel((item.candidate || {}).review)
      : (v === 'correct' ? '已修正' : '');
    return `<button class="${cls.join(' ')}" data-pos="${position}">
      <span class="mark"></span><span class="num">${esc(item.question_number)}</span>
      <span class="fig">${item.has_figure ? '▣' : ''}</span>
      <span class="grp">${isGrouped(item) ? '組' : ''}</span>
      <span class="who">${esc(applied)}</span>
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
/* `withApply` 是**題目區專屬**的開關：那顆「帶入修正」按鈕寫的是題目區的編輯框（`#editStem`
   在 `renderTextSide` 的那一份 innerHTML 裡，而委派綁定綁的是 `#textSide`）。別的區用同一支畫
   同一題時，按鈕會是一顆按了沒反應的按鈕——所以預設不畫，只有題目區自己要。 */
function findingHtml(candidate, withApply) {
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
        + (withApply ? `<button class="af-apply" data-field="${esc(c.field)}">帶入修正</button>` : '')
        + '</div>').join('')
    : '';
  // **為什麼沒有自動改**（2026-09-25，業主問「同樣情況，在block的題目，你也提出一堆建議，但是卻
  // 沒有改，為什麼會有這樣的差異」）。兩個來源，順序固定：
  //
  //  1. 代理的**反問**（`candidate.repair_ask`，伺服器 `discuss.repair_asks_by_key` 的投影，只有
  //     還沒被回答的）。那一筆的 `reason` 就是機器自己寫下的理由（「紙本判讀被閘門擋住：第二次判讀
  //     的結論是 CARE…」），照抄，不翻譯、不改寫。它原本只在討論區畫得出來。
  //  2. 沒有反問的那些：機器只改人擋過的題（這是業主自己的規則），所以**還沒被擋過的題目**它讀到
  //     也不會自己整欄改寫。這一句是**規則的敘述**，不是這一題的判斷——判斷只有一個地方做
  //     （`dispute_apply.page_read` 的閘門），畫面不重算、也不猜第二個理由。
  //
  // 機器真的改過字（`applied_kind` 是那三種修復）時兩者都不畫：那時卡片下方那一列本來就寫著
  // 「已標記：AI已修改・<哪一種改動>」，多一句「沒有自己改」會互相矛盾。
  const appliedKind = String((candidate.review || {}).applied_kind || '');
  const machineWrote = appliedKind !== '' && appliedKind !== 'withdrawn';
  const ask = candidate.repair_ask || {};
  // 退滿上限的那一題要說出**機器停手了**（而不是「它還沒讀」）：這是業主 2026-09-25 的循環終點，
  // 沒有這一句的話畫面與其他沒有反問的題一模一樣，而它們的處置完全不同。
  const attempts = Number((candidate.review || {}).attempts || 0);
  const exhausted = Boolean((candidate.review || {}).exhausted);
  const stopped = (exhausted && !machineWrote)
    ? `<div class="af-fence"><b>機器已經停手：</b>這一題試過 ${attempts} 次、每一次都被你打回，`
      + '它不再自己改這一題（放回或接受會讓它重新開始）。</div>'
    : '';
  const fence = machineWrote ? ''
    : (stopped || (ask.reason
      ? `<div class="af-fence"><b>機器沒有自己改：</b>${esc(ask.reason)}</div>`
      : (changes && !rowReviewAction(candidate)
        ? '<div class="af-fence">機器只改你擋過的題：這一題你還沒拒絕，所以它讀到了也不會自己改。</div>'
        : '')));
  // 卡身（`af-body`）自己捲動：題目區那一張卡封頂（`v2.html` 的 `#areaQuestion .ai-finding`），
  // 但紙本截圖與逐字比對是證據，不截短——往下捲就讀得到，而「模型認為…」那一行留在捲動區之外。
  return `<div class="ai-finding"><div class="af-head"><span>模型意見（${esc(label)}）</span>`
    + `<span class="af-hint">${esc(record.model || '未知模型')} · ${esc(who)}`
    + `${record.prompt_version ? ` · 提示詞版本 ${esc(record.prompt_version)}` : ''}</span></div>`
    + `<div class="af-body">${fence}${crop}${parts.join('')}${changes}</div></div>`;
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

/* 一題的**題目畫面**：題目區畫的就是這一支，原則區的右欄上面那三分之一畫的也是這一支。

   owner 2026-09-24：「那個原則區的 修理代理的反問 你好歹右邊上面三分之一顯示UI的題目畫面，
   右邊下面顯示PDF，不然我真的很難跟你對話」。反問問的是某一題的某一段文字，而那一題的長相
   本來只在題目區；人得離開原則區、回頭找那一題，才能回答自己被問什麼。所以右欄要顯示那一題
   的畫面——而「那一題的畫面」只有一個定義：題目區自己那一份。抄一份一定會漂移，漂移的那一天
   人就會在原則區讀到跟題目區不同的題目（兩邊的答案標記、圖、題組共用題幹都是靠這裡的規則畫
   的），所以畫法抽成這支回傳字串的純函式，兩邊呼叫同一支。

   `opts.editor`（題目區的編輯框）與 `opts.apply`（「帶入修正」按鈕）都預設**關**：那兩樣是
   題目區自己的狀態與 DOM（`#editStem`、委派綁在 `#textSide` 上），別的區畫出來只會是死掉的
   控制項。`opts.finding === false` 則連模型意見卡都不畫（預設**畫**，與題目區一致）：那一張卡
   在同一頁的左欄已經有一張一模一樣的（截圖、哪裡、逐欄的兩邊），而右欄上面那三分之一只有
   三分之一——被那張卡佔掉，owner 就看不到題目，那正是他要的東西。
   `opts.chrome === false` 也不畫題目區自己的那條抬頭（`side-head`＋`qnum`，預設**畫**）。原則區的
   右欄上面那三分之一只有三百多像素，而這一格自己的抬頭（`.pdf-head`）已經寫出科目、卷號、
   「第 N 題」與 key——再畫一次那條，掉到捲軸下面的就是第四個選項。`opts.prefix` 給呼叫者自己的
   id 前綴，免得同一頁出現第二個 `viewStem`。 */
function questionTextHtml(item, candidate, opts) {
  const conf = opts || {};
  const prefix = conf.prefix || '';
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
  const options = candidate.options || [];
  const flags = (item.reason_codes || []).filter(Boolean);
  const edited = metadata.review_status === 'human_corrected' || (candidate.review || {}).has_correction;
  const note = noteOf(item.candidate_key);
  // 紙本表格：讀法指出表格的那一題，表格那一段從題幹切出來，改畫紙本的截圖（見 `tableBlockHtml`）。
  // 切不出來（`split` -1）就是整段照原本畫，截圖補在後面——寧可多看到一次文字，也不要把散文切掉
  // 或畫出一個空的區塊。
  const tableCrops = tableCropRefs(candidate);
  const stem = candidate.stem || '';
  const split = tableCrops.length ? tableSplitIndex(stem, tableCrops[0].table_lines) : -1;
  // 切點在 0 是「整段題幹就是那張表」：題幹那一格留空（`（題幹空白）` 是「沒有字」的講法，
  // 這裡的字都在下面的截圖與文字版裡，不該說它空白）。
  const stemHtml = split >= 0 ? richText(stem.slice(0, split)) : richText(stem || '（題幹空白）');
  return `
    ${conf.chrome === false ? '' : `<div class="side-head">抽出文字${edited ? '（已人工修正）' : ''}<span class="hint">${flags.length ? flags.map(esc).join(' · ') : '與右側紙本對照'}</span></div>
    <div class="qnum">第 ${esc(item.question_number)} 題</div>`}
    ${disputeHtml(candidate)}
    ${conf.finding === false ? '' : findingHtml(candidate, conf.apply)}
    ${groupHtml(candidate)}
    <div class="stem" id="${prefix}viewStem">${stemHtml}</div>
    ${tableCrops.length ? tableBlockHtml(tableCrops, split >= 0 ? stem.slice(split) : '') : ''}
    ${lostGlyphHtml(candidate)}
    <div class="opts" id="${prefix}viewOpts">${options.map((option) => `
      <div class="opt${accepted.has(option.key) ? ' is-answer' : ''}">
        <span class="k">${esc(option.key)}</span>${optionCropHtml(candidate, option)}<span class="t">${richText(option.text)}</span>
      </div>`).join('')}</div>
    ${figureHtml(candidate, tableCrops)}
    ${note ? `<div class="noteShown">註記：${esc(note)}</div>` : ''}
    ${conf.editor ? `<div class="edit on" id="${prefix}editor">${editorHtml(candidate, options)}</div>` : ''}
    ${conf.editor ? '' : evidenceHtml(candidate, metadata)}`;
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
  const options = candidate.options || [];

  $('where').innerHTML = `<b>第 ${esc(item.question_number)} 題</b> · ${esc(item.category)} · ${esc(item.year)}年第${esc(item.ordinal)}次 · ${esc(item.subject)}`;
  const standing = verdictOf(item.candidate_key);
  const note = noteOf(item.candidate_key);
  // 被退回來的題目要說出**是誰退回、為什麼**，不然「AI／管線退回」只是一個標籤。
  // 原因就在清單自己帶的 `review.reset` 裡（退回事件的 notes／reset_notes 與 previous_action），
  // 不是另一支 API 才有的欄位。人按的 block／needs_review 沒有這塊：原因就是審題者自己。
  const resetEvent = (candidate.review || {}).reset || {};
  // 退回來的題目要說出**動的是哪一種**：`applied_kind` 是伺服器算出來的三分之一
  // （依紙本改字／字形替換／正規化（部首碼位）），讀同一份投影；沒有 `applied` 的（管線退回、
  // 模型提問）不會多出這一節，維持原本那一句話。
  //
  // **「機器在你標記之後把字改好了」的那些題目走的就是這一條路**（owner 2026-09-25 回報：有些
  // block 的題目其實已經被改好了，卻還顯示成阻擋，於是他以為 block 還很多）。伺服器在那 22 題上
  // （`queue_view.review_projection`：人的 `block` 被保留、`pending_reset` 存在、`applied_kind`
  // 是那一種改動）會把 `is_reset_unreviewed` 也設起來，所以這裡的 `standing` 讀成 `reset_review`
  // → 標籤「AI已修改」（2026-09-25 前叫「AI已解決」），再加上下面那一節說出機器動了哪一種字、
  // 以及他原本的標記是什麼（`resetEvent.previous_action` → 「原為：阻擋」）。實測站上
  // `藥師(一)/103/2 q29`：「已標記：AI已修改・依紙本改字（…｜原為：阻擋）」。
  //
  // 這一格沒有第二條分支可以走：`is_repair_pending` 是 `reset_waiting` 的子集，而
  // `reset_waiting` 一成立就等於 `is_reset_unreviewed`，也就是 `rowReviewAction()` 一定回
  // `reset_review`。所以「機器改好了、等你複核」在畫面上**只會**是這一種說法——想再加一句
  // 「待你複核」就是第二個說法，而兩個說同一件事的地方就是兩個可以不一致的地方。
  //
  // **撤回（`withdrawn`）不走那一句話**（owner 2026-09-25：「「還原」這種事情不是修改」）。機器把
  // 自己改錯的字收回去之後，這一列**沒有**被 AI 改過：伺服器也不再把它投影成待複核
  // （`queue_view.review_projection` 的 `reset_waiting`），所以那些題目的 `standing` 回到那個人
  // 自己的判決（實測 127 題撤回裡 123 題如此）、或回到未看（3 題）。撤回本身仍是這一列的事實，
  // 只是自己說一句、畫在後面（`#stateHint .af-withdrawn`）——一句「已標記：AI已修改・已還原（機器
  // 改錯）」會把「還原」讀成「修改」，那正是他打回的那個說法。
  const withdrawnNote = String((candidate.review || {}).applied_kind || '') === 'withdrawn'
    ? machineAppliedLabel(candidate.review) : '';
  const appliedNote = standing === 'reset_review' && !withdrawnNote
    ? machineAppliedLabel(candidate.review) : '';
  const returnedNote = standing === 'reset_review' && !withdrawnNote
    ? [resetEvent.reset_notes || resetEvent.notes,
       resetEvent.previous_action ? `原為：${LABEL[resetEvent.previous_action] || resetEvent.previous_action}` : '']
        .filter(Boolean).join('｜')
    : '';
  $('stateHint').innerHTML = [
    standing
      ? `已標記：${esc(LABEL[standing] || standing)}${appliedNote ? `・${esc(appliedNote)}` : ''}`
        + `${returnedNote ? `（${esc(returnedNote)}）` : ''}`
      : (note ? '只有註記・尚未決定' : ''),
    withdrawnNote ? `<span class="af-withdrawn">${esc(withdrawnNote)}</span>` : '',
  ].filter(Boolean).join(' ');

  // The text pane is the shared builder's output, not a second copy of it: whatever the reviewer
  // reads here is literally the same markup the 原則區 draws for the same key.
  $('textSide').innerHTML = questionTextHtml(item, candidate, { editor: S.editing, apply: true });

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

/* 紙本表格（`label: 'paper-table'` 的那一筆裁切）：題幹要讓位給它。

   owner 2026-09-24：「叫你這種文字型表格要用截圖來顯示，聽不懂嗎」。這一題的抽取結果是一串壓平的
   字（`劑型  給藥途徑  劑量（mg）  AUC (μg．h/mL) 錠劑  口服  100  40…`）：欄位之間只剩兩個空格，
   人得自己數字數才看得出哪個數字屬於哪一欄，而紙本上本來就是一張看得懂的表，機器也已經依讀法把它
   裁下來了（`crop_run_figures.py --queue` 寫進 `image_refs`，`label: 'paper-table'`）。

   所以畫法**取代**原本那一段，不是並排新增：散文照原本畫，表格那一段換成截圖，抽取到的原字收在
   截圖底下的「文字版」（原樣，不重排）。文字仍然是這一題的證據，只是不再是第一眼看到的東西。
   `data-view` 說的是元素是什麼（與範圍 chips 同一個約定），程式不看摘要上寫的字。 */
const TABLE_REF_LABEL = 'paper-table';

function tableCropRefs(candidate) {
  return (candidate.image_refs || []).filter((ref) => ref && typeof ref === 'object'
    && ref.exists !== false && ref.asset_role !== 'option-image' && ref.label === TABLE_REF_LABEL);
}

/* NFKC＋去掉空白之後的字串，以及每個字回推到**原字串**的位置（`at`）。

   兩份文字要對得起來就得先縮成同一種寫法：紙本與抽取檔的差別都在標點與空白的寫法（`（`／`(`、
   `．`／`·`、全形空白），字本身一樣。`at` 是為了切回原字串——切出來的那一段要逐字印回畫面，
   不能是我重排過的版本。 */
function foldedIndex(text) {
  const chars = [];
  const at = [];
  for (let i = 0; i < text.length; i += 1) {
    const folded = text[i].normalize('NFKC').replace(/\s+/g, '');
    for (let j = 0; j < folded.length; j += 1) {
      chars.push(folded[j]);
      at.push(i);
    }
  }
  return { text: chars.join(''), at };
}

/* 題幹在哪一個字開始是表格。

   錨是**讀法自己引的第一行**（`table_lines[0]`），比對的是 NFKC＋去空白之後的前綴：表格第一列的
   欄名在紙本與抽取檔兩邊一定一樣，差異都落在後面（實測 q065：紙本 `AUC（μg·h/mL）`、抽取檔
   `AUC (μg．h/mL)`——整行比對失敗，前 12 個字一樣）。最長的前綴先試，因為短前綴可能在散文裡
   出現；再用**最後一行**當第二個錨，它必須落在第一個錨後面，這樣才不會切在散文中間。回 -1 就是
   切不出來，呼叫者把整段當散文畫、截圖補在後面。 */
function tableSplitIndex(stem, lines) {
  if (!stem || !lines || !lines.length) return -1;
  const folded = foldedIndex(stem);
  const head = foldedIndex(lines[0]).text;
  const tail = foldedIndex(lines[lines.length - 1]).text;
  for (let length = Math.min(12, head.length); length >= 4; length -= 1) {
    const start = folded.text.indexOf(head.slice(0, length));
    if (start < 0) continue;
    if (tail && folded.text.indexOf(tail, start + length) < 0) continue;
    // 切點在 0（整段題幹就是那張表）也算：那時散文是空的，題幹那一格留空，表格那一塊帶著全部的內容。
    const at = folded.at[start];
    if (at !== undefined) return at;
  }
  return -1;
}

/* 這一張圖**是誰的**，在截圖旁邊說出來——量到的那一半才有話說。

   業主 2026-09-25：「有些題目原本沒圖卻截了上下題圖片；AI 截圖檢查只看當下這題、沒上下資訊，
   於是回報『找不到問題』。」切圖那一步（`crop_run_figures`）量了每一張圖的歸屬，而畫面上只印了
   `description`：框蓋到隔壁題的那一種，句尾帶著「（紙本這張圖還蓋到隔壁題：…，只切這一題的列）」
   ——所以它看得出來；**列量不到、歸屬無法確認的那一種（站上 414 張）沒有任何記號**，而它在畫面上
   長得跟「這一題的圖」一模一樣。標籤是這一句，事實是 `ownership`（伺服器／切圖那一步寫的）。 */
function cropOwnershipNote(ref) {
  return String((ref || {}).ownership || '') === 'unverified'
    ? '（無法確認這張圖屬於哪一題：紙本上量不到這一題的列）' : '';
}

/* One crop, drawn the same way wherever it appears. */
function cropFigureHtml(ref) {
  const caption = (ref.description || ref.label || '') + cropOwnershipNote(ref);
  return `<figure class="crop">
      <img src="${esc(fileUrl(ref.path))}" alt="${esc(ref.description || ref.raw_ref || '裁切圖')}" loading="lazy">
      <figcaption>${esc(caption)}</figcaption>
    </figure>`;
}

/* 表格那一塊：截圖在上，抽取到的原字收在下方的「文字版」。

   `flattened` 空的時候只畫截圖——那代表這一筆裁切沒有帶讀法引的行（較早的裁切），寧可只畫圖，
   也不要畫一個空的「文字版」。 */
function tableBlockHtml(refs, flattened) {
  return `<div class="paper-table" data-view="table-crop">
    ${refs.map(cropFigureHtml).join('')}
    ${flattened ? `<details class="paper-table-text" data-view="table-text">
      <summary>文字版（抽取到的表格文字，原樣）</summary>
      <div class="paper-table-raw" style="white-space:pre-wrap;margin-top:8px;font-size:13px;color:var(--muted)">${richText(flattened)}</div>
    </details>` : ''}</div>`;
}

function figureHtml(candidate, skip) {
  const already = new Set(skip || []);
  const refs = (candidate.image_refs || []).filter((ref) => ref && typeof ref === 'object'
    && ref.exists !== false && ref.asset_role !== 'option-image' && !already.has(ref));
  const optionImages = (candidate.options || []).filter((o) => o && o.image && typeof o.image === 'object' && o.image.exists !== false);
  const all = refs.concat(optionImages.map((o) => o.image));
  if (!all.length) return '';
  return `<div class="crops"><div class="crops-head">圖片（機器裁切，僅供對照）<span class="hint">${all.length} 張</span></div>
    ${all.map(cropFigureHtml).join('')}</div>`;
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

/* 把一列換成**伺服器手上那一份**。

   缺陷（2026-09-25 業主回報）：「我用帶入修正、儲存修正，它就通過了，我就找不到了」，而且
   「**改動跟真實畫面顯示是不同的**」。畫面上的字來自 `S.rows[i].candidate`，那是**開這個範圍時**
   讀進來的那一份；儲存修正只把事件寫進日誌，這一列沒有任何欄位被更新，所以按下儲存之後人看到的
   是**改動前**的字，而 `next()` 同時把他帶到下一題——他沒有任何機會對照「我改的」與「存下來的」。

   所以存完之後重讀那一列，並用 `toItem()`（開範圍時把伺服器的列變成畫面上那一列的同一支函式）
   重建它：重讀之後看到的，就是重新載入會看到的。`focusKey` 會把這一列插在回傳清單最前面
   （`focus_injected`），所以當下的範圍或 chip 不會讓它找不到——`03-area-principles.js` 的
   「叫出原題」走的是同一條路。

   回傳 false 代表讀失敗。那時**不能**假裝畫面是新的：由呼叫者說出來。 */
async function refetchRow(key) {
  const payload = await fetchAreaJson('/api/candidates', { focusKey: key });
  const fresh = payload
    ? (payload.candidates || []).find((c) => c.candidate_key === key) : null;
  if (!fresh) return false;
  // 兩份清單都要換掉：`S.rows` 是畫出來的、`S.view` 是過濾的來源，只換一份的話下一次
  // `rebuildRows()` 會把舊的那一份放回來。
  for (const list of [S.view, S.rows]) {
    const at = list.findIndex((entry) => entry.candidate_key === key);
    if (at >= 0) list[at] = toItem(fresh);
  }
  // 狀態與註解照 `loadScopeRows()` 的同一條規則重讀。`rowReviewAction` 讀的是伺服器的投影，
  // 不是這一頁的猜測；`comment` 不進 `S.verdict`，因為註解不是決定。
  const action = rowReviewAction(fresh);
  if (action && !NOTE_ACTIONS.has(action)) S.verdict.set(key, action);
  else S.verdict.delete(key);
  const note = String((fresh.review || {}).notes || '');
  if (note) S.notes.set(key, note);
  else S.notes.delete(key);
  return true;
}

/* 儲存修正。它以 append-only 的 `correct` 事件存下來（帶著改過的文字），所以人打的字可以與抽取
   器產生的一份逐字對照；它**不**等於通過——`correct` 不在 `QUESTION_READY_ACTIONS` 裡，所以這一題
   不會因此進入正式題庫，人還要另外按 `A`（確認）或 `B`（阻擋）。

   存完之後**停在這一題**，並把伺服器存下來的那一份畫出來（`refetchRow`）。從前這裡是
   `await next()`：人看不到自己剛存的東西，還被帶去下一題——業主 2026-09-25 回報的正是這一段
   （「改動跟真實畫面顯示是不同的」）。修正的價值一半在「人當下確認畫面」；把他帶走就等於取消驗收。 */
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
    // 重讀這一題，再離開編輯模式（`setEditMode(false)` 會重畫文字面），最後重畫清單：列的底色與
    // 「已修正」那一格都要跟著換。**不呼叫 `next()`**——修正之後人要看到存下來的那一份。
    const drawn = await refetchRow(item.candidate_key);
    setEditMode(false);
    renderList();
    toast(drawn
      ? `第 ${item.question_number} 題：修正已儲存，畫面以下是存下來的那一份`
      : `第 ${item.question_number} 題：修正已儲存，但重讀這一題失敗，畫面可能還是舊的`, !drawn);
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

/* ==================================================================== 五個區
   The request named four areas (首頁 / 題目審核區 / 答案審核區 / 錯題討論區); 原則區 was added as a
   fifth on 2026-09-24. They are one page with a mode switch, not five pages, because they are five
   readings of **one** queue: the answer area's eligible set is defined by the question area's
   decisions, and the discussion area reads the corrections those two produced. Five pages would
   mean five loaders of the same 198 MB
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
