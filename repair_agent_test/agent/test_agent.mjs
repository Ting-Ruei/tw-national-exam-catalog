/**
 * The agent's contracts, each with a negative control.
 *
 * Run: `node --test test_agent.mjs`
 *
 * The project's rule is that **every check ships with the case that must fail on the old
 * behaviour**. These are the four claims the agent makes about itself, and each test below exists
 * because someone could make the claim false without noticing:
 *
 *   1. Pi is the substrate, and the local engines are registered as Pi providers derived from
 *      `engines.py` — not a second table that can drift.
 *   2. The learning loop is *closed*: what run N measures is in run N+1's prompt. The old agent
 *      failed exactly here (`learned=None`), so this is the test that matters most.
 *   3. A lesson belongs to its subject. Subjects have their own habitual errors, so a lesson
 *      leaking across subjects is not harmless — it is a false prior for a different exam.
 *   4. `record_judgement` writes the sandbox store and never a human review stream.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync, rmSync, mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

// The store path is read at module load, so it is redirected before the import.
const SCRATCH = mkdtempSync(join(tmpdir(), "repair-agent-test-"));
process.env.REPAIR_AGENT_STORE = SCRATCH;

const identity = await import("./lib/identity.mjs");
const { Type } = await import("typebox");
const { toolsFor, PATHS } = await import("./lib/tools.mjs");
const AGENT_DIR = PATHS.AGENT_DIR;

const MICRO = "微生物學與臨床微生物學（包括細菌與黴菌）";
const PHARM = "藥學(一)(包括藥理學與藥物化學)";

/**
 * A Python source excerpt with docstrings and comments removed.
 *
 * Needed because the honest way to document "this deliberately does *not* read file X" is to name
 * file X. A search over the whole function then finds it and reports the opposite of the truth —
 * the third time in this file that a check would have lied for exactly this reason.
 */
