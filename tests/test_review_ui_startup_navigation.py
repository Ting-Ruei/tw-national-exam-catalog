"""An area opened while queue_index is loading must remain open afterward."""
from __future__ import annotations

from test_review_ui_discuss import run_node


def test_area_navigation_during_queue_index_fetch_survives_scope_initialization():
    result = run_node("""(async () => {
      const realShowArea = showArea;
      showArea = () => {};
      renderArea = () => {};
      history.replaceState = (_state, _title, url) => { location.hash = url; };
      location.hash = '#藥師/115/2/藥劑學/q41?discuss=paper%3Aq501';
      let releaseIndex;
      fetch = async () => new Promise((resolve) => { releaseIndex = resolve; });
      applyScope = () => scopeToHash();
      const tree = {藥師: {papers: 1, years: {'115': {papers: 1, sittings: {
        '2': {papers: 1, questions: 1, subjects: {
          藥劑學: {papers: ['paper'], questions: 1}
        }}
      }}}}};
      const startup = boot().then(() => realShowArea(areaFromHash(), {push: false}));
      realShowArea('discuss');
      const during = location.hash;
      releaseIndex({ok: true, json: async () => ({taxonomy: tree})});
      await startup;
      return {area: A.area, during, selection: discussSelectionFromUrl(), hash: location.hash};
    })()""")

    assert result["area"] == "discuss"
    assert result["during"].startswith("#錯題/")
    assert result["selection"] == "paper:q501"
    assert "discuss=paper:q501" in result["hash"]
