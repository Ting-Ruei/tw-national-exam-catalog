/* 原則區（2026-09-24）：基本原則與修理代理的反問，獨立一頁來管理。

   使用者原文：
   「我已為原則區你會獨立做一頁UI來管理，結果你藏在錯題討論區，修理代理的反問（4 題待答）這些
   也應該放在原則區UI來管理，而且是要叫得出原題PDF讓我了解情況的」。

   三個面板：
     左 `#principleList`  計數（基本原則幾條／反問幾題待答）＋ 跳躍清單，待答的排前面
     中 `#principleMain`  兩個管理區塊：基本原則（可加可減）、修理代理的反問（可回答），
                          以及代理工作區（代理自己回報的進度）
     右 `#principlePdf`   原題：上面三分之一是**題目區自己畫的**那一題（`questionTextHtml`），
                          下面三分之二是那一題的官方題目紙本（「叫出原題」叫進來的 PDF）

   中間那兩個區塊的 HTML 是**搬過來的**，class 一個都沒有改（`.principles`／`.ph-head`／
   `.pl-list`／`.principle`／`.ptext`／`.principle-add` 與 `.repair-qs`／`.rq-list`／`.rq`／
   `.rq-q`／`.rq-a`／`.rqSave`，以及 `.agent-zone`／`.ag-*`），所以既有的 CSS 照樣生效——
   這不是第二套畫法，是同一個東西換一個地方放，錯題討論區那一份已經刪掉（兩個地方畫同一條
   原則是這一輪要移除的缺陷本身）。

   資料只有一個來源：`GET /api/discuss?limit=1`。原則與反問是 `discuss.principles_projection`／
   `repair_questions_projection` 的投影（`qbr/src/qbr/discuss.py`，一份折疊規則，有單元測試
   釘住），這一頁不讀 JSONL、也不自己折疊。`limit` 只裁候選題（這一頁不畫它們），兩個投影
   每次都是完整的；「幾題待答」只讀 `repair_questions.open_count`，不自己數。

   寫入走既有的 append-only 端點：`POST /api/principles`（add／remove）與
   `POST /api/repair-question`（answer）。兩者的回應都帶著重算過的投影，所以直接放進這一頁
   渲染的同一份狀態，再交給 `afterWrite()` 收尾——這一頁不自己發明收尾順序。

   載入序＝檔名序：這一檔在 `03-areas.js` 之前，所以外殼呼叫 `renderPrinciples()` 時它已經在了。 */

/*: 這一頁的狀態。草稿（正在打的原則、正在打的回答）**放在這裡，不是只放在 DOM**：
   `drawPrinciples()` 會重畫 `innerHTML`，草稿若只存在輸入框裡，一次重畫就沒了。 */
const P = {
  //: `/api/discuss` 的 principles 投影（`{principles, removed, count, approved_count, pending_count}`）。
  //: `approved_count`／`pending_count` 是**伺服器折疊出來的**（`discuss.principles_projection`），
  //: 不是這一頁數的：畫面上的「幾條待你核准」與提示詞讀到的那一份必須是同一次折疊。
  principles: { principles: [], removed: [], count: 0, approved_count: 0, pending_count: 0 },
  //: 同一個回應的 repair_questions 投影，`open_count` 是「幾題待答」唯一的來源。
  questions: { questions: [], open_count: 0, count: 0 },
  //: 同一個回應的 `questions`：key → 「哪一題」的五個欄位（伺服器投影 `queue_view.question_identity`）。
  //: 121 則反問的 key 長得像 `moex:114020:305:0403:1:question:q054`，看不出是哪一卷哪一題，
  //: 而這一頁一次要把它們全部印出來——所以身分跟著 `/api/discuss` 一起回來，不是每一列各問一次。
  identities: {},
  //: 同一個回應的 agent 區塊：代理工作區讀它，不另外抓一次。
  agent: {},
  //: **模型與人各自看到什麼**（2026-09-24）。key（candidate_key）→ `/api/findings` 回來的那一筆
  //: 記錄（沒有就是 `null`）。原則的 `evidence` 與反問的 `candidate_key` 都查這張表。
  evidence: new Map(),
  //: 已經抓過哪一組 key。同一組 key 不重抓——重畫（例如剛寫完一條原則）不該再問一次伺服器。
  evidenceKeys: null,
  //: 這一輪有沒有因為 key 太多只抓了前一部分（要說出來，不能讓卡片假裝沒有那一題）。
  evidenceCapped: false,
  //: 每一條原則的類似題清單：principle_id → 伺服器回的那一份（`null` ＝正在抓，`undefined` ＝還沒抓）。
  similar: new Map(),
  //: 是否正在「新增原則」輸入模式，以及打到一半的字。
  newOpen: false,
  newText: '',
  //: 回答草稿，以 `question_id` 為鍵。
  drafts: new Map(),
  //: **現在攤開的那一則反問**（`question_id`；`''`＝還沒選）。121 則反問若全部攤開，中欄是
  //: 33,206px 高的一條（2026-09-25 在站上量到），而且每一則都要下載一張截圖——畫面上一次只有
  //: 一則在回答，所以只有那一則攤開，其餘只留抬頭（題號／卷／年／次／科目＋問什麼）。
  openAsk: '',
  //: 叫過的題目，key → row。點第二次不再重抓（同一份資料在同一次 session 裡不會變）。
  rows: new Map(),
  //: 右欄現在指的題目；`''`＝沒有。`notice` 是非空時要說的話（例如沒有指定題目）。
  currentKey: '',
  notice: '',
  //: 右欄**已經畫出來**的內容代號。同一份文件不重畫，否則 iframe 會重新載入、把審題者
  //: 滾到的位置丢掉（題目區與錯題討論區都為同一件事踩過）。
  shown: null,
  //: 上一次叫題失敗的原因。
  error: '',
};

/* 一條原則在講哪一題：`evidence[0]`。策展的原則與在這一頁打的原則寫的是**同一個欄位**
   （`append_principle` 把 `candidate_key` 存成 `evidence` 的清單），所以讀的那一邊不必分兩種。 */
function principleKeyOf(principle) {
  const evidence = (principle || {}).evidence;
  if (Array.isArray(evidence) && evidence.length) return String(evidence[0] || '');
  return '';
}

//: 題目 key 很長（`moex:115090:311:0704:1:question:q001`），**完整的 key 只留在 title**；
//: 畫面上要看的是「哪一題」（見 `whereText`）。
const shortKey = (key) => String(key || '').slice(-18);

/* ---------------------------------------------------------------- 「這一題是哪一題」

   使用者回報（2026-09-25）：「看不出哪一則對應哪一題（卷／年／次／科目／第 N 題）」——畫面上
   原本只印 key 的尾段（`03:1:question:q054`），而 121 則反問每一則都長得一樣。

   五個欄位是**伺服器的投影**（`queue_view.question_identity`，跟著 `/api/discuss` 的 `questions`
   一起回來）：這一頁不從 key 猜年次科目，也不自己讀 79k 列候選題。這裡只負責把它組成
   **題目區那句話**（`#where`：`第 N 題 · 類別 · YYYY年第N次 · 科目`）——同一件事在兩個地方印
   就要是同一個寫法。沒有欄位時回空字串：**查不到就說查不到**，不拿 key 假裝是身分。 */
function identityOf(key) {
  const wanted = String(key || '');
  const row = (P.identities || {})[wanted];
  return row && typeof row === 'object' ? row : {};
}

