#!/usr/bin/env python3
"""給設計者用的互動判讀介面：一次一題、整題完整、右邊配 PDF、判讀寫進檔案。

**這個檔是 UI 的後端，不是 agent。** agent 的本體是 `agent.mjs`（Pi SDK）；
本檔只做兩件事：把 `bridge.py` 已經算好的東西端給瀏覽器，以及把設計者的判讀
**寫進同一條學習流**（`store/agent_feedback.jsonl`）。

## 為什麼不是靜態頁

`a2/runs/*.html` 是靜態頁、零 fetch、判讀只存 localStorage（`grep fetch` = 0）。
設計者第八輪要的是**互動**：每一題獨立顯示、右邊配 PDF、判讀**寫入檔案**當學習語料。
localStorage 是「寫在這台瀏覽器裡」，換一台機器就不見了，也不會進到 agent 的記憶。

## 與 v2 的關係（為什麼不直接改 v2）

v2 是**審題介面**（人做 accept／block 決定，寫 `question_review_events.jsonl`）。
這一頁是**學習介面**（人下指導，寫 agent 的學習語料）。兩者的資料流不同、
**權威也不同**：v2 的決定是人工審核紀錄，這一頁的指導是模型的前驗。
照 Q15 的裁決「實驗階段只加註記」，這一頁**另開一支**，不動 v2。

## 紀律

- **只寫 `store/agent_feedback.jsonl`**（append-only，schema 對齊
  `review_state.append_ai_feedback`）。**永不碰 `question_review_events.jsonl`**。
- 讀取一律走 `bridge.py` 的函式，**不重寫一份**。路徑解析走
  `qbr.review_ui.paths.project_path`（v2 用的同一支），所以瀏覽器安全變體
  （JPEG 2000 的掃描頁）自動生效。

    python3 server.py --port 8790          # 然後開 http://127.0.0.1:8790
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import subprocess

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parent
SANDBOX = AGENT_DIR.parent
CATALOG = SANDBOX.parent
QBR = CATALOG / "qbr"

for path in (QBR / "src", QBR / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# `bridge.py` is imported as a module so the UI shows exactly what the agent sees. A second
# implementation of "the complete question" here would be a second answer to the same question,
# and the first thing to drift would be the figure boxes.
_spec = importlib.util.spec_from_file_location("repair_agent_bridge", AGENT_DIR / "bridge.py")
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

import platform_view  # noqa: E402  (bridge already put `lib/` on sys.path)

from qbr.review_ui.paths import content_type_of, safe_file_path  # noqa: E402

# The queue root must be an allowed asset root, or `/file` refuses every figure and PDF crop.
#
# This is not a new idea: it is the **station's own contract**. The v2 service runs with
# `REVIEW_UI_ADDITIONAL_ASSET_ROOTS: /queue` and mounts the live queue at `/queue`
# (`deploy/qbr-review/compose.yaml`), which is what makes `review-ui/crops/...` resolvable there.
# Measured 2026-09-28: without it, `safe_file_path` maps a crop to
# `國考題資料夾/review-ui/crops/...` (which does not exist) and `/file` answers **404** — the crops
# were on disk, the pipeline had cut them correctly, and the page showed none of them.
#
# Set with `setdefault` so a deployment that already sets it (the station does) keeps its own value;
# one contract, two places it can be configured, and the station wins where it is deployed.
os.environ.setdefault("REVIEW_UI_ADDITIONAL_ASSET_ROOTS", bridge.QUEUE_ROOT)

STORE = Path(bridge.STORE)
RATINGS = bridge.JUDGEMENT_RATINGS
STORE_DIR = Path(bridge.STORE_DIR)
LESSONS = STORE_DIR / "lessons.jsonl"
# The agent's own trace, written by `agent.mjs`. Read-only here: this server never writes it.
LOG = STORE_DIR / "agent.log.jsonl"
CROPS = STORE_DIR / "crops"

_write_lock = threading.Lock()

#: The reviewer's ledger. The **bridge** never writes it (its own header contract:
#: 永不碰 `question_review_events.jsonl`); the *only* way a sandbox-written human event comes into
#: existence is a designer's own click on the accept key — server-side, under this lock, with the
#: accepted draft's checksum carried in the event so the decision points at an immutable proposal.
#: `REPAIR_AGENT_LEDGER` redirects it for tests; unset in real use means the live queue's own file.
HUMAN_LEDGER = os.environ.get("REPAIR_AGENT_LEDGER") or bridge.HUMAN_EVENTS

#: The sandbox's own source marker: distinguishable, at a glance, from v2's `linear_v2` rows —
#: the ledger must be able to answer 「這一筆 accept 是在哪個入口按的」.
SANDBOX_ACCEPT_SOURCE = "sandbox_accept"

#: Where the v2 審題站 lives. The sandbox links to it and v2 links back here (owner 2026-10-02:
#: 「整套圈要可以運作」), so neither side hard-codes the other's host in HTML. Default is the
#: station's own service; a checkout running v2 locally overrides with `REPAIR_AGENT_V2_BASE`.
V2_BASE = os.environ.get("REPAIR_AGENT_V2_BASE") or "http://192.168.10.70:8765"

# 指揮者的腦（owner 2026-10-03：「指揮者可以換成 192.168.10.90:8888 的模型」＋「指揮者是要去
# 訓提示詞跟指揮 MoE 模型做事的」）。只交給**對話子進程**（ui/chat.mjs）；做事 agent（worker）
# 維持原腦 occamy——智能分層是整個指揮鏈的前提。值是 `REPAIR_AGENT_MODEL` 同格式：
# `<引擎名>/<served id>`；引擎表在 qbr/src/qbr/engines.py（`dgx-flash`＝timsdgx:8888，
# LAN 192.168.10.90:8888 同源）。設 `REPAIR_AGENT_CONDUCTOR_MODEL=""` 回復 occamy 對話。
CONDUCTOR_MODEL = os.environ.get("REPAIR_AGENT_CONDUCTOR_MODEL", "dgx-flash/GLM-5.3-Flash-EXL3")


def _page(name: str) -> bytes:
    """One UI page with the deployment's own links baked in.

    The pages are static files carrying a single token, `__V2_BASE__`; replacing it here is what
    lets the same checkout point at a local v2 or at the station without editing HTML. `no-store`
    already governs these responses, so a changed base is a refresh away.
    """
    return (HERE / name).read_bytes().replace(b"__V2_BASE__", V2_BASE.encode())


def _read_jsonl(path: Path) -> list[dict]:
    """Tolerant read: a half-written last line must not blank the whole page."""
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


# --- 調度監控（owner 2026-10-03）---------------------------------------------------------
# 指揮者（對話 GLM）沒有派工工具；做事 run 是 runner.mjs 吃 workorder 手動開的。她的「訊號」
# 是對話裡那句「這要開一次做事 run」，做事的「事實」是三個 append-only 檔：
#   store/workorders/report.jsonl   — runner 每輪的 metrics（drafted/rejected/failed）
#   store/repair_drafts.jsonl       — 草案落成（run_id 依輪計數）
#   store/chat.jsonl                — 指揮者請求做事的那句話
# 這裡只**讀**三個檔、算出畫面要的形狀；不寫任何檔（G0），也不假裝能開始/停止 run——
# 那是 G3 派工，未經 owner 逐次核准不存在。

#: 指揮者請求做事的話，跟她 seed 裡被教的那句完全同形（ui/chat.mjs 兩處）。
DISPATCH_PHRASES = ("要開一次做事 run", "開一次做事 run", "開做事 run")


def dispatch_status() -> dict:
    """The conductor↔worker dispatch picture, read-only, for the header lamp and the runs panel."""
    reports = _read_jsonl(STORE_DIR / "workorders" / "report.jsonl")
    batches = [r for r in reports if r.get("kind") == "batch"]
    last_batch = batches[-1] if batches else None
    metrics = (last_batch or {}).get("metrics") or {}

    # drafts per run: count lines by run_id (whole-file scan; the file is 5.8 MB and read once here)
    per_run: dict[str, int] = {}
    if (STORE_DIR / "repair_drafts.jsonl").is_file():
        with (STORE_DIR / "repair_drafts.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if "run_id" not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                rid = row.get("run_id")
                if rid:
                    per_run[rid] = per_run.get(rid, 0) + 1

    # the conductor's own requests, newest last, from the append-only transcript
    asks = []
    chat_path = STORE_DIR / "chat.jsonl"
    if chat_path.is_file():
        with chat_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                text = row.get("text") or ""
                # The phrase appears in both directions: the conductor asks for a worker run, and
                # the designer may relay it. What matters is the *request*, not the speaker — the
                # role is recorded and the panel shows it, so a designer's relay is not passed off
                # as the conductor's own words.
                if any(p in text for p in DISPATCH_PHRASES):
                    asks.append({
                        "at": row.get("at"),
                        "role": row.get("role"),
                        "key": row.get("candidate_key"),
                        "text": text[:120],
                    })
    return {
        "last_batch": {
            "at": last_batch.get("at"),
            "workorder": last_batch.get("workorder"),
            "variant": last_batch.get("variant"),
            "run_id": last_batch.get("run_id"),
            "metrics": metrics,
        } if last_batch else None,
        "drafts_by_run": per_run,
        "conductor_asks": asks[-5:],
    }


def judgements_for(key: str) -> list[dict]:
    """What has already been said about this question, by the agent and by the designer.

    Both are in the same stream on purpose: the designer's guidance and the agent's judgement are
    both "evidence about this question", and separating them by file would make "who said this"
    a property of the path rather than of the record. `source` carries that, explicitly.
    """
    return [row for row in _read_jsonl(STORE) if row.get("candidate_key") == key]


def lessons_for(key: str) -> list[dict]:
    return [row for row in _read_jsonl(LESSONS) if row.get("key") == key]


def trace_for(key: str) -> list[dict]:
    """The agent's tool calls that named this question, in order.

    This is the honest answer to「AI 實際上讀到／產生什麼」for a judgement already made: the
    feedback record keeps only the conclusion (`reason`), so a reviewer who wants to argue with the
    reading has to see **what the agent did** — which tool, with what arguments, returning what.

    Read from `store/agent.log.jsonl`, which is append-only and written by `agent.mjs`. A call is
    matched by the question key appearing in its arguments *or* its recorded summary, because
    `read`/`read_page` name the crop or the key and `crop_question` names the `--out` path — matching
    on `candidate_key` alone would drop exactly the calls that produced the picture.

    Deliberately **not** summarised here: the log's `summarize()` already decided what to keep (a
    lossy step, and one that once lost an image), and this returns the stored line so the reviewer
    reads what was recorded rather than a third rendering of it.
    """
    rows = []
    for row in _read_jsonl(LOG):
        blob = json.dumps(row, ensure_ascii=False)
        if key in blob:
            rows.append(row)
    return rows


def append_judgement(record: dict) -> dict:
    """Append one record. The only write this server performs.

    Mirrors `review_state.append_ai_feedback`'s shape so that promoting this stream into the real
    one later is a change of destination, not of format. `candidate_key` is required because a
    judgement with no question is a judgement nobody can act on.

    The provenance stamp is the **bridge's** `judgement_envelope()` — the one producer of the four
    columns (`at`/`run_id`/`session_id`/`prompt_version`), so a designer row and an agent row are
    the same shape and cannot drift (workplan 2.1, 2026-09-30). A designer rating is not an agent
    run, so its run/session id is the honest empty string; `engine: human:designer` still says who
    wrote it.

    The reason is **optional** here (2026-09-30, workplan 2.2): the designer's decision is the
    verdict, and a one-key rating must not be gated on writing prose first. The agent's rating —
    a different writer — carries its basis as a contract of its own, refused in `bridge.py`.
    """
    if not record.get("candidate_key"):
        raise ValueError("candidate_key is required")
    stamp = bridge.judgement_envelope()
    merged = {**stamp, **record}
    merged["at"] = record.get("at") or stamp["at"]
    merged.setdefault("action", "ai_feedback")
    merged.setdefault("schema", bridge.AI_FEEDBACK_SCHEMA)
    with _write_lock:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        with STORE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(merged, ensure_ascii=False) + "\n")
    return merged


def _draft_line_by_sha(key: str, draft_sha256: str) -> dict:
    """The referenced draft, looked up by the checksum of **its own line**.

    The reference is the whole point of the checksum: an accept event that names a draft as
    「the 3rd one, roughly」 points at a moving target, while the line's own SHA-256 cannot change.
    A ref that matches no line is a fabricated or stale decision and is refused outright.
    """
    import hashlib

    if not os.path.exists(bridge.DRAFTS):
        raise ValueError("no drafts exist for this store yet")
    with open(bridge.DRAFTS, "rb") as handle:
        for raw in handle:
            line = raw.rstrip(b"\n")
            if not line:
                continue
            digest = hashlib.sha256(line).hexdigest()
            if digest != draft_sha256:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("candidate_key") != key:
                raise ValueError("draft checksum refers to another question's draft")
            out = dict(row)
            out["_line_sha256"] = digest
            return out
    raise ValueError("no draft of key %s carries checksum %s" % (key, draft_sha256))


def append_accept(event: dict) -> dict:
    """Append one **human** review event — reachable only from the designer's own click.

    The ruling it implements (2026-09-30, workplan 2.5): the pass key writes the reviewer's ledger
    with the event referencing the accepted draft's checksum, and **the machine never presses it**
    — there is no CLI, tool or agent route into this function's write, and the bridge itself stays
    a reader of the ledger by contract. Appended-only (`open "a"`): a mistake in judgement is a
    row to outlive, not something to rewrite.
    """
    key = str(event.get("key") or "").strip()
    draft_sha = str(event.get("draft_sha256") or "").strip()
    if not key or not draft_sha:
        raise ValueError("accept requires the question key and the draft's line checksum")
    draft = _draft_line_by_sha(key, draft_sha)
    created = bridge._utc_now()
    review_event = {
        "action": "accept",
        "candidate_key": key,
        "created_at": created,
        "notes": (event.get("notes") or "").strip()[:2000],
        "reviewer": "local",
        "source": SANDBOX_ACCEPT_SOURCE,
        "repair_draft_sha256": draft["_line_sha256"],
        "repair_draft_crop_sha256": (draft.get("crop") or {}).get("sha256") or "",
        "repair_draft_at": (draft.get("at") or ""),
    }
    with _write_lock:
        ledger = Path(HUMAN_LEDGER)
        if not ledger.exists():
            raise ValueError(
                "reviewer ledger not found at %s; refusing to create one from the sandbox" % ledger)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(review_event, ensure_ascii=False) + "\n")
    return {"event": review_event, "ledger": str(HUMAN_LEDGER)}


def append_return(payload: dict) -> dict:
    """Record the designer's 退回 of a draft: a judgement in the learning stream, reason required.

    The reason is the point: it is the raw material the rule house (3.1) harvests, so an empty
    return is refused rather than politely saved. The draft's file itself is untouched — the
    proposal stays proposed on paper and is superseded by the designer's row here, with the draft
    checksum keeping the two record types joinable.
    """
    key = str(payload.get("key") or "").strip()
    draft_sha = str(payload.get("draft_sha256") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not key or not draft_sha:
        raise ValueError("return requires the question key and the draft's line checksum")
    if not reason:
        raise ValueError("a return must say why: the reason is the rule candidate")
    draft = _draft_line_by_sha(key, draft_sha)
    stamp = bridge.judgement_envelope()
    row = {
        **stamp,
        "action": "repair_return",
        "schema": bridge.AI_FEEDBACK_SCHEMA,
        "candidate_key": key,
        "question_number": draft.get("question_number"),
        "subject": draft.get("subject"),
        "rating": "down",
        "reason": reason[:2000],
        "engine": "human:designer",
        "source": "designer",
        "repair_draft_sha256": draft["_line_sha256"],
    }
    with _write_lock:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        with STORE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"appended": row, "store": str(STORE)}


def sandbox_accepts_for(key: str) -> list[dict]:
    """This question's sandbox accept events, read from the reviewer's ledger itself.

    The drafts panel marks a proposal 「已通過」 only when the **ledger** says so — the drafts file
    stays proposals-only, and what the panel shows as decided is exactly what the reviewer's own
    stream records, not a shadow state that could disagree with it.
    """
    path = Path(HUMAN_LEDGER)
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if key not in line or SANDBOX_ACCEPT_SOURCE not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("candidate_key") == key and row.get("source") == SANDBOX_ACCEPT_SOURCE:
                out.append(row)
    return out


FIGURE_LANDING_REVIEWER = "repair_figure_landing"
FIGURE_LANDING_MANIFEST = STORE_DIR / "figure_landing.jsonl"


def _human_landing_states(keys: set) -> dict:
    """`key → {human, human_at, reset_after, source, draft_sha}` — 一次掃完帳本，只看**人**寫的列。

    `human` 是該題最後一筆人審 action；`reset_after` 是那句話**之後**有沒有 reset_review
    （內容變過 → 已經在重審池裡）。機器列不覆寫人的話，只讓 reset 在人話之後成立。
    `source`/`draft_sha` 記最後一筆人審的來源與它指到的草案行——文字落地要靠它分辨
    「這一筆 accept 就是授權本身」（sandbox accept、同一草案）與「更早的最終決定」（不動）。
    """
    states: dict[str, dict] = {}
    if not os.path.exists(bridge.HUMAN_EVENTS):
        return states
    with open(bridge.HUMAN_EVENTS, encoding="utf-8") as handle:
        for line in handle:
            if "candidate_key" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            key = row.get("candidate_key") or ""
            if key not in keys:
                continue
            st = states.setdefault(key, {"human": None, "human_at": "", "reset_after": False,
                                         "source": "", "draft_sha": ""})
            reviewer = str(row.get("reviewer") or "")
            if any(reviewer.startswith(p) for p in bridge.REPAIR_REVIEWER_PREFIXES):
                if row.get("action") == "reset_review" and st["human"] is not None \
                        and str(row.get("created_at") or "") > st["human_at"]:
                    st["reset_after"] = True
                continue
            action = str(row.get("action") or "")
            if action:
                st["human"], st["human_at"], st["reset_after"] = action, str(row.get("created_at") or ""), False
                st["source"], st["draft_sha"] = str(row.get("source") or ""), str(row.get("repair_draft_sha256") or "")
    return states


def _apply_figure_drafts(keys=None) -> dict:
    """把**已判 pass** 的圖草案寫進 candidates.jsonl——設計者按「寫入」的當下生效。

    這就是 G3 的執行點，不是又一個閘門：核准是學習檔裡那一筆人類 pass（`figure_review`），
    這裡只執行它；沒有記錄在案的 pass 就沒有可執行的核准，逐鍵拒絕。

    契約：
    - 只增不刪：`image_refs` 只追加；裁片必須實際存在於 QUEUE_ROOT 才可引用。
    - 人的最新一句話是 accept 且之後沒有 reset → **不動**（accept 是最終決定，
      草案要碰到核准過的題目才需要理由——owner 2026-10-03 裁決）。
    - 被審過的其他題（block／comment）寫入後補一筆 append-only `reset_review`
      （repo 規則：內容改變的已審候選要逐題 reset 並保留原註記），v2 會重新端上來；
      從沒被審過的題默默寫入——沒有人的話可 reset。
    - 第一次寫入前整份備份到 `<queue>/backups/`；manifest（append-only）記 before/after sha256。
    """
    import hashlib

    state = _figure_state()
    if keys is None:
        keys = [k for k, d in state.items() if d.get("agreed") and d.get("ruling") == "pass"]
    keys = [str(k) for k in (keys or []) if k]
    if not keys:
        raise ValueError("no keys to apply")

    skipped: list[dict] = []
    want = set(keys)
    plans: dict[str, dict] = {}
    for key in keys:
        d = state.get(key) or {}
        if not d:
            skipped.append({"key": key, "reason": "沒有圖草案"})
            want.discard(key)
            continue
        if not d.get("agreed") or d.get("ruling") != "pass":
            skipped.append({"key": key, "reason": "沒有記錄在案的人類 pass（或兩輪不一致）"})
            want.discard(key)
            continue
        by_base = {os.path.basename(p): p for p in (d.get("crops") or [])}
        entries = []
        for fn in (d.get("a") or {}).get("refs") or []:
            path = by_base.get(fn)
            if not path or not os.path.exists(os.path.join(bridge.QUEUE_ROOT, path)):
                skipped.append({"key": key, "reason": "裁片不存在：%s" % fn})
                want.discard(key)
                entries = []
                break
            entries.append({"asset_role": "figure-crop", "description": "figure 草案落地（設計者核可）",
                            "exists": True, "label": "figure-crop", "page": None, "path": path,
                            "placement": "question", "raw_ref": fn,
                            "source": "figure_missing_second_pass"})
        if entries:
            plans[key] = {"entries": entries, "draft": d}

    human = _human_landing_states(want)
    events: list[dict] = []
    now = bridge._utc_now()
    for key in list(plans):
        st = human.get(key) or {}
        if st.get("human") == "accept" and not st.get("reset_after"):
            skipped.append({"key": key, "reason": "你已 accept（最終決定），不動"})
            del plans[key]
        elif st.get("human") is not None:
            events.append({"action": "reset_review", "candidate_key": key, "created_at": now,
                           "reviewer": FIGURE_LANDING_REVIEWER,
                           "notes": "figure 草案落地：設計者核可的圖 refs 已寫入 image_refs，"
                                    "題目內容改變，請重新確認。原裁決與理由保留在學習檔。",
                           "changes": [{"field": "image_refs", "from": "（未含草案圖）", "to":
                                        [e["path"] for e in plans[key]["entries"]]}]})

    candidates_path = Path(bridge.CANDIDATES)
    if not candidates_path.is_file():
        raise ValueError("candidates.jsonl not found at %s" % candidates_path)
    data = candidates_path.read_bytes()
    sha_before = hashlib.sha256(data).hexdigest()
    lines = data.decode("utf-8").splitlines(keepends=True)
    replaced: set[str] = set()
    for i, raw in enumerate(lines):
        if not raw.strip() or len(replaced) == len(plans):
            continue
        probe = raw[:4096]
        if not any(k.encode() in probe.encode() for k in plans):
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        key = row.get("candidate_key") or ""
        if key not in plans or key in replaced:
            continue
        existing = row.get("image_refs") or []
        have = {r.get("path") for r in existing if isinstance(r, dict)}
        fresh = [e for e in plans[key]["entries"] if e["path"] not in have]
        if not fresh:
            skipped.append({"key": key, "reason": "已寫入過"})
            del plans[key]
            continue
        if isinstance(row.get("question_page"), int):
            for e in fresh:
                e["page"] = row["question_page"]
        row["image_refs"] = list(existing) + fresh
        newline = json.dumps(row, ensure_ascii=False)
        lines[i] = newline + ("\n" if raw.endswith("\n") else "\n")
        replaced.add(key)
    for key in set(plans) - replaced:
        skipped.append({"key": key, "reason": "candidates 沒有這一題"})
        del plans[key]
    if not plans:
        return {"applied": [], "skipped": skipped, "wrote": False}

    backup_dir = Path(bridge.QUEUE_ROOT) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("candidates.jsonl.before-figure-landing-%s" % now.replace(":", ""))
    backup.write_bytes(data)
    out = ("".join(lines)).encode("utf-8")
    with _write_lock:
        tmp = candidates_path.with_suffix(".jsonl.tmp")
        tmp.write_bytes(out)
        os.replace(tmp, candidates_path)
        if events:
            with open(bridge.HUMAN_EVENTS, "a", encoding="utf-8") as handle:
                for ev in events:
                    handle.write(json.dumps(ev, ensure_ascii=False) + "\n")
        with FIGURE_LANDING_MANIFEST.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "at": now, "actor": "human:designer", "applied": sorted(plans),
                "skipped": skipped, "sha_before": sha_before,
                "sha_after": hashlib.sha256(out).hexdigest(),
                "backup": str(backup), "resets": len(events),
            }, ensure_ascii=False) + "\n")
    return {"applied": sorted(plans), "skipped": skipped, "wrote": True,
            "backup": str(backup), "resets": len(events)}


# --- 文字草案落地（L3 後端：✅ 的落點）------------------------------------------------------
#
# 與 `_apply_figure_drafts` 同一個骨架，但授權來源不同：圖草案的核准在學習檔（figure_review），
# 文字草案的 ✅ 是**帳本裡的一筆 sandbox accept**（`append_accept` 寫的，帶草案行 sha256）。
# 落地只執行帳本記錄在案的 ✅——沒有授權的鍵逐鍵拒絕，機器永遠不能自己按下這一步。
TEXT_LANDING_REVIEWER = "repair_text_landing"
TEXT_LANDING_MANIFEST = STORE_DIR / "text_landing.jsonl"

# 落點解析與結構守門的**唯一**實作在 `bridge.py`（`resolve_text_target`／`is_full_replacement`）
# ——生產者（L2）與落地寫入器共用一份，兩份實作就是兩個可以不一致的地方。
_resolve_text_target = bridge.resolve_text_target
_is_full_replacement = bridge.is_full_replacement
_FIX_OPTION_PREFIX = bridge._FIX_OPTION_PREFIX


def _text_plans_for(keys: list[str]) -> tuple[dict, list[dict]]:
    """`keys` →（可落地的計畫 `key → {field, old, fix, draft_sha}`，被拒清單）。

    授權＝帳本該題**最新**一筆 sandbox accept（檔案序＝時間序）；它指到的草案就是要落地的草案。
    fix 空／等於現值／insert 認不得 → 拒絕該鍵。題幹帶結構守門（與前端預覽同一條規則，
    `bridge.is_full_replacement`）：片段或建議不是整欄替換，拒絕。
    """
    skipped: list[dict] = []
    plans: dict[str, dict] = {}
    for key in keys:
        accepts = sandbox_accepts_for(key)
        if not accepts:
            skipped.append({"key": key, "reason": "帳本沒有這一題的 ✅（未 accept 的 key 拒絕）"})
            continue
        accept = accepts[-1]
        try:
            draft = _draft_line_by_sha(key, accept.get("repair_draft_sha256") or "")
        except ValueError as exc:
            skipped.append({"key": key, "reason": "✅ 指到的草案找不到：%s" % exc})
            continue
        try:
            row = bridge.load_question(key)
        except bridge.QuestionNotFound:
            skipped.append({"key": key, "reason": "candidates 沒有這一題"})
            continue
        try:
            field, old = _resolve_text_target(row, draft.get("insert") or "")
        except ValueError as exc:
            skipped.append({"key": key, "reason": str(exc)})
            continue
        fix = _FIX_OPTION_PREFIX.sub("", (draft.get("fix") or "").strip())
        if not fix:
            skipped.append({"key": key, "reason": "fix 是空的，沒有東西可寫"})
            continue
        if fix == old:
            skipped.append({"key": key, "reason": "fix 與現值相同（已寫入過或草案無改變）"})
            continue
        if field == "stem" and not _is_full_replacement(old, fix):
            skipped.append({"key": key,
                            "reason": "fix 不是「改完後的完整題幹」（與原句差太多，像是建議或片段）——請叫 agent 重提整句"})
            continue
        plans[key] = {"field": field, "old": old, "fix": fix, "draft_sha": draft["_line_sha256"]}
    return plans, skipped


def _apply_text_drafts(keys=None) -> dict:
    """把**帳本記錄在案的 ✅** 落地成 candidates.jsonl 的整欄替換——✅ 的當下生效，沒有第二段。

    契約（鐵律 3/4）：
    - `fix` 整欄替換 `insert` 指的欄位（選項換的是該選項的 `text`）；`answer` 同步
      `answer_payload`（同一件事的另一種形狀，留著舊值就是自相矛盾的列）。
    - 原值保存在 `reset_review` 事件的 `changes[].from`；欄位只增不刪。
    - 被人審過的題補一筆 append-only `reset_review`（機器前綴 `repair_text_landing`）；
      沒被審過的默默寫。最新人審是 accept 且無 reset → **不動**——除非那筆 accept 就是
      這次落地正在執行的授權本身（同一草案的 sandbox accept）。
    - 寫入前整份備份到 `<queue>/backups/`；manifest（append-only）記 before/after sha256 與草案 sha。
    """
    import hashlib

    keys = [str(k) for k in (keys or []) if k]
    if not keys:
        raise ValueError("no keys to apply")
    plans, skipped = _text_plans_for(keys)
    if not plans:
        return {"applied": [], "skipped": skipped, "wrote": False}

    # 鐵律 4：accept 過且未 reset 的題不動。但**這次正在執行的授權**（最新的 sandbox accept、
    # 同一草案）不算阻擋——它是 ✅ 本身，不是更早的最終決定。
    human = _human_landing_states(set(plans))
    now = bridge._utc_now()
    for key in list(plans):
        st = human.get(key) or {}
        if st.get("human") == "accept" and not st.get("reset_after"):
            authorized = st.get("source") == SANDBOX_ACCEPT_SOURCE \
                and st.get("draft_sha") == plans[key]["draft_sha"]
            if not authorized:
                skipped.append({"key": key, "reason": "你已 accept（最終決定），不動——除非你指名"})
                del plans[key]

    candidates_path = Path(bridge.CANDIDATES)
    if not candidates_path.is_file():
        raise ValueError("candidates.jsonl not found at %s" % candidates_path)
    data = candidates_path.read_bytes()
    sha_before = hashlib.sha256(data).hexdigest()
    lines = data.decode("utf-8").splitlines(keepends=True)
    replaced: set[str] = set()
    for i, raw in enumerate(lines):
        if not raw.strip() or len(replaced) == len(plans):
            continue
        probe = raw[:4096]
        if not any(k in probe for k in plans):
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        key = row.get("candidate_key") or ""
        if key not in plans or key in replaced:
            continue
        plan = plans[key]
        changes: list[dict] = []
        if plan["field"].startswith("option:"):
            letter = plan["field"].split(":", 1)[1]
            hit = False
            for opt in row.get("options") or []:
                if isinstance(opt, dict) and str(opt.get("key") or "").upper() == letter:
                    if str(opt.get("text") or "") != plan["fix"]:
                        changes.append({"field": "options[%s].text" % letter,
                                        "from": str(opt.get("text") or ""), "to": plan["fix"]})
                        opt["text"] = plan["fix"]
                        hit = True
                    break
            if not hit:
                skipped.append({"key": key, "reason": "candidates 列上沒有 %s 選項" % letter})
                del plans[key]
                continue
        else:
            field = plan["field"].split(":", 1)[1] if plan["field"].startswith("field:") else plan["field"]
            if str(row.get(field) or "") == plan["fix"]:
                skipped.append({"key": key, "reason": "已是這個內容（已寫入過）"})
                del plans[key]
                continue
            changes.append({"field": field, "from": str(row.get(field) or ""), "to": plan["fix"]})
            row[field] = plan["fix"]
            if field == "answer" and isinstance(row.get("answer_payload"), dict):
                ap = row["answer_payload"]
                for sub in ("answer", "raw_answer"):
                    if isinstance(ap.get(sub), str) and ap[sub] != plan["fix"]:
                        changes.append({"field": "answer_payload.%s" % sub, "from": ap[sub],
                                        "to": plan["fix"]})
                        ap[sub] = plan["fix"]
                if isinstance(ap.get("accepted_values"), list) and plan["fix"] not in ap["accepted_values"]:
                    changes.append({"field": "answer_payload.accepted_values",
                                    "from": ap["accepted_values"], "to": [plan["fix"]]})
                    ap["accepted_values"] = [plan["fix"]]
        plan["changes"] = changes
        newline = json.dumps(row, ensure_ascii=False)
        lines[i] = newline + ("\n" if raw.endswith("\n") else "\n")
        replaced.add(key)
    for key in set(plans) - replaced:
        skipped.append({"key": key, "reason": "candidates 沒有這一題"})
        del plans[key]
    if not plans:
        return {"applied": [], "skipped": skipped, "wrote": False}

    events = []
    for key in sorted(plans):
        st = human.get(key) or {}
        if st.get("human") is not None:
            events.append({"action": "reset_review", "candidate_key": key, "created_at": now,
                           "reviewer": TEXT_LANDING_REVIEWER,
                           "notes": "文字草案落地：設計者核可的 fix 已整欄寫入，題目內容改變，"
                                    "請重新確認。原裁決與理由保留在學習檔。",
                           "changes": plans[key]["changes"]})

    backup_dir = Path(bridge.QUEUE_ROOT) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("candidates.jsonl.before-text-landing-%s" % now.replace(":", ""))
    backup.write_bytes(data)
    out = ("".join(lines)).encode("utf-8")
    with _write_lock:
        tmp = candidates_path.with_suffix(".jsonl.tmp")
        tmp.write_bytes(out)
        os.replace(tmp, candidates_path)
        if events:
            with open(bridge.HUMAN_EVENTS, "a", encoding="utf-8") as handle:
                for ev in events:
                    handle.write(json.dumps(ev, ensure_ascii=False) + "\n")
        with TEXT_LANDING_MANIFEST.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "at": now, "actor": "human:designer", "applied": sorted(plans),
                "skipped": skipped, "sha_before": sha_before,
                "sha_after": hashlib.sha256(out).hexdigest(),
                "backup": str(backup), "resets": len(events),
                "drafts": {k: plans[k]["draft_sha"] for k in sorted(plans)},
            }, ensure_ascii=False) + "\n")
    return {"applied": sorted(plans), "skipped": skipped, "wrote": True,
            "backup": str(backup), "resets": len(events)}


def _latest_human_block_notes(key: str) -> str:
    """該題最新一筆**人類** block 的理由（經驗「錯誤：」半句的原料）；沒有就空字串。

    機器列（`repair_*` 前綴）不冒充人的話——經驗要記的是**你**說哪裡錯，不是機器自己的狀態。
    """
    if not os.path.exists(bridge.HUMAN_EVENTS):
        return ""
    notes = ""
    with open(bridge.HUMAN_EVENTS, encoding="utf-8") as handle:
        for line in handle:
            if '"block"' not in line or key not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("candidate_key") != key or row.get("action") != "block":
                continue
            reviewer = str(row.get("reviewer") or "")
            if any(reviewer.startswith(p) for p in bridge.REPAIR_REVIEWER_PREFIXES):
                continue
            if str(row.get("notes") or "").strip():
                notes = str(row["notes"]).strip()
    return notes


def _remember_lesson(subject: str, text: str, key: str, evidence: str = "") -> dict:
    """✅ hook 的經驗入庫。與 `lib/identity.mjs` `remember()` 同一條契約：同 (subject, kind, text)
    只累計次數不重複列——同一修法在整份卷子上重複出現時，清單該變短而不是變長。"""
    kind = "approved-repair"
    rows = _read_jsonl(LESSONS)
    for row in rows:
        if row.get("kind") == kind and row.get("text") == text and row.get("subject") == subject:
            row["count"] = (row.get("count") or 1) + 1
            row["last_key"] = key
            with _write_lock:
                tmp = LESSONS.with_suffix(".jsonl.tmp")
                tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                               encoding="utf-8")
                os.replace(tmp, LESSONS)
            return row
    record = {"at": bridge._utc_now(), "subject": subject, "kind": kind, "text": text,
              "evidence": evidence, "key": key, "count": 1, "actor": "human:designer"}
    with _write_lock:
        LESSONS.parent.mkdir(parents=True, exist_ok=True)
        with LESSONS.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def accept_and_apply(payload: dict) -> dict:
    """設計者按下 ✅ 的**那一個動作**：帳本 accept → 落地 candidates → 經驗入庫，一次完成。

    鐵律 2：不準出現「判通了還要再按寫入」。所以這不是三個端點，是一個——先驗證到
    「這個 ✅ 確實有東西可寫」（草案存在、fix 非空且異於現值、insert 認得）才寫第一個
    byte；驗證不過就整個拒絕（400），帳本、candidates、經驗檔一個都不動。
    """
    key = str(payload.get("key") or "").strip()
    draft_sha = str(payload.get("draft_sha256") or "").strip()
    if not key or not draft_sha:
        raise ValueError("accept requires the question key and the draft's line checksum")
    draft = _draft_line_by_sha(key, draft_sha)  # 不存在 → ValueError → 400，什麼都沒寫
    row = bridge.load_question(key)             # 不存在 → QuestionNotFound → 404
    field, old = _resolve_text_target(row, draft.get("insert") or "")
    fix = _FIX_OPTION_PREFIX.sub("", (draft.get("fix") or "").strip())
    if not fix:
        raise ValueError("fix 是空的，沒有東西可寫")
    if fix == old:
        raise ValueError("fix 與現值相同（已寫入過或草案無改變）——沒有東西可 ✅")
    if field == "stem" and not _is_full_replacement(old, fix):
        raise ValueError("fix 不是「改完後的完整題幹」（與原句差太多，像是建議或片段）——請叫 agent 重提整句")

    accepted = append_accept({"key": key, "draft_sha256": draft_sha,
                              "notes": str(payload.get("notes") or "")})
    result = _apply_text_drafts([key])
    lesson = None
    if result["wrote"]:
        block_reason = _latest_human_block_notes(key)
        summary = fix[:120] + ("…" if len(fix) > 120 else "")
        lesson = _remember_lesson(
            subject=draft.get("subject") or (row.get("metadata") or {}).get("normalized_subject_name") or "",
            text="錯誤：%s → 修法：%s" % (block_reason or "（沒有記錄的 block 理由）", summary),
            key=key,
            evidence="草案 %s…（%s）" % (draft_sha[:12], field))
    return {"event": accepted["event"], "ledger": accepted["ledger"], **result, "lesson": lesson}


def question_payload(key: str) -> dict:
    question = bridge.load_question(key)
    view = bridge.question_view(question)
    pdf = bridge.pdf_path_of(question)
    view["pdf_absolute"] = pdf
    view["pdf_relative"] = question.get("metadata", {}).get("question_pdf_relative")
    view["page"] = question.get("metadata", {}).get("question_page")
    view["crop_png"] = None
    # Matched on the paper's own file name, not just the number: a crop is named after the paper it
    # came from (`1152_醫事檢驗師_..._q068.png`), and two different papers both have a q068. Globbing
    # on the number alone would show one paper's crop beside another paper's question — a picture
    # that looks authoritative and is of the wrong page.
    paper = Path(pdf).name.replace(".pdf", "") if pdf else ""
    candidates = sorted(CROPS.glob("%s_q%03d*.png" % (paper, view.get("question_number") or 0)))
    if candidates:
        view["crop_png"] = str(candidates[-1])
    view["judgements"] = judgements_for(key)
    view["lessons"] = lessons_for(key)
    view["trace"] = trace_for(key)
    # The agent's own repair proposals, read-only here: the server never writes `repair_drafts.jsonl`
    # — the agent proposes, the person decides (2026-09-30, workplan 2.4). Each row travels with
    # the SHA-256 of **its own line** (`_sha256`), because that checksum is what a verdict event
    # must reference to point at an immutable proposal.
    drafts = bridge.drafts_for(key)
    if drafts and os.path.exists(bridge.DRAFTS):
        import hashlib

        by_prefix = {}
        with open(bridge.DRAFTS, "rb") as handle:
            for raw in handle:
                line = raw.rstrip(b"\n")
                if line:
                    by_prefix[hashlib.sha256(line).hexdigest()] = line
        for row in drafts:
            # Match by content is the only join a stream of append-only rows can offer.
            for digest, line in by_prefix.items():
                try:
                    if json.loads(line.decode("utf-8")) == row:
                        row["_sha256"] = digest
                        break
                except (ValueError, UnicodeDecodeError):
                    continue
    view["drafts"] = drafts
    # 草案的 fix 是「改完後的完整文字」，而欄位在畫面上是**平台渲染**（⁻¹／sup／sub）。
    # 給它補一份渲染版 `_fix_html`（同題幹走的 `as_platform_html`），預覽與草案列顯示都用它；
    # 原 `fix` 保留——驗收事件比對的是原字串。上標因此不再以 `<sup>` 字面出現在畫面上。
    for row in drafts:
        try:
            row["_fix_html"] = platform_view.as_platform_html(row.get("fix") or "")
        except Exception:
            row["_fix_html"] = None
        # 結構守門的唯一實作：difflib 相似度在這裡算一次，前端預覽只讀 `_stem_guard`。
        # None＝insert 認不得（✅ 會被後端拒）；False＝片段／建議；True＝整句替換。
        try:
            _field, _old = _resolve_text_target(question, row.get("insert") or "")
            row["_stem_guard"] = _is_full_replacement(_old, row.get("fix") or "") if _field == "stem" else True
        except ValueError as _exc:
            row["_stem_guard"] = None
            row["_stem_guard_reason"] = str(_exc)
    view["sandbox_accepts"] = sandbox_accepts_for(key)
    # 圖片草案（agent 對「圖怎麼插」的提議）只掛在**有草案的題**上；主頁中段的「圖片草案」
    # 區塊讀這裡。沒有就不帶鍵——前端不出現空區塊。
    draft = figure_draft_for(key)
    if draft:
        draft = dict(draft)
        draft["landed"] = any(
            isinstance(r, dict) and r.get("source") == "figure_missing_second_pass"
            for r in (question.get("image_refs") or []))
        view["figure_draft"] = draft
    # 綁定 v2（owner 2026-10-02：「整套圈要可以運作」）。The hash follows v2's own
    # `scopeToHash()` shape `#類科/年/次/科目/qNNN`, spelled with the **normalized** names —
    # v2's tree is keyed on those (its `toItem()` reads `normalized_*`). The `/qNNN` tail is the
    # named-question contract `scopeFromHash()` already reads. A missing piece leaves the URL
    # unset and the link hidden, rather than a link that opens the wrong paper.
    meta = question.get("metadata") or {}
    cat = meta.get("normalized_category_name") or meta.get("official_category_name")
    subj = meta.get("normalized_subject_name") or meta.get("official_subject_name")
    year, ordinal = meta.get("year"), meta.get("exam_ordinal")
    if cat and subj and year is not None and ordinal is not None and view.get("question_number"):
        import urllib.parse

        tail = "/".join(urllib.parse.quote(str(p), safe="")
                        for p in (cat, year, ordinal, subj, "q%d" % view["question_number"]))
        view["v2_url"] = "%s/v2#%s" % (V2_BASE, tail)
    return view


# --- figure-missing 證據包草案 --------------------------------------------------------------
#
# `figure_missing_second_pass.py`（qbr/scripts，PR #20）把 deterministic 掃描工單的每一列
# 決策寫進 `store/scans/20261001-figure-producer/figure_drafts_run{A,B}.jsonl`（append-only、
# queue 零寫入）。這一節只**讀**那兩個流 + 工單的裁片路徑，合成一張卡的素材；設計者的
# 裁決走 `/api/judgement` 既有寫路徑（`store/agent_feedback.jsonl`，學習流）——這一頁
# **不**寫人審帳本：落地（G3）另行 owner 核准。
FIGURE_DIR = STORE_DIR / "scans" / "20261001-figure-producer"
FIGURE_WO = STORE_DIR / "workorders" / "wo-4.1-figure-missing.jsonl"


_FIGURE: dict = {"stamp": None, "by_key": {}}


def _figure_state() -> dict:
    """`candidate_key → 草案`，以輸入檔 mtime 為快取鍵。

    兩個 run 流 + 工單 + 學習檔都是 append-only；任一變動（含你在題目頁按下裁決）就整表
    重建。量測：634 題冷建 ~2 s（不含題幹——那是 `/api/question` 的事），熱讀是 dict 查表。
    """
    import figure_missing_second_pass as figure_producer  # qbr/scripts 已在 sys.path

    sources = sorted(FIGURE_DIR.glob("figure_drafts_run*.jsonl")) + [FIGURE_WO, STORE]
    stamp = tuple((p, p.stat().st_mtime_ns) for p in sources if p.exists())
    if _FIGURE["stamp"] == stamp:
        return _FIGURE["by_key"]

    wo_rows: dict[str, dict] = {}
    for row in _read_jsonl(FIGURE_WO):
        hits = figure_producer.crop_hits_of(row.get("note"))
        wo_rows[row.get("key") or ""] = hits[0] if hits else {}

    rounds: dict[str, dict[str, dict]] = {}
    for path in sorted(FIGURE_DIR.glob("figure_drafts_run*.jsonl")):
        for row in _read_jsonl(path):
            key = row.get("candidate_key") or ""
            tag = str(row.get("tag") or "")[:1]
            if key and tag:
                rounds.setdefault(key, {})[tag] = row

    said: dict[str, str] = {}
    for row in _read_jsonl(STORE):  # 最後一次裁決說話（append-only 的「現在值」）
        extras = (row.get("extras") or {}) if isinstance(row.get("extras"), dict) else {}
        if (row.get("candidate_key") or "") in rounds and extras.get("figure_review"):
            said[row["candidate_key"]] = str(extras["figure_review"])

    def view(row: dict) -> dict:
        rec = row.get("record") or {}
        return {"decision": rec.get("decision"), "refs": rec.get("refs") or [],
                "where": rec.get("where"), "basis": rec.get("basis"),
                "degraded": bool(rec.get("degraded")),
                "at": row.get("at"), "tag": row.get("tag")}

    by_key = {}
    for key, by in rounds.items():
        hit = wo_rows.get(key) or {}
        crops = ["review-ui/crops/%s/%s" % (hit.get("run", ""), fn)
                 for fn in (by.get("A", {}).get("crop_files") or [])]
        a, b = view(by.get("A", {})), view(by.get("B", {}))
        agreed = None
        if by.get("A") and by.get("B"):
            agreed = (a["decision"], sorted(a["refs"])) == (b["decision"], sorted(b["refs"]))
        by_key[key] = {"a": a, "b": b, "agreed": agreed, "crops": crops,
                       "ruling": said.get(key, "")}
    _FIGURE["stamp"], _FIGURE["by_key"] = stamp, by_key
    return by_key


def figure_draft_for(key: str) -> dict | None:
    return _figure_state().get(key)


# --- 待你看（L3 單一清單）-------------------------------------------------------------------
#
# 一個數字回答「現在積幾題等我」：文字草案待驗收＋圖草案已判 pass 未落地＋圖草案未判。
# 來源全是 append-only 流，以 mtime 戳快取——任何一支筆動過就整表重建。

_PENDING: dict = {"stamp": None, "payload": None}


def pending_state() -> dict:
    """待你看清單，最舊優先。每列帶 kind／草案摘要／該題被退回次數（≥3 → 反覆退回，要升級）。

    文字草案（kind=text）pending 的判準：該題**最新**的 proposed 草案，且
    （a）沒有帳本 accept 指到它、（b）不在 text_landing manifest（沒落地過）、
    （c）晚於該題最新一筆人類 accept／退回——人已經說過話之後才提的草案才值得看。
    圖草案：ruling=pass 未落地（figure_pass）與未判（figure_new）。
    """
    import hashlib

    drafts_path = Path(bridge.DRAFTS)
    ledger_path = Path(bridge.HUMAN_EVENTS)
    sources = [drafts_path, ledger_path, Path(STORE), TEXT_LANDING_MANIFEST, FIGURE_LANDING_MANIFEST,
               FIGURE_WO] + sorted(FIGURE_DIR.glob("figure_drafts_run*.jsonl"))
    stamp = tuple((p, p.stat().st_mtime_ns) for p in sources if p.exists())
    if _PENDING["stamp"] == stamp:
        return _PENDING["payload"]

    # 帳本一遍：每題最新**人類** accept 的時間、所有 sandbox accept 指到的草案 sha。
    last_accept_at: dict[str, str] = {}
    accepted_shas: set[str] = set()
    if ledger_path.is_file():
        with ledger_path.open(encoding="utf-8") as handle:
            for line in handle:
                if '"accept"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("action") != "accept":
                    continue
                reviewer = str(row.get("reviewer") or "")
                if any(reviewer.startswith(p) for p in bridge.REPAIR_REVIEWER_PREFIXES):
                    continue
                key = row.get("candidate_key") or ""
                at = str(row.get("created_at") or "")
                if at > last_accept_at.get(key, ""):
                    last_accept_at[key] = at
                if row.get("source") == SANDBOX_ACCEPT_SOURCE:
                    accepted_shas.add(str(row.get("repair_draft_sha256") or ""))

    # 退回（學習流）：理由是下一輪的第一行輸入；次數是「反覆退回」升級提示的原料。
    returns: dict[str, list[dict]] = {}
    for row in _read_jsonl(Path(STORE)):
        if row.get("action") != "repair_return":
            continue
        returns.setdefault(row.get("candidate_key") or "", []).append(
            {"at": str(row.get("at") or ""), "reason": str(row.get("reason") or "")})

    landed_text_shas: set[str] = set()
    for row in _read_jsonl(TEXT_LANDING_MANIFEST):
        drafts = row.get("drafts")
        if isinstance(drafts, dict):
            landed_text_shas.update(str(v) for v in drafts.values())
    landed_figure_keys: set[str] = set()
    for row in _read_jsonl(FIGURE_LANDING_MANIFEST):
        for k in row.get("applied") or []:
            landed_figure_keys.add(str(k))

    items: list[dict] = []

    def key_number(key: str):
        m = re.search(r"q(\d+)$", key)
        return int(m.group(1)) if m else None

    # (a) 文字草案：每題**最新一列**（proposed 或 degraded，檔案序＝時間序，後面蓋前面）。
    # 問過的題永遠有落點：最新是 proposed → 待你看；最新是 degraded → AI 答不出（原因）。
    latest: dict[str, dict] = {}
    if drafts_path.is_file():
        with drafts_path.open("rb") as handle:
            for raw in handle:
                line = raw.rstrip(b"\n")
                if not line or b'"repair_draft"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("status") not in ("proposed", "degraded"):
                    continue
                key = row.get("candidate_key") or ""
                if key:
                    row["_sha256"] = hashlib.sha256(line).hexdigest()
                    latest[key] = row
    for key, draft in latest.items():
        sha = draft.get("_sha256") or ""
        if sha in accepted_shas or sha in landed_text_shas:
            continue
        rets = returns.get(key) or []
        last = max(last_accept_at.get(key, ""), rets[-1]["at"] if rets else "")
        if draft.get("at", "") > last:
            if draft.get("status") == "degraded":
                items.append({"key": key, "kind": "text_degraded", "at": draft.get("at") or "",
                              "number": draft.get("question_number") or key_number(key),
                              "subject": draft.get("subject"),
                              "label": "AI 答不出：%s" % (draft.get("note") or "未說明原因")[:70],
                              "returns": len(rets)})
                continue
            items.append({"key": key, "kind": "text", "at": draft.get("at") or "",
                          "number": draft.get("question_number") or key_number(key),
                          "subject": draft.get("subject"),
                          "label": "%s：%s" % (draft.get("insert") or "（未說明位置）",
                                               (draft.get("fix") or "")[:60]),
                          "draft_sha256": sha, "returns": len(rets)})

    # (b)+(c) 圖草案：已判 pass 未落地、未判。
    for key, d in _figure_state().items():
        ruling = d.get("ruling") or ""
        if ruling == "return":
            continue
        if ruling == "pass":
            if key in landed_figure_keys:
                continue
            kind, label = "figure_pass", "圖草案已判通過、還沒寫入題庫"
        else:
            kind, label = "figure_new", "圖草案待核：%s" % ((d.get("a") or {}).get("decision") or "?")
        items.append({"key": key, "kind": kind,
                      "at": (d.get("a") or {}).get("at") or (d.get("b") or {}).get("at") or "",
                      "number": key_number(key), "subject": None, "label": label,
                      "returns": len(returns.get(key) or [])})

    items.sort(key=lambda it: it["at"] or "9999")
    payload = {"count": len(items), "items": items}
    _PENDING["stamp"], _PENDING["payload"] = stamp, payload
    return payload


# The A2 run artifacts. `HERE` is `agent/ui`, so the sandbox is two levels up.
A2_RUNS = Path(bridge.SANDBOX) / "a2" / "runs"


def a2_runs() -> list[dict]:
    """The comparison pages and run files that already exist, newest first.

    Listed from the directory rather than hard-coded: a run is produced by a script, and a list of
    known names would silently omit the next one.
    """
    if not A2_RUNS.is_dir():
        return []
    out = []
    for path in sorted(A2_RUNS.iterdir(), reverse=True):
        if path.suffix.lower() not in (".html", ".jsonl"):
            continue
        out.append({
            "name": path.name,
            "kind": "頁面" if path.suffix.lower() == ".html" else "執行記錄",
            "bytes": path.stat().st_size,
            "url": "/a2?name=%s" % path.name,
        })
    return out


# --- the chat box ------------------------------------------------------------------------
#
# The session lives in a **Node process** (`ui/chat.mjs`), not in this server. That is not a
# preference: a Pi session is a JavaScript object (it holds the conversation, its compaction and its
# model client), and Python cannot own one. So this process keeps exactly one child and speaks
# JSON-per-line to it; the child keeps one session per question.
#
# If the child dies, its sessions die with it. That is stated plainly rather than papered over with a
# restart-and-pretend: a restarted child answers as a stranger, and a conversation that silently
# became a new conversation is worse than one that says so.
class Chat:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None

    def ensure(self) -> subprocess.Popen:
        if self.process is None or self.process.poll() is not None:
            self.process = subprocess.Popen(
                ["node", str(HERE / "chat.mjs")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(CATALOG), text=True, bufsize=1,
                env={**os.environ, "REPAIR_AGENT_STORE": str(STORE_DIR),
                     # 指揮者的腦（owner 2026-10-03）。空字串＝沿用 session.mjs 的預設腦。
                     **({"REPAIR_AGENT_MODEL": CONDUCTOR_MODEL} if CONDUCTOR_MODEL else {})},
            )
        return self.process

    def send(self, message: dict) -> None:
        with self.lock:
            process = self.ensure()
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()

    def stream(self, message: dict):
        """Send one message and yield the child's lines until its turn ends.

        The lock is held for the whole turn, and that is deliberate: two designers typing at once
        would interleave two answers into one stream, and the delta text cannot be told apart. One
        conversation at a time is a limit this UI accepts rather than hides.

        The child is a **single** process for all questions, and it answers with the request's own
        `id` on every line, so an in-flight turn for q042 cannot be mistaken for one about q068.
        """
        with self.lock:
            process = self.ensure()
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield event
                # `done` and `error` are the two ends of a turn; `ready`/`pong` are not answers.
                if event.get("event") in ("done", "error"):
                    return


CHAT = Chat()


def a2_page() -> str:
    """The index of A2 artifacts, generated from the directory."""
    runs = a2_runs()
    if not runs:
        body = "<p>a2/runs 裡還沒有產物。</p>"
    else:
        body = "<ul>" + "".join(
            '<li><a href="%s">%s</a> <span class="k">%s · %s KB</span></li>'
            % (row["url"], row["name"], row["kind"], max(1, row["bytes"] // 1024))
            for row in runs) + "</ul>"
    return (
        "<!doctype html><html lang=\"zh-Hant\"><meta charset=\"utf-8\">"
        "<title>A2 執行產物</title>"
        "<style>body{font:15px/1.7 -apple-system,'PingFang TC',sans-serif;margin:24px 32px;"
        "background:#faf9f7;color:#23201c}h1{font-size:19px}"
        "a{color:#1b5e20}.k{color:#8a8275;font-size:12.5px}"
        "li{margin:6px 0}</style>"
        "<h1>A2 執行產物</h1>"
        "<p>這些是 <code>a2/</code> 產出的模型比對頁與執行記錄。<b>它們是靜態頁，原本只能雙擊開啟</b>"
        "（<code>build_compare_page.py</code> 自己的說明就寫了 <em>opens by double-click with no "
        "server</em>），所以之前在 UI 裡看不到——<b>不是壞掉，是從來沒接上</b>。"
        "這裡照原檔直接服務，不重新渲染：頁面內嵌了它那一次的 run，重畫就會多出第二份版本。</p>"
        + body + '<p><a href="/">← 回判讀介面</a></p></html>'
    )


def chat_turns(key: str) -> list[dict]:
    """The conversation transcript for one question, oldest first.

    The **file** is the source, not the live child's memory: after a crash or a restart the
    transcript is what survived, and showing the child's in-memory view would show a conversation
    that no longer exists anywhere else.
    """
    path = STORE_DIR / "chat.jsonl"
    if not path.is_file():
        return []
    # An empty key is the **unbound** conversation (candidate_key null), not "no conversation".
    # Returning [] for both made the corpus-level transcript invisible after a reload while it was
    # still on disk — the same class of defect as a chat box that forgets a restart.
    wanted = key or None
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (row.get("candidate_key") or None) == wanted:
            # usage 隨列走（owner 2026-10-03 的 token 計量）；舊列沒有就不帶鍵。
            if row.get("usage"):
                out.append({**row, "usage": row["usage"]})
            else:
                out.append(row)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "RepairAgentUI/0.1"

    def log_message(self, fmt, *args):  # noqa: A003 - quieter than the default
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in ("/", "/index.html"):
                self._send(200, _page("index.html"), "text/html; charset=utf-8")
            elif parsed.path == "/figure":
                # 收掉獨立頁（owner 2026-10-02：「我要簡化而不是一直擴張」——主頁本來就有
                # PDF 面板，圖草案併進題目頁中段）。302 只是讓舊書籤不要 404。
                self._redirect("/")
            elif parsed.path == "/api/figure":
                self._json({"count": len(_figure_state()),
                            "keys": sorted(_figure_state())})
            elif parsed.path == "/api/pending":
                self._json(pending_state())
            elif parsed.path == "/api/search":
                self._search(query)
            elif parsed.path == "/api/browse":
                self._browse(query)
            elif parsed.path == "/api/question":
                self._question(query)
            elif parsed.path == "/a2":
                # The A2 comparison pages exist but were never served by anything — they are built
                # as static files that open by double-click (`build_compare_page.py` says so in its
                # own docstring). The designer went looking for them in the UI and found nothing
                # (2026-09-28: 「昨天在 a2/run 看到的多重比較為甚麼沒有顯示出來」), because "not
                # linked anywhere" and "does not exist" look the same from a browser.
                #
                # This serves the file **as built**, with no re-rendering: the page embeds its run,
                # and re-rendering it here would create a second version of a scored record.
                self._a2(query)
            elif parsed.path == "/api/a2":
                self._json({"runs": a2_runs()})
            elif parsed.path == "/api/chat":
                # The conversation so far, read from the append-only transcript. A page reload must
                # not lose the conversation, and the transcript is the record of it either way.
                key = (query.get("key") or [""])[0]
                self._json({"turns": chat_turns(key)})
            elif parsed.path == "/api/queue":
                self._queue(query)
            elif parsed.path == "/api/runs":
                # 業主 2026-10-03：「要有測試去監控指揮與做事模型之間的調度」。指揮者只能「說」
                # 要開做事 run；真正派工（runner.mjs）的進度在這裡變成畫面上可見的狀態。
                self._json(dispatch_status())
            elif parsed.path == "/file":
                self._file(query)
            elif parsed.path == "/crop":
                self._crop(query)
            else:
                self._json({"error": "not found: %s" % parsed.path}, 404)
        except bridge.QuestionNotFound as error:
            self._json({"error": str(error)}, 404)
        except Exception as error:  # noqa: BLE001 - the UI must show why, not die
            self._json({"error": "%s: %s" % (type(error).__name__, error)}, 500)

    def _a2(self, query: dict) -> None:
        """Serve one A2 artifact by name, refusing anything outside `a2/runs`."""
        name = (query.get("name") or [""])[0]
        if not name:
            self._send(200, (a2_page() ).encode("utf-8"), "text/html; charset=utf-8")
            return
        # `name` comes from the URL, so it is resolved and then **checked to be inside** the runs
        # directory. `..%2f` in a query string is otherwise a file read of the whole disk.
        path = (A2_RUNS / name).resolve()
        if not str(path).startswith(str(A2_RUNS.resolve()) + os.sep) or not path.is_file():
            self._json({"error": "no such A2 run: %s" % name}, 404)
            return
        if path.suffix.lower() == ".html":
            self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(200, path.read_bytes(), "application/x-ndjson; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "body is not JSON"}, 400)
            return
        try:
            if parsed.path == "/api/judgement":
                record = {
                    "candidate_key": payload.get("key"),
                    "rating": payload.get("rating") or "up",
                    "reason": payload.get("reason"),
                    "engine": payload.get("engine") or "human:designer",
                    "source": "designer",
                    "audit_scope": "question",
                    "question_number": payload.get("question_number"),
                }
                if record["rating"] not in RATINGS:
                    self._json({"error": "rating must be one of %s" % ", ".join(RATINGS)}, 400)
                    return
                extra = payload.get("extras")
                if extra is not None:
                    # 圖頁的結構化欄位（figure_review／refs／tag），只放窄白名單：鍵短、值短、
                    # 最多 8 個——裁決的主體仍是 rating + reason，這些是可 join 的指針。
                    if not isinstance(extra, dict):
                        self._json({"error": "extras must be an object"}, 400)
                        return
                    record["extras"] = {str(k)[:32]: str(v)[:300]
                                        for k, v in list(extra.items())[:8]}
                self._json({"appended": append_judgement(record), "store": str(STORE)})
            elif parsed.path == "/api/accept-draft":
                self._json(append_accept(payload))
            elif parsed.path == "/api/accept-and-apply":
                self._json(accept_and_apply(payload))
            elif parsed.path == "/api/figure/apply":
                self._json(_apply_figure_drafts(payload.get("keys")))
            elif parsed.path == "/api/text/apply":
                self._json(_apply_text_drafts(payload.get("keys")))
            elif parsed.path == "/api/return-draft":
                self._json(append_return(payload))
            elif parsed.path == "/api/chat/ask":
                self._chat_ask(payload)
            elif parsed.path == "/api/chat/stop":
                CHAT.send({"id": payload.get("id") or "stop", "op": "stop"})
                self._json({"ok": True})
            else:
                self._json({"error": "not found: %s" % parsed.path}, 404)
        except ValueError as error:
            self._json({"error": str(error)}, 400)

    def _chat_ask(self, payload: dict) -> None:
        """Stream one chat turn back as Server-Sent Events.

        SSE rather than one JSON blob because the answer arrives token by token and the designer is
        watching it arrive — a 60-second wait with a blank box is indistinguishable from a hang. The
        same subscription that logs the agent's tool calls is what feeds this, so what streams here
        is what the agent is actually doing, not a progress animation.

        Headers are chosen for a stream that must not be buffered: `no-store` and `X-Accel-Buffering`
        (the station fronts this with a proxy, and a buffered SSE stream arrives all at once at the
        end — which is exactly the blank box again).
        """
        # A missing key is the **unbound** conversation, not an error. The designer asked for both
        # kinds (「3 兩者都要，1先做」); the first is bound to a question, the second starts from the
        # whole corpus. Until this accepted `key: null`, the second kind could not be reached from
        # the browser at all — the server answered 400 and the box looked broken.
        key = payload.get("key") or ""
        message = {
            "id": payload.get("id") or "chat",
            "op": "ask",
            "key": key or None,
            "text": payload.get("text") or "",
            "reset": bool(payload.get("reset")),
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for event in CHAT.stream(message):
                self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
        except Exception as error:  # noqa: BLE001 - the designer must see why the box stopped
            self.wfile.write(("data: " + json.dumps(
                {"event": "error", "error": "%s: %s" % (type(error).__name__, error)},
                ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

    # -- endpoints ---------------------------------------------------------------------------
    def _search(self, query: dict) -> None:
        # `number` must be **None** when absent, not 0: `do_find` filters on
        # `args.number is not None`, so a 0 here silently turns every search into "question number
        # zero", which matches nothing and looks like an empty corpus rather than a bad default.
        raw_number = (query.get("number") or [""])[0]
        args = argparse.Namespace(
            subject=(query.get("subject") or [""])[0],
            number=int(raw_number) if raw_number.strip().isdigit() else None,
            contains=(query.get("contains") or [""])[0],
            with_figures=(query.get("with_figures") or ["0"])[0] in ("1", "true"),
            limit=int((query.get("limit") or ["40"])[0] or 40),
        )
        self._json(bridge.do_find(args))

    def _browse(self, query: dict) -> None:
        """The candidate list. Without this the UI is unusable by anyone but its author.

        Delegated to `bridge.do_browse` so the browser, the agent and the CLI see one list — a
        second query written here would be a second answer to 「哪些題還沒看」.
        """
        args = argparse.Namespace(
            category=(query.get("category") or [""])[0],
            unjudged=(query.get("unjudged") or ["0"])[0] in ("1", "true"),
            with_figures=(query.get("with_figures") or ["0"])[0] in ("1", "true"),
            disputed=(query.get("disputed") or ["0"])[0] in ("1", "true"),
            status=(query.get("status") or [""])[0],
            offset=int((query.get("offset") or ["0"])[0] or 0),
            limit=int((query.get("limit") or ["0"])[0] or 0),
        )
        self._json(bridge.do_browse(args))

    def _question(self, query: dict) -> None:
        key = (query.get("key") or [""])[0]
        if not key:
            self._json({"error": "key is required"}, 400)
            return
        self._json(question_payload(key))

    def _queue(self, query: dict) -> None:
        """A paper's questions in order, with the machine's own signal per question.

        This is what makes grouping possible without inventing a score: `quality_status` and the
        finding population are already on the record (2,858 of 3,471 figure questions were judged
        `OK` by a text-only pass that could not see the picture — see qa-log Q21). The UI shows the
        existing signal and lets the designer decide which group deserves attention.
        """
        key = (query.get("key") or [""])[0]
        question = bridge.load_question(key)
        limit = int((query.get("limit") or ["0"])[0] or 0)
        rows = []
        touched = bridge.human_event_keys()
        for peer in bridge.questions_of_paper(question, limit=limit):
            rows.append({
                "candidate_key": peer.get("candidate_key"),
                "question_number": peer.get("question_number"),
                "quality_status": peer.get("quality_status"),
                "figures": len(peer.get("image_refs") or []),
                "human_touched": (peer.get("candidate_key") or "") in touched,
            })
        self._json({"paper": question.get("candidate_key"), "questions": rows})

    def _file(self, query: dict) -> None:
        """Serve a corpus file (the question PDF) the same way v2 does.

        Delegated to `qbr.review_ui.paths.safe_file_path` rather than reimplemented: it applies the
        allowed-roots check and prefers the browser-safe variant that makes JPEG 2000 scans
        renderable in Chrome. A second path resolver here would miss exactly that fix.
        """
        raw = (query.get("path") or [""])[0]
        resolved = safe_file_path(raw)
        if resolved is None or not resolved.is_file():
            self._json({"error": "file not available: %s" % raw}, 404)
            return
        data = resolved.read_bytes()
        self._send(200, data, content_type_of(resolved.name, data))

    def _crop(self, query: dict) -> None:
        path = (query.get("path") or [""])[0]
        resolved = Path(path).resolve()
        # Confined to the sandbox crop directory: this route serves pictures the agent itself
        # produced, and an unconfined reader here would be a path-traversal in front of the whole
        # repository.
        if not str(resolved).startswith(str(CROPS.resolve())) or not resolved.is_file():
            self._json({"error": "crop not available"}, 404)
            return
        data = resolved.read_bytes()
        self._send(200, data, content_type_of(resolved.name, data))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("判讀介面： http://%s:%d" % (args.host, args.port), file=sys.stderr)
    print("學習語料： %s" % STORE, file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
