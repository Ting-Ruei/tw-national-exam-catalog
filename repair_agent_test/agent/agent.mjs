/**
 * The self-progressing repair agent — **Pi is the substrate** (designer, 2026-09-27).
 *
 *   node agent.mjs "看 moex:...:q068 這一題，確認它的文字抽取對不對"
 *   node agent.mjs --probe            # build the session, call nothing, print what it sees
 *   node agent.mjs --interactive       # keep the conversation open
 *
 * What is Pi's job here and what is not:
 *
 *   Pi        — planning, deciding which question to look at, calling tools, holding the
 *               conversation, persisting the session (so a later run can continue it).
 *   bridge.py — the four judgement-bearing actions: find / read / crop / record. Pi calls
 *               them as tools; the tools are the only place question content is touched.
 *   engines.py— the endpoint table. **The Pi provider is derived from it** (see `providerFor`),
 *               so the address the agent calls and the address the pipeline calls cannot drift.
 *               Two tables would be exactly the "two places that can disagree" the charter bans.
 */

import { SessionManager } from "@earendil-works/pi-coding-agent";
import { STORE_DIR, LOG_PATH, PROMPT_VERSION } from "./lib/identity.mjs";
import { PATHS } from "./lib/tools.mjs";
import { buildSession, BRAIN } from "./lib/session.mjs";
import { appendFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

// `fileURLToPath`, not `URL.pathname`: this checkout's path contains a space, and `pathname`
// leaves it as `%20`, which turns a valid interpreter into ENOENT.
const HERE = fileURLToPath(new URL(".", import.meta.url)).replace(/\/$/, "");

function log(record) {
  mkdirSync(STORE_DIR, { recursive: true });
  appendFileSync(LOG_PATH, JSON.stringify({ at: new Date().toISOString(), ...record }) + "\n", "utf8");
}

/**
 * A one-line summary of a tool result for the audit log.
 *
 * The full payload is not repeated here: `read_page` returns an entire question's reading, and a
 * log that holds five copies of it is a log nobody reads. What has to be auditable is *which*
 * question, *which* engine, and *whether* a diff was found — the reading itself lives in the
 * tool result the model saw and in the crop on disk.
 */
function summarize(details, content) {
  // An image read arrives in `content` as a part list. Name the parts before anything else, because
  // that is the only trace that the model actually received a picture.
  const parts = Array.isArray(content)
    ? content.map((part) => part?.type || typeof part)
    : undefined;
  const payload = details;
  if (!payload || typeof payload !== "object") {
    return parts ? { content_parts: parts } : undefined;
  }
  // `error` first and always: a failed tool whose log line says only `{"ok":false}` is a log that
  // costs a re-run to interpret. The reason is the one field that must survive into the record.
  if (payload.error) {
    return { error: String(payload.error).slice(0, 400) };
  }
  const keys = ["candidate_key", "question_number", "engine", "png_bytes", "complaint",
                "count", "boxes_supplied", "rows"].filter((k) => payload[k] !== undefined);
  const out = {};
  for (const key of keys) out[key] = payload[key];
  // A tool whose details do not contain any of the named keys must **not** log as `{}`.
  //
  // Measured 2026-09-28: `read` returned a picture (a real, working result) and the log line said
  // `-> {}` for all four calls. That `{}` was read as "the read failed", which produced a wrong
  // conclusion — 「read 讀不到圖」 — and a prompt sentence that told the agent not to try. The
  // picture had worked the whole time; the log had lost it.
  //
  // So: name what kind of payload it was, and how big. Never an empty object for a success.
  if (parts) out.content_parts = parts.slice(0, 8);
  if (!Object.keys(out).length) {
    if (typeof payload.text === "string") {
      out.text_chars = payload.text.length;
    } else {
      out.shape = Object.keys(payload).slice(0, 8);
    }
  }
  if (payload.diff) {
    out.stem_differs = Boolean(payload.diff.stem_differs);
    out.option_differs = Object.keys(payload.diff.option_differs || {});
  }
  if (payload.seen) out.seen_options = Object.keys(payload.seen.options || {});
  return out;
}

async function main() {
  const argv = process.argv.slice(2);
  const interactive = argv.includes("--interactive");
  const probe = argv.includes("--probe");
  const task = argv.filter((a) => !a.startsWith("--")).join(" ").trim();

  if (!task && !interactive && !probe) {
    console.error("用法：node agent.mjs \"<要做什麼>\" | --interactive | --probe");
    return 2;
  }

  // One builder, shared with the chat box (`lib/session.mjs`). This file must not build its own —
  // a second construction site is a second place a prompt fix can be applied to only one of.
  // The judgement stamp (workplan 2.1, 2026-09-30): `bridge.py` reads these at write time, so a
  // `record_judgement` row carries the run, the Pi session and the prompt version it was made in —
  // without them「同一題為什麼換了答案」cannot be asked of a specific run.
  process.env.REPAIR_AGENT_RUN_ID = `run-${new Date().toISOString()}-${process.pid}`;
  process.env.REPAIR_AGENT_PROMPT_VERSION = PROMPT_VERSION;
  const manager = SessionManager.create(HERE);
  let built;
  try {
    built = await buildSession({ sessionManager: manager });
  } catch (error) {
    console.error(error.message);
    return error.code === "MODEL_NOT_FOUND" ? 3 : 1;
  }
  process.env.REPAIR_AGENT_SESSION_ID = manager.getSessionId?.() || "";
  const { session, model, prompt } = built;

  log({ event: "session_start", brain: BRAIN, system_prompt: prompt, task,
        tools: session.getActiveToolNames() });

  if (probe) {
    // `session.systemPrompt` is what the model will actually be given. Printing `prompt.length`
    // alone was how this agent shipped for a day with `systemPrompt()` computed, logged, and never
    // passed to the session — every line said 7,504 chars while the model read Pi's coding-assistant
    // prompt. The two numbers are printed side by side so they cannot diverge silently again.
    const actual = session.systemPrompt || "";
    const wired = actual.includes("題目修理代理");
    console.log("brain          :", `${model.provider}/${model.id}`);
    console.log("thinking       :", session.thinkingLevel);
    console.log("tools          :", session.getActiveToolNames().join(", "));
    console.log("store          :", STORE_DIR);
    console.log("system prompt  :", prompt.length, "chars built →", actual.length, "chars in session",
                wired ? "(the agent's own)" : "⚠ NOT THE AGENT'S — the session has Pi's default");
    console.log("--- prompt ---");
    console.log(actual);
    session.dispose();
    return wired ? 0 : 4;
  }

  session.subscribe((event) => {
    if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
      process.stdout.write(event.assistantMessageEvent.delta);
    }
    if (event.type === "message_end" && event.message.role === "assistant" && event.message.errorMessage) {
      console.error("\n[error]", event.message.errorMessage);
      log({ event: "assistant_error", error: event.message.errorMessage });
    }
    // `tool_execution_start` is the *session* event. `tool_call` is the extension hook that can
    // block a call, and subscribing to that name here silently logged nothing while tools really
    // ran — which looked exactly like the agent inventing its answer. It did not; the log was wrong.
    if (event.type === "tool_execution_start") {
      log({ event: "tool_call", tool: event.toolName, args: event.args });
      console.error(`\n[tool] ${event.toolName}`);
    }
    if (event.type === "tool_execution_end") {
      // **Both** halves of the result, not just `details`.
      //
      // Measured 2026-09-28: built-in tools put their output in `result.content` (an array of parts,
      // where an image read is `[{type:'text'},{type:'image',data:'iVBOR...'}]`), while this project's
      // custom tools put theirs in `result.details`. Reading only `details` logged every successful
      // `read` as `{}` — which is how a working image read was mistaken for a failure.
      const content = event.result?.content;
      const details = event.result?.details;
      log({ event: "tool_result", tool: event.toolName, ok: !event.isError,
            summary: summarize(details, content) });
    }
    if (event.type === "agent_settled") {
      log({ event: "agent_settled" });
    }
  });

  try {
    if (task) await session.prompt(task);
    if (interactive) {
      const readline = await import("node:readline/promises");
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      try {
        for (;;) {
          // `rl.question` throws `ERR_USE_AFTER_CLOSE` when stdin ends (piped input, Ctrl-D) —
          // measured 2026-09-28: `printf "/quit" | node agent.mjs --interactive` crashed *after*
          // answering, so a scripted conversation could never be read to the end. `close` is the
          // normal end of input, not an error.
          let line;
          try {
            line = (await rl.question("\n> ")).trim();
          } catch (error) {
            if (error?.code === "ERR_USE_AFTER_CLOSE") break;
            throw error;
          }
          if (!line) continue;
          if (line === "/quit" || line === "/exit") break;
          await session.prompt(line);
          process.stdout.write("\n");
        }
      } finally {
        rl.close();
      }
    }
    console.log();
  } finally {
    // `agent_settled` would be the exact "Pi will not continue" signal; for a one-shot CLI the
    // finally block is enough, and `dispose()` aborts anything still streaming.
    log({ event: "session_end" });
    session.dispose();
  }
  return 0;
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error(error);
  process.exit(1);
});
