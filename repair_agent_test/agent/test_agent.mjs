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
  const source = readFileSync(new URL("./agent.mjs", import.meta.url), "utf8");
  assert.match(source, /cwd: PATHS\.CATALOG/, "both the loader and the session work from the root");
  // Negative control: the old value. If this comes back, the failure is a long tool trace with a
  // correct answer at the end — invisible unless someone reads the trace.
  assert.equal(/cwd: HERE/.test(source), false,
    "the agent directory is not the data root; paths would break again");
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
  const source = readFileSync(new URL("./agent.mjs", import.meta.url), "utf8");
  assert.match(source, /engines\.endpoints\(\)/,
    "the provider table must be read from the pipeline's own module");
  // Negative control: a hard-coded localhost port in agent.mjs would be a second table. The
  // ports live in engines.py; only that file may name them.
  assert.equal(/(18120|18130|8088|8888)/.test(source), false,
    "no engine port may be hard-coded in the agent");
});

test("the agent refuses to silently fall back to a different model", () => {
  const source = readFileSync(new URL("./agent.mjs", import.meta.url), "utf8");
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
