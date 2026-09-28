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

import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, getAgentDir } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { systemPrompt, remember, STORE_DIR, LOG_PATH } from "./lib/identity.mjs";
import { toolsFor, PATHS } from "./lib/tools.mjs";
import { appendFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);
// `fileURLToPath`, not `URL.pathname`: this checkout's path contains a space, and `pathname`
// leaves it as `%20`, which turns a valid interpreter into ENOENT.
const HERE = fileURLToPath(new URL(".", import.meta.url)).replace(/\/$/, "");

/**
 * The brain. A separate decision from the eyes: `read_page` is always a local vision engine,
 * while this is whichever model does the planning. Default is the local 35B MoE so the agent is
 * self-contained; `REPAIR_AGENT_MODEL=llm-share/deepseek-v4.1-flash` switches it to the cloud
 * model the designer approved for this stage (D3: exam questions are public data).
 */
const BRAIN = process.env.REPAIR_AGENT_MODEL || "ornith-mtplx/ornith-1.5-mtplx-35b";

/**
 * Build the Pi provider record from `engines.py`, not from a hand-written copy.
 *
 * This is the one place the "two tables" risk lives, so it is a call into the pipeline's own
 * module. If someone repoints `QBR_ENGINE_MTPLX_35B_URL`, the agent follows on the next run
 * without an edit here — which is the whole reason the endpoints were made runtime-settable
 * (branch `agent/engine-endpoints-runtime-20260927`).
 */
const PYTHON_CMD = process.env.QBR_PYTHON || `${HERE}/../../qbr/.venv/bin/python`;

async function localProviders() {
  const script = `
import json, sys
sys.path.insert(0, r"${HERE}/../../qbr/src")
from qbr import engines
out = {}
for name, record in engines.endpoints().items():
    out[name] = {"url": record["url"], "model": record["name"], "key": record.get("key", ""),
                 "reasoning": bool(record.get("reasoning") or record.get("thinking"))}
print(json.dumps(out))
`;
  const { stdout } = await run(PYTHON_CMD, ["-c", script], { timeout: 60_000 });
  return JSON.parse(stdout);
}

function providerConfig(name, record) {
  return {
    name,
    baseUrl: record.url.replace(/\/$/, "") + "/v1",
    api: "openai-completions",
    apiKey: record.key || "local-no-auth",
    authHeader: Boolean(record.key),
    compat: {
      supportsStore: false,
      maxTokensField: "max_tokens",
      // The thinking spelling is engine-specific and a wrong one is silently ignored, so it is
      // declared here rather than left to Pi's default. Same decision as `engines.body_for`.
      ...(record.reasoning ? {} : { thinkingFormat: "qwen-chat-template" }),
    },
    models: [
      {
        id: record.model,
        name: `${record.model} (local)`,
        reasoning: record.reasoning,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 262144,
        maxTokens: 32768,
      },
    ],
  };
}

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
function summarize(payload) {
  if (!payload || typeof payload !== "object") return undefined;
  // `error` first and always: a failed tool whose log line says only `{"ok":false}` is a log that
  // costs a re-run to interpret. The reason is the one field that must survive into the record.
  if (payload.error) {
    return { error: String(payload.error).slice(0, 400) };
  }
  const keys = ["candidate_key", "question_number", "engine", "png_bytes", "complaint",
                "count", "boxes_supplied", "rows"].filter((k) => payload[k] !== undefined);
  const out = {};
  for (const key of keys) out[key] = payload[key];
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

  const modelRuntime = await ModelRuntime.create();
  for (const [name, record] of Object.entries(await localProviders())) {
    modelRuntime.registerProvider(name, providerConfig(name, record));
  }

  const [provider, id] = BRAIN.split("/");
  const model = modelRuntime.getModel(provider, id);
  if (!model) {
    // Not a fallback. Using a different model silently would make every number from this run
    // unattributable to a model — and the project's whole point is that its numbers are auditable.
    const available = (await modelRuntime.getAvailable()).map((m) => `${m.provider}/${m.id}`);
    console.error(`找不到模型 ${BRAIN}。可用：\n  ${available.join("\n  ") || "(無)"}`);
    return 3;
  }

  // The system prompt is built once and also recorded, so the record and the send agree.
  const prompt = systemPrompt();

  const resourceLoader = new DefaultResourceLoader({
    // The repository root, not `agent/`. `read`, `grep` and `find` resolve their paths against
    // this cwd, and every path this agent is handed is repository-root-relative
    // (`qbr/data/review-queues/...`). Running from `agent/` made those reads fail — measured
    // 2026-09-28: four `read` calls on the option crops returned errors, the model concluded the
    // files were missing, and it ran four `find` and five `bash` calls (including `find /`) to
    // locate pictures that were exactly where the tool had told it they were. The run still
    // answered correctly, which is why the wrong cwd survived: the damage showed up only as a
    // long tool trace, never as a wrong conclusion.
    cwd: PATHS.CATALOG,
    // `agentDir` is required by the loader (passing undefined crashes inside its own path
    // resolution). `getAgentDir()` is Pi's own answer, so the agent reads the same auth/models
    // store the CLI does instead of a second convention invented here.
    agentDir: process.env.PI_AGENT_DIR || getAgentDir(),
    // Discovery of this repo's own skills/extensions is off: a repair run must be reproducible,
    // and "which extensions happened to be installed" is not part of the experiment.
    noExtensions: true,
    noSkills: true,
  });
  await resourceLoader.reload();

  // Names must be listed here: `tools` is an **allowlist** ("only the listed tool names are
  // enabled"), and omitting the custom names silently leaves them inactive — the agent then
  // reaches for `bash` and calls bridge.py by hand, which works but loses the named audit trail.
  const customTools = toolsFor(Type);
  const builtin = ["read", "grep", "find", "ls", "bash"];

  const { session } = await createAgentSession({
    cwd: PATHS.CATALOG,
    modelRuntime,
    model,
    thinkingLevel: process.env.REPAIR_AGENT_THINKING || "medium",
    resourceLoader,
    // No edit/write: this agent produces findings, not edits — applying a fix is a different,
    // more constrained step.
    tools: [...builtin, ...customTools.map((tool) => tool.name)],
    customTools,
    // Persistent by construction: the designer asked for an agent that gets smarter, and a
    // memory that disappears with the process cannot be smarter than its last run.
    sessionManager: SessionManager.create(HERE),
    sessionStartEvent: { reason: "startup" },
  });

  log({ event: "session_start", brain: BRAIN, system_prompt: prompt, task,
        tools: session.getActiveToolNames() });

  if (probe) {
    console.log("brain          :", `${model.provider}/${model.id}`);
    console.log("thinking       :", session.thinkingLevel);
    console.log("tools          :", session.getActiveToolNames().join(", "));
    console.log("store          :", STORE_DIR);
    console.log("system prompt  :", prompt.length, "chars");
    console.log("--- prompt ---");
    console.log(prompt);
    session.dispose();
    return 0;
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
      const payload = event.result?.details;
      log({ event: "tool_result", tool: event.toolName, ok: !event.isError,
            summary: summarize(payload) });
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
      for (;;) {
        const line = (await rl.question("\n> ")).trim();
        if (!line) continue;
        if (line === "/quit" || line === "/exit") break;
        await session.prompt(line);
        process.stdout.write("\n");
      }
      rl.close();
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
