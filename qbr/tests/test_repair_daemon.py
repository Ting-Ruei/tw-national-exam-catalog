"""The scheduled repair daemon is evidence-only; model reads require an explicit command."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


QBR = Path(__file__).resolve().parents[1]
DAEMON = QBR / "scripts" / "repair_daemon.sh"


def _fake_python(tmp_path):
    executable = tmp_path / "fake-python"
    executable.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$PY_CALLS\"\n"
        "case \"$*\" in\n"
        "  *\"${FAIL_STAGE:-__never__}\"*) exit \"${FAIL_RC:-0}\" ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run(tmp_path, mode, *, extra_env=None):
    calls = tmp_path / "calls.txt"
    env = os.environ.copy()
    env.update({
        "QBR_PYTHON": str(_fake_python(tmp_path)),
        "PY_CALLS": str(calls),
        "QUEUE": str(tmp_path / "queue"),
        "RUN_DIR": str(tmp_path / "runs"),
        "ONCE": "1",
        "LANE": "mtplx-35b",
        "WINDOW": "1",
    })
    env.pop("ORCHESTRATE", None)
    env.pop("QBR_ALLOW_EXTERNAL_LLM", None)
    env.pop("QBR_LLM_ENV_FILE", None)
    env.update(extra_env or {})
    result = subprocess.run(["bash", str(DAEMON), mode], env=env,
                            text=True, capture_output=True, check=False)
    invocations = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, invocations


def test_scheduled_loop_scans_and_reports_without_model_or_apply(tmp_path):
    result, invocations = _run(tmp_path, "loop")

    assert result.returncode == 0, result.stderr
    assert len(invocations) == 2, invocations
    assert "scripts/scan_for_repairs.py" in invocations[0]
    assert "scripts/report_repair_progress.py" in invocations[1]
    assert all(name not in "\n".join(invocations) for name in (
        "confirm_dispute.py", "apply_dispute_repairs.py", "apply_text_corrections.py",
        "apply_experience_repairs.py", "crop_run_figures.py", "propose_principles.py"))
    assert "rc=0" in result.stdout


def test_scheduled_stage_failure_reaches_launchd_exit_status(tmp_path):
    result, invocations = _run(tmp_path, "loop", extra_env={
        "FAIL_STAGE": "report_repair_progress.py",
        "FAIL_RC": "7",
    })

    assert result.returncode == 7, result.stdout + result.stderr
    assert len(invocations) == 2, invocations
    assert "report 失敗（rc=7）" in result.stdout
    assert "rc=7" in result.stdout


def test_explicit_repair_is_bounded_and_pending_only(tmp_path):
    result, invocations = _run(tmp_path, "repair")

    assert result.returncode == 0, result.stderr
    assert len(invocations) == 1, invocations
    command = invocations[0]
    assert "scripts/confirm_dispute.py" in command
    assert "--pending-only" in command and "--skip-confirmed" in command
    assert "--limit 1" in command and "--model mtplx-35b" in command
    assert "apply_dispute_repairs.py" not in command


def test_remote_lane_override_is_rejected_before_invocation(tmp_path):
    result, invocations = _run(tmp_path, "repair", extra_env={"LANE": "dgx-qwen3.8-flash"})

    assert result.returncode == 2
    assert "拒絕非核准的本機 lane" in result.stderr
    assert invocations == []


def test_explicit_read_failure_reaches_the_callers_exit_status(tmp_path):
    result, invocations = _run(tmp_path, "repair", extra_env={
        "FAIL_STAGE": "confirm_dispute.py",
        "FAIL_RC": "9",
    })

    assert result.returncode == 9, result.stdout + result.stderr
    assert len(invocations) == 1 and "scripts/confirm_dispute.py" in invocations[0]