//: 五個欄位 → 一句話。缺哪一段就少哪一段（不是補一個空的分隔號）。
function whereText(row) {
  const value = row || {};
  const field = (name) => String(value[name] || '').trim();
  const number = field('question_number');
  const year = field('year');
  const ordinal = field('ordinal');
  const sitting = year ? `${year}年${ordinal ? `第${ordinal}次` : ''}` : '';
  return [number ? `第 ${number} 題` : '', field('category'), sitting, field('subject')]
    .filter(Boolean).join(' · ');
}

/* 一列的「哪一題」，兩種排版、**同一組欄位與同一個寫法**：中欄（647px）用一句話，左欄只有
   238px 所以拆成三小段（題號／卷年次／科目）。`title` 一律放完整 key——要回報或搜尋時還是有它。 */
function whereHtml(key) {
  const wanted = String(key || '');
  if (!wanted) return '<span class="qid none">沒有指定題目</span>';
  const text = whereText(identityOf(wanted));
  if (!text) return `<span class="qid none" title="${esc(wanted)}">查不到這一題（佇列裡沒有這一筆）</span>`;
  return `<span class="qid" title="${esc(wanted)}">${esc(text)}</span>`;
}

function whereBlocksHtml(key) {
  const wanted = String(key || '');
  if (!wanted) return '<span class="qid none">沒有指定題目</span>';
  const row = identityOf(wanted);
  const text = whereText(row);
  if (!text) return `<span class="qid none" title="${esc(wanted)}">查不到這一題</span>`;
  const number = String(row.question_number || '').trim();
  const year = String(row.year || '').trim();
  const ordinal = String(row.ordinal || '').trim();
  const paper = [String(row.category || '').trim(), year ? `${year}年${ordinal ? `第${ordinal}次` : ''}` : '']
    .filter(Boolean).join(' · ');
  return `<span class="qid" title="${esc(text)}" data-where="${esc(wanted)}">`
    + `<b class="w-num">${number ? `第 ${esc(number)} 題` : '（無題號）'}</b>`
    + (paper ? `<span class="w-paper">${esc(paper)}</span>` : '')
    + `<span class="w-subject">${esc(String(row.subject || '').trim())}</span></span>`;
}

/* ---------------------------------------------------------------- 「我們兩個到底看到了什麼」

   使用者原文：「你看到認為對，我叫出PDF看不出所以然，我們還缺少了一個『在Jsonl資料庫中我們兩個
   到底看到了什麼』」。所以這一頁每一張卡（原則與反問）都畫同一塊東西：

     * `crop` 的縮圖——**模型當時看的那張紙**（走 `/file`，與題目區、錯題討論區同一條路）；
     * 逐欄的「**抽取存的** → **紙本讀成的**」——機械比對算出來的兩個讀法；
     * 模型自己寫的 `finding.where`（哪裡）。

   一個來源：`GET /api/findings?keys=…` 讀的是 `QbrAiFindingsStore`，也就是錯題討論區每一列的
   `qbr_ai_finding` 從哪裡來的那一份 store。一次問完這一頁所有 key 的記錄（key → 記錄或 null）。

   **反問的記錄本身沒有 evidence 指標**（`question_repair_questions.jsonl` 一筆只有 `candidate_key`），
   所以反問是**用 `candidate_key` join** 到同一份 finding——不是第二份資料，是同一個 key 查同一張表。 */

//: 一次能問幾個 key。伺服器的上限是 50（`FINDING_KEYS_LIMIT`）；超過就在畫面上說出來，
//: 不讓卡片安靜地少畫幾題。
const EVIDENCE_KEYS_LIMIT = 50;

function changePairsHtml(changes, withApply) {
  const rows = Array.isArray(changes) ? changes.filter(Boolean) : [];
  if (!rows.length) {
    return '<div class="af-line">紙本與抽取一致，機械比對沒有差異。</div>';
  }
  // `stored`／`page` 是整欄的文字，`from`／`to` 是機械比對的兩邊；兩個都在的時候優先讀整欄的
  // 那一組，因為那一組可以直接貼回欄位（`applyFindingChange` 讀的也是它）。
  const raw = (c) => (c.stored !== undefined && c.stored !== null ? c.stored : c.from);
  const rawTo = (c) => (c.page !== undefined && c.page !== null ? c.page : c.to);
  return '<div class="af-line"><b>機械比對（程式逐字相減）：</b></div>'
    + rows.map((c) => `<div class="af-change"><code>${esc(c.field)}</code>`
      + `<span class="from">${esc(String(raw(c) || '').slice(0, 160))}</span>`
      + '<span class="arrow">→</span>'
      + `<span class="to">${esc(String(rawTo(c) || '').slice(0, 160))}</span>`
      + (withApply ? `<button class="af-apply" data-field="${esc(c.field)}">帶入</button>` : '')
      + '</div>').join('');
}

/* 一題的證據卡。`key` 是 candidate_key；沒有 key 時說出來（一條沒有出處的原則，畫一個空白窗格
   比說出來更糟——這是這一頁既有的一條規則）。 */
function principleEvidenceHtml(key) {
  const wanted = String(key || '');
  if (!wanted) {
    return '<div class="pe none">這條沒有指定題目，所以沒有「模型看到的那張紙」可以對照。</div>';
  }
  if (!P.evidence.has(wanted)) {
    return '<div class="pe none">（這一題的模型記錄還沒有讀到；下次重畫會再讀一次。）</div>';
  }
  const record = P.evidence.get(wanted);
  if (!record) return '<div class="pe none">這一題還沒有 AI 意見——模型還沒讀過它。</div>';
  const finding = record.finding || {};
  const crop = record.crop
    ? `<figure class="af-crop"><img src="${esc(fileUrl(record.crop))}" alt="模型看到的紙本截圖"
         loading="lazy" onclick="window.open(this.src,'_blank')">
       <figcaption>模型看到的紙本截圖（${esc(record.model || '未知模型')}）</figcaption></figure>`
    : '<div class="af-line bad">這一筆意見沒有截圖，無法核對。</div>';
  const lines = [];
  if (finding.where) lines.push(`<div class="af-line"><b>模型說哪裡：</b>${esc(finding.where)}</div>`);
  return `<div class="pe">${crop}`
    + `<div class="hint">抽取存的 → 紙本讀成的（${whereHtml(wanted)}）</div>`
    + lines.join('') + changePairsHtml(record.changes, false) + '</div>';
}

/* 這一頁所有 key 的模型記錄，一次抓回來，同一組 key 只抓一次。

   為什麼不是每張卡各抓一次：`/api/candidates?focusKey=…`（「叫出原題」那條路）一次回 501 題，
   只為了讀一筆 finding 就要搬一整份候選題；而這一頁的卡可能有十幾張。 */
async function loadPrincipleEvidence() {
  const keys = [];
  const add = (value) => {
    const key = String(value || '');
    if (key && !keys.includes(key)) keys.push(key);
  };
  for (const row of (P.principles.principles || [])) {
    for (const key of (row.evidence || [])) add(key);
  }
  for (const row of (P.questions.questions || [])) add(row.candidate_key);
  P.evidenceCapped = keys.length > EVIDENCE_KEYS_LIMIT;
  const wanted = keys.slice(0, EVIDENCE_KEYS_LIMIT);
  const signature = wanted.join(',');
  if (signature === P.evidenceKeys) return;   // 同一組 key：不重抓
  P.evidenceKeys = signature;
  if (!wanted.length) return;
  const payload = await fetchAreaJson('/api/findings', { keys: signature });
  if (!payload || !payload.findings) {
    // 讀不到就**不要**記住這一組 key：下一次重畫要再試，而不是一輩子畫「沒有意見」。
    P.evidenceKeys = null;
    return;
  }
  for (const [key, record] of Object.entries(payload.findings)) P.evidence.set(key, record);
}

