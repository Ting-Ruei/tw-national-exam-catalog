"""A scope refresh must not expose stale rows or accept stale responses."""
from __future__ import annotations

from test_review_ui_discuss import run_node


_SCOPE_SETUP = """
  showArea = () => {};
  S.tree = {cat: {years: {'115': {sittings: {'2': {subjects: {
    subject: {papers: ['paper']}
  }}}}}}};
  S.scope = {category: 'cat', year: '115', sitting: '2', subject: 'subject'};
"""


def test_scope_refresh_clears_stale_rows_and_blocks_decisions_until_ready():
    result = run_node(f"""(async () => {{
      {_SCOPE_SETUP}
      S.view = [{{candidate_key: 'stale', question_number: '1'}}];
      S.rows = S.view;
      let resolveCandidates;
      const posts = [];
      fetch = (url, options = {{}}) => {{
        if (url.startsWith('/api/candidates?'))
          return new Promise((resolve) => {{ resolveCandidates = resolve; }});
        if (options.method === 'POST') posts.push(JSON.parse(options.body));
        return Promise.resolve({{ok: false, status: 503, json: async () => ({{ok: false}})}});
      }};
      const refresh = applyScope();
      const refreshing = S.refreshing === true;
      const rowsDuring = S.rows.map((item) => item.candidate_key);
      await decide('accept');
      resolveCandidates({{ok: true, json: async () => ({{candidates: [], filtered_count: 0}})}});
      await refresh;
      return {{refreshing, rowsDuring, posts, refreshingAfter: S.refreshing}};
    }})()""")

    assert result == {
        "refreshing": True,
        "rowsDuring": [],
        "posts": [],
        "refreshingAfter": False,
    }


def test_every_question_write_path_refuses_a_refreshing_row():
    result = run_node("""(async () => {
      showArea = () => {};
      const nodes = {};
      const get = document.getElementById;
      document.getElementById = (id) => nodes[id] || (nodes[id] = get(id));
      nodes.reasonText = document.getElementById('reasonText');
      nodes.editStem = document.getElementById('editStem');
      nodes.reasonText.value = 'note from the old row';
      nodes.editStem.value = 'corrected old row';
      S.refreshing = true;
      S.rows = [{candidate_key: 'stale', question_number: '1', candidate: {
        stem: 'old stem', options: []
      }}];
      S.index = 0;
      const posts = [];
      fetch = async (_url, options) => {
        posts.push(JSON.parse(options.body));
        return {ok: false, status: 503, json: async () => ({ok: false})};
      };
      await decide('accept');
      await saveCorrection();
      await saveNote();
      return posts;
    })()""")

    assert result == []


def test_an_older_scope_response_cannot_replace_the_newer_rows():
    result = run_node("""(async () => {
      showArea = () => {};
      S.tree = {cat: {years: {'115': {sittings: {'2': {subjects: {
        subject: {papers: ['paper']}
      }}}}}}};
      S.scope = {category: 'cat', year: '115', sitting: '2', subject: 'subject'};
      S.openQuestion = '';
      renderCrumbs = () => {};
      renderList = () => {};
      go = async (index) => { S.index = index; };
      const pending = [];
      fetch = (url) => url.startsWith('/api/candidates?')
        ? new Promise((resolve) => pending.push(resolve))
        : Promise.resolve({ok: false, status: 404, json: async () => ({})});
      const older = applyScope();
      const newer = applyScope();
      const row = (key) => ({candidate_key: key, question_number: '1', metadata: {
        question_pdf_relative: 'paper.pdf', normalized_category_name: 'cat',
        normalized_subject_name: 'subject', year: '115', exam_ordinal: '2'
      }});
      pending[1]({ok: true, json: async () => ({candidates: [row('newer')], filtered_count: 1})});
      await newer;
      pending[0]({ok: true, json: async () => ({candidates: [row('older')], filtered_count: 1})});
      await older;
      return {rows: S.rows.map((item) => item.candidate_key), refreshing: S.refreshing};
    })()""")

    assert result == {"rows": ["newer"], "refreshing": False}
