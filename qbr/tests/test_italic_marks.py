# -*- coding: utf-8 -*-
"""紙本斜體字與收錄文字的對不對得起來（業主 2026-09-25：「檢查範圍要覆蓋到以前審過的所有題目」）。

三條要守的東西，每一條都配一個負控制：

1. **斜體是量出來的，不是問出來的。** 紙本自己說哪幾個字是斜體（字型、PyMuPDF 的 `flags`），
   所以這一條不必模型參與；反過來說，收錄的文字沒有 `<i>` 就是漏了排版——那是待辦，不是「沒有
   這種事」。站上實測：那一本微生物學紙本有 157 個 `Helvetica-Oblique` 片段，而全佇列 79,090 列
   的收錄文字一個 `<i>` 都沒有。
2. **`marked` 只認整段。** 紙本斜體的是整個學名，包住半段不是同一個主張；找不到就 `missing`，
   不推論（抽取器可能拆行或改了字）。
3. **全庫都掃，不分已審／未審。** 這是業主的原話：以前審過的題目也要被檢查，所以人口是整份候選檔。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG / "src"))
sys.path.insert(0, str(PKG / "scripts"))

import scan_italic_marks as marks  # noqa: E402
import confirm_dispute  # noqa: E402
from qbr import extract  # noqa: E402


def _pdf(tmp_path, text="Streptococcus pyogenes 平板上有…", italic=True):
    module = __import__("pymu" + "pdf")
    document = module.open()
    page = document.new_page()
    page.insert_text((72, 100), text, fontsize=12, fontname="heit" if italic else "helv")
    path = tmp_path / "paper.pdf"
    document.save(str(path))
    document.close()
    return str(path)


def test_italic_spans_come_from_the_font_not_from_the_reading(tmp_path):
    spans = extract.italic_spans_a(_pdf(tmp_path))
    assert spans, "斜體那段要量得到（`heit`＝Helvetica-Oblique）"
    assert any("Streptococcus" in span["text"] for span in spans[1]), spans
    assert any("oblique" in span["font"].lower() or "italic" in span["font"].lower()
               for span in spans[1]), spans[1]


def test_the_negative_control_upright_text_is_not_measured_as_italic(tmp_path):
    """負對照：同一段字用直立字型 ⇒ 一個斜體 span 都不該有（否則整條檢查沒有意義）。"""
    assert extract.italic_spans_a(_pdf(tmp_path, italic=False)) == {}


def test_a_run_inside_the_italic_tag_is_marked_and_a_bare_one_is_the_work_list():
    field = "下列何管為<i>Bacteroides</i> spp. 在液體培養基生長情形？"
    assert marks.mark_state(field, "Bacteroides") == "marked"
    assert marks.mark_state("下列何管為Bacteroides spp. 生長情形？", "Bacteroides") == "unmarked"


def test_the_negative_control_half_a_run_inside_the_tag_is_not_marked():
    """包住半段不是同一個主張：紙本斜體的是整個學名。"""
    assert marks.mark_state("為<i>Bacteroid</i>es spp. 生長？", "Bacteroides") == "unmarked"


def test_a_run_the_field_does_not_carry_is_missing_rather_than_a_todo():
    """抽取器可能把它拆行或改了字：找不到就不推論，這一種自己一格。"""
    assert marks.mark_state("為 Bactero spp. 生長？", "Bacteroides") == "missing"
    assert marks.mark_state("", "Bacteroides") == "missing"


def test_only_the_spans_overlapping_this_questions_own_rows_are_counted():
    spans = {1: [{"text": "上面的題", "bbox": [10.0, 10.0, 100.0, 30.0], "font": "Helvetica-Oblique"},
                 {"text": "這一題自己的", "bbox": [10.0, 150.0, 100.0, 170.0],
                  "font": "Helvetica-Oblique"},
                 {"text": "下面的題", "bbox": [10.0, 400.0, 100.0, 420.0], "font": "Helvetica-Oblique"}]}
    assert marks.italic_runs_in_band(spans, {1: (100.0, 200.0)}) == ["這一題自己的"]
    assert marks.italic_runs_in_band(spans, {}) == []
    assert marks.italic_runs_in_band(spans, {2: (100.0, 200.0)}) == []


def test_the_state_is_asked_per_field_so_the_apply_step_knows_which_one():
    """一列的題幹與 A–D 是四個不同的地方；套用那一半是按欄換字，所以要記下是哪一欄。"""
    fields = {"stem": "為 Bacteroides spp.", "option A": "為<i>Bacteroides</i> spp.",
              "option B": "為 Bactero spp."}
    assert marks.field_state(fields, "Bacteroides") == ("marked", "option A")
    assert marks.field_state({"stem": "為 Bacteroides spp."}, "Bacteroides") == ("unmarked", "stem")
    assert marks.field_state({"stem": "為 Bactero spp."}, "Bacteroides") == ("missing", None)


def test_the_detail_file_has_one_line_per_question_with_italics(tmp_path, capsys):
    """下游要的是「哪些題、哪一欄、幾個字」，不是二十個樣本 ⇒ 逐列寫一份 JSONL。"""
    review = tmp_path / "review-ui"
    review.mkdir()
    rows = [{"candidate_key": "k-1", "question_number": 1, "stem": "為 Bacteroides spp. 生長？"},
            {"candidate_key": "k-2", "question_number": 2, "stem": "沒有斜體的題"}]
    (review / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    pdf = _pdf(tmp_path, text="第1題 第2題")

    original = (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
                marks.band_extents)
    detail = tmp_path / "detail.jsonl"
    try:
        confirm_dispute.paper_pdf_of = lambda row: pdf
        extract.italic_spans_a = lambda path: {
            1: [{"text": "Bacteroides", "bbox": [10.0, 10.0, 100.0, 30.0], "font": "Helvetica-Oblique"}]}
        extract.extract_cells_a = lambda path: []
        # 第 2 題量不到自己的列 ⇒ 它根本不會被算成「有斜體」（這一支只算落在這一題列裡的片段）。
        marks.band_extents = lambda cells, number: ({1: (0.0, 100.0)} if int(number) == 1 else {})
        marks.scan(marks.parse_args(["--queue", str(tmp_path), "--detail", str(detail)]))
    finally:
        (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
         marks.band_extents) = original

    lines = [json.loads(line) for line in detail.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1, lines                       # 只有第 1 題有斜體 ⇒ 只有一行
    assert lines[0]["candidate_key"] == "k-1"
    assert lines[0]["runs"] == [{"text": "Bacteroides", "state": "unmarked", "field": "stem"}]
    assert lines[0]["unmarked"] == 1 and lines[0]["missing"] == 0
    assert lines[0]["longest_unmarked"] == len("Bacteroides")
    assert "逐列明細寫到" in capsys.readouterr().out


def test_the_census_reads_every_row_not_only_the_blocked_ones(tmp_path, capsys):
    """業主的範圍：以前審過的題目也要被檢查——人口是整份候選檔，一列都不會被題號或狀態濾掉。"""
    review = tmp_path / "review-ui"
    review.mkdir()
    rows = [{"candidate_key": "k-1", "question_number": 1, "stem": "已經審過的題 Streptococcus"},
            {"candidate_key": "k-2", "question_number": 2, "stem": "沒審過的題 Streptococcus"}]
    (review / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    pdf = _pdf(tmp_path, text="第1題 第2題")

    banded = []
    original = (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
                marks.band_extents)
    try:
        confirm_dispute.paper_pdf_of = lambda row: pdf
        extract.italic_spans_a = lambda path: {1: [{"text": "Streptococcus",
                                                    "bbox": [10.0, 10.0, 100.0, 30.0],
                                                    "font": "Helvetica-Oblique"}]}
        extract.extract_cells_a = lambda path: []
        marks.band_extents = lambda cells, number: banded.append(int(number)) or {1: (0.0, 100.0)}
        marks.scan(marks.parse_args(["--queue", str(tmp_path)]))
    finally:
        (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
         marks.band_extents) = original

    assert banded == [1, 2], banded          # 兩列都被看過（不是只有 block 的那一批）
    out = capsys.readouterr().out
    assert "掃過 2 列" in out, out
    assert "未標（待辦）        2" in out, out


def test_the_safe_rule_is_one_occurrence_and_the_ambiguous_run_is_left_to_a_person():
    """業主的範圍是**全部題目**；這裡擋的是方法做不到的那一部分，而且逐段列出來。

    `mark_state` 是子字串比對，所以一個字的片段（站上樣本裡有 479 個）在題幹出現兩次以上時，
    插在哪一個上面是猜的——猜錯就是把別的字變成斜體。
    """
    text, applied, left = marks.apply_runs("為 Bacteroides spp. 生長？", ["Bacteroides"])
    assert text == "為 <i>Bacteroides</i> spp. 生長？"
    assert applied == ["Bacteroides"] and left == []

    # 大小寫不折疊（`E` 不是 `e`）：兩次小寫才算模糊。
    text, applied, left = marks.apply_runs("e 與 e 的差別", ["e"])
    assert text == "e 與 e 的差別" and applied == [] and left == ["e"]
    assert marks.apply_runs("E 與 e 的差別", ["e"])[1] == ["e"]

    # 已經在 `<i>in situ</i>` 裡面的那一個 `in` 不能當成目標（不疊標記）；標記**外面**那個才是
    # 紙本這一段斜體指的地方。
    text, applied, left = marks.apply_runs("<i>in situ</i> 與 in 的差別", ["in"])
    assert text == "<i>in situ</i> 與 <i>in</i> 的差別", text
    assert applied == ["in"] and left == []


def test_the_longer_run_is_marked_before_the_shorter_one_that_contains_it():
    """`K'` 要在 `K` 之前處理，否則 `K` 會先被包起來、`K'` 就找不到乾淨的字串了。"""
    text, applied, left = marks.apply_runs("速率常數 K' 與 K 的關係", ["K", "K'"])
    assert text == "速率常數 <i>K'</i> 與 <i>K</i> 的關係", text
    assert sorted(applied) == ["K", "K'"] and left == []


def test_the_apply_mode_writes_the_markup_the_field_and_one_machine_event(tmp_path, capsys):
    """套用那一半：欄位改的是**同一段字加標記**，事件是一筆機器的 `reset_review`（不是人的決定）。"""
    review = tmp_path / "review-ui"
    review.mkdir()
    rows = [{"candidate_key": "k-1", "question_number": 1,
             "stem": "為 Bacteroides spp. 生長？",
             "options": [{"key": "A", "text": "沒有斜體"},
                         {"key": "B", "text": "Streptococcus 的培養"}]}]
    (review / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    pdf = _pdf(tmp_path, text="第1題")

    original = (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
                marks.band_extents)
    try:
        confirm_dispute.paper_pdf_of = lambda row: pdf
        extract.italic_spans_a = lambda path: {
            1: [{"text": "Bacteroides", "bbox": [10.0, 10.0, 100.0, 30.0],
                 "font": "Helvetica-Oblique"},
                {"text": "Streptococcus", "bbox": [10.0, 50.0, 100.0, 70.0],
                 "font": "Helvetica-Oblique"}]}
        extract.extract_cells_a = lambda path: []
        marks.band_extents = lambda cells, number: {1: (0.0, 100.0)}
        marks.scan(marks.parse_args(["--queue", str(tmp_path), "--apply"]))
    finally:
        (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
         marks.band_extents) = original

    written = json.loads((review / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert written["stem"] == "為 <i>Bacteroides</i> spp. 生長？", written["stem"]
    assert written["options"][1]["text"] == "<i>Streptococcus</i> 的培養", written["options"]
    assert list(review.glob("candidates.jsonl.before-*")), "寫回前要先備份"

    events = [json.loads(line) for line in
              (review / "question_review_events.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    assert len(events) == 1, events
    event = events[0]
    assert event["action"] == "reset_review"
    assert event["reviewer"] == "repair_italic_markup"      # 機器，不是人（AGENTS.md）
    assert event["candidate_key"] == "k-1"
    assert sorted(change["field"] for change in event["changes"]) == ["option B", "stem"]
    assert event["correction"]["stem"] == "為 <i>Bacteroides</i> spp. 生長？"
    assert "斜體" in event["experience"]["why"]


def test_the_line_a_span_sits_on_says_which_field_owns_it():
    """歸屬那一半的判準：紙本那一列的文字落在哪一欄（不是印象）。"""
    fields = {"stem": "下列何者為 Bacteroides spp. 的生長條件？",
              "option A": "anaerobic", "option B": "Bacteroides 在 37°C 生長"}
    assert marks.line_owner("Bacteroides 在 37°C 生長", fields, "option B")[0] == "line-matches-the-field"
    verdict, owner = marks.line_owner("Bacteroides 在 37°C 生長", fields, "stem")
    assert verdict == "line-belongs-to-another-field" and owner == "option B", (verdict, owner)
    assert marks.line_owner("紙本上別的行", fields, "stem")[0] == "line-text-not-in-any-field"
    assert marks.line_owner("", fields, "stem")[0] == "line-not-measured"


def test_the_revert_moves_a_tag_written_on_the_wrong_field(tmp_path, capsys):
    """業主 2026-09-25：「斜體有些是誤植，你怎麼解決。」

    錯的形狀：`<i>` 寫在 option C，可是紙本那一列的文字屬於 option B。修法是把標記從錯的欄位
    拿掉、放進紙本那一列所在的欄位，而且記一筆機器的 `reset_review`（append-only，不塗改歷史）。
    """
    review = tmp_path / "review-ui"
    review.mkdir()
    rows = [{"candidate_key": "k-1", "question_number": 1,
             "stem": "下列何者為 Bacteroides spp. 的生長條件？",
             "options": [{"key": "A", "text": "anaerobic"},
                         {"key": "B", "text": "Bacteroides 在 37°C 生長"},
                         {"key": "C", "text": "<i>Bacteroides</i> 是革蘭氏陰性"}]}]
    (review / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    pdf = _pdf(tmp_path, text="第1題")

    original = (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
                marks.band_extents, marks.reread.band_rows)
    try:
        confirm_dispute.paper_pdf_of = lambda row: pdf
        extract.italic_spans_a = lambda path: {
            1: [{"text": "Bacteroides", "bbox": [10.0, 10.0, 100.0, 30.0],
                 "font": "Helvetica-Oblique"}]}
        extract.extract_cells_a = lambda path: []
        marks.band_extents = lambda cells, number: {1: (0.0, 100.0)}
        # 紙本上這個片段坐在「屬於 option B 的那一列」。
        marks.reread.band_rows = lambda cells, number: [
            {"page": 1, "y0": 5.0, "y1": 35.0, "text": "Bacteroides 在 37°C 生長"}]
        marks.scan(marks.parse_args(["--queue", str(tmp_path), "--verify", "-", "--revert"]))
    finally:
        (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
         marks.band_extents, marks.reread.band_rows) = original

    written = json.loads((review / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert written["options"][2]["text"] == "Bacteroides 是革蘭氏陰性", written["options"][2]
    assert written["options"][1]["text"] == "<i>Bacteroides</i> 在 37°C 生長", written["options"][1]
    events = [json.loads(line) for line in
              (review / "question_review_events.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    assert len(events) == 1 and events[0]["reviewer"] == "repair_italic_markup", events
    assert "歸屬" in (events[0].get("notes") or ""), events[0]
    assert "歸屬錯的已修：1 個片段（移到紙本那一列所在的欄：1）" in capsys.readouterr().out


def _revert_fixture(tmp_path, *, owner_text):
    review = tmp_path / "review-ui"
    review.mkdir()
    rows = [{"candidate_key": "k-1", "question_number": 1,
             "stem": "下列何者為 Bacteroides spp. 的生長條件？",
             "options": [{"key": "A", "text": "anaerobic"},
                         {"key": "B", "text": owner_text},
                         {"key": "C", "text": "<i>Bacteroides</i> 是革蘭氏陰性"}]}]
    (review / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return review


def test_a_tag_the_papers_own_position_can_place_is_moved(tmp_path, capsys):
    """**紙本自己有位置**：那一列的文字在 owner 欄裡唯一時，片段的位置＝列的位置＋片段在列裡的位移。

    那一段字在 owner 欄出現兩次以上時，「只出現一次」那一條放不回去——站上 53 個片段裡有 52 個是這個
    形狀（`owner` 指到 `stem`）。紙本那一列在 owner 欄裡唯一就夠了：位移算得出來，位置就唯一。
    """
    review = _revert_fixture(tmp_path, owner_text="Bacteroides 與 Bacteroides 在 37°C 生長")
    pdf = _pdf(tmp_path, text="第1題")
    original = (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
                marks.band_extents, marks.reread.band_rows)
    try:
        confirm_dispute.paper_pdf_of = lambda row: pdf
        extract.italic_spans_a = lambda path: {
            1: [{"text": "Bacteroides", "bbox": [10.0, 10.0, 100.0, 30.0],
                 "font": "Helvetica-Oblique"}]}
        extract.extract_cells_a = lambda path: []
        marks.band_extents = lambda cells, number: {1: (0.0, 100.0)}
        # 紙本上這個片段坐在「Bacteroides 在 37°C 生長」那一列；那一列在 owner 欄（option B）裡
        # 出現一次，而 `Bacteroides` 在那一欄出現兩次 ⇒ 位置由「列＋位移」決定，不是猜第一個。
        marks.reread.band_rows = lambda cells, number: [
            {"page": 1, "y0": 5.0, "y1": 35.0, "text": "Bacteroides 在 37°C 生長"}]
        marks.scan(marks.parse_args(["--queue", str(tmp_path), "--verify", "-", "--revert"]))
    finally:
        (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
         marks.band_extents, marks.reread.band_rows) = original

    written = json.loads((review / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert written["options"][2]["text"] == "Bacteroides 是革蘭氏陰性", written["options"][2]
    assert written["options"][1]["text"] == "Bacteroides 與 <i>Bacteroides</i> 在 37°C 生長", \
        written["options"][1]
    assert "歸屬錯的已修：1 個片段（移到紙本那一列所在的欄：1）" in capsys.readouterr().out


def test_a_tag_that_cannot_be_put_back_is_left_alone(tmp_path, capsys):
    """**放不回去就不要拿掉**（站上實測 2026-09-25 21:50 的真事故）。

    第一次跑 `--revert` 時，53 個片段的 `owner` 都指到別欄而**放不回去**（那一列在 owner 欄裡不唯一），
    舊碼仍然把標記從錯的欄位拿掉 ⇒ 53 個紙本真的有的斜體被刪掉、0 個被放回，摘要卻印
    「歸屬錯的已修：53」。正解：有疑慮的那一段留在 `--verify` 的明細裡給人看，**不動**。
    """
    review = _revert_fixture(
        tmp_path, owner_text="Bacteroides 在 37°C 生長；Bacteroides 在 37°C 生長 是對的")
    pdf = _pdf(tmp_path, text="第1題")
    original = (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
                marks.band_extents, marks.reread.band_rows)
    try:
        confirm_dispute.paper_pdf_of = lambda row: pdf
        extract.italic_spans_a = lambda path: {
            1: [{"text": "Bacteroides", "bbox": [10.0, 10.0, 100.0, 30.0],
                 "font": "Helvetica-Oblique"}]}
        extract.extract_cells_a = lambda path: []
        marks.band_extents = lambda cells, number: {1: (0.0, 100.0)}
        marks.reread.band_rows = lambda cells, number: [
            {"page": 1, "y0": 5.0, "y1": 35.0, "text": "Bacteroides 在 37°C 生長"}]
        marks.scan(marks.parse_args(["--queue", str(tmp_path), "--verify", "-", "--revert"]))
    finally:
        (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
         marks.band_extents, marks.reread.band_rows) = original

    written = json.loads((review / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert written["options"][2]["text"] == "<i>Bacteroides</i> 是革蘭氏陰性", written["options"][2]
    assert not (review / "question_review_events.jsonl").exists(), "沒有動就不該記事件"
    out = capsys.readouterr().out
    assert "歸屬錯的已修：0 個片段" in out and "放不回去所以沒動它" in out


def test_the_move_does_not_wrap_a_tag_that_is_already_there(tmp_path, capsys):
    """第二趟跑同一條規則時不要包成 `<i><i>X</i></i>`：owner 欄已經帶著這個標記 ⇒ 不再動它。"""
    review = _revert_fixture(tmp_path, owner_text="<i>Bacteroides</i> 在 37°C 生長")
    pdf = _pdf(tmp_path, text="第1題")
    original = (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
                marks.band_extents, marks.reread.band_rows)
    try:
        confirm_dispute.paper_pdf_of = lambda row: pdf
        extract.italic_spans_a = lambda path: {
            1: [{"text": "Bacteroides", "bbox": [10.0, 10.0, 100.0, 30.0],
                 "font": "Helvetica-Oblique"}]}
        extract.extract_cells_a = lambda path: []
        marks.band_extents = lambda cells, number: {1: (0.0, 100.0)}
        marks.reread.band_rows = lambda cells, number: [
            {"page": 1, "y0": 5.0, "y1": 35.0, "text": "Bacteroides 在 37°C 生長"}]
        marks.scan(marks.parse_args(["--queue", str(tmp_path), "--verify", "-", "--revert"]))
    finally:
        (confirm_dispute.paper_pdf_of, extract.italic_spans_a, extract.extract_cells_a,
         marks.band_extents, marks.reread.band_rows) = original

    written = json.loads((review / "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert written["options"][2]["text"] == "<i>Bacteroides</i> 是革蘭氏陰性"
    assert written["options"][1]["text"] == "<i>Bacteroides</i> 在 37°C 生長"
    assert "<i><i>" not in json.dumps(written, ensure_ascii=False)