/* ---------------------------------------------------------------- 核准

   一條原則進了提示詞，模型就會照著它改題目文字，所以「哪幾條是人點頭過的」是一個被記錄下來的
   狀態（`approve`／`unapprove` 事件，append-only，與 add／remove 同一條流）。這一頁只負責把它
   畫出來與送出去；折疊與「提示詞只讀已核准」在伺服器（`discuss.principles_projection`／
   `approved_principles`）。

   狀態由伺服器回的**重算過的投影**決定，不是這一頁自己翻的：按下去之後畫面會說「已核准」，
   只可能是因為那筆事件真的寫進去了。 */
const APPROVE_LABEL = { yes: '已核准', no: '待你核准' };

function principleApproveHtml(principle) {
  const approved = !!(principle || {}).approved;
  const id = esc((principle || {}).principle_id);
  const who = [(principle || {}).approved_by, (principle || {}).approved_at]
    .filter(Boolean).join(' · ');
  return `<span class="pl-state${approved ? ' on' : ''}"`
    + `${approved && who ? ` title="核准：${esc(who)}"` : ''}>`
    + `${approved ? APPROVE_LABEL.yes : APPROVE_LABEL.no}</span>`
    + `<button class="${approved ? 'ghost' : 'act'} pl-approve" data-id="${id}"`
    + ` data-approved="${approved ? '1' : ''}"`
    + ` title="${approved ? '取消核准：這條原則回到待核准，不再進提示詞'
        : '核准：下一次修題／AI 審核的提示詞會帶上這條原則'}">`
    + `${approved ? '取消核准' : '核准'}</button>`;
}

/* ---------------------------------------------------------------- 類似題

   一條原則涵蓋哪些題目。清單由**伺服器**算（`GET /api/principles/similar?principle_id=…`），
   因為它要在 73k 筆 finding 上比對「同一種爭議種類 + 同一個欄位形狀」，而那 73k 筆在伺服器
   手上（`QbrAiFindingsStore`），不在瀏覽器上——在這裡掃 79k 列正是這一輪要修掉的東西。

   先列原則自己的 `evidence`（那條原則自己說它在講哪一題），再列同形狀的題目；**上限與被截掉的
   數量由伺服器回報**（`matched`／`capped`／`limit`），所以清單不會假裝它是全部。

   每一列都可以「叫出原題」：選了它就走既有的 `focusKey` 路徑（`openPrincipleQuestion`），
   **不是** `candidate_key=`——那一個參數伺服器只有 `/workflow` 認（2026-09-24 實測）。 */
function principleSimilarBody(principleId) {
  const data = P.similar.get(principleId);
  if (data === null) return '<option value="">（正在算…）</option>';
  if (!data) return '<option value="">（點一下這裡列出來）</option>';
  const rows = data.rows || [];
  if (!rows.length) return '<option value="">（這條沒有指定題目，沒有可以比的形狀）</option>';
  const label = (row) => {
    const number = row.question_number === undefined || row.question_number === null
      ? '（無題號）' : `第 ${row.question_number} 題`;
    const where = [row.subject, row.paper].filter(Boolean).join(' · ');
    const why = row.source === 'evidence' ? '原則自己指出來的' : '同形狀';
    return `${number}${where ? `　${where}` : ''}　(${why})`;
  };
  return '<option value="">選一題，右邊叫出原題…</option>'
    + rows.map((row) => `<option value="${esc(row.candidate_key)}"`
        + ` title="${esc(row.candidate_key)}">${esc(label(row))}</option>`).join('')
    + (data.capped
        ? `<option value="" disabled>（還有 ${Number(data.matched) - rows.length} 題沒列出，`
          + `上限 ${Number(data.limit) || rows.length} 題）</option>`
        : '');
}

function principleSimilarHtml(principle) {
  const id = (principle || {}).principle_id;
  const data = P.similar.get(id);
  const hint = !data ? '由伺服器在整份 finding 上比對同一種爭議與欄位形狀'
    : (data.capped
        ? `共 ${Number(data.matched)} 題，清單只列前 ${Number(data.limit) || (data.rows || []).length} 題`
        : `共 ${Number(data.matched) || 0} 題`);
  return '<div class="pl-similar">'
    + '<label>這條原則涵蓋的題目</label>'
    + `<select class="pl-similar-select" data-id="${esc(id)}" title="選一題就把它的官方紙本叫到右邊">`
    + principleSimilarBody(id) + '</select>'
    + `<span class="hint pl-similar-hint">${esc(hint)}</span></div>`;
}

/* 抓一條原則的類似題。抓過（`P.similar.has`）就不重抓：同一份資料在同一次 session 裡不會變。
   只重畫那一張卡片的 dropdown，不重畫整頁——正在打的原則與回答留在原來的位置。 */
async function loadPrincipleSimilar(principleId) {
  if (!principleId || P.similar.has(principleId)) return;
  P.similar.set(principleId, null);   // 佔位＝正在抓
  const payload = await fetchAreaJson('/api/principles/similar', { principle_id: principleId });
  if (!payload || !payload.ok) {
    P.similar.delete(principleId);
    toast(`算不出這條原則的類似題：${esc((payload && payload.error) || A.error['/api/principles/similar'] || '')}`, true);
    return;
  }
  P.similar.set(principleId, payload);
  const card = document.querySelector(`#principleMain .principle[data-id="${CSS.escape(principleId)}"]`);
  const holder = card && card.querySelector('.pl-similar');
  if (holder) holder.outerHTML = principleSimilarHtml({ principle_id: principleId });
  bindPrinciples();
}

/* 重新載入並重畫這一區。由 `renderArea`（`03-areas.js`）呼叫，寫入後由 `afterWrite()` 再叫一次。
   每次重畫都重抓：`limit=1` 的回應很小，而原則與反問是「人剛剛寫了什麼」的東西——寧可多問一次，
   不要畫一個舊的。 */
async function renderPrinciples() {
  const list = $('principleList'), main = $('principleMain'), pdf = $('principlePdf');
  if (!list || !main || !pdf) return;  // 舊的 shell 沒有這一頁：不做事，也不要讓整頁倒
  list.innerHTML = '<div class="empty">載入中…</div>';
  const payload = await fetchAreaJson('/api/discuss', { limit: 1 });
  if (!payload) {
    P.error = A.error['/api/discuss'] || '';
    list.innerHTML = `<div class="empty">讀不到原則區（${esc(P.error)}）</div>`;
    return;
  }
  P.principles = payload.principles || P.principles;
  P.questions = payload.repair_questions || P.questions;
  P.identities = payload.questions || {};
  P.agent = payload.agent || {};
  P.openAsk = chooseOpenAsk();
  P.error = '';
  // 先把「模型與人各自看到什麼」抓回來再畫：卡片上的截圖與逐欄對照是這一頁要證明的東西，
  // 先畫再補會讓它閃一下，而且一張空的證據卡比沒有卡片更糟。
  await loadPrincipleEvidence();
  drawPrinciples();
}

