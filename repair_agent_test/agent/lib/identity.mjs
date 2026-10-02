/**
 * Who the agent is, and what it remembers.
 *
 * The system prompt is composed fresh each run from three places, and that is the point:
 *
 *   1. `ROLE`      — the task's non-negotiables, in code, because they are a contract.
 *   2. `LESSONS`   — what earlier runs learned about **this subject**, read from disk each run.
 *   3. `promptRules`— the designer's standing rules from the rules house (`agent/rules/rules.json`),
 *
 * Two failure modes this shape is built to avoid:
 *
 *   **A lesson that never reaches the prompt.** The project already had one of these: the old
 *   `repair_agent.py` passed `learned=None` into its own prompt builder, so the "learning" loop
 *   could not have worked even if it had run — and it never ran (zero callers). Read at prompt
 *   time, so a lesson written by run N is in run N+1 by construction.
 *
 *   **A prompt that is not the prompt.** `confirm_dispute.transcribe_system` exists precisely so
 *   the text sent and the text recorded cannot disagree. The same rule applies here: this file is
 *   the only place the system prompt is built, and `agent.mjs` records what it returned.
 */

import { readFileSync, existsSync, appendFileSync, mkdirSync, renameSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promptRules } from "./rules.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
export const AGENT_DIR = join(HERE, "..");
export const STORE_DIR = process.env.REPAIR_AGENT_STORE || join(AGENT_DIR, "store");
const LESSONS_PATH = join(STORE_DIR, "lessons.jsonl");

/**
 * Which local engine is the agent's own brain — the one whose vision it looks through with `read`.
 *
 * Designer's ruling 2026-09-29:「1 換 occamy（腦與眼同一顆）」. It is declared here, in the file that
 * describes *who the agent is*, because two other decisions are derived from it: `session.mjs` builds
 * the Pi model from it, and `read_page` uses it to decide whether a reading is a **second opinion**
 * or just the same model reading the page again. Written in one place, read in both — the alternative
 * is the pair of tables that can disagree, which this project has already paid for once.
 */
export const BRAIN_ENGINE = process.env.REPAIR_AGENT_ENGINE || "occamy-6bit";

/**
 * Which version of this agent's prompt a judgement was made under.
 *
 * The discipline: bump when the prompt's meaning changes (identity text, task, rules), not on every
 * phrasing touch-up — what it labels is「這一判讀是哪一代提示詞下做的」. Declared next to the
 * prompt's own words; `agent.mjs` and the chat box read it from here, and `bridge.py` writes
 * whatever value the caller handed it — no second table.
 */
export const PROMPT_VERSION = "repair-agent-2026-09-30-rules-house+compress-2";

/**
 * The task. Every line here is a constraint someone measured or asked for, not a style choice.
 */
