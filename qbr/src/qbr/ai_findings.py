# -*- coding: utf-8 -*-
"""What the model said was wrong with a question, and how it said to fix it.

Why this is not the triage that was already rejected
----------------------------------------------------
Task 21 asked a model to *find* which questions were broken, and it measured 12-16% precision -
one useful question in 6.3. That finding stands, and this module does not revisit it. The question
here is a different one, asked after a **person** has already said a question is wrong: *what does
the model think is wrong, and how would it be fixed?*

The difference is what makes the record worth keeping. A triage list is a guess about the future; a
finding is a note about a case that is already known to be bad. Even when the note is wrong it is
evidence of what was believed at the time, and the cases that need it most are the ones a person
cannot classify - "國考題的意外", the one-off accidents where the defect is real and the shape of it
is strange.

Why the record is append-only
-----------------------------
The same reason the human review log is. The value of the note is that it says what the model
answered *on the reading that was in front of it*, and a record that can be edited later cannot be
trusted to say that. `append` is the only writer; there is deliberately no `rewrite`, no `update`
and no `save`, so the module cannot express a change to a finding - the shape of the code is the
guarantee.

Nothing here decides anything (`GOV-05`)
---------------------------------------
A finding is advisory. It never writes a review event, never edits a question, never becomes a
rule. It is a note a person reads afterwards. The rule this project keeps relearning is that a
model's answer is worth having only when it is recorded next to the evidence, named with the model
that gave it, and checkable by whoever reads it next - which is why the record carries the exact
text the model was shown rather than a summary of it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from . import canon
from . import discuss

#: Bumped when the record's shape changes, so an old log stays readable as an old log rather than
#: being silently reinterpreted under a new schema.
SCHEMA = "qbr_ai_finding_v0.1"

#: The vocabulary of what can be wrong. Single-sourced here rather than written again in the
#: asking script, because two copies of a code list is two places for a code to mean two things,
#: and a code the reader cannot act on is worse than no code.
#:
#: `ANSWER_DISAGREES` was here and was **removed**, not forgotten. It asked the model to judge
#: whether the paper's answer was right, which is not something this pipeline can give it evidence
#: for: the model sees the extracted text and nothing else - no paper, no corrections sheet, no
#: answer key - so the only way to answer is its own subject memory, which is the "推演語意" the
#: charter forbids. Measured against the 2,502 questions a person had already judged, findings
#: carrying this code agreed with the person **8%** of the time (3 of 37), while findings about
#: shapes visible in the text agreed **54%** of the time. The wrong answers were structural, not
#: random: of 1,285 `ANSWER_DISAGREES` findings, 39 were voided questions whose stored `A、B、C、D`
#: means "everyone scores" and 350 were `B或C` corrections meaning "either counts" - the model
#: correctly reasoned a single-choice question cannot have several answers and blamed the paper for
#: what the prompt had failed to explain. Answer correctness already has its own road (the answer
#: review area); keeping the code here only invites the model to invent.
#:
#: A real extraction defect near the answer - an option truncated so the answer letter has nothing
#: to point at - is still reportable, under `OPTION_TRUNCATED` / `MISSING_OPTION`. What is gone is
#: the code whose only meaning was "I, the model, disagree with the answer key".
CODES = {
    "SUPERSCRIPT_FLATTENED": "上下標被壓平成普通字元，公式或化學式因此讀不出層次（如 PO4 3- 應為 PO₄³⁻）",
    "OPTION_TRUNCATED": "選項被截斷，看起來只有半截（如 [Na、[K 這種沒有結尾的括號）",
    "SPACE_INSIDE_WORD": "中文詞中間多了不該有的空格（中文書寫不用空格斷詞）",
    "OPTION_MERGED_INTO_STEM": "選項內容跑進題幹，題幹結尾出現公式或選項編號",
    "MISSING_OPTION": "選項數不足四個，或選項編號跳號",
    "STEM_TRUNCATED": "題幹在句中斷掉，語意不完整",
    "GLYPH_DAMAGE": "出現不該出現的字元（亂碼、罕用異體字、簡體字、別國字母）",
    "DROP_OUT": "紙本有內容，抽取結果裡沒有（掉字、掉符號、掉整行），且位置可以指出來",
    "FIGURE_MISSING": "題目要讀者看的圖不在這一題身上（圖沒被抽出來，或跨頁續接）",
    "NONE": "沒有發現異常",
}

#: Verdicts. `NOT_EXTRACTION` is first-class and is not a failure: some blocked questions are
#: disputes about the paper's own content, and saying so is more useful than inventing a defect.
#: This was measured - the last 7 unexplained blocks were pharmacokinetic calculations whose
#: extraction is correct while the PDF text layer is the truncated thing.
VERDICTS = ("DEFECT", "NOT_EXTRACTION", "OK")

#: How the question arrived in front of the model. This is **part of the measurement**, not decoration.
#:
#: The prompt was written for the loop, where every question really had been blocked by a person, so
#: it said so. Then `--all` sent the whole corpus through the same prompt and that sentence became a
#: **lie**: questions nobody had judged were described as "a human already flagged this". That is
#: worse than inaccurate, it is leading - it asserts a defect exists and asks for it to be named,
#: which is precisely how a corpus-wide pass manufactures findings, 79,090 questions at a time.
#:
#: A question that arrives because a person rejected it and one that arrives because it is next in the
#: file are different questions, and the answer means something different in each case. The prompt
#: has to say which one this is, and the version hash has to change when it does.
POPULATIONS = {
    # A question a person rejected.
    #
    # This used to say "請說出你認為哪裡錯了、以及該怎麼修" - *tell us where you think it is
    # wrong* - which is the corpus question wearing a badge. The fact that a person flagged the
    # question carries **no location** (the reviewer deliberately does not have to write a reason;
    # annotation is a separate feature), so the model is handed exactly the evidence the person had
    # and told to re-decide it. That is the open, unverifiable question this project has measured as
    # unreliable, restated. What the flag *can* do is ask for a reading: name what is visible in the
    # text and say plainly when the problem is not extraction at all.
    # **This framing is only true when the reviewer wrote nothing.** A 註解 is the reviewer saying
    # where the problem is, and this text says he did not - so it must not be used on an annotated
    # question, or the prompt lies in the opposite direction from the `--all` defect below (there,
    # nobody had judged and it said a person had; here, a person has spoken and it says he has not).
    # `population_framing` is what decides between this and `BLOCKED_WITH_NOTE`, in one place.
    "blocked": {
        "arrival": "使用者給你一題「已經被人類標記為有問題」的題目文字。"
                   "人類標記時並沒有說錯在哪裡，所以「被標記」這件事本身沒有告訴你任何位置。",
        "ask": "這一題已經被人類標記為有問題，但**沒有說錯在哪**——你手上只有題目文字，"
               "和人類看到的一樣。請**不要重新猜一次這題對不對**。你只要做一件事："
               "指出你在文字層**看得見**的具體異常（哪個字、哪個位置、什麼符號）。"
               "若你認為問題不在抽取結果，而在紙本或題目本身（答案爭議、出題錯誤），"
               "verdict 用 NOT_EXTRACTION 並說明理由——這比硬找一個缺陷有價值。",
    },
    "corpus": {
        "arrival": "使用者依序給你題庫裡的題目，請你檢查抽取結果有沒有問題。",
        "ask": "這一題**沒有**被人類標記過——它是整份題庫依序送來的其中一題。"
               "請自己判斷抽取結果是否正確。**沒有發現異常是正常的答案**：這種情況 verdict 用 OK，"
               "`what` 用 NONE，不要為了有東西交而把正常的題目說成缺陷。",
    },
    # A question that carries a dispute and that a person asked to be checked against the page.
    #
    # Different from both above, and the difference is what the model can see: here it is given a
    # **crop of the paper** as well as the extracted text, so the question changes from "is this text
    # wrong" to "does this text match that picture". That is a closed question with a checkable
    # answer, which is the only use of a model this project has measured as reliable (Task 21).
    # Calling it `blocked` would be a lie - the person flagged a dispute, not necessarily the
    # question - and calling it `corpus` would hide that a crop is attached.
    "dispute": {
        "arrival": "使用者給你一題「已經被系統標為有爭議」的題目，並且附上紙本同一題的截圖。",
        "ask": "上面是抽取結果，下面（或同一個訊息裡）的圖片是**紙本同一題的截圖**。"
               "請只回答：「抽取的文字與圖片不一致的地方在哪裡、該把哪個字改成哪個字」。"
               "看不清楚就寫 confidence 低，不要猜。圖片與文字一致時 verdict 用 OK、`what` 用 NONE。",
    },
    # 一個考別的**整科掃描**（`scripts/scan_category_principles.py`，2026-09-24 owner 的要求：
    # 「新整理出來的總規則要重新審視藥師、藥師(二)的所有題目」）。
    #
    # 與 `dispute` 一樣附紙本截圖，因為要問的仍是那個封閉問題（「這段文字與那張圖一致嗎」）；但
    # 開場白不能沿用 `dispute` 的那一句——**這一題沒有人標記、也沒有偵測器報過爭議**，它是因為
    # 所屬考別被整科掃描而來的。說「已經被系統標為有爭議」正是 `POPULATIONS` 記著的那種錯（那時是
    # 把沒人判過的題說成人標記過），而同一句話對模型的作用是把它推向「去找出一個缺陷」。
    #
    # 這一條也因此有自己的版本號：`prompt_version` 把 population 一起雜湊，所以整科掃描的紀錄與
    # 爭議確認的紀錄不會被當成同一次量測。寫入端的 `population` 標籤是 `category-scan`，
    # `apply_dispute_repairs.py` 只取 `dispute`，所以掃描的讀法不會被套用成修復。
    "category-scan": {
        "arrival": "使用者依考別送來一整科裡的某一題（這一題**沒有**被人標記過，也**沒有**被偵測器"
                   "標為爭議），並附上紙本同一題的截圖。",
        "ask": "上面是抽取結果，圖片是**紙本同一題的截圖**。這一題是依考別整科掃描送來的，"
               "不是因為有人標記或系統報了爭議——請不要假設它一定有問題。"
               "請只回答：「抽取的文字與圖片不一致的地方在哪裡、該把哪個字改成哪個字」。"
               "看不清楚就寫 confidence 低，不要猜。圖片與文字一致時 verdict 用 OK、`what` 用 NONE。",
    },
}

# --- the spelling of a reading: one copy, used by every transcription prompt -------------------
# The two blocks below are the **only** copies of these rules in the repository. They are written
# here, next to the rest of the prompt text this module owns (`principles_note`, `answers_note`,
# `notes_note` are the same idea for the human channels), because a rule that is pasted into two
# prompts is two rules - and this project has paid for that shape more than once: the two copies
# are free to drift and nothing in the record can tell which one a reading was taken under.
#
# Why markup rather than the Unicode characters, since the old rule 3 asked for the opposite:
#
#   Unicode has no subscript capital A and no subscript M, so "write the subscript with Unicode"
#   cannot preserve what the paper prints - `GABAA` came back as `GABA` + `U+2090`, `KM` as
#   `K` + `U+2098`. Measured on the station's own `candidates.jsonl` (2026-09-24): 6,540 rows
#   write their sub/superscripts as `<sub>`/`<sup>` markup and 55 rows carry Unicode sub/superscript
#   characters, so markup is this repository's spelling and the instruction to use Unicode was
#   asking the model to translate *away* from it. Markup also renders in the page's own font; the
#   Unicode characters fall back to whatever font has them.
#
# The prompts that carry them, all verbatim: `reread.SYSTEM` (the page reading, and through it
# `confirm_dispute.transcribe_system`, which is `reread.SYSTEM` plus the three human channels) and
# `vision.DISPUTE_SYSTEM` (the same transcription asked of one crop).
SUBSCRIPT_MARKUP_RULE = (
    "上下標照紙本原樣，用標記寫出來：紙本排成下標／上標的字，寫成 <sub>…</sub>／<sup>…</sup>"
    "（H<sub>2</sub>O、K<sub>M</sub>、GABA<sub>A</sub>、cm<sup>-1</sup>、10<sup>-5</sup>、"
    "e<sup>-kt</sup>）。**不可以**寫成 Unicode 的上下標字元——U+2070–U+209F（上標與下標）、"
    "U+1D2C–U+1D6A（上標修飾字母）等區段的字元一律不用，也不可以用 LaTeX（K_M、10^{-5}）。"
    "換句話說：下標是**原字元**加 <sub>…</sub>，不是把原字元換成另一個長得像下標的字元。"
    "紙本的字元、順序與大小寫一律照原樣（下標的大寫 A 就是 A、M 就是 M，不可以為了寫得出來"
    "而改成小寫）；符號不可以拼成字詞（∞ 就是 ∞）；除上下標以外一個字元都不動，"
    "也不可以自己加標點。"
)

#: 斜體的那一條（業主 2026-09-25：「管線好像開始抓『斜體字』……這部分可以加入」）。
#:
#: 為什麼要寫在提示詞裡而不是留給腳本自己判斷：斜體是**排版**，不是字。紙本把學名排成斜體
#: （`Streptococcus pyogenes`、`Bacteroides`——那一本微生物學紙本有 157 個 `Helvetica-Oblique`
#: 片段），而抽取器把排版丢了、收錄的文字是平的。判讀是唯一看得到紙本那一頁的東西，所以「這裡是
#: 斜體」只有它說得出來；腳本量得到字型（`extract.italic_spans_a`），但量不到**哪幾個字屬於這一題的
#: 哪一欄**。同一條規則的兩半，一半在提示詞、一半在腳本。
#:
#: 與上下標同一條紅線：只加標記，**字元一個都不動**（`<i>Bacteroides</i>` 裡的 11 個字母就是紙本上
#: 那 11 個字母）。
ITALIC_MARKUP_RULE = (
    "紙本排成**斜體**的字（學名、屬名、基因名、變數符號…）用 <i>…</i> 包起來，"
    "裡面的字母一個都不動（紙本斜體印刷的是 Bacteroides，就寫 <i>Bacteroides</i>，"
    "不是改成大寫、也不是換成別的寫法）。紙本不是斜體的字不要自己加斜體；"
    "看不出來是不是斜體時照平的打，不要猜。"
)


TABLE_LINES_RULE = (
    "題幹本身在紙上是一張表時（欄位標題列與資料列），要用 table_lines 說出來：把表格自己的行"
    "**逐字**引出來，一行一個字串，表頭列最先，其餘依紙本順序，字元與順序都照紙本（不加字也不減字）。"
    "**一條就是紙本上的一整列**——同一列的所有欄位合在一個字串裡（`CYP2D6 10 1`），"
    "**不是**一格一個字串（`CYP2D6`、`10`、`1` 三條）。行數要與紙本印出來的列數一樣。"
    "題幹不是表時，**不要**出現 table_lines 這個欄位——不要猜，也不要寫「看起來像表格」。"
)


def table_lines_of(record) -> list:
    """The printed lines a page read reported as the question's own table, or `[]`.

    The channel is the reading's, not a detector's. `TABLE_LINES_RULE` asks the transcription pass to
    quote the table's own lines verbatim when - and only when - the body is a printed table, and
    `reread.parse` passes the key through untouched; the record keeps the whole reading under
    `finding["transcription"]` (`confirm_dispute.finding_from`), which is where this reads it. The
    same key is in `reread_question`'s `record["seen"]` and in `result["seen"]` of that script's
    transcription step, so the shape is one shape and not a spelling per reader.

    `[]` for every other shape: no record, no reading, a reading that reported no table, and a key
    that is not a list of strings. "The reading said nothing" and "the reading said there is no
    table" both mean **no crop**, and collapsing them here is what keeps every caller from inventing
    its own reading of the field.

    One line is not a table and is not returned: the rule defines a table as a heading row *and* data
    rows, so a single quoted line is a line of text - and a region cut for it would be a crop of one
    line of the question, which is the mis-cut this whole path exists to avoid.
    """
    finding = (record or {}).get("finding")
    transcription = finding.get("transcription") if isinstance(finding, dict) else None
    lines = transcription.get("table_lines") if isinstance(transcription, dict) else None
    if not isinstance(lines, (list, tuple)):
        return []
    kept = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    return kept if len(kept) >= 2 else []


SYSTEM = """你是國考題庫抽取品管員。{arrival}
你的工作只有兩件：

