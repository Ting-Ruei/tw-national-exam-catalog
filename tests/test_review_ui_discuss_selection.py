"""Discussion selection in the URL must survive the capped list response."""
from __future__ import annotations

from test_review_ui_discuss import load_ui_module, run_node


def test_discussion_selection_requests_and_selects_a_key_outside_the_first_page():
    result = run_node("""(async () => {
      showArea = () => {};
      location.hash = '#錯題/藥師/115/2/藥劑學/q41?discuss=paper%3Aq501';
      let request = null;
      fetchAreaJson = async (path, params) => {
        request = {path, params};
        return {candidates: [
          {candidate_key: 'paper:q1'},
          {candidate_key: 'paper:q501'},
        ]};
      };
      await loadDiscuss();
      return {path: request.path, focusKey: request.params.focusKey,
        selected: D.rows[D.index]};
    })()""")

    assert result == {
        "path": "/api/discuss",
        "focusKey": "paper:q501",
        "selected": "paper:q501",
    }

def test_discussion_selection_falls_back_to_local_storage():
    result = run_node("""(async () => {
      showArea = () => {};
      location.hash = '#錯題/藥師/115/2/藥劑學/q41';
      window.localStorage.getItem = (key) =>
        key === 'v2.discuss.selectedKey' ? 'paper:q501' : null;
      let request = null;
      fetchAreaJson = async (path, params) => {
        request = {path, params};
        return {candidates: [{candidate_key: 'paper:q501'}]};
      };
      await loadDiscuss();
      return {focusKey: request.params.focusKey, selected: D.rows[D.index]};
    })()""")

    assert result == {"focusKey": "paper:q501", "selected": "paper:q501"}


def test_selection_hash_survives_discussion_to_question_scope_round_trip():
    result = run_node("""(() => {
      showArea = () => {};
      const stored = {};
      window.localStorage.getItem = (key) => stored[key] || null;
      window.localStorage.setItem = (key, value) => { stored[key] = value; };
      location.hash = '#錯題/藥師/115/2/藥劑學/q41';
      history.replaceState = (_state, _title, url) => { location.hash = url; };
      rememberDiscussSelection('paper:q501');
      const selection = discussSelectionFromUrl();
      const scope = scopeFromHash();
      S.scope = scope;
      S.rows = [{question_number: '41'}];
      S.index = 0;
      scopeToHash();
      return {selection, stored: stored['v2.discuss.selectedKey'], scope,
        hash: location.hash};
    })()""")

    assert result == {
        "selection": "paper:q501",
        "stored": "paper:q501",
        "scope": {
            "category": "藥師",
            "year": "115",
            "sitting": "2",
            "subject": "藥劑學",
            "question": "q41",
        },
        "hash": "#錯題/%E8%97%A5%E5%B8%AB/115/2/%E8%97%A5%E5%8A%91%E5%AD%B8/q41?discuss=paper%3Aq501",
    }

def test_discuss_payload_injects_a_selected_row_beyond_the_500_row_cap():
    ui = load_ui_module()
    state = object.__new__(ui.ReviewState)
    candidates = [
        {"candidate_key": f"paper:q{index}", "question_number": str(index), "metadata": {}}
        for index in range(501)
    ]
    state.sql_review_enabled = False
    state.candidates = candidates
    state.candidate_by_key = {row["candidate_key"]: row for row in candidates}
    state.latest_reviews = {row["candidate_key"]: {"action": "block"} for row in candidates}
    state.latest_reset_reviews = {}
    state.latest_ai_reviews = {}
    state.issues = {}
    state.candidate_payload = lambda item: dict(item)
    state._mobile_review_maps = lambda keys: {}
    state.facets = lambda params: {}
    state.candidate_data_status = lambda: {}
    state.discuss_taxonomy = lambda: ({}, len(candidates))
    state.principles_events = []
    state.repair_questions_events = []

    payload = state.discuss_payload({
        "reviewStatus": "discuss",
        "limit": "500",
        "focusKey": "paper:q500",
    })

    selected = payload["candidates"][0]
    assert selected["candidate_key"] == "paper:q500"
    assert selected["focus_injected"] is True
    assert payload["filtered_count"] == 501
