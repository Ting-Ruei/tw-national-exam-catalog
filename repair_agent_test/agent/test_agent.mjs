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
import { readFileSync, existsSync, rmSync, mkdtempSync, writeFileSync, mkdirSync,
         openSync, readSync, closeSync } from "node:fs";
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

/** The queue head's first candidate — a 4 KB head read, never a 300 MB stream into memory. */
function queueHeadKey() {
  const fd = openSync(join(PATHS.CATALOG, "qbr", "data", "review-queues", "live", "review-ui",
                           "candidates.jsonl"), "r");
  const head = Buffer.alloc(4096);
  readSync(fd, head, 0, 4096, 0);
  closeSync(fd);
  return JSON.parse(head.toString("utf8").split("\n")[0]).candidate_key;
}

/**
 * A judgement row says **when, and in what context**, it was written — whoever wrote it.
 *
 * 2.1 (2026-09-30): the agent's rows carried no `at` at all, and neither writer carried a run /
 * session id or a prompt version — two writers of one stream were two shapes, and no row could
 * answer「這一判是何時、在哪個 run 做的」. One producer, `bridge.judgement_envelope()`, now stamps
 * both writers; the agent and the chat box name the run in the environment, and a direct designer
 * rating (no run) carries the honest empty string. The negative control is the very row the old
 * code wrote: no `at`, no ids, no schema on the designer's side.
 */
test("every judgement row is stamped with when and in what context it was written", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);

  const KEY = queueHeadKey();
  assert.ok(KEY, "the queue head yields a candidate to judge");

  // Drive the bridge — what must hold is the row it writes, not the call site.
  const runBridge = (extraEnv = {}) => run(PATHS.PYTHON, [PATHS.BRIDGE, "feedback",
    "--key", KEY, "--rating", "up", "--reason", "stamp shape check"],
    { timeout: 300_000, maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, REPAIR_AGENT_RUN_ID: "test-run-01",
             REPAIR_AGENT_SESSION_ID: "sess-01",
             REPAIR_AGENT_PROMPT_VERSION: "pv-test", ...extraEnv } });

  const { stdout } = await runBridge();
  const row = JSON.parse(stdout).appended;
  assert.equal(row.run_id, "test-run-01", "the row names the run that made it");
  assert.equal(row.session_id, "sess-01", "the row names the conversation it was made in");
  assert.equal(row.prompt_version, "pv-test", "the row names the prompt it answered to");
  assert.match(row.at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/, "and says when");
  assert.equal(row.schema, "repair_agent_test/agent_feedback v2", "the shape says its own version");

  // The designer's writer stamps with the **same producer**: same columns, and since the POST
  // carries no run, the run id is the honest empty string rather than a silently wrong one.
  const uiScript = [
    "import importlib.util, json, sys",
    "spec = importlib.util.spec_from_file_location('ui_server', sys.argv[1])",
    "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)",
    "out = mod.append_judgement({'candidate_key': sys.argv[2], 'rating': 'up',",
    "  'reason': 'ui stamp check'})",
    "print(json.dumps(out, ensure_ascii=False))",
  ].join("\n");
  const { stdout: uiOut } = await run(PATHS.PYTHON, ["-c", uiScript,
    join(AGENT_DIR, "ui", "server.py"), KEY],
    { timeout: 300_000, maxBuffer: 16 * 1024 * 1024 });
  const uiRow = JSON.parse(uiOut);
  for (const column of ["at", "run_id", "session_id", "prompt_version"]) {
    assert.ok(column in uiRow, `the designer's row carries ${column} too`);
  }
  assert.equal(uiRow.run_id, "", "no run may be invented for a direct designer rating");
  assert.match(uiRow.at, /^\d{4}-\d{2}-\d{2}T/, "the designer's row is still stamped with when");
  assert.equal(uiRow.schema, row.schema, "one stream, one schema string, both writers");
  assert.equal(uiRow.action, "ai_feedback", "the designer's row is self-describing too");

  // The wiring that hands the stamp its values: the run names itself, and the chat box names the
  // session the ask came in on (source checks here; the runtime shape is asserted above).
  const agentSource = readFileSync(new URL("./agent.mjs", import.meta.url), "utf8");
  assert.match(agentSource, /REPAIR_AGENT_RUN_ID/, "the run names itself to the bridge");
  assert.match(agentSource, /manager\.getSessionId/, "the run names its Pi session");
  const chatSource = readFileSync(new URL("./ui/chat.mjs", import.meta.url), "utf8");
  assert.match(chatSource, /REPAIR_AGENT_SESSION_ID = entry\.manager\.getSessionId/,
    "a chat judgement names this question's session, not the process's default");
});

/**
 * A note is optional for the designer, never for the agent.
 *
 * Old UI (measured 2026-09-30): a judgement without prose was refused in two places — the
 * browser's `judgeKey` put up 「請先寫下判讀依據」 and the server raised `reason is required`. v2's
 * own `A`/`R` never asked for prose, and the designer ruled his decision **is** the verdict
 * (workplan 2.2: 判定免註解). The asymmetry is the point: a human rating and a machine rating are
 * different authorships with different burdens, so the burden moved to the machine — both writers
 * are driven for real here, each with the case that must fail on the old behaviour.
 */
test("a note is optional for the designer, never for the agent", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const KEY = queueHeadKey();

  // The designer's writer, through the real server module: a rating with an **empty** note must
  // append and come back stamped (the old server raised before writing a byte).
  const uiScript = [
    "import importlib.util, json, sys",
    "spec = importlib.util.spec_from_file_location('ui_server', sys.argv[1])",
    "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)",
    "out = mod.append_judgement({'candidate_key': sys.argv[2], 'rating': 'up', 'reason': ''})",
    "print(json.dumps(out, ensure_ascii=False))",
  ].join("\n");
  const { stdout: uiOut } = await run(PATHS.PYTHON, ["-c", uiScript,
    join(AGENT_DIR, "ui", "server.py"), KEY],
    { timeout: 300_000, maxBuffer: 16 * 1024 * 1024 });
  const uiRow = JSON.parse(uiOut);
  assert.ok(uiRow.at, "the bare designer rating still carries its stamp");
  assert.equal(uiRow.reason, "", "and its honest empty note");

  // The agent's writer: a bare rating is refused — the case that must fail on the old behaviour,
  // where a machine could write an unevidenced rating and it would land as if it were a judgement.
  // A **human** source (the rare CLI path where the designer types the rating himself) still goes
  // through, so the gate is on authorship, not on the column.
  const runBridge = (extraArgs, extraEnv = {}) => run(PATHS.PYTHON,
    [PATHS.BRIDGE, "feedback", "--key", KEY, "--rating", "up", ...extraArgs],
    { timeout: 300_000, maxBuffer: 16 * 1024 * 1024, env: { ...process.env, ...extraEnv } });

  await assert.rejects(
    runBridge(["--source", "agent"]),
    (error) => {
      assert.match(String(error.stdout || ""), /--reason/,
        "the refusal must say what is missing");
      return true;
    },
    "the agent must not write a rating with no basis");

  // And the same bare input under the human source appends: the gate follows the author.
  const { stdout: humanOut } = await runBridge(["--source", "human"]);
  assert.equal(JSON.parse(humanOut).appended.reason, "",
    "a human bare rating goes through — the gate is on authorship");
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
  // The designer's rating is one key, and the note is optional (2.2, 2026-09-30 判定免註解) —
  // the server must not refuse it. The agent's rating carries the burden instead: the bridge
  // refuses an agent judgement with no basis (its own contract below).
  assert.equal(/reason is required/.test(server), false,
    "a one-key designer rating must write itself, note or no note");
});