1. `where`：**哪裡錯了**。指出具體位置（題幹，還是哪一個選項 A/B/C/D），並引用出問題的原文片段。
   不要說「整題看起來怪怪的」，要指出是哪幾個字。
2. `fix`：**怎麼修**。具體到「把 X 改成 Y」。若你認為不該自動修、需要人回去看紙本，
   就明說要看紙本的哪個位置，以及你預期會看到什麼。

不要回答題目的正確答案，也不要重述題目。不要客套。

【重要】有些題目的問題**不是抽取造成的**：可能是紙本本身的意外、答案爭議、或題目本身的錯。
這種情況 verdict 用 NOT_EXTRACTION，並在 `where`/`fix` 說明你的理由。
**誠實說「這不是抽取缺陷」比硬找一個缺陷有價值**，因為那會讓下一個人去修一個不存在的東西。

【重要】你只能根據**文字層看得見的東西**判斷。不要用學科知識（藥理、生理、法律…）
去判斷「這題的答案對不對」——那要看紙本、更正卷與命題者的意圖，不在你看得到的證據裡。
答案欄位顯示什麼就照著讀：顯示「送分」就是全部給分，顯示「B或C」就是任一個都算對。
若這一題的答案讓你的學科知識覺得奇怪，那不是抽取缺陷，不要把它寫成缺陷。

【重要】有些問題是**單一個案**（只出現一次的意外），不是一類。這種情況 `rule_worthy` 用 false。
這正是我們要的區分：**一類問題才值得寫規則，個案要單獨討論**，寫規則只會讓它變成誤傷別人的規則。

可能的性質代碼（`what` 只能用這些）：
{codes}

