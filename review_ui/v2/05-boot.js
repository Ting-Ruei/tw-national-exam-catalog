/* `boot()`：開啟時讀取整份佇列（本次 line，限）；建 `S.rows`/`S.byKey`；`invalidateAreas` 清時重讀快取；`(PAPER` 端點：本機 `qbr` 自包含（pilot 12）；`/api/*`；`/api/health`。本機 `qbr`：`answer` 直 `A.ROWS`（pilot 自包含 4 字串）與 `PAPER`（pilot PAPER）。（實测 PAPER：PAPER；PAPER：500。） */
/* ---------------------------------------------------------------- boot */
async function boot() {
  try {
    // The taxonomy and the queue's own counts come from `queue_index.json`, which is 659 KB and
    // 0.07 s. This used to also call `/api/workflow` here, which took **2.9 s on every page load**
    // to return a 2,000-row window of `queue_items` - a window the list then used as its source of
    // per-question review state. Both halves of that were wrong: the cost was paid on every load,
    // and the window is capped, so 77,090 of the 79,090 questions had no state in it and a
    // reviewed question showed as 未看 after a reload. Every field the list needs is on the row
    // that `applyScope()` fetches, so the call is gone rather than made smaller.
    let index = null;
    try {
      const indexResponse = await fetch('/api/queue_index', { cache: 'no-store' });
      if (indexResponse.ok) index = await indexResponse.json();
    } catch (error) { index = null; }
    const tree = index && index.taxonomy;
    if (!tree) throw new Error('這個佇列沒有 queue_index.json，無法建立分類');
    buildScope(tree);
  } catch (error) {
    $('textSide').innerHTML = `<div class="empty">無法讀取佇列：${esc(error.message || error)}</div>`;
  }
}

$('actAccept').onclick = () => decide('accept');
$('actFix').onclick = () => setEditMode(!S.editing);
$('actSave').onclick = () => saveCorrection();
$('actHold').onclick = () => decide('needs_review');
$('actBlock').onclick = () => decide('block');
$('actNote').onclick = () => toggleNote();
$('reasonText').addEventListener('keydown', noteKeydown);
$('btnFirst').onclick = () => go(0);
$('btnPrev').onclick = () => go(S.index - 1);
$('btnNext').onclick = () => go(S.index + 1);
$('btnLast').onclick = () => go(S.rows.length - 1);

/* W/S walk the paper so the left hand never leaves the keyboard; the right hand stays on
   the mouse for scrolling the two panes. */
document.addEventListener('keydown', (event) => {
  // The question area's shortcuts belong to the question area. `w`/`s`/`a`/`b` are ordinary letters
  // in the 錯題討論區 and in an answer note, and firing a decision there would write a review event
  // from a keystroke the reviewer meant as text.
  if (A.area === 'discuss') {
    // 三欄：左清單（同 S.rows 的序）｜中資訊（1..6 選項、7 註解、8 紙本）｜右 PDF。
    // 打字時 1..8 送給框，不算按號；Enter 儲存（由框上的 listener），Shift+Enter 換行。
    if (event.target.tagName === 'TEXTAREA' || event.target.tagName === 'INPUT') return;
    const k = (event.key || '').toLowerCase();
    if (k === 'w') { D.index = Math.max(0, D.index - 1); renderDiscuss(); event.preventDefault(); }
    else if (k === 's') { D.index = Math.min(Math.max(0, D.rows.length - 1), D.index + 1);
      renderDiscuss(); event.preventDefault(); }
    else if (k === 'e') { saveDiscuss(D.rows[D.index]); event.preventDefault(); }
    else if (/^[1-8]$/.test(k)) { focusDiscuss(Number(k)); event.preventDefault(); }
    return;
  }
  if (A.area !== 'question') return;
  if (event.target.tagName === 'TEXTAREA' || event.target.tagName === 'INPUT') {
    // `Esc` closes the editor and the note box. `Enter` saves a note - handled by the box's own
    // `keydown` listener (`noteKeydown`), not here, because this handler must not also prevent the
    // default when the reviewer is typing `Enter` inside the 修正 editor.
    if (event.key === 'Escape') { $('actFix').focus(); setEditMode(false); closeNote(); }
    return;
  }
  const key = event.key.toLowerCase();
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const map = { w: 'prev', s: 'next', a: 'accept', r: 'needs_review', b: 'block', e: 'fix', c: 'note' };
  const hit = map[key];
  if (!hit) return;
  event.preventDefault();
  if (hit === 'prev') go(S.index - 1);
  else if (hit === 'next') go(S.index + 1);
  else if (hit === 'fix') S.editing ? saveCorrection() : setEditMode(true);
  else if (hit === 'note') toggleNote();
  else decide(hit);
});


boot().then(() => showArea(areaFromHash(), { push: false }));