/**
 * 「刷新此題」 re-draws the question area, and the list keeps its place.
 *
 * The old flow (measured 2026-09-30): after a run the designer pressed F5 — which threw away his
 * filters, his selected row and the list scroll: his way back to the question he was looking at.
 * The contract (workplan 2.3): a button in the question pane, an auto re-fetch when the run's turn
 * ends, and a lamp that says "data may be changing" while it runs.
 */
test("刷新此題 re-fetches the question, list untouched, run completion refreshes itself", () => {
  const html = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  assert.match(html, /id="refresh-question"/, "the question pane carries the button");
  assert.match(html, /refresh-question"\)\.addEventListener\("click"/, "and it is wired to a click");
  // Negative control: the page had no such button before today — every match below is new code.
  assert.match(html, /refreshQuestion\("run"\)/, "a finished run re-fetches without a click");

  // A lamp that turns on and never off is a lamp nobody trusts: it lights in `askChat` and goes
  // out in the send's `finally`, because a turn that died mid-stream never saw its `done`.
  assert.match(html, /classList\.add\("watching"\)/, "the lamp lights while the run is in flight");
  const finallyBlock = html.slice(html.indexOf("} finally {"));
  assert.match(finallyBlock, /classList\.remove\("watching"\)/,
    "and a dead stream still turns it off");

  // It re-walks `loadQuestion` and nothing around it: re-filtering the list would reorder what
  // the designer is walking — the F5 behaviour this replaces.
  const refreshStart = html.indexOf("async function refreshQuestion");
  const refreshNext = html.indexOf("\nasync function", refreshStart + 10);
  const refresh = html.slice(refreshStart, refreshNext);
  assert.match(refresh, /loadQuestion\(\)/, "it re-reads the question from the source");
  assert.equal(/loadList\(\)/.test(refresh), false,
    "and must not re-filter the list — his place stays");
});

/**
 * A repair proposal is the agent's own, in its own file — and writing one is not approving one.
 *
 * Rules the designer fixed 2026-09-30 (workplan 2.4): the agent **proposes, never applies**; a
 * draft names 修法/插入點/依據 and its crop **with the crop's own checksum** (evidence that cannot
 * be re-proved is a claim); several drafts may sit per question (多版可存); and none of that
 * arrives a batch approval (不逐稿批) — the question's JSONL and the human ledger stay untouched.
 * Negative controls: a draft with no 修法, and a crop citation to a file that does not exist —
 * the two ways a confident-sounding proposal hides that it has neither substance nor evidence.
 */
test("propose_repair drafts evidence-bearing proposals to their own file, writes no answers", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const { createHash } = await import("node:crypto");
  const run = promisify(execFile);
  const KEY = queueHeadKey();

  // A draft through the real bridge, with a real crop citation carrying its real checksum.
  const crop = join(SCRATCH, "draft-evidence.png");
  writeFileSync(crop, Buffer.from("propose-repair-negative-control")); // contents, not format: the hash is the contract
  const expectSha = createHash("sha256").update("propose-repair-negative-control").digest("hex");
  const runPropose = (extra) => run(PATHS.PYTHON,
    [PATHS.BRIDGE, "propose", "--key", KEY, "--fix", "抽取值改成「白色蠟」",
     "--insert", "選項 B 的化學式行", "--basis", "紙本第 2 欄第 3 個結構式", ...extra],
    { timeout: 120_000, maxBuffer: 16 * 1024 * 1024 });
  const { stdout: first } = await runPropose(["--crop", crop]);
  const row = JSON.parse(first).appended;
  assert.equal(row.action, "repair_draft", "the stream's own action, so a draft is not a judgement");
  assert.equal(row.status, "proposed", "writing a draft is not approving it");
  assert.equal(row.insert, "選項 B 的化學式行", "插入點 travels with the draft");
  assert.equal(row.crop.sha256, expectSha, "the crop citation is provable, not decorative");

  // A second draft for the same question lands next to the first — revision, not overwrite.
  const { stdout: second } = await runPropose([]);
  const draftsFile = JSON.parse(second).drafts;
  const onDisk = readFileSync(draftsFile, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(onDisk.length, 2, "multiple drafts may sit per question (多版可存)");
  assert.ok(onDisk.every((r) => r.candidate_key === KEY), "and both belong to the question");

  // Negative control: no 修法 → refused (argparse fails before the write); a crop citation
  // without a file → refused by the bridge itself. Both names must reach the caller.
  await assert.rejects(
    run(PATHS.PYTHON, [PATHS.BRIDGE, "propose", "--key", KEY, "--insert", "x", "--basis", "y"],
        { timeout: 120_000, maxBuffer: 16 * 1024 * 1024 }),
    (error) => {
      assert.match(String(error.stderr || "") + String(error.stdout || ""), /--fix/,
        "the refusal must name what is missing");
      return true;
    },
    "a draft with no 修法 is not actionable");
  await assert.rejects(
    runPropose(["--crop", join(SCRATCH, "does-not-exist.png")]),
    (error) => { assert.match(String(error.stdout || ""), /does not exist/); return true; },
    "a crop citation without a file is not evidence");

  // The UI reads them — read-only: the server renders drafts into the question view and never
  // writes the drafts file (the person's accept/return key is the only mover of a status). The
  // check runs on executable lines only: the *docstring* that names the file while promising not
  // to touch it is allowed to say so.
  const server = readFileSync(new URL("./ui/server.py", import.meta.url), "utf8");
  assert.match(server, /drafts = bridge\.drafts_for\(/, "the question view loads the drafts");
  assert.match(server, /view\["drafts"\] = drafts/, "and it carries them into the view");
  const html = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  assert.match(html, /function renderDrafts\(/, "the drafts renderer exists");
  assert.match(html, /\$\{renderDrafts\(question\)}/, "and it is actually called");
  // Nothing after `append_judgement` may OPEN the drafts file for write — reading is allowed
  // (the checksum lookup does exactly that), appending or rewriting is what a second writer means.
  // Executable lines only: the docstring that names the file while promising not to touch it
  // is allowed to say so.
  const onlyServerWrite = stripDocstringsAndComments(
    server.slice(server.indexOf("def append_judgement")));
  const draftWrites = onlyServerWrite.split("\n").filter((ln) =>
    /DRAFTS|repair_drafts/.test(ln) && /open\(/.test(ln) && /["'](a|w)[\+a-z]*["']/.test(ln));
  assert.deepEqual(draftWrites, [], "nothing in the server appends or rewrites the drafts file");
});

/**
 * The pass key writes the reviewer's ledger; the machine never gets one.
 *
 * 2026-09-30 (workplan 2.5): 「通過鍵在教學 pane、按即寫人審帳本（事件參照草稿 checksum；機器不得
 * 代按）; 退回理由進規則候選」. The runtime proof drives the real server module with a redirected
 * ledger, and the negative controls are the exact impersonations the rule forbids: a checksum that
 * names no draft (a forged or stale decision), and a return with no reason — both refused before a
 * byte is written. The 退回 lands in the learning stream with the draft's checksum attached, so
 * the rule house (3.1) can harvest the reason without re-reading the drafts file.
 */
test("an accept points at a proven draft row; a return carries its rule raw material", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const { createHash } = await import("node:crypto");
  const run = promisify(execFile);
  const KEY = queueHeadKey();

  // The sandbox's own surfaces, all redirected: store (drafts + learning stream) and ledger.
  const store = join(SCRATCH, "verdict-store");
  const ledger = join(SCRATCH, "verdict-ledger.jsonl");
  mkdirSync(store, { recursive: true });
  writeFileSync(ledger, ""); // append-only surface; the server refuses to invent one

  const seed = promisify(execFile)(PATHS.PYTHON,
    [PATHS.BRIDGE, "propose", "--key", KEY, "--fix", "把「hroblastosis」修成「fibroblastosis」",
     "--insert", "題幹", "--basis", "紙本 f 音節；read_page 附圖"],
    { timeout: 120_000, maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, REPAIR_AGENT_STORE: store } });
  await seed;

  // The reference is the SHA-256 of the draft's **own line** in the store — computed here from
  // those exact bytes, so what the event carries is what the file said, byte for byte.
  const draftsFile = join(store, "repair_drafts.jsonl");
  const line = readFileSync(draftsFile, "utf8").split("\n")[0];
  const lineSha = createHash("sha256").update(line, "utf8").digest("hex");

  const SERVER_BOOT = [
    "import importlib.util, json, sys",
    "spec = importlib.util.spec_from_file_location('ui_server', sys.argv[1])",
    "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)",
  ].join("\n");
  const runServer = (pyCode) => run(PATHS.PYTHON,
    ["-c",
     SERVER_BOOT + "\n" + pyCode + "\nprint(json.dumps(OUT, ensure_ascii=False))",
     join(AGENT_DIR, "ui", "server.py")],
    { timeout: 300_000, maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, REPAIR_AGENT_STORE: store, REPAIR_AGENT_LEDGER: ledger } });

  const { stdout: acceptOut } = await runServer(
    `OUT = mod.append_accept({'key': ${JSON.stringify(KEY)}, 'draft_sha256': '${lineSha}', 'notes': '照 v2 的 A 流程'})`);
  const accepted = JSON.parse(acceptOut).event;
  assert.equal(accepted.action, "accept", "a sandbox verdict is a real review event");
  assert.equal(accepted.reviewer, "local", "the decision is a human's — the ledger says so");
  assert.equal(accepted.source, "sandbox_accept", "and it names the entry it was pressed from");
  assert.equal(accepted.repair_draft_sha256, lineSha, "the event references the draft by checksum");
  const onDisk = readFileSync(ledger, "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(onDisk.length, 1, "exactly one event was written");
  assert.ok(onDisk[0].created_at, "and it keeps the reviewer's own timestamp");

  // Reading the verdict back marks the draft decided — from the ledger, never a shadow status.
  const marks = await run(PATHS.PYTHON,
    ["-c", SERVER_BOOT + `\nOUT = mod.sandbox_accepts_for(${JSON.stringify(KEY)})\nprint(json.dumps(OUT, ensure_ascii=False))`,
     join(AGENT_DIR, "ui", "server.py")],
    { timeout: 120_000, maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, REPAIR_AGENT_STORE: store, REPAIR_AGENT_LEDGER: ledger } });
  assert.ok(JSON.parse(marks.stdout).some((e) => e.repair_draft_sha256 === lineSha),
    "the drafts panel can see the accept where the ledger wrote it");

  // Refusals: a checksum no draft carries must not write; an unreasoned return must not learn.
  await assert.rejects(
    runServer(`OUT = mod.append_accept({'key': ${JSON.stringify(KEY)}, 'draft_sha256': '${"f".repeat(64)}', 'notes': ''})`),
    (error) => {
      assert.match(String(error.stdout || "") + String(error.stderr || ""), /no draft of key/,
        "the refusal must say what is wrong");
      return true;
    },
    "a checksum that names nothing is a forged decision");
  await assert.rejects(
    runServer(`OUT = mod.append_return({'key': ${JSON.stringify(KEY)}, 'draft_sha256': '${lineSha}', 'reason': ''})`),
    (error) => {
      assert.match(String(error.stdout || "") + String(error.stderr || ""), /must say why/,
        "the refusal must name the missing reason");
      return true;
    },
    "the return's reason is the rule raw material; empty is refused");
  const { stdout: returnOut } = await run(PATHS.PYTHON,
    ["-c", SERVER_BOOT + `\nOUT = mod.append_return({'key': ${JSON.stringify(KEY)}, 'draft_sha256': '${lineSha}', 'reason': '選項斜體與紙本不符'})\nprint(json.dumps(OUT, ensure_ascii=False))`,
     join(AGENT_DIR, "ui", "server.py")],
    { timeout: 120_000, maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, REPAIR_AGENT_STORE: store, REPAIR_AGENT_LEDGER: ledger } });
  const returnRow = JSON.parse(returnOut).appended;
  assert.equal(returnRow.action, "repair_return", "a return is its own stream action");
  assert.equal(returnRow.repair_draft_sha256, lineSha, "joinable to the draft it rejected");
  assert.match(returnRow.reason, /斜體/);

  // The machine-never-presses guard is structural: no accept route exists outside the server, and
  // the bridge — the agent's whole surface — contains none of it.
  const bridgeSource = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  assert.equal(/append_accept|sandbox_accept/.test(bridgeSource), false,
    "no ledger-writing route exists on the agent's side of the wall");
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
 * Before the rules house (2026-09-30), `principles()` walked up three levels from
 * `REPAIR_AGENT_STORE` to find the designer's approved rules. That path was correct only while the
 * store was the default — the moment it was redirected (which `run_tests.sh` and the README both
 * do), the walk landed elsewhere and the function returned `[]`. Measured: **0 of 19 principles**,
 * with no error and a shorter prompt that still built.
 *
 * The rules house keeps the rule of that failure: the prompt must carry the same rules wherever
 * the store points, and a broken house must be *loud* (the old shape went silent).
 */
test("the designer's principles survive a redirected store", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);

  const script = `import { systemPrompt, AGENT_DIR } from "${new URL("./lib/identity.mjs", import.meta.url).pathname}";
import { rulesPath } from "${new URL("./lib/rules.mjs", import.meta.url).pathname}";
const prompt = systemPrompt();
console.log(JSON.stringify({ agentDir: AGENT_DIR,
  labelled: (prompt.match(/^- \\[p\\d+\\]/gm) || []).length }));`;

  const normal = JSON.parse((await run("node", ["--input-type=module", "-e", script],
    { cwd: AGENT_DIR, timeout: 300_000 })).stdout.trim().split("\n").pop());
  const redirected = JSON.parse((await run("node", ["--input-type=module", "-e", script],
    { cwd: AGENT_DIR, env: { ...process.env, REPAIR_AGENT_STORE: "/tmp/not-the-store" },
      timeout: 300_000 })).stdout.trim().split("\n").pop());

  // If the house were empty the count is 0 for a real reason and the test cannot measure anything;
  // say so rather than passing vacuously.
  assert.ok(normal.labelled > 0,
    "the designer's rules must be injected at all — otherwise this test proves nothing");
  assert.equal(redirected.labelled, normal.labelled,
    `redirecting the store must not change the injected rules (got ${redirected.labelled} of ` +
    `${normal.labelled}) — the house is not a store input`);
  // Negative control: the shape that failed. Scope it to where the *house* resolves — hits.jsonl
  // **should** follow the store (they are run facts); it is the rules themselves that must not.
  const rulesSource = stripDocstringsAndComments(
    readFileSync(new URL("./lib/rules.mjs", import.meta.url), "utf8"));
  const rulesPathBody = rulesSource.slice(rulesSource.indexOf("export function rulesPath"),
                                          rulesSource.indexOf("export function hitsPath"));
  assert.equal(/REPAIR_AGENT_STORE/.test(rulesPathBody), false,
    "the rules house path must not be derived from the store (the store is not where rules live)");
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
  // hand-rolled converter, which would be a second opinion about how a question looks. The stem
  // render takes the **previewed** fix's rendered html first (`_fix_html`, same server-side
  // renderer) and falls back to the question's own `stem_html` when not previewing.
  const ui = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  assert.match(ui, /platformHtml\(question\._fix_html \|\| question\.stem_html/,
    "the stem must render the previewed fix via the platform view first, falling back to the question's own stem_html");
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
 * The conductor/worker boundary and the full-text draft contract (owner 2026-10-03).
 *
 * Two measured failures: the chat 指揮者 called `read_page` — the transcription engine, i.e.
 * 做事模型 — with her own hands (chat transcript 2026-09-30 03:37); and she proposed a draft whose
 * `fix` was a suggestion sentence, which the stem preview applied verbatim and erased the question.
 * The chat must not carry the worker's pen, and a preview that would replace a whole field with
 * something that is not the resulting text must refuse visibly.
 */
test("the chat conductor carries no worker pen, and a bad fix cannot erase the stem", () => {
  const chat = readFileSync(new URL("./ui/chat.mjs", import.meta.url), "utf8");
  assert.match(chat, /omit:\s*\["read_page"\]/,
    "the conductor must not carry read_page — transcription is one-shot-run work");
  const session = readFileSync(new URL("./lib/session.mjs", import.meta.url), "utf8");
  assert.match(session, /BUILTIN_TOOLS\.filter\(\(name\) => !omit\.includes\(name\)\)/,
    "an omit that hid only custom tools would still ship the pen");
  // The fix contract lives where the erasure happened: appliedQuestion replaces the whole field.
  const html = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  assert.match(html, /_previewBlocked/,
    "a stem preview whose fix is not the full resulting text must refuse visibly, not erase the stem");
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

  // 2026-10-03: prompt archaeology (`prompt_system`/`prompt_user`) no longer rides the view, so the
  // markers for it are gone too — what must reach the page is the verdict, the fix, and the raw
  // output (the evidence), plus the pointer to the jsonl for the full prompt.
  for (const marker of ["WHERE-MARKER", "RAW-MARKER", "question_ai_findings.jsonl"]) {
    assert.ok(result.pipeline.includes(marker),
      `${marker} must reach the page — the parsed verdict and raw output are the evidence`);
  }
  assert.ok(!result.pipeline.includes("SYSTEM-MARKER") && !result.pipeline.includes("USER-MARKER"),
    "prompt archaeology must not render from the view (it is not in the payload)");
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
  // 2026-10-03: the **prompt archaeology** (`prompt_system`/`prompt_user`/`orchestration`/
  // `principles`) is stripped from the view — past runs' prompts are not evidence, and they cost
  // the conductor ~4k input tokens per tool call (measured: q051 get_question 22.6k → 8.7k chars).
  // What "what the AI was shown" means to a person is still carried: the parsed verdict, the raw
  // completion, which model said it, and when. The full rows stay on disk in the jsonl.
  for (const field of ["prompt_system", "prompt_user", "orchestration", "principles"]) {
    assert.equal(field in row, false, `${field} must be stripped from the view (prompt archaeology, not evidence)`);
  }
  assert.ok(row.finding && row.finding.where, "the parsed answer is carried");
  assert.ok(row.model, "and which model said it");

  // The bridge must also be the one that reads it, not the UI guessing from another file.
  const source = readFileSync(new URL("./bridge.py", import.meta.url), "utf8");
  assert.match(source, /question_ai_findings\.jsonl/, "the bridge names the stream it reads");
  assert.match(source, /"ai_findings": \[/, "and puts the trimmed rows in the view");
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

  // Mislabel measured 2026-09-30 from the designer's screenshot: the ledger also holds machine
  // repair rows, and the renderer printed them as 設計者（v2） — a machine name on a human. The
  // authorship must be computed once, in the bridge, from **v2's own** closed prefix set, and the
  // renderer branches on that flag.
  //
  // The machine **case** is found by streaming the ledger for the first row whose reviewer carries
  // a repair prefix, then loading that key. Found the over-strict way on 2026-09-30: picking the
  // case from the annotated dispute assumed the designer's question carries machine rows too —
  // after its queue was rebuilt it carries only his own rows, so the assertion could no longer see
  // a machine row the ledger really has. The key is now defined by the stream, not by the notes.
  const { stdout: prefixJson } = await run(PATHS.PYTHON,
    ["-c", "import json,sys; sys.path.insert(0, sys.argv[1]); from qbr.review_ui.constants import REPAIR_REVIEWER_PREFIXES; print(json.dumps(list(REPAIR_REVIEWER_PREFIXES)))",
     join(PATHS.CATALOG, "qbr", "src")],
    { timeout: 300_000 });
  const prefixes = JSON.parse(prefixJson);
  const { createReadStream } = await import("node:fs");
  const { createInterface } = await import("node:readline");
  const ledger = join(PATHS.CATALOG, "qbr", "data", "review-queues", "live", "review-ui",
                      "question_review_events.jsonl");
  let machineKey = null;
  const lines = createInterface({ input: createReadStream(ledger), crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.includes('"reviewer"')) continue;
    try {
      const row = JSON.parse(line);
      if (prefixes.some((prefix) => String(row.reviewer || "").startsWith(prefix))) {
        machineKey = String(row.candidate_key || row.canonical_question_key || "");
        break;
      }
    } catch { /* the ledger's last line can be mid-write; skip it */ }
  }
  assert.ok(machineKey, "the ledger really carries a machine repair row to test the label against");
  const { stdout: mq } = await run(PATHS.PYTHON, [PATHS.BRIDGE, "question", "--key", machineKey],
                                  { timeout: 300_000, maxBuffer: 64 * 1024 * 1024 });
  const machineView = JSON.parse(mq);
  assert.ok(machineView.human_events.some((row) => row.machine_reviewer === true),
    "the streamed machine row arrives labelled machine in the bridge's view");
  assert.ok(machineView.human_events.every((row) => typeof row.machine_reviewer === "boolean"),
    "the bridge labels every ledger row; an unlabelled row would read as the designer's");
  const htmlUi = readFileSync(new URL("./ui/index.html", import.meta.url), "utf8");
  assert.match(htmlUi, /row\.machine_reviewer === true/,
    "the renderer branches on the bridge-computed authorship, not its own copy of the list");
  assert.ok(!/isMachine\s*=/.test(htmlUi),
    "no second copy of the prefix list may live in the UI");
});

/**
 * A question-bound conversation really receives the question — proved from the transcript, not the source.
 *
 * Why this test exists (2026-09-29): `sessionFor()` decided "this session was restored" **after**
 * calling `buildSession()`, and `buildSession()` writes the session's system message, so the answer
 * was always "yes". The seed was therefore never sent to any conversation, new or old. The source
 * checks above passed the whole time — they looked for `getEntries().length > 0` and `seeded:
 * restored`, which were both present and both useless in that order.
 *
 * What it looked like from the designer's side (verbatim answer from a real turn, question key
 * supplied by the UI): 「你只說「這一題」，卻沒給 candidate_key，我無法判斷是哪一道」. Reading the
 * session file showed `system → user` with no question in between.
 *
 * So this drives the real child process and reads what Pi actually recorded. Negative control: on
 * the old order the seed is absent, and the assertion fails. Second half: a restart must **continue**
 * the conversation without seeding it a second time, which is the case that protects the
 * `restored` half of the same expression.
 */
test("a fresh bound conversation is seeded with the question, and a restart is not seeded twice", async () => {
  const { spawn } = await import("node:child_process");
  const { readdirSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const store = mkdtempSync(join(tmpdir(), "chat-seed-"));
  // `fileURLToPath`, not `.pathname`: this repository lives under a path with a space in it, and
  // `.pathname` hands back `AI%20workspace` — the child then fails to resolve its own entry point
  // and the test waits for a turn that can never arrive. Measured 2026-09-29: that cost the suite
  // 180 s of dead waiting, which is the failure the exit check below now makes loud instead of slow.
  const chat = fileURLToPath(new URL("./ui/chat.mjs", import.meta.url));
  const key = "moex:115090:311:0704:1:question:q001";
  const marker = "設計者正在跟你一起看這一題";

  // Count **structured user messages** carrying the seed, not raw substrings: the model's own
  // thinking sometimes *quotes* the seed line back (measured 2026-09-30 — the thinking field
  // contained a verbatim quotation while quoting is harmless), and a raw grep then reads one seed
  // as two. A re-seed, the thing this test must catch, always adds another user message.
  const seedsInStore = () => {
    const dir = join(store, "chat-sessions");
    if (!existsSync(dir)) return 0;
    let n = 0;
    for (const sub of readdirSync(dir)) {
      for (const file of readdirSync(join(dir, sub))) {
        for (const line of readFileSync(join(dir, sub, file), "utf8").split("\n")) {
          if (!line.trim() || !line.includes(marker)) continue;
          try {
            const row = JSON.parse(line);
            const message = row?.message;
            if (message?.role === "user") n += 1;
          } catch {
            // a torn line is not a seed; the transcript is written structured
          }
        }
      }
    }
    return n;
  };

  // The turn is done when the harness's own transcript holds the designer's sentence: that line is
  // written before the model is asked, so waiting on it does not wait for a full answer.
  const turnsInStore = () => {
    const file = join(store, "chat.jsonl");
    if (!existsSync(file)) return 0;
    return readFileSync(file, "utf8").split("\n")
      .filter((line) => line.includes('"role":"designer"') || line.includes('"role": "designer"')).length;
  };

  const askOnce = async (id, wantTurns) => {
    const child = spawn("node", [chat],
      { cwd: PATHS.CATALOG, env: { ...process.env, REPAIR_AGENT_STORE: store },
        stdio: ["pipe", "pipe", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.stdin.write(JSON.stringify({ id, op: "ask", key, text: "用一句話說明這一題在做什麼。" }) + "\n");
    const deadline = Date.now() + 180_000;
    while (Date.now() < deadline && turnsInStore() < wantTurns) {
      if (child.exitCode !== null) break;  // a dead child cannot record a turn; say so at once
      await new Promise((done) => setTimeout(done, 500));
    }
    child.kill("SIGKILL");
    return { stderr, turns: turnsInStore() };
  };

  try {
    const first = await askOnce("seed-1", 1);
    assert.ok(first.turns >= 1, `the turn must be recorded (stderr: ${first.stderr.slice(0, 300)})`);
    assert.equal(seedsInStore(), 1,
      `the question must reach the model's context on a fresh session (stderr: ${first.stderr.slice(0, 300)})`);

    // A second process on the same store must **continue** that session. If it re-seeded, the
    // question would be in the conversation twice and the model would read the question as new.
    const second = await askOnce("seed-2", 2);
    assert.ok(second.turns >= 2, `the second turn must be recorded (stderr: ${second.stderr.slice(0, 300)})`);
    assert.equal(seedsInStore(), 1,
      "a restart continues the same session instead of seeding the question a second time");
  } finally {
    rmSync(store, { recursive: true, force: true });
  }
});

/**
 * The brain is one engine, named once, and it is a model the pipeline actually serves.
 *
 * Designer's ruling 2026-09-29:「1 換 occamy（腦與眼同一顆）」. Two things follow, and each has a
 * failure that would be silent:
 *
 *   1. `BRAIN` must name a provider **that `engines.py` serves**, with the model id that engine
 *      records. A typo'd or stale id does not fail loudly at import time; it fails inside a run as
 *      "找不到模型", and every number produced before then belongs to no model at all.
 *   2. `BRAIN_ENGINE` (identity) and `BRAIN` (session) must agree, because `read_page` uses the
 *      first to decide whether a reading is a second opinion while the session uses the second to
 *      pick the model. Two constants for one fact is the pair-of-tables defect this repo keeps
 *      tripping over.
 */
test("the brain names an engine the pipeline serves, and both constants agree", async () => {
  const { BRAIN } = await import("./lib/session.mjs");
  const { BRAIN_ENGINE } = await import("./lib/identity.mjs");
  const [provider, id] = BRAIN.split("/");
  assert.equal(provider, BRAIN_ENGINE,
    "the brain's provider is BRAIN_ENGINE — one engine, declared once");

  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const { stdout } = await run(PATHS.PYTHON, ["-c", `
import json, sys
sys.path.insert(0, r"${PATHS.CATALOG}/qbr/src")
from qbr import engines
print(json.dumps({name: record["name"] for name, record in engines.endpoints().items()}))
`], { timeout: 60_000 });
  const endpoints = JSON.parse(stdout);
  assert.ok(endpoints[provider], `engines.py must serve ${provider}; it serves: ${Object.keys(endpoints)}`);
  assert.equal(id, endpoints[provider],
    `the model id must be the one engines.py records for ${provider} (${endpoints[provider]})`);

  // Negative control: the previous default. Nothing may still name the old engine as the brain.
  assert.notEqual(BRAIN, "mtplx-35b/ornith-1.5-mtplx-35b",
    "the 4-bit engine is no longer the brain (ruling of 2026-09-29)");
});

/**
 * One engine is not a second opinion, and the tool says which one it is.
 *
 * The ruling made the brain and the default reader the same model, so the sentence this project
 * used to print — 「兩個引擎都看」— became false for the default call. The label is derived from the
 * engine the reading actually came from, which is why the same-engine case cannot be hardcoded:
 * a hardcoded `independent: true` would make every re-read look like independent agreement.
 *
 * Negative control: ask the real tool for a reading from the *other* engine and require it to be
 * marked independent. If the label were hardcoded false, or taken from the requested engine rather
 * than the one that answered, this fails.
 */
test("a reading is only called independent when the engine differs from the brain", async () => {
  const source = readFileSync(new URL("./lib/tools.mjs", import.meta.url), "utf8");
  assert.match(source, /result\.independent = used !== BRAIN_ENGINE/,
    "independence is computed against the brain's own engine");
  assert.match(source, /const used = result\.engine \|\| engine/,
    "and from the engine that answered, not from the one that was requested");
  // The default must be passed through explicitly: leaving it to the bridge's own default is what
  // lets the two defaults drift apart.
  assert.match(source, /const engine = params\.engine \|\| BRAIN_ENGINE/,
    "an unset engine means the brain's engine, named here");

  const { toolsFor } = await import("./lib/tools.mjs");
  const { Type } = await import("typebox");
  const tool = toolsFor(Type).find((entry) => entry.name === "read_page");
  assert.ok(tool, "read_page must exist");
  const result = await tool.execute("t1", { key: "moex:115090:311:0704:1:question:q001", engine: "mtplx-35b" });
  const payload = result?.details ?? result;
  assert.ok(!payload.error, `the reading must succeed: ${JSON.stringify(payload).slice(0, 200)}`);
  assert.equal(payload.independent, true,
    "a reading from the other engine is independent evidence");
  assert.equal(payload.engine, "mtplx-35b", "and it says which engine produced it");
});

/**
 * The brain has to **answer**, not just be configured.
 *
 * Two 500/400s were found by running the agent, not by any test here (2026-09-29):
 *
 *   1. Pi sends the system prompt as `role: "developer"` when it takes the model for a reasoning
 *      one, and mlx_vlm's Jinja chat template refuses any role outside system/user/assistant/tool —
 *      `500 status code (no body)`, and on the server `Unexpected message role`. MTPLX answers the
 *      same request with 200, so the old brain hid the defect. Declared as
 *      `compat.supportsDeveloperRole: false`, which Pi reads from **`model.compat`** — a
 *      provider-level copy is not merged, which is why the first attempt at this fix did nothing.
 *   2. `max_tokens` was the same 32768 for every engine, but this deployment runs with
 *      `MAX_KV_SIZE=65536`, so `prompt + max_tokens` overflowed and the server answered
 *      `400 {"detail":"Request needs 72264 context tokens (39496 prompt + 32768 max generation),
 *      but MAX_KV_SIZE is 65536."}` — again a body-less error at the client. The cap now travels
 *      with the engine (`engines.py`).
 *
 * This test therefore does the thing that was missing: it builds the real session (real prompt,
 * real tools) and makes it answer, and it holds the two facts that made it fail as negative
 * controls against the servers themselves.
 */
test("the configured brain answers, and the roles/limits it fails on are still refused", async (t) => {
  const { buildSession, BRAIN } = await import("./lib/session.mjs");
  const { SessionManager } = await import("@earendil-works/pi-coding-agent");
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const [provider] = BRAIN.split("/");
  const { stdout } = await run(PATHS.PYTHON, ["-c", `
import json, sys
sys.path.insert(0, r"${PATHS.CATALOG}/qbr/src")
from qbr import engines
record = engines.endpoints()[${JSON.stringify(provider)}]
print(json.dumps(record))
`], { timeout: 60_000 });
  const engine = JSON.parse(stdout);

  const { session, model } = await buildSession({
    sessionManager: SessionManager.inMemory(PATHS.CATALOG),
    thinking: "low",
  });
  assert.equal(model.compat?.supportsDeveloperRole, false,
    "Pi must send the system prompt as `system`; the local vision server rejects `developer`");
  assert.equal(model.maxTokens, engine.max_output_tokens,
    "the generation cap must come from the engine's own record, not from a constant here");

  // Negative control 1: the server really does refuse the role Pi would otherwise send. If this
  // ever starts passing, the flag above is no longer load-bearing and should be removed with the
  // evidence, not kept as folklore.
  const developer = await fetch(`${engine.url}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: engine.name, messages: [{ role: "developer", content: "hi" }], max_tokens: 8 }),
  });
  assert.notEqual(developer.status, 200,
    "the engine must still reject `developer`; otherwise compat.supportsDeveloperRole is unnecessary");

  // Negative control 2: the deployment refuses a request whose prompt + max_tokens exceeds its
  // KV budget, which is why the cap is per engine. A single word is enough to overflow when the
  // requested generation is the whole context.
  const overflow = await fetch(`${engine.url}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: engine.name, messages: [{ role: "user", content: "hi" }],
                           max_tokens: engine.context_window }),
  });
  assert.notEqual(overflow.status, 200,
    "asking for the whole context as output must still be refused; the cap exists for this");

  const answers = [];
  const unsubscribe = session.subscribe((event) => {
    if (event.type === "message_end" && event.message.role === "assistant") {
      const content = event.message.content;
      answers.push(typeof content === "string" ? content
        : (Array.isArray(content) ? content.filter((p) => p?.type === "text").map((p) => p.text).join("") : ""));
    }
    if (event.type === "error") t.diagnostic(`error event: ${JSON.stringify(event).slice(0, 200)}`);
  });
  try {
    await session.prompt("不用動任何工具，只回答兩個字：收到");
  } finally {
    unsubscribe();
  }
  assert.ok(answers.join("").trim().length > 0,
    `the brain must answer; it produced ${answers.length} assistant messages (${BRAIN})`);
});

// ===========================================================================
// The rules house (2026-09-30, §9.11-6/7/8).
//
// `rules/rules.json` is the single authority: one entry per distinct rule, provenance preserved,
// `active`/`superseded_by` lineage, hits measured in a store-side stream, injection ordered by
// recent hits under a budget, compression done by an *approved* pass that this module never
// performs. Each test below ships the case that must fail on the old behaviour.
// ===========================================================================

import {
  loadRules, promptRules, recordRuleHit, readHits, hitIndex, compressionReport,
  rulesPath, hitsPath, DUPLICATE_JACCARD,
} from "./lib/rules.mjs";

/** A minimal but fully-populated house entry, so the validator sees a real shape. */
const fxRule = (id, text, over = {}) => ({
  principle_id: id,
  text,
  rationale: null,
  evidence: [],
  reviewer: "principle_curator",
  source: "comment_review",
  scope: null,
  action: null,
  created_at: "2026-09-24T11:42:35",
  schema: "qbr_review_principle_v0.1",
  active: true,
  superseded_by: null,
  occurrences: 1,
  other_row_stamps: [],
  ...over,
});

/** Write a fixture house (never the tracked one) and return its path. */
function houseFixture(rules) {
  const dir = join(SCRATCH, `house-fx-${Math.random().toString(36).slice(2)}`);
  mkdirSync(dir, { recursive: true });
  const file = join(dir, "rules.json");
  writeFileSync(file, JSON.stringify({ schema: "qbr_review_rules_v0.1", version: "fx", rules }, null, 1) + "\n");
  return file;
}

const writeHitsFixture = (path, rows) =>
  writeFileSync(path, rows.map((r) => JSON.stringify(r)).join("\n") + "\n");
const fxHit = (rule_id, at, key = "") => ({ schema: "rule_hit_v0.1", rule_id, at, by: "agent", key, why: "" });

test("a broken rules house stops the run loudly — the old stream shape went silent", () => {
  const broken = [
    ["wrong schema", [{ schema: "other", rules: [fxRule("p1", "x")] }]],
    ["duplicate id", [{ schema: "qbr_review_rules_v0.1", rules: [fxRule("p1", "x"), fxRule("p1", "y")] }]],
    ["textless entry", [{ schema: "qbr_review_rules_v0.1", rules: [fxRule("p1", "")] }]],
    ["non-boolean active", [{ schema: "qbr_review_rules_v0.1", rules: [fxRule("p1", "x", { active: 1 })] }]],
  ];
  for (const [label, doc] of broken) {
    const file = houseFixture(doc[0].rules ? doc[0].rules : []);
    // `houseFixture` wrote the doc's rules; the doc wrapper itself varies per case, so rewrite raw.
    writeFileSync(file, JSON.stringify(label === "wrong schema"
      ? { schema: doc[0].schema, rules: doc[0].rules }
      : { schema: "qbr_review_rules_v0.1", rules: doc[0].rules }, null, 1) + "\n");
    assert.throws(() => loadRules(file), new RegExp(label === "wrong schema" ? "expected" : "rules house"),
      `the house must refuse: ${label}`);
  }
  // The real house, by contrast, must validate — it is what every run stands on.
  const real = loadRules(rulesPath());
  assert.ok(real.rules.length > 0, "the real rules house must load and carry rules");
});

test("record_rule_hit measures use, duplicates collapse, unknown ids refuse", () => {
  const house = houseFixture([fxRule("p1", "alpha rule"), fxRule("p2", "beta rule")]);
  const hits = join(SCRATCH, "fx-hits.jsonl");
  const first = recordRuleHit("p1", { key: "moex:1:q7", why: "subscript check" }, { rulesPath: house, hitsPath: hits });
  const again = recordRuleHit("p1", { key: "moex:1:q7", why: "subscript check" }, { rulesPath: house, hitsPath: hits });
  assert.ok(first.ok && !first.duplicated, "first record must append");
  assert.ok(again.duplicated, "same (rule, key) must collapse — a question used a rule or it did not");
  // The negative control: an id that is not in the house must refuse, not vanish into a stray stream.
  assert.throws(() => recordRuleHit("p99", { key: "moex:1:q8" }, { rulesPath: house, hitsPath: hits }),
    /no active rule/, "unknown rule id must fail loudly");
  // A keyless hit records (tooling may hit outside a single question) but never dedupes.
  process.env.REPAIR_AGENT_PROMPT_VERSION = "pv-test";
  try {
    assert.ok(recordRuleHit("p2", {}, { rulesPath: house, hitsPath: hits }).ok);
  } finally {
    delete process.env.REPAIR_AGENT_PROMPT_VERSION;
  }
  const { hits: rows, torn } = readHits(hits);
  assert.equal(torn.length, 0, "nothing written by this module may be torn");
  assert.deepEqual(
    rows.map((r) => ({ rule_id: r.rule_id, key: r.key, schema: r.schema, by: r.by,
                       prompt_version: r.prompt_version })),
    [{ rule_id: "p1", key: "moex:1:q7", schema: "rule_hit_v0.1", by: "agent", prompt_version: "" },
     { rule_id: "p2", key: "", schema: "rule_hit_v0.1", by: "agent", prompt_version: "pv-test" }],
    "hit facts must carry the recording identity, the rule they credit, and the prompt they ran under");
  const index = hitIndex(rows);
  assert.equal(index.get("p1").count, 1, "collapsed duplicates must count once");
});

test("the injection budget keeps recently-hit rules and ranks the rest deterministically", () => {
  const house = houseFixture([
    fxRule("p1", "r1", { created_at: "2026-09-24T10:00:00" }),
    fxRule("p2", "r2", { created_at: "2026-09-24T11:00:00" }),
    fxRule("p3", "r3", { created_at: "2026-09-24T09:00:00" }),
    fxRule("p4", "r4", { created_at: "2026-09-24T12:00:00", active: false }),
    fxRule("p5", "r5", { created_at: "2026-09-24T08:00:00", superseded_by: "p1" }),
  ]);
  const hits = join(SCRATCH, "fx-order-hits.jsonl");
  writeHitsFixture(hits, [
    fxHit("p2", "2026-09-30T08:00:00"), // less recent
    fxHit("p1", "2026-09-30T09:00:00"), // most recent
  ]);
  const all = promptRules({ path: house, hits: readHits(hits), budget: 0 });
  assert.deepEqual(all.ordered.map((r) => r.id), ["p1", "p2", "p3"],
    "without a budget every active non-superseded rule injects, hot first, never-hit by curation age");
  assert.equal(all.capped, false);
  const slice = promptRules({ path: house, hits: readHits(hits), budget: 2 });
  assert.deepEqual(slice.ordered.map((r) => r.id), ["p1", "p2"],
    "under budget, the recently-hit rules stay in the prompt");
  assert.deepEqual([slice.capped, slice.total, slice.budget], [true, 3, 2]);
});

test("the compression pass reports candidates and never writes the house", () => {
  const long = "上下標的核對要看到字母，不是只看到數字。紙本的 GPIIb/IIIa、VD、KM、GABA_B 與 r_t 都要逐個字母核對；數字對不上就是錯，要回到紙本那一行。";
  const twin = "上下標的核對要看到字母，不是只看到數字。紙本的 GPIIb/IIIa、VD、KM、GABA_B 與 r_t 都要逐個字母核對；數字對不上就是錯，須回到紙本那一行。";
  const unrelated = "表格的內容不要靠文字層推論或重排。紙本的表格在文字層只剩一串數字，遇到表格時要回到原表。";
  const house = houseFixture([fxRule("p1", long), fxRule("p8", twin), fxRule("p3", unrelated)]);
  const before = readFileSync(house, "utf8");
  const report = compressionReport({ rulesPath: house, hitsPath: join(SCRATCH, "fx-empty-hits.jsonl") });
  const top = report.duplicate_candidates[0];
  assert.equal(top?.rules.join(","), "p1,p8", "near-duplicates must surface with their measured score");
  assert.ok(top.similarity >= DUPLICATE_JACCARD);
  assert.ok(!report.duplicate_candidates.some((p) => p.rules.join(",").includes("p3")),
    "a distinct-topic rule must not be a candidate");
  assert.deepEqual(report.zero_hit, ["p1", "p8", "p3"], "with no hits everything reads zero-hit — honestly");
  assert.equal(readFileSync(house, "utf8"), before,
    "the compression pass is a report; it must not touch the house it measured");
});

test("the rules house env contract: budget refusal and stream-inbox drift surface in the report", () => {
  const house = houseFixture([fxRule("p1", "alpha rule")]);
  process.env.REPAIR_AGENT_RULE_BUDGET = "not-a-number";
  try {
    assert.throws(() => promptRules({ path: house }),
      /REPAIR_AGENT_RULE_BUDGET/, "a broken budget env must refuse to guess");
  } finally {
    delete process.env.REPAIR_AGENT_RULE_BUDGET;
  }
  // Inbox drift: the house records which stream state it imported; new rows must be reported,
  // never injected by themselves (§9.11-6: 規則誕生需要設計者).
  const stream = join(SCRATCH, "fx-stream.jsonl");
  const imported = houseFixture([fxRule("p1", "alpha rule")]);
  writeFileSync(house, readFileSync(imported, "utf8").replace('"version": "fx"', '"version": "fx",\n "imported_from": {"file": ' + JSON.stringify(stream) + ', "sha256": "old", "row_count": 1, "empty_rows": 0, "empty_row_ids": [], "torn_lines": []}'), "utf8");
  writeFileSync(stream, JSON.stringify({ principle_id: "p2", text: "a brand-new rule awaiting approval", reviewer: "principle_curator", created_at: "2026-09-30T09:00:00" }) + "\n", "utf8");
  const report = compressionReport({ rulesPath: house, hitsPath: join(SCRATCH, "fx-drift-hits.jsonl") });
  assert.ok(report.inbox?.drifted === true, "a changed stream behind the house must be visible");
  assert.deepEqual(report.inbox.rows_not_in_house.map((r) => r.principle_id), ["p2"],
    "the drift is the row's arrival; its id is reported, not invented");
});

test("the record_rule_hit tool is registered and writes only its own store-side stream", async () => {
  const tool = toolsFor(Type).find((t) => t.name === "record_rule_hit");
  assert.ok(tool, "the agent must be able to record a rule hit");
  assert.equal(tool.parameters.properties.rule.type, "string",
    "the tool's rule parameter must be a string schema — it carries the house's id");
  // p2 was retired (compression pass 1 → superseded_by p18), and this test earned the refusal the
  // hard way (measured 2026-09-30): hits may only credit *active* rules. As of compress-2, p18 is
  // the only active entry left (Q57#7 retired p1/p3/p4/p5/p7/p11) — the seam must credit it.
  const firstWrap = await tool.execute("t1", { rule: "p18", key: "moex:smoke:1", why: "tool seam" });
  const first = firstWrap?.details ?? firstWrap;
  const secondWrap = await tool.execute("t2", { rule: "p18", key: "moex:smoke:1", why: "tool seam" });
  const second = secondWrap?.details ?? secondWrap;
  assert.equal(first.ok, true, "the tool seam writes the same store the library reads");
  assert.equal(second.duplicated, true, "the tool must not double-count one question's hit");
  // Negative control: a *retired* rule loses its tool credit too — no new facts may land on it.
  // p1 as the negative is the freshly retired one (compress-2, Q57#7): the same loud refusal.
  const retiredWrap = await tool.execute("t3", { rule: "p1", key: "moex:smoke:2", why: "old wording" });
  const retired = retiredWrap?.details ?? retiredWrap;
  assert.match(String(retired.error), /no active rule/,
    "hits must refuse a superseded rule — the same loud refusal as an unknown id");
  // The tool takes no paths: the store-side default seam is where the facts must land.
  assert.equal(hitsPath(), join(SCRATCH, "rule_hits.jsonl"),
    "without REPAIR_AGENT_HITS the tool writes the redirected store's rule_hits.jsonl");
  assert.equal(readHits(hitsPath()).hits.length, 1, "exactly one fact per (rule, key)");
});