export const ROLE = `你是一個**題目修理代理**。你的工作是看一份台灣國家考試題目，判斷它的**文字抽取**對不對。

## 你的位置
- **你是指揮者**：你決定看哪一題、要不要看紙本、信不信判讀。地端視覺模型是你的**眼睛**。
- **你自己看得到圖。** \`read\` 讀 \`.png\`／\`.jpg\` 時，**圖會真的送進你的模型**——
  **你是有視覺能力的模型**（\`occamy-1.0-6bit-xl-mlx\`，6-bit 含 vision；2026-09-29 設計者裁定
  腦與眼同一顆）。
  裁好圖之後直接 \`read\` 它，你就看到了。
- \`read_page\` 是**逐字轉錄工具**：它裁圖、把圖交給指定的地端視覺引擎，回傳逐字判讀與
  「抽取值 vs 紙本」的差異。**看引擎是不是你自己那一顆**（回傳裡的 \`independent\` 會說）：
  - \`independent: false\`（預設，引擎＝你自己的引擎）→ **那是「再看一次」，不是第二個意見**。
    同一顆模型重讀同一張圖幾乎一樣（實測逐欄位翻轉 0/278），所以它不能拿來當互相驗證。
  - \`independent: true\`（\`engine\` 指定另一顆，例：\`mtplx-35b\`）→ **這才是獨立證據**。
    實測兩顆引擎逐欄位只有 **37.8%** 重疊，至少一個對 **63.3%**、單一最好 **53.3%**。
    **重要的題目才用**（成本是兩倍）。
  - 要**自己看**、判斷圖對不對、範圍對不對 → \`read\` 你的裁片。
- 你有 \`bash\`／\`grep\` 可以讀這個專案的**文字**（JSONL、程式碼、日誌），
  但**判讀一定走那四個工具**。**健康的軌跡不含 \`bash\`**；出現 \`bash\` 幾乎都是繞路。

## 鐵則（違反就是做錯）
1. **紙本才是真相，不是抽取值。** 抽取值與紙本不一致時，預設是抽取值錯。要改的是抽取值。
2. **只看格式，不看語意。** 你要找的是：字的差異（異體字、繁簡、上下標、標點）、
   選項 A B C D 齊不齊、圖片在不在、對不對得上、文字有沒有被截斷或重複。
   **不要**評論「這題答案合不合理」「這個選項在醫學上對不對」——那不是你的工作，
   而且那是幻覺的來源。設計者說得很清楚：「只看格式問題，不要語意幻覺」。

   **\`rating\` 只回答一件事：抽取值與紙本一不一致。** 你看不出選項上的化學結構是什麼，
   所以**不能**因為「我覺得答案應該是 D」而把 rating 打成 down——官方答案不是你推翻的對象。
   你覺得答案可疑、但抽取與紙本一致時：**rating 還是 up**，把那個懷疑寫在 \`reason\` 裡
   給設計者看就好。理由見下面「一個真實的翻車」。
3. **每一題都要讀完整**：題幹、A B C D、答案、這一題的圖。不要只看「被動過的欄位」——
   沒被動過的欄位也可能有錯。
4. **圖片是重點。** 這一題有圖，就要確認：圖有沒有被裁進這一題（歸屬）、框到的範圍對不對
   （有些圖被切成一條細縫）。**不要**只看文字就說「沒問題」。
5. **不確定就說不確定。** \`read_page\` 回 \`complaint\` 就是模型沒讀出可解析的東西；
   引擎回不同答案也是訊息。把不確定寫進 \`reason\`，不要猜一個答案填滿它。
6. **絕對不可以寫人工審核紀錄。** 你的判斷只寫進 \`record_judgement\`（實驗自己的檔）。
   你不能冒充人類審核者，也不能說「我通過了這題」——那是設計者的權力。

## 怎麼做一輪
1. 拿到題目的 \`candidate_key\`（用 \`find_question\`，不要自己拼）。
2. \`get_question\` 讀**完整**這一題——先看抽取值長什麼樣、有沒有圖、人有沒有留話。
   **\`prior_judgements\` 裡 \`source: designer\` 的句子最優先看**：那是設計者已經指出
   他覺得哪裡錯了，你的工作就是去看那件事是不是真的，而不是自己另找一個問題。
   自己以前判過的也在同一個列表裡。
   **設計者寫的字在 \`notes\` 欄，不在 \`reason\`。** \`human_events\` 每一筆都有 \`action\`
   （\`accept\`／\`block\`／\`comment\`／\`correct\`…），**有 \`notes\` 才是他說了什麼**。例如
   「答案沒進去」、「表格應該用截圖的」、「上下標修復：…請重新確認」。**先把 \`notes\` 讀完再動手**：
   他留話通常就是叫你去看那一件事。**不要**只看 \`action\`——\`action\` 只說他按了哪一顆鈕。
3. 有圖、或抽取值可疑、或人留過話 → \`read_page\` 讓地端模型看紙本。
   重要的題目**兩個引擎都看**：預設那一顆（＝你自己，\`read_page\` 不給 \`engine\`）看一次，
   再指定**另一顆**（\`mtplx-35b\`）看一次——它們錯的地方不一樣（逐欄位只重疊 37.8%）。
4. 比對「抽取值 vs 紙本」。指出**具體哪個字／哪個欄位／哪張圖**不同。
5. \`record_judgement\` 寫下結論與**依據**。
   **結論是具體修法時（例：某字改成 X、在某處補「如下圖」、某圖該插進某題），
   接著用 \`propose_repair\` 把它寫成草案**——fix／insert／basis 三欄寫實際內容，
   看過的裁片就帶 \`crop\`。草案是**提議**，不動任何正式檔；核准是設計者在沙盒 UI 按的。
   只說不做（「應該補如下圖」卻不 propose）＝只交了一半。
6. 如果這題揭露了一個**會再發生**的觀察（例：這個引擎系統性把某字讀錯、這類題的圖常被切細縫），
   再用 \`remember_lesson\` 記一句話。**先問：這句話對其他題也成立嗎？** 不成立就別寫——
   你開頭讀到的「學到的事」就是這樣累積來的。字元替換由 \`read_page\` 自動記下，不必重複。

## 講話方式
- 簡短、具體、可查證。說「第 3 個字 螢光／熒光 不同」，不要說「這題有問題」。
- 有把握才下結論；沒把握就說「引擎 A 說 X、B 說 Y，需要人看」。

## 一個真實的翻車（你一定要知道，這是你自己發生過的）

\`1152_藥師(一)_藥學(一)\` **q042**（Eteplirsen 的骨架）——同一題、同一個你、跑四次，
**結論換了三次**：

| 第幾次 | 當時看到什麼 | 你判 |
|---|---|---|
| 1 | 裁片只有選項 A（B/C/D 空白） | up |
| — | 設計者從 UI 寫下「四張結構式都要在裁片裡」 | （人的話） |
| 2 | 裁片修好了，四張結構式全到齊 | up |
| 3 | 同一組四張圖 | **down（說答案應為 D）** |

第 3 次的理由是：

> 「Eteplirsen 的真實骨架是 2'-O-methyl ribose ＋ phosphorothioate，對應選項 D」

**這件事是錯的。** Eteplirsen（Exondys 51）是 **PMO（phosphorodiamidate morpholino
oligomer）**：嗎啉環 ＋ 磷醯二胺鍵——**就是選項 B**。2'-MOE ＋ phosphorothioate 是
**mipomersen（Kynamro）**，另一顆藥。官方答案 **B 是對的**。

### 這一次翻車的教訓（比 bug 本身重要）

1. **圖修好了，判讀反而變壞。** 第 2 次看到四張圖，判 up；第 3 次看到同一組圖，判 down。
   差別不在資料，在**模型決定開始推理化學**。
   → 你拿到越多可看的東西，越容易忍不住「用眼睛去解題」。那正是設計者說「不要語意幻覺」的原因。
2. **文字 diff 為空時最危險。** 選項全是圖、\`pipeline\` 文字是 \`""\`、紙本也讀成 \`""\`
   → 格式上「一致」，你就沒事了。但你會把「一致」讀成「沒東西可做」，然後去找別的
   事情做——找到的就是「答案好像怪怪的」。**找不到格式問題就回報「沒有格式問題」。**
3. **兩次跑會給相反答案，所以「非決定性」不是抽象風險。** 這種題（選項是圖、答案是專業
   知識）**必須由人看**。你要做的是把它**標出來**，不是替人下結論。
`;

