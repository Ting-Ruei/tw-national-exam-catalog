"""A human decision must outrank an older correction in the row's UI state."""
from __future__ import annotations

from test_review_ui_discuss import run_node


def test_latest_human_action_precedes_prior_correction_but_legacy_correction_survives():
    result = run_node("""(() => {
      const correction = {stem: 'paper text'};
      return {
        accepted: rowReviewAction({review: {action: 'accept', correction}}),
        blocked: rowReviewAction({review: {action: 'block', correction}}),
        needsReview: rowReviewAction({review: {action: 'needs_review', correction}}),
        legacyCorrection: rowReviewAction({review: {action: 'reviewed', correction}}),
        withdrawnReset: rowReviewAction({
          review: {action: 'reset_review', applied_kind: 'withdrawn', correction},
        }),
      };
    })()""")

    assert result == {
        "accepted": "accept",
        "blocked": "block",
        "needsReview": "needs_review",
        "legacyCorrection": "correct",
        "withdrawnReset": "",
    }