/* 現在攤開哪一則：**還沒選過、或選的那一則消失了**就回到第一則待答的；沒有待答的就第一則。
   選過而且還在，就留著——剛回答完一則、`afterWrite()` 重畫時，畫面不該跳到別的地方。 */
function chooseOpenAsk() {
  const questions = P.questions.questions || [];
  const ids = questions.map((row) => String(row.question_id || ''));
  if (P.openAsk && ids.includes(P.openAsk)) return P.openAsk;
  const open = questions.find((row) => row.open);
  return String((open || questions[0] || {}).question_id || '');
}

function drawPrinciples() {
  $('principleList').innerHTML = principleSideHtml();
  $('principleMain').innerHTML = principleCenterHtml();
  drawPrinciplePdf();
  bindPrinciples();
}

/* 左欄：計數 ＋ 跳躍清單。數字全部是伺服器的：`principles.count` 與 `repair_questions.open_count`
   （「幾題待答」只有這一個來源）。待回答的反問排前面——一個還沒被回答的問題，就是代理還停在
   那裡的地方。 */
function principleSideHtml() {
  const principles = P.principles.principles || [];
  const questions = P.questions.questions || [];
  const pending = questions.filter((row) => row.open);
  const answered = questions.filter((row) => !row.open);
  const count = Number(P.principles.count ?? principles.length) || 0;
  const openCount = Number(P.questions.open_count ?? pending.length) || 0;
  // 「幾條待你核准」是伺服器折疊出來的數字（`pending_count`），不是這一頁數的：提示詞讀的是
  // 同一份折疊（只有已核准的會進去），所以兩個數字必須來自同一次計算。
  const pendingPrinciples = Number(P.principles.pending_count
    ?? principles.filter((p) => !p.approved).length) || 0;
  const head = '<div class="discuss-side"><h3>原則區</h3>'
    + `<div class="row"><span>基本原則</span><b class="n">${count} 條</b></div>`
    + `<div class="row${pendingPrinciples ? ' flag' : ''}"><span>待你核准</span>`
    + `<b class="n">${pendingPrinciples} 條</b></div>`
    + `<div class="row"><span>反問</span><b class="n">${openCount} 題待答</b></div></div>`;
  const pRows = principles.map((p) => `<button class="row" data-goto="pp_${esc(p.principle_id)}"`
      + ` title="${esc(p.text)}"><span class="num">${p.approved ? '已准' : '待准'}</span>`
      + `<span class="row-body">${whereBlocksHtml(principleKeyOf(p))}`
      + `<span class="row-text">${esc(String(p.text || '').slice(0, 60))}</span></span></button>`).join('');
  // 一則反問的左欄列：**先說它是哪一題**（題號／卷／年／次／科目），再說它在問什麼。以前只有
  // 一句重複 121 次的問句與一個「待答」，等於看不出這一列跟哪一題有關（使用者 2026-09-25 回報）。
  const qRow = (row) => `<button class="row${row.open ? ' flag' : ''}" data-goto="pq_${esc(row.question_id)}"`
    + ` title="${esc([whereText(identityOf(row.candidate_key)), row.question, row.reason].filter(Boolean).join('｜'))}">`
    + `<span class="num">${row.open ? '待答' : '已答'}</span>`
    + `<span class="row-body">${whereBlocksHtml(row.candidate_key)}`
    + `<span class="row-text">${esc(String(row.question || '').slice(0, 34))}</span></span></button>`;
  const qRows = pending.concat(answered).map(qRow).join('');
  return head
    + `<div class="list-head">基本原則<b>　${count} 條</b>`
    + '<span class="hint">會被編成修題／AI 審核的提示詞</span></div>'
    + (pRows || '<div class="hint" style="padding:6px 13px">（還沒有原則）</div>')
    + `<div class="list-head">反問<b>　${openCount} 題待答</b>`
    + '<span class="hint">模型讀不懂時反問，人回答</span></div>'
    + (qRows || '<div class="hint" style="padding:6px 13px">（修理代理沒有卡住的地方）</div>');
}

/* 中欄：兩個管理區塊，加上代理自己回報的進度。

   順序是「通則 → 反問 → 代理在做什麼」：原則是人寫給模型看的約束，反問是模型停下來問人的地方，
   代理工作區是它這一輪走到哪裡。`.case` 只是卡片外框（以及既有的 `--reading-size` 讀數），
   裡面的每一塊都沿用它在錯題討論區用的 class，不新增一套。 */
function principleCenterHtml() {
  return '<div class="case" style="--reading-size:15px">'
    + principlePrinciplesHtml()
    + principleQuestionsHtml()
    + principleAgentHtml()
    + '</div>';
}

/* 原則怎麼分組（owner 2026-09-25：「原則區非常混亂」＋「新原則與舊原則邏輯重複」）。

   分組的鍵是**來源 ＋ 題材**，不是狀態：人從註解寫的一組，機器從已確認修復提案的**每一個
   `change_class` 一組**。理由是業主要看的那件事——「新原則跟舊原則，邏輯類似」——只有把講同一
   件事的兩條放在相鄰兩列才看得出來，而它們的狀態往往不同（一條已核准、一條剛被提案）。所以
   狀態用**每一列自己的籤**表示（`principleApproveHtml`），組頭再給一次計數。

   站上的實例：p6（人寫的，上下標）與 p8（機器提的，上下標）是同一件事各說一次；p8 已核准、p9／
   p10 是同一個 `change_class` 的後續提案。這一分組讓「機器提案（格式（上下標標籤））」那一組裡
   同時看得到已核准與待核准的那幾條，不必先把兩句話讀完才知道它們在講同一件事。 */
const SOURCE_LABEL = { comment_review: '人從註解寫的', feedback_learning: '機器從已確認的修復提案' };
const CHANGE_CLASS_LABEL = { format_rule: '格式（上下標標籤）', exact_ocr_rule: '字形（形近字、標點）' };
/*: 來源的顯示順序：人寫的在最前（那是核准過的判斷來源），機器的提案次之，沒有 `source` 的舊紀錄
   最後。用明確的名次而不是字串排序，否則空字串會排到最前面。 */
const SOURCE_RANK = { comment_review: 0, feedback_learning: 1 };
function principleGroupsOf(principles) {
  const groups = new Map();
  for (const p of principles) {
    const source = String(p.source || '');
    // `change_class` 只用在機器提案上：那是策展員從已確認修復量出來的分類。人寫的原則沒有這一欄，
    // 硬把它們按空字串分成一組只會多一層沒有資訊的標題。
    const changeClass = source === 'feedback_learning' ? String(p.change_class || '') : '';
    const groupKey = `${source}|${changeClass}`;
    if (!groups.has(groupKey)) groups.set(groupKey, { source, changeClass, rows: [] });
    groups.get(groupKey).rows.push(p);
  }
  const rank = (entry) => (entry.source in SOURCE_RANK ? SOURCE_RANK[entry.source] : 2);
  return [...groups.values()]
    .sort((a, b) => (rank(a) - rank(b)) || a.changeClass.localeCompare(b.changeClass)
      || a.source.localeCompare(b.source))
    .map((entry) => ({ ...entry, rows: entry.rows.slice().sort(principleRowOrder) }));
}
/* 一組裡：**已核准的在前**（它們正在改題目文字，先看到），再按寫下的時間。 */
function principleRowOrder(a, b) {
  if (Boolean(a.approved) !== Boolean(b.approved)) return a.approved ? -1 : 1;
  return String(a.created_at || '').localeCompare(String(b.created_at || ''));
}
function principleGroupHead(entry) {
  const approved = entry.rows.filter((p) => p.approved).length;
  const label = SOURCE_LABEL[entry.source] || '來源不明（舊紀錄）';
  const subject = entry.changeClass
    ? `（${CHANGE_CLASS_LABEL[entry.changeClass] || entry.changeClass}）` : '';
  return `<li class="pl-group"><span>${esc(label)}${esc(subject)}</span>`
    + `<span class="hint">${entry.rows.length} 條（已核准 ${approved}／`
    + `待你核准 ${entry.rows.length - approved}）</span></li>`;
}
function principleGroupsHtml(principles, rowHtml) {
  return principleGroupsOf(principles)
    .map((entry) => principleGroupHead(entry) + entry.rows.map(rowHtml).join(''))
    .join('');
}