/**
 * Read the lessons written by earlier runs. Never throws on a missing file: a brand new agent
 * with no lessons is a normal state, and a crash there would make the first run impossible.
 *
 * Returns **all** lessons sorted by how often they were seen (a habit observed twenty times is a
 * better prior than one seen once). No `limit` here on purpose: `remember` deduplicates against
 * this list, and a cap would silently let a duplicate through once the list grew past it. The
 * prompt applies its own cap where a cap is actually wanted.
 *
 * `subject` narrows the read, but **the prompt no longer passes it** — see `systemPrompt`.
 */
export function lessons({ subject = "" } = {}) {
  if (!existsSync(LESSONS_PATH)) return [];
  const out = [];
  for (const line of readFileSync(LESSONS_PATH, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (subject && row.subject && !row.subject.includes(subject)) continue;
      out.push(row);
    } catch {
      // A malformed line is skipped rather than fatal: one bad append must not cost the agent
      // every lesson it ever learned. It is counted nowhere, which is deliberate — see below.
    }
  }
  return out.sort((left, right) => (right.count || 1) - (left.count || 1));
}

/**
 * Append one lesson (append-only; a run may add but never rewrite).
 *
 * `subject` is stored so lessons can be recalled per subject later. The designer's expectation
 * is that different subjects have different habitual errors — 醫事檢驗師's are not 藥師's — so a
 * lesson that cannot say which subject it came from is a lesson the next subject must re-learn.
 */
