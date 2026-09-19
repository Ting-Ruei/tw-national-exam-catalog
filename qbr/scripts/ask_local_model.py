# -*- coding: utf-8 -*-
"""Ask the local model to judge what the deterministic pipeline cannot.

The pipeline reads a paper by geometry: it groups spans by where they sit on the page. That
is enough for almost every question, and it is cheap. It also fails in ways that are
invisible from the inside - a superscript whose baseline is 1.1 points high ends up in a
different line, an option is cut to `[Na`, and every gate still passes.

Catching that does not need a big model reading the whole paper. It needs the model to look
at one short piece of text and say what is wrong with it. So the prompt is built to make the
answer short and specific: a verdict, a machine-readable code, and a one-line reason. The
codes are the point - they are what the repair step acts on.

The model is advisory. It never accepts, blocks, or writes anything; it reports a suspicion,
and a rule is written for the suspicion and measured against the corpus. That is GOV-05, and
it is also the only way the answer is worth having.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# The endpoint is a local engine on this machine, not a service. `AI395` is named here only
# because it was the first host this was pointed at; it is switched off, and the architecture is
# meant to be machine-independent, so the address lives in the environment rather than in the
# code. The default is the local MTPLX engine.
#
# `mtplx` is the OpenAI-compatible server in front of the local weights: 35b on port 18120 and
# 9b on 18121. The 35b is the one worth asking - it is slower but it answers, and a wrong answer
# costs more than a slow one here because every verdict is read by a person.
BASE_URL = os.environ.get("QBR_MODEL_BASE_URL", "http://127.0.0.1:18120")
API_KEY = os.environ.get("QBR_MODEL_API_KEY", "mtplx")
MODEL = os.environ.get("QBR_MODEL_NAME", "ornith-1.5-mtplx-35b")


def _endpoint(base, path="/chat/completions"):
    """Join a base URL and an OpenAI path without caring whether `/v1` was written down.

    Both `http://host:port` and `http://host:port/v1` are the same server, and a reader setting
    the environment should not have to know which form this file expects. The version segment is
    normalised in rather than required.
    """
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + path

# The vocabulary of defects worth asking about. Each one is a thing this pipeline has
# actually got wrong, or a thing a reader can see from the text alone. A code the repair step
# does not know how to act on is worse than no code: it invites a guess.
DEFECT_CODES = {
    "SUPERSCRIPT_FLATTENED": "上下標被壓平成普通字元，公式或化學式因此讀不出層次（如 PO4 3- 應為 PO₄³⁻）",
    "OPTION_TRUNCATED": "選項被截斷，看起來只有半截（如 [Na、[K 這種沒有結尾的括號）",
    "SPACE_INSIDE_WORD": "中文詞中間多了不該有的空格（中文書寫不用空格斷詞）",
    "OPTION_MERGED_INTO_STEM": "選項內容跑進題幹，題幹結尾出現公式或選項編號",
    "MISSING_OPTION": "選項數不足四個，或選項編號跳號",
    "STEM_TRUNCATED": "題幹在句中斷掉，語意不完整",
    "GLYPH_DAMAGE": "出現不該出現的字元（亂碼、罕用異體字、簡體字）",
    "OK": "沒有發現異常",
}

SYSTEM = """你是國考題庫的抽取品管員。你的工作是看一段「從官方 PDF 自動抽出的題目文字」，判斷它有沒有被抽取過程弄壞。

你只看文字，不要回答題目的正確答案，也不要管題目本身難不難。你唯一要判斷的是：這段文字能不能忠實呈現紙本題目。

可能的損壞種類（只能用這些代碼）：
{codes}

【最重要的已知損壞模式】這套抽取器會把上下標（superscript / subscript）的字元推出原位：
- 上下標字元會被搬到「行尾」或「下一個位置」，形成游離碎片。
- 紙本 H₂PO₄⁻ 可能抽成「H PO」＋行尾的「2 4 -」。
- 紙本 Vmax、Km 可能抽成「V」＋行尾「max」，或「K」＋行尾「m」。
- 紙本 10⁻⁵ 可能抽成「10」＋行尾「-5」。
- 紙本 ①α₁ 可能抽成「①α」＋行尾「1」。

**因此：凡是題幹或選項結尾出現游離的英文字母、數字、正負號（如 "max m -5"、"2 3"、"+ 4"），幾乎都是上下標或下標文字被推出原位，應判定 SUPERSCRIPT_FLATTENED。不要試著解讀這些碎片的意思，也不要推理題目本身的內容。**

