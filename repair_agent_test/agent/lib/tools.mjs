/**
 * The four tools Pi may call, each a thin wrapper over `bridge.py`.
 *
 * Why the agent has no free-form `bash`: every number in this project's records has to be
 * reproducible, and "the agent ran some shell" is not. A named tool means the audit trail
 * says which question was cropped, by which engine, with which reading — because the tool
 * wrote it that way. `bash` stays available for the human and for the agent to *look* at
 * the repository; the four judgement-bearing actions do not go through it.
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { remember, lessonsFromDiff, BRAIN_ENGINE } from "./identity.mjs";
import { recordRuleHit } from "./rules.mjs";

const run = promisify(execFile);

/**
 * Absolute path so the tool does not depend on the agent's cwd.
 *
 * This file is `agent/lib/tools.mjs`, so `..` from here is `agent/` — **two** levels up to
 * `repair_agent_test/`, three to the repository root where `qbr/` lives. Getting this wrong is not
 * cosmetic: the bridge runs, cannot find the PDF, and the symptom reads like "the question has no
 * page" rather than "the path is short one segment".
 */
const HERE = fileURLToPath(new URL(".", import.meta.url)).replace(/\/$/, "");
const AGENT_DIR = `${HERE}/..`;
const SANDBOX = `${HERE}/../..`;
const CATALOG = `${HERE}/../../..`;
const BRIDGE = `${AGENT_DIR}/bridge.py`;

/**
 * The interpreter is the qbr venv on purpose: PyMuPDF and the pipeline live there, and a
 * system python without them would fail inside a crop with a confusing error. `QBR_PYTHON`
 * is the override, documented rather than guessed.
 */
const PYTHON = process.env.QBR_PYTHON || `${CATALOG}/qbr/.venv/bin/python`;

/**
 * The paths `tools.mjs` runs the bridge with, exported so a test drives the same ones.
 *
 * A test that resolves `../../..` on its own gets the repository root wrong by one segment and
 * keeps the `%20` from `file:///...AI%20workspace/...`, which is not cosmetic: the failure reads
 * `ENOENT .../ai_learning_platform//qbr/.venv/bin/python` — a missing interpreter, not "the path
 * was short one level". Recomputing a path that already exists is the second place it can drift.
 */
export const PATHS = { HERE, AGENT_DIR, SANDBOX, CATALOG, BRIDGE, PYTHON };

/** Long enough for a vision call with thinking on; the bridge's own timeout is shorter. */
const DEFAULT_TIMEOUT_MS = 600_000;

async function callBridge(argv, { timeout = DEFAULT_TIMEOUT_MS } = {}) {
  try {
    const { stdout } = await run(PYTHON, [BRIDGE, ...argv], {
      timeout,
      maxBuffer: 16 * 1024 * 1024,
    });
    return JSON.parse(stdout);
  } catch (error) {
    // A bridge failure is **information the model needs**, not an exception for the harness.
    // `bridge.py` exits non-zero with a JSON `{"error": ...}` for the failures it anticipates
    // (bad key, missing PDF, no rows), and `execFile` turns that into a rejected promise whose
    // message is just `Command failed: ...`. Returning the real reason lets the agent read
    // "question PDF not found" and stop, instead of seeing an opaque failure and going off to
    // investigate the harness with `bash` — which is what it actually did before this change.
    const stderr = String(error.stderr || "").trim();
    const stdout = String(error.stdout || "").trim();
    let reason = stderr || stdout || error.message;
    try {
      const parsed = JSON.parse(stdout);
      if (parsed.error) reason = parsed.error;
    } catch {
      // stdout was not the JSON envelope; the raw text is still the best explanation we have.
    }
    return {
      error: reason.split("\n").slice(-6).join("\n").slice(0, 1500),
      bridge_argv: argv.join(" "),
      exit_code: error.code ?? null,
      timed_out: error.killed === true || error.signal === "SIGTERM",
    };
  }
}

/** Bridge failures are data, not exceptions: the model should see what went wrong and say so. */
function asToolResult(payload) {
  return { content: [{ type: "text", text: JSON.stringify(payload, null, 1) }], details: payload };
}

