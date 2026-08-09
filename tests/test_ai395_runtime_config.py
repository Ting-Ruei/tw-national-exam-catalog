from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


class Ai395RuntimeConfigTests(unittest.TestCase):
    def test_client_defaults_point_at_ai395_production_and_keep_local_fallback_isolated(self):
        values = read_env(ROOT / ".env.example")
        self.assertEqual(values["CATALOG_RUNTIME_SSH_ALIAS"], "ai395")
        self.assertEqual(values["CATALOG_RUNTIME_UI_URL"], "http://192.168.10.90:8765/")
        self.assertEqual(values["CATALOG_RUNTIME_DB_PORT"], "54329")
        self.assertEqual(values["CATALOG_RUNTIME_LOCAL_DB_PORT"], "54330")
        self.assertEqual(values["CATALOG_RUNTIME_WRITE_ALLOWED"], "1")
        self.assertEqual(values["REVIEW_PRIMARY_UI_URL"], "http://192.168.10.90:8765/")
        self.assertEqual(values["REVIEW_PRIMARY_DB_HOST"], "127.0.0.1")
        self.assertEqual(values["CATALOG_RESTART_POLICY"], "no")
        self.assertEqual(values["POSTGRES_BIND"], "127.0.0.1")
        self.assertEqual(values["REVIEW_UI_BIND"], "127.0.0.1")

    def test_runtime_helper_is_valid_shell_and_has_no_restore_action(self):
        helper = ROOT / "scripts" / "ai395_catalog_runtime.sh"
        completed = subprocess.run(
            ["bash", "-n", str(helper)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        text = helper.read_text(encoding="utf-8")
        self.assertNotIn("|restore)", text)
        self.assertNotIn("|start-ui)", text)
        self.assertNotIn("|enable-writes)", text)

    def test_compose_local_defaults_are_loopback_and_restart_is_configurable(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("${CATALOG_RESTART_POLICY:-unless-stopped}", compose)
        self.assertIn("${POSTGRES_BIND:-127.0.0.1}", compose)
        self.assertIn("${REVIEW_UI_BIND:-127.0.0.1}", compose)

    def test_ai395_ubuntu_version_is_supported_by_preflight(self):
        path = ROOT / "scripts" / "migration_preflight.py"
        spec = importlib.util.spec_from_file_location("migration_preflight_ai395_test", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertIn("26.04", module.SUPPORTED_TARGET_UBUNTU_VERSIONS)


if __name__ == "__main__":
    unittest.main()
