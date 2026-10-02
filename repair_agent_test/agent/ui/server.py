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
    view["sandbox_accepts"] = sandbox_accepts_for(key)
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


def figure_drafts() -> dict:
    import figure_missing_second_pass as figure_producer  # qbr/scripts 已在 sys.path

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

    drafts = []
    for key, by in rounds.items():
        hit = wo_rows.get(key) or {}
        crops = ["review-ui/crops/%s/%s" % (hit.get("run", ""), fn)
                 for fn in (by.get("A", {}).get("crop_files") or [])]
        a, b = view(by.get("A", {})), view(by.get("B", {}))
        agreed = None
        if by.get("A") and by.get("B"):
            agreed = (a["decision"], sorted(a["refs"])) == (b["decision"], sorted(b["refs"]))
        # 整題**不在**這份 payload 裡（owner 2026-10-02:「沒有看到整題，無法判斷對錯」——但要
        # 的是卡上看得到整題，不是把 634 題塞進一次回應：634 × 題幹＋選項＝數 MB、冷建 15 s）。
        # 卡片各自向既有 `/api/question` 懶載入（每題 ~0.1 s，隨渲染補上），前端 questionPane。
        drafts.append({"key": key, "a": a, "b": b, "agreed": agreed,
                       "crops": crops, "ruling": said.get(key, "")})
    drafts.sort(key=lambda d: d["key"])
    return {"count": len(drafts), "drafts": drafts}


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
                env={**os.environ, "REPAIR_AGENT_STORE": str(STORE_DIR)},
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in ("/", "/index.html"):
                self._send(200, _page("index.html"), "text/html; charset=utf-8")
            elif parsed.path == "/figure":
                self._send(200, _page("figure.html"), "text/html; charset=utf-8")
            elif parsed.path == "/api/figure":
                self._json(figure_drafts())
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