export function remember({ subject = "", kind, text, evidence = "", key = "" }) {
  mkdirSync(STORE_DIR, { recursive: true });

  // Deduplicate by (subject, kind, text). The same observation recurs across a whole subject's
  // paper — occamy reads 螢 as 熒 in many questions — and a lesson list with the same sentence
  // twenty times is a list the next run learns nothing extra from. **The first occurrence keeps
  // its evidence**; later ones are counted, because "this happened 20 times" is itself the signal
  // that it is a subject-wide habit rather than one question's accident.
  const existing = lessons({ subject });
  const duplicate = existing.find((row) => row.kind === kind && row.text === text);
  if (duplicate) {
    const bumped = { ...duplicate, count: (duplicate.count || 1) + 1, last_key: key };
    rewriteLessons((rows) =>
      rows.map((row) => (row.kind === kind && row.text === text && row.subject === subject ? bumped : row))
    );
    return bumped;
  }

  const record = {
    at: new Date().toISOString(),
    subject,
    kind,
    text,
    evidence,
    key,
    count: 1,
  };
  appendFileSync(LESSONS_PATH, JSON.stringify(record) + "\n", "utf8");
  return record;
}

/**
 * Rewrite the lesson store for a count bump.
 *
 * This is the one file the agent rewrites rather than appends, and it is deliberate: a lesson's
 * `count` is a property of the lesson, not a new event. The append-only rule protects **records of
 * what happened** (human events, findings, judgements) — those are never rewritten here. A lesson
 * count is the agent's own working note, and it says so by living in its own file.
 */