/* 基本原則：可加可減、要**核准**才會進提示詞。每一條旁邊都有「叫出原題」——一條說不出它在講
   哪一題的原則，讀的人無從核對（這就是使用者要的「叫得出原題PDF讓我了解情況」）。

   卡片由四塊組成，順序就是閱讀順序：**這句話**（含它的核准狀態）→ **這條原則涵蓋哪些題目**
   （類似題 dropdown）→ **模型與人各自看到什麼**（截圖／抽取存的 → 紙本讀成的／哪裡）→ 叫出原題。
   核准的意義寫在抬頭：提示詞只讀已核准的（`approved_principles`），所以「待你核准」的原則
   目前**不會**影響任何模型輸出——這句話不寫出來，那顆按鈕看起來就只是裝飾。

   清單本身**分組**（`principleGroupsHtml`：狀態 × 來源），因為散成一長條時，講同一件事的人寫
   原則與機器提案相隔好幾列，重複看不出來。 */
function principlePrinciplesHtml() {
  const principles = P.principles.principles || [];
  const pendingCount = Number(P.principles.pending_count
    ?? principles.filter((p) => !p.approved).length) || 0;
  const rowHtml = (p) => {
    const key = principleKeyOf(p);
    // 兩行，不是一行：第一行是**這句話**（`flex:1` 撐滿），第二行才是身分與三顆按鈕。
    // 全塞在一行的話，`flex-wrap` 會讓身分那一段被壓成窄欄而折成十幾行（2026-09-25 在站上量到
    // 一張卡的 `.pl-top` 是 **1,272px**：490px 寬的卡片裡 `.ptext` 只分到 270px，`.qid` 沒有
    // 生長的份，只好自己折行）。這一條是「格式跑掉」的另一半。
    return `<li class="principle" id="pp_${esc(p.principle_id)}" data-id="${esc(p.principle_id)}">`
      + '<div class="pl-top">'
      + `<span class="ptext">${esc(p.text)}</span>`
      + '<div class="pl-meta">'
      + whereHtml(key)
      + principleApproveHtml(p)
      + `<button class="pl-open" data-key="${esc(key)}" data-id="${esc(p.principle_id)}"`
      + ' title="把這一條講的那一題的官方紙本叫到右邊">叫出原題</button>'
      + `<button class="ghost pl-remove" data-id="${esc(p.principle_id)}"
           title="移除（append-only：是加一筆 remove，不是刪掉歷史）">移除</button></div></div>`
      + principleSimilarHtml(p)
      + principleEvidenceHtml(key)
      + '</li>';
  };
  const rows = principleGroupsHtml(principles, rowHtml);
  const input = P.newOpen
    ? `<div class="principle-add"><textarea id="dpNew" placeholder="一句可以被機器遵守的原則，例如：中文詞中間不該有空格。Enter 加入、Shift+Enter 換行">${esc(P.newText)}</textarea>
       <div class="hint" id="dpHint">${P.currentKey
         ? `會記成以右邊這一題為例（${esc(whereText(identityOf(P.currentKey)) || shortKey(P.currentKey))}）`
         : '右邊沒有開著題目，這條會沒有指定題目；先叫出原題再加，就會記住那一題。'}</div>
       <button class="act" id="dpSave">加入</button>
       <button class="ghost" id="dpCancel">取消</button></div>`
    : '<button class="ghost" id="dpStart">＋ 新增原則</button>';
  return `<div class="principles"><div class="ph-head">基本原則（${principles.length}）`
    + `<span class="hint">已核准 ${Number(P.principles.approved_count ?? 0)}／待你核准 ${pendingCount}`
    + '；只有已核准的會被編成修題／AI 審核的提示詞</span></div>'
    + `<ul class="pl-list">${rows || '<li class="hint">（還沒有原則）</li>'}</ul>${input}</div>`;
}

/* 修理代理的反問：模型讀不懂時停在這裡，人回答。未回答的排最前面，而且第一眼要看得出
   「幾題待答」——那是 `open_count`（伺服器算的，不是這一頁數的）。

   每一則分成**抬頭**與**內容**：抬頭（哪一題／待回答／為什麼問／誰問的／叫出原題／展開）永遠
   看得見，內容（模型與人各自看到什麼 ＋ 回答框）只有**現在這一則**畫出來（`P.openAsk`）。
   2026-09-25 在站上量到：121 則全部攤開時中欄是 **33,206px** 高的一條，而且每一則都要下載一張
   截圖——人一次只回答一則，所以只畫那一則。點左欄的列就換這一則（`selectAsk`）。 */
function principleQuestionsHtml() {
  const questions = P.questions.questions || [];
  const ordered = questions.filter((row) => row.open).concat(questions.filter((row) => !row.open));
  const body = ordered.length ? ordered.map((row) => {
    const on = String(row.question_id) === P.openAsk;
    const mine = P.drafts.get(row.question_id);
    const answer = row.answer_text || mine || '';
    const key = String(row.candidate_key || '');
    return `<li class="rq${row.open ? ' open' : ''}${on ? ' on' : ''}" id="pq_${esc(row.question_id)}" data-id="${esc(row.question_id)}">`
      + `<div class="rq-q"><b>${row.open ? '待回答' : '已回答'}</b>`
      + ` ${whereHtml(key)}</div>`
      + '<div class="rq-line">'
      + `<span class="rq-text" title="${esc(row.question || '')}">${esc(row.question || '')}</span>`
      + `<button class="rq-open" data-key="${esc(key)}"
           title="把這一題的官方紙本叫到右邊">叫出原題</button>`
      + `<button class="ghost rq-goto" data-key="${esc(key)}"
           title="切到題目審核區，開在這一題（可以在那裡判它）">去看這一題</button>`
      + `<button class="ghost rq-toggle" data-id="${esc(row.question_id)}" data-on="${on ? '1' : ''}"
           title="${on ? '收合這一則（回答框與模型看到的紙收起來）' : '展開這一則（回答框與模型看到的紙）'}">`
      + `${on ? '收合' : '展開回答'}</button>`
      + (row.answer_text
          ? `<span class="hint">${row.answer_at
              ? `已回覆於 ${esc(String(row.answer_at).slice(5, 16))}` : '已回覆'}</span>`
          : '')
      + '</div>'
      + `<div class="rq-meta">${askMetaHtml(row)}</div>`
      // 攤開的那一則才畫：模型看到的紙與逐欄對照（`principleEvidenceHtml`）、回答框。
      + (on
          ? principleEvidenceHtml(key)
            + `<textarea class="rq-a" data-id="${esc(row.question_id)}" placeholder="回答（Enter 送出、Shift+Enter 換行）">${esc(answer)}</textarea>`
            + `<button class="act rqSave" data-id="${esc(row.question_id)}"${row.answer_text ? ' disabled' : ''}>送出回答</button>`
          : '')
      + '</li>';
  }).join('') : '<li class="hint">（修理代理沒有卡住的地方。）</li>';
  return `<div class="repair-qs"><div class="ph-head">修理代理的反問（${Number(P.questions.open_count ?? 0) || 0} 題待答）`
    + '<span class="hint">模型讀不懂時會停在這裡，而不是猜</span></div>'
    + `<ul class="rq-list">${body}</ul></div>`;
}

