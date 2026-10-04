/**
 * The chat box's backend: **one long-lived Pi session per question**, spoken to over JSON-lines.
 *
 * Why this exists at all: a Pi session is stateful (it holds the conversation and its own compaction),
 * and `agent.mjs` builds one per process and exits. A conversation has to outlive one HTTP request,
 * so something must hold the session between requests. That something is this file.
 *
 * The designer asked for the **question-bound** variant first (2026-09-28: 「3 兩者都要，1先做」).
 * So the session is keyed by `candidate_key`: talking about q042 does not leak into q068. The
 * unbound conversation is the second variant and is deliberately not built here — one session that
 * both is and is not about a question is the "two things that can disagree" shape the charter bans,
 * so they are two maps, not one map with a flag.
 *
 * Protocol (stdin/stdout, one JSON object per line):
 *
 *   in   {"id": "...", "op": "ask",  "key": "...", "text": "...", "reset": false}
 *   in   {"id": "...", "op": "stop"}
 *   in   {"id": "...", "op": "ping"}
 *   out  {"id": "...", "event": "delta", "text": "..."}      — as the model streams
 *   out  {"id": "...", "event": "tool",  "tool": "...", "args": {...}}
 *   out  {"id": "...", "event": "tool_result", "tool": "...", "ok": true, "summary": {...}}
 *   out  {"id": "...", "event": "turn",  "role": "designer"|"agent", "text": "..."}   — the record
 *   out  {"id": "...", "event": "done",  "seconds": 12.3}
 *   out  {"id": "...", "event": "error", "error": "..."}
 *
 * It does not decide what a question is or how to render it — it asks `bridge.py` (through the same
 * tools the one-shot agent uses) and writes the transcript to the same append-only store. Nothing
 * here is a second source of `agent_feedback.jsonl`.
 */