最重要的判斷依據：
- 化學式與離子式：上下標被壓平會讓 PO4 3- 這種寫法出現。紙本 PO₄³⁻ 抽出後若變成「PO4 3-」或「PO43-」，那是上下標資訊遺失。
- 括號：[Na+] 是完整的；[Na 是壞的。
- 中文詞不該有空格：「中毒現 象」是壞的，「anion gap 上升」是好的（英數需要空格）。
- 選項應該是四個，且每個都語意完整。

【輸出要求】直接給結論，不要長篇推理。只輸出這個 JSON，不要有其他文字：
{{"verdict":"OK"或"DEFECT","code":"上面其中一個代碼","detail":"一句話說明哪裡壞了，指出具體位置","confidence":0.0到1.0}}

若 verdict 是 DEFECT，code 不可以是 OK。若不確定，verdict 用 OK 並把 confidence 設低，不要猜。"""

USER = """科目：{subject}
題號：{number}

題幹：
{stem}

選項：
{options}

請判斷這段抽取文字有沒有損壞。"""


def _options_block(options):
    return "\n".join(f"{o.get('key')}. {o.get('text')}" for o in (options or []))


def build_messages(*, number, stem, options, subject=""):
    codes = "\n".join(f"- {code}：{desc}" for code, desc in DEFECT_CODES.items())
    return [
        {"role": "system", "content": SYSTEM.format(codes=codes)},
        {"role": "user", "content": USER.format(subject=subject or "（未知）", number=number,
                                                stem=stem or "（空白）",
                                                options=_options_block(options) or "（無）")},
    ]


# A reasoning model spends most of its budget thinking before it answers, and the thinking
# is charged to `max_tokens`. Measured on this one: a trivial question took 932 reasoning
# tokens and a verdict of the same size; at 400 the answer was cut off mid-thought and the
# verdict was lost entirely. The budget is therefore set well above the length of the answer
# itself, because what has to fit is the thinking, not the JSON.
def ask(messages, *, max_tokens=2000, timeout=900):
    """One call. Returns (parsed_or_None, raw_text, usage, seconds)."""
    import time
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
    request = urllib.request.Request(
        _endpoint(BASE_URL), data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
    started = time.time()
    try:
        raw = json.loads(urllib.request.urlopen(request, timeout=timeout).read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return None, f"request failed: {exc}", {}, time.time() - started
    elapsed = time.time() - started
    choice = (raw.get("choices") or [{}])[0].get("message") or {}
    content = choice.get("content") or ""
    return _parse(content), content, raw.get("usage") or {}, elapsed


def _parse(content):
    """Pull the JSON verdict out of whatever the model wrapped it in."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        verdict = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if verdict.get("code") not in DEFECT_CODES:
        return None
    return verdict


def judge(number, stem, options, subject=""):
    parsed, raw, usage, seconds = ask(build_messages(number=number, stem=stem,
                                                    options=options, subject=subject))
    return {"number": number, "verdict": parsed, "raw": raw, "usage": usage,
            "seconds": round(seconds, 1)}


def judge_paper(questions, *, subject="", verbose=False):
    """Judge every question of one paper. Returns (verdicts, summary).

    Sequential on purpose: the endpoint is a single local engine, and firing eighty requests
    at it would queue them anyway while making the timing meaningless.
    """
    verdicts, summary = [], {"ok": 0, "defect": 0, "unparsed": 0,
                             "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}
    for question in questions:
        number = int(question["question_number"])
        result = judge(number, question.get("stem"), question.get("options"), subject=subject)
        verdict = result["verdict"] or {}
        if not verdict:
            summary["unparsed"] += 1
        elif verdict.get("verdict") == "DEFECT" and verdict.get("code") != "OK":
            summary["defect"] += 1
        else:
            summary["ok"] += 1
        summary["prompt_tokens"] += result["usage"].get("prompt_tokens") or 0
        summary["completion_tokens"] += result["usage"].get("completion_tokens") or 0
        summary["seconds"] = round(summary["seconds"] + result["seconds"], 1)
        verdicts.append(result)
        if verbose:
            label = verdict.get("code", "UNPARSED")
            print(f"  Q{number:03d} {label:24} {verdict.get('confidence', '')} "
                  f"{verdict.get('detail', result['raw'][-80:])[:90]}", flush=True)
    return verdicts, summary


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Ask the local model to judge extracted questions (advisory only).")
    parser.add_argument("--candidates", required=True,
                        help="candidate JSONL from the golden path (review-ui/candidates.jsonl)")
    parser.add_argument("--out", required=True, help="where to write the verdicts (JSONL)")
    parser.add_argument("--subject", default="")
    parser.add_argument("--only", nargs="*", type=int, default=None, help="question numbers")
    parser.add_argument("--resume", action="store_true", help="skip numbers already in --out")
    args = parser.parse_args()

    with open(args.candidates, encoding="utf-8") as handle:
        questions = [json.loads(line) for line in handle if line.strip()]
    if args.only:
        wanted = set(args.only)
        questions = [q for q in questions if int(q["question_number"]) in wanted]
    questions.sort(key=lambda q: int(q["question_number"]))

    done = set()
    if args.resume and os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as handle:
            done = {int(json.loads(line)["number"]) for line in handle if line.strip()}
        questions = [q for q in questions if int(q["question_number"]) not in done]

    subject = args.subject
    if not subject and questions:
        subject = (questions[0].get("metadata") or {}).get("normalized_subject_name") or ""
    print(f"judging {len(questions)} questions against {MODEL} at {BASE_URL}", flush=True)

    totals = {"prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}
    with open(args.out, "a", encoding="utf-8") as handle:
        for question in questions:
            number = int(question["question_number"])
            result = judge(number, question.get("stem"), question.get("options"), subject=subject)
            verdict = result["verdict"] or {"verdict": "UNPARSED", "code": None,
                                            "detail": result["raw"][-200:]}
            handle.write(json.dumps({"number": number, "subject": subject, **verdict,
                                     "seconds": result["seconds"],
                                     "usage": result["usage"]}, ensure_ascii=False) + "\n")
            handle.flush()
            for key in totals:
                totals[key] += (result["seconds"] if key == "seconds"
                                else result["usage"].get(key) or 0)
            flag = "  <== DEFECT" if verdict.get("code") not in (None, "OK") else ""
            print(f"Q{number:03d} {str(verdict.get('code')):24} "
                  f"{result['seconds']:5.1f}s {verdict.get('detail', '')[:70]}{flag}", flush=True)

    print(f"\ntotal: {totals['seconds']:.0f}s  prompt={totals['prompt_tokens']} "
          f"completion={totals['completion_tokens']}", flush=True)


if __name__ == "__main__":
    main()