/* 一則反問自己的欄位（`ask`）：為什麼問、誰問的、用哪個模型、什麼時候問的。

   伺服器回的 `ask` 是**一個 dict**（以前是 Python dict 的 repr 字串，畫面上是一串看不懂的括號），
   所以這裡把它拆成欄位顯示——複核一則反問的第一個問題就是「這是誰、在什麼時候、用什麼模型問的」。
   欄位缺席就少印一段，不補空格子。 */
function askMetaHtml(row) {
  const ask = (row && row.ask && typeof row.ask === 'object') ? row.ask : {};
  const parts = [];
  if (row && row.reason) parts.push(`為什麼問：${row.reason}`);
  const who = [ask.reviewer, ask.model].filter(Boolean).join(' · ');
  if (who) parts.push(`誰問的：${who}`);
  if (ask.created_at) parts.push(`問的時間：${String(ask.created_at).slice(0, 16).replace('T', ' ')}`);
  return parts.map((part) => esc(part)).join('　·　');
}

/* 代理工作區：代理正在做什麼、做到哪裡、哪些它已經放行。

   這一塊畫的是**進度與分流**，不是問題清單：排隊中還沒做完的題數（掃描自己的紀錄）、
   指揮者的 TRUST／CARE／DOUBT 分流、以及兩個模型讀法不同的題（最該先看的一批）。
   它讀的是 `/api/discuss` 一起帶回來的 `agent`，與伺服器的回報腳本讀同一份紀錄。 */
function principleAgentHtml() {
  const a = P.agent || {};
  const tally = a.orchestration || {};
  const pending = a.pending;
  const disagreements = a.disagreements || [];
  const parts = ['TRUST', 'CARE', 'DOUBT']
    .filter((name) => tally[name])
    .map((name) => `<span class="ag-t ag-${name.toLowerCase()}">${name} ${tally[name]}</span>`)
    .join('');
  const undivided = tally['未分流']
    ? `<span class="ag-t ag-none">未分流 ${tally['未分流']}</span>` : '';
  const pendingText = pending === null || pending === undefined
    ? '（還沒跑過掃描）' : `${pending} 題`;
  return `<div class="agent-zone">`
    + `<div class="ph-head">代理工作區`
    + `<span class="hint">掃描 → 地端讀紙本 → 指揮者分流，三件事分開</span></div>`
    + `<div class="ag-row"><span>排隊中（掃到還沒做完）</span><b class="n">${esc(pendingText)}</b></div>`
    + `<div class="ag-row"><span>指揮者分流</span><b class="n">${parts || '沒有判斷'}${undivided}</b></div>`
    + (a.recent
        ? '<div class="hint">以上是最近一批：分流紀錄已經很長，只讀最新的那一截。</div>'
        : '')
    + (disagreements.length
        ? `<div class="ag-row ag-warn"><span>⚠ 兩個模型不一致</span>`
          + `<b class="n">${disagreements.length} 題（優先看）</b></div>`
          + `<ul class="ag-dis">` + disagreements.slice(0, 5).map((item) =>
              `<li>${whereHtml(item.candidate_key)}`
              + `　${esc(item.why || '')}</li>`).join('') + '</ul>'
        : '')
    + `<div class="hint">指揮者只做建議：它不會 accept、不會 block、不會改題目文字（GOV-05）。</div>`
    + `</div>`;
}

/* ---------------------------------------------------------------- 右欄：題目畫面 ＋ 原題紙本

   owner（2026-09-24）：「那個原則區的 修理代理的反問 你好歹右邊上面三分之一顯示UI的題目畫面，
   右邊下面顯示PDF，不然我真的很難跟你對話」。反問在問的是一題裡的一段文字，而那一題長什麼樣
   本來只在題目區——人得離開這一頁才看得到自己被問的是哪一題。所以右欄切成兩塊，兩塊跟著**同一個
   `focusKey`**：上面那一塊呼叫**題目區自己的** `questionTextHtml`（所以看到的是審題者看到的
   文字，不是檔案原文，也不是第二套畫法），下面那一塊是同一筆的官方**題目**紙本
   （`source_files.official_pdf || metadata.question_pdf_relative`，不是答案卷）。

   `fileUrl(path)` ＋ `#view=FitH`：與題目區、錯題討論區的同一種紙本，同一種讀法（同一組算術，
   所以三個區的紙本一樣大）。比例（上 1／下 2）寫在 `review_ui/v2.html` 的
   `.principle-pdf .qview { flex:1 }` 與 `.principle-frame { flex:2 }`。 */

function drawPrinciplePdf() {
  const pane = $('principlePdf');
  if (!pane) return;
  const token = `${P.currentKey}\u0000${P.notice}\u0000${P.error}`;
  // The frame is **not** re-created when the paper has not changed: re-assigning `src` - even to the
  // same file - reloads the viewer and throws the scroll position away. A re-render after a write
  // (a new principle) must therefore leave the iframe exactly as it is.
  if (P.shown === token) return;
  P.shown = token;
  pane.innerHTML = principlePdfHtml();
}

function principlePdfHtml() {
  if (P.notice) return `<div class="empty-area">${esc(P.notice)}</div>`;
  if (!P.currentKey) {
    return '<div class="empty-area">按一條原則或一則反問旁邊的「叫出原題」，<br>'
      + '就把那一題的官方紙本叫到這裡。</div>';
  }
  const row = P.rows.get(P.currentKey);
  if (!row) return `<div class="empty-area">叫不出這一題（${esc(P.error || '查不到這筆題目')}）。</div>`;
  // 與題目區、錯題討論區讀的是同一組欄位：官方 PDF 優先，其次才是 metadata 裡的相對路徑。
  const meta = row.metadata || {};
  const pdf = (row.source_files || {}).official_pdf || meta.question_pdf_relative;
  const subject = meta.normalized_subject_name || meta.subject_name || meta.group_name || '';
  const paper = meta.paper_id || row.paper_id || row.source_registry_key || '';
  const head = '<div class="pdf-head">'
    + `<b>${esc(subject || '（未知科目）')}</b>`
    + `${paper ? `<span>${esc(paper)}</span>` : ''}`
    + `第 ${esc(row.question_number ?? '?')} 題`
    + '<span class="spacer"></span>'
    + `<span class="pl-key" title="${esc(P.currentKey)}">${esc(P.currentKey)}</span></div>`;
  // 上面那一塊是**題目區自己畫的**那一題（同一支 `questionTextHtml`，同一個 `focusKey`）。
  // 反問問的是這一題的某一段文字，人得看著題目回答——以前他得離開原則區、回頭去找那一題。
  // 兩塊跟著同一個 key：`row` 就是 `/api/candidates?focusKey=` 回來的那一筆，所以上面的題目、
  // 下面的紙本、左邊的原則，講的是同一題。id 前綴 `pq_` 是因為同一頁已經有題目區的 `viewStem`
  // 與 `viewOpts`，兩個節點不能同名。
  // `finding: false`：模型意見卡在同一頁左欄（原則區的證據卡）已經有一張一模一樣的；`chrome: false`：
  // 題目區那條抬頭（「抽出文字」＋「第 N 題」）這一格自己已經有了（`.pdf-head`）。兩塊都只有三分之一高，
  // 少掉這些重複，四個選項才全部在第一眼裡——owner 要看的是題目。
  const question = `<div class="qview" id="principleQview">${
    questionTextHtml(row, row, { prefix: 'pq_', finding: false, chrome: false })}</div>`;
  const body = pdf
    ? `<iframe class="principle-frame" title="原題官方 PDF"
         src="${esc(fileUrl(pdf))}#view=FitH"></iframe>`
    : '<div class="empty-area">這一題沒有官方 PDF 路徑。<br>請不要憑抽出文字判斷。</div>';
  return head + question + body;
}