import { SessionManager } from "@earendil-works/pi-coding-agent";
import { buildSession, BRAIN } from "../lib/session.mjs";
import { STORE_DIR, PROMPT_VERSION } from "../lib/identity.mjs";
import { PATHS } from "../lib/tools.mjs";
import { appendFileSync, mkdirSync, readFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

// The transcript of the conversation itself. Separate from `agent_feedback.jsonl`, which holds
// **judgements** (a rating about a question). A conversation and a judgement are different records:
// folding them together would make "what did the agent decide" and "what was said while deciding"
// the same file, and the ratings table would fill with sentences that rate nothing.
const CHAT = join(STORE_DIR, "chat.jsonl");

function write(record) {
  process.stdout.write(JSON.stringify({ at: new Date().toISOString(), brain: BRAIN, ...record }) + "\n");
}

function remember(record) {
  mkdirSync(STORE_DIR, { recursive: true });
  appendFileSync(CHAT, JSON.stringify({ at: new Date().toISOString(), ...record }) + "\n", "utf8");
}

/**
 * The opening context for a question-bound session.
 *
 * Sent as the **first user turn**, not folded into the system prompt: the system prompt is this
 * agent's standing identity (who it is, the five rules) and is identical for every question, while
 * this is one question. Putting a question in the system prompt would also mean rebuilding the
 * session for it, throwing away the conversation — the opposite of what a chat box is for.
 *
 * The question is loaded through the **bridge**, so the view here is byte-for-byte the view a
 * one-shot run gets. Assembling it in JavaScript would be a second reading of the candidate file,
 * and the two would drift the first time a field was added to one of them.
 */
async function seed(key) {
  // **The unbound conversation.** The designer asked for both kinds (2026-09-28: 「3 兩者都要，
  // 1先做」), and this is the second: no question in hand, so the model must be able to *find* one.
  // Without a seed the model is standing in an empty room and says so — verbatim from the
  // designer's transcript: 「我需要更多資訊才能回答這個問題——目前這則對話還沒有指定哪一道題」.
  // That answer is not a model failure, it is a missing tool: the corpus view (`see_corpus`,
  // `find_disputed`) and a sentence saying this conversation is allowed to start from nothing.
  if (!key) {
    return [
      "這一則對話**沒有綁定任何一題**——設計者會从整個題庫的角度跟你討論。",
      "",
      "你現在有一組看全局的工具，**不要因為沒有指定題目就回答「我需要更多資訊」**：",
      "- `see_corpus`：看整個題庫（每個類科幾題、有圖、被標記、被設計者點名、你判過幾題）。",
      "- `find_disputed`：列出設計者曾標記為有問題（block／comment）的題目，含**他自己寫的字**。",
      "- `find_question`：用科目／題號／關鍵字找一題；找到 key 之後再用 `get_question`。",
      "",
      "他說的是問題或方向時，先看全局、再挑一題深入，或直接回答他的問題。"
      + "如果他點出一條**可以重複使用的規則**，用 `remember_lesson` 記下來。",
      "**你是指揮者，不是做事模型**：`read_page`（引擎逐字轉錄）不在這則對話裡——那是做事模型的"
      + "筆，由一次性工單 run 去做。看紙本證據＝`crop_question` 裁下來自己 `read`（你的眼睛）；"
      + "真的需要逐字轉錄/引擎 diff，就明說「這要開一次做事 run」，不要自己硬做。",
      "**這裡是對話，不是判讀**：不要自己下 rating。**但他叫你修（例：「要補(如下圖)」、"
      + "「某字改成 X」），就用 `propose_repair` 把修法寫成草案**——草案是提議，不是 rating，"
      + "也不動正式檔。只說「應該補」而不 propose ＝ 只交了一半。"
      + "**fix 必須是改完後的完整文字**（整句題幹／整個選項，從頭到尾），不是建議、不是片段——"
      + "UI 會拿 fix 整句替換該欄位；寫「建議在X後補Y」會把原題蓋掉。",
    ].join("\n");
  }
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const { stdout } = await run(PATHS.PYTHON, [PATHS.BRIDGE, "question", "--key", key],
                               { timeout: 300_000, maxBuffer: 64 * 1024 * 1024 });
  const view = JSON.parse(stdout);
  // The figure list carries resolved paths (`path`, absolute). Handing them over is what keeps the
  // run healthy: measured 2026-09-29, a chat turn that mentioned pictures made the agent start with
  // `bash ls` and `find`, fail one, and only then `read` the four crops that were sitting exactly
  // where the tool had said. The paths are already in the view; withholding them makes the model
  // rediscover them, and the rediscovery is visible only as a long, dirty trace.
  const figures = (view.figures || []).map((f) => ({
    label: f.label, page: f.page, asset_role: f.asset_role, option_key: f.option_key,
    exists: f.exists,
    // `path` is absolute and is what `read` wants. `relative_path` is kept because it is the stable
    // one across machines, and a reviewer reading the record should see what the queue stored.
    path: f.path, relative_path: f.relative_path,
  }));
  return [
    `設計者正在跟你一起看這一題：${key}`,
    "",
    "以下是機器對這一題的紀錄（與一次性判讀時你看到的完全相同）：",
    "```json",
    JSON.stringify({
      candidate_key: view.candidate_key,
      question_number: view.question_number,
      stem: view.stem,
      options: view.options,
      answer_display: view.answer_display,
      answer_keys: view.answer_keys,
      figures,
    }, null, 2),
    "```",
    figures.length
      ? "要看圖就直接 `read` 上面 `figures[].path`——**那已經是解析好的絕對路徑**，"
        + "不必再用 `bash` 或 `find` 去找（找了只會弄髒軌跡，檔就在那裡）。"
      : "",
    view.prior_judgements?.length
      ? `\n之前已經有人／agent 說過（設計者寫的字在 notes 欄，不是 reason）：\n` +
        "```json\n" + JSON.stringify(view.prior_judgements, null, 2) + "\n```"
      : "",
    "",
    "設計者接下來會跟你說話。**先不要自己跑一輪判讀、也不要自己下 rating**——這裡是對話，"
    + "不是判讀。等他說完，再決定要看什麼、要不要 call 工具。"
    + "**你是指揮者，不是做事模型**：`read_page`（引擎逐字轉錄）不在這則對話裡；看紙本證據＝"
    + "`crop_question`＋`read`（你自己的眼睛），需要逐字轉錄就明說「要開一次做事 run」。"
    + "但**他叫你修（例：補字、改字、換圖），就用 `propose_repair` 寫成草案**——"
    + "草案是提議、不是 rating，也不動正式檔。"
    + "**fix 必須是改完後的完整文字**（整句題幹／整個選項，從頭到尾），不是建議、不是片段——"
    + "UI 會拿 fix 整句替換該欄位；寫「建議在X後補Y」會把原題蓋掉。"
    + "如果你在他的話裡聽出一個**可以重複使用的規則**（不是關於這一題而已），"
    + "用 `remember_lesson` 記下來，並在回答裡說你記了什麼。",
  ].filter(Boolean).join("\n");
}

/** One session per question. Created on first use, kept until the process ends. */
const sessions = new Map();

// Where Pi persists a session transcript. `SessionManager.create(cwd, sessionDir)` takes **two**
// arguments and the first is the cwd — measured 2026-09-29: passing only `join(STORE_DIR, ...)`
// made that the cwd and left Pi writing to its own default store under `~/.pi/agent/sessions/`,
// named after a mangled version of this path. The sessions were persisted, just not where the store
// says they are, so redirecting `REPAIR_AGENT_STORE` (which the tests do) did not move them.
const SESSION_DIR = join(STORE_DIR, "chat-sessions");

/**
 * One directory per question, so `continueRecent` finds **this** question's session and not another's.
 *
 * The key is hashed rather than used directly: it contains `:` and CJK, and a directory name is not
 * the place to find out which characters a filesystem dislikes. The readable part is kept as a
 * suffix (`q042`) so a human looking in the store can tell which conversation a file holds —
 * a directory named only by a hash is a directory nobody can audit.
 */
function sessionDirFor(key) {
  // An unbound conversation is its own session, with its own directory, so it neither inherits a
  // question's context nor leaks its own into one — the same isolation the bound chat is built on.
  const source = key || "__corpus__";
  const readable = key ? (key.split(":").pop() || "q").replace(/[^A-Za-z0-9_-]/g, "") : "corpus";
  const hash = createHash("sha256").update(source).digest("hex").slice(0, 12);
  return join(SESSION_DIR, `${readable}-${hash}`);
}

async function sessionFor(key) {
  if (sessions.has(key)) return sessions.get(key);
  // `continueRecent` continues the newest session in this question's directory, or creates one if
  // there is none. That is what makes a restart a **continuation** rather than a new conversation:
  // without it the transcript is on disk and shown in the panel while the model has none of it, so
  // 「你剛剛不是說…」 gets answered as if for the first time.
  const manager = SessionManager.continueRecent(PATHS.CATALOG, sessionDirFor(key));
  // **Measured before `buildSession`, and that order is the whole point.** `buildSession` writes the
  // session's own system message, so asking afterwards answers "does this session have entries" with
  // "yes" for a session that was created one millisecond ago — and the opening context was then
  // never sent. Found 2026-09-29 by reading a real transcript: a question-bound conversation opened
  // system → designer with no question in it, and the agent answered 「你沒給 candidate_key」 about a
  // question the UI had just handed it. `SessionManager.continueRecent` on an empty directory
  // returns 0 entries and creates no file, so this check is the one that can tell the two apart.
  const restored = manager.getEntries().length > 0;
  // 設計者 2026-10-03：對話裡的是**指揮者**；做事模型由她指揮（一次性工單 run），不是她親手
  // 調用——`read_page`（引擎逐字轉錄）因此不進對話。她看證據用自己的眼睛（crop_question＋read）。
  const built = await buildSession({ sessionManager: manager, omit: ["read_page"] });
  // Entries already present means this session was restored, so its opening context is already in
  // it. Re-sending the seed would put the question in the conversation twice. `manager` is kept
  // so a judgement written inside this conversation can name the session it belongs to.
  const entry = { ...built, seeded: restored, manager };
  sessions.set(key, entry);
  return entry;
}

function summarize(details, content) {
  const parts = Array.isArray(content) ? content.map((p) => p?.type || typeof p) : undefined;
  const payload = details;
  if (!payload || typeof payload !== "object") return parts ? { content_parts: parts } : undefined;
  if (payload.error) return { error: String(payload.error).slice(0, 400) };
  const keys = ["candidate_key", "question_number", "engine", "png_bytes", "count",
                "boxes_supplied", "reminder_saved", "appended"];
  const out = {};
  for (const k of keys) if (payload[k] !== undefined) out[k] = payload[k];
  if (parts) out.content_parts = parts.slice(0, 8);
  if (!Object.keys(out).length) {
    if (typeof payload.text === "string") out.text_chars = payload.text.length;
    else if (payload.prior_judgements) out.prior_judgements = payload.prior_judgements.length;
    else out.shape = Object.keys(payload).slice(0, 8);
  }
  if (payload.diff) {
    out.stem_differs = Boolean(payload.diff.stem_differs);
    out.option_differs = Object.keys(payload.diff.option_differs || {});
  }
  return out;
}

let current = null;
// stdin has closed but a turn is still running (or has not finished starting). Exit at the end of
// that turn, not now.
let closing = false;
// Counted **synchronously** when an `ask` line is read. `current` is set only after the session has
// been built (seconds of model-runtime setup), and the stdin `end` that follows a piped input
// arrives long before that — measured 2026-09-29: the process exited during setup, so the caller
// saw one `ready` line and no answer. A synchronous counter is what makes "busy" true in time.
let inflight = 0;

async function ask(message) {
  const { id, key, text, reset } = message;
  if (!text || !text.trim()) {
    write({ id, event: "error", error: "沒有文字" });
    return;
  }
  const started = Date.now();
  // Token metering for this ask (reset every turn; see the `done` event below).
  const turnUsage = { calls: 0 };
  const entry = await sessionFor(key);
  const { session } = entry;

  // The stamp a judgement written **inside this conversation** will carry — `bridge.py` reads these
  // at write time (workplan 2.1, 2026-09-30). A turn is one question: these are set *before* the
  // model runs, so a `record_judgement` inside this turn names this question's session, and the
  // next ask overwrites them for its own — a judgement can only be written inside a turn, so no
  // other turn can read a stale value.
  process.env.REPAIR_AGENT_RUN_ID = `chat-${key || "__corpus__"}`;
  process.env.REPAIR_AGENT_SESSION_ID = entry.manager.getSessionId?.() || "";
  process.env.REPAIR_AGENT_PROMPT_VERSION = PROMPT_VERSION;

  if (reset) {
    entry.seeded = false;
    sessions.set(key, entry);
  }

  current = { id, abort: () => session.abort() };

  // Subscribed once per ask and unsubscribed at the end. A subscription installed at session
  // creation would stream **every** later question's tokens into the first question's answer —
  // two conversations talking over each other, and only visible as duplicated text.
  const unsubscribe = session.subscribe((event) => {
    if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
      write({ id, event: "delta", text: event.assistantMessageEvent.delta });
    }
    if (event.type === "tool_execution_start") {
      write({ id, event: "tool", tool: event.toolName, args: event.args });
    }
    if (event.type === "tool_execution_end") {
      write({ id, event: "tool_result", tool: event.toolName, ok: !event.isError,
              summary: summarize(event.result?.details, event.result?.content) });
    }
    if (event.type === "message_end" && event.message.role === "assistant" && event.message.errorMessage) {
      write({ id, event: "error", error: event.message.errorMessage });
    }
  });

  let reply = "";
  try {
    if (!entry.seeded) {
      entry.seeded = true;
      await session.prompt(await seed(key));
    }
    // The designer's turn is recorded **before** the answer, so a crash mid-answer still leaves the
    // sentence he typed. Losing his input is worse than losing the reply to it.
    // `candidate_key: null` for the unbound conversation, so a reader of the file can tell a
    // corpus-level sentence from a question-bound one without inferring it from the text.
    remember({ candidate_key: key || null, role: "designer", text });
    write({ id, event: "turn", role: "designer", text });

    // Capture the assistant's own text from the event stream, not from a return value: `prompt()`
    // resolves with nothing, and the answer exists only as the deltas it already sent.
    // Token metering (owner 2026-10-03): every assistant message_end carries `usage`
    // {input, output, cacheRead, cacheWrite, totalTokens, reasoning} — accumulated across the
    // turn (one turn may be many model calls: tool rounds) and reported with `done`.
    const collect = session.subscribe((event) => {
      if (event.type === "message_end" && event.message.role === "assistant") {
        const content = event.message.content;
        reply = typeof content === "string"
          ? content
          : (Array.isArray(content) ? content.filter((p) => p?.type === "text").map((p) => p.text).join("") : "");
        const u = event.message.usage;
        if (u && typeof u === "object") {
          turnUsage.calls += 1;
          for (const k of ["input", "output", "cacheRead", "cacheWrite", "reasoning", "totalTokens"]) {
            if (typeof u[k] === "number") turnUsage[k] = (turnUsage[k] || 0) + u[k];
          }
        }
      }
    });
    try {
      await session.prompt(text);
    } finally {
      collect();
    }
    remember({ candidate_key: key || null, role: "agent", text: reply,
               ...(turnUsage.calls ? { usage: turnUsage } : {}) });
    write({ id, event: "turn", role: "agent", text: reply });
    write({ id, event: "done", seconds: (Date.now() - started) / 1000, usage: turnUsage.calls ? turnUsage : null });
  } catch (error) {
    write({ id, event: "error", error: `${error?.name || "Error"}: ${error?.message || error}` });
  } finally {
    unsubscribe();
    current = null;
  }
}

