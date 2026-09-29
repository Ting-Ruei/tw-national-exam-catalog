"""每一張圖都必須落在**這一題自己的列**裡（站上實測：3,263 張圖有 844 張沒有）。

The figure stage cut a picture object wherever the page put it, so a crop could be of the question
above or below - and a reviewer looking at "this question" was shown a neighbouring question's
picture while the crop looked complete. These tests hold the fix: the box is clipped to the rows the
skeleton gave this question, a box that never reaches those rows is **not attached at all**, and the
clip is *reported* rather than hidden.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import confirm_dispute  # noqa: E402
import crop_run_figures  # noqa: E402


def cell(page, y0, y1, x0=70.0, x1=500.0, text="row"):
    return {"page": page, "y0": y0, "y1": y1, "x0": x0, "x1": x1, "text": text}


BAND = {1: (100.0, 200.0)}


class ClipToBandTests(unittest.TestCase):
    def test_a_box_inside_the_questions_own_rows_is_left_alone(self):
        box = [72.0, 110.0, 400.0, 190.0]
        clipped, above, below = crop_run_figures.clip_to_band(1, box, BAND)
        self.assertEqual(clipped, [72.0, 110.0, 400.0, 190.0])
        self.assertEqual((above, below), (0.0, 0.0))

    def test_a_box_that_reaches_into_the_question_above_is_cut_and_says_how_much(self):
        """負對照＝舊行為：舊碼會把整個 40pt 的框都切下來，鄰題的那一段也在裡面。"""
        box = [39.1, 0.0, 304.6, 282.9]
        clipped, above, below = crop_run_figures.clip_to_band(1, box, BAND)
        self.assertEqual(clipped, [39.1, 100.0, 304.6, 200.0])
        self.assertEqual(above, 100.0)   # 上面那 100pt 是上一題的
        self.assertEqual(below, 82.9)    # 下面那 82.9pt 是下一題的

    def test_a_box_that_never_reaches_these_rows_is_not_this_question_s_picture(self):
        self.assertIsNone(crop_run_figures.clip_to_band(1, [39.1, 300.0, 304.6, 400.0], BAND))

    def test_a_box_on_another_page_or_no_measured_rows_is_not_clipped_into_place(self):
        self.assertIsNone(crop_run_figures.clip_to_band(2, [39.1, 110.0, 304.6, 190.0], BAND))
        self.assertIsNone(crop_run_figures.clip_to_band(1, [39.1, 110.0, 304.6, 190.0], {}))


class ClipKeepingPicturesTests(unittest.TestCase):
    """裁到這一題的列時**不切過一張圖**：跨列界的圖整張留著（業主 2026-09-25 的「截圖不完整」）。

    紙本自己回答這一題：`vision.picture_boxes` 量到的圖物件（相鄰切片已合成一張）。一端落在
    這一題的列裡的圖，就是這一題的圖被列界切到；兩端都不在的圖是整張屬於隔壁題的，不補。
    """

    #: 這一題的列（第 1 頁 y 100–200）、一張從列裡開始往下畫到 300 的圖，以及一張整張在列下面的圖。
    BAND = {1: (100.0, 200.0)}
    OWN_FIGURE = (39.1, 150.0, 304.6, 300.0)
    NEIGHBOURS_FIGURE = (39.1, 260.0, 304.6, 400.0)

    def test_a_picture_that_starts_in_this_band_is_not_cut_in_half(self):
        box = [39.1, 150.0, 304.6, 300.0]
        clipped, above, below, extended = crop_run_figures.clip_keeping_pictures(
            1, box, self.BAND, [self.OWN_FIGURE, self.NEIGHBOURS_FIGURE])
        # 列界在 200：框被裁到 200，然後因為紙本的圖物件到 300，整張補回來。
        self.assertEqual(clipped, [39.1, 150.0, 304.6, 300.0])
        self.assertEqual((above, below), (0.0, 100.0))
        self.assertEqual(extended, [39.1, 150.0, 304.6, 300.0])

    def test_the_negative_control_a_neighbours_picture_entirely_below_stays_clipped(self):
        """負對照：整張在這一題的列下面的圖不補——掛上鄰題的圖正是 2026-09-24 修掉的那個缺陷。"""
        box = [39.1, 190.0, 304.6, 400.0]
        clipped, above, below, extended = crop_run_figures.clip_keeping_pictures(
            1, box, self.BAND, [self.NEIGHBOURS_FIGURE])
        self.assertEqual(clipped, [39.1, 190.0, 304.6, 200.0])
        self.assertEqual(below, 200.0)
        self.assertIsNone(extended)

    def test_the_negative_control_a_box_that_already_is_the_whole_picture_is_left_alone(self):
        box = [39.1, 110.0, 304.6, 190.0]
        clipped, above, below, extended = crop_run_figures.clip_keeping_pictures(
            1, box, self.BAND, [(39.1, 110.0, 304.6, 190.0)])
        self.assertEqual(clipped, box)
        self.assertEqual((above, below), (0.0, 0.0))
        self.assertIsNone(extended)

    def test_a_crop_that_was_already_cut_small_is_lifted_back_to_the_whole_picture(self):
        """已受損的列也修得回來：框在列內、但紙本的圖比它大 ⇒ 那就是被裁過的痕跡。"""
        box = [39.1, 150.0, 304.6, 190.0]
        clipped, above, below, extended = crop_run_figures.clip_keeping_pictures(
            1, box, self.BAND, [self.OWN_FIGURE])
        self.assertEqual(clipped, [39.1, 150.0, 304.6, 300.0])
        self.assertEqual((above, below), (0.0, 0.0))
        self.assertEqual(extended, [39.1, 150.0, 304.6, 300.0])

    def test_a_picture_that_ends_in_this_band_but_starts_above_is_grown_upwards(self):
        box = [39.1, 120.0, 304.6, 180.0]
        picture = (39.1, 20.0, 304.6, 180.0)
        clipped, above, below, extended = crop_run_figures.clip_keeping_pictures(
            1, box, self.BAND, [picture])
        self.assertEqual(clipped, [39.1, 20.0, 304.6, 180.0])
        self.assertEqual((above, below), (0.0, 0.0))
        self.assertEqual(extended, [39.1, 20.0, 304.6, 180.0])

    def test_a_box_that_never_reaches_the_band_is_still_not_this_questions_picture(self):
        self.assertIsNone(crop_run_figures.clip_keeping_pictures(
            1, [39.1, 300.0, 304.6, 400.0], self.BAND, [self.NEIGHBOURS_FIGURE]))


class PictureCrossingTests(unittest.TestCase):
    """說明那一句話的數字來自**圖**，不是來自框（框是上一次裁過的版本）。"""

    def test_the_picture_under_a_box_is_the_one_it_overlaps_most(self):
        pictures = [(39.1, 20.0, 304.6, 60.0), (39.1, 150.0, 304.6, 300.0)]
        self.assertEqual(crop_run_figures.picture_of_box(pictures, [39.1, 160.0, 304.6, 200.0]),
                         [39.1, 150.0, 304.6, 300.0])

    def test_the_negative_control_a_box_on_no_picture_has_no_picture(self):
        self.assertIsNone(crop_run_figures.picture_of_box([(39.1, 20.0, 304.6, 60.0)],
                                                         [39.1, 300.0, 304.6, 400.0]))
        self.assertIsNone(crop_run_figures.picture_of_box([], [39.1, 300.0, 304.6, 400.0]))

    def test_the_crossing_is_measured_against_this_questions_own_rows(self):
        picture = [39.1, 405.0, 304.6, 500.0]
        self.assertEqual(crop_run_figures.picture_crossing(picture, (400.0, 440.0)), (0.0, 60.0))
        self.assertEqual(crop_run_figures.picture_crossing(picture, (410.0, 520.0)), (5.0, 0.0))
        # 列比圖窄的兩邊都算：圖 405–500 對上列 420–480 ⇒ 上面越過 15、下面越過 20。
        self.assertEqual(crop_run_figures.picture_crossing(picture, (420.0, 480.0)), (15.0, 20.0))
        self.assertEqual(crop_run_figures.picture_crossing(None, (400.0, 440.0)), (0.0, 0.0))


class BandExtentsTests(unittest.TestCase):
    def test_the_band_is_the_union_of_this_questions_own_rows_per_page(self):
        rows = [cell(1, 100.0, 120.0, text="1.某藥的敘述如下"), cell(1, 180.0, 200.0),
                cell(2, 50.0, 70.0), cell(2, 300.0, 320.0, text="2.下一題")]
        self.assertEqual(crop_run_figures.band_extents(rows, 1), {1: (100.0, 200.0), 2: (50.0, 70.0)})
        self.assertEqual(crop_run_figures.band_extents(rows, 2), {2: (300.0, 320.0)})
        self.assertEqual(crop_run_figures.band_extents([], 1), {})


class ClipEntryTests(unittest.TestCase):
    def test_the_neighbour_s_picture_is_dropped_and_the_question_s_own_is_kept(self):
        entry = {"page": 1, "box": [0.0, 0.0, 600.0, 842.0],
                 "figure_boxes": [[39.1, 110.0, 300.0, 150.0], [39.1, 300.0, 300.0, 340.0]],
                 "reasons": ["embedded-image"]}
        clipped, notes = crop_run_figures.clip_entry_to_band(entry, BAND)
        self.assertIsNotNone(clipped)
        self.assertEqual(clipped["figure_boxes"], [[39.1, 110.0, 300.0, 150.0]])
        self.assertEqual(clipped["box"], [39.1, 110.0, 300.0, 150.0])
        self.assertEqual(notes, [{"box": [39.1, 300.0, 300.0, 340.0],
                                  "dropped": "outside-this-question"}])

    def test_an_entry_whose_pictures_are_all_elsewhere_yields_no_crop_at_all(self):
        """負對照＝舊行為：舊碼會把 [[39.1, 300.0, 300.0, 340.0]] 這一張切下來掛到這一題。"""
        entry = {"page": 1, "box": [0.0, 0.0, 600.0, 842.0],
                 "figure_boxes": [[39.1, 300.0, 300.0, 340.0]], "reasons": ["embedded-image"]}
        clipped, notes = crop_run_figures.clip_entry_to_band(entry, BAND)
        self.assertIsNone(clipped)
        self.assertEqual(len(notes), 1)

    def test_an_entry_with_only_a_region_box_is_clipped_by_that_box(self):
        entry = {"page": 1, "box": [39.1, 0.0, 304.6, 282.9], "reasons": ["embedded-image"]}
        clipped, notes = crop_run_figures.clip_entry_to_band(entry, BAND)
        self.assertEqual(clipped["box"], [39.1, 100.0, 304.6, 200.0])
        self.assertEqual(notes, [])


class SliverRecordTests(unittest.TestCase):
    """裁完只剩一小片的框不能留：站上實測（`1152_藥師(一)_藥學(一)` 第 42 題）存活 11pt。

    那一題自己的列裡只有三個選項標記 `B.`/`C.`/`D.`（x 39.2-50.3），旁邊的選項圖到 x=158.4，所以
    裁完只剩 11pt 的文字條；那 32,331 bytes 被當成這一題的圖送給模型，模型說選項是空白，這一題就
    答了 `▢`。一張看不清的圖比沒有圖更糟，因為它看起來像紙本的答案。
    """

    def test_a_crop_that_survives_11pt_of_its_band_is_a_sliver(self):
        record = crop_run_figures.sliver_record([39.2, 100.0, 158.4, 511.0],
                                                [39.2, 500.0, 158.4, 511.0])
        self.assertEqual(record["dropped"], "sliver")
        self.assertEqual(record["why"], "surviving-height-under-floor")
        self.assertEqual(record["surviving"], 11.0)
        self.assertEqual(record["box"], [39.2, 500.0, 158.4, 511.0])     # 存活的那一段
        self.assertEqual(record["was"], [39.2, 100.0, 158.4, 511.0])     # 量到的原框
        # 負對照＝舊行為：舊碼把這個框重畫成圖留下來，`image_refs` 照樣帶著它。

    def test_a_crop_that_survives_100pt_of_a_120pt_box_is_kept(self):
        self.assertIsNone(crop_run_figures.sliver_record([50.0, 500.0, 400.0, 620.0],
                                                         [50.0, 500.0, 400.0, 600.0]))

    def test_a_figure_that_is_only_30pt_tall_is_still_a_figure(self):
        """地板是 24pt，不是「要多大才算圖」：一張 30pt 的真圖整張留在自己的列裡就要留著。

        負對照＝地板調高：把 `SLIVER_MIN_HEIGHT` 調到 40 就會把這張圖當 sliver 丟掉。
        """
        self.assertIsNone(crop_run_figures.sliver_record([50.0, 100.0, 400.0, 130.0],
                                                         [50.0, 100.0, 400.0, 130.0]))

    def test_a_crop_that_survives_less_than_half_of_its_box_is_a_sliver_whatever_the_floor(self):
        """30pt 不是 0，但它是一張 200pt 的圖被切掉 85% 之後剩下的。"""
        record = crop_run_figures.sliver_record([50.0, 300.0, 400.0, 500.0],
                                                [50.0, 470.0, 400.0, 500.0])
        self.assertEqual(record["why"], "surviving-height-under-half-the-box")
        self.assertEqual(record["surviving"], 30.0)

    def test_a_slice_that_holds_a_picture_is_the_questions_own_figure_not_a_strip(self):
        """半個框的規則不能只看框有多高：那個框可能是舊的重疊規則撐大的。

        站上實測（2026-09-25，`--fix-figure-ownership` 3,297 張圖）：95 張被判 sliver 的框裡，
        **44** 張留下來的那一片**裡面有圖**（存活 42-138pt），另外 50 張是 10-17pt 的文字條。那 44
        張是「這一題自己的列＋自己的圖」被下面那一題的圖撐大了原框，砍掉等於把 44 題的圖弄不見。

        負對照＝沒有這條判斷的舊行為：同一片 138pt 的文字／圖會被當 sliver 丟掉。
        """
        self.assertIsNone(crop_run_figures.sliver_record(
            [47.0, 360.7, 549.2, 754.7], [47.0, 360.7, 549.2, 498.9],
            [(7, [100.0, 400.0, 500.0, 460.0])]))          # 圖跨進留下來的那一片

    def test_a_slice_with_no_picture_in_it_is_still_a_sliver(self):
        """同樣的高度、同樣的比例，但那一片裡只有字（或那張圖完全在裁掉的部分）⇒ 一樣不留。"""
        record = crop_run_figures.sliver_record(
            [47.0, 360.7, 549.2, 754.7], [47.0, 360.7, 549.2, 498.9],
            [(7, [100.0, 600.0, 500.0, 700.0])])           # 圖在裁掉的那一段裡
        self.assertEqual(record["why"], "surviving-height-under-half-the-box")

    def test_a_picture_that_only_grazes_the_slice_does_not_save_it(self):
        """8pt 是「這張圖真的在留下來的那一片裡」，不是「擦到邊」。

        留下來的那一片是 y 360.7-498.9：圖從 490 開始 ⇒ 跨進來 8.9pt（算在裡面）；從 492 開始
        ⇒ 只有 6.9pt（不算）。
        """
        box, clipped = [47.0, 360.7, 549.2, 754.7], [47.0, 360.7, 549.2, 498.9]
        self.assertIsNone(crop_run_figures.sliver_record(box, clipped, [(7, [100.0, 490.0, 500.0, 600.0])]))
        self.assertEqual(crop_run_figures.sliver_record(box, clipped, [(7, [100.0, 492.0, 500.0, 600.0])])["why"],
                         "surviving-height-under-half-the-box")

    def test_a_fragment_of_a_picture_under_the_floor_is_still_dropped(self):
        """地板優先：留下來的那一片只有 11pt，裡面就算有圖也是一張被切斷的圖（看不清比沒有更糟）。"""
        record = crop_run_figures.sliver_record([39.2, 100.0, 158.4, 511.0],
                                                [39.2, 500.0, 158.4, 511.0],
                                                [(9, [50.0, 500.0, 150.0, 560.0])])
        self.assertEqual(record["why"], "surviving-height-under-floor")

    def test_a_crop_that_is_only_20pt_tall_where_it_lies_is_not_a_crop(self):
        """整張框都在這一題的列裡，但這張圖本來就太扁：低於 24pt 的地板一樣不留。"""
        record = crop_run_figures.sliver_record([50.0, 100.0, 400.0, 120.0],
                                                [50.0, 100.0, 400.0, 120.0])
        self.assertEqual(record["why"], "surviving-height-under-floor")


class OwnershipPassSliverTests(unittest.TestCase):
    """`--fix-figure-ownership` 真的把 sliver 從列的 `image_refs` 拿掉，而且看得到它為什麼被拿掉。

    走的是那支腳本本身（暫存佇列 + 換掉量列、換掉重畫），因為要驗的是三件事同時成立：ref 不見了、
    `stats` 有一個 `sliver`、摘要印得出來。只驗 `sliver_record` 的話，一個沒接上去的判斷照樣通過。
    """

    def _queue(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        review = os.path.join(root, "review-ui")
        crops = os.path.join(review, "crops", "1152_藥師(一)_藥學(一)")
        os.makedirs(crops)
        rows = [
            # 第 42 題：自己的列只有 11pt 高，量到的圖框到 y=511 - 裁完只剩 11pt。
            {"candidate_key": "key-42", "question_number": 42,
             "image_refs": [{"asset_role": "figure-crop", "page": 9, "raw_ref": "q042.png",
                             "path": "review-ui/crops/1152_藥師(一)_藥學(一)/q042.png",
                             "description": "第 42 題的圖（紙本這張圖還蓋到隔壁題：上 300pt、下 71pt，"
                                            "只切這一題的列）",
                             "box": [39.2, 100.0, 158.4, 511.0]}]},
            # 第 43 題：自己的列 100pt 高（500-600），圖框 120pt（500-620）⇒ 存活 100pt，留著。
            {"candidate_key": "key-43", "question_number": 43,
             "image_refs": [{"asset_role": "figure-crop", "page": 10, "raw_ref": "q043.png",
                             "path": "review-ui/crops/1152_藥師(一)_藥學(一)/q043.png",
                             "box": [50.0, 500.0, 400.0, 620.0]}]},
            # 第 44 題：列量不到 ⇒ 只標 `ownership: unverified`，不丟。
            {"candidate_key": "key-44", "question_number": 44,
             "image_refs": [{"asset_role": "figure-crop", "page": 11, "raw_ref": "q044.png",
                             "path": "review-ui/crops/1152_藥師(一)_藥學(一)/q044.png",
                             "box": [50.0, 200.0, 400.0, 700.0]}]},
        ]
        with open(os.path.join(review, "candidates.jsonl"), "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        for name in ("q042.png", "q043.png", "q044.png"):
            with open(os.path.join(crops, name), "wb") as handle:
                handle.write(b"before")
        return root

    def test_an_unclear_figure_directive_preserves_the_existing_crop(self):
        root = self._queue()
        review = os.path.join(root, "review-ui")
        events = [{"candidate_key": "key-42", "action": "block", "notes": "圖像還是錯的",
                   "reviewer": "local", "source": "linear_v2",
                   "created_at": "2026-09-25T10:00:00"}]
        with open(os.path.join(review, "question_review_events.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(events[0], ensure_ascii=False) + "\n")

        original = crop_run_figures.ask_about_blocks.read_figure_directive
        crop_run_figures.ask_about_blocks.read_figure_directive = (
            lambda _note, question="": ("unclear", "", "local endpoint unavailable"))
        try:
            crop_run_figures.fix_queue_figure_ownership(types.SimpleNamespace(
                queue=root, only=["key-42"], human_flagged=True))
        finally:
            crop_run_figures.ask_about_blocks.read_figure_directive = original

        with open(os.path.join(review, "candidates.jsonl"), encoding="utf-8") as handle:
            row = json.loads(handle.readline())
        assert row["image_refs"][0]["raw_ref"] == "q042.png"
        assert row["image_refs"][0]["ownership"] == "unverified"
        with open(os.path.join(review, "crops", "1152_藥師(一)_藥學(一)", "q042.png"), "rb") as handle:
            assert handle.read() == b"before"
        with open(os.path.join(review, "figure_ownership.json"), encoding="utf-8") as handle:
            report = json.load(handle)
        assert report["records"][0]["why"] == "human-directive-not-actionable"
        assert report["stats"]["unverified"] == 1

    def test_the_sliver_is_dropped_and_counted_and_the_unmeasured_question_is_only_marked(self):
        root = self._queue()
        measured = {42: {9: (500.0, 511.0)}, 43: {10: (500.0, 600.0)}}   # 44 沒量到 ⇒ 空
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    crop_run_figures.vision.crop_region, confirm_dispute.paper_pdf_of)
        try:
            crop_run_figures.band_extents = lambda cells, number: measured.get(int(number), {})
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            # 這一頁**沒有**量到圖 ⇒ 留下來的 11pt 是文字條，該丟。
            crop_run_figures.extract.extract_images_a = lambda pdf: []
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: []
            crop_run_figures.vision.crop_region = lambda *a, **k: b"png-bytes"
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             crop_run_figures.vision.crop_region, confirm_dispute.paper_pdf_of) = original

        review = os.path.join(root, "review-ui")
        with open(os.path.join(review, "candidates.jsonl"), encoding="utf-8") as handle:
            rows = {}
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    rows[row["question_number"]] = row
        self.assertEqual(rows[42]["image_refs"], [], "11pt 的框不該留在這一題身上")

        kept = rows[43]["image_refs"][0]
        self.assertEqual(kept["box"], [50.0, 500.0, 400.0, 600.0])
        self.assertEqual(kept["clipped"], {"above": 0.0, "below": 20.0})
        self.assertEqual(kept["bytes"], len(b"png-bytes"))
        with open(os.path.join(review, "crops", "1152_藥師(一)_藥學(一)", "q043.png"), "rb") as handle:
            self.assertEqual(handle.read(), b"png-bytes")

        unmeasured = rows[44]["image_refs"][0]
        self.assertEqual(unmeasured["ownership"], "unverified", "列量不到只能標記，不能丟")

        with open(os.path.join(review, "figure_ownership.json"), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["stats"]["sliver"], 1)
        self.assertEqual(report["stats"]["unverified"], 1)
        self.assertEqual(report["stats"]["clipped"], 1)
        dropped = [record for record in report["records"] if record.get("dropped") == "sliver"]
        self.assertEqual([record["number"] for record in dropped], [42])
        self.assertEqual(dropped[0]["box"], [39.2, 500.0, 158.4, 511.0])
        self.assertEqual(dropped[0]["was"], [39.2, 100.0, 158.4, 511.0])
        self.assertIn("只剩一小片（存活 < 24pt 或不到原框一半，移除） 1", printed.getvalue())

    def test_a_question_whose_figure_cannot_be_measured_gets_the_whole_question_stitched(self):
        """量不到圖物件時，不要再交出一條「題目文字列」的窄條。

        業主點名的 `q006`／`q056`／`q071` 是這個形狀：紙本的圖是向量圖（或圖物件被切成很多小片），
        `vision.picture_boxes` 一張都量不到，於是截圖＝這一題文字列的矩形，右緣與頁界把圖切掉——
        引擎回讀寫的就是「右側邊緣被截斷」「邊緣有被裁切殘留的文字」。改成**整題縫成一張**。
        """
        root = self._queue()
        review = os.path.join(root, "review-ui")
        measured = {42: {9: (500.0, 511.0)}, 43: {10: (500.0, 600.0)}}
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    crop_run_figures.vision.crop_region, crop_run_figures.reread.band_rows,
                    crop_run_figures.reread.crop_rows, confirm_dispute.paper_pdf_of)
        try:
            crop_run_figures.band_extents = lambda cells, number: measured.get(int(number), {})
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            crop_run_figures.extract.extract_images_a = lambda pdf: []
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: []
            crop_run_figures.vision.crop_region = lambda *a, **k: b"png-bytes"
            crop_run_figures.reread.band_rows = lambda cells, number: (
                [{"page": 10, "bbox": [50.0, 500.0, 400.0, 620.0]}] if int(number) == 43 else [])
            crop_run_figures.reread.crop_rows = lambda pdf, rows, **kwargs: b"png-bytes"
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             crop_run_figures.vision.crop_region, crop_run_figures.reread.band_rows,
             crop_run_figures.reread.crop_rows, confirm_dispute.paper_pdf_of) = original

        with open(os.path.join(review, "candidates.jsonl"), encoding="utf-8") as handle:
            rows = {json.loads(line)["question_number"]: json.loads(line)
                    for line in handle if line.strip()}
        ref = rows[43]["image_refs"][0]
        self.assertEqual(ref["box"], [50.0, 500.0, 400.0, 620.0])
        self.assertEqual(ref["pages"], [10])
        self.assertEqual(ref["widened"], "the-whole-question-because-no-picture-was-measured")
        self.assertIn("整題縫成一張", ref["ownership_note"])
        with open(os.path.join(review, "figure_ownership.json"), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["stats"]["whole-question"], 1)
        self.assertIn("整題縫成一張（不交出會被切掉的窄條） 1", printed.getvalue())

    def test_a_picture_far_smaller_than_the_question_is_treated_as_unmeasured(self):
        """量到的圖**遠小於**這一題的範圍時，那不是圖，是圖物件被切成的一片。

        業主點名的 `q071`／`107100 q066`／`q068`：照那一片切，會把一條「有點被切到的窄條」換成
        一小塊碎片（引擎回讀「局部幾何圖形」「局部截圖」）——比原來更糟。當成量不到，走整題縫一張。
        """
        root = self._queue()
        review = os.path.join(root, "review-ui")
        # 真實形狀（`q071`）：業主留過話，所以這一題一定會重切；重切時量到的圖是碎片。
        with open(os.path.join(review, "question_review_events.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"action": "block", "reviewer": "owner", "candidate_key": "key-43",
                                     "created_at": "2026-09-25T11:05:00", "notes": "截圖不完整"},
                                    ensure_ascii=False) + "\n")
        measured = {43: {10: (500.0, 600.0)}}          # 這一題自己的範圍 100pt
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    crop_run_figures.vision.crop_region, crop_run_figures.reread.band_rows,
                    crop_run_figures.reread.crop_rows, crop_run_figures.ask_about_blocks.read_figure_directive,
                    confirm_dispute.paper_pdf_of)
        try:
            crop_run_figures.band_extents = lambda cells, number: measured.get(int(number), {})
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            crop_run_figures.extract.extract_images_a = lambda pdf: [object()]
            # 量到的圖只有 20pt 高（100pt 的 20%）⇒ 碎片。
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: [
                (10, [50.0, 560.0, 400.0, 580.0])]
            crop_run_figures.vision.crop_region = lambda *a, **k: b"png-bytes"
            crop_run_figures.reread.band_rows = lambda cells, number: (
                [{"page": 10, "bbox": [50.0, 500.0, 400.0, 620.0]}] if int(number) == 43 else [])
            crop_run_figures.reread.crop_rows = lambda pdf, rows, **kwargs: b"png-bytes"
            crop_run_figures.ask_about_blocks.read_figure_directive = (
                lambda note, **kwargs: ("wrong-region", note, ""))
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None, human_flagged=True))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             crop_run_figures.vision.crop_region, crop_run_figures.reread.band_rows,
             crop_run_figures.reread.crop_rows,
             crop_run_figures.ask_about_blocks.read_figure_directive,
             confirm_dispute.paper_pdf_of) = original

        with open(os.path.join(review, "candidates.jsonl"), encoding="utf-8") as handle:
            rows = {json.loads(line)["question_number"]: json.loads(line)
                    for line in handle if line.strip()}
        ref = rows[43]["image_refs"][0]
        self.assertEqual(ref["widened"], "the-whole-question-because-no-picture-was-measured")
        self.assertEqual(ref["pages"], [10])
        self.assertIn("量到的圖小得不像一張圖", printed.getvalue())

    def test_a_question_a_person_left_a_note_on_is_recut_to_this_questions_own_pictures(self):
        """人打了記，管線要讀。

        業主 2026-09-25：「我其實都會打住記，但是結果不盡理想」。這一題的框**剛好**跟量到的圖
        重疊，所以舊行為判「本來就在自己列裡」而跳過——他打了記也沒用，下一趟同一張截圖還在。
        有人留過話的題目這一趟不放過：改成這一題區域裡量到的圖，並把那句話原文放進紀錄。
        """
        root = self._queue()
        review = os.path.join(root, "review-ui")
        with open(os.path.join(review, "question_review_events.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"action": "block", "reviewer": "owner", "candidate_key": "key-43",
                                     "created_at": "2026-09-25T11:05:00", "notes": "跨頁截圖錯誤"},
                                    ensure_ascii=False) + "\n")
            handle.write(json.dumps({"action": "block", "reviewer": "repair_experience_apply",
                                     "candidate_key": "key-42", "created_at": "2026-09-25T11:06:00",
                                     "notes": "機器的話不算"}, ensure_ascii=False) + "\n")
        measured = {42: {9: (500.0, 511.0)}, 43: {10: (500.0, 600.0)}}
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    crop_run_figures.vision.crop_region, crop_run_figures.crop_pictures,
                    crop_run_figures.ask_about_blocks.read_figure_directive,
                    confirm_dispute.paper_pdf_of)
        try:
            crop_run_figures.band_extents = lambda cells, number: measured.get(int(number), {})
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            crop_run_figures.extract.extract_images_a = lambda pdf: [object()]
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: [
                (10, [50.0, 500.0, 400.0, 560.0])]
            crop_run_figures.vision.crop_region = lambda *a, **k: b"png-bytes"
            # 縫圖那一段（`crop_pictures`）要用 PIL 開真的 PNG；這一條測的是「有沒有走過去重切」，
            # 不是 PIL，所以換掉它——真的 PNG 走法在 `test_vision_table_crop.py` 與站上實跑裡。
            crop_run_figures.crop_pictures = lambda *a, **k: b"png-bytes"
            crop_run_figures.ask_about_blocks.read_figure_directive = (
                lambda note, **kwargs: ("wrong-region", note, ""))
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None, human_flagged=True))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             crop_run_figures.vision.crop_region, crop_run_figures.crop_pictures,
             crop_run_figures.ask_about_blocks.read_figure_directive,
             confirm_dispute.paper_pdf_of) = original

        with open(os.path.join(review, "candidates.jsonl"), encoding="utf-8") as handle:
            rows = {json.loads(line)["question_number"]: json.loads(line)
                    for line in handle if line.strip()}
        ref = rows[43]["image_refs"][0]
        self.assertEqual(ref["box"], [50.0, 500.0, 400.0, 560.0], "要換成紙本量到的圖，不是自己列的框")
        self.assertEqual(ref["widened"], "the-pictures-in-this-questions-own-part")
        with open(os.path.join(review, "figure_ownership.json"), encoding="utf-8") as handle:
            report = json.load(handle)
        widened = [record for record in report["records"] if record.get("widened")]
        self.assertEqual([record["number"] for record in widened], [43])
        self.assertEqual(widened[0]["human_note"], "跨頁截圖錯誤")
        self.assertIn("照人說過的話重切（這一題有人留話） 1", printed.getvalue())

    def test_a_slice_that_holds_a_picture_is_kept_by_the_pass_itself(self):
        """同一條路徑、同一個 fixture，只把「這一頁量到的圖」換成真的在留下來的那一片裡。

        這是站上 44 題的形狀：原框 411pt（這一題自己的列＋下面那一題的圖），自己的列只有 40pt，
        而那 40pt 裡有這一題自己的圖。舊行為會把它當 sliver 丟掉 ⇒ 這一題就沒有圖了。
        """
        root = self._queue()
        measured = {42: {9: (400.0, 440.0)}, 43: {10: (500.0, 600.0)}}
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    crop_run_figures.vision.crop_region, confirm_dispute.paper_pdf_of)
        try:
            crop_run_figures.band_extents = lambda cells, number: measured.get(int(number), {})
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            crop_run_figures.extract.extract_images_a = lambda pdf: []
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: [
                (9, [54.0, 405.0, 156.0, 500.0])]          # 圖跨過第 42 題留下來的那一片
            crop_run_figures.vision.crop_region = lambda *a, **k: b"png-bytes"
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            with contextlib.redirect_stdout(io.StringIO()):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             crop_run_figures.vision.crop_region, confirm_dispute.paper_pdf_of) = original

        review = os.path.join(root, "review-ui")
        with open(os.path.join(review, "candidates.jsonl"), encoding="utf-8") as handle:
            rows = {json.loads(line)["question_number"]: json.loads(line)
                    for line in handle if line.strip()}
        kept = rows[42]["image_refs"][0]
        # 紙本的圖物件是 405–500，它的一端（405）落在第 42 題自己的列（400–440）裡 ⇒ 這一張圖
        # 是**這一題的圖被列界切到**，所以整張留著（`clip_keeping_pictures`），並標記 `whole_picture`
        # ——不切半張圖是業主 2026-09-25 的「截圖不完整」，`clipped` 照實記它越過列界多少。
        self.assertEqual(kept["box"], [39.2, 400.0, 158.4, 500.0])
        self.assertEqual(kept["clipped"], {"above": 300.0, "below": 71.0})
        self.assertIs(kept["whole_picture"], True)
        with open(os.path.join(review, "figure_ownership.json"), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["stats"]["sliver"], 0, "有圖的那一片不是 sliver")
        self.assertEqual(report["stats"]["clipped"], 2)
        self.assertEqual(report["stats"]["kept-whole"], 1)
        whole = [record for record in report["records"] if record.get("kept_whole")]
        self.assertEqual([record["number"] for record in whole], [42])
        self.assertEqual(whole[0]["picture"], [54.0, 405.0, 156.0, 500.0])

    def test_the_ownership_note_is_replaced_not_appended(self):
        """舊句是**上一次那一個框**的事實。留著它，審題者就同時讀到「只切這一題的列」與
        「整張留著不切」兩句互相矛盾的話（第一趟把框補回完整之後必然如此）。"""
        root = self._queue()
        measured = {42: {9: (400.0, 440.0)}, 43: {10: (500.0, 600.0)}}
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    crop_run_figures.vision.crop_region, confirm_dispute.paper_pdf_of)
        try:
            crop_run_figures.band_extents = lambda cells, number: measured.get(int(number), {})
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            crop_run_figures.extract.extract_images_a = lambda pdf: []
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: [
                (9, [54.0, 405.0, 156.0, 500.0])]
            crop_run_figures.vision.crop_region = lambda *a, **k: b"png-bytes"
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            with contextlib.redirect_stdout(io.StringIO()):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None))
                first = self._description(root, 42)
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             crop_run_figures.vision.crop_region, confirm_dispute.paper_pdf_of) = original

        self.assertIn("整張留著不切", first)
        self.assertEqual(first.count("（紙本這張圖"), 1, first)
        self.assertNotIn("只切這一題的列", first)
        # 數字是**圖**越過列界多少，不是框越過多少：框是上一次裁過的版本，補回完整之後它的越界量
        # 是 0，寫出來會是「上 0pt、下 0pt」——站上實測 `moex:106100:311:11:1:question:q078` 就印成
        # 那一句（正確但沒有資訊）。這一題的圖是 405–500、列是 400–440 ⇒ 下 60pt。
        self.assertIn("上 0pt、下 60pt", first)
        second = self._description(root, 42)
        self.assertEqual(second.count("（紙本這張圖"), 1, second)
        self.assertIn("整張留著不切", second)
        self.assertNotIn("只切這一題的列", second)
        self.assertEqual(first, second, "第二趟（`whole_picture` 那一條路）要寫出同一句")

    def _description(self, root, number):
        with open(os.path.join(root, "review-ui", "candidates.jsonl"), encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    if row.get("question_number") == number:
                        return row["image_refs"][0].get("description") or ""
        raise AssertionError("沒有第 %s 題" % number)

    def test_a_second_pass_does_not_cut_the_whole_picture_back_in_half(self):
        """標記過 `whole_picture` 的列，下一趟不再切回去——否則同一趟會把它裁成半張、下一趟再判定一次。

        這支程式沒有 dry-run（站上自己備份成 `candidates.jsonl.before-ownership-<stamp>`），
        所以要能安全地重複跑：第二趟的框與 bytes 都不變，而且計數落在 `inside`（沒有再切）。
        """
        root = self._queue()
        measured = {42: {9: (400.0, 440.0)}, 43: {10: (500.0, 600.0)}}
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    crop_run_figures.vision.crop_region, confirm_dispute.paper_pdf_of)
        try:
            crop_run_figures.band_extents = lambda cells, number: measured.get(int(number), {})
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            crop_run_figures.extract.extract_images_a = lambda pdf: []
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: [
                (9, [54.0, 405.0, 156.0, 500.0])]
            crop_run_figures.vision.crop_region = lambda *a, **k: b"png-bytes"
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            with contextlib.redirect_stdout(io.StringIO()):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None))
                printed = io.StringIO()
                with contextlib.redirect_stdout(printed):
                    crop_run_figures.fix_queue_figure_ownership(
                        types.SimpleNamespace(queue=root, only=None))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             crop_run_figures.vision.crop_region, confirm_dispute.paper_pdf_of) = original

        review = os.path.join(root, "review-ui")
        with open(os.path.join(review, "candidates.jsonl"), encoding="utf-8") as handle:
            rows = {json.loads(line)["question_number"]: json.loads(line)
                    for line in handle if line.strip()}
        self.assertEqual(rows[42]["image_refs"][0]["box"], [39.2, 400.0, 158.4, 500.0])
        self.assertEqual(rows[42]["image_refs"][0]["bytes"], len(b"png-bytes"))
        with open(os.path.join(review, "figure_ownership.json"), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["stats"].get("clipped", 0), 0, "第二趟沒有東西可切（第 43 題上一趟已切）")
        self.assertEqual(report["stats"].get("kept-whole", 0), 0, "第二趟不該再補一次")
        self.assertIn("其中 0 張是紙本的圖物件跨過列界", printed.getvalue())


if __name__ == "__main__":
    unittest.main()


class HumanSaysNoFigureTests(unittest.TestCase):
    """業主 2026-09-25：「這一題沒有圖」「這題本來就沒有圖，你硬要截圖」「額外多截了圖」。

    量到的形狀（站上實測）：他明確說沒有圖／多截的 **11 題，11 題都還帶著一張 `figure-crop`**，
    而且他留話之後 **一個字都沒動**。原因有兩個，這一組測試把它們都釘住：那些列的框是空的
    （`box: []`），舊碼把它們當成 `unverified` 留著；而**人留過話、紙本這一題自己的區域裡也量不到
    任何圖物件**時，舊碼縫一張整題的圖交出去——那正是他口中的「多截圖」。
    """

    def _queue(self, note=None):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        review = os.path.join(root, "review-ui")
        os.makedirs(os.path.join(review, "crops", "109020_藥師(一)_藥學(一)"))
        rows = [
            {"candidate_key": "key-59", "question_number": 59,
             "image_refs": [{"asset_role": "figure-crop", "page": 4, "raw_ref": "q059.png",
                             "path": "review-ui/crops/109020_藥師(一)_藥學(一)/q059.png",
                             "description": "第 59 題的圖", "box": []}]},
        ]
        with open(os.path.join(review, "candidates.jsonl"), "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        events = []
        if note is not None:
            events.append({"candidate_key": "key-59", "action": "block", "reviewer": "local",
                           "notes": note, "created_at": "2026-09-25T10:00:00"})
        with open(os.path.join(review, "question_review_events.jsonl"), "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return root

    def _run(self, root):
        measured = {59: {4: (300.0, 380.0)}}
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    confirm_dispute.paper_pdf_of)
        try:
            crop_run_figures.band_extents = lambda cells, number: measured.get(int(number), {})
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            crop_run_figures.extract.extract_images_a = lambda pdf: []
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: []
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             confirm_dispute.paper_pdf_of) = original
        with open(os.path.join(root, "review-ui", "candidates.jsonl"), encoding="utf-8") as handle:
            row = json.loads(handle.readline())
        with open(os.path.join(root, "review-ui", "figure_ownership.json"), encoding="utf-8") as handle:
            ownership = json.load(handle)
        return row, ownership, printed.getvalue()

    def test_a_plain_human_note_does_not_authorize_removing_an_unverified_crop(self):
        root = self._queue(note="這題本來就沒有圖，你硬要截圖")
        row, ownership, _printed = self._run(root)
        self.assertEqual(len(row["image_refs"]), 1, "未經核准的語意解讀不能移除現有裁切")
        self.assertEqual(row["image_refs"][0]["ownership"], "unverified")
        self.assertEqual(ownership["stats"]["unverified"], 1)
        self.assertFalse(any(record.get("dropped") for record in ownership["records"]))

    def test_without_a_person_saying_so_the_crop_remains_unverified(self):
        """沒有人留過話 ⇒ 不猜，紙本確實可能把圖放到別頁。"""
        root = self._queue(note=None)
        row, ownership, _printed = self._run(root)
        self.assertEqual(len(row["image_refs"]), 1, "沒有人說話就不動它")
        self.assertEqual(row["image_refs"][0]["ownership"], "unverified")
        self.assertEqual(ownership["stats"]["unverified"], 1)


class NoteDirectiveTests(unittest.TestCase):
    """業主 2026-09-25：「四個選項是圖片，但是題目沒有圖」「多截圖」。

    站上實測（同一句話，兩個訊號）：他明確說「沒有圖／多截」的 11 題裡，紙本量得到的訊號只解釋得了
    **5 題**（區域裡連一個圖物件都量不到 ⇒ `dropped-no-figure`）；剩下 **6 題**的區域**確實量到 2-4 個
    圖物件**（`108030:305 q061`「四個選項是圖片，但是題目沒有圖」、`109020:305 q074`「多截圖」…），
    量測於是留著一張圖，而他看到的就是「他不改」。紙本量不出「這張圖該不該交給審題者」——那是他的
    意思，所以這一條路是**讀他的話**（`vision.read_note_directive`，標籤是封閉集合，讀不出來就抱怨），
    不是一條新的量測規則（charter：腳本只保留紙張的性質，讀出意義的是提示詞）。
    """

    def _queue(self, note, *, box=(50.0, 305.0, 400.0, 375.0)):
        self._note = note
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        review = os.path.join(root, "review-ui")
        os.makedirs(os.path.join(review, "crops", "108030_藥師(一)_藥學(一)"))
        rows = [
            {"candidate_key": "key-61", "question_number": 61,
             "stem": "下列何者是 meperidine 造成癲癇發作的代謝物？",
             "image_refs": [{"asset_role": "figure-crop", "page": 4, "raw_ref": "q061.png",
                             "path": "review-ui/crops/108030_藥師(一)_藥學(一)/q061.png",
                             "description": "第 61 題的圖", "box": list(box)}]},
        ]
        with open(os.path.join(review, "candidates.jsonl"), "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        with open(os.path.join(review, "question_review_events.jsonl"), "w",
                  encoding="utf-8") as handle:
            handle.write(json.dumps({"candidate_key": "key-61", "action": "block",
                                     "reviewer": "local", "notes": note,
                                     "created_at": "2026-09-25T10:00:00"},
                                    ensure_ascii=False) + "\n")
        with open(os.path.join(review, "crops", "108030_藥師(一)_藥學(一)", "q061.png"),
                  "wb") as handle:
            handle.write(b"before")
        return root

    def _run(self, root, *, label, quote="", pictures=((4, [54.0, 305.0, 156.0, 375.0]),),
             calls=None):
        """跑一趟 `--human-flagged`。引擎被換掉：這一支的輸入是他的話，不該在測試裡真的問模型。"""
        original = (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
                    crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
                    crop_run_figures.ask_about_blocks.read_figure_directive, crop_run_figures.crop_pictures,
                    confirm_dispute.paper_pdf_of)

        def ask(note, **kwargs):
            if calls is not None:
                calls.append((note, kwargs.get("question") or ""))
            return label, quote, ""

        try:
            crop_run_figures.band_extents = lambda cells, number: {4: (300.0, 380.0)}
            crop_run_figures.extract.extract_cells_a = lambda pdf: []
            crop_run_figures.extract.extract_images_a = lambda pdf: []
            crop_run_figures.vision.picture_boxes = lambda images, **kwargs: list(pictures)
            crop_run_figures.ask_about_blocks.read_figure_directive = ask
            crop_run_figures.crop_pictures = lambda *a, **k: b"png-bytes"
            confirm_dispute.paper_pdf_of = lambda row: "paper.pdf"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None, human_flagged=True))
        finally:
            (crop_run_figures.band_extents, crop_run_figures.extract.extract_cells_a,
             crop_run_figures.extract.extract_images_a, crop_run_figures.vision.picture_boxes,
             crop_run_figures.ask_about_blocks.read_figure_directive, crop_run_figures.crop_pictures,
             confirm_dispute.paper_pdf_of) = original
        if calls:
            # **引擎收到的是他的話，不是那個時間戳**：`human_notes` 的形狀是 `(created_at, notes)`
            # （時間在前），對調過來就會讓每一題都回 `unclear`、並把時間戳照抄成 `quote`
            # ——站上實測 2026-09-25 21:43，他點名的 6 題全部那樣。
            self.assertEqual(calls[0][0], self._note,
                             "引擎收到 %r，不是他的話 %r" % (calls[0][0], self._note))
        with open(os.path.join(root, "review-ui", "candidates.jsonl"), encoding="utf-8") as handle:
            row = json.loads(handle.readline())
        with open(os.path.join(root, "review-ui", "figure_ownership.json"), encoding="utf-8") as handle:
            ownership = json.load(handle)
        return row, ownership, printed.getvalue()

    def test_a_note_saying_there_is_no_figure_removes_it_even_though_the_paper_measures_one(self):
        """紙本量到 2-4 個圖物件，他說「題目沒有圖」⇒ **他的話是權威**，這一張不放，並留下他的原話。"""
        root = self._queue("四個選項是圖片，但是題目沒有圖，你多放了一張圖")
        row, ownership, printed = self._run(root, label="no-figure", quote="題目沒有圖")
        self.assertEqual(row["image_refs"], [], "他說沒有圖 ⇒ 量到圖也不留")
        dropped = [record for record in ownership["records"] if record.get("dropped")]
        self.assertEqual([record["dropped"] for record in dropped], ["human-said-no-figure"])
        self.assertEqual(dropped[0]["quote"], "題目沒有圖")
        self.assertEqual(dropped[0]["note"], "四個選項是圖片，但是題目沒有圖，你多放了一張圖")
        self.assertEqual(ownership["stats"]["dropped-by-note"], 1)
        self.assertEqual(ownership["directives"]["key-61"]["directive"], "no-figure")
        self.assertIn("他說這一題不要圖（no-figure／多截）⇒ 不放圖（移除） 1", printed)

    def test_more_crop_is_the_same_drop(self):
        """「多截圖」：他沒有說這一題本來沒有圖，但他說的是**多出來的圖**，一樣不放。"""
        root = self._queue("多截圖")
        row, ownership, _printed = self._run(root, label="extra-crop", quote="多截圖")
        self.assertEqual(row["image_refs"], [])
        self.assertEqual(ownership["stats"]["dropped-by-note"], 1)

    def test_the_negative_control_a_wrong_region_note_still_holds_a_figure(self):
        """「範圍錯」不是「沒有圖」：他說這一題有圖、只是切錯了 ⇒ 走量測重切，不刪。

        這一條是這一組的負控制：把標籤換成 `wrong-region`，同一份 fixture 就**不能**被刪掉。
        """
        root = self._queue("跨頁只截了一半，範圍錯了")
        row, ownership, _printed = self._run(root, label="wrong-region", quote="跨頁只截了一半")
        self.assertEqual(len(row["image_refs"]), 1, "他說有圖 ⇒ 不刪")
        self.assertEqual(ownership["stats"]["dropped-by-note"], 0)
        self.assertEqual(ownership["stats"]["widened"], 1, "改成這一題自己的圖")

    def test_a_keep_note_holds_the_figure_when_no_picture_is_measured(self):
        """「有圖」＋紙本量不到圖物件 ⇒ 保留未驗證裁切，不從空量測推導移除。"""
        root = self._queue("這一題有圖，你切掉了")
        row, ownership, _printed = self._run(root, label="keep", pictures=())
        self.assertEqual(len(row["image_refs"]), 1)
        self.assertFalse(any(record.get("dropped") for record in ownership["records"]))

    def test_a_second_run_does_not_ask_the_engine_again(self):
        """同一句話問一次就夠：`directives` 快取在 `figure_ownership.json`（迴圈每 10 分鐘一輪）。"""
        root = self._queue("多截圖")
        calls = []
        self._run(root, label="extra-crop", quote="多截圖", calls=calls)
        self.assertEqual(len(calls), 1, calls)
        # 第二趟：標籤換成別的，若它真的再問就會拿到 `no-figure` ⇒ 兩趟的結果會不一樣。
        self._run(root, label="no-figure", quote="題目沒有圖", calls=calls)
        self.assertEqual(len(calls), 1, "第二趟不該再問：" + str(calls))

    def test_the_full_pass_does_not_ask_at_all(self):
        """整本的那一趟（1,763 秒）是量測，不讀他的話：讀話是迴圈那一趟（`--human-flagged`）的事。"""
        root = self._queue("多截圖")
        calls = []
        original = crop_run_figures.ask_about_blocks.read_figure_directive

        def ask(note, **kwargs):
            calls.append(note)
            return "no-figure", "", ""

        try:
            crop_run_figures.ask_about_blocks.read_figure_directive = ask
            with contextlib.redirect_stdout(io.StringIO()):
                crop_run_figures.fix_queue_figure_ownership(
                    types.SimpleNamespace(queue=root, only=None, human_flagged=False))
        finally:
            crop_run_figures.ask_about_blocks.read_figure_directive = original
        self.assertEqual(calls, [], "沒有 `--human-flagged` 就不問")

    def test_a_table_crop_survives_a_note_saying_there_is_no_figure(self):
        """表格那一張的 `asset_role` 也是 `figure-crop`，但它不是「這一題的圖」。

        它是紙本表格那一張（讀法用它的文字代替被壓平的表）。他說「沒有圖」時把同一條路上的表格一起
        刪掉，那一題就失去表格——同一條路刪掉兩種東西，是一個決定管兩件事。
        """
        root = self._queue("題目本體沒有圖")
        review = os.path.join(root, "review-ui")
        path = os.path.join(review, "candidates.jsonl")
        with open(path, encoding="utf-8") as handle:
            row = json.loads(handle.readline())
        row["image_refs"].append({"asset_role": "figure-crop",
                                  "label": crop_run_figures.TABLE_REASON,
                                  "page": 4, "raw_ref": "q061-table.png",
                                  "path": "review-ui/crops/108030_藥師(一)_藥學(一)/q061-table.png",
                                  "table_lines": ["A 1", "B 2"], "box": [50.0, 305.0, 400.0, 340.0]})
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        kept_row, ownership, _printed = self._run(root, label="no-figure", quote="沒有圖")
        labels = [ref.get("label") for ref in kept_row["image_refs"]]
        self.assertEqual(labels, [crop_run_figures.TABLE_REASON], "表格留著、圖刪掉")
        dropped = [record for record in ownership["records"] if record.get("dropped")]
        self.assertEqual([record["file"] for record in dropped], ["q061.png"], "紀錄只記真的刪掉的那張")