/* 叫出原題：`GET /api/candidates?focusKey=…` 會把那一筆**插在清單最前面**（`focus_injected`），
   目前的篩選或 `limit` 把它排除掉也一樣拿得到——所以這一頁不必先知道任何範圍。

   **參數名是 `focusKey`，不是 `candidate_key`**（2026-09-24 實測，live 佇列的 79,090 題）：
   `/api/candidates?candidate_key=<key>` 回的是**預設篩選的前 500 題**，那一筆不在裡面
   （`candidate_key` 只有 `/workflow` 認，`workflow_payload` 裡那一段），
   而 `/api/candidates?focusKey=<key>` 回 501 題且那一筆在最前面、帶 `focus_injected: true` 與
   它的 `source_files.official_pdf`。v1 的 console 用的也是 `focusKey`——那不是新發明的參數。

   沒有 key 的一律說出來：「這條沒有指定題目」——一條沒有出處的原則，畫一個空白窗格比說出來更糟。 */
async function openPrincipleQuestion(key) {
  const wanted = String(key || '');
  if (!wanted) {
    P.currentKey = '';
    P.error = '';
    P.notice = '這條沒有指定題目';
    drawPrinciplePdf();
    return;
  }
  P.notice = '';
  if (!P.rows.has(wanted)) {
    const payload = await fetchAreaJson('/api/candidates', { focusKey: wanted });
    const row = payload
      ? (payload.candidates || []).find((c) => c.candidate_key === wanted) : null;
    if (!row) {
      P.currentKey = '';
      P.error = A.error['/api/candidates'] || '查不到這筆題目';
      P.notice = `叫不出這一題（${P.error}）`;
      drawPrinciplePdf();
      return;
    }
    P.rows.set(wanted, row);
  }
  P.currentKey = wanted;
  P.error = '';
  drawPrinciplePdf();
}

/* 去看這一題：從原則區跳到**題目審核區**的那一題。

   使用者原文（2026-09-25）：「我找不到『等你對那 72 題「紙本與抽取一致但仍阻擋」給一句原則』
   的那些題目」。他手上只有「叫出原題」——那條路把官方紙本叫到**右邊的窗格**，人還是停在原則區，
   看不到那一題的判讀狀態、也不能在那裡判它；而「去找那一題」對他真正的意思是「回到我審題的地方」。

   走的**不是新路**：`S.scope` 與 `S.openQuestion` 就是題目區讀的兩個欄位（`buildScope()` 從網址
   讀的也是它們，`applyScope()` 用 `S.openQuestion` 找那列），所以這裡設定的東西與貼一個
   `#類別/年/次/科目/qNNN` 網址完全同義——同一份投影、同一條路，不新增第二套定位法。

   五個欄位取自伺服器的身分投影（`identityOf`，`queue_view.question_identity`），**不從 key 猜**；
   欄位不齊或那一卷不在類科樹裡就說出來，不亂跳（跳到別的紙本比不跳更糟：人會以為自己看的是那一題）。 */
function gotoQuestionArea(key) {
  const wanted = String(key || '');
  const row = identityOf(wanted);
  const number = String(row.question_number || '').trim();
  const category = String(row.category || '').trim();
  if (!number || !category) {
    toast('這一則沒有帶到「哪一題」的欄位，跳不過去。', true);
    return;
  }
  if (S.tree && !Object.prototype.hasOwnProperty.call(S.tree, category)) {
    toast(`佇列的類科樹裡沒有「${category}」，跳不過去。`, true);
    return;
  }
  S.scope = { category, year: String(row.year || '').trim(),
              sitting: String(row.ordinal || '').trim(), subject: String(row.subject || '').trim() };
  S.openQuestion = `q${number}`;
  showArea('question');
  // `applyScope()` 是非同步的（要讀那個範圍的列），落地之後才檢查游標是不是真的在那一題上：
  // 沒有落在這一題就要說出來。題目區的規則本來是「找不到就回到第一題還沒審的」——在這裡那正是
  // 一條安靜的錯路（畫面看起來很正常，而人以為自己站在那一題上）。
  applyScope().then(() => {
    const landed = (S.rows[S.index] || {}).question_number;
    if (String(landed || '') !== number) {
      toast(`跳到第 ${number} 題沒有成功（落點是第 ${landed === undefined ? '?' : landed} 題）。`, true);
    }
  });
}

/* ---------------------------------------------------------------- 綁定 */

function bindPrinciples() {
  const main = $('principleMain');
  if (!main) return;
  // 新增／取消／加入原則。`Enter` 加入、`Shift+Enter` 換行——與其他框同一條規則。
  const start = $('dpStart');
  if (start) start.onclick = () => { P.newOpen = true; drawPrinciples(); const box = $('dpNew'); if (box) box.focus(); };
  const cancel = $('dpCancel');
  if (cancel) cancel.onclick = () => { P.newOpen = false; P.newText = ''; drawPrinciples(); };
  const save = $('dpSave');
  if (save) save.onclick = () => addPrinciple();
  const box = $('dpNew');
  if (box) {
    box.oninput = () => { P.newText = box.value; };
    box.onkeydown = (event) => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); addPrinciple(); }
    };
  }
  for (const button of main.querySelectorAll('.pl-remove')) {
    button.onclick = () => removePrinciple(button.dataset.id);
  }
  // 核准／取消核准：一顆按鈕、兩種動作，動的是同一條 append-only 流上的一筆事件。
  for (const button of main.querySelectorAll('.pl-approve')) {
    button.onclick = () => approvePrinciple(button.dataset.id, !button.dataset.approved);
  }
  // 類似題：**點下去才算**（`mousedown` 是選單打開的那一刻；`focus` 是鍵盤走到它），
  // 所以一頁十幾條原則不會一開頁就打十幾個請求。選一題＝走既有的 `focusKey` 路徑叫出原題。
  for (const node of main.querySelectorAll('.pl-similar-select')) {
    const id = node.dataset.id;
    node.onmousedown = () => loadPrincipleSimilar(id);
    node.onfocus = () => loadPrincipleSimilar(id);
    node.onchange = () => {
      const key = node.value;
      if (key) openPrincipleQuestion(key);
    };
  }
  // 叫出原題：原則與反問都是同一顆按鈕、同一條路徑。
  for (const button of main.querySelectorAll('.pl-open, .rq-open')) {
    button.onclick = () => openPrincipleQuestion(button.dataset.key || '');
  }
  // 去看這一題：離開原則區、開在題目審核區的那一題（`gotoQuestionArea`）。
  for (const button of main.querySelectorAll('.rq-goto')) {
    button.onclick = () => gotoQuestionArea(button.dataset.key || '');
  }
  // 回答反問：Enter 送出、Shift+Enter 換行。以 `data-id`（question_id）認，不以行序。
  for (const node of main.querySelectorAll('.rq-a')) {
    node.oninput = () => P.drafts.set(node.dataset.id, node.value);
    node.onkeydown = (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        answerRepairQuestion(node.dataset.id);
      }
    };
  }
  for (const button of main.querySelectorAll('.rqSave')) {
    button.onclick = () => answerRepairQuestion(button.dataset.id);
  }
  // 展開／收合一則反問：中欄一次只攤開一則（回答框與模型看到的紙），其餘只留抬頭。
  for (const button of main.querySelectorAll('.rq-toggle')) {
    button.onclick = () => selectAsk(button.dataset.id, { toggle: true, scroll: false });
  }
  // 左欄的跳躍清單：反問那一列＝**攤開那一則**（中欄一次只有一則攤開，所以「捲過去」等於
  // 捲到一則收起來的列）；原則那一列＝捲到它，並把這一列標成 active（`03-areas.js` 的
  // `.row.active` 已經有樣式，不新增一套）。
  const list = $('principleList');
  for (const node of list.querySelectorAll('[data-goto]')) {
    node.onclick = () => {
      const goto = String(node.dataset.goto || '');
      if (goto.startsWith('pq_')) { selectAsk(goto.slice(3)); return; }
      markActiveRow(goto, true);
    };
  }
}

