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
 * this is whichever model does the planning. It is the **same engine** as the eyes since the
 * designer's ruling of 2026-09-29（「1 換 occamy（腦與眼同一顆）」）, because 6-bit occamy read the
 * paper better than 4-bit ornith on the measured sample (field-exact 53.3% vs 47.8%, ceiling 78.4%
 * vs 64.9% — `a2.1-vision-ceiling.md`).
 *
 * The provider name is `BRAIN_ENGINE` from `identity.mjs` and the model id is the one `engines.py`
 * records for that engine; `test_agent.mjs` compares the two, so this line cannot drift into
 * naming a model the pipeline does not serve. `REPAIR_AGENT_MODEL` switches it wholesale.
 */
export const BRAIN = process.env.REPAIR_AGENT_MODEL || "occamy-6bit/occamy-1.0-6bit-xl-mlx";

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
                 "reasoning": bool(record.get("reasoning") or record.get("thinking")),
                 # The engine's own thinking switch (chat_template_kwargs on DGX): Pi's
                 # chat-template thinking format sends exactly this dict. Without it the DGX GLM
                 # answers with content null and everything in reasoning_content — measured
                 # 2026-10-03 — so it must travel with the record, not be re-derived per call.
                 "thinking": record.get("thinking") or {},
                 "context_window": record.get("context_window"),
                 "max_output_tokens": record.get("max_output_tokens")}
print(json.dumps(out))
`;
  const { stdout } = await run(PYTHON_CMD, ["-c", script], { timeout: 60_000 });
  return JSON.parse(stdout);
}

function providerConfig(name, record) {
  // The compat block describes the **model**, not the provider: Pi reads it as `model.compat`
  // (`getCompat(model)` in the SDK), and a provider-level copy is not merged into the model record.
  // Measured 2026-09-29: declared only on the provider, `supportsDeveloperRole: false` had no
  // effect and every run still sent `role: "developer"` → 500 from the local vision server.
  const compat = {
    supportsStore: false,
    maxTokensField: "max_tokens",
    // **Pi defaults to the OpenAI `developer` role for the system prompt when the model is
    // declared as reasoning, and the local vision server rejects it.** Measured 2026-09-29: with
    // occamy as the brain, every agent run died with `500 status code (no body)`; the server log
    // was `Unexpected message role` from the Jinja chat template
    // (`transformers/utils/chat_template_utils.py:479`). A hand-sent request reproduces it:
    // `role: "developer"` → 500 from the occamy engine, 200 from the MTPLX one (MTPLX accepts it,
    // mlx_vlm does not); the ports are in `engines.py` and are deliberately not repeated here.
    // The role is a per-server fact, not a preference, so it is pinned here rather than left to
    // Pi's default: `instructionRole = reasoning && supportsDeveloperRole ? "developer" : "system"`.
    supportsDeveloperRole: false,
  };
  // The thinking spelling is engine-specific and a wrong one is silently ignored, so it is
  // declared here rather than left to Pi's default. Same decision as `engines.body_for`.
  // An engine that ships its own switch dict (DGX: `chat_template_kwargs.enable_thinking=False`)
  // maps to Pi's `chat-template` format + `chatTemplateKwargs` — Pi sends the dict verbatim.
  // Without this the DGX GLM runs thinking-on and answers `content: null` (measured 2026-10-03).
  if (record.thinking && typeof record.thinking === "object"
      && Object.keys(record.thinking).length) {
    compat.thinkingFormat = "chat-template";
    compat.chatTemplateKwargs = record.thinking.chat_template_kwargs || {};
  } else if (!record.reasoning) {
    compat.thinkingFormat = "qwen-chat-template";
  }
  return {
    name,
    baseUrl: record.url.replace(/\/$/, "") + "/v1",
    api: "openai-completions",
    apiKey: record.key || "local-no-auth",
    authHeader: Boolean(record.key),
    compat,
    models: [
      {
        id: record.model,
        name: `${record.model} (local)`,
        reasoning: record.reasoning,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        // From `engines.py` when the deployment declares them; otherwise the previous constants.
        // Asking a server for more than it can hold is a 400 with no body, so the numbers travel
        // with the engine instead of being assumed here.
        contextWindow: record.context_window || 262144,
        maxTokens: record.max_output_tokens || 32768,
        compat,
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
export async function buildSession({ sessionManager, thinking, cwd, omit = [] } = {}) {
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
    // 2026-10-03 latency work: Pi's context-file loader walks the AGENTS.md chain from `cwd` and
    // appends **both** the umbrella (`ai_learning_platform/AGENTS.md`, 6.3k chars) and the catalog
    // repo's (`tw-national-exam-catalog/AGENTS.md`, 11.9k chars) — 18.3k chars ≈ 7k input tokens on
    // **every** conductor call, on top of the ROLE. The sandbox session's contract is its own ROLE
    // (`identity.mjs`): governance rules it needs live there; the repo AGENTS.md chain is this
    // developer session's context, not the reviewer-model's. Baron-cwd control measured: 7.4k
    // total without it vs 25.6k with it.
    noContextFiles: true,
  });
  await resourceLoader.reload();

  // `omit` is the conductor/worker boundary (owner 2026-10-03): the chat conductor must not carry
  // the worker's pen. `read_page` drives a transcription engine — that is 做事模型 work, done in a
  // one-shot run, not inside a conversation. The one-shot agent passes no `omit` and keeps it.
  const customTools = toolsFor(Type).filter((tool) => !omit.includes(tool.name));
  const builtinTools = BUILTIN_TOOLS.filter((name) => !omit.includes(name));
  const { session } = await createAgentSession({
    cwd: cwd || PATHS.CATALOG,
    modelRuntime,
    model,
    thinkingLevel: thinking || process.env.REPAIR_AGENT_THINKING || "medium",
    resourceLoader,
    // No edit/write: this agent produces findings, not edits.
    tools: [...builtinTools, ...customTools.map((tool) => tool.name)],
    customTools,
    sessionManager: sessionManager || SessionManager.create(PATHS.AGENT_DIR),
    sessionStartEvent: { reason: "startup" },
  });

  return { session, model, prompt, store: STORE_DIR };
}
