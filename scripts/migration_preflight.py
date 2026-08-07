#!/usr/bin/env python3
"""Read-only source/target readiness checks for the Ryzen host migration.

The report never prints secret values.  It is intentionally independent of
project Python dependencies so it can run immediately after cloning the repo.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
DEFAULT_DATA_ROOT_NAMES = (
    "國考題資料夾",
    "國考題資料夾_其他類型",
    "國考題資料夾_非醫學剩餘全集",
)
SECRET_ENV_NAMES = {
    "DATABASE_URL",
    "OPENAI_API_KEY",
    "POSTGRES_PASSWORD",
    "REVIEW_UI_DATABASE_URL",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("source", "target"), required=True)
    parser.add_argument(
        "--asset-root",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="Data root to inspect. Repeat for multiple roots.",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Recursively count files and bytes. This can take minutes on the source hosts.",
    )
    parser.add_argument(
        "--min-free-gib",
        type=float,
        help="Required free space. Defaults to 25 GiB on a source and 250 GiB on the target.",
    )
    parser.add_argument("--env-file", type=Path, help="Load simple KEY=VALUE settings without printing values.")
    parser.add_argument("--json-output", type=Path, help="Also write the report as JSON.")
    return parser.parse_args()


def command_output(command: list[str], *, timeout: int = 20) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "returncode": None, "output": str(exc)}
    output = completed.stdout.strip()
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": output[-4000:],
    }


def load_env_file(path: Path) -> None:
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise SystemExit(f"Invalid env line {path}:{number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def add_check(
    report: dict[str, Any],
    check_id: str,
    status: str,
    message: str,
    **details: Any,
) -> None:
    item = {"id": check_id, "status": status, "message": message}
    if details:
        item["details"] = details
    report["checks"].append(item)


def parse_root_specs(mode: str, values: list[str]) -> list[tuple[str, Path]]:
    if not values:
        if mode == "source":
            return [(name, PROJECT_ROOT / name) for name in DEFAULT_DATA_ROOT_NAMES]
        configured = Path(os.environ.get("ASSET_ROOT", PROJECT_ROOT / "國考題資料夾")).expanduser()
        return [("國考題資料夾", configured)]

    roots: list[tuple[str, Path]] = []
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--asset-root must use LABEL=PATH: {value}")
        label, raw_path = value.split("=", 1)
        if not label or not raw_path:
            raise SystemExit(f"--asset-root must use LABEL=PATH: {value}")
        roots.append((label, Path(raw_path).expanduser()))
    return roots


def recursive_usage(root: Path) -> tuple[int, int, list[str]]:
    files = 0
    total_bytes = 0
    errors: list[str] = []
    for directory, _, names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in names:
            path = base / name
            try:
                stat = path.lstat()
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            files += 1
            total_bytes += stat.st_size
    return files, total_bytes, errors[:20]


def inspect_git(report: dict[str, Any]) -> None:
    head = command_output(["git", "rev-parse", "HEAD"])
    branch = command_output(["git", "branch", "--show-current"])
    status = command_output(["git", "status", "--porcelain=v1", "--untracked-files=all"])
    if not (head["ok"] and status["ok"]):
        add_check(report, "git", "fail", "Git repository state could not be read.", head=head, status=status)
        return

    lines = [line for line in status["output"].splitlines() if line]
    untracked = sum(line.startswith("??") for line in lines)
    tracked_dirty = len(lines) - untracked
    report["git"] = {
        "head": head["output"],
        "branch": branch["output"] if branch["ok"] else None,
        "tracked_dirty": tracked_dirty,
        "untracked": untracked,
    }
    if lines:
        add_check(
            report,
            "git-clean",
            "fail",
            "Worktree is not snapshot-ready; reconcile or preserve every change before cutover.",
            tracked_dirty=tracked_dirty,
            untracked=untracked,
            sample=lines[:30],
        )
    else:
        add_check(report, "git-clean", "pass", "Worktree is clean and can be pinned to a commit.")


def inspect_commands(report: dict[str, Any], mode: str) -> None:
    required = ["git", "rsync", "docker"]
    if mode == "target":
        required.extend(["rocminfo", "ollama"])
    for name in required:
        path = shutil.which(name)
        add_check(
            report,
            f"command-{name}",
            "pass" if path else ("fail" if mode == "target" or name in {"git", "rsync"} else "warn"),
            f"{name} is {'available' if path else 'not available'}.",
            path=path,
        )

    docker = shutil.which("docker")
    if docker:
        compose = command_output([docker, "compose", "version"])
        add_check(
            report,
            "docker-compose",
            "pass" if compose["ok"] else ("fail" if mode == "target" else "warn"),
            "Docker Compose plugin is usable." if compose["ok"] else "Docker Compose plugin is not usable.",
            output=compose["output"],
        )
        if mode == "target":
            daemon = command_output([docker, "info", "--format", "{{.ServerVersion}} {{.OSType}}/{{.Architecture}}"])
            add_check(
                report,
                "docker-daemon",
                "pass" if daemon["ok"] else "fail",
                "Docker daemon is usable." if daemon["ok"] else "Docker daemon is not usable.",
                output=daemon["output"],
            )
            config = command_output([docker, "compose", "config", "--quiet"])
            add_check(
                report,
                "compose-config",
                "pass" if config["ok"] else "fail",
                "Compose configuration is valid." if config["ok"] else "Compose configuration is invalid.",
                output=config["output"],
            )


def inspect_platform(report: dict[str, Any], mode: str) -> None:
    system = platform.system()
    machine = platform.machine().lower()
    report["platform"] = {
        "system": system,
        "release": platform.release(),
        "machine": machine,
        "python": platform.python_version(),
    }
    if mode != "target":
        add_check(report, "platform", "pass", f"Source platform recorded: {system}/{machine}.")
        return

    linux_ok = system == "Linux"
    arch_ok = machine in {"x86_64", "amd64"}
    add_check(report, "target-linux", "pass" if linux_ok else "fail", f"Target OS is {system}.")
    add_check(report, "target-arch", "pass" if arch_ok else "fail", f"Target architecture is {machine}.")

    os_release = Path("/etc/os-release")
    if os_release.exists():
        values: dict[str, str] = {}
        for line in os_release.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        report["platform"]["os_release"] = values
        supported = values.get("ID") == "ubuntu" and values.get("VERSION_ID") == "24.04"
        add_check(
            report,
            "target-os-release",
            "pass" if supported else "warn",
            "Ubuntu 24.04 detected." if supported else "Target is outside the documented Ubuntu 24.04 baseline.",
            id=values.get("ID"),
            version_id=values.get("VERSION_ID"),
        )


def inspect_environment(report: dict[str, Any], mode: str) -> None:
    names = (
        "TW_EXAM_REPO",
        "ASSET_ROOT",
        "MINERU_BIN",
        "DATABASE_URL",
        "REVIEW_UI_DATABASE_URL",
        "POSTGRES_PASSWORD",
        "POSTGRES_BIND",
        "REVIEW_UI_BIND",
        "REVIEW_PRIMARY_UI_URL",
        "OLLAMA_BASE_URL",
    )
    report["environment"] = {
        name: {"set": bool(os.environ.get(name)), "secret": name in SECRET_ENV_NAMES}
        for name in names
    }
    if mode != "target":
        return

    for name in (
        "TW_EXAM_REPO",
        "ASSET_ROOT",
        "MINERU_BIN",
        "DATABASE_URL",
        "REVIEW_UI_DATABASE_URL",
        "POSTGRES_PASSWORD",
        "REVIEW_UI_BIND",
        "REVIEW_PRIMARY_UI_URL",
        "OLLAMA_BASE_URL",
    ):
        present = bool(os.environ.get(name))
        add_check(
            report,
            f"env-{name.lower()}",
            "pass" if present else "fail",
            f"{name} is {'set' if present else 'missing'}.",
        )

    password = os.environ.get("POSTGRES_PASSWORD", "")
    insecure = not password or password in {"national_exam_dev_password", "CHANGE_ME_USE_A_LONG_RANDOM_PASSWORD"}
    add_check(
        report,
        "postgres-password",
        "fail" if insecure else "pass",
        "PostgreSQL password is still missing/default." if insecure else "PostgreSQL password is non-default.",
    )

    placeholders = [
        name
        for name in (
            "DATABASE_URL",
            "REVIEW_UI_DATABASE_URL",
            "REVIEW_PRIMARY_UI_URL",
            "TW_EXAM_REPO",
            "ASSET_ROOT",
            "MINERU_BIN",
            "REVIEW_UI_BIND",
        )
        if "CHANGE_ME" in os.environ.get(name, "")
    ]
    add_check(
        report,
        "env-placeholders",
        "fail" if placeholders else "pass",
        f"Unresolved CHANGE_ME values: {', '.join(placeholders)}" if placeholders else "No required value contains CHANGE_ME.",
    )

    repo_value = os.environ.get("TW_EXAM_REPO")
    if repo_value:
        configured_repo = Path(repo_value).expanduser().resolve(strict=False)
        actual_repo = PROJECT_ROOT.resolve()
        add_check(
            report,
            "repo-path",
            "pass" if configured_repo == actual_repo else "fail",
            "TW_EXAM_REPO matches the running checkout." if configured_repo == actual_repo else "TW_EXAM_REPO does not match the running checkout.",
            configured=str(configured_repo),
            actual=str(actual_repo),
        )

    postgres_bind = os.environ.get("POSTGRES_BIND", "127.0.0.1")
    add_check(
        report,
        "postgres-bind",
        "pass" if postgres_bind in {"127.0.0.1", "::1", "localhost"} else "warn",
        f"PostgreSQL bind is {postgres_bind}; remote exposure requires an explicit firewall/VPN decision.",
    )

    review_bind = os.environ.get("REVIEW_UI_BIND", "127.0.0.1")
    broad_review_bind = review_bind in {"0.0.0.0", "::", "[::]"}
    add_check(
        report,
        "review-ui-bind",
        "warn" if broad_review_bind else "pass",
        (
            f"Review UI bind is {review_bind}; use a fixed LAN/VPN address instead of every interface."
            if broad_review_bind
            else f"Review UI bind is explicitly scoped to {review_bind}."
        ),
    )

    mineru_value = os.environ.get("MINERU_BIN")
    if mineru_value:
        mineru_path = Path(mineru_value).expanduser()
        executable = mineru_path.is_file() and os.access(mineru_path, os.X_OK)
        add_check(
            report,
            "mineru-executable",
            "pass" if executable else "fail",
            "Configured MinerU executable is usable." if executable else "Configured MinerU executable is missing or not executable.",
            path=str(mineru_path),
        )


def inspect_data_roots(
    report: dict[str, Any],
    roots: list[tuple[str, Path]],
    *,
    mode: str,
    deep: bool,
    min_free_gib: float,
) -> None:
    report["data_roots"] = []
    free_values: list[int] = []
    for label, root in roots:
        exists = root.is_dir()
        item: dict[str, Any] = {"label": label, "path": str(root), "exists": exists}
        if exists:
            usage = shutil.disk_usage(root)
            item["filesystem_free_bytes"] = usage.free
            free_values.append(usage.free)
            if deep:
                files, total_bytes, errors = recursive_usage(root)
                item.update(files=files, bytes=total_bytes, errors=errors)
        report["data_roots"].append(item)
        required = mode == "target" or label == "國考題資料夾"
        add_check(
            report,
            f"data-root-{label}",
            "pass" if exists else ("fail" if required else "warn"),
            f"Data root {label} {'exists' if exists else 'is missing'}.",
            path=str(root),
            files=item.get("files"),
            bytes=item.get("bytes"),
        )

    if not free_values:
        probe = PROJECT_ROOT
        free_values.append(shutil.disk_usage(probe).free)
    free_bytes = min(free_values)
    add_check(
        report,
        "free-space",
        "pass" if free_bytes >= min_free_gib * GIB else "fail",
        f"Minimum observed free space is {free_bytes / GIB:.1f} GiB; required {min_free_gib:.1f} GiB.",
        free_bytes=free_bytes,
        required_bytes=int(min_free_gib * GIB),
    )


def inspect_machine_references(report: dict[str, Any]) -> None:
    grep = command_output(
        [
            "git",
            "grep",
            "-n",
            "-E",
            r"/Users/tim|/Volumes/|/private/tmp|192\.168\.10\.70",
            "--",
            "scripts",
            "compose.yaml",
            ".env.example",
            ".env.devspace.example",
        ]
    )
    if grep["returncode"] not in {0, 1}:
        add_check(report, "machine-references", "warn", "Could not scan machine-specific references.", output=grep["output"])
        return
    lines = [line for line in grep["output"].splitlines() if line]
    add_check(
        report,
        "machine-references",
        "warn" if lines else "pass",
        f"Found {len(lines)} machine-specific reference(s) in operational files.",
        sample=lines[:30],
    )


def summarize(report: dict[str, Any]) -> None:
    counts = {status: 0 for status in ("pass", "warn", "fail")}
    for item in report["checks"]:
        counts[item["status"]] += 1
    report["summary"] = counts
    report["ready"] = counts["fail"] == 0


def print_report(report: dict[str, Any]) -> None:
    print(f"Migration preflight: mode={report['mode']} ready={str(report['ready']).lower()}")
    print(
        "Checks: "
        f"pass={report['summary']['pass']} "
        f"warn={report['summary']['warn']} "
        f"fail={report['summary']['fail']}"
    )
    for item in report["checks"]:
        print(f"[{item['status'].upper():4}] {item['id']}: {item['message']}")


def main() -> int:
    args = parse_args()
    if args.env_file:
        load_env_file(args.env_file.expanduser())
    roots = parse_root_specs(args.mode, args.asset_root)
    min_free_gib = args.min_free_gib if args.min_free_gib is not None else (250.0 if args.mode == "target" else 25.0)
    report: dict[str, Any] = {
        "schema_version": "tw_exam_migration_preflight_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": args.mode,
        "project_root": str(PROJECT_ROOT),
        "checks": [],
    }
    inspect_platform(report, args.mode)
    inspect_git(report)
    inspect_commands(report, args.mode)
    inspect_environment(report, args.mode)
    inspect_data_roots(
        report,
        roots,
        mode=args.mode,
        deep=args.deep,
        min_free_gib=min_free_gib,
    )
    inspect_machine_references(report)
    summarize(report)
    print_report(report)

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"JSON report: {args.json_output}")
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