const keyParam = (Type) =>
  Type.Object({
    key: Type.String({
      description: "candidate_key of one question, e.g. moex:115090:308:0503:1:question:q068",
    }),
  });

const keyRequiredMessage =
  "需要 candidate_key。先用 list_questions 或 find_question 取得，不要自己拼 key。";

/** Build the tool list. `Type` is passed in so this file owns no second copy of typebox. */
export function toolsFor(Type) {
  return [
    {
      name: "see_corpus",
      label: "看全庫（不是一題）",
      description:
        "看**整個題庫**的樣子：每個類科有幾題、幾題有圖、幾題被機器標記、幾題被設計者標記為有問題、" +
        "你已經判過幾題。**回答『我做到哪了』／『有沒有全局』／『先看哪一科』之前先呼叫這個。**" +
        "不要因為不知道某一題的 key 就回答「我需要更多資訊」——先看全庫，再挑。",
      parameters: Type.Object({}),
      execute: async () => asToolResult(await callBridge(["overview"], { timeout: 300_000 })),
    },

    {
      name: "find_disputed",
      label: "找設計者說有問題的題",
      description:
        "列出**設計者曾經標記為有問題（block／comment）**的題目，含他寫的字（在 notes 欄）。" +
        "這是他真正的需求：「我想針對 block 的題目跟你進行對話」。**要挑一題深入看時，從這裡挑。**" +
        "回傳的題目都有 key，可以直接接 get_question。",
      parameters: Type.Object({
        limit: Type.Optional(Type.Integer({ description: "最多幾筆，預設 50" })),
      }),
      execute: async (_id, params) =>
        asToolResult(await callBridge(["disputes", "--limit", String(params.limit ?? 50)],
                                      { timeout: 300_000 })),
    },

    {
      name: "find_question",
      label: "找題目",
      description:
        "依科目／題號／關鍵字找 candidate_key。回傳符合的題目清單（最多 20 筆），" +
        "每筆含 candidate_key、題號、科目、題幹前 80 字。找題目的第一步一定是這個。",
      parameters: Type.Object({
        subject: Type.Optional(Type.String({ description: "科目關鍵字，例如 微生物" })),
        number: Type.Optional(Type.Integer({ description: "題號" })),
        contains: Type.Optional(Type.String({ description: "題幹裡要包含的字" })),
        limit: Type.Optional(Type.Integer({ description: "最多幾筆，預設 20" })),
      }),
      execute: async (_id, params) => {
        const argv = ["find", "--limit", String(params.limit ?? 20)];
        if (params.subject) argv.push("--subject", params.subject);
        if (params.number !== undefined) argv.push("--number", String(params.number));
        if (params.contains) argv.push("--contains", params.contains);
        return asToolResult(await callBridge(argv, { timeout: 120_000 }));
      },
    },

    {
      name: "get_question",
      label: "讀題目（完整）",
      description:
        "讀某一題的**完整**內容：題幹、A B C D、答案、這一題的圖（含框與裁切狀態）、" +
        "以及人對這一題做過什麼。一次一題，不是只讀被動過的欄位。" +
        keyRequiredMessage,
      parameters: keyParam(Type),
      execute: async (_id, params) => asToolResult(await callBridge(["question", "--key", params.key])),
    },

    {
      name: "crop_question",
      label: "裁這一題的紙本",
      description:
        "把這一題在官方 PDF 上的區塊裁成一張圖（含這一題自己的圖框，跨頁會縫成一張）。" +
        "回傳存檔路徑與檔名。**看紙本之前先裁圖**：文字抽取有錯時，紙本才是答案。" +
        keyRequiredMessage,
      parameters: Type.Object({
        key: Type.String({ description: keyRequiredMessage }),
        out: Type.Optional(Type.String({ description: "存檔路徑；預設放 agent/store/crops/" })),
        dpi: Type.Optional(Type.Integer({ description: "解析度，預設 150" })),
      }),
      execute: async (_id, params) => {
        const argv = ["crop", "--key", params.key];
        if (params.out) argv.push("--out", params.out);
        if (params.dpi) argv.push("--dpi", String(params.dpi));
        return asToolResult(await callBridge(argv));
      },
    },

    {
      name: "read_page",
      label: "讓地端模型看紙本",
      description:
        "裁這一題的紙本，**把圖送給地端視覺引擎**，回傳它逐字讀到的內容，" +
        "並列出「抽取值 vs 紙本」的差異（diff）。" +
        "**不給 engine ＝ 用你自己那一顆（" + BRAIN_ENGINE + "），回傳的 independent 會是 false：" +
        "那是「再看一次」，不是第二個意見**——同一顆模型重讀同一張圖幾乎一樣（實測逐欄位翻轉 0/278）。" +
        "要**獨立證據**就指定另一顆（mtplx-35b）：兩顆逐欄位只重疊 37.8%，至少一個對 63.3%、" +
        "單一最好 53.3%，所以重要的題目才值得付兩倍成本。" +
        keyRequiredMessage,
      parameters: Type.Object({
        key: Type.String({ description: keyRequiredMessage }),
        engine: Type.Optional(
          Type.String({
            description: "不給＝你自己那一顆（" + BRAIN_ENGINE + "，independent:false）；"
              + "mtplx-35b＝獨立第二意見（independent:true）",
            enum: ["occamy-6bit", "mtplx-35b", "dgx-flash"],
          }),
        ),
        no_image: Type.Optional(
          Type.Boolean({
            description:
              "負對照：同一個提示詞但不送圖。用來證明分數來自圖片而不是提示詞。正常判讀時不要用。",
          }),
        ),
      }),
      execute: async (_id, params) => {
        const engine = params.engine || BRAIN_ENGINE;
        const argv = ["read", "--key", params.key, "--engine", engine];
        if (params.no_image) argv.push("--no-image");
        const result = await callBridge(argv);
        // **Whether a reading is independent is a fact about the model, not about the call.** After
        // the 2026-09-29 ruling the brain and the default reader are the same engine, so "two
        // engines agreed" would be a false claim for the default call. The label is computed from
        // the engine the bridge actually used (`result.engine`, falling back to what was asked for)
        // against the brain, so a future engine change cannot leave it saying the old thing.
        if (!result.error) {
          const used = result.engine || engine;
          result.independent = used !== BRAIN_ENGINE;
          result.independence_note = result.independent
            ? "獨立第二意見：與你自己的引擎不同（" + used + " vs " + BRAIN_ENGINE + "）"
            : "同一顆引擎（" + used + "）＝再看一次，不是第二個意見；要獨立證據請指定另一顆";
        }
        // **The measured lesson is filed without asking the model.** Every character the vision
        // engine misread becomes a lesson for the next run, because this is the one place where
        // "the model looked at the paper" is a fact rather than a claim. The old agent's
        // `learned=None` was exactly this omission: the machinery existed and nothing filled it.
        // `no_image` is skipped because a withheld picture has no reading to learn from.
        if (!result.error && !params.no_image && result.diff) {
          result.lessons_recorded = lessonsFromDiff({
            subject: result.subject || "",
            engine,

            key: params.key,
            diff: result.diff,
          }).map((row) => row.text);
        }
        return asToolResult(result);
      },
    },

    {
      name: "record_judgement",
      label: "記下判讀（學習語料）",
      description:
        "把對這一題的判讀寫進學習語料（append-only）。**只有在你已經看過紙本／讀過判讀之後才呼叫。**" +
        "rating=up 表示「抽取值與紙本一致、這題沒問題」；down 表示「有問題」。" +
        "reason 要寫**你依據什麼**（哪個字、哪張圖），不要只寫「有問題」。" +
        "寫入的是實驗自己的檔，不會動到人工審核紀錄。",
      parameters: Type.Object({
        key: Type.String({ description: keyRequiredMessage }),
        rating: Type.String({ description: "up＝沒問題；down＝有問題", enum: ["up", "down"] }),
        reason: Type.String({ description: "判讀依據（哪個字／哪張圖／哪個欄位）" }),
        engine: Type.Optional(Type.String({ description: "做出這個判讀的引擎" })),
      }),
      execute: async (_id, params) => {
        const argv = [
          "feedback",
          "--key", params.key,
          "--rating", params.rating,
          "--reason", params.reason,
        ];
        if (params.engine) argv.push("--engine", params.engine);
        const result = await callBridge(argv, { timeout: 120_000 });
        return asToolResult(result);
      },
    },

    {
      name: "propose_repair",
      label: "提出修法（草案，不動正式檔）",
      description:
        "把你建議的**修法**寫成草案（append-only；可多版，寫入不等於核准）。" +
        "fix＝**改完後的完整文字**（整句題幹／整個選項，從頭到尾）——不是建議、不是步驟、不是只寫差異；" +
        "UI 會拿 fix **整句替換**該欄位，寫「建議在X後補Y」會把原題蓋掉。「補一個詞」也要寫補完後的整句。" +
        "insert＝落到哪一格／哪一選項；basis＝**你依據什麼**（紙本哪個字、哪張圖）；" +
        "crop＝你實際看過的那張裁片路徑（會記其 SHA-256 當憑證）。" +
        "它不修題目、不動人工審核紀錄——真正核准在沙盒 UI，由設計者按。",
      parameters: Type.Object({
        key: Type.String({ description: keyRequiredMessage }),
        fix: Type.String({ description: "改完後的**完整**文字（整句題幹／整個選項，從頭到尾）——不是建議、不是片段" }),
        insert: Type.String({ description: "插入點：哪一格／哪一欄／哪一選項" }),
        basis: Type.String({ description: "依據：紙本哪個字、哪張圖（你親眼看過的）" }),
        crop: Type.Optional(Type.String({ description: "裁片憑證：你看過的裁片路徑" })),
      }),
      execute: async (_id, params) => {
        const argv = [
          "propose",
          "--key", params.key,
          "--fix", params.fix,
          "--insert", params.insert,
          "--basis", params.basis,
        ];
        if (params.crop) argv.push("--crop", params.crop);
        const result = await callBridge(argv, { timeout: 120_000 });
        return asToolResult(result);
      },
    },

    {
      name: "remember_lesson",
      label: "記下一條教訓",
      description:
        "把一則**會再發生**的觀察記下，讓下一輪的自己知道——**先問自己：這句話對其他題也成立嗎？**" +
        "成立才寫（例：「這個科目常把 X 讀成 Y」、「這類題的圖常被切成細縫」）。" +
        "不成立就不要呼叫這個工具：「本題…」「第 68 題…」這種單題細節屬於 record_judgement 的 reason。" +
        "相同的觀察會被累計次數，不會重複列出。",
      parameters: Type.Object({
        subject: Type.String({ description: "科目（例如 微生物學與臨床微生物學）" }),
        text: Type.String({ description: "觀察本身，一句話" }),
        kind: Type.Optional(
          Type.String({
            description: "種類：character-substitution／crop／layout／answer／other",
            enum: ["character-substitution", "crop", "layout", "answer", "other"],
          }),
        ),
        evidence: Type.Optional(Type.String({ description: "依據：哪一題、哪個字、哪張圖" })),
        key: Type.Optional(Type.String({ description: "來自哪一題" })),
      }),
      execute: async (_id, params) =>
        asToolResult({
          saved: remember({
            subject: params.subject,
            kind: params.kind || "other",
            text: params.text,
            evidence: params.evidence || "",
            key: params.key || "",
          }),
        }),
    },

    {
      name: "record_rule_hit",
      label: "記下規則命中",
      description:
        "設計者的基本原則若在這一題**真的被你用上**——依它察覺錯誤、依它決定要看什麼或怎麼修——記一筆命中" +
        "（規則編號是原則清單方括號裡的 `pN`）。**命中率是規則淘汰的證據**：亂記會讓好規則被誤殺、" +
        "漏記會讓有用的規則看起來沒用，所以「用上才記」。同一題同一條重複記不會加分。",
      parameters: Type.Object({
        rule: Type.String({ description: "規則編號，例如 p3（原則清單方括號裡的那個）" }),
        key: Type.String({ description: keyRequiredMessage }),
        why: Type.Optional(
          Type.String({ description: "它這次幫你看到或決定了什麼，一句話" }),
        ),
      }),
      execute: async (_id, params) => {
        try {
          return asToolResult(recordRuleHit(params.rule, { key: params.key, why: params.why || "" }));
        } catch (error) {
          return asToolResult({ error: String(error.message).slice(0, 600) });
        }
      },
    },
  ];
}
