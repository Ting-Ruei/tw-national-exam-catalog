---
name: operate-local-open-models
description: Discover and use the local open-model endpoints for this workspace — MTPLX, mlx-vlm, and the DGX vLLM service — including how to turn reasoning on and off on each engine, how to budget tokens so reasoning is not truncated, and how to measure throughput honestly. Use when calling or comparing local models, when a model's output looks empty or truncated, when a reasoning switch appears to do nothing, or when reporting tokens/s.
---

# Operate Local Open Models

Three engines, three different spellings for the same switch, and two of the wrong spellings return
**HTTP 200 with no effect**. Everything below was measured; nothing here is assumed.

## Endpoints

| Engine | Base URL | Model | Vision | ctx | Auth |
|---|---|---|---|---|---|
| MTPLX 35b | `http://127.0.0.1:18120/v1` | `ornith-1.5-mtplx-35b` | yes | 262,144 | `Bearer mtplx` |
| MTPLX 9b | `http://127.0.0.1:18121/v1` | `ornith-1.5-mtplx-9b` | no | 262,144 | `Bearer mtplx` |
| mlx-vlm (this Mac) | `http://127.0.0.1:8082/v1` | `Youssofal/Qwen3.8-27B-MTPLX-Optimized-Speed` | yes | 262,144 | none |
| DGX vLLM | `http://192.168.10.90:8888/v1` | `qwen3.8-flash-next` | yes | 524,288 | `Bearer mtplx` |

All OpenAI-compatible. The scripts take `QBR_MODEL_BASE_URL`, `QBR_MODEL_NAME`,
`QBR_MODEL_API_KEY`.

**Contention:** run one job at a time against MTPLX — two concurrent jobs return HTTP 500. The DGX
tolerated 16-way concurrency with 0 errors. The mlx-vlm and DGX machines are different, so they can
run in parallel.

## 1. The reasoning switch is engine-specific — measure it, never hard-code it

| Engine | Turns reasoning OFF | Silently ignored |
|---|---|---|
| MTPLX | `chat_template_kwargs: {enable_thinking: false}` | top-level `enable_thinking` |
| DGX vLLM | `chat_template_kwargs` **or** top-level `reasoning_effort: "none"` | — |
| **mlx-vlm 8082** | **top-level `enable_thinking: false`** | **`chat_template_kwargs`** |

- `reasoning_effort: "none"` is **7× faster** than `chat_template_kwargs` on the DGX (1.1 s vs
  7.4 s).
- `reasoning_effort: "high"` on the DGX is an **HTTP 400**.
- On mlx-vlm, `chat_template_kwargs.enable_thinking=true|false` returns **HTTP 200, byte-identical
  output** to sending nothing. It never reaches the template. **A published delivery note for this
  server claims this is the only working switch; it is wrong.**

**A spelling can only be shown to work by removing reasoning that was demonstrably there.** Probe
with a question that *does* provoke reasoning (`9.11 vs 9.9`), measure `reasoning_content` in both
directions, and pick what actually changes. `qbr/vision.py::_thinking_forms()` does this.

**Two traps this caught, both in measuring tools:**
- Probing with a question that provokes nothing makes every spelling look like it works.
- One engine does not report `reasoning_tokens` at all, so `if not reasoning` is true for
  everything and the first spelling gets credit for free.

## 2. Reasoning is a capability, and the budget decides whether you see it

Measured on `9.11 vs 9.9`: reasoning off answers **wrong**; reasoning on answers **correct**.

**But a small `max_tokens` makes reasoning look harmful.** At `max_tokens=1500`, thinking-on scored
*below* thinking-off — because 8 of the answers came back **empty**, not wrong. At 6000 the same
questions scored **above** thinking-off.

| Budget | flash-next on | 27B on |
|---|---|---|
| 1500 | 25/34 (looked worse) | 28/34 (looked worse) |
| **6000** | **27/34** | **30/34 (better)** |

Average reasoning is 2,146 chars (flash-next) and 2,569 chars (27B), and both still hit 6000 on some
questions.

**An empty answer is evidence about the budget, not about the model.** Always check for empty
before scoring, and always report the budget beside the score.

| Task | Budget |
|---|---|
| Answer a letter | 64–256 |
| Defect checklist (JSON) | 1,500 |
| **Anything with thinking on** | **≥ 8,000** |

## 3. Measuring throughput honestly

- **Use unique, non-repeating filler.** Repeated filler is served from the prefix cache and produces
  a fake rising curve (1,428 → 10,418 tok/s). `measure_speed._unique_filler()` builds filler from
  random words.
- **Use streaming TTFT for prefill.** Wall time on a small prompt is fixed-overhead-bound. Measured
  honestly, flash-next prefill is **~2,050 tok/s and flat from 2,550 to 161,356 tokens** (161K in
  79 s).
- **Decode is bandwidth-bound and cannot be tuned away.** 18 GB of weights ÷ ~410 GB/s ≈ 23 tok/s;
  measured 24.9.

Measured: flash-next decode 19.7 tok/s, 16-way 48 tok/s, 0 errors. mlx-vlm 27B decode 24.9 tok/s,
24-way 99.3 tok/s (32-way falls to 93).

## 4. Choosing an engine by task

Reading ability is the same (both 29/34 on 36 real questions); the differences are elsewhere.

| Task | Use | Why |
|---|---|---|
| Bulk reading | flash-next, thinking off | 85%, 0.44 s/question, off this Mac |
| Text defect detection | **27B** | 30/35 vs 24/35; missing-word 5/5 vs 1/5 |
| Long documents (>50K) | flash-next | 2,050 tok/s flat, 161K in 79 s |
| High concurrency | 27B | 99 tok/s vs 48 |
| Hard reasoning / arbitration | 27B, thinking on, budget ≥8000 | 88% vs 85% |

**Reasoning costs 51× wall time on both engines** (14.9 s → 762 s) for +3–6% accuracy. Use it for
disputes, not for batches.

## 5. Prompt rules learned by measurement

- **Any field the model does not need is a risk.** Send the system prompt as a `system` message —
  one battery failed entirely because the system prompt was defined and never sent, and the model
  answered the question instead of auditing it.
- **Prefer an executable checklist to a judgement.** Numbered checks that report which check fired
  produce evidence; "decide if this is defective" does not.
- **State what is NOT a defect.** All engines rarely false-alarm (0 false positives in the battery)
  but this is partly a bias toward CLEAN.
- **Ask each case more than once.** A case that passed once failed 5/5 for the other engine; the
  reverse was also true. **A single pass is not a pass, it is an untested case.**
- **A model must be able to only organize, never resolve semantics** — every check has to be
  executable.

## 6. Reporting

Report prefill and decode separately, name the probe method, and state the token budget next to
any accuracy number. Report a false alarm count, not only a hit count. When a model answers an
exam question, score it against the official answer sheet and accept `送分` / `A或D` as legitimate —
and count empties separately from wrong answers.

## Reference implementation

`scripts/compare_engines.py` (same battery across engines), `scripts/measure_speed.py`
(prefill/decode/concurrency with measured reasoning control), `src/qbr/vision.py`
(`_thinking_forms`). Full findings: `docs/ENGINE_BOUNDARY_REPORT.md` and
`docs/MODEL_CAPABILITY_PLAN.md`.
