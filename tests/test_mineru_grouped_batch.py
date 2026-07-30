from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_mineru_pdf_batch.py"
SPEC = importlib.util.spec_from_file_location("mineru_batch", SCRIPT_PATH)
assert SPEC and SPEC.loader
mineru_batch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mineru_batch
SPEC.loader.exec_module(mineru_batch)


class GroupedMinerUTests(unittest.TestCase):
    def test_non_positive_timeout_means_unlimited(self) -> None:
        self.assertIsNone(mineru_batch.subprocess_timeout(0))
        self.assertIsNone(mineru_batch.subprocess_timeout(-1))
        self.assertEqual(mineru_batch.subprocess_timeout(30), 30)

    def test_group_uses_one_process_for_multiple_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_dir = root / "pdf"
            output_parent = root / "output"
            input_dir = root / "inputs"
            pdf_dir.mkdir()
            tasks = []
            for stem in ("question", "answer"):
                pdf = pdf_dir / f"{stem}.pdf"
                pdf.write_bytes(b"%PDF-test")
                tasks.append(
                    mineru_batch.MinerUTask(
                        task_id=stem,
                        scope="paired-primary",
                        group_name="test",
                        document_role=stem,
                        pair_status="paired_ans_only",
                        pdf_path=str(pdf),
                        pdf_relative=str(pdf),
                        sha256="",
                        output_parent=str(output_parent),
                        expected_md=str(output_parent / stem / "vlm" / f"{stem}.md"),
                    )
                )

            def fake_run(command, **kwargs):
                source_dir = Path(command[command.index("-p") + 1])
                output_dir = Path(command[command.index("-o") + 1])
                for pdf in source_dir.glob("*.pdf"):
                    md = output_dir / pdf.stem / "vlm" / f"{pdf.stem}.md"
                    md.parent.mkdir(parents=True, exist_ok=True)
                    md.write_text("ok", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with mock.patch.object(mineru_batch.subprocess, "run", side_effect=fake_run) as run_mock:
                results = mineru_batch.run_group(Path("/fake/mineru"), tasks, 30, False, input_dir)

            self.assertEqual(run_mock.call_count, 1)
            self.assertEqual({result.status for result in results}, {"ok"})
            self.assertEqual(len(list(input_dir.glob("*.pdf"))), 2)


if __name__ == "__main__":
    unittest.main()
