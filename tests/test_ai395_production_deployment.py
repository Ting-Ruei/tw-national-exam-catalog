from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Ai395ProductionDeploymentTests(unittest.TestCase):
    def test_production_compose_has_no_runtime_dependency_install(self) -> None:
        compose = (ROOT / "deploy" / "ai395" / "compose.production.yaml").read_text(encoding="utf-8")
        self.assertNotIn("pip install", compose)
        self.assertIn("REVIEW_UI_BASIC_AUTH_PASSWORD", compose)
        self.assertIn("REVIEW_UI_READ_ONLY", compose)
        self.assertIn("REVIEW_UI_ALLOW_PROJECT_FILES", compose)
        self.assertIn("127.0.0.1:${POSTGRES_PORT", compose)
        self.assertIn(":/workspace:ro", compose)
        self.assertIn(":/assets:ro", compose)
        self.assertIn(":/assets/40_manual_assets:rw", compose)

    def test_production_helper_requires_explicit_restore_and_cutover_gates(self) -> None:
        helper = (ROOT / "scripts" / "ai395_catalog_production.sh").read_text(encoding="utf-8")
        self.assertIn("CATALOG_PRODUCTION_RESTORE_APPROVED", helper)
        self.assertIn("CATALOG_CUTOVER_APPROVED", helper)
        self.assertIn("ready_for_promotion", helper)
        self.assertIn("mismatches", (ROOT / "scripts" / "build_migration_asset_manifest.py").read_text())


if __name__ == "__main__":
    unittest.main()
