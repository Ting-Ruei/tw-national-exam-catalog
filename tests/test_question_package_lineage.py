from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
EXPORTER_SCRIPT = ROOT / "scripts" / "export_question_bank_package_from_postgres.py"
VALIDATOR_SCRIPT = ROOT / "scripts" / "validate_question_bank_package.py"


def import_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_question(review_status: str) -> dict:
    """A question that passes every required-field check except the review-status one."""
    return {
        "source_question_key": "moex:115090:308:0504:1:question:q007",
        "source_registry_key": "moex:115090:308:0504:1:question",
        "stem": "Which answer?",
        "options": [{"key": "A", "text": "choice A"}],
        "answer": ["A"],
        "question_type": "single_choice",
        "metadata": {
            "review_status": review_status,
            "source_content_hash": "b" * 64,
            "canonical_subject_name": "生物化學與臨床生化學",
        },
    }


class ReviewStatusGateTests(unittest.TestCase):
    """The G4 gate: what a package may claim about how far it has been verified.

    Each test states the case that must fail, because the failure mode here is silent: a gate
    loosened by one comparison admits more than intended and still prints "pass".
    """

    @classmethod
    def setUpClass(cls):
        cls.validator = import_script("question_package_validator_gate", VALIDATOR_SCRIPT)

    def codes(self, questions):
        issues: list[dict] = []
        self.validator.validate_questions(SimpleNamespace(max_examples=20), questions, [], [], issues)
        return {issue["code"] for issue in issues if issue["count"]}

    def test_agent_verified_is_deliverable_but_reported_separately(self):
        codes = self.codes([make_question("agent_verified")])
        # Deliverable: the required-fields error must not fire.
        self.assertNotIn("package_question_required_fields_invalid", codes)
        # But never silent: the weaker status is its own warning, so a reader can count how much
        # of the package met a person. Collapsing it into `accepted` is what this forbids.
        self.assertIn("package_agent_verified_not_human_accepted", codes)

    def test_human_accepted_raises_no_warning(self):
        codes = self.codes([make_question("accepted")])
        self.assertNotIn("package_question_required_fields_invalid", codes)
        self.assertNotIn("package_agent_verified_not_human_accepted", codes)

    def test_an_unreviewed_machine_record_is_still_refused(self):
        # The negative control for the loosened gate: `machine_verified_pending_human` was already
        # the pipeline's own default, so accepting it here would make the gate accept a record
        # whose only claim is that a script ran. It must stay refused.
        codes = self.codes([make_question("machine_verified_pending_human")])
        self.assertIn("package_question_required_fields_invalid", codes)

    def test_a_record_without_any_review_status_is_refused(self):
        # A missing status must fail, not be silently treated as verified.
        question = make_question("accepted")
        del question["metadata"]["review_status"]
        self.assertIn("package_question_required_fields_invalid", self.codes([question]))

    def test_agent_verified_is_not_the_accepted_string(self):
        # Spelled out because the whole point is that these two words never become one. If a
        # future edit made `agent_verified` an alias of `accepted`, an agent would be recorded as
        # a human reviewer — the governance floor's one prohibition.
        self.assertNotEqual(
            getattr(package_module(), "REVIEW_STATUS_AGENT_VERIFIED", None), "accepted")
        self.assertNotEqual(
            getattr(package_module(), "REVIEW_STATUS_AGENT_VERIFIED", None),
            getattr(package_module(), "REVIEW_STATUS_MACHINE_ONLY", None),
            "agent_verified must not be the untouched-backlog default either",
        )


def golden_path_module():
    """`qbr/scripts/golden_path.py`, imported by path (it puts qbr/src on sys.path itself)."""
    return import_script("qbr_golden_path_for_gate_test", ROOT / "qbr" / "scripts" / "golden_path.py")


