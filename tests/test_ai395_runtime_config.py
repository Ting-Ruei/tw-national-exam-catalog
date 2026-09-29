from __future__ import annotations

from pathlib import Path
import unittest


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


class LocalRuntimeConfigTests(unittest.TestCase):
    def test_defaults_are_loopback_only_and_do_not_authorize_writes(self) -> None:
        values = read_env(ROOT / ".env.example")
        self.assertEqual(values["CATALOG_RUNTIME_SSH_ALIAS"], "")
        self.assertEqual(values["CATALOG_RUNTIME_UI_URL"], "http://127.0.0.1:8765/")
        self.assertEqual(values["CATALOG_RUNTIME_DB_PORT"], "54329")
        self.assertEqual(values["CATALOG_RUNTIME_LOCAL_DB_PORT"], "54330")
        self.assertEqual(values["CATALOG_RUNTIME_WRITE_ALLOWED"], "0")
        self.assertEqual(values["REVIEW_PRIMARY_UI_URL"], "http://127.0.0.1:8765/")
        self.assertEqual(values["REVIEW_PRIMARY_DB_HOST"], "127.0.0.1")
        self.assertEqual(values["CATALOG_RESTART_POLICY"], "no")
        self.assertEqual(values["POSTGRES_BIND"], "127.0.0.1")
        self.assertEqual(values["REVIEW_UI_BIND"], "127.0.0.1")

    def test_compose_local_defaults_are_loopback_and_restart_is_configurable(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("${CATALOG_RESTART_POLICY:-unless-stopped}", compose)
        self.assertIn("${POSTGRES_BIND:-127.0.0.1}", compose)
        self.assertIn("${REVIEW_UI_BIND:-127.0.0.1}", compose)


if __name__ == "__main__":
    unittest.main()
