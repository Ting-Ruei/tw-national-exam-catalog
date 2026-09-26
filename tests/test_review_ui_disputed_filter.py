"""The dispute filter excludes pipeline-returned rows without hiding human-reviewed disputes."""
from __future__ import annotations

from test_review_ui_discuss import run_node


def test_disputed_filter_excludes_returned_rows_but_keeps_human_decisions():
    result = run_node("""(() => {
      document.querySelector = () => ({value: 'disputed'});
      const dispute = [{kind: 'content-mismatch'}];
      S.view = [
        {candidate_key: 'returned', candidate: {disputes: dispute}},
        {candidate_key: 'accepted', candidate: {disputes: dispute}},
      ];
      S.verdict.clear();
      S.verdict.set('returned', 'reset_review');
      S.verdict.set('accepted', 'accept');
      return visibleRows().map((item) => item.candidate_key);
    })()""")

    assert result == ["accepted"]