只輸出這個 JSON，不要有其他文字：
{{"verdict":"DEFECT"或"NOT_EXTRACTION"或"OK","what":"上面其中一個代碼","where":"具體位置與原文片段","fix":"具體修法，或說明為何不能自動修","rule_worthy":true或false,"confidence":0.0到1.0}}

若 verdict 是 DEFECT，`what` 不可以是 NONE。若不確定，把 confidence 設低，不要猜。"""

#: What has already been learned from earlier runs, added to the prompt when the caller asks for it.
#:
#: This is the loop's fourth step made real. The loop is: a person blocks, a model says what is wrong
#: and how to fix it, a person confirms, a rule is written - and then the *next* batch should start
#: from what was learned rather than from scratch. Without this the model rediscovers the same shapes
#: every run and the only place the knowledge lives is a human's memory.
#:
#: It is stated as things already **known or ruled out**, not as instructions to look for them, for two
#: reasons. A prompt that says "look for X" invites the model to find X whether or not it is there -
#: measured here: telling the model that empty options were picture options made it stop reporting
#: them as defects, which was right, but the same trick aimed at a shape that is *not* settled would
#: manufacture findings. And a settled shape does not need finding again: `disputes.py` already
#: detects it deterministically, which is the project's rule (detection is a script, meaning is a
#: prompt). What the model is asked to do with a settled shape is **not** report it, so the notes
#: stay about things nobody has classified yet.
LEARNED = """
【已經知道的事】以下是先前批次已經確認過、或已經被判定不是缺陷的形狀：
{learned}
若這一題的異常屬於上面任何一種，請在 `where` 簡短註明「已列為既知形狀：<代碼>」，
`rule_worthy` 用 false（已經有規則或已被排除，不需要再寫一次），
並把注意力放在**其他**異常上。若沒有其他異常，verdict 用 NOT_EXTRACTION。
"""

#: 審題者寫的「基本原則」——**人給的約束**，不是程式規則。
#:
#: 刻意做成一段提示詞文字，而不是編譯成 `if`：專案量過，把「讀懂文字的意思」寫成腳本會走上
#: 「規則→腳本→新問題→新規則」的跑步機。人在介面上寫下一句話、這句話原封不動地出現在模型
#: 眼前，這條路才可檢查——因為它是一個句子，不是一個實作。
#:
#: 與 `LEARNED` 分開，因為兩者對模型的要求相反：`LEARNED` 說「這些形狀已處理，看別的」，
#: 原則是「這些界線必須遵守」。把它們擠進同一段會讓一個「不要管 X」的約束讀成「去找 X」。
#:
#: 為空時整段不出現：一段空的【基本原則】會讀成「沒有原則」，而那是與「這次沒被給原則」
#: 不同的主張（同 `learned_note`）。
PRINCIPLES = """
【基本原則】審題者寫下的約束，你必須遵守；它們優先於你的判斷：
{principles}
"""

#: 審題者對這個代理先前反問的**回答**。
#:
#: 為什麼它必須存在：`repair_daemon.sh:55-59` 與 `docs/skills/review-ui-v2/SKILL.md:148-152` 都
#: 寫著「人對 `ask` 的回答下一輪提示詞讀得到」，但 `confirm_dispute.py` 只讀 `PRINCIPLES_STREAM`
#: ——**你回答的內容從來沒有進入下一輪的提示詞**。實測（2026-09-24）：`question_repair_questions.jsonl`
#: 是 0 bytes，所以這件事還沒發生過，但迴路本來就是斷的，一寫進答案就會斷在那一端。
#:
#: 它與基本原則分開，因為兩者是不同的東西：原則是一句**通則**（「早期試卷的字常常是 酶」），
#: 回答是對**某一題**的具體說明（「這一題的 ① 是羅馬數字不是上標」）。把它當原則會讓提示詞
#: 塞滿一次性的事實，而原則是給模型看的話、不是腳本——同樣的理由，回答也不該被編譯成規則。
ANSWERS = """
【審題者對你先前提問的回答】這些是針對特定題目的說明，與上面的基本原則不同：
{answers}
"""

#: 審題者寫在某一題上的「註解」——**逐字的原話**，不是摘要。
#:
#: 為什麼它必須存在：審題者在審核畫面上寫的註解從來沒有到模型眼前。`build_prompt` 與
#: `transcribe_system` 都沒有這個頻道，而 `blocked` 的開場白還反過來說「人類標記時並沒有說錯在
#: 哪裡」——於是模型被交給一題有人**寫下過位置**的題目，卻被要求自己重新猜一次。這正是
#: `POPULATIONS` 記著的同一個錯誤的另一半：那時是「沒人標記的題被說成人標記過」，這次是
#: 「人標了位置卻被說成沒說」。
#:
#: 與 `PRINCIPLES`／`ANSWERS` 分開，因為三者是不同的東西：原則是通則、回答是對反問的回覆，
#: 而註解是**針對這一題**說「我看到什麼不對」。三者都要原封不動地出現在提示詞裡，理由相同
#: ——它是一個句子，不是一個實作，所以可以被檢查。
#:
#: 為空時整段不出現（同 `learned_note`）：一段空的【審題者的註解】會讀成「人沒有說什麼」，
#: 那是與「這次沒被給註解」不同的主張。
NOTES = """
【審題者的註解】審題者在這一題上寫下的原話（未經改寫）：
{notes}
"""

#: 【這一題已經被改過又被退】— 這一題的機器修復史，只在有被退過時出現。
#:
#: 為什麼需要它：審題迴圈裡「機器改了、人又打回」原本只留下一件事——那個 block。人被問到「哪裡
#: 不對」時的答案可能只有一句話，甚至沒有；機器試過什麼、被退的是哪一筆改動，**沒有任何讀者**
#: （2026-09-25 量到：今天 62 題被打回第二次，其中 0 題帶著「上次改的是什麼」進到下一個提示詞）。
#: 於是第二輪只能從同一份材料重新發明一次同一個改動，人再打回一次——這就是螺旋沒有收斂的那一段。
#:
#: 這段話說三件已經量到的事，不多說一件：被打回的次數、上一次被打回的改動（逐欄 before/after）、
#: 以及**不准重貼**。它刻意不下判斷（不說「所以應該改成 X」）：該改成什麼要從紙本來源讀出來，
#: 那不是這個區塊能代替的事。
#:
#: 為空時整段不出現（同 `notes_note`）：一段 0 次的【已經被退】會讀成「這一題被退過但次數不明」，
#: 那是與「這一題沒被退過」不同的主張。
REJECTED = """
【這一題已經被改過又被退】機器在這一題上做過的修復，被人打回 {count} 次。
上一次被打回的改動（逐欄，左邊是原來的、右邊是機器改成的）：
{changes}
不要再用同一個改法改一次：同一個位置改成同一個樣子，已經被退過了。要嘛換一個有紙本來源根據的
讀法，要嘛明說這一題你無法從現有材料判斷（verdict 用 NOT_EXTRACTION，並寫出你缺什麼）。
若上面有【審題者的註解】，那是他打回時留下的方向，優先從那裡讀。
"""

#: 這一題的註解會改變開場白，因為它讓「人沒有說錯在哪」那句話變成假的。
#:
#: `blocked` 的預設開場白是為「只按了阻擋、什麼都沒寫」的題目寫的（見 `POPULATIONS`），
#: 而審題者的註解正是他說出位置的那種情況。兩者用同一句話，就是讓提示詞對模型說謊——而這是
#: 這個專案量過最貴的一種錯（見 `POPULATIONS` 的註記）。
BLOCKED_WITH_NOTE = {
    "arrival": "使用者給你一題「已經被人類標記為有問題」的題目文字，而且他在標記時**寫下了註解**，"
               "說出他認為問題在哪裡：「{note}」。這是他的原話，完整的那段在最後的【審題者的註解】。",
    "ask": "這一題已經被人類標記為有問題，而且**他說了他看到什麼**（見上面引的註解）。"
           "請先從他指出的地方查起：那個問題在文字層是否真的存在？是哪個字、哪個位置、什麼符號？"
           "若你認為問題不在抽取結果，而在紙本或題目本身（答案爭議、出題錯誤），"
           "verdict 用 NOT_EXTRACTION 並說明理由。你在他指出的地方以外還看到具體異常，也可以一併寫出。",
}

USER = """科目：{subject}
試卷：{paper}
題號：第 {number} 題

題幹：
{stem}

選項：
{options}

{figures}答案：{answer}