function turnsFor(key) {
  if (!existsSync(CHAT)) return [];
  const out = [];
  for (const line of readFileSync(CHAT, "utf8").split("\n")) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      // `null` is the unbound conversation's key, and `undefined` (a line written before this
      // field was always set) must not match it by accident.
      if ((row.candidate_key ?? null) === (key || null)) out.push(row);
    } catch { /* a half-written line must not blank the conversation */ }
  }
  return out;
}

/**
 * Read one JSON object per line from stdin.
 *
 * `readline` is not used: it splits on `\n`, and a question stem or a designer's sentence can carry
 * one. A length-prefixed framing would be sturdier still, but JSON-per-line is what the Python side
 * already speaks everywhere else in this project, and a newline inside a JSON string arrives escaped
 * (`\n`), never as a raw byte — so the framing is safe as long as the writer uses `JSON.stringify`,
 * which both ends do.
 */
let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", async (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      write({ id: null, event: "error", error: "不是 JSON" });
      continue;
    }
    if (message.op === "ping") {
      write({ id: message.id, event: "pong", brain: BRAIN, sessions: [...sessions.keys()] });
    } else if (message.op === "turns") {
      write({ id: message.id, event: "turns", turns: turnsFor(message.key) });
    } else if (message.op === "stop") {
      current?.abort();
      write({ id: message.id, event: "done", seconds: 0 });
    } else if (message.op === "ask") {
      inflight += 1;
      ask(message)
        .catch((error) => write({ id: message.id, event: "error", error: String(error) }))
        .finally(() => {
          inflight -= 1;
          if (closing && !inflight) process.exit(0);
        });
    } else {
      write({ id: message.id, event: "error", error: `unknown op: ${message.op}` });
    }
  }
});

process.stdin.on("end", () => {
  // stdin closing is how the parent says "no more requests". It must **not** kill a turn that is
  // already running: measured 2026-09-29, piping a file (`chat.mjs < in.jsonl`) closed stdin right
  // after the ask was read, `end` fired, and the process exited before the model answered — the
  // caller saw one `ready` line and nothing else, which is indistinguishable from a hang. The same
  // shape would kill a long answer if the browser tab closed mid-stream.
  //
  // `current` is the in-flight turn; when there is one, close when it finishes instead.
  if (!inflight) process.exit(0);
  else closing = true;
});

write({ id: null, event: "ready", brain: BRAIN, store: STORE_DIR });
