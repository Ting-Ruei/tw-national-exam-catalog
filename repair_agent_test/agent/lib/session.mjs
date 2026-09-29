/**
 * One builder for the Pi session, so the CLI, the probe and the chat box cannot drift.
 *
 * Why this is a module and not a function inside `agent.mjs`: the chat box needs the **same**
 * session the one-shot run gets — same brain, same tools, same prompt, same cwd. Two builders would
 * be two places a prompt fix could be applied to only one of, which is exactly how this project
 * shipped for a day with `systemPrompt()` computed and never passed (see `agent.mjs::--probe`).
 * The designer's own rule applies to code too: 兩個做同一件事的東西，就是兩個可以不一致的地方.
 */

import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, getAgentDir } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { systemPrompt, STORE_DIR } from "./identity.mjs";
import { toolsFor, PATHS } from "./tools.mjs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

/**
 * The brain. A separate decision from the eyes: `read_page` is always a local vision engine, while
 * this is whichever model does the planning. Default is the local 35B MoE so the agent is
 * self-contained; `REPAIR_AGENT_MODEL` switches it.
 */
export const BRAIN = process.env.REPAIR_AGENT_MODEL || "ornith-mtplx/ornith-1.5-mtplx-35b";

const PYTHON_CMD = process.env.QBR_PYTHON || `${PATHS.CATALOG}/qbr/.venv/bin/python`;

/**
 * Build the Pi provider records from `engines.py`, not from a hand-written copy.
 *
 * This is the one place the "two tables" risk lives, so it is a call into the pipeline's own module.
 * Repointing `QBR_ENGINE_MTPLX_35B_URL` must move the agent on the next run with no edit here.
 */
async function localProviders() {
  const script = `
import json, sys
sys.path.insert(0, r"${PATHS.CATALOG}/qbr/src")
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

/** The tools that are enabled. Built-in names first, then this project's own. */
export const BUILTIN_TOOLS = ["read", "grep", "find", "ls", "bash"];

/**
 * Build (or restore) the session.
 *
 * `sessionManager` is passed in rather than created here: the CLI wants a fresh persistent session
 * per process, while the chat box wants **one session that outlives many HTTP requests** — that is
 * the whole point of a conversation. Choosing it at the call site keeps this function from deciding
 * a lifetime it cannot know.
 *
 * Throws rather than falling back to another model. Using a different brain silently would make
 * every number from a run unattributable to a model, and auditable numbers are the project's point.
 */
export async function buildSession({ sessionManager, thinking, cwd } = {}) {
  const modelRuntime = await ModelRuntime.create();
  for (const [name, record] of Object.entries(await localProviders())) {
    modelRuntime.registerProvider(name, providerConfig(name, record));
  }

  const [provider, id] = BRAIN.split("/");
  const model = modelRuntime.getModel(provider, id);
  if (!model) {
    const available = (await modelRuntime.getAvailable()).map((m) => `${m.provider}/${m.id}`);
    const error = new Error(`找不到模型 ${BRAIN}。可用：\n  ${available.join("\n  ") || "(無)"}`);
    error.code = "MODEL_NOT_FOUND";
    throw error;
  }

  // Built once and passed explicitly. **`DefaultResourceLoader({systemPrompt})` is the only field
  // that reaches the model** — `CreateAgentSessionOptions` has no `systemPrompt`. Measured
  // 2026-09-28: computed here, logged here, and the session still held Pi's 30,452-char
  // "expert coding assistant" prompt, so all five governance rules were never in front of the
  // model. `agent.mjs --probe` exists to prove the two numbers agree.
  const prompt = systemPrompt();

  const resourceLoader = new DefaultResourceLoader({
    systemPrompt: prompt,
    // The repository root, not `agent/`: `read`/`grep`/`find` resolve against this cwd, and every
    // path handed to this agent is repository-root-relative.
    cwd: cwd || PATHS.CATALOG,
    agentDir: process.env.PI_AGENT_DIR || getAgentDir(),
    noExtensions: true,
    noSkills: true,
  });
  await resourceLoader.reload();

  const customTools = toolsFor(Type);
  const { session } = await createAgentSession({
    cwd: cwd || PATHS.CATALOG,
    modelRuntime,
    model,
    thinkingLevel: thinking || process.env.REPAIR_AGENT_THINKING || "medium",
    resourceLoader,
    // No edit/write: this agent produces findings, not edits.
    tools: [...BUILTIN_TOOLS, ...customTools.map((tool) => tool.name)],
    customTools,
    sessionManager: sessionManager || SessionManager.create(PATHS.AGENT_DIR),
    sessionStartEvent: { reason: "startup" },
  });

  return { session, model, prompt, store: STORE_DIR };
}