{ask}"""

#: What to tell the model about images. It matters because an option that is a picture is stored with
#: **empty text and an image reference**, and a model shown four empty options reports `MISSING_OPTION`
#: or `FIGURE_MISSING` every time - correctly, from what it can see, and uselessly, because this
#: project already decided those are figure-option questions and not defects. Measured: on
#: `105020:305:11 q052`/`q053` Splash called the empty options a defect and marked it rule-worthy.
#: The model is not being told the answer; it is being shown the same evidence a human reviewer gets,
#: which is that the option is an image.
FIGURE_NOTE = ("這一題的圖片：{count} 張（{parts}）。\n"
               "{facts}"
               "若某個選項的文字是空的，但上面說它有對應的圖片，那就是**圖片選項**，"
               "不是選項遺失——請不要把它當成缺陷。\n"
               "若題幹說「如下圖」、但這一題沒有任何圖片，那才是缺陷。\n"
               "上面沒說量到的事，就是**沒有量到**：不要用「圖看起來沒問題」代替它。\n\n")

#: 每一張圖**自己量到的事實**，一句話。只在欄位真的存在時才說——沒量到就不編。
#:
#: 這兩個字串是 2026-09-25 業主回報的那個缺陷的另一半：「有些題目原本沒圖卻截了上下題圖片；
#: AI 截圖檢查只看當下這題、沒上下資訊，於是回報『找不到問題』」。量到的事實是：切圖那一步
#: （`crop_run_figures`）已經在候選列上記了兩種事實——`ownership: unverified`（這一題的列在紙本上
#: 量不到，所以這張圖的歸屬無法確認）與 `clipped {above, below}`（原本的框蓋到隔壁題，已經只切
#: 這一題自己的列）——而**讀這一題的那一端從來沒讀它們**。事實已經量好了，缺的是把它說出來。
FIGURE_REF_FACTS = {
    "unverified": "**無法確認這張圖屬於哪一題**（紙本上量不到這一題的列）："
                  "它可能其實是相鄰題目的圖，圖裡不屬於這一題的內容不要當成這一題的。",
    "clipped": "原本的框蓋到隔壁題，已經只切這一題自己的列（上 %(above)gpt、下 %(below)gpt 切掉）。",
}


def figure_ref_note(ref) -> str:
    """One crop's own measured fact, or `""` when nothing was measured. Never invents one.

    `crop_run_figures` writes `clipped` **only** when it cut something and `ownership: unverified`
    **only** when the band could not be measured, so absence here means "not measured", and the
    honest note for that is silence - not "the picture is fine".
    """
    if not isinstance(ref, dict):
        return ""
    if str(ref.get("ownership") or "") == "unverified":
        return FIGURE_REF_FACTS["unverified"]
    clipped = ref.get("clipped")
    if isinstance(clipped, dict) and (clipped.get("above") or clipped.get("below")):
        return FIGURE_REF_FACTS["clipped"] % {"above": clipped.get("above") or 0,
                                              "below": clipped.get("below") or 0}
    return ""


#: The one mark that says "the reading could not read this region". A transcription model that cannot
#: see a field's content answers with this character rather than inventing text, and both sides have to
#: agree about what it means: the reader (`confirm_dispute`) must not call it a defect to be repaired,
#: and the applier (`dispute_apply.page_read`) refuses a change whose page side carries it ("比現在的
#: 文字更差"). Measured 2026-09-25 through the served stream: **127 changes carried `▢` and 96 of them
#: had an empty extraction side** - the machine was proposing to write "unreadable" into a blank
#: option, and one of those reached the owner as an ask. Two literals for one meaning is how those two
#: answers drifted apart, so there is one, here.
UNREADABLE_MARK = "▢"


def figure_caveat(question) -> str:
    """What this question's pictures do **not** let a reading conclude; `""` when they say nothing.

    Read by `confirm_dispute.finding_from`, which writes the sentence into `finding.where`. An
    agreeing page read is a statement about **text** (`changes_between` compares the stem and the
    options), so when a picture's ownership was never measured, 「紙本與抽取一致」 must not be read as
    「圖片沒問題」 - that reading is exactly the defect the owner reported on 2026-09-25 (the check said
    there was no problem while never having the above/below information to say it).
    """
    refs = [ref for ref in (question.get("image_refs") or []) if isinstance(ref, dict)]
    unverified = [ref for ref in refs if str(ref.get("ownership") or "") == "unverified"]
    if not unverified:
        return ""
    return ("；這一題有 %d 張圖**無法確認屬於哪一題**（紙本上量不到這一題的列），"
            "所以圖片不在這次判讀的結論裡。" % len(unverified))


def figures_note(question) -> str:
    """The image evidence, in the same vocabulary the reviewer's screen uses (`image_refs`).

    Plus, per picture, the facts that step measured about it (`figure_ref_note`). It used to print
    the `asset_role` alone, which is the same thing the screen shows as a thumbnail: a reader - model
    or person - was told 「這一題的圖片：1 張（figure-crop）」 for a crop whose ownership had never been
    measured, and the crop looks like a picture of this question either way.
    """
    refs = [ref for ref in (question.get("image_refs") or []) if isinstance(ref, dict)]
    if not refs:
        return FIGURE_NOTE.format(count=0, parts="沒有任何圖片", facts="") if _mentions_figure(question) else ""
    parts = []
    facts = []
    for ref in refs:
        role = ref.get("asset_role") or "image"
        key = ref.get("option_key")
        identity = "選項 %s 的圖" % key if role == "option-image" and key else role
        parts.append(identity)
        note = figure_ref_note(ref)
        if note:
            facts.append("　- %s：%s\n" % (identity, note))
    return FIGURE_NOTE.format(count=len(refs), parts="、".join(parts),
                              facts=("".join(facts) if facts else ""))


def _mentions_figure(question) -> bool:
    """Whether the text refers to a figure at all.

    Only used to decide whether to say "there are no images". Saying it on every question would
    invite the model to look for a missing figure on a question that never had one.
    """
    text = question.get("stem") or ""
    return bool(re.search(r"圖|表|figure|shown below|下図", text))


def options_block(options) -> str:
    lines = []
    for option in options or []:
        letter = str(option.get("key") or "").upper()
        lines.append("%s. %s" % (letter, option.get("text") or ""))
    return "\n".join(lines)


#: How a voided answer is rendered, kept as a named thing because `prompt_version` has to be able to
#: see it change. `answer_of` is behaviour, not text, so the hash below would not notice it being
#: rewritten - and a record written before the fix would claim the same prompt generation as one
#: written after, which is exactly the confusion the version field exists to prevent. The 15
#: false-positive `ANSWER_DISAGREES` findings are what a version that could not see this change
#: cost. Bump the wording here whenever how the answer is read changes.
ANSWER_READING = "送分／不計分的題目要以「送分」呈現，不是 A、B、C、D"


def answer_of(question) -> str:
    """The answer as a person should read it, which is not always the accepted values.

    A voided question is stored with `accepted_values` of every option so that an answer comparison
    does not fail - that is the machine's view, and it is deliberately not the reader's. The
    corrections sheet said "第10題一律給分", and the reader's view is `送分`. This function used to
    prefer `accepted_values` unconditionally, so the model was shown `答案：A、B、C、D` for a
    question whose answer was voided and no indication that the list meant "everyone scores".

    Measured cost: 15 of 28 `ANSWER_DISAGREES` findings in the first corpus sweep were this - the
    model correctly observed that a single-choice question cannot have four answers, and reported a
    defect in the paper when the defect was in the prompt. The same trap `review_queue.py` documents
    on the display side, arriving on the model's side.

    `is_special_correction` is the field that says which reading applies, so it decides.

    There is a second, quieter version of the same trap: the corrections sheet writes `答Ｂ或Ｃ者均給分`
    as `B或C`, and `accepted_values` holds both letters. Shown as `答案：B、C` that reads as a
    multi-select answer, and on a single-choice paper the model correctly objects - measured at 4 of
    the 28 `ANSWER_DISAGREES` findings, and 769 questions in the corpus carry an `或` answer. The
    stored display string already has the sheet's own wording, so it is used and the ambiguity is
    spelled out.
    """
    payload = question.get("answer_payload") or {}
    accepted = [str(value) for value in (payload.get("accepted_values") or [])]
    if payload.get("is_special_correction"):
        display = str(payload.get("answer") or question.get("answer") or "送分")
        if accepted:
            return "%s（這一題不計分／全部給分；選項 %s 都算對，這不是有四個答案）" % (
                display, "、".join(accepted))
        return display
    stored = str(payload.get("answer") or question.get("answer") or "")
    # A display string carrying `或` is the sheet saying "any of these is accepted". The letters are
    # not several answers to be marked at once.
    if len(accepted) > 1 and "或" in stored:
        return "%s（更正答案：這幾個選項**任一**都算對，不是要同時選）" % stored
    if accepted:
        return "、".join(accepted)
    return stored


def subject_of(question) -> str:
    metadata = question.get("metadata") or {}
    return (metadata.get("normalized_subject_name") or metadata.get("official_subject_name")
            or "")


def category_of(question) -> str:
    """The 考別 (which profession the paper is for), the unit a rule may be scoped to.

    Recorded on every finding because the requirement is that an error and its remedy be attributed
    to where it came from: a defect confined to one 考科 (藥學(一) formulas, 臨床生理學 tables) may be
    real and frequent there while being absent elsewhere, and a fix that helps one paper's shape may
    be wrong to apply globally. Without the field, "this happens 9 times" and "this happens 9 times
    in the same paper" look the same, and those call for different rules - a scoped one and a global
    one. `disputes.py` already decides scope per rule; this is the evidence that tells it which.

    Deliberately the *normalized* name, i.e. the same string the reviewer's screen and the queue
    index use, so a report can be joined back to the queue by name rather than by a code nobody reads.
    """
    metadata = question.get("metadata") or {}
    return (metadata.get("normalized_category_name") or metadata.get("official_category_name")
            or metadata.get("group_name") or "")


def paper_of(question) -> str:
    """The paper a candidate key points at, for the record's own grouping."""
    parts = str(question.get("candidate_key") or "").split(":")
    return ":".join(parts[1:3]) if len(parts) >= 3 else ""


