"""The paper's own legend for its private-use marks, and the lost glyphs that have none.

Two different things print as tofu, and telling them apart is the whole point of this module:

* a **mark the paper defines**, whose meaning the stem states one line above (`\ue000砂粒病毒…`), and
  which the options then use (`\ue18c\ue000\ue001` = "Arenavirus and Hantavirus");
* a **lost glyph**, where the paper prints a character the text layer cannot spell
  (`轉氨\ue2c6` = 轉氨酶). This is a real defect at a known position and must be reported, never
  guessed at.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbr import subitems  # noqa: E402

# The two shapes, taken verbatim from the corpus.
MULTI_SELECT = (
    "下列那些感染是藉由老鼠傳染的？"
    "\ue000砂粒病毒（Arenavirus）  \ue001漢他病毒（Hantavirus）  \ue002西尼羅病毒（West Nile virus）"
)
MULTI_SELECT_65 = (
    "泌尿系統感染的尿液分析以下列那些物質的增加為主？"
    "\ue000細菌  \ue001白血球  \ue002紅血球  \ue003葡萄糖"
)
LOST_GLYPH = "下列那一項是丙酮酸羧\ue2c6（pyruvate carboxylase）的輔\ue2c6？"


class LegendTest(unittest.TestCase):
    def test_the_stem_states_what_each_mark_means(self):
        legend = subitems.legend_of(MULTI_SELECT)
        self.assertEqual(legend["\ue000"], "砂粒病毒（Arenavirus）")
        self.assertEqual(legend["\ue001"], "漢他病毒（Hantavirus）")
        self.assertEqual(legend["\ue002"], "西尼羅病毒（West Nile virus）")

    def test_a_stem_that_defines_nothing_has_no_legend(self):
        self.assertEqual(subitems.legend_of("下列何者正確？"), {})

    def test_the_same_mark_keeps_its_first_spelling(self):
        legend = subitems.legend_of("\ue000細菌 \ue000白血球")
        self.assertEqual(legend["\ue000"], "細菌")


class ResolveTest(unittest.TestCase):
    def test_a_run_of_marks_becomes_the_items_it_selects(self):
        legend = subitems.legend_of(MULTI_SELECT)
        self.assertEqual(subitems.resolve("\ue000\ue001", legend),
                         "砂粒病毒（Arenavirus）+漢他病毒（Hantavirus）")

    def test_all_three_marks(self):
        legend = subitems.legend_of(MULTI_SELECT)
        self.assertEqual(
            subitems.resolve("\ue000\ue001\ue002", legend),
            "砂粒病毒（Arenavirus）+漢他病毒（Hantavirus）+西尼羅病毒（West Nile virus）")

    def test_text_around_the_run_is_kept_as_the_paper_set_it(self):
        legend = subitems.legend_of(MULTI_SELECT_65)
        self.assertEqual(subitems.resolve("僅\ue002\ue003", legend), "僅紅血球+葡萄糖")

    def test_a_mark_with_no_legend_is_left_alone(self):
        # Showing the mark is honest; inventing a word for it would not be.
        self.assertEqual(subitems.resolve("\ue2c6檢查", {}), "\ue2c6檢查")

    def test_a_lost_glyph_is_not_rewritten_even_when_a_legend_exists(self):
        legend = subitems.legend_of(MULTI_SELECT)
        self.assertEqual(subitems.resolve("快速尿素\ue2c6檢查", legend), "快速尿素\ue2c6檢查")


class LostGlyphTest(unittest.TestCase):
    def test_a_mark_inside_a_word_is_a_lost_glyph(self):
        found = subitems.lost_glyphs(LOST_GLYPH)
        self.assertEqual([index for index, _ in found], [10, 35])

    def test_the_note_points_at_the_character_and_its_word(self):
        note = subitems.describe_lost_glyphs("經上消化道內視鏡胃黏膜切片之快速尿素\ue2c6檢查")
        self.assertIn("快速尿素▢檢查", note)

    def test_no_lost_glyphs_means_no_note(self):
        self.assertIsNone(subitems.describe_lost_glyphs(MULTI_SELECT))
        self.assertEqual(subitems.lost_glyphs(MULTI_SELECT), [])

    def test_a_mark_the_legend_defines_is_not_a_lost_glyph(self):
        # `\ue000` sits after `？`, and it is defined - either reason is enough.
        legend = subitems.legend_of(MULTI_SELECT)
        self.assertEqual(subitems.lost_glyphs(MULTI_SELECT, legend), [])


if __name__ == "__main__":
    unittest.main()
