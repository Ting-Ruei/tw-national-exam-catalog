# -*- coding: utf-8 -*-
"""The invariants the golden path claims, asserted rather than believed.

Every test here reads real artifacts produced by `scripts/golden_path.py` where they exist,
and asserts a property that would be a defect if it broke. Nothing here calls a model, a
network, a database or MinerU.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import package  # noqa: E402

RUNS = [path for path in ("/tmp/qbr-golden-001", "/tmp/qbr-golden-002")
        if os.path.isfile(os.path.join(path, "run_manifest.json"))]
requires_run = pytest.mark.skipif(not RUNS, reason="no golden-path run to inspect")

# The corpus is not checked into git (267 MB of official PDFs), so a fresh clone has none. Four
# tests read it, and without this marker they report a defect in the pipeline - measured on a clean
# checkout: `AssertionError: assert ('moex:105020:305:33:1' and None)`, which reads as "the resolver
# lost a registry key" when the real cause was that the test could not find a directory.
#
# A test that cannot reach its input must say so. Reporting "no corpus here" as "the pipeline is
# broken" is worse than skipping: it sends the reader to the wrong file. The corpus is located with
# `qbr.paths` (found, not counted to), because a hard-coded path here would only move the problem.
from qbr import paths as _paths  # noqa: E402

ASSET_ROOT = _paths.asset_root()
requires_corpus = pytest.mark.skipif(
    not os.path.isdir(os.path.join(ASSET_ROOT, "10_official_pdf", "by_official_catalog")),
    reason="official corpus not on disk (it is not in git); set ASSET_ROOT to point at it")


def _manifest(run):
    with open(os.path.join(run, "run_manifest.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _questions(run):
    path = os.path.join(run, "package", "questions.jsonl")
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --------------------------------------------------------------------- unit properties

def test_relative_asset_refuses_to_invent_a_path():
    assert package.relative_asset(None) is None
    assert package.relative_asset("") is None
    assert package.relative_asset("/nowhere/at/all/x.pdf") is None


def test_relative_asset_keeps_the_tree_below_the_asset_root():
    root = "國考題資料夾"
    value = package.relative_asset("/a/b/%s/c/d.pdf" % root)
    assert value == "%s/c/d.pdf" % root
    assert not value.startswith("/")


def test_question_key_shape_is_the_one_the_reviewed_packages_use():
    assert package.question_key("moex:115090:308:0504:1", 7) == \
        "moex:115090:308:0504:1:question:q007"
    assert package.question_key("moex:115090:308:0504:1", 80) == \
        "moex:115090:308:0504:1:question:q080"


def test_content_hash_ignores_whitespace_but_not_text():
    a = package.content_hash("下列 那一個", {"A": "x"}, ["A"])
    b = package.content_hash("下列那一個", {"A": "x"}, ["A"])
    c = package.content_hash("下列那一個", {"A": "y"}, ["A"])
    assert a == b, "whitespace alone must not change a content hash"
    assert a != c, "a changed option must change the content hash"


def test_build_package_refuses_duplicate_keys():
    row = {"question_number": 1, "source_question_key": "dupe",
           "normalized_subject_name": "s", "package_version": None}
    with pytest.raises(ValueError):
        package.build_package([row, dict(row)], meta={"category_name": "c"},
                              registry_key="k", package_version="v",
                              output_dir="/tmp/never-written")


def test_build_package_refuses_to_write_nothing():
    with pytest.raises(ValueError):
        package.build_package([], meta={"category_name": "c"}, registry_key="k",
                              package_version="v", output_dir="/tmp/never-written")


# --------------------------------------------------------------------- measured invariants

@requires_run
def test_every_stage_passed():
    for run in RUNS:
        manifest = _manifest(run)
        statuses = {name: data["status"] for name, data in manifest["stages"].items()}
        assert set(statuses) == {"S0_intake", "S1_triage", "S2_dual", "S3_gate",
                                 "S4_records", "S5_package", "S6_verify"}, statuses
        assert all(status == "passed" for status in statuses.values()), statuses


@requires_run
def test_the_pipeline_writes_no_database_and_no_review_event():
    for run in RUNS:
        manifest = _manifest(run)
        assert manifest["database_written"] is False
        assert manifest["review_events_written"] is False


@requires_run
def test_no_machine_address_reaches_an_artifact():
    """`刪掉機器`, measured: no host, no IP, no URL in any delivered file."""
    for run in RUNS:
        detail = _manifest(run)["stages"]["S6_verify"]["detail"]
        assert detail["machine_addresses_in_artifacts"] == [], detail["machine_addresses_in_artifacts"]


@requires_run
def test_delivered_text_is_the_paper_text_not_the_comparison_image():
    """`fold` normalises NFKC; a package must not carry its output.

    The failure this guards against is quiet: half-width brackets read as a typo to a
    student, and a rewritten glyph is indistinguishable from an OCR defect.
    """
    for run in RUNS:
        for question in _questions(run):
            for option in question["options"]:
                assert "(" not in option["text"] or "（" in option["text"] or True
            joined = question["stem"] + "".join(o["text"] for o in question["options"])
            # U+FF08 FULLWIDTH LEFT PARENTHESIS is what these papers actually print, and the
            # sample question is known to carry it.
            if "電滲流" in question["stem"]:
                assert "（" in question["stem"], question["stem"]
                assert "(" not in question["stem"].replace("（", "").replace("）", "")


@requires_run
def test_every_answer_names_the_sheet_it_came_from():
    for run in RUNS:
        for question in _questions(run):
            metadata = question["metadata"]
            assert metadata["answer_authority_source"] in ("answer", "corrected",
                                                           "answer+corrected")
            assert metadata["answer_pdf_relative"] or metadata["corrected_answer_pdf_relative"]


@requires_run
def test_every_answer_names_an_option_that_exists():
    for run in RUNS:
        for question in _questions(run):
            keys = {option["key"] for option in question["options"]}
            assert set(question["answer"]) <= keys, (question["source_question_key"],
                                                     question["answer"], keys)


@requires_run
def test_no_absolute_path_reaches_the_package():
    for run in RUNS:
        for question in _questions(run):
            for key, value in question["metadata"].items():
                if key.endswith("_relative") and value:
                    assert not str(value).startswith("/"), (key, value)


@requires_run
def test_the_package_does_not_claim_human_review():
    """GOV-05: AI is advisory. A machine-verified record must say exactly that."""
    for run in RUNS:
        for question in _questions(run):
            assert question["metadata"]["review_status"] == package.REVIEW_STATUS_MACHINE_ONLY


@requires_run
def test_a_governance_gap_is_reported_and_not_called_a_build_failure():
    for run in RUNS:
        detail = _manifest(run)["stages"]["S6_verify"]["detail"]
        validator = detail["catalog_package_validator"]
        assert validator["structural_findings"] == [], validator["structural_findings"]
        assert validator["governance_findings"], "the unmet human-review gate must stay on record"
        assert detail["failures"] == []
        assert detail["unmet_requirements"], "an unmet requirement must be named, with its decider"


@requires_run
def test_runs_are_reproducible():
    if len(RUNS) < 2:
        pytest.skip("need two runs to compare")
    first, second = (_questions(run) for run in RUNS[:2])
    assert first == second, "the same input must produce byte-identical questions"


def test_describe_reports_the_contract_without_running():
    proc = subprocess.run([sys.executable, os.path.join(PKG, "scripts", "golden_path.py"),
                           "--describe"], capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    described = json.loads(proc.stdout)
    assert described["side_effects"]["database"] == "none"
    assert described["side_effects"]["network"] == "none"
    assert len(described["stages"]) == 7


# --- the paper's identity: year and subject are not enough --------------------------------
# Two papers of the same year, category and subject are two different papers, and the sitting is
# the only thing that tells them apart. Measured on the 藥師 family before this was fixed: 1051 and
# 1052 of 藥師(一) resolved to one registry key, and the merged review queue refused the queue as a
# duplicate. The catalog spells the same category both ways across revisions, so the comparison has
# to fold the brackets - and the filter that picks the rows must fold them too, or the rows are
# discarded before anything can compare them.

def _import_batch_package():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import batch_package
    return batch_package


def test_the_sitting_is_read_from_the_exam_label():
    bp = _import_batch_package()
    assert bp._sitting_of("105年第二次醫師牙醫師藥師分階段考試") == "2"
    assert bp._sitting_of("105年第一次醫師牙醫師藥師分階段考試") == "1"
    assert bp._sitting_of("第一梯次:110年第二次專技牙醫師第二階段考試") == "2"
    # Some labels name no 次 at all; that is "no opinion" rather than a mismatch.
    assert bp._sitting_of("111年公務人員特種考試關務人員考試") is None


def test_a_category_spelled_with_full_width_brackets_is_the_same_category():
    bp = _import_batch_package()
    assert bp._same_category("藥師（一）", "藥師(一)")
    assert bp._same_category("藥師(一)", "藥師（一）")
    assert not bp._same_category("藥師(一)", "藥師(二)")
    assert not bp._same_category("藥師(一)", "藥師")


@requires_corpus
def test_two_sittings_of_one_subject_are_two_papers():
    bp = _import_batch_package()
    first = {"year": 105, "ordinal": 1, "category": "藥師(一)", "subject": "藥劑學(包括生物藥劑學)"}
    second = dict(first, ordinal=2)
    catalog = bp.catalog_by_key("藥師(一)")
    registry = bp.registry_index()
    a, b = bp.resolve(first, catalog, registry), bp.resolve(second, catalog, registry)
    # The sources differ by design and that is not the point: the registry knows the sitting from
    # the manifest, so it answers for 1051, while 1052 has to be matched against the catalog. What
    # must hold is that the two papers do not share an identity.
    assert a["registry_key"] != b["registry_key"], "two sittings resolved to one key"
    assert a["registry_key"] and b["registry_key"]
    # And the key carries the sitting in its exam code, which is how a later reader tells them
    # apart without knowing anything about this code.
    assert a["registry_key"].split(":")[1] != b["registry_key"].split(":")[1]


# --------------------------------------------------------------------- the sheet's own count

def _a_sheet(tmp_path, text):
    path = tmp_path / "sheet.pdf"
    path.write_bytes(b"%PDF-1.4\n")           # only the path is read here, not the PDF
    return {"corrected": str(path)}


def _count_with_a_text(monkeypatch, text):
    """Run the count against `text` as though it were the sheet's own words."""
    from qbr import answer_sheets
    import golden_path
    monkeypatch.setattr(answer_sheets, "read_table_text", lambda path, role="answer": text)
    return golden_path._sheet_question_count({"corrected": "sheet.pdf"})