class ValidatorIssueClassificationTests(unittest.TestCase):
    """A validator refusal caused only by the review requirement is a governance gap.

    This is the seam where the two modules have to agree: the validator decides the wording of
    "missing field", and the pipeline reads it to decide whether a run *failed*. When the wording
    changed, an exact-string match silently turned every unreviewed package into a build defect.
    """

    @classmethod
    def setUpClass(cls):
        cls.golden = golden_path_module()

    def report(self, missing):
        return {"issues": [{
            "severity": "error", "code": "package_question_required_fields_invalid",
            "count": 1, "examples": [{"source_question_key": "k", "missing": missing}],
        }]}

    def test_a_pending_human_review_is_a_governance_gap(self):
        structural, governance = self.golden.classify_validator_issues(
            self.report(["metadata.review_status in (agent_verified, accepted)"]))
        self.assertEqual(structural, [])
        self.assertEqual(governance, [{"code": "package_question_required_fields_invalid", "count": 1}])

    def test_the_old_wording_is_still_classified_as_governance(self):
        # The wording this pipeline was written against before the gate was loosened. Both forms
        # must classify the same way, or the release after this change reclassifies old runs.
        structural, governance = self.golden.classify_validator_issues(
            self.report(["metadata.review_status=accepted"]))
        self.assertEqual(structural, [])
        self.assertEqual(len(governance), 1)

    def test_a_missing_stem_is_a_structural_defect(self):
        # The negative control: an issue that is *not* only about review status must still fail the
        # run. Without this, the prefix rule could be widened until nothing is ever structural.
        structural, governance = self.golden.classify_validator_issues(
            self.report(["stem", "metadata.review_status=accepted"]))
        self.assertEqual(governance, [])
        self.assertEqual(structural[0]["missing"], ["stem", "metadata.review_status=accepted"])

    def test_a_missing_review_status_alone_is_structural(self):
        # A record with *no* status field at all is not "waiting for a human" — it is a record that
        # never said what it was. That is a build defect, and the empty-missing guard is why.
        structural, governance = self.golden.classify_validator_issues(self.report([]))
        self.assertEqual(governance, [])
        self.assertEqual(len(structural), 1)


def package_module():
    """`qbr/src/qbr/package.py`, imported by path so the test does not need qbr installed."""
    return import_script("qbr_package_for_gate_test", ROOT / "qbr" / "src" / "qbr" / "package.py")


class QuestionPackageLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exporter = import_script("question_package_exporter", EXPORTER_SCRIPT)
        cls.validator = import_script("question_package_validator", VALIDATOR_SCRIPT)

    def test_export_preserves_correction_document_identity_and_pdf_hash(self):
        row = {
            "question_key": "moex:115090:308:0504:1:question:q007",
            "question_number": "7",
            "question_text": "Which answer?",
            "question_json": {"stem": "Which answer?"},
            "question_raw_json": {"question_type": "single_choice"},
            "parser_version": "parser-v1",
            "review_status": "accepted",
            "answer_value": "A",
            "answer_json": {"answer": "A", "accepted_values": ["A"]},
            "answer_source_registry_key": "moex:115090:308:0504:1:correction",
            "answer_document_role": "correction",
            "question_pdf_relative": "國考題資料夾/115/questions.pdf",
            "question_pdf_sha256": "b" * 64,
            "answer_pdf_relative": "國考題資料夾/115/corrections.pdf",
            "answer_pdf_sha256": "a" * 64,
            "source_registry_key": "moex:115090:308:0504:1:question",
            "question_set": "1",
            "roc_year": 115,
            "exam_number": 2,
            "exam_code": "115090",
            "category_code": "308",
            "subject_code": "0504",
            "official_category_name": "醫事檢驗師",
            "normalized_category_name": "醫事檢驗師",
            "official_subject_name": "生物化學與臨床生化學",
            "normalized_subject_name": "生物化學與臨床生化學",
            "canonical_subject_name": "生物化學與臨床生化學",
            "options": [{"option_label": "A", "option_text": "choice A"}],
            "assets": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            record, assets, warnings = self.exporter.build_question_record(
                row, "package-v1", "medtech", Path(temporary), copy_assets=False,
            )

        self.assertEqual(assets, [])
        self.assertEqual(warnings, [])
        self.assertEqual(record["answer_source_registry_key"], row["answer_source_registry_key"])
        metadata = record["metadata"]
        self.assertEqual(metadata["question_pdf_relative"], row["question_pdf_relative"])
        self.assertEqual(metadata["question_pdf_sha256"], row["question_pdf_sha256"])
        self.assertEqual(metadata["answer_pdf_primary_relative"], row["answer_pdf_relative"])
        self.assertEqual(metadata["answer_source_registry_keys"], [row["answer_source_registry_key"]])
        self.assertEqual(metadata["answer_source_documents"], [{
            "role": "correction",
            "registry_key": row["answer_source_registry_key"],
            "pdf_relative": row["answer_pdf_relative"],
            "sha256": row["answer_pdf_sha256"],
        }])

    def test_manifest_file_and_package_hashes_reject_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_dir = Path(temporary)
            contents = {
                "questions": ("questions.jsonl", "{}\n"),
                "subjects": ("subjects.json", "{}\n"),
                "groups": ("groups.jsonl", ""),
                "asset_manifest": ("asset_manifest.jsonl", ""),
            }
            files = {}
            for name, (relative_path, text) in contents.items():
                path = package_dir / relative_path
                path.write_text(text, encoding="utf-8")
                data = path.read_bytes()
                files[name] = {
                    "path": relative_path,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            asset_path = package_dir / "assets" / "fixture.bin"
            asset_path.parent.mkdir()
            asset_path.write_bytes(b"asset")
            content_payload = {
                name: files[name]["sha256"]
                for name in ("questions", "subjects", "groups", "asset_manifest")
            }
            content_payload["asset_files"] = [hashlib.sha256(b"asset").hexdigest()]
            manifest = {
                "files": {**files, "warnings": None},
                "counts": {"warnings": 0},
                "package_content_sha256": hashlib.sha256(
                    json.dumps(content_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            }
            args = SimpleNamespace(max_examples=20)

            issues = []
            self.validator.validate_manifest_files(args, package_dir, manifest, issues)
            self.assertEqual(issues, [])

            changed_questions = package_dir / "questions.jsonl"
            changed_questions.write_text('{"tampered":true}\n', encoding="utf-8")
            issues = []
            self.validator.validate_manifest_files(args, package_dir, manifest, issues)
            self.assertIn(
                "package_manifest_file_sha256_mismatch",
                {issue["code"] for issue in issues},
            )

            changed_bytes = changed_questions.read_bytes()
            manifest["files"]["questions"]["bytes"] = len(changed_bytes)
            manifest["files"]["questions"]["sha256"] = hashlib.sha256(changed_bytes).hexdigest()
            issues = []
            self.validator.validate_manifest_files(args, package_dir, manifest, issues)
            self.assertIn("package_content_sha256_mismatch", {issue["code"] for issue in issues})

    def test_answer_validation_requires_the_exact_source_document_role(self):
        question = {
            "source_question_key": "moex:115090:308:0504:1:question:q007",
            "answer": ["A"],
            "metadata": {
                "question_pdf_relative": "國考題資料夾/115/questions.pdf",
                "question_pdf_sha256": "b" * 64,
                "answer_source_registry_key": "moex:115090:308:0504:1:correction",
                "answer_source_registry_keys": ["moex:115090:308:0504:1:correction"],
                "answer_source_documents": [{
                    "role": "correction",
                    "registry_key": "moex:115090:308:0504:1:correction",
                    "pdf_relative": "國考題資料夾/115/corrections.pdf",
                    "sha256": "a" * 64,
                }],
            },
        }
        args = SimpleNamespace(max_examples=20)
        issues = []
        self.validator.validate_source_document_lineage(args, [question], issues)
        self.assertEqual(issues, [])

        question["metadata"]["answer_source_documents"][0]["role"] = "answer"
        issues = []
        self.validator.validate_source_document_lineage(args, [question], issues)
        self.assertIn(
            "package_answer_pdf_lineage_invalid",
            {issue["code"] for issue in issues},
        )


    def test_non_relative_source_and_asset_paths_are_rejected(self):
        args = SimpleNamespace(max_examples=20)
        invalid_paths = (
            "/tmp/answer.pdf",
            "C:/private/answer.pdf",
            r"C:\private\answer.pdf",
            r"\\server\share\answer.pdf",
            "https://example.test/answer.pdf",
        )
        with tempfile.TemporaryDirectory() as temporary:
            package_dir = Path(temporary)
            for path in invalid_paths:
                source_key = "moex:115090:308:0504:1:question:q007"
                answer_key = "moex:115090:308:0504:1:correction"
                question = {
                    "source_question_key": source_key,
                    "answer": ["A"],
                    "metadata": {
                        "question_pdf_relative": path,
                        "question_pdf_sha256": "b" * 64,
                        "answer_source_registry_key": answer_key,
                        "answer_source_registry_keys": [answer_key],
                        "answer_source_documents": [{
                            "role": "correction",
                            "registry_key": answer_key,
                            "pdf_relative": path,
                            "sha256": "a" * 64,
                        }],
                    },
                }
                issues = []
                self.validator.validate_source_document_lineage(args, [question], issues)
                codes = {issue["code"] for issue in issues}
                self.assertIn("package_question_pdf_lineage_invalid", codes, path)
                self.assertIn("package_answer_pdf_lineage_invalid", codes, path)

                issues = []
                self.validator.validate_assets(
                    args, package_dir, [{"package_path": path, "sha256": "c" * 64}], issues,
                )
                self.assertIn(
                    "package_asset_path_invalid",
                    {issue["code"] for issue in issues},
                    path,
                )


if __name__ == "__main__":
    unittest.main()
