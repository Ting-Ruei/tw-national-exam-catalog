# -*- coding: utf-8 -*-
"""人已經確認過的那一種改動，自動套用到同一類的題目上。

業主 2026-09-25 的方向：「改對應該收斂為經驗，然後這些 block 或是反問應該用這些經驗自動去改，
不是定在原地等我每一題下指令。」

這一支就是那一步。它**不重新判斷文字對不對**——判斷已經有人做過了；它做的是：把人**同意過**的那一種
改動形狀，拿去比對其他被阻擋／被反問的題目上、模型自己的判讀所提出的同一種改動，符合就寫成一筆
機器修復（`reset_review`），由人接受或退回。

    python scripts/apply_experience_repairs.py --queue <queue-root>               # 乾跑（預設）
    python scripts/apply_experience_repairs.py --queue <queue-root> --apply
    python scripts/apply_experience_repairs.py --queue <queue-root> --report      # 只印現在的經驗與待辦

三個輸入，各一個出處，都不在這裡重新發明：

1. **經驗**＝`question_review_events.jsonl` 裡「機器改過、人`accept`了」的那些題（`repair_loop.confirmations_by_key`），
   由 `propose_principles.confirmed_changes` 分類（`review_feedback.classify_change`）。
2. **待辦**＝同一份折疊裡 `block` 的題目，加上反問流（`discuss.REPAIR_QUESTIONS_STREAM`）裡還沒被回答的問題。
3. **這一題該怎麼改**＝`question_ai_findings.jsonl` 裡模型自己對這一題的判讀（`changes`：`{field,
   from, to, stored, page}`）。這一支不問模型、不寫句子；它讀的是已經存在的判讀。

經驗的形狀（`form_of`）**可以檢查**，這是它敢自動套用的全部理由：

* `markup_only` —— 只加上／下標標記，**看得到的字元一個都沒變**（`strip_markup(page) ==
  strip_markup(stored)`，且兩者不同）。業主 2026-09-25 同意的 52 題裡有 51 題是這一種
  （`MAOA`→`MAO<sub>A</sub>`、`t1/2`→`t<sub>1/2</sub>`），而它正是「這一類改對的類似題」的形狀。
  這一種**要先有人確認過同一種形狀**（`--min-confirmations`，預設 2 題不同的題目）。

業主 2026-09-25 另外放行了三種**確定性類別**（`DETERMINISTIC_FORMS`）：它們不是「內容決策」而是
「這個字元壞了」，判準在字元自己身上，所以**不必有人先確認過同一種改動**：

* `lost_glyph` —— `stored` 那一邊是 Unicode 私用區字元、紙本判讀那一邊是真字元。站上待辦題裡最大的
  單一模式就是它（`\ue2c6` → `酶` 33 題；全佇列 53 題含 48 個這種字元）。
* `typographic_variant` —— 同一族的排版變體（破折號／引號／間隔號／全形半形標點；`.／．`另有十進位
  守門，見 `_DECIMAL_BETWEEN_DIGITS`）。
* `compatibility_fold` —— Unicode 自己說的同一個字（`⽣` → `生`、`⼗` → `十`、`⽊` → `木`），且兩邊都是
  漢字或部首字形。`⻑` → `長` **不在**這一類：CJK Radicals Supplement 沒有相容分解，所以 NFKC 拿不到
  它—那一種由偵測器自己的表提供，不是這支能自行放行的東西。

每一個都要求**這一欄的整組改動全部落在同一類裡**，而且標記骨架沒被動到：混進一個真正改字的
（`癇`→`癲`、`s`→`S`、`長效型`→`短效型`），整欄就退回「要人看過」那一邊。

形狀不在經驗裡、也不是確定性類別，就不套用——那一題要回到判讀迴圈
（`repair_agent.py`／`confirm_dispute.py`），因為「換掉整個句子」這種改動光是形狀分不出對錯，
只有人看過才能同意。

三道閘門（每一道都有它拒絕的理由，寫在 `subs_for` 裡）：`stored` 必須等於現在列上的那一段文字
（判讀過期就不套用）、欄位必須是題幹／選項／共同題幹（`answer` 不在其中）、同一種改動被同一個人
退過就不重提（`repair_loop` 的拒絕記憶）。

反問：套用之後，這一題在反問流裡還沒被回答的問題會被回答成「已依經驗改成這樣，請你接受或退回」
（署名機器）。`confirm_dispute.py` 只把**人寫的**回答放進提示詞，所以這一句不會冒充審題者的話。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, HERE)

from qbr import canon, cjk, discuss  # noqa: E402
from qbr.dispute_apply import text as dispute_text  # noqa: E402
from qbr.dispute_apply.text import (  # noqa: F401,E402
    DETERMINISTIC_FORMS,
    MARKUP_RE,
    deterministic_form,
)
from qbr.dispute_apply.withdrawals import MAX_ATTEMPTS  # noqa: E402
import apply_dispute_repairs as apply_mod  # noqa: E402
import propose_principles as principles_mod  # noqa: E402
import repair_loop  # noqa: E402

#: The producer's own name, in the two places an event says who wrote it. `repair_` is the prefix the
#: server recognises as a machine repair (`review_ui.constants.REPAIR_REVIEWER_PREFIXES`), which is
#: what keeps this event from ever reading as a person's decision.
REPAIR_REVIEWER = "repair_experience_apply"
SOURCE = "qbr_experience_apply"

#: The fields a repair from experience may touch. `answer`/`answer_payload` are deliberately absent:
#: an answer change is a content decision the person has to make, not a shape a confirmed edit can
#: authorise. The list is the one `apply_dispute_repairs` writes (`stem`, `option X`) plus the shared
#: stem, which a group question's repair also moves.
ALLOWED_FIELDS = ("stem", "shared_stem")

#: 任何標記（骨架比對用）。以前只認 `sub|sup`，所以判讀寫出來的 `<i>斜體</i>` 對這一支是**看不見的**
#: ——整欄的文字看起來一模一樣，形狀判不出來，也就永遠不會被套用。業主 2026-09-25 指出的就是這一類：
#: 紙本把學名排成斜體（`Helvetica-Oblique`），判讀已經自己寫 `<i>`，而收錄的文字是平的。
#: 定義在 `qbr.dispute_apply.text.MARKUP_RE`（唯一的一份，上面已經匯入）。

#: The only form this producer applies, and the reason is measurable rather than stylistic: it is the
#: shape of 51 of the 52 changes a person has confirmed on the station (2026-09-25), and it is
#: checkable without a model - strip the markup and the text must be identical, so the repair cannot
#: change one readable character.
FORM = "markup_only"

#: The **only** population of readings this producer may apply, and it is not this file's own rule:
#: `apply_dispute_repairs.py` - the other, older producer of repairs - reads `population == "dispute"`
#: and nothing else, and `scan_category_principles.py` states its own output is advisory ("整科掃描的
#: 讀法不會被套用那一步拿去改題目文字"). A producer that read every population would be the one place
#: where the whole-subject scan silently rewrites question text, so the gate is spelled here too.
#:
#: Measured (2026-09-25, before this gate existed): the first live run applied 74 repairs, **56 of them
#: authorised by `category-scan` readings** (18 by `dispute`). Those 56 were withdrawn the same day; the
#: count is the reason this constant exists rather than a comment.
POPULATIONS = ("dispute",)

#: **確定性類別**的判準住在 `qbr.dispute_apply.text`（`DETERMINISTIC_FORMS`、`deterministic_form`、
#: `_TYPOGRAPHIC_FAMILIES`）：紙本判讀那一條路（`dispute_apply.page_read`）也要用它——同一欄裡
#: 多引進標記時，那些差異算不算「動到了上下標以外的字元」要跟這裡說的一致，所以它不留在腳本裡。
#: 站上實測的那一段話跟著定義一起搬過去了。
def strip_markup(text) -> str:
    """The readable text: **the platform's** markup removed, everything else as it was.

    用的是 `canon.strip_markup`（平台自己認得的標記表：`sub`／`sup`／`i`／`b`／`u`／`em`…），不是這裡
    自己的一份。兩個理由：平台的標記法只有一份，而**不認得的標記會留在可見文字裡**——那正是我們要的，
    一個引入 `<foo>` 的改動因此不會被判成「看得到的字元不變」。
    """
    return canon.strip_markup(str(text if text is not None else ""))


def readable(text) -> str:
    """看得見的字元，空白收乾淨（比對用）。

    `canon.strip_markup` 把標記**周圍的空白**一起折掉（`GABA<sub>A</sub> 受體` 與 `GABAA受體` 是同
    一份文字，那段空白屬於標記），所以直接比對它會把 `Bacteroides</i> spp.` 少掉的那一格空白算成
    「改了字」——而空白不是內容，這一條在下面 `pairs` 的過濾裡對同一件事已經寫過一次。兩邊都收乾淨
    就不必在兩個地方講同一個規則。
    """
    return re.sub(r"[\s\u3000\u00a0]+", "", canon.strip_markup(str(text if text is not None else "")))


def form_of(stored, page) -> str:
    """The **shape** of one proposed change, or `""` when it is not a shape this producer may apply.

    `markup_only` is the one form a person's confirmations authorise: the page reading differs from the
    stored text by `<sub>`/`<sup>` markers only, so the readable characters are unchanged and the repair
    is a spelling of the paper's own typography rather than a new reading. Anything else - a word
    replaced, an option emptied - is a content decision, and no count of confirmations makes it safe to
    make that decision without a person looking at *that* question.

    The deterministic classes (`DETERMINISTIC_FORMS`) do not need that count, because nothing about them
    is a decision: a private-use character is not text, `-`/`–`/`−` are the same dash written three ways,
    and NFKC says `⽣` and `生` are the same character. They are checked after `markup_only` and they are
    the reason `subs_for` lets a form through without a confirmation.
    """
    before, after = str(stored or ""), str(page or "")
    if not before or not after or before == after:
        return ""
    if readable(before) != readable(after):
        return deterministic_form(before, after)
    # 可見字元相同還不夠：改動必須**真的是標記**。否則一份只差一個空白的判讀也會走到這裡，
    # 而它既不是標記也不是字形，不屬於這一支的任何一種形狀。
    if MARKUP_RE.findall(before) == MARKUP_RE.findall(after):
        return ""
    return FORM


#: 每一種形狀在人讀的那一句裡叫什麼。`markup_only` 站的是「人確認過的先例」，另三種站的是判準本身，
#: 所以字面上要分得出證據是哪一種（第三支指揮者對後者的說法是「機械性字形還原」）。
_FORM_NAMES = {
    "markup_only": "人已同意的同形改動（只加標記——上下標或斜體——看得到的字元不變）",
    "lost_glyph": "還原字型沒有對映到的字元（私用區 → 真字元）",
    "typographic_variant": "同一族的排版變體互換（破折號／引號／間隔號／全形半形，字義不變）",
    "compatibility_fold": "Unicode 說的同一個字（相容字形還原）",
    "mixed": "人已同意的同形改動與可檢查的機械還原",
}


def forms_of(subs: list) -> list:
    """這一組改動用到的形狀（去重、保留順序）。"""
    out: list = []
    for sub in subs:
        form = form_of(sub.get("before"), sub.get("after"))
        if form and form not in out:
            out.append(form)
    return out


#: 類別裡「印出來是空白、看起來像沒改」的字元：控制、格式、私用區，以及還沒指派的碼位。
_INVISIBLE_CATEGORIES = frozenset(("Cc", "Cf", "Co", "Cn"))


def visible(text) -> str:
    """把看不見的字元寫成看得見的碼位（`\ue2c6`），其餘照原樣。

    報告要回答的是「這一筆到底改了什麼」。私用區字元在終端機上印出來是**空白**，所以一筆
    `\ue2c6` → `酶` 的還原在報告裡曾經長成 `A→A`（站上實測，`mannitol 發酵產酸會促進凝固`），
    而人分不出「改了字」與「什麼都沒改」——這一支的報告就是人要據以放行的那一份，所以它必須
    把碼位寫出來。這裡只改報告的顯示，事件與題目文字一律保持原文。
    """
    out = []
    for char in str(text if text is not None else ""):
        if cjk.is_pua(char) or unicodedata.category(char) in _INVISIBLE_CATEGORIES:
            out.append("\\u%04x" % ord(char))
        else:
            out.append(char)
    return "".join(out)


def lead_for(subs: list) -> str:
    """`subs` 說明的開頭（事件備註與回覆反問都用它）。

    一組裡有兩種形狀時用最保守的說法：人讀的那一句不能比事實寬。字面刻意依形狀分開——「看得到的
    字元不變」對私用區還原不成立，那時看得到的字元正是被換掉的那一個。
    """
    forms = forms_of(subs)
    name = _FORM_NAMES.get(forms[0], _FORM_NAMES["mixed"]) if len(forms) == 1 else _FORM_NAMES["mixed"]
    return "依%s套用：" % name


def experience(queue_dir: str) -> dict:
    """What people have confirmed, grouped by the **form** of the change.

    `{form: {"count": n, "keys": [...], "examples": [(before, after), …]}}`, counted per question (the
    unit a person clicks) and only for changes whose form is one this producer can check. The
    examples travel so the confirmation the repair stands on can be opened and read.
    """
    rows = principles_mod.load_rows(queue_dir)
    out: dict = collections.defaultdict(lambda: {"count": 0, "keys": [], "examples": []})
    for confirmed in principles_mod.confirmed_changes(queue_dir, rows):
        for change in confirmed.get("changes") or []:
            form = form_of(change.get("from"), change.get("to"))
            if not form:
                continue
            bucket = out[form]
            if confirmed["candidate_key"] not in bucket["keys"]:
                bucket["keys"].append(confirmed["candidate_key"])
                bucket["count"] = len(bucket["keys"])
            if len(bucket["examples"]) < 8:
                bucket["examples"].append((str(change.get("from") or ""),
                                           str(change.get("to") or "")))
    return dict(out)


def pending_keys(queue_dir: str, folded: dict) -> dict:
    """`{key: why}` for the questions this producer may work on: blocked, or asked and unanswered.

    Both come from state that already exists - the fold of the review stream and the projection of the
    repair-question stream - rather than from a third list of "work to do", because a second list is a
    second thing that can disagree with what the person sees on screen.
    """
    out = {}
    for key, row in folded.items():
        if row.get("action") in repair_loop.BLOCKING_ACTIONS:
            out[key] = "人阻擋"
    path = os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM)
    if os.path.exists(path):
        projection = discuss.repair_questions_projection(discuss.load_events(path))
        for row in projection.get("questions") or []:
            key = row.get("candidate_key")
            if key and row.get("open"):
                out.setdefault(key, "反問尚未回答")
    return out


def open_asks(queue_dir: str, key: str) -> list:
    """The unanswered `ask` events on one question, newest first (a key can have more than one: each
    reading of the paper opens its own question, and only the current reading's is worth answering).
    """
    path = os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM)
    if not os.path.exists(path):
        return []
    projection = discuss.repair_questions_projection(discuss.load_events(path))
    return [row for row in projection.get("questions") or []
            if row.get("candidate_key") == key and row.get("open")]


def latest_findings(path: str, populations=None) -> dict:
    """`{candidate_key: record}` — the most recent finding per question that carries `changes`.

    The latest one wins for the same reason `repair_loop` says "the latest decision wins": a question
    repaired once has a later reading, and re-applying an older reading would move the text backwards.
    Records without `changes` are skipped: the proposal has to come from an actual reading of this
    question's paper, and "no diff" is not a proposal.

    `populations` narrows **which pass** may authorise a repair (see `POPULATIONS`). It is a parameter
    rather than a filter at the call site so that "the latest reading from a pass this producer may act
    on" is one lookup: a question read by both the whole-subject scan and the dispute pass must be
    repaired from the dispute reading, not skipped because the scan read it last.
    """
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            key = record.get("candidate_key")
            if not key or not record.get("changes"):
                continue
            if populations is not None and str(record.get("population") or "") not in populations:
                continue
            out[key] = record
    return out


def append_review_event(path: str, event: dict) -> None:
    """Append one review event, in the shape the other producer writes it (`sort_keys`, no ASCII
    escapes). One writer for this file, because two spellings of the same line is how a diff between
    two rounds becomes unreadable.
    """
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def reading_populations(path: str) -> dict:
    """`{(candidate_key, created_at): population}` — which pass wrote a reading.

    The key is the pair, not the candidate key, because a question can be read more than once (the
    whole-subject scan and the dispute pass both read it), and a repair's own event records **which**
    reading authorised it (`experience.reading`). Withdrawal has to ask about that exact reading.
    """
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            key = record.get("candidate_key")
            if key:
                out[(key, record.get("created_at"))] = record.get("population")
    return out


def standing_repairs(events_path: str) -> dict:
    """`{key: event}` — this producer's repairs that are still standing.

    "Still standing" is the last event this producer wrote about the key, and it is not a withdrawal.
    A key can carry a repair, a withdrawal and a later repair; only the last one describes what is on
    the question now.
    """
    latest = {}
    for event in repair_loop.iter_events(events_path):
        if str(event.get("reviewer") or "") != REPAIR_REVIEWER:
            continue
        key = event.get("candidate_key")
        if key:
            latest[key] = event
    return {key: event for key, event in latest.items()
            if str(event.get("applied") or "").strip().lower() != repair_loop.WITHDRAWN}


def reopen_asks(queue_dir: str, keys: set, why: str, now: str) -> int:
    """Put the questions back in the agent's open list when this producer's answer is withdrawn with it.

    A machine answer ("I applied the fix; accept or refuse") stops being true the moment the fix is
    taken back, and leaving it standing would leave the question looking answered in the discussion
    area - the one place a person goes to see what is still waiting on them. Re-opening writes another
    `ask` with the same `question_id` (the projection lets a later `ask` supersede an earlier answer),
    so the question is open again and says why.
    """
    path = os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM)
    if not os.path.exists(path):
        return 0
    latest = {}
    for event in discuss.load_events(path):
        question_id = str(event.get("question_id") or "")
        if question_id:
            latest[question_id] = event
    reopened = 0
    for question_id, event in sorted(latest.items()):
        if str(event.get("action") or "") != "answer":
            continue
        if str(event.get("reviewer") or "") != REPAIR_REVIEWER:
            continue
        if event.get("candidate_key") not in keys:
            continue
        discuss.append_event(path, {
            "action": "ask", "question_id": question_id,
            "candidate_key": event.get("candidate_key"),
            "question": "這一題自動套用的修正已撤回（%s）。這一題仍需要你的判斷，或下一輪重讀。" % why,
            "reason": "自動套用已撤回", "reviewer": REPAIR_REVIEWER, "source": SOURCE,
            "created_at": now,
        })
        reopened += 1
    return reopened


def withdraw_repairs(queue_dir: str, population: str, *, apply: bool, now: str) -> int:
    """Take back this producer's repairs whose authority came from `population`.

    The general shape of the mistake this exists for: a producer is allowed to act on one class of
    input, acts on a wider one, and the wrong repairs are in the log before anyone notices. Withdrawal
    is the machine's own way back (`apply_text_corrections.py` restores the field from `parser_original`
    on the next landing pass), and it is a revision like any other: the person's screen and the file
    both follow the last line.
    """
    events_path = os.path.join(queue_dir, "question_review_events.jsonl")
    populations = reading_populations(os.path.join(queue_dir, "question_ai_findings.jsonl"))
    planned = []
    for key, event in sorted(standing_repairs(events_path).items()):
        reading = (event.get("experience") or {}).get("reading")
        if populations.get((key, reading)) != population:
            continue
        fields = [str(change.get("field") or "") for change in event.get("changes") or []]
        fields = [field for field in fields if field]
        if fields:
            planned.append((key, fields, event))
    print("授權這一支的判讀來自 %s 而仍站著的自動套用：%d 題" % (population, len(planned)))
    for key, fields, _event in planned[:10]:
        print("  %-46s %s" % (key[-36:], "、".join(fields)))
    if not apply:
        print()
        print("（乾跑：沒有寫任何東西。要真的撤回請加 --apply。）")
        return 0
    why = "授權這一筆自動套用的判讀來自 %s：那個人口是 advisory（只寫 finding、不改題目文字），所以撤回" % population
    for key, fields, _event in planned:
        append_review_event(events_path, apply_mod.build_withdrawal_event(
            key, fields, why, REPAIR_REVIEWER, now))
    reopened = reopen_asks(queue_dir, {key for key, _fields, _event in planned}, why, now)
    print()
    print("撤回了 %d 題（署名 %s）；下一次落地會把欄位還原成 parser_original。" % (len(planned), REPAIR_REVIEWER))
    if reopened:
        print("把 %d 則被這一支回答過的反問重新打開（它回答的那一筆修正已經不在了）。" % reopened)
    return 0


def field_value(row: dict, field: str) -> str:
    return apply_mod.field_text(row, field)


def subs_for(row: dict, record: dict, *, refused: list, already_applied: list, forms: dict) -> tuple:
    """`(subs, complaints)` for one question: the changes this producer may apply, and why not.

    Every complaint is a fact about *this* question, so a dry run says the same thing an `--apply` run
    would do and a reader can check it. The gates, each with the mistake it prevents:

    * a reading that failed (`error`) proposes nothing, and a question with no reading is left to the
      loop that reads papers (`repair_agent.py`) rather than guessed at from the stored text;
    * the change's `stored` must equal the text on the row **right now** - a reading made before
      another repair landed would otherwise overwrite that repair with an older reading;
    * the field must be one a typography repair may touch (never the answer);
    * the form must be confirmed (`markup_only`) and confirmed often enough (`--min-confirmations`);
    * the same `(field, before, after)` must not be one this person already refused on this question -
      that is the loop's own memory, and re-proposing it is the treadmill the owner reported;
    * an identical change the event fold says is still standing is not written twice (the loop runs on
      a timer).
    """
    complaints = []
    changes = [change for change in (record.get("changes") or []) if isinstance(change, dict)]
    if not changes:
        return [], ["這一題的判讀沒有提出任何改動"]
    if record.get("error"):
        return [], ["紙本讀不到（%s）" % record["error"]]
    population = str(record.get("population") or "")
    if population not in POPULATIONS:
        return [], ["判讀來自 %s（只有 %s 的判讀可以改題目文字）"
                    % (population or "（沒有記人口）", "、".join(POPULATIONS))]
    refused_changes = {(str(change.get("field") or ""), str(change.get("from") or ""),
                         str(change.get("to") or "")) for change in refused or []}
    standing_changes = {(str(change.get("field") or ""), str(change.get("from") or ""),
                          str(change.get("to") or "")) for change in already_applied or []}
    # 同一欄的同一**種**改動被退過，就不再自動送一次——即使這一筆的字面不同。業主 2026-09-24
    # 的實測：「剛才上下標亂改的我全部都 block」——那一批就是這一種形狀，而它們逐字不同，
    # 所以字面比對攔不住第二輪。被退過的題目要**再進入掃描的迴圈**（`confirm_dispute.py` 的新判讀
    # 帶著 `rejected_note` 的逐欄 from/to），不是把同一種改動再送一次。
    refused_forms = {(str(change.get("field") or ""), form_of(change.get("from"), change.get("to")))
                     for change in refused or []}
    subs = []
    for change in changes:
        field = str(change.get("field") or "")
        stored = str(change.get("stored") or "")
        page = str(change.get("page") or "")
        if not (field == "stem" or field == "shared_stem" or field.startswith("option ")):
            complaints.append("%s：不是這一支可以動的欄位" % (field or "（沒有欄位）"))
            continue
        if (field, stored, page) in refused_changes:
            complaints.append("%s：人退過這一筆改動，不重提" % field)
            continue
        if (field, stored, page) in standing_changes:
            complaints.append("%s：相同的機器改動仍然生效，不重複寫入" % field)
            continue
        if (field, FORM) in refused_forms or (field, form_of(stored, page)) in refused_forms:
            complaints.append("%s：人退過這一欄的同一種改動，回判讀迴圈" % field)
            continue
        if stored != field_value(row, field):
            complaints.append("%s：判讀的那一段與現在的文字不符（過期）" % field)
            continue
        form = form_of(stored, page)
        if not form:
            complaints.append("%s：不是可檢查的形狀（改到了字，或同一欄混了兩種改動）" % field)
            continue
        if form not in forms and form not in DETERMINISTIC_FORMS:
            complaints.append("%s：形狀 %s 還沒有被確認過" % (field, form))
            continue
        subs.append({"field": field, "before": stored, "after": page, "replace": True})
    return subs, complaints


def build_event(row: dict, subs: list, record: dict, previous: dict, forms: dict,
                created_at: str) -> dict:
    """One machine `reset_review`, in the shared shape, saying where the change came from.

    `previous_*` travels so the person sees what they had decided before the text moved under them,
    and the `experience` block names the confirmations this repair stands on - a repair from
    experience is only reviewable if the reviewer can read the precedent it was generalised from.
    """
    correction = apply_mod.build_correction(row, subs)
    key = str(row.get("candidate_key") or "")
    forms_seen = forms_of(subs)
    event = apply_mod.build_repair_event(key, subs, correction, previous,
                                         reviewer=REPAIR_REVIEWER, created_at=created_at,
                                         crop=record.get("crop"), applied="field",
                                         lead=lead_for(subs), source=SOURCE)
    # 這一塊是複核的人用來分辨「這筆改動站的是什麼證據」的：`markup_only` 站的是**人被確認過的
    # 先例**（所以列得出那幾題），另三種站的是**判準本身**（所以確認題數是 0，理由寫在 why）。
    if forms_seen == [FORM]:
        event["experience"] = {
            "form": FORM,
            "confirmed_questions": int(forms.get(FORM, {}).get("count") or 0),
            "evidence": list(forms.get(FORM, {}).get("keys") or [])[:8],
            "reading": record.get("created_at"),
        }
    else:
        event["experience"] = {
            "form": forms_seen[0] if len(forms_seen) == 1 else "mixed",
            "forms": forms_seen,
            "confirmed_questions": 0,
            "evidence": [],
            "why": "可檢查的機械還原（%s），不需要人確認過同一種改動" % "、".join(forms_seen),
            "reading": record.get("created_at"),
        }
    return event


def answer_text(subs: list) -> str:
    """What the machine says when it answers the agent's own question about this question.

    Signed as the machine (`REPAIR_REVIEWER`) and phrased as what it *did*, not as a judgement: the
    remaining decision is the person's, and it is one button on the question itself. The opening is
    `lead_for`, so the sentence names the same evidence the event does — a reply to an open question
    must not describe the change more loosely than the event the person will read next to it.
    """
    parts = "；".join("%s：%s→%s" % (sub["field"], sub["before"][:24], sub["after"][:24])
                      for sub in subs)[:300]
    return ("已" + lead_for(subs) + parts
            + "。這一題的文字已經改成這樣，請你在題目區接受或退回。")


def queue_dir_of(queue: str) -> str:
    return repair_loop.review_ui_dir(queue)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", required=True, help="queue root (or its review-ui subdirectory)")
    parser.add_argument("--apply", action="store_true",
                        help="append the repair events and answer the open asks; without it nothing "
                             "is written (dry run is the default)")
    parser.add_argument("--report", action="store_true",
                        help="print the confirmed experience, the pending questions and the matches, "
                             "and change nothing")
    parser.add_argument("--min-confirmations", type=int, default=2,
                        help="how many different questions must confirm a form before it is applied "
                             "(default 2: one click can be a mistake, two cannot be the same mistake)")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many questions (0 = no limit)")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                        help="stop repairing a question once this many of its machine repairs have "
                             "been rejected and nobody has accepted since (default %d; 0 = no cap). "
                             "與 `apply_dispute_repairs.py` 同一個上限、同一個定義"
                             "（`withdrawals.MAX_ATTEMPTS`）。" % MAX_ATTEMPTS)
    parser.add_argument("--show", type=int, default=10, help="how many matches to print")
    parser.add_argument("--withdraw-population", metavar="NAME",
                        help="take back this producer's repairs whose authorising reading came from "
                             "NAME (e.g. category-scan: that pass is advisory and must not rewrite "
                             "question text); needs --apply to write")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_dir = queue_dir_of(args.queue)
    events_path = os.path.join(queue_dir, "question_review_events.jsonl")
    if args.withdraw_population:
        return withdraw_repairs(queue_dir, args.withdraw_population, apply=args.apply,
                                now=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    folded = repair_loop.fold_review_events(events_path)
    forms = experience(queue_dir)
    confirmations = {form: bucket["count"] for form, bucket in sorted(forms.items())}
    print("經驗：%s" % (confirmations or "（沒有任何被確認過的同形改動）"))
    if FORM in forms:
        for before, after in forms[FORM]["examples"][:3]:
            print("  例：%s → %s" % (before[:40], after[:40]))
    trusted = {form for form, bucket in forms.items() if bucket["count"] >= args.min_confirmations}
    # 確定性類別不需要確認，所以它們一定會被套用；把它們列進這一行的集合，是為了讓報告上「可套用的
    # 形狀」等於等一下真的會套用的那一組，而不是只列出「靠人確認門檻過關」的那一組。
    trusted |= set(DETERMINISTIC_FORMS)
    print("可套用的形狀：%s（確認門檻 %d 題；確定性類別 %s 不需確認）"
          % (sorted(trusted) or "無", args.min_confirmations, "、".join(DETERMINISTIC_FORMS)))

    pending = pending_keys(queue_dir, folded)
    rows = principles_mod.load_rows(queue_dir)
    all_findings = latest_findings(os.path.join(queue_dir, "question_ai_findings.jsonl"))
    findings = latest_findings(os.path.join(queue_dir, "question_ai_findings.jsonl"), POPULATIONS)
    print("待辦：%d 題（%s）；其中有判讀的 %d 題，其中判讀可以改題目的 %d 題"
          % (len(pending), "、".join(sorted(set(pending.values()))),
             sum(1 for key in pending if key in all_findings),
             sum(1 for key in pending if key in findings)))
    print()

    matched = []
    exhausted = 0
    for key in sorted(pending):
        record = findings.get(key)
        row = rows.get(key)
        if not row:
            continue
        if args.max_attempts and int(folded.get(key, {}).get("rejections") or 0) >= args.max_attempts:
            # 迴圈上限（業主 2026-09-25）：這一題已經被退滿三次而沒有人接受過，機器不再改它。
            # 次數用的是同一份折疊的 `rejections`（與 ③ 的 `rejection_counts` 同一個定義）。
            exhausted += 1
            continue
        if not record:
            # 這一題有判讀、但來自不改題目文字的人口（整科掃描），或根本沒有判讀。兩種都不套用，
            # 但理由不同——報告要把兩種分開寫，因為「沒有判讀」是迴圈還沒讀到，「掃描的判讀」是
            # 讀了也不該動手。
            other = all_findings.get(key)
            if args.report:
                print("  %-46s %s" % (key[-28:],
                                      "判讀來自 %s（只有 %s 的判讀可以改題目文字）"
                                      % (str(other.get("population") or "（沒有記人口）"),
                                         "、".join(POPULATIONS)) if other else "沒有判讀"))
            continue
        folded_row = folded.get(key, {})
        already_applied = ((folded_row.get("machine_applied") or [])
                           if folded_row.get("repair_standing") else [])
        subs, complaints = subs_for(
            row, record,
            refused=folded_row.get("refused_changes") or [],
            already_applied=already_applied,
            forms=trusted,
        )
        if subs:
            matched.append({"candidate_key": key, "question_number": row.get("question_number"),
                            "subs": subs, "record": record, "why": pending[key],
                            "complaints": complaints})
        elif args.report:
            print("  %-46s %s" % (key[-28:], "；".join(complaints[:2])))

    if exhausted:
        print("已退滿上限而不再修：%d 題（人接受或放回會重新開門）" % exhausted)
    print("可以自動套用的：%d 題" % len(matched))
    if matched:
        tally = collections.Counter(form for item in matched for form in forms_of(item["subs"]))
        print("  形狀分布：%s" % "、".join("%s %d 題" % (form, count)
                                          for form, count in sorted(tally.items())))
    for item in matched[:max(0, args.show)]:
        print("  %-46s q%-4s %s" % (item["candidate_key"][-28:], item["question_number"],
                                    "；".join("%s：%s→%s" % (sub["field"],
                                                            visible(sub["before"])[:18],
                                                            visible(sub["after"])[:18])
                                              for sub in item["subs"])[:110]))
    if len(matched) > args.show:
        print("  …（其餘 %d 題）" % (len(matched) - args.show))
    if not args.apply:
        print()
        print("（乾跑：沒有寫任何東西。要真的套用請加 --apply。）")
        return 0

    if args.limit:
        matched = matched[:args.limit]
    # **寫入前重驗**：人可能在量測與這一行之間決定了某一題，而一筆蓋在他後面的修復會把那個決定
    # 悄悄換掉。`apply_dispute_repairs.py` 用的是同一道閘門（重讀事件流，不信任剛才的快照），
    # 這裡比的是「這一題最新的決定還是量測時那一筆」。
    fresh = repair_loop.fold_review_events(events_path)
    refused_now = 0
    for item in matched:
        key = item["candidate_key"]
        before = (folded.get(key, {}).get("event") or {}).get("created_at")
        after = (fresh.get(key, {}).get("event") or {}).get("created_at")
        if before != after:
            item["stale"] = "%s → %s" % (before, after)
            refused_now += 1
    matched = [item for item in matched if not item.get("stale")]
    if refused_now:
        print("寫入前重驗：%d 題的決定在量測後變了，不動它們。" % refused_now)
    created_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    written = answered = 0
    events = []
    for item in matched:
        key = item["candidate_key"]
        row = rows[key]
        previous = dict(folded.get(key, {}).get("event") or {})
        events.append(build_event(row, item["subs"], item["record"], previous, forms, created_at))
    # 先備份，因為這個檔案是審題者的歷史：一次錯的 append 必須是一個可以放回去的檔案。
    backup = events_path + ".before-" + created_at.replace(":", "")
    shutil.copyfile(events_path, backup)
    for event in events:
        append_review_event(events_path, event)
        written += 1
    for item in matched:
        for ask in open_asks(queue_dir, item["candidate_key"]):
            discuss.append_event(os.path.join(queue_dir, discuss.REPAIR_QUESTIONS_STREAM), {
                "action": "answer", "question_id": ask["question_id"],
                "candidate_key": item["candidate_key"],
                "answer": answer_text(item["subs"]), "reviewer": REPAIR_REVIEWER,
                "source": SOURCE, "created_at": created_at,
            })
            answered += 1
    print()
    print("備份 %s" % backup)
    print("寫了 %d 筆機器修復（署名 %s）；回答了 %d 則反問。" % (written, REPAIR_REVIEWER, answered))
    print("每一題都還在你的手上：接受、退回，或什麼都不做——這一支不寫任何決定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