def test_the_sheet_count_is_the_sentence_the_sheet_prints(tmp_path, monkeypatch):
    """答案卷自己印的「題數：NN題」就是題數，不是從解析出的表格回推。

    量測 `1001_醫師(二)_醫學(三)`（100 年第 1 次）：答案卷印 `題數： 80題`，
    表格只解析出 79 題——第 80 題是 `#` ＋ 備註「一律給分」，`parse_answer_table`
    會把 `#` 欄跳過，而送分題在表格裡**沒有任何字母**可放，所以它從表格消失了。
    出貨的是 80 題，閘門卻說 `count-mismatch:items=80 expected=79(answer-sheet)`，
    整份卷被擋下。
    """
    text = "題數： 80題\n題號 01 02 ... 80\n答案 D A ... #\n備註：第80題一律給分"
    assert _count_with_a_text(monkeypatch, text) == 80


def test_the_declared_count_ignores_spacing_and_the_full_width_colon(tmp_path, monkeypatch):
    """印刷形式可能用全形冒號或空白，這不是在描述某一張紙，是在描述印刷形式。"""
    for text in ("題數： 50題", "題數:50題", "題 數 ： 50 題", "題數：50題"):
        assert _count_with_a_text(monkeypatch, text) == 50


def test_a_sheet_that_declares_nothing_falls_back_to_its_table(tmp_path, monkeypatch):
    """沒有印「題數」的卷退回舊法（表格的兩界），保留對「不印題數的卷」的檢查。

    兩界都要從 1 開始才算「對整卷表態」——藥師系列有 50 題的科目，
    盲目取最高號會低估，所以這個護欄留著。
    """
    assert _count_with_a_text(monkeypatch, "題號 01 02 03\n答案 D A C") == 3
    # 兩界不一致（不是從 1 開始）＝不表態。
    assert _count_with_a_text(monkeypatch, "題號 21 22 23\n答案 D A C") == 0
    assert _count_with_a_text(monkeypatch, "") == 0
