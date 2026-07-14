from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_question_candidates_from_mineru as parser  # noqa: E402


class ExamHeaderRegressionTests(unittest.TestCase):
    def test_year_header_with_many_options_is_not_a_question(self) -> None:
        body = """
考試時間 60 分鐘 類科名稱 醫事檢驗師 科目名稱 生物化學 本試題 80 題 座號
(A) 一 (B) 二 (C) 三 (D) 四
(A) 一 (B) 二 (C) 三 (D) 四
(A) 一 (B) 二 (C) 三 (D) 四
"""
        self.assertTrue(parser.is_exam_header_block(body, number="114", year="114"))

    def test_real_question_equal_to_year_is_kept(self) -> None:
        body = """下列何者正確？
(A) 一
(B) 二
(C) 三
(D) 四
"""
        self.assertFalse(parser.is_exam_header_block(body, number="114", year="114"))

    def test_header_without_options_is_kept_out(self) -> None:
        body = "考試時間 60 分鐘 類科名稱 醫事檢驗師 科目名稱 生物化學 本試題 80 題"
        self.assertTrue(parser.is_exam_header_block(body, number="114", year="114"))

    def test_mixed_parenthesis_around_english_term_is_normalized(self) -> None:
        value = "急性腎絲球體腎炎（acute glomerulonephritis)？"
        self.assertEqual(
            parser.normalize_science_markup(value),
            "急性腎絲球體腎炎（acute glomerulonephritis）？",
        )

    def test_pharmacy_text_and_notation_are_normalized(self) -> None:
        value = (
            r"檠師：藁事法；罂粟；肥胖者须依照 adjusted BW，insulin 剂量；使其内所含之成分能均匀分散；"
            r"HCO_{3}^{-}=16\ mEq/L，FEV_{1}=35\%，$3^{rd}$ degree，Seebri <sup>®</sup>，溫度20°C"
        )
        self.assertEqual(
            parser.normalize_text(value, category="藥師"),
            "藥師：藥事法；罌粟；肥胖者須依照 adjusted BW，insulin 劑量；使其內所含之成分能均勻分散；"
            "HCO₃⁻=16 mEq/L，FEV₁=35%，3ʳᵈ degree，Seebri<sup>®</sup>，溫度 20℃",
        )

    def test_confirmed_ocr_rules_are_deterministic(self) -> None:
        value = "内、藁事法、罂粟"
        self.assertEqual(
            parser.normalize_text(value, category="藥師"),
            "內、藥事法、罌粟",
        )

    def test_confirmed_yao_terms_do_not_corrupt_herbal_name(self) -> None:
        value = "藁局、藁用酒精、藁名、藁商、藁害、藁本"
        self.assertEqual(
            parser.normalize_text(value, category="中醫師"),
            "藥局、藥用酒精、藥名、藥商、藥害、藁本",
        )

    def test_post_106_bare_question_numbers_fall_back_after_header_swallow(self) -> None:
        markdown = """111. 年第二次考試 類科名稱 醫事檢驗師 科目名稱 生物化學 考試時間 60 分鐘 座號 本試題
1 第一題何者正確？
(A) 甲
(B) 乙
(C) 丙
(D) 丁
2 第二題何者正確？
(A) 甲
(B) 乙
(C) 丙
(D) 丁
3 第三題何者正確？
(A) 甲
(B) 乙
(C) 丙
(D) 丁
"""
        questions = parser.parse_questions(markdown, Path("sample.md"), "111")
        self.assertEqual([item["question_number"] for item in questions], ["1", "2", "3"])
        self.assertTrue(all(len(item["options"]) == 4 for item in questions))

    def test_pharmacy_pharmacokinetic_subscripts_and_unit_escapes_are_normalized(self) -> None:
        value = r"Cl Cr、SCr、Scr、FE Na、ER H、Ctrough、CSS、Vd、HbA1C、14\ mEq/L、20 ~mL/min、68\%"
        self.assertEqual(
            parser.normalize_text(value, category="藥師"),
            "CL<sub>Cr</sub>、S<sub>Cr</sub>、S<sub>Cr</sub>、FE<sub>Na</sub>、ER<sub>H</sub>、"
            "C<sub>trough</sub>、C<sub>SS</sub>、V<sub>d</sub>、HbA₁<sub>C</sub>、"
            "14 mEq/L、20 mL/min、68%",
        )

    def test_pharmacokinetic_letter_subscripts_are_token_bounded(self) -> None:
        value = r"KM、K_M、Vmax、V_{max}、ka、k_a、ke、k_e、Vp、V_p、VD、V_D、0.16 ~h⁻¹；keto、karma、Vmaximal、hypothyroidism、Vmax}{KM"
        self.assertEqual(
            parser.normalize_text(value, category="藥師"),
            "K<sub>M</sub>、K<sub>M</sub>、V<sub>max</sub>、V<sub>max</sub>、"
            "k<sub>a</sub>、k<sub>a</sub>、k<sub>e</sub>、k<sub>e</sub>、"
            "V<sub>p</sub>、V<sub>p</sub>、V<sub>D</sub>、V<sub>D</sub>、0.16 h⁻¹；"
            "keto、karma、Vmaximal、hypothyroidism、V<sub>max</sub>}{K<sub>M</sub>",
        )

    def test_extended_pharmacokinetic_subscripts_are_token_bounded(self) -> None:
        value = (
            r"Cmax、tmax、Cp、Ccr、Clcr、Css、fe、fu、D0、DL、Rin、"
            r"MW dextrose、Ksp、Du∞、D_{u(0-t)}、Co；"
            r"Co、cobalt、Vmaximal、freedom"
        )
        self.assertEqual(
            parser.normalize_text(value, category="藥師"),
            "C<sub>max</sub>、t<sub>max</sub>、C<sub>p</sub>、C<sub>cr</sub>、"
            "CL<sub>Cr</sub>、C<sub>SS</sub>、f<sub>e</sub>、f<sub>u</sub>、D₀、"
            "D<sub>L</sub>、R<sub>in</sub>、MW<sub>dextrose</sub>、K<sub>sp</sub>、"
            "D<sub>u</sub>∞、D<sub>u(0-t)</sub>、Co；Co、cobalt、Vmaximal、freedom",
        )

    def test_steady_state_and_experimental_volume_subscripts_are_normalized(self) -> None:
        value = "Vss、V ss、V_{ss}、Vexp、V exp、V_{exp}、Vessel"
        self.assertEqual(
            parser.normalize_text(value, category="藥師"),
            "V<sub>ss</sub>、V<sub>ss</sub>、V<sub>ss</sub>、"
            "V<sub>exp</sub>、V<sub>exp</sub>、V<sub>exp</sub>、Vessel",
        )

    def test_layout_tildes_before_hour_and_litre_units_are_removed(self) -> None:
        value = "1 ~hr、2 ～hr、3 ~L / min、4 ～ L"
        self.assertEqual(
            parser.normalize_text(value, category="藥師"),
            "1 hr、2 hr、3 L / min、4 L",
        )

    def test_contextual_superscript_zero_does_not_rewrite_cobalt(self) -> None:
        self.assertEqual(
            parser.normalize_text("還原電位（Eo）與 Co；初濃度（Co）", category="藥師"),
            "還原電位（E⁰）與 Co；初濃度（C₀）",
        )

    def test_steady_state_distribution_volume_subscript_is_preserved(self) -> None:
        self.assertEqual(
            parser.normalize_text("（VD） ss、VD,ss", category="藥師"),
            "（V<sub>D</sub>）<sub>ss</sub>、V<sub>D,ss</sub>",
        )

    def test_traditional_character_rule_covers_number(self) -> None:
        self.assertEqual(parser.normalize_text("最大数为 352,000"), "最大數为 352,000")

    def test_po_subscript_normalization_is_token_bounded(self) -> None:
        value = "hypo、hypothyroidism、PO、PO2、hyPO2thyroidism"
        self.assertEqual(
            parser.normalize_text(value),
            "hypo、hypothyroidism、PO、PO₂、hyPO2thyroidism",
        )

    def test_percent_latex_escapes_are_fully_removed(self) -> None:
        value = r"3\%、4\\%、5\ %、6%"
        self.assertEqual(parser.normalize_text(value), "3%、4%、5%、6%")

    def test_celsius_latex_variants_are_normalized_without_touching_angles(self) -> None:
        value = r"20\circC、20\\circ C、20^{\circ}C、20^{\\circ}C、90^\circ、90\circ"
        self.assertEqual(parser.normalize_text(value), "20℃、20℃、20℃、20℃、90°、90°")


if __name__ == "__main__":
    unittest.main()
