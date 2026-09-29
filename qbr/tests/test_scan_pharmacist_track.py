"""The legacy pharmacist batch helper must stay bounded, local, sequential, and observable."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


QBR = Path(__file__).resolve().parents[1]
SCRIPT = QBR / "scripts" / "scan_pharmacist_track.sh"


def _run(tmp_path, *, fail_category=None):
    calls = tmp_path / "calls.txt"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$PY_CALLS\"\n"
        "case \"$*\" in *\"${FAIL_CATEGORY:-__never__}\"*) exit \"${FAIL_RC:-0}\";; esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "QBR_PYTHON": str(fake_python),
        "PY_CALLS": str(calls),
        "QUEUE": str(tmp_path / "approved-queue"),
        "RUNS": str(tmp_path / "runs"),
        "WINDOW": "1",
    })
    if fail_category:
        env.update(FAIL_CATEGORY=fail_category, FAIL_RC="7")
    result = subprocess.run(["bash", str(SCRIPT)], env=env, text=True,
                            capture_output=True, check=False)
    invocations = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, invocations


def test_category_scans_are_local_bounded_and_sequential(tmp_path):
    result, invocations = _run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(invocations) == 4, invocations
    for invocation in invocations[:3]:
        assert "--model mtplx-35b" in invocation
        assert "--limit 1" in invocation
        assert "--skip-confirmed" in invocation and "--apply" in invocation
    assert "藥師(一)" in invocations[0]
    assert "藥師(二)" in invocations[1]
    assert "--category 藥師" in invocations[2]
    assert "--report" in invocations[3]


def test_a_failed_lane_stops_the_batch_and_preserves_its_status(tmp_path):
    result, invocations = _run(tmp_path, fail_category="藥師(二)")

    assert result.returncode == 7, result.stdout + result.stderr
    assert len(invocations) == 2, invocations
    assert "藥師(二)" in invocations[1]
    assert "失敗（rc=7）" in result.stderr