function rewriteLessons(transform) {
  let rows = [];
  try {
    rows = readFileSync(LESSONS_PATH, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
  } catch {
    rows = [];
  }
  const next = transform(rows);
  mkdirSync(STORE_DIR, { recursive: true });
  appendFileSync(LESSONS_PATH + ".tmp", next.map((r) => JSON.stringify(r)).join("\n") + "\n", "utf8");
  renameSync(LESSONS_PATH + ".tmp", LESSONS_PATH);
}

/**
 * Turn a `read_page` diff into lessons. Mechanical: every lesson is a character pair the vision
 * model actually read, so nothing here can be a hallucination.
 *
 * This exists because the alternative — hoping the agent remembers to call `remember_lesson` —
 * reproduces the old agent's failure exactly (`learned=None`: the machinery existed, nothing
 * filled it). The measured kind is written without the model's cooperation.
 */
export function lessonsFromDiff({ subject = "", engine = "", key = "", diff = {} }) {
  const written = [];
  const stem = diff?.stem_differs;
  if (stem?.pipeline && stem?.page) {
    const pair = firstCharacterDifference(stem.pipeline, stem.page);
    if (pair) {
      written.push(
        remember({
          subject,
          kind: "character-substitution",
          text: `${engine} 讀紙本時把「${pair.pipeline}」讀成「${pair.page}」`,
          evidence: `題幹第 ${pair.index + 1} 個字；抽取值 vs 紙本`,
          key,
        })
      );
    }
  }
  return written;
}

/**
 * The first position where two readings differ, as a character pair.
 *
 * Only the *first* difference is taken: a diff list would be long, and the useful lesson is "this
 * engine substitutes this character", which one example establishes. The count in `remember`
 * supplies the frequency separately.
 */
function firstCharacterDifference(left, right) {
  const max = Math.min(left.length, right.length);
  for (let index = 0; index < max; index += 1) {
    if (left[index] !== right[index]) {
      return { index, pipeline: left[index], page: right[index] };
    }
  }
  return null;
}

/**
 * 2026-09-30 (§9.11-7, rules house): the designer's standing rules come from
 * `agent/rules/rules.json` — the single authority — via `rules.mjs::promptRules()`, which sorts by
 * most recent recorded hit and cuts at the injection budget. The old stream read (`identity.mjs`'s
 * own `principles()` walking to `qbr/data/review-queues/live/review-ui/`) was retired with the
 * import of its 19 usable rows; new designer rules arrive in that stream as an **inbox** and reach
 * the prompt only through the approved import. Stream rows are not injected by themselves, the
 * same way candidates never become packages without the gate.
 *
 * The failure that shaped both readers: measured 2026-09-28, the stream read derived its path
 * three levels up from `STORE_DIR`, so redirecting the store (every test run, per `run_tests.sh`)
 * silently left the prompt with **0 of the designer's 19 principles** — no error, a shorter prompt
 * that still built. The house path is derived from the module's own directory, never from
 * `STORE_DIR`, and a broken house now throws instead of going quiet.
 *
 * The prompt shows each rule's `principle_id` in square brackets: `record_rule_hit` takes that id,
 * so the label the agent reads is exactly the label it records — one naming, both places.
 */

/**
 * Compose the run's system prompt. Called once per run; the result is what gets recorded, so the
 * record and the send cannot disagree.
 */
export function systemPrompt() {
  const parts = [ROLE];

  // **Every lesson, not only the current subject's.** The run is handed a question key, not a
  // subject, so a subject-filtered prompt would load nothing whenever the operator did not pass
  // `REPAIR_AGENT_SUBJECT` — the same shape of failure as `repair_agent.py`'s `learned=None`
  // ("carrying is automatic — no flag to forget"). Each line is labelled instead, which is the
  // honest version: an engine-wide substitution is visibly engine-wide, and a subject's figure
  // habit is visibly that subject's, so the agent can weigh them rather than inherit a silent
  // prior from a different exam.
  const learned = lessons().slice(0, 40);
  if (learned.length) {
    parts.push(
      "\n## 你（或前幾輪的你）學到的事\n" +
        "以下是先前判讀留下的教訓，**依出現次數排序**。它們是觀察，不是鐵則——" +
        "如果這一題看起來和它們相反，以你看見的紙本為準，並把新的發現記下來。\n" +
        "標「[引擎]」的是引擎層級的行為，任何科目都適用；標科目的是那個科目的習慣。\n" +
        learned
          .map((row) => {
            const where = row.subject ? `[${row.subject}]` : "[引擎]";
            const times = (row.count || 1) > 1 ? `（已見 ${row.count} 次）` : "";
            const why = row.evidence ? `（依據：${row.evidence}）` : "";
            return `- ${where} ${row.text}${times}${why}`;
          })
          .join("\n")
    );
  }

  const rules = promptRules();
  if (rules.ordered.length) {
    parts.push(
      "\n## 設計者訂的基本原則（每次都必須遵守）\n" +
        "方括號是規則編號——哪一條原則幫你看到或決定了什麼，用 `record_rule_hit` 記它的編號" +
        "（命中率是規則淘汰的證據；沒用到就不要記，亂記會讓好規則被誤殺）。\n" +
        rules.ordered.map((rule) => `- [${rule.id}] ${rule.text}`).join("\n")
    );
  }

  return parts.join("\n");
}

/** Where the agent writes its own narrative log (append-only), separate from judgements. */
export const LOG_PATH = join(STORE_DIR, "agent.log.jsonl");
