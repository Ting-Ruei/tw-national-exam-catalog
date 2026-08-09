import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_agent_governance as governance  # noqa: E402


class AgentGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = governance.load_policy()

    def test_policy_contract_is_valid(self):
        self.assertEqual([], governance.validate_policy(self.policy))

    def test_repository_boundaries_are_valid(self):
        self.assertEqual([], governance.validate_repository(self.policy))

    def test_g3_and_g4_actions_cannot_be_agent_initiated(self):
        for action in self.policy["actions"]:
            if action["level"] in {"G3", "G4"}:
                with self.subTest(action=action["id"]):
                    self.assertFalse(action["agent_may_initiate"])

    def test_human_review_decision_is_owner_only(self):
        actions = {action["id"]: action for action in self.policy["actions"]}
        decision = actions["review.human_decision"]
        self.assertEqual("G4", decision["level"])
        self.assertEqual("owner_only", decision["approval"])
        self.assertIn("agent_must_not_impersonate_reviewer", decision["constraints"])

    def test_main_requires_named_checks(self):
        self.assertEqual(
            {"governance", "unit-tests"},
            set(self.policy["required_pr_checks"]),
        )

    def test_ruleset_rollout_does_not_lock_single_owner(self):
        controls = self.policy["github_controls"]
        self.assertEqual(0, controls["current_single_owner_required_approvals"])
        self.assertEqual(1, controls["distinct_agent_identity_required_approvals"])
        self.assertFalse(controls["agent_ruleset_bypass"])


if __name__ == "__main__":
    unittest.main()