/* 選一則反問：攤開它、把左欄那一列標成 active、捲進視線。

   `drawPrinciples()` 會把左欄的 `innerHTML` 一起換掉，所以 active 是**重畫之後**才加上去的——
   順序反了就會標在一顆已經不存在的節點上。`toggle` 是「點同一則把它收起來」，`scroll` 在展開
   那一則自己按的時候是關的（畫面不該跳）。 */
function selectAsk(questionId, options) {
  const wanted = String(questionId || '');
  if (!wanted) return;
  const opts = options || {};
  P.openAsk = (opts.toggle && P.openAsk === wanted) ? '' : wanted;
  drawPrinciples();
  markActiveRow(`pq_${wanted}`, opts.scroll !== false);
}

//: 把左欄某一列標成 active（其餘取消），必要時捲進視線。
function markActiveRow(gotoId, scroll) {
  const list = $('principleList');
  if (!list) return;
  let node = null;
  for (const row of list.querySelectorAll('[data-goto]')) {
    const on = row.dataset.goto === gotoId;
    row.classList.toggle('active', on);
    if (on) node = row;
  }
  if (node && scroll && node.scrollIntoView) node.scrollIntoView({ block: 'center' });
}

/* ---------------------------------------------------------------- 寫入

   三條端點都是 append-only（`append_principle`／`append_repair_question`／核准那一筆事件），
   回應都帶著**重算過的投影**：放進這一頁渲染的同一份狀態，然後交給 `afterWrite()`——寫入後的
   收尾只有那一個入口。

   投影的形狀只有一份（`principlesFrom`）：寫入的回應與 `/api/discuss` 的 `principles` 區塊是
   同一份資料，抄三遍就是三個可以不一致的地方。 */
function principlesFrom(data) {
  const rows = data.principles || [];
  return { principles: rows, removed: data.removed || [],
           count: Number(data.count ?? rows.length),
           approved_count: Number(data.approved_count
             ?? rows.filter((p) => p.approved).length),
           pending_count: Number(data.pending_count
             ?? rows.filter((p) => !p.approved).length) };
}

/* 核准一條原則／取消核准：同一顆按鈕的兩個方向，送的是 `approve`／`unapprove`。
   事件寫進同一條 `question_review_principles.jsonl`（append-only，不重寫那筆 add）。 */
async function approvePrinciple(principleId, approve) {
  if (!principleId) return;
  try {
    const response = await fetch('/api/principles', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: approve ? 'approve' : 'unapprove',
        principle_id: principleId, reviewer: 'local',
      }),
    });
    const data = await response.json().catch(() => ({ ok: false, error: '回應不是 JSON' }));
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    P.principles = principlesFrom(data);
    toast(approve
      ? '已核准；下一次修題／AI 審核的提示詞會帶上這一條'
      : '已取消核准；這一條回到待核准，不再進提示詞');
    await afterWrite();
  } catch (error) {
    toast(`核准失敗：${error.message || error}`, true);
  }
}

/* 新增一條基本原則。右欄正開著某一題時，把那一題的 `candidate_key` 一起送出去，讓這條原則
   **自己說得出它在講哪一題**（伺服器存成 `evidence`，與策展那條路同一個欄位名）。 */
async function addPrinciple() {
  const box = $('dpNew');
  const text = (box ? box.value : P.newText).trim();
  if (!text) { toast('原則是空的', true); return; }
  const body = { action: 'add', text, reviewer: 'local' };
  if (P.currentKey) body.candidate_key = P.currentKey;
  try {
    const response = await fetch('/api/principles', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({ ok: false, error: '回應不是 JSON' }));
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    P.principles = principlesFrom(data);
    P.newOpen = false;
    P.newText = '';
    toast(P.currentKey
      ? `原則已加入（以 ${whereText(identityOf(P.currentKey)) || shortKey(P.currentKey)} 為例）；核准之後下一次修題才會帶進提示詞`
      : '原則已加入；核准之後下一次修題才會帶進提示詞');
    await afterWrite();
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
    const data = await response.json().catch(() => ({ ok: false, error: '回應不是 JSON' }));
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    P.principles = principlesFrom(data);
    toast('原則已移除（歷史留著，只是不再生效）');
    await afterWrite();
  } catch (error) {
    toast(`移除失敗：${error.message || error}`, true);
  }
}

/* 回答修理代理的反問。 */
async function answerRepairQuestion(questionId) {
  if (!questionId) return;
  const box = document.querySelector(`#principleMain .rq-a[data-id="${CSS.escape(questionId)}"]`);
  const answer = (box ? box.value : (P.drafts.get(questionId) || '')).trim();
  if (!answer) { toast('回答是空的', true); return; }
  try {
    const response = await fetch('/api/repair-question', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'answer', question_id: questionId, answer, reviewer: 'local' }),
    });
    const data = await response.json().catch(() => ({ ok: false, error: '回應不是 JSON' }));
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    P.questions = { questions: data.questions || [],
                    open_count: Number(data.open_count ?? 0),
                    count: Number(data.count ?? (data.questions || []).length) };
    P.drafts.delete(questionId);
    // 回答完就換下一則**待答**的：這一區是佇列，人的動作是一則一則答完。沒有下一則時留在原地
    // （剛寫下的回答還留在眼前）。`openAsk` 只在畫面上換一則，不動任何紀錄。
    const next = (P.questions.questions || []).find((row) => row.open
      && String(row.question_id) !== String(questionId));
    if (next) P.openAsk = String(next.question_id);
    toast('已回答；代理下一輪會讀到');
    await afterWrite();
  } catch (error) {
    toast(`回答失敗：${error.message || error}`, true);
  }
}