function stripDocstringsAndComments(source) {
  let out = "";
  let inDocstring = false;
  for (const rawLine of source.split("\n")) {
    let line = rawLine;
    const quotes = (line.match(/"""/g) || []).length;
    if (inDocstring) {
      if (quotes % 2 === 1) inDocstring = false;
      continue;
    }
    if (quotes % 2 === 1) {
      inDocstring = true;
      line = line.slice(0, line.indexOf('"""'));
    }
    out += line.split("#")[0] + "\n";
  }
  return out;
}

test("figure boxes reach the crop, in the shape the cropper reads", () => {
  // 1152_藥師(一)_藥學(一) q42, 2026-09-28: options A–D are chemical structures, and the crop showed
  // only A — B/C/D came out as blank white space. The boxes were in the data the whole time;
  // `figure_boxes` built them as bare tuples while `reread.page_extents` reads `entry.get("box")`,
  // so `if not isinstance(entry, dict): continue` dropped every one of them. The page-9 region
  // stayed at x1 50.3 (the B./C./D. marker column) instead of 158.4 (where the structures end).
  //
  // This is the crop defect the owner reported on 2026-09-25 as 「有些題目的圖片沒有截圖正確」.
  const source = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  const start = source.indexOf("def figure_boxes");
  const end = source.indexOf("\ndef ", start + 1);
  assert.ok(start > 0 && end > start, "figure_boxes must be a top-level function");
  const body = stripDocstringsAndComments(source.slice(start, end));

  // Negative control: the old shape. A bare `tuple(box)` append is what silently disabled the
  // whole mechanism, and nothing in the output says so — the crop just lacks its figures.
  assert.equal(/append\(\s*tuple\(/.test(body), false,
    "a bare tuple is skipped by page_extents, so the boxes would be dropped again");
});

test("the boxes a real question produces actually widen a real crop region", async () => {
  // Drives both languages end to end: `bridge.py figure_boxes` builds the list, and
  // `reread.page_extents` reads it. A shape test on the Python source can pass while the two
  // halves still disagree — this is the check that would have caught the tuple bug on day one.
  const { execFileSync } = await import("node:child_process");
  const script = [
    "import json, sys",
    "sys.path.insert(0, sys.argv[1])",
    "from qbr import reread",
    "sys.path.insert(0, sys.argv[2])",
    "import importlib.util as u",
    "spec = u.spec_from_file_location('bridge', sys.argv[3])",
    "bridge = u.module_from_spec(spec); spec.loader.exec_module(bridge)",
    "q = {'image_refs': [{'box': [39.2, 199.4, 158.4, 357.8], 'page': 9}]}",
    "boxes = bridge.figure_boxes(q)",
    "rows = [{'page': 9, 'x0': 39.2, 'y0': 34.6, 'x1': 50.3, 'y1': 537.1}]",
    "print(json.dumps({'boxes': boxes, 'text': reread.page_extents(rows, ()),",
    "                  'with': reread.page_extents(rows, boxes)}))",
  ].join("\n");
  const catalog = PATHS.CATALOG;
  const raw = execFileSync(PATHS.PYTHON, ["-c", script, `${catalog}/qbr/src`, `${catalog}/qbr/scripts`,
    PATHS.BRIDGE], { encoding: "utf8" });
  const data = JSON.parse(raw);

  assert.deepEqual(data.boxes, [{ box: [39.2, 199.4, 158.4, 357.8], page: 9 }],
    "a figure box is a dict with box and page");
  // The measured numbers from the reported defect. If the boxes are dropped again, `with` equals
  // `text` and the option pictures are outside the crop — which is exactly how it shipped broken.
  assert.equal(data.text["9"][2], 50.3, "the row's own region is the marker column");
  assert.equal(data.with["9"][2], 158.4, "the boxes must reach where the structures end");
});

test("a figure's path is where the file actually is", async () => {
  // On 2026-09-28 the agent was handed `image_refs[].path` as recorded — relative to the live queue
  // root — could not open it, and spent two `bash` calls running `find /` to locate its own crop.
  // The run still reached the right answer, so the failure was invisible in the output and visible
  // only in the tool trace. That is the worst kind: a correct conclusion reached by searching.
  //
  // Measured: joining against the directory holding `candidates.jsonl` gives
  // `.../review-ui/review-ui/crops/...` and matches nothing; the base is that directory's parent.
  const { execFileSync } = await import("node:child_process");
  const script = [
    "import json, os, sys",
    "sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])",
    "import importlib.util as u",
    "spec = u.spec_from_file_location('bridge', sys.argv[3])",
    "bridge = u.module_from_spec(spec); spec.loader.exec_module(bridge)",
    "keys = []",
    "seen = 0",
    "with open(bridge.CANDIDATES, encoding='utf-8') as fh:",
    "    for line in fh:",
    "        rec = json.loads(line)",
    "        seen += 1",
    "        if rec.get('image_refs'):",
    "            keys.append(rec['candidate_key'])",
    "            if len(keys) >= 5: break",
    "view = bridge.question_view(bridge.load_question(keys[0]))",
    "out = {'n': seen, 'figs': len(view['figures']),",
    "       'resolved': [bool(f['path']) and os.path.exists(f['path']) for f in view['figures']],",
    "       'rel': [f['relative_path'] for f in view['figures']]}",
    "print(json.dumps(out))",
  ].join("\n");
  const raw = execFileSync(PATHS.PYTHON, ["-c", script, `${PATHS.CATALOG}/qbr/src`,
    `${PATHS.CATALOG}/qbr/scripts`, PATHS.BRIDGE], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const data = JSON.parse(raw);

  assert.ok(data.figs > 0, "the sample question must have figures, or this proves nothing");
  assert.ok(data.resolved.every(Boolean),
    `every figure path must open; got ${JSON.stringify(data.resolved)}`);
  // Negative control: the recorded relative paths are NOT usable as written. If they were, the
  // resolution step would be unnecessary and the old code would have worked.
  assert.ok(data.rel.some((p) => !p.startsWith("/")),
    "the queue records relative paths, so resolution is what makes them open");
});

test("the agent works from the repository root, so the paths it is given resolve", () => {
  // `read`/`grep`/`find` resolve against the session cwd, and every path this agent handles is
  // repository-root-relative (`qbr/data/review-queues/...`). Running from `agent/` made those
  // reads fail: measured 2026-09-28, four reads on the option crops errored, the model decided the
  // files were missing, and it ran four `find` and five `bash` calls to find pictures that were
  // exactly where it had been told. The answer was still right, so only the trace showed it.
  // The session is built in `lib/session.mjs` (shared with the chat box), so the cwd is asserted
  // there. `agent.mjs` is checked too, because it must **delegate** — a second construction site in
  // the CLI would be a second place this fix could be missing from.
  const source = readFileSync(new URL("./lib/session.mjs", import.meta.url), "utf8");
  assert.match(source, /cwd: cwd \|\| PATHS\.CATALOG/, "both the loader and the session work from the root");
  // Negative control: the old value. If this comes back, the failure is a long tool trace with a
  // correct answer at the end — invisible unless someone reads the trace.
  assert.equal(/cwd: HERE/.test(source), false,
    "the agent directory is not the data root; paths would break again");
  const cli = readFileSync(new URL("./agent.mjs", import.meta.url), "utf8");
  assert.match(cli, /buildSession\(\{/, "the CLI must use the shared builder, not build its own session");
  assert.equal(/createAgentSession\(/.test(cli), false,
    "a second session construction in the CLI is a second place a prompt fix can be applied to one of");
});

test("a correct official answer is not overridden by the model's own reasoning", () => {
  // 1152_藥師(一)_藥學(一) q42, 2026-09-28. Same question, same model, four runs, three answers:
  // up, then (after the crops were fixed and all four structures became visible) DOWN, arguing
  // Eteplirsen's backbone is 2'-O-methyl phosphorothioate so the answer should be D. Eteplirsen is
  // a PMO -- morpholino rings and phosphorodiamidate linkages, which is option B, the official
  // answer. The next run said up again. The fix was not to hide the pictures: it was to say what
  // `rating` is allowed to mean, and to put this failure in front of the model as its own history.
  const prompt = identity.systemPrompt();
  assert.match(prompt, /rating.*只回答一件事/s, "the rating's meaning must be stated, not implied");
  assert.match(prompt, /官方答案不是你推翻的對象/, "the official answer is not the agent's to overrule");
  // The failure itself, because an abstract prohibition already existed and did not hold.
  assert.match(prompt, /一個真實的翻車/, "the model is shown what it actually got wrong");
  assert.match(prompt, /Eteplirsen/, "named, so it is a memory and not a platitude");

  // Negative control: the abstract rule alone. It was in the prompt for the whole failure.
  assert.match(prompt, /只看格式，不看語意/, "the old rule is still there, which is the point");
});

test("the learning loop is closed: a lesson written by one run is in the next run's prompt", () => {
  rmSync(join(SCRATCH, "lessons.jsonl"), { force: true });

  const before = identity.systemPrompt();
  // Negative control: with no lessons on disk the section must be absent. If the prompt always
  // contained a "what you learned" heading, the test below could pass while learning nothing.
  assert.equal(before.includes("你（或前幾輪的你）學到的事"), false,
    "a fresh agent must not claim to have learned anything");

  identity.remember({
    subject: MICRO,
    kind: "character-substitution",
    text: "occamy 把螢讀成熒",
    evidence: "q068 題幹第 52 字",
    key: "k",
  });

  // **No subject is passed.** The agent is handed a question key, not a subject, so a prompt that
  // needed one would load nothing whenever the operator forgot the flag — the same silent failure
  // as the old agent's `learned=None`.
  const after = identity.systemPrompt();
  assert.equal(after.includes("occamy 把螢讀成熒"), true,
    "what run N measured must be in run N+1's prompt, without being told the subject");
  assert.equal(after.includes(MICRO), true, "the lesson must say which subject it came from");
});

test("an engine-level lesson is marked engine-wide, not filed under a subject", () => {
  // Assert on the whole lesson line, not the bare `[引擎]` marker: the prompt's own instructions
  // contain that marker (「標「[引擎]」的是引擎層級的行為」), so a bare-token search returns true
  // before any lesson exists — the same false positive as grepping a single 螢 character.
  assert.equal(identity.systemPrompt().includes("[引擎] 這個引擎系統性把異體字折半"), false,
    "nothing is engine-level until one is written");

  identity.remember({
    subject: "",
    kind: "character-substitution",
    text: "這個引擎系統性把異體字折半",
  });

  const after = identity.systemPrompt();
  assert.equal(after.includes("[引擎] 這個引擎系統性把異體字折半"), true,
    "a lesson with no subject must be shown as engine-wide");
});

test("a lesson says which subject it came from, so a prior is never silent", () => {
  // The subject is **labelled, not filtered**. A lesson from another exam is still shown, because
  // a prompt that hid it would be a prompt that learns nothing unless the caller knew the subject
  // in advance. What must never happen is a lesson arriving without saying where it came from.
  const prompt = identity.systemPrompt();
  assert.equal(prompt.includes(`[${MICRO}] occamy 把螢讀成熒`), true,
    "the lesson must be attributed to its subject");
  assert.equal(prompt.includes(`[${PHARM}]`), false,
    "a subject with no lessons must not be invented");
});

test("the same lesson is counted, not repeated", () => {
  rmSync(join(SCRATCH, "lessons.jsonl"), { force: true });
  const first = identity.remember({ subject: MICRO, kind: "crop", text: "這一類題的圖常被切細縫" });
  const second = identity.remember({ subject: MICRO, kind: "crop", text: "這一類題的圖常被切細縫" });

  assert.equal(first.count, 1);
  assert.equal(second.count, 2, "the second occurrence must bump the count");
  const rows = identity.lessons({ subject: MICRO });
  assert.equal(rows.length, 1, "a repeated lesson must not be listed twice");
  assert.equal(rows[0].count, 2);
});

test("measured lessons come from the diff, so they cannot be invented", () => {
  rmSync(join(SCRATCH, "lessons.jsonl"), { force: true });
  const written = identity.lessonsFromDiff({
    subject: MICRO,
    engine: "occamy-6bit",
    key: "k",
    diff: {
      stem_differs: { pipeline: "黃綠色螢光。KOH", page: "黃綠色熒光。KOH" },
      option_differs: {},
    },
  });
  assert.equal(written.length, 1);
  assert.equal(written[0].text.includes("螢"), true);
  assert.equal(written[0].text.includes("熒"), true);

  // Negative control: a diff that agrees must produce no lesson. Without this, a "lesson" could
  // be written on every read and the list would fill with noise.
  const none = identity.lessonsFromDiff({
    subject: MICRO, engine: "occamy-6bit", key: "k",
    diff: { stem_differs: null, option_differs: {} },
  });
  assert.equal(none.length, 0, "an agreeing read must teach nothing");
});

test("the judgement store is the sandbox stream, not a human review stream", async () => {
  const tools = Object.fromEntries(toolsFor(Type).map((t) => [t.name, t]));
  assert.ok(tools.record_judgement, "record_judgement must be registered");
  assert.ok(tools.remember_lesson, "remember_lesson must be registered");

  // The store the bridge writes to is asserted by reading bridge.py's own constant: a test that
  // only checked a local variable would pass while the file path drifted.
  const source = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  assert.match(source, /agent_feedback\.jsonl/, "judgements go to the sandbox stream");
  assert.equal(/question_review_events\.jsonl"\s*,\s*"a"/.test(source), false,
    "the agent must never append to the human review stream");
});

test("the Pi providers are derived from engines.py, not written a second time", () => {
  const source = readFileSync(new URL("./lib/session.mjs", import.meta.url), "utf8");
  assert.match(source, /engines\.endpoints\(\)/,
    "the provider table must be read from the pipeline's own module");
  // Negative control: a hard-coded localhost port would be a second table. The ports live in
  // engines.py; only that file may name them. Both files are checked, because the table could be
  // re-typed in either without the other noticing.
  for (const file of ["./lib/session.mjs", "./agent.mjs"]) {
    const text = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.equal(/(18120|18130|8088|8888)/.test(text), false,
      `no engine port may be hard-coded in ${file}`);
  }
});

test("the agent refuses to silently fall back to a different model", () => {
  const source = readFileSync(new URL("./lib/session.mjs", import.meta.url), "utf8");
  assert.match(source, /找不到模型/, "an unavailable brain must stop the run");
  assert.equal(/model\s*\|\|\s*available\[0\]/.test(source), false,
    "silently using another model would make every number unattributable");
});

test("the designer's guidance reaches the agent, not just the file", () => {
  // The whole point of the loop. The UI writes a designer judgement to
  // `store/agent_feedback.jsonl`; `get_question` must read it back, or the designer's sentence
  // lands in a file the next agent run never opens. This is the same shape of break as
  // `learned=None`: the machinery exists and nothing connects it.
  const source = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  assert.match(source, /def prior_judgements/, "the feedback stream must be read back");
  assert.match(source, /"prior_judgements": prior_judgements\(/, "and it must be in the view");
  assert.match(source, /source.{0,4}designer|designer/, "the designer's own sentences are distinguished");

  // Negative control: the human review stream must stay a *different* authority. If
  // `prior_judgements` read `question_review_events.jsonl`, the agent's guidance and a reviewer's
  // decision would be indistinguishable, and "who decided this is acceptable" unanswerable.
  //
  // Only executable lines are searched. The docstring *names* that file while explaining that it
  // is not read — so a whole-function search returns true on correct code. Same false positive as
  // grepping a single 螢 character or a bare `[引擎]` marker: a query whose text appears in the
  // prose is not a check.
  const start = source.indexOf("def prior_judgements");
  const end = source.indexOf("\ndef ", start + 1);
  assert.ok(start > 0 && end > start, "prior_judgements must be a top-level function");
  const code = stripDocstringsAndComments(source.slice(start, end));
  assert.equal(code.includes("question_review_events"), false,
    "prior_judgements must not read the human review stream");
  assert.match(code, /agent_feedback|STORE/, "it reads the sandbox learning stream instead");
});

test("the UI writes to the file, and only the file", () => {
  const server = readFileSync(new URL("./ui/server.py", import.meta.url), "utf8");
  assert.match(server, /bridge\.STORE\b/, "the UI and the agent share one learning stream");
  // Negative control: the UI must never append to a human review stream. A learning UI that
  // could write a reviewer's decision would be an agent impersonating a human by another route.
  assert.equal(/question_review_events\.jsonl[^\n]*"a"/.test(server), false,
    "the UI must not append to the human review stream");
  // A judgement with no basis cannot be learned from, so the server must refuse it.
  assert.match(server, /reason is required/);
});

/**
 * The candidate list, and the three dispositions.
 *
 * Both exist because of one measured usability failure (designer, 2026-09-28):
 * 「畫面有了，但是沒有題目，我不可能記得 key，應該要有候選列表」and「下面的結論只有沒問題跟有問題，
 * 我認為做成三個按鈕就好」. A UI whose only way in is a key, and whose only answers are two, is a UI
 * that cannot record what the designer actually wants to say — which is usually an annotation.
 */

test("the list is reachable without a key, and says what is left", async () => {
  const bridge = await import("./bridge.py").catch(() => null);
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const python = process.env.QBR_PYTHON || PATHS.PYTHON;

  // The map: every category, with the two numbers that answer 「我做到哪了」.
  const { stdout } = await run(python, [PATHS.BRIDGE, "browse"], { timeout: 300_000 });
  const data = JSON.parse(stdout);
  assert.ok(Array.isArray(data.categories) && data.categories.length > 0,
    "browse must list categories without being told a key");
  for (const category of data.categories) {
    for (const field of ["category", "total", "judged", "with_figures"]) {
      assert.notEqual(category[field], undefined, `${category.category} must carry ${field}`);
    }
  }
  // Negative control: a list that cannot say whether a question was judged makes the designer
  // judge the same question again. The field has to be there and has to be per-question.
  const { stdout: one } = await run(python, [PATHS.BRIDGE, "browse", "--category", "藥師(二)", "--limit", "2"],
    { timeout: 300_000 });
  const rows = JSON.parse(one).questions;
  assert.ok(rows.length > 0, "a category must list its questions");
  assert.ok("judged" in rows[0], "each row must carry the judged state");
});

test("the three dispositions are up, down and hold — and nothing else", () => {
  const bridge = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  assert.match(bridge, /JUDGEMENT_RATINGS\s*=\s*\("up",\s*"down",\s*"hold"\)/,
    "the three dispositions must be declared once, in one place");
  // The CLI must accept the third one...
  assert.match(bridge, /--rating",\s*required=True,\s*choices=JUDGEMENT_RATINGS/,
    "the CLI must accept hold as well as up/down");
  // ...and the server must validate against the same list rather than its own copy.
  const server = readFileSync(new URL("./ui/server.py", import.meta.url), "utf8");
  assert.match(server, /bridge\.JUDGEMENT_RATINGS/, "the server must use the one list, not a second");
  // Negative control: production ratings must not have been widened by this change. `hold` is a
  // sandbox annotation; letting it into the reviewed stream is a separate, reviewed decision.
  const constants = readFileSync(new URL("../../qbr/src/qbr/review_ui/constants.py", import.meta.url), "utf8");
  assert.match(constants, /AI_FEEDBACK_RATINGS\s*=\s*\{?"up",\s*"down"\}?/,
    "the production rating set must still be up/down");
  assert.equal(/AI_FEEDBACK_RATINGS\s*=\s*\{[^}]*hold/.test(constants), false,
    "hold must not have been added to the production ratings");
});

test("a hold is recorded as a hold, not silently downgraded to a rating", () => {
  // The whole point of the third button is that the record says "not yet decided". A store that
  // coerces unknown values to a default would make the designer's annotation indistinguishable
  // from a verdict, which is the confusion the button exists to remove.
  const bridge = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  const start = bridge.indexOf("def do_feedback");
  const end = bridge.indexOf("\ndef ", start + 1);
  const code = stripDocstringsAndComments(bridge.slice(start, end));
  assert.match(code, /"rating":\s*args\.rating/, "the rating must be written through unchanged");
  assert.equal(/rating.*or\s*["']up["']/.test(code), false,
    "a missing or unusual rating must not be silently turned into up");
});

/**
 * The answer is a string. The UI treated it as a list and crashed on every question.
 *
 * Measured 2026-09-28 across all 79,090 questions: `answer` is a `str` **100%** of the time, and
 * 1,205 of them are not a bare letter — 1,161 voided (`送分`) and 44+ corrected (`B或C`). The UI
 * did `(question.answer || []).map(...)`, which throws on a string; the fetch succeeded, the render
 * threw, and the panel kept its placeholder. The designer's report was 「我還是沒有辦法看到任何題目」
 * — an empty page, not an error. That is why this test drives the real bridge instead of trusting
 * the shape.
 */

test("the answer reaches the UI as a string plus letters, for all three shapes", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const python = process.env.QBR_PYTHON || PATHS.PYTHON;

  const cases = [
    // The ordinary case.
    ["moex:115090:305:0401:1:question:q042", "letter"],
    // Voided: `accepted_values` is every option, and the reader must be told it is not four answers.
    ["moex:115090:308:0504:1:question:q010", "voided"],
    // Corrected: `答Ｂ或Ｃ者均給分` — either scores, which is not a multi-select answer.
    ["moex:115090:308:0503:1:question:q016", "or"],
  ];

  for (const [key, kind] of cases) {
    const { stdout } = await run(python, [PATHS.BRIDGE, "question", "--key", key], { timeout: 300_000 });
    const view = JSON.parse(stdout);
    assert.equal(typeof view.answer_display, "string",
      `${kind}: answer_display must be a string — a list here is what crashed the UI`);
    assert.ok(Array.isArray(view.answer_keys),
      `${kind}: answer_keys must be an array (it is the machine's accepted_values)`);
    assert.ok(view.answer_keys.every((k) => typeof k === "string"),
      `${kind}: every answer key must be a string`);
    // The raw field is still handed through for anyone who wants the stored value, but it must
    // **not** be the thing the UI iterates. Pinning its type is what stops the old crash returning.
    assert.equal(typeof view.answer, "string", `${kind}: the stored answer is a string`);

    if (kind === "voided") {
      assert.equal(view.answer_is_void, true, "a 送分 question must be flagged as voided");
      assert.equal(view.answer_keys.length, 4, "every option scores on a voided question");
      assert.match(view.answer_display, /送分/, "the reader must see 送分, not A、B、C、D");
    }
    if (kind === "or") {
      assert.deepEqual(view.answer_keys, ["B", "C"], "either letter scores, so both are keys");
      assert.match(view.answer_display, /或/, "the sheet's own wording must survive");
    }
  }
});

test("the UI never iterates the answer, and options may be bare values", () => {
  const ui = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  // Negative control, stated as the exact expression that crashed:
  assert.equal(/question\.answer\s*\|\|\s*\[\]\)\s*\.map/.test(ui), false,
    "the answer is a string; `(question.answer || []).map` throws on every question");
  assert.equal(/\(question\.answer\)\.map/.test(ui), false,
    "the answer must not be iterated directly");
  assert.match(ui, /question\.answer_keys/, "the UI must highlight from answer_keys");
  assert.match(ui, /question\.answer_display/, "the UI must show the reader's form of the answer");
  // `options` is a list of records on 78,979 questions and a list of bare values on 111; both must
  // be tolerated, because the render loop is all-or-nothing.
  assert.match(ui, /typeof option === "object"/, "a bare option value must be normalised, not crash");
});

test("the bridge does not reimplement the answer's meaning", () => {
  const bridge = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  const start = bridge.indexOf("def question_answer_display");
  const end = bridge.indexOf("\ndef question_answer_keys", start + 1);
  assert.ok(start > 0 && end > start, "question_answer_display must be a top-level function");
  const code = stripDocstringsAndComments(bridge.slice(start, end));
  // It must **delegate**. `ai_findings.answer_of` is the tested normaliser that documents the
  // 送分 / 或 / letter traps; a second parser here would be a second answer to the one question
  // this project cannot answer two ways.
  assert.match(code, /ai_findings\.answer_of/, "the display answer must come from the pipeline's own normaliser");
  assert.equal(/is_special_correction|accepted_values/.test(code), false,
    "the display logic must not be re-derived here — that is what answer_of is for");
});

/**
 * The pictures the pipeline already cut. They were on disk, correctly bound to their options, and
 * the page showed none of them.
 *
 * Designer's report (2026-09-28): 「過去很多管線的截圖是對的，但在這裡完全沒有截圖（因為題目本身有圖）」.
 * Two independent causes, both measured:
 *
 *   1. `question_view` dropped `asset_role` and `option_key`, so the UI could not bind a crop to
 *      its option row. An image-only question (four structures, empty option text) rendered as four
 *      blank rows. 1,320 refs carry `option-image`; 3,209 carry `figure-crop`.
 *   2. `/file` refused the crop path. `REVIEW_UI_ADDITIONAL_ASSET_ROOTS` was unset, so
 *      `safe_file_path` mapped `review-ui/crops/...` to `國考題資料夾/review-ui/crops/...` — which
 *      does not exist — and answered 404. The station's own v2 sets this to the queue root
 *      (`deploy/qbr-review/compose.yaml`); the sandbox UI must use the same contract.
 */

test("an option's picture reaches the view bound to its option key", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const python = process.env.QBR_PYTHON || PATHS.PYTHON;

  // q042: four chemical structures, every option text empty. Nothing but the crop can distinguish
  // them, so this is the question that cannot be reviewed without the pictures.
  const { stdout } = await run(python, [PATHS.BRIDGE, "question", "--key",
    "moex:115090:305:0401:1:question:q042"], { timeout: 300_000 });
  const view = JSON.parse(stdout);

  assert.ok(view.options.every((o) => !String(o.text || "").trim()),
    "this question's options are pictures, so their text is empty — that is the case under test");
  const optionImages = view.figures.filter((f) => f.asset_role === "option-image");
  assert.equal(optionImages.length, 4, "all four option pictures must reach the view");
  assert.deepEqual(optionImages.map((f) => f.option_key), ["A", "B", "C", "D"],
    "each picture must carry the option it was cut for");
  for (const ref of optionImages) {
    assert.ok(ref.path, `option ${ref.option_key} must have a resolvable path`);
    assert.ok(existsSync(ref.path), `option ${ref.option_key}'s file must exist: ${ref.path}`);
  }
  // Negative control: the fields the UI binds on are exactly the ones that were dropped. If they
  // are absent the page cannot place a picture, and an image-only question is four blank rows.
  assert.ok("asset_role" in view.figures[0], "asset_role must survive into the view");
  assert.ok("option_key" in view.figures[0], "option_key must survive into the view");
});

test("/file serves a queue-relative crop, because the queue root is an allowed asset root", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const python = process.env.QBR_PYTHON || PATHS.PYTHON;

  // A crop path exactly as the queue records it — relative to the **queue root**, not the repo.
  const relative = "review-ui/crops/1152_藥師(一)_藥學(一)(包括藥理學與藥物化學)/q042_option_B.png";
  const script = `
import json, os, sys
sys.path.insert(0, r"${PATHS.CATALOG}/qbr/src")
sys.path.insert(0, r"${PATHS.CATALOG}/qbr/scripts")
import importlib.util
spec = importlib.util.spec_from_file_location("bridge", r"${PATHS.BRIDGE}")
bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
from qbr.review_ui.paths import safe_file_path
rel = ${JSON.stringify(relative)}
out = {}
# As the server configures itself.
os.environ.setdefault("REVIEW_UI_ADDITIONAL_ASSET_ROOTS", bridge.QUEUE_ROOT)
p = safe_file_path(rel)
out["with_root"] = bool(p and p.is_file())
# The negative control: without the queue root allowed, the same path must NOT resolve.
os.environ.pop("REVIEW_UI_ADDITIONAL_ASSET_ROOTS", None)
p2 = safe_file_path(rel)
out["without_root"] = bool(p2 and p2.is_file())
print(json.dumps(out))
`;
  const { stdout } = await run(python, ["-c", script], { timeout: 300_000 });
  const result = JSON.parse(stdout);
  assert.equal(result.with_root, true,
    "a queue-relative crop must resolve when the queue root is an allowed asset root");
  assert.equal(result.without_root, false,
    "negative control: without that root the crop must NOT resolve — that is the 404 the designer hit");

  // **The server must actually configure it.** The two assertions above only prove how
  // `safe_file_path` behaves; a server that never sets the variable is a server that 404s every
  // crop while this test still passes. (Measured: an earlier version of this test passed with the
  // setting deleted from `server.py` — a check that could not catch its own bug.)
  const server = readFileSync(new URL("./ui/server.py", import.meta.url), "utf8");
  const code = stripDocstringsAndComments(server);
  assert.match(code, /REVIEW_UI_ADDITIONAL_ASSET_ROOTS/,
    "the UI server must allow the queue root as an asset root, or /file refuses every crop");
  assert.match(code, /bridge\.QUEUE_ROOT/,
    "it must be the queue root the bridge already resolved, not a second path computed here");
});

test("the sandbox UI binds crops the way v2 does, so both consoles show the same picture", () => {
  const mine = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  const v2 = readFileSync(new URL("../../review_ui/v2/02-area-question.js", import.meta.url), "utf8");

  // Both bind an option's picture with the same two facts. Two consoles drawing the same question
  // must not disagree about which structure sits under which letter — that is a reviewer reading a
  // different question from the one they are judging.
  for (const [name, source] of [["the sandbox UI", mine], ["v2", v2]]) {
    assert.match(source, /asset_role === ['"]option-image['"]/,
      `${name} must select option pictures by asset_role`);
    assert.match(source, /option_key === /, `${name} must bind them by option_key`);
  }
  // And it must not be positional: guessing "the nth crop is option n" puts A's structure under C's
  // letter whenever a crop fails, with no visible sign.
  assert.equal(/figures\[\s*\d+\s*\]/.test(mine), false,
    "the UI must not index figures positionally to find an option's picture");
});

/**
 * The agent must receive **its own** system prompt.
 *
 * This is the defect that made everything else look wrong. `systemPrompt()` was computed, printed
 * by `--probe`, and written to the log — while `createAgentSession` was never given it. The session
 * therefore ran on Pi's **default coding-assistant prompt**, and the agent:
 *
 *   - introduced itself as 「我是一個編碼助理」 (asked in interactive mode, 2026-09-28);
 *   - reached for `bash`/`read` on files instead of the judgement tools (the trace the designer was
 *     told to read as a health signal was the *symptom*);
 *   - and, most seriously, ran without the five 鐵則 — including 「絕對不可以寫人工審核紀錄」 and
 *     「`rating` 只回答抽取值與紙本一不一致」. The governance rules this project is built around were
 *     never in the model's context at all.
 *
 * A green suite here proved nothing: `--probe` printing 7,504 chars *looked* like the prompt was in
 * use. The check has to build a real session and ask the session what it holds.
 */
test("`--probe` proves the session holds the agent's own prompt, not Pi's coding-assistant default", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);

  // Drive the **real** entry point. An earlier version of this test re-created the wiring inside the
  // test file, so deleting `systemPrompt:` from `agent.mjs` still passed — a check that could not
  // catch its own bug. `--probe` now asks the session what it holds, and exits non-zero when that is
  // not the agent's own prompt.
  const { stdout, stderr } = await run("node", ["agent.mjs", "--probe"], { cwd: AGENT_DIR, timeout: 300_000 });
  const out = stdout + stderr;

  const line = out.split("\n").find((l) => l.startsWith("system prompt")) || "";
  assert.match(line, /the agent's own/,
    `--probe must report that the session holds the agent's own prompt; got: ${line.trim()}`);
  assert.ok(out.includes("題目修理代理"),
    "the prompt printed by --probe must be the repair agent's, and it is the session's own text");
  for (const rule of ["絕對不可以寫人工審核紀錄", "只看格式", "read_page"]) {
    assert.ok(out.includes(rule), `the session's prompt must carry 「${rule}」`);
  }
  assert.equal(out.includes("expert coding assistant"), false,
    "Pi's coding-assistant default must not be what the session holds");

  // The negative control is structural, not textual: the two numbers `--probe` prints (chars built
  // vs chars in session) are the two that silently diverged. A run where they disagree fails above,
  // and a run that stops printing the session's own text cannot satisfy the `match`.
});


/**
 * Two more silent failures found by reading a real trace rather than the conclusion (2026-09-28).
 *
 * The run answered correctly, so neither showed up as a wrong answer — which is exactly why the
 * designer's rule 「審一個 agent 要看他怎麼答的」 is the one that catches them.
 */

test("`crop` names the file it wrote, so the model is not told a picture it cropped does not exist", () => {
  const bridge = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  const code = stripDocstringsAndComments(bridge);

  // `do_read` already named its default output; `do_crop` returned `args.out` instead, which is
  // `None` on the ordinary call. q042 cropped 134,227 bytes and reported `"png": null` — the model
  // read that as "no picture" and went hunting (4 × `read` on the option PNGs + a `bash ls`).
  const crop = code.slice(code.indexOf("def do_crop"), code.indexOf("def do_read"));
  assert.match(crop, /out_png = args\.out or os\.path\.join\(/,
    "do_crop must name a default output file, or it reports png: null for a crop that succeeded");
  assert.match(crop, /"png": out_png/,
    'the payload must report the file actually written, not the optional --out argument');
  // Negative control: the shape that was there before.
  assert.equal(/"png": args\.out\b/.test(crop), false,
    'reporting `args.out` is the defect — it is None whenever the caller omits --out');
});

test("the role prompt renders whole: every section reaches the model, in order", async () => {
  const { systemPrompt } = await import("./lib/identity.mjs");
  const prompt = systemPrompt();

  // An unescaped backtick inside `ROLE` ends the template literal early. Two of the three times this
  // happened the module would not load at all; the third produced a *shorter prompt that still
  // built* — rules quietly absent from the model's context, with every check still green.
  //
  // So the check is **structural**: the sections must all be there, in order. A truncation removes
  // the tail, and a section silently edited away shows up as a missing header. (Counting backticks
  // was the first attempt and it was wrong — the prompt legitimately renders them as text.)
  const sections = [
    "## 你的位置",
    "## 鐵則（違反就是做錯）",
    "## 怎麼做一輪",
    "## 講話方式",
    "## 一個真實的翻車",
    "## 你（或前幾輪的你）學到的事",
    "## 設計者訂的基本原則（每次都必須遵守）",
  ];
  let last = -1;
  for (const header of sections) {
    const at = prompt.indexOf(header);
    assert.ok(at >= 0, `the rendered prompt is missing 「${header}」 — the string was truncated`);
    assert.ok(at > last, `「${header}」 must come after the previous section, in order`);
    last = at;
  }
  // The red line lives in 鐵則, not the last section. A truncation of the role text would delete it
  // while leaving the appended lessons/principles intact — the silent case. Check both.
  assert.match(prompt, /絕對不可以寫人工審核紀錄/,
    "the governance red line must reach the model, not be cut off by an early string end");
  assert.ok(prompt.indexOf("## 一個真實的翻車") < prompt.indexOf("## 你（或前幾輪的你）學到的事"),
    "the appended lessons must come after the role text, i.e. the role was not cut short");
});

/**
 * Memory inputs must not depend on where the *store* is.
 *
 * `principles()` walked up three levels from `REPAIR_AGENT_STORE` to find the designer's approved
 * rules. That path is correct only while the store is the default — the moment it is redirected
 * (which `run_tests.sh` and the README both do), the walk lands elsewhere and the function returns
 * `[]`. Measured: **0 of 19 principles**, with no error and a shorter prompt that still built.
 *
 * The designer's standing rules are the last thing that may disappear silently, so this is checked
 * by **redirecting the store** and requiring the count to be unchanged.
 */
test("the designer's principles survive a redirected store", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);

  const script = `import { principles, AGENT_DIR } from "${new URL("./lib/identity.mjs", import.meta.url).pathname}";
console.log(JSON.stringify({ count: principles().length, agentDir: AGENT_DIR }));`;

  const normal = JSON.parse((await run("node", ["--input-type=module", "-e", script],
    { cwd: AGENT_DIR, timeout: 300_000 })).stdout.trim().split("\n").pop());
  const redirected = JSON.parse((await run("node", ["--input-type=module", "-e", script],
    { cwd: AGENT_DIR, env: { ...process.env, REPAIR_AGENT_STORE: "/tmp/not-the-store" },
      timeout: 300_000 })).stdout.trim().split("\n").pop());

  // If the corpus is absent the count is 0 for a real reason and the test cannot measure anything;
  // say so rather than passing vacuously.
  assert.ok(normal.count > 0,
    "the designer's principles must be readable at all — otherwise this test proves nothing");
  assert.equal(redirected.count, normal.count,
    `redirecting the store must not change the principles (got ${redirected.count} of ${normal.count}) ` +
    `— memory inputs are not store inputs`);
  // Negative control: the shape that failed. Scope it to `principles()` — `lessons.jsonl` and the
  // narrative log **should** follow the store; it is the corpus path that must not.
  const identity = stripDocstringsAndComments(
    readFileSync(new URL("./lib/identity.mjs", import.meta.url), "utf8"));
  const body = identity.slice(identity.indexOf("export function principles"),
                              identity.indexOf("export function lessonsFromDiff"));
  assert.equal(/join\(STORE_DIR,/.test(body), false,
    "the principles path must not be derived from STORE_DIR (the store is not where rules live)");
});

/**
 * The agent can see. This test exists because I said it could not.
 *
 * Reading the trace (2026-09-28) I saw four `read` calls logged as `-> {}` and concluded "`read` is a
 * text tool, so reading a `.png` gives nothing". That conclusion was **wrong on both halves**:
 *
 *   - `read` on a PNG really works: `isError: false`, content `[{type:'text',text:'Read image file
 *     [image/png]'}, {type:'image',data:'iVBOR...'}]` — the picture is attached to the conversation.
 *   - `{}` was **my own lossy logger**. `summarize()` kept a fixed key list, so any result whose
 *     details lacked those keys recorded as an empty object. The picture had arrived; the log lost it.
 *
 * Acting on that, I added a prompt sentence telling the agent it could not see images. That is worse
 * than the original non-bug: it **removed a working capability** and pushed every judgement through a
 * second model. This is the project's own rule broken — I stated a rule in terms of my parser's
 * output, so the parser and the check confirmed each other.
 *
 * So the capability is now measured, and the prompt is checked against the measurement.
 */
test("`read` on an image really attaches the picture, so the agent can look for itself", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);

  // A crop that exists on disk (q042's option B), passed as an absolute path.
  const crop = [...(await import("node:fs")).default.readdirSync(join(AGENT_DIR, "store", "crops"))]
    .find((name) => name.includes("q042"));
  assert.ok(crop, "the q042 crop must exist for this test to measure anything");
  const cropPath = join(AGENT_DIR, "store", "crops", crop);

  const probePath = join(AGENT_DIR, ".read-image-probe.mjs");
  writeFileSync(probePath, `
import { createAgentSession, ModelRuntime, DefaultResourceLoader, getAgentDir, SessionManager } from "@earendil-works/pi-coding-agent";
const rt = await ModelRuntime.create();
const model = rt.getModel("ornith-mtplx", "ornith-1.5-mtplx-35b");
const rl = new DefaultResourceLoader({ cwd: ${JSON.stringify(PATHS.CATALOG)}, agentDir: getAgentDir(),
  noExtensions: true, noSkills: true, systemPrompt: "Call the read tool on the path you are given." });
await rl.reload();
const { session } = await createAgentSession({ cwd: ${JSON.stringify(PATHS.CATALOG)}, modelRuntime: rt, model,
  resourceLoader: rl, tools: ["read"], sessionManager: SessionManager.create("/tmp/read-img-test") });
let out = null;
session.subscribe((event) => {
  if (event.type === "tool_execution_end" && event.toolName === "read" && !out) {
    out = { isError: !!event.isError, parts: (event.result?.content || []).map((p) => p.type) };
  }
});
await session.prompt("Use the read tool on this absolute path and nothing else: " + ${JSON.stringify(cropPath)});
console.log(JSON.stringify(out));
session.dispose();
`);
  try {
    const { stdout } = await run("node", [probePath], { cwd: AGENT_DIR, timeout: 300_000 });
    const { isError, parts } = JSON.parse(stdout.trim().split("\n").pop());
    assert.equal(isError, false, "reading a PNG must succeed");
    assert.ok(parts.includes("image"),
      `the read result must carry an image part (got ${JSON.stringify(parts)}) — this is how the agent sees`);
  } finally {
    rmSync(probePath, { force: true });
  }

  // And the prompt must agree with that measurement, or it disables the capability again.
  const identity = stripDocstringsAndComments(
    readFileSync(new URL("./lib/identity.mjs", import.meta.url), "utf8"));
  assert.match(identity, /你自己看得到圖/,
    "the prompt must tell the agent it can see images");
  assert.equal(/你看不到圖|read\\`?\\s*是\*\*文字\*\*工具/.test(identity), false,
    "the prompt must NOT claim the agent cannot see images — that retracts a working capability");
});

/**
 * A tool result must never be logged as `{}`.
 *
 * `summarize()` kept a fixed key list. Any successful result whose details lacked those keys became
 * an empty object — and an empty object reads as "nothing happened", which is how a working image
 * read was mistaken for a failure and produced a wrong prompt change. Success has to be *visible*.
 */
test("a successful tool result is never summarized as an empty object", () => {
  const source = stripDocstringsAndComments(readFileSync(new URL("./agent.mjs", import.meta.url), "utf8"));
  const body = source.slice(source.indexOf("function summarize"), source.indexOf("async function main"));
  assert.match(body, /content_parts/,
    "summarize must record the result content parts (an image read has no `details`)");
  assert.match(body, /out\.shape|out\.text_chars/,
    "and it must name something for an unrecognised details payload, not return {}");
  // The caller must pass both halves, or a built-in tool result is still lost.
  assert.match(source, /summarize\(details, content\)/,
    "the tool_execution_end handler must pass result.content as well as result.details");
});

/**
 * The reviewer must see the question the platform will show — not the storage format.
 *
 * Designer, 2026-09-28:「這題在考題平台會是以什麼方式被我看到，所以斜體、上下標都應該是直接呈現
 * 出來（我不應該看到 <sup> 這種東西）」.
 *
 * The sandbox UI printed every field through `esc()`, so a stem stored as `GABA<sub>B</sub>` was
 * displayed as the literal text `GABA<sub>B</sub>`. That is the *machine's* form: a reviewer reading
 * it cannot see what a learner sees, which is the thing under review.
 */
test("the platform's rendering is used, and its allowlist comes from the platform's own file", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);

  const script = `
import sys, json
sys.path.insert(0, r"${PATHS.SANDBOX}/agent/lib")
import platform_view as pv
tags, attrs = pv.load_allowlist()
out = {
  "has_sub": "sub" in tags, "has_sup": "sup" in tags, "has_em": "em" in tags,
  "renders_sub": pv.as_platform_html("GABA<sub>B</sub>受體"),
  "renders_sup": pv.as_platform_html("10<sup>-5</sup> M"),
  # A tag the platform does NOT allow must not be passed through by the reviewer's renderer.
  "drops_script": pv.as_platform_html("<script>alert(1)</script>H<sub>2</sub>O"),
  # And an attribute the platform does not allow must be dropped, not rendered.
  "drops_onclick": pv.as_platform_html('<span onclick="x()">hi</span>'),
  "source": pv.SANITIZE_TS,
};
print(json.dumps(out))
`;
  const { stdout } = await run(PATHS.PYTHON, ["-c", script], { timeout: 300_000 });
  const out = JSON.parse(stdout.trim().split("\n").pop());

  assert.ok(existsSync(out.source),
    `the platform's sanitize.ts must be the source of the allowlist (looked at ${out.source})`);
  assert.ok(out.has_sub && out.has_sup && out.has_em,
    "the allowlist must be the platform's — it allows sub/sup/em");
  assert.match(out.renders_sub, /<sub>B<\/sub>/,
    "a stored subscript must render as markup, so the reviewer sees GABA_B and not `<sub>`");
  assert.match(out.renders_sup, /<sup>-5<\/sup>/, "and a superscript likewise");
  assert.equal(/<script/.test(out.drops_script), false,
    "a tag the platform does not allow must not reach the page");
  assert.equal(/onclick/.test(out.drops_onclick), false,
    "an attribute the platform does not allow must be dropped");
  assert.match(out.drops_script, /<sub>2<\/sub>/,
    "dropping a disallowed tag must not drop the question's real text");

  // The UI must actually use the rendered field, with a fallback to escaped raw text — never a
  // hand-rolled converter, which would be a second opinion about how a question looks.
  const ui = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  assert.match(ui, /platformHtml\(question\.stem_html/, "the stem must render via the platform view");
  assert.match(ui, /platformHtml\(rendered\.get\(key\)/, "and each option likewise");
  assert.equal(/stem_markup/.test(ui), false,
    "the UI must not assemble markup itself; it renders what the platform renderer produced");
});

/**
 * The A2 comparison pages exist and were never served.
 *
 * Designer, 2026-09-28:「昨天在 a2/run 看到的多重比較為甚麼沒有顯示出來」. The artifacts were on
 * disk (`a2/runs/`, 10 of them); `build_compare_page.py` writes a **static** page meant to be opened
 * by double-click, so nothing linked to them. From a browser, "not linked" and "not there" look
 * identical — so this checks they are reachable, and that a path cannot escape the runs directory.
 */
test("the A2 comparison pages are served, and the route cannot read outside a2/runs", async () => {
  const runs = join(PATHS.SANDBOX, "a2", "runs");
  assert.ok(existsSync(runs), "a2/runs must exist for this test to measure anything");
  const pages = (await import("node:fs")).default.readdirSync(runs).filter((n) => n.endsWith(".html"));
  assert.ok(pages.length, "there must be at least one built comparison page");

  const server = stripDocstringsAndComments(readFileSync(new URL("./ui/server.py", import.meta.url), "utf8"));
  assert.match(server, /A2_RUNS/, "the server must locate the A2 runs directory");
  assert.match(server, /startswith\(str\(A2_RUNS\.resolve\(\)\)/, 
    "a requested name must be resolved and checked to be inside a2/runs — otherwise `../` reads the disk");
  assert.match(server, /elif parsed\.path == "\/a2":/, "the /a2 route must exist");
});

/**
 * The chat box is the question-bound variant, and it is a **conversation**, not a second judge.
 *
 * Designer, 2026-09-28:「3 兩者都要，1先做」 — both variants, question-bound first. Two claims are
 * checked, each with the case that must fail:
 *
 *   1. The session is keyed by the question, so two questions cannot talk over each other.
 *   2. A designer turn is recorded **before** the answer, so a crash mid-answer still leaves the
 *      sentence he typed. Losing his input is worse than losing the reply to it.
 */
test("the chat is bound to one question, and both ends of a turn are recorded", () => {
  const source = readFileSync(new URL("./ui/chat.mjs", import.meta.url), "utf8");

  assert.match(source, /const sessions = new Map\(\)/, "one session per question");
  assert.match(source, /sessions\.has\(key\)/, "the question key is what a session is looked up by");
  assert.match(source, /sessions\.set\(key, entry\)/, "and what it is stored under");
  // Negative control: any session key that is **not** the question collapses every question into one
  // conversation, and the mistake then shows up as the agent "remembering" something it was never
  // told about this question. Checked as a shape rather than one spelling, because the first version
  // of this check (`/sessions\.get\(\)/`) passed against `sessions.get("_")` — a negative control
  // that did not fail, which is a check that cannot prove anything.
  const storeKeys = [...source.matchAll(/sessions\.(?:set|has|get)\(([^)]*)/g)].map((m) => m[1].split(",")[0].trim());
  assert.ok(storeKeys.length, "the session map must be used");
  for (const used of storeKeys) {
    assert.equal(used, "key",
      `sessions must be keyed by the question key, not by ${used}`);
  }

  // The designer's turn lands in the transcript before the model is asked, so his words survive an
  // error in the answer. The order is the contract, so it is the order that is asserted.
  // `key || null` and not `key`: the **unbound** conversation records `null`, so a reader of the
  // file can tell a corpus-level sentence from a question-bound one. That is also why this looks for
  // the shape rather than one exact spelling.
  const designerAt = source.search(/remember\(\{ candidate_key: key \|\| null, role: "designer"/);
  const promptAt = source.indexOf("await session.prompt(text)");
  assert.ok(designerAt > 0 && promptAt > 0, "both the record and the prompt must be here");
  assert.ok(designerAt < promptAt,
    "the designer's sentence must be written before the answer is requested");

  // And it must not be a second judge: the chat's own seed says so in the prompt it builds.
  assert.match(source, /先不要自己跑一輪判讀、也不要自己下 rating/,
    "the chat is a conversation; the prompt must say a rating is not its job");
});

/**
 * A conversation is not a judgement, so they are two files.
 *
 * `agent_feedback.jsonl` holds ratings about questions; `chat.jsonl` holds what was said. Folding
 * them together would fill the ratings table with sentences that rate nothing, and "what did the
 * agent decide" would stop being answerable from the ratings alone.
 */
test("the chat transcript is not the judgement stream", () => {
  const chat = readFileSync(new URL("./ui/chat.mjs", import.meta.url), "utf8");
  assert.match(chat, /const CHAT = join\(STORE_DIR, "chat\.jsonl"\)/, "a separate transcript file");
  // Comments are stripped first: the honest way to write "this deliberately does not write file X"
  // is to name file X, and a raw search would then report the opposite of the truth (the fourth
  // time in this file a check would have lied for exactly this reason).
  const chatCode = chat.split("\n").filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line)).join("\n");
  assert.equal(/agent_feedback/.test(chatCode), false,
    "the chat must not write the judgement stream — a conversation is not a rating");

  // The server reads the **file**, not the live child's memory, so a reload shows the record of the
  // conversation rather than what this tab happened to have seen.
  const server = readFileSync(new URL("./ui/server.py", import.meta.url), "utf8");
  assert.match(server, /def chat_turns/, "the history endpoint reads the transcript");
  assert.match(server, /chat\.jsonl/, "and names the same file the child writes");
  assert.match(server, /REPAIR_AGENT_STORE": str\(STORE_DIR\)/,
    "the child must be handed the same store, or the transcript is written where the server cannot read it");
});

/**
 * The seed hands over resolved paths, so the agent reads instead of searching.
 *
 * Measured 2026-09-29: a chat turn mentioning pictures made the agent open with `bash ls` and `find`
 * (one of which failed) before `read`ing the four crops that were exactly where the view said.
 * Healthy is zero `bash`. The negative control is the seed that omits the paths.
 */
test("the chat seed hands over the resolved figure paths", () => {
  const source = readFileSync(new URL("./ui/chat.mjs", import.meta.url), "utf8");
  assert.match(source, /figures,\s*\n\s*}, null, 2\)/, "the seed must include the figure list");
  assert.match(source, /path: f\.path/, "and the resolved path, not only the queue-relative one");
  assert.match(source, /不必再用 `bash` 或 `find` 去找/,
    "the prompt must say the paths are already resolved — otherwise the model rediscovers them");
});

/**
 * A restart continues the conversation instead of losing it.
 *
 * `SessionManager.create(cwd, sessionDir)` takes two arguments, and the first is the cwd. Measured
 * 2026-09-29: passing only the store path made that the cwd and left Pi writing sessions under
 * `~/.pi/agent/sessions/<mangled path>/`, so `REPAIR_AGENT_STORE` did not move them — the tests
 * redirected the store and the sessions stayed in the home directory. The store is a controlled
 * experiment variable here; a piece of state that ignores it is a piece that breaks the experiment.
 */
test("the chat session persists inside the store and is continued on restart", () => {
  const source = readFileSync(new URL("./ui/chat.mjs", import.meta.url), "utf8");
  // Both arguments, in order.
  assert.match(source, /SessionManager\.continueRecent\(PATHS\.CATALOG, sessionDirFor\(key\)\)/,
    "continueRecent(cwd, sessionDir) — continue this question's newest session, or make one");
  // Negative control: the one-argument form, which silently persists somewhere the store does not name.
  assert.equal(/SessionManager\.(create|continueRecent)\(join\(STORE_DIR/.test(source), false,
    "a single argument would be read as the cwd, and the sessions would land outside the store");
  // And the seed must not be re-sent to a restored session, or the question enters the conversation twice.
  assert.match(source, /getEntries\(\)\.length > 0/, "restoration is detected from the session's own entries");
  assert.match(source, /seeded: restored/, "and a restored session is treated as already opened");
});

/**
 * Every field the raw view needs is really rendered, and the prompt really reaches the page.
 *
 * This test exists because the previous version of this check **passed while the feature was dead**:
 * it looked for the string `renderAiRead` in the file, and the function was defined but never
 * called, so nothing was drawn. The designer found it, not the test — 「目前看起來還是沒有，
 * 請問是沒有做還是做了沒有帶入」.
 *
 * So this drives the real entry: it renders the question with the page's own functions and asserts
 * on the **output**. A defined-but-uncalled function produces nothing, and that is the case the old
 * check could not see.
 */
test("the raw view really renders, and the model's own prompt reaches the page", () => {
  const html = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");

  // This test exists because the previous check **passed while the feature was dead**: it looked for
  // the string `renderAiRead` in the file, and the function was defined but never called, so the
  // page drew nothing. The designer found it, not the test — 「目前看起來還是沒有，請問是沒有做
  // 還是做了沒有帶入」. So this runs the real functions and asserts on their **output**.
  const helpers = ["esc", "platformHtml", "renderPipelineFindings", "renderAiRead", "renderTrace"];
  const parts = [];
  for (const name of helpers) {
    const at = scripts.indexOf(`function ${name}(`);
    assert.ok(at >= 0, `${name} must exist in the page`);
    // The next **top-level** declaration — `async function` counts, and the first version of this
    // slice did not allow for it, so it ran past the end of `renderPipelineFindings` and swallowed
    // `loadQuestion` (which is `async` and uses `$`, an undefined name outside a browser).
    const rest = scripts.slice(at + 1);
    const next = rest.search(/\n(?:async )?function [A-Za-z_$]/);
    parts.push(next >= 0 ? scripts.slice(at, at + 1 + next) : scripts.slice(at));
  }

  const question = {
    judgements: [],
    ai_findings: [{
      model: "ornith-1.5-mtplx-35b", created_at: "2026-09-21T22:32:41", seconds: 1.0,
      prompt_system: "SYSTEM-MARKER", prompt_user: "USER-MARKER",
      finding: { verdict: "OK", where: "WHERE-MARKER", fix: "FIX-MARKER" },
      raw: "RAW-MARKER", usage: { prompt_tokens: 849 },
    }],
    trace: [
      { event: "tool_call", tool: "read", args: { path: "q042_option_A.png" } },
      { event: "tool_result", tool: "read", ok: true, summary: { content_parts: ["text", "image"] } },
    ],
  };
  const source = `${parts.join("\n")}\nreturn { pipeline: renderPipelineFindings(question.ai_findings), read: renderAiRead(question) };`;

  // `renderPipelineFindings`/`renderAiRead` return strings, so no DOM is needed at all — which is the
  // point: a function that builds a string can be measured without a browser, and that is what makes
  // "it renders" a check instead of a claim.
  const result = new Function("question", source)(question);

  for (const marker of ["SYSTEM-MARKER", "USER-MARKER", "WHERE-MARKER", "RAW-MARKER"]) {
    assert.ok(result.pipeline.includes(marker),
      `${marker} must reach the page — the prompt and the raw output are the evidence, not the conclusion`);
  }
  assert.ok(result.read.includes("content_parts"),
    "the agent's tool trace must render, including the image part");
  assert.ok(result.read.includes("q042_option_A.png"), "and the arguments it was called with");

  // Negative control: the raw view must actually be **placed** in the question body, or the functions
  // are as dead as they were when this test was written. This is the check the old one lacked.
  assert.match(html, /\$\{renderPipelineFindings\(question\.ai_findings\)\}/,
    "renderPipelineFindings must be called from the rendered question body");
  assert.match(html, /\$\{renderAiRead\(question\)\}/,
    "renderAiRead must be called from the rendered question body");
});

/**
 * The pipeline's finding records are carried into the view, so the raw view has something to show.
 *
 * The render test above drives the renderer with a synthetic record, so it cannot notice the bridge
 * dropping the field. This is the other half: the real `question_ai_findings.jsonl` is read, and a
 * record known to be there is found. Without both halves, "the prompt reaches the page" is testable
 * while "the prompt ever leaves the disk" is not.
 */
test("the pipeline's AI findings are read from its stream and reach the view", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const key = "moex:108030:305:33:1:question:q014";
  const { stdout } = await run(PATHS.PYTHON, [PATHS.BRIDGE, "question", "--key", key],
                               { timeout: 300_000, maxBuffer: 64 * 1024 * 1024 });
  const view = JSON.parse(stdout);
  const rows = view.ai_findings || [];
  assert.ok(rows.length, `the stream must yield this question's records (${key})`);
  const row = rows[0];
  assert.ok(row.prompt_system && row.prompt_system.length > 50,
    "the prompt the model was given must be carried — it is what 'what the AI was shown' means");
  assert.ok(row.prompt_user && row.prompt_user.includes("題號"),
    "and the user prompt, which holds the question's own text");
  assert.ok(row.finding && row.finding.where, "and the parsed answer");
  assert.ok(row.model, "and which model said it");

  // The bridge must also be the one that reads it, not the UI guessing from another file.
  const source = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  assert.match(source, /question_ai_findings\.jsonl/, "the bridge names the stream it reads");
  assert.match(source, /"ai_findings": ai_findings\(/, "and puts it in the view");
});

/**
 * The agent has a **corpus view**, because the designer caught it asking for a key it already had.
 *
 * Verbatim (2026-09-28): 「目前的Agent無法看到全局」, with the transcript where it answered 「我需要
 * 更多資訊才能回答這個問題——目前這則對話還沒有指定哪一道題」. It was right: every tool it had took a
 * key, so a question about the whole corpus was unanswerable. `see_corpus` and `find_disputed` are
 * the missing end — and the second is the workflow the designer named: 「我想針對 block 的題目跟你
 * 進行對話」.
 */
test("the agent can look at the whole corpus, not only one question", async () => {
  const source = readFileSync(new URL("./lib/tools.mjs", import.meta.url), "utf8");
  assert.match(source, /name: "see_corpus"/, "there is a corpus-level tool");
  assert.match(source, /name: "find_disputed"/, "and one for the designer's own disputes");

  // The tools must be backed by the bridge, not by a second reading of the candidate file here.
  assert.match(source, /callBridge\(\["overview"\]/, "see_corpus calls the bridge");
  assert.match(source, /callBridge\(\["disputes"/, "find_disputed calls the bridge");

  // Measured, not assumed: the bridge answers, and the designer's own words come back.
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const { stdout } = await run(PATHS.PYTHON, [PATHS.BRIDGE, "disputes", "--limit", "3"],
                               { timeout: 300_000, maxBuffer: 64 * 1024 * 1024 });
  const disputes = JSON.parse(stdout);
  assert.ok(disputes.count > 0, "the designer's disputes must be countable");
  assert.ok(disputes.with_notes > 0, "and some must carry his sentence");
  const withNote = disputes.disputes.find((row) => (row.notes || "").trim());
  assert.ok(withNote, "the list must include a row with notes");
  assert.ok(withNote.candidate_key, "each dispute must carry a key, so the agent can go straight to it");
  // `notes`, not `reason`: every human event has an empty `reason`, so a reader that looked there
  // would report "nobody wrote anything" — the exact false negative measured 2026-09-28.
  assert.notEqual(withNote.notes, undefined, "the person's sentence is the `notes` field");
});

/**
 * The **unbound** conversation — the second of the two the designer asked for.
 *
 * He said「3 兩者都要，1先做」and only the bound one was built. The bound one cannot answer a
 * corpus question at all, because its shape is "here is one question"; the unbound one is what makes
 * `see_corpus` reachable from the box, and without it the agent's correct answer to a corpus question
 * is「我需要更多資訊」. This checks the wiring in both directions: the seed that tells the model it
 * may start from nothing, and the null key that keeps it a different session.
 */
test("there is an unbound conversation, and it is not the bound one's session", () => {
  const chat = readFileSync(new URL("./ui/chat.mjs", import.meta.url), "utf8");
  // The seed for a keyless turn must send the model to the corpus tools.
  assert.match(chat, /if \(!key\) \{/, "a keyless turn has its own opening context");
  assert.match(chat, /see_corpus/, "and is told the corpus tool exists");
  assert.match(chat, /不要因為沒有指定題目就回答「我需要更多資訊」/,
    "and is told not to ask for a key — that answer is the reported symptom");
  // The unbound session is keyed distinctly, so it neither inherits a question's context nor leaks.
  assert.match(chat, /const source = key \|\| "__corpus__";/,
    "the unbound session gets its own hash input");
  assert.match(chat, /const readable = key \?/, "and its own directory name");

  // The server must accept a missing key rather than answer 400: with the old `if not key:
  // error`, corpus mode could not be reached from the browser at all.
  const server = readFileSync(new URL("./ui/server.py", import.meta.url), "utf8");
  assert.match(server, /key = payload\.get\("key"\) or ""/,
    "the ask endpoint treats a missing key as unbound, not as an error");
  assert.doesNotMatch(server, /chat needs a key/,
    "and no longer refuses it");

  // And the transcript of the unbound conversation must be readable back — `null` and `""` are the
  // same conversation, and the old `if not key: return []` made it invisible after a reload.
  const turns = readFileSync(new URL("./ui/server.py", import.meta.url), "utf8");
  assert.match(turns, /wanted = key or None/, "the transcript reader normalises the unbound key");
});

/**
 * The designer reaches the questions he flagged by clicking, not by remembering keys.
 *
 * His complaint: 「我原本在 v2 審核過的大量題目也沒有紀錄，我想要針對 block 的題目跟你進行對話」.
 * The list must carry his flag and his own sentence, and the walk must stay on the filtered list
 * (the v2 rule already measured: a filter narrows what is drawn, not what is walked).
 */
test("the designer's disputes are reachable from the list, with his own words", () => {
  const html = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  assert.match(html, /id="only-disputed"/, "there is a filter for the questions he flagged");
  assert.match(html, /query\.set\("disputed", "1"\)/, "and it is passed to the query");
  assert.match(html, /你說過/, "the row shows that he flagged it");
  // The note is what he actually wrote, drawn in the list, so he can pick by it.
  assert.match(html, /const note = q\.notes \?/, "his sentence is drawn on the row");

  const bridge = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  assert.match(bridge, /p_browse\.add_argument\("--disputed"/, "the bridge takes the filter");
  assert.match(bridge, /rows = \[row for row in rows if row\.get\("disputed"\)\]/,
    "and applies it to what is walked, not to what is drawn");

  // `W`/`S` walk the drawn list. The index is taken from `listRows`, which is what was rendered —
  // walking the category while a filter is on is the defect v2 already paid for.
  assert.match(html, /function go\(step\)/, "there is a walker");
  assert.match(html, /listRows\.findIndex/, "and it walks the drawn list");
  assert.match(html, /document\.addEventListener\("keydown"/, "bound to the keyboard");
});

test("the bridge does not count an empty question_number as a valid search", () => {
  const server = readFileSync(new URL("./ui/server.py", import.meta.url), "utf8");
  // Kept from an earlier fix: `number=0` silently matches nothing and reads as an empty corpus.
  assert.match(server, /number=int\(raw_number\) if raw_number\.strip\(\)\.isdigit\(\) else None/,
    "an absent number must stay None");
});

/**
 * The designer's own v2 history is shown where he is looking, not just counted.
 *
 * He said「我原本在 v2 審核過的大量題目也沒有紀錄」. The events were in the payload all along, but the
 * page printed only 「人動過 N 次」 — which says he touched a question and hides what he found. The
 * sentence is the part worth keeping, and it is in `notes` (`reason` is empty on every human event).
 */
test("the designer's v2 decisions and his own words are shown on the question", async () => {
  const html = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  assert.match(html, /function renderHumanEvents\(/, "there is a renderer for the human events");
  assert.match(html, /\$\{renderHumanEvents\(question\.human_events\)\}/,
    "and it is actually called from the question body — not merely defined");
  assert.match(html, /row\.notes \? esc\(row\.notes\)/,
    "it draws the person's sentence, the field that carries text");

  // Measured on the real stream: a question the designer blocked with a reason still carries it.
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const { stdout } = await run(PATHS.PYTHON,
    [PATHS.BRIDGE, "disputes", "--limit", "200"],
    { timeout: 300_000, maxBuffer: 64 * 1024 * 1024 });
  const rows = JSON.parse(stdout).disputes || [];
  const key = (rows.find((row) => (row.notes || "").trim()) || {}).candidate_key;
  assert.ok(key, "a dispute with text must exist to test against");
  const { stdout: q } = await run(PATHS.PYTHON, [PATHS.BRIDGE, "question", "--key", key],
                                  { timeout: 300_000, maxBuffer: 64 * 1024 * 1024 });
  const view = JSON.parse(q);
  assert.ok((view.human_events || []).length, `the view carries the human events (${key})`);
  const withNote = view.human_events.find((row) => (row.notes || "").trim());
  assert.ok(withNote, "and at least one carries the designer's sentence");
});