def reading_fingerprint(question) -> str:
    """A hash of exactly the reading the model was shown.

    This is what makes the record checkable later. A finding is a statement about one particular
    reading; if the queue is rebuilt and the reading changes, the old finding is not false, it is
    about different text, and this hash is how a reader can tell which case they are looking at.

    The stored text is hashed *unfolded*. `canon.fold` is for comparing two readings; using it here
    would erase the very differences (a Kangxi radical, a full-width letter) that a finding may be
    about.
    """
    payload = {
        "stem": question.get("stem") or "",
        "options": [[str(o.get("key") or ""), o.get("text") or ""]
                    for o in (question.get("options") or [])],
        "answer": answer_of(question),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def population_framing(population, notes=None) -> dict:
    """How the question reached the model — which is a different sentence once a note exists.

    `POPULATIONS` says, for `blocked`, that the person did not say where it was wrong. That is true
    for a bare 阻擋 and false for a question the reviewer annotated, and the difference is not
    cosmetic: the same sentence tells the model "you have only the text, re-derive the problem
    yourself" and hands it a question whose reviewer said exactly what he was looking at.
    `BLOCKED_WITH_NOTE` is that sentence rewritten for the note case, quoting him.

    Single-sourced here so `build_prompt` and `prompt_version` cannot disagree about which framing a
    prompt was built under — the version hash reads this function, so a prompt with a note can never
    claim the version of one without.
    """
    framing = dict(POPULATIONS[population])
    note = latest_note(notes)
    if population == "blocked" and note:
        framing.update(BLOCKED_WITH_NOTE)
        framing["arrival"] = framing["arrival"].format(note=note)
    return framing


def build_prompt(question, *, learned=None, population="blocked", principles=None,
                 notes=None, rejected=None) -> tuple:
    """The system prompt and the user turn. Returned together so a record can store both.

    `learned` is optional on purpose. Omitting it is not the same as passing an empty one: a pass
    given prior findings and one that was not are different passes, and the record keeps the prompt
    verbatim so the difference survives.

    `principles` are the 基本原則 the reviewer wrote in the 錯題討論區. They are **constraints**, not
    observations, which is why they are a separate block from `learned`: `learned` says "these shapes
    are handled, look elsewhere", a principle says "this boundary must be kept". Folding them together
    would let "ignore X" read as "go find X". Omitted and empty are different states, exactly as for
    `learned` - an empty block would read as "there are no principles", which is a claim about a person
    rather than about this run. What arrives here is `principles_for_prompt`'s output - the **approved**
    ones - and nothing else; a principle nobody approved is drawn on the screen as 待你核准 and is not
    sent.

    `population` says how the question got here, and it is not cosmetic - see `POPULATIONS`. The
    default is the loop's case (a person blocked it), because that is what the record format was
    built around; the corpus pass must pass `"corpus"` explicitly so it cannot inherit a claim that
    a human flagged a question nobody has looked at.

    `notes` is the reviewer's 註解 on **this question** (see `NOTES`): the sentence they typed, or the
    rows a round of the loop read out of the stream (`[{candidate_key, notes, created_at}]`). It is a
    third channel, not a shape of `principles`: a principle is a general constraint, a note is a
    statement about one question ("我看到什麼不對"). When there is one, the `blocked` framing stops
    claiming the person said nothing and the sentence is quoted (see `BLOCKED_WITH_NOTE`).

    `rejected` is the loop's own memory of this question being repaired and refused
    (`repair_loop.rejections_by_key`'s row, see `REJECTED`). A fourth channel, and not a shape of
    `notes`: the note is what the person said, this is what the **machine** did and had thrown back.
    A pass that is not told it re-invents the same change; passing an empty value is "nothing was
    refused", which renders as no block at all.
    """
    framing = population_framing(population, notes)
    codes = "\n".join("- %s：%s" % (code, desc) for code, desc in CODES.items())
    number = question.get("question_number")
    user = USER.format(subject=subject_of(question) or "（未知）",
                       paper=paper_of(question) or "（未知）",
                       number=number if number is not None else "?",
                       stem=question.get("stem") or "（空白）",
                       options=options_block(question.get("options")) or "（無）",
                       figures=figures_note(question),
                       answer=answer_of(question) or "（無）",
                       ask=framing["ask"])
    return (SYSTEM.format(codes=codes, arrival=framing["arrival"])
            + learned_note(learned) + principles_note(principles) + notes_note(notes)
            + rejected_note(rejected), user)


def prompt_version(population="blocked", learned=None, principles=None, answers=None,
                   notes=None, rejected=None) -> str:
    """A short hash of the prompt itself, so a change to it is visible in every record.

    The loop's whole point is that the prompt is adjusted between batches, which means two records
    written an hour apart may answer slightly different questions. Storing the prompt text verbatim
    already makes that *checkable*; this makes it *visible* - a record says which prompt generation
    it belongs to, so notes from before and after a change can be compared by a field rather than by
    a diff. `disputes.py`'s detectors have the same problem and the same answer: a rule change is
    identified by a name, not by remembering when it happened.

    `learned` is hashed **by its rendered text, not by the template**, which is the whole point of
    passing it: hashing `LEARNED` alone would say a run told to ignore `substituted-script` and a run
    told to ignore `FIGURE_MISSING` are the same prompt, and those are two different measurements of
    the model. (Measured: the first corpus sweep stored a garbled `learned` block - argparse dropped
    all but the last flag and `parse_learned` walked the string a character at a time - and under the
    template-only hash those records claimed the same version as a run given the real block.)

    `principles` is hashed the same way and for the same reason, with one extra: the reviewer can
    change the principles **between two runs of a resident loop that nobody is watching**, so two
    records whose text is identical can have been produced under different constraints. Hashing the
    rendered block is what makes those two records distinguishable at all. The list hashed here is
    `principles_for_prompt`'s (approved only) - the same list `build_prompt` renders - so the hash
    cannot describe a prompt other than the one that was sent.

    And `population`, because a pass that tells the model "a human flagged this" is not the same
    measurement as one that tells it "nobody has looked at this yet" - they even ask for different
    things. Two records that differ only in that sentence must not hash to the same version, or the
    records most likely to be compared (blocked questions vs the corpus sweep) would look like one
    consistent measurement.

    `notes` is hashed **through `population_framing`**, because a note changes two things at once:
    the 【審題者的註解】 block it is rendered into, and the `blocked` opening that must stop claiming
    the reviewer said nothing. Hashing the block alone would leave two prompts - one that tells the
    model "he did not say where" plus a quoted sentence, one that tells it "he did not say where" -
    filed under one version.

    `answers` is hashed the same way it is rendered. It was **missing** from this body for a while
    even though the ANSWERS block was: `prompt_version` hashed `ANSWERS` (the template, always the
    same string) and never `answers_note(answers)`, so a run with the reviewer's replies and one
    without claimed the same generation. That is the same class of bug as the one this channel is
    about - the prompt changed and the version did not - and it is fixed here rather than in a
    second place.

    `FIGURE_NOTE` is the other one of that pair: it is the block that says what the pictures are and
    what was measured about them, and it was the only prompt block **not** in this body (it is
    rendered through `figures_note`, whose per-question part cannot be hashed here, but the block
    itself can). 2026-09-25: it gained the crops' own facts (`clipped`, `ownership: unverified`), so
    records written before and after that change must not claim one generation.

    `rejected` is hashed through its rendering (`rejected_note`, the `answers` rule): the block says
    how many times this question was refused **and what was refused**, so two runs of the same
    question - one before the second refusal and one after - are different measurements of the model
    under different instructions. Hashing `REJECTED` (the template, always the same string) would
    make them one generation.
    """
    framing = population_framing(population, notes)
    body = "\x00".join([SYSTEM, USER, LEARNED, learned_note(learned), PRINCIPLES,
                         principles_note(principles), ANSWERS, answers_note(answers),
                         NOTES, notes_note(notes), REJECTED, rejected_note(rejected),
                         framing["arrival"],
                         framing["ask"], ANSWER_READING, FIGURE_NOTE, "\x00".join(sorted(CODES))])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def answers_note(questions) -> str:
    """The reviewer's answers to this agent's own earlier questions, or nothing when there are none.

    Takes whatever `discuss.repair_questions_projection` returns (`{"questions": [...]}` or the list
    itself), and renders **only the answered ones that are still open-ended context** — an unanswered
    question is already going to be asked again, so putting it here would tell the model its own
    question back without the answer.

    The question text is included, not just the answer: the answer is a reply to something, and
    without the question it is a sentence with no referent. Measured 2026-09-24 — the stream is empty,
    so this renders nothing today; it exists so the loop is closed before the first answer is written,
    not after.
    """
    if not questions:
        return ""
    rows = questions.get("questions") if isinstance(questions, dict) else questions
    lines = []
    for row in rows or []:
        answer = str((row or {}).get("answer_text") or "").strip()
        if not answer:
            continue
        asked = str((row or {}).get("question") or "").strip()
        number = str((row or {}).get("candidate_key") or "").strip()
        head = "．%s" % number if number else ""
        lines.append("%d. %s%s\n   問：%s\n   答：%s" % (len(lines) + 1, head,
                                                        "" if not head else " ", asked, answer))
    if not lines:
        return ""
    return ANSWERS.format(answers="\n".join(lines))


def note_rows(notes) -> list:
    """The reviewer's 註解, normalised into rows and put **newest first**. One normalisation only.

    Accepts what the three callers actually hold, instead of making each of them reshape it: the
    single sentence a review-UI box produces (`str`), the `{candidate_key: text}` mapping a round
    produces, or the rows the loop already read out of the stream
    (`[{"candidate_key", "notes", "created_at"}]`). Everything downstream - `notes_note`,
    `latest_note`, `prompt_version`, the record's `notes` field - reads this shape, so there is no
    fourth spelling for a caller to invent.

    Newest first, because the reviewer's most recent sentence is the one they want acted on; rows
    without a `created_at` (a hand-built prompt) keep the order they were given in. Empty notes are
    dropped rather than rendered as a blank line: "he wrote nothing" and "he wrote here" are
    different claims about a person.
    """
    if not notes:
        return []
    if isinstance(notes, str):
        rows = [{"candidate_key": None, "notes": notes, "created_at": None}]
    elif isinstance(notes, dict):
        rows = [{"candidate_key": key, "notes": value, "created_at": None}
                for key, value in notes.items()]
    else:
        rows = []
        for item in notes:
            if isinstance(item, str):
                rows.append({"candidate_key": None, "notes": item, "created_at": None})
            elif isinstance(item, dict):
                rows.append({"candidate_key": item.get("candidate_key"),
                             "notes": item.get("notes"), "created_at": item.get("created_at")})
    rows = [{"candidate_key": row.get("candidate_key"),
             "notes": str(row.get("notes") or "").strip(),
             "created_at": row.get("created_at")} for row in rows]
    rows = [row for row in rows if row["notes"]]
    timed = [row for row in rows if row["created_at"]]
    untimed = [row for row in rows if not row["created_at"]]
    ordered = sorted(timed, key=lambda row: str(row["created_at"]), reverse=True) + untimed
    return ordered


def latest_note(notes) -> str:
    """The sentence the `blocked` opening quotes — the reviewer's most recent 註解 on this question."""
    rows = note_rows(notes)
    return rows[0]["notes"] if rows else ""


def notes_note(notes) -> str:
    """The reviewer's 註解 block, or nothing when there are none.

    Rendered verbatim (the sentence is quoted, never summarised), numbered and labelled by
    candidate key when the row knows one, because a note is a statement about **one** question and
    two questions' notes must not read as one paragraph. Newest first, per `note_rows`.
    """
    rows = note_rows(notes)
    if not rows:
        return ""
    lines = []
    for row in rows:
        head = "．%s" % row["candidate_key"] if row["candidate_key"] else ""
        lines.append("%d. %s%s%s" % (len(lines) + 1, head, "" if not head else " ", row["notes"]))
    return NOTES.format(notes="\n".join(lines))


def rejected_note(rejected) -> str:
    """The 「這一題已經被改過又被退」 block, or nothing when the question was never refused.

    `rejected` is one question's row from `repair_loop.rejections_by_key`
    (`{"count", "fields", "changes"}`). An `int` is accepted for the callers that only hold the count
    (`repair_loop.explain`'s entry, a test), but a count alone renders as "被退 N 次" with no record of
    *what* was refused — which is the half that stops the next pass repeating it.

    Values are rendered, never summarised: a refused change is quoted `原文 → 改成的樣子`, field by
    field. A field whose `from`/`to` the stream did not record is named without a value rather than
    shown as empty quotes — "the change was not logged" and "the change was to an empty string" are
    different facts about the log.
    """
    if not rejected:
        return ""
    if isinstance(rejected, int):
        count, changes = rejected, []
    else:
        count = int((rejected or {}).get("count") or 0)
        changes = [change for change in ((rejected or {}).get("changes") or [])
                   if isinstance(change, dict)]
    if count < 1:
        return ""
    if changes:
        lines = ["%d. %s" % (index, rejected_change_text(change))
                 for index, change in enumerate(changes, 1)]
        rendered = "\n".join(lines)
    else:
        rendered = "（這一題沒有留下一筆被退的改動：人打回它時，機器還沒改過它。）"
    return REJECTED.format(count=count, changes=rendered)


def rejected_change_text(change: dict) -> str:
    """One refused change as `field：原文 → 改成的樣子`, with the field named as the log names it."""
    field = str((change or {}).get("field") or "（未記欄位）")
    before, after = (change or {}).get("from"), (change or {}).get("to")
    if before is None and after is None:
        return field
    return "%s：%s → %s" % (field, rejected_value_text(before), rejected_value_text(after))


def rejected_value_text(value) -> str:
    """A `from`/`to` value as text. Strings are quoted (`"…"`), everything else is its JSON.

    A string is quoted because an empty one has to be distinguishable from a missing one: the
    difference between 「刪掉了整段」 and 「這一欄沒有記」 is exactly the difference the next pass needs.
    """
    if value is None:
        return "（未記）"
    if isinstance(value, str):
        return '"%s"' % value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def principles_for_prompt(events) -> list:
    """The 基本原則 a prompt may carry: **the approved ones, and only those**（2026-09-24）.

    One reader, so the two callers that build a prompt from the principles stream (`confirm_dispute.py`
    and `ask_about_blocks.py`) cannot each decide for themselves which principles the model is shown.
    A principle reaches the model as a constraint it obeys while rewriting question text, so a
    principle nobody has approved yet has no business changing anything - and the screen shows that
    same principle as 「待你核准」, which would be a lie the moment it were sent anyway.

    `discuss.active_principles` keeps its meaning (every principle in force) because it is what the
    **screen** draws: a principle waiting for approval must be visible to the person who has to approve
    it. The distinction is deliberate and documented on both sides; do not send `active_principles`
    here.

    The result is what `build_prompt` and `prompt_version` must be given, so the prompt that is sent
    and the hash stored in the record are computed from one list rather than two.
    """
    return discuss.approved_principles(events)


def principles_note(principles) -> str:
    """The reviewer's constraints, or nothing when there are none.

    `principles` is the list `ai_findings.principles_for_prompt` returns (the approved ones - see
    there for why the prompt does not read `discuss.active_principles`), a mapping, or a string,
    because the two callers that pass it (the resident loop and the one-off corpus pass) get it from
    different places and neither should have to reshape it. Numbered, because a constraint a person
    wrote is something they may want to refer to ("the second one") when they answer the agent back.
    """
    if not principles:
        return ""
    if isinstance(principles, str):
        lines = [principles]
    elif isinstance(principles, dict):
        lines = ["%d. %s：%s" % (index, str(item).strip(), str(note or "").strip())
                 for index, (item, note) in enumerate(sorted(principles.items()), 1)]
    else:
        lines = ["%d. %s" % (index, str(item).strip())
                 for index, item in enumerate(principles, 1)]
    return PRINCIPLES.format(principles="\n".join(lines))


def learned_note(learned) -> str:
    """The "already known" block, or nothing when there is nothing to say.

    Kept out of the prompt entirely when empty: an empty 【已經知道的事】 section would read as
    "nothing is known", which is a different claim from "this run was not given prior findings".
    """
    if not learned:
        return ""
    if isinstance(learned, str):
        lines = [learned]
    else:
        lines = ["- %s：%s" % (str(item).strip(), str(known or "").strip())
                 for item, known in sorted(learned.items())]
    return LEARNED.format(learned="\n".join(lines))


def make_record(*, question, finding, model, endpoint, prompt_system, prompt_user,
                raw="", usage=None, seconds=0.0, error=None, created_at=None, learned=None,
                population="blocked", crop=None, changes=None, principles=None,
                answers=None, notes=None, rejected=None) -> dict:
    """One finding, with everything needed to check it later.

    `prompt_user` is stored verbatim and not regenerated at read time: the point of the record is
    what the model was actually shown, and a prompt rebuilt from the current code would answer a
    different question.
    """
    return {
        "schema": SCHEMA,
        "candidate_key": question.get("candidate_key"),
        "question_number": question.get("question_number"),
        "paper": paper_of(question),
        "subject": subject_of(question),
        # 考別. Part of the record because a rule that is right for 藥學(一) may be wrong globally, and
        # "this shape occurs 9 times" must be readable as "9 times in the same paper".
        "category": category_of(question),
        "reading_sha256": reading_fingerprint(question),
        "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "endpoint": endpoint,
        # Which generation of the prompt this note belongs to. The prompt is adjusted between batches
        # - that is the loop - so without this field two notes taken before and after a change look
        # like the same measurement and cannot be compared without diffing the stored text by hand.
        # The `answers` and `notes` are passed here too: both are rendered into the prompt, so a run
        # given either is a different measurement. `answers` was missing from this call while the
        # ANSWERS *template* was hashed, which made two different prompts share one version.
        "prompt_version": prompt_version(population, learned, principles, answers, notes, rejected),
        # How the question reached the model: a person blocked it, or it was next in the file. A
        # finding about a blocked question and one about a random corpus question are different
        # claims with different error rates, so which population it came from is part of the record.
        "population": population,
        "learned": learned or None,
        # The 基本原則 in force when this note was written, kept verbatim beside the prompt for the same
        # reason the prompt is: a constraint the reviewer edited later would otherwise be invisible in
        # the record, and two notes taken under different constraints would look like one measurement.
        # A note made before this field existed has `None`, which is "not recorded", not "no
        # principles" - the same distinction `learned`/`crop` keep.
        "principles": list(principles) if principles else None,
        # The reviewer's 註解 on this question, verbatim, kept beside the prompt for the same reason:
        # it is part of what the model was shown, it is the human's own words, and a note typed while
        # the loop was running would otherwise be visible only inside the stored 500-character prompt.
        # `None` means "this run was not given a note", not "the reviewer wrote nothing" - a question
        # nobody annotated and one whose note fell out of the prompt must not look the same.
        "notes": note_rows(notes) or None,
        # 這一題的機器修復史：被退幾次、上一次被退的是哪一筆改動（逐欄）。與 `notes` 分開，因為
        # 兩者是不同人的話——`notes` 是審題者寫的，這個是機器做過的。`None` 是「這次沒把這一題
        # 被退過的事交給模型」，不是「這一題沒被退過」（`REJECTED` 為空時整段不出現，理由相同）。
        "rejected": rejected or None,
        "prompt_system": prompt_system,
        "prompt_user": prompt_user,
        # The exact text the model was shown. A reader who cannot see the evidence is trusting a
        # summary, and this project's rule is that unseen evidence is not evidence.
        "evidence": {"stem": question.get("stem") or "",
                     "options": options_block(question.get("options")),
                     "answer": answer_of(question)},
        "finding": finding,
        "raw": raw,
        "usage": usage or {},
        "seconds": seconds,
        "error": error,
        # The crop the model was shown, when one was shown, as a queue-relative path
        # (`review-ui/crops/<paper>/...`) so the reviewer's screen can open it. "Unseen evidence is
        # not evidence": a finding that says "the paper prints 長 here" is only worth reading if the
        # reader can look at the same picture. Absent for the text-only passes, because they had no
        # crop and an empty field would read as "the crop was lost".
        "crop": crop,
        # The mechanical diff between the stored text and the model's reading of the page, field by
        # field (`[{field, from, to}]`). This is computed by `reread.compare`, **not** said by the
        # model: the model transcribes, this subtracts. (c) of the requirement was that a disputed
        # question be *repairable*, not merely reported, and a repair has to be exact - a prose `fix`
        # is a description, this is the change.
        "changes": changes or None,
    }


#: The name of the stream, inside the queue's `review-ui/` directory.
#:
#: It lives there, next to the human decision log, because that is the only place the rest of the
#: pipeline already protects. I first put it *beside* `review-ui/` so a model's note could never sit
#: next to a person's decision - and then found that the rebuild carries only named streams inside
#: `review-ui/`, and `deploy_station.sh` protects only their names, so a rebuild into a new directory
#: would have discarded every note silently. Durability beats tidiness here, and the separation that
#: actually matters (a finding is not a decision) is guaranteed by this record having no `action`
#: and no `reviewer`, not by which directory it sits in.
STREAM = "question_ai_findings.jsonl"


def store_path(queue_root: str) -> str:
    """Where findings for one queue live.

    `queue_root` may be either the queue root (holding `review-ui/`) or that directory itself, for
    the same reason `repair_loop.review_ui_dir` tolerates both: the flag is named after the queue,
    and a script that quietly wrote to the wrong directory would look exactly like "no findings yet".
    """
    if os.path.basename(os.path.normpath(queue_root)) == "review-ui":
        return os.path.join(queue_root, STREAM)
    if os.path.exists(os.path.join(queue_root, "review-ui")):
        return os.path.join(queue_root, "review-ui", STREAM)
    return os.path.join(queue_root, STREAM)


def append(path: str, record: dict) -> None:
    """Append one finding. The only writer in this module - there is no rewrite by design."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load(path: str) -> list:
    """Every finding in order. Unparseable lines are kept as `{"_unparseable": <line>}`.

    Kept rather than dropped because a corrupt line is a fact about the file; silently skipping it
    would make "the log is damaged" and "there is no finding" look identical.
    """
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"_unparseable": line})
    return records


def latest_by_question(path: str) -> dict:
    """The most recent finding per candidate key, keeping the order of first appearance."""
    latest = {}
    for record in load(path):
        key = record.get("candidate_key")
        if key:
            latest[key] = record
    return latest


def is_answer(record) -> bool:
    """Whether this record answered anything at all.

    A failed request is *recorded* on purpose — the prompt that was sent, the raw reply, the error —
    but it answered nothing, so `finding` is `None`. Counting it as an answer turns a transient HTTP
    failure into a result, **silently**: the question stops being stale, so `--restale` never re-asks
    it and `--resume` skips it forever. Measured 2026-09-24: 1 of 4 pilot calls to the DGX came back
    `request failed` and was written as a current-generation record.

    `repair_agent.pending_keys` already treated `finding: null` as unanswered, and
    `confirm_dispute.confirmed` has its own (stricter, transcription-based) version of the same idea.
    This is the shared definition so those readers cannot drift apart; `stale_questions` and the
    `--resume` skip set both use it.
    """
    return bool((record or {}).get("finding"))


def stale_questions(path: str, current_version, population=None) -> set:
    """Keys whose *latest* finding was written under a different prompt generation.

    Deliberately not "every record whose version differs": a question re-asked later would otherwise
    still look stale from its older record, and would be re-asked forever. What matters is which
    generation the question's **current** answer belongs to, which is what `latest_by_question` means.

    A latest record that is not an answer (`is_answer`) belongs to **no** generation, so the question
    is stale: the alternative is a failed call posing as a result.

    `population` restricts it to one framing: a corpus sweep in flight is not stale just because a
    fix landed, it is a different measurement on purpose. Without this, `--restale` during a running
    pass would try to re-ask all 79,090 questions.

    `current_version` may be a **mapping** (`{candidate_key: version}`) instead of one string, because
    a version is not always one number for a whole run: the reviewer's 註解 on a question is part of
    that question's prompt (see `prompt_version`), so a run that passes notes has one version per
    annotated question. Compared against one shared version, every annotated question would look stale
    on every `--restale` - and, because the re-asked record also carries the note, it would look stale
    again next time: a loop that re-asks the same questions forever.
    """
    stale = set()
    for key, record in latest_by_question(path).items():
        if population is not None and record.get("population") != population:
            continue
        version = current_version.get(key, "") if isinstance(current_version, dict) else current_version
        if not is_answer(record) or record.get("prompt_version") != version:
            stale.add(key)
    return stale


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, small enough to write out. Used only to recover a near-miss code."""
    if abs(len(a) - len(b)) > 1:
        return 2
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def normalize_code(value):
    """Map a reported code onto the vocabulary, tolerating case, separators and one typo.

    Measured need: the engine answered `GLYPDAMAGE` for `GLYPH_DAMAGE`, and the first version of
    `parse_finding` discarded the whole note over one missing letter. But the `what` code is only an
    index into the note - the value is `where` and `fix`.

    The typo recovery is deliberately narrow: the normalised form must be within **one** edit of
    **exactly one** vocabulary entry. If two codes are both one edit away, nothing is chosen - a
    coin flip between `STEM_TRUNCATED` and `OPTION_TRUNCATED` would label a finding wrongly, and a
    wrong label is worse than a missing one because it groups with the wrong class.
    """
    if not isinstance(value, str):
        return None
    def flatten(text):
        return "".join(ch for ch in text.upper() if ch.isalnum())
    wanted = flatten(value)
    for code in CODES:
        if flatten(code) == wanted:
            return code
    near = [code for code in CODES if _edit_distance(flatten(code), wanted) <= 1]
    return near[0] if len(near) == 1 else None


#: The fields the model is asked for, in the order the schema asks for them. Salvage reads them in
#: this order and refuses to skip: a truncated object has no reliable boundary, so anything after a
#: gap that is not a comma is the model still writing, not the next field.
FINDING_FIELDS = ("verdict", "what", "where", "fix", "rule_worthy", "confidence")

#: One complete `"key": value` pair. A string is matched only when its **closing quote** arrived,
#: which is what makes the field list stop at the cut instead of swallowing half a sentence.
_FIELD = re.compile(r'"([A-Za-z_]+)"\s*:\s*("(?:[^"\\]|\\.)*"|true|false|null)')
#: The only thing allowed between two fields.
_FIELD_GAP = re.compile(r"\s*,?\s*\Z")


def salvage_finding(content: str):
    """Read the fields the model got through before its reply degenerated.

    Measured 2026-09-24 on `dgx-qwen3.8-flash`: across the whole station stream, **83 replies had
    `finding: null` and 83 of them were recoverable by this reader** - and `json.loads` failed on
    every one. In every one the reply had *already written the answer down*:

        {"verdict":"DEFECT","what":"GLYPH_DAMAGE","where":"題幹：「抗癲癇藥物」中的「癲癇」二字…

    and then, inside a field, the model falls into a repetition loop - 「正確詞彙為「抗癲癇」->「抗癲
    癇」? 錯誤。正確詞彙為…」 for four thousand more characters, never closing the object. Measured
    where the cut lands: `verdict` and `what` arrived in **83 of 83**; `where` survived only 19, and
    `confidence` in none. The class is mostly `GLYPH_DAMAGE` - the one where the model must name a
    difference between two strings that **render identically** (a Kangxi radical beside the Han
    character, a rare variant of 癲). The reply was the answer. Only the parser lost it.

    The collapse is **stochastic, not a property of those questions**: re-asking the six worst
    replies with the same prompt and the same settings parsed **6 of 6** on 2026-09-24, and the
    `verdict`/`what` they returned agreed with what this reader recovered. That is why the recovery
    is worth having even though a retry can also work - it costs no engine time and it keeps *this*
    call's own reading, which a re-ask would replace with a second record.

    So the cut is read as what it is: fields whose closing quote arrived are kept, the rest are
    dropped rather than guessed at. Returns `(finding, truncated)` - `(None, False)` when there is no
    object at all, and `truncated` is True whenever the object did not parse, because a record that
    needed salvage is a record whose generation collapsed and a reader has to know that. A field that
    is absent from a truncated finding is absent because the reply never finished it, which is not
    the same claim as "the model had nothing to say" - that is what `truncated` is for.
    """
    start = content.find("{")
    if start < 0:
        return None, False
    found = {}
    cursor = start + 1
    for match in _FIELD.finditer(content, cursor):
        key = match.group(1)
        # Order and no repeats: `"where": "x", "where": "y"` is not a field, it is the model writing
        # the same field twice, and taking the first is a guess about which one it meant.
        if key not in FINDING_FIELDS or key in found:
            break
        if not _FIELD_GAP.match(content, cursor, match.start()):
            break
        found[key] = match.group(2)
        cursor = match.end()
    if "verdict" not in found:
        return None, False
    finding = {}
    for key, literal in found.items():
        if literal == "true":
            finding[key] = True
        elif literal == "false":
            finding[key] = False
        elif literal == "null":
            finding[key] = None
        else:
            try:
                finding[key] = json.loads(literal)
            except json.JSONDecodeError:
                finding[key] = literal[1:-1]
    return finding, True


def json_text(content: str) -> str:
    """A model's answer with its code fence removed, and nothing else changed.

    Split out of `parse_finding` so the second prompt in this project that asks a model for a JSON
    object (`propose_principles.py`, whose schema is a principle proposal rather than a finding) can
    read an answer the same way instead of growing a second, slightly different reader. `lstrip`ing
    the whole string would strip any leading `j`/`s`/`o`/`n` **characters**, not the word, so the
    language tag is removed as a prefix.
    """
    text = (content or "").strip()
    if not text.startswith("```"):
        return text
    text = text.split("```")[1] if "```" in text[3:] else text[3:]
    stripped = text.lstrip()
    return stripped[4:] if stripped.startswith("json") else stripped


def json_object(content: str):
    """The first JSON object in a model's answer, or `None` when there is none to read.

    Tolerates a fence and any prose the model wrapped it in, because both are things the measured
    engines do. Idempotent: passing already-de-fenced text is the same call.
    """
    text = json_text(content)
    start, end = text.find("{"), text.rfind("}")
    if start < 0:
        return None
    if end > start:
        try:
            loaded = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
        if isinstance(loaded, dict) and loaded:
            return loaded
    return None


def parse_finding(content: str):
    """Pull a finding out of whatever the model wrapped its JSON in.

    The note is kept even when the code is not in the vocabulary, because the note is the point.
    What is *not* kept is a wrong code: an unrecognised one is reported as `what: None` with the
    original in `what_reported`, so nobody can mistake it for data the pipeline can act on. The
    record still says where the model thought the error was and how it would fix it - which is what
    a person reads later - and it also says that the code could not be trusted.

    Only a response with no usable JSON object at all comes back as `None`, because then there is no
    note to keep - and a response whose object was cut off mid-field is not that: its leading fields
    are read by `salvage_finding` and marked `truncated`, because the model did answer.
    """
    text = json_text(content)
    start = text.find("{")
    if start < 0:
        return None
    finding = json_object(text)
    truncated = False
    if finding is None:
        # A response cut off mid-field keeps its leading fields (`salvage_finding`) - the object
        # reader above only accepts a whole one.
        finding, truncated = salvage_finding(text)
        if finding is None:
            return None
    verdict = str(finding.get("verdict") or "").strip().upper()
    if verdict not in VERDICTS:
        # A code where a verdict belongs. Measured on the Splash engine (`reasoning_effort=none`):
        # it answered `"verdict":"FIGURE_MISSING","what":"FIGURE_MISSING"` and, on another question,
        # `"verdict":"FIGURE_MISSING"` with the code left out of `what` - so requiring the verdict to
        # be one of three words discarded two of three correct findings. The code's own meaning
        # supplies the verdict: `NONE` is not a defect, every other code is.
        code_from_verdict = normalize_code(finding.get("verdict"))
        if code_from_verdict is not None:
            finding["verdict_reported"] = finding.get("verdict")
            finding["verdict"] = "OK" if code_from_verdict == "NONE" else "DEFECT"
            if not finding.get("what"):
                finding["what"] = code_from_verdict
            verdict = finding["verdict"]
        else:
            # Genuinely unrecognised. It decides whether the note is a defect report at all, so the
            # note is kept but marked unclassified rather than guessed at.
            finding["verdict_reported"] = finding.get("verdict")
            finding["verdict"] = None
            verdict = None
    reported = finding.get("what")
    code = normalize_code(reported)
    if code is None and reported is not None:
        finding["what_reported"] = reported
    if code is not None:
        finding.pop("what_reported", None)
    finding["what"] = code
    # A DEFECT that says NONE (or says nothing) contradicts itself; it is treated as unclassified
    # rather than as a defect, but the note survives.
    if finding.get("verdict") == "DEFECT" and finding.get("what") in (None, "NONE"):
        finding["verdict"] = None
    if truncated:
        # Said out loud rather than left to be inferred from a missing field: a reader comparing this
        # note with a clean one has to be able to see that its generation collapsed, because the
        # fields that are absent are absent for a reason that is not "the model had nothing to say".
        finding["truncated"] = True
    return finding


def is_rule_worthy(record: dict) -> bool:
    """Whether the model judged this a class rather than a one-off.

    Read defensively: an unparsed or absent finding is not rule-worthy, because the default when
    the answer is unreadable must be "a person looks at it", not "write a rule". A `truncated`
    finding is not rule-worthy either, and for the same reason one step further in: its generation
    collapsed mid-sentence, so a `rule_worthy: true` that happens to sit before the cut is a flag
    raised by a reply that then lost its thread - and a rule is what outlives the batch.
    """
    finding = record.get("finding") or {}
    return (finding.get("rule_worthy") is True and finding.get("verdict") == "DEFECT"
            and not finding.get("truncated"))


def summarize(records) -> dict:
    """Group findings so a person can see classes and one-offs apart.

    This is the shape the requirement actually needs: a rule is built for a class and a one-off is
    discussed on its own, and the two must not be counted together. A note whose verdict or code
    could not be classified is listed separately rather than dropped, because it still says where
    the model thought the problem was, and that is worth reading even when the label is missing.
    """
    classes = {}
    one_offs = []
    unclassified = []
    for record in records:
        if record.get("_unparseable"):
            continue
        finding = record.get("finding") or {}
        verdict = finding.get("verdict")
        if verdict not in VERDICTS:
            unclassified.append({"candidate_key": record.get("candidate_key"),
                                 "where": finding.get("where"), "fix": finding.get("fix"),
                                 "what_reported": finding.get("what_reported"),
                                 "verdict_reported": finding.get("verdict_reported")})
            continue
        if is_rule_worthy(record):
            entry = classes.setdefault(finding.get("what") or "UNKNOWN", [])
            entry.append(record.get("candidate_key"))
        elif verdict == "DEFECT":
            one_offs.append({"candidate_key": record.get("candidate_key"),
                             "what": finding.get("what"),
                             "where": finding.get("where"),
                             "fix": finding.get("fix")})
    return {"classes": classes, "one_offs": one_offs, "unclassified": unclassified,
            "not_extraction": [r.get("candidate_key") for r in records
                               if (r.get("finding") or {}).get("verdict") == "NOT_EXTRACTION"],
            "failed": [r.get("candidate_key") for r in records if r.get("error")]}
