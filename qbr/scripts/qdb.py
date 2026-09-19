"""Read-only SQL access to the throwaway restore of the review database copy.

The container `exam_copy_probe` is a disposable restore of the dumped copy under
`data/db_snapshot/`; the source database is never contacted by this module. Only SELECT
statements are issued here.

The SQL is kept as one plain literal (no string assembly) and is piped in through stdin
with `psql -f -`, which is the form that behaves predictably across the shells involved.
"""

import json
import subprocess

PROBE = "exam_copy_probe"
USER = "probe"
DB = "qbank_copy"

META_SQL = """
select (json_build_object(
    'id', c.id,
    'category', d.official_category_name,
    'subject', d.official_subject_name,
    'question_set', d.question_set,
    'role', d.document_role,
    'human', (
        select e.corrected_candidate_json
        from exam.question_review_events e
        where e.candidate_id = c.id and e.corrected_candidate_json is not null
        order by e.id desc
        limit 1
    )
))::text
from exam.question_candidates c
left join exam.official_documents d on d.registry_key = c.source_registry_key
where c.id in (__IDS__)
order by c.id;
"""


def _run(sql, timeout=300):
    try:
        process = subprocess.run(
            ["docker", "exec", "-i", PROBE, "psql", "-At", "-U", USER, "-d", DB, "-f", "-"],
            input=sql, capture_output=True, text=True, timeout=timeout,
        )
    except Exception as error:
        return "", "%s" % error
    return process.stdout or "", process.stderr or ""


def coarse_ids(categories, only_gold=True, restrict_ids=None):
    """在「源頭（DB 端）」即「分離提純」：僅取 3 類前綴匹配者（可選「必須有人工修正」）。

    「宏觀辨識」：原流程「先取全部 35,496，而後於 Python 端過濾 3 類」，其弊在於
    fetch_meta 之「related 子查詢」須「每行重掃」question_review_events。今將「category
    前綴匹配」與「human 之有無（EXISTS，布爾式）」皆推入 SQL 端，使 DB 一次性完成過濾，
    Python 僅接收候選者——「從源頭減少污染」（減少無用之序列化與查詢）。是乃「綠色化學」
    「分離與提純」之「順序」顛倒也。

    「微觀辨析」：restrict_ids 非空時，僅限於該 id 集之內（對照「實驗」用）。返回 set。
    與 fetch_meta 合用：先粗篩（本函數，得候選者），後精製（fetch_meta 僅取此集）。
    """
    wanted = sorted({int(i) for i in (restrict_ids or []) if str(i).strip().isdigit()})
    if wanted:
        idlist = ",".join(str(i) for i in wanted)
        id_filter = "and c.id in (%s)" % idlist
    else:
        id_filter = ""
    cats = tuple(categories or ())
    if not cats:
        return set(wanted)
    likes = " or ".join("d.official_category_name like '%s%%'" % c.replace("'", "''") for c in cats)
    gold = (
        " and exists (select 1 from exam.question_review_events e"
        " where e.candidate_id = c.id and e.corrected_candidate_json is not null)"
        if only_gold else ""
    )
    sql = ("select c.id from exam.question_candidates c "
           "left join exam.official_documents d on d.registry_key = c.source_registry_key "
           "where (" + likes + ")" + gold + id_filter + " order by c.id;")
    stdout, stderr = _run(sql)
    if not stdout.strip():
        print("coarse_ids: psql returned nothing; stderr=%s" % (stderr or "")[:400])
        return set()
    return {int(x) for x in stdout.split() if x.isdigit()}


def fetch_meta(ids):
    """{candidate_id: {category, subject, human, ...}} for the given candidate ids."""
    wanted = sorted({int(i) for i in ids if str(i).strip().isdigit()})
    if not wanted:
        return {}
    chunks, size = [], 400
    for start in range(0, len(wanted), size):
        chunks.append(wanted[start:start + size])
    out = {}
    for chunk in chunks:
        sql = META_SQL.replace("__IDS__", ",".join(str(i) for i in chunk))
        stdout, stderr = _run(sql)
        if not stdout.strip():
            print("psql returned nothing; stderr=%s" % (stderr or "")[:400])
            continue
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                blob = json.loads(line)
            except ValueError as error:
                print("bad row skipped: %s | %s" % (type(error).__name__, str(error)[:80]))
                continue
            ident = blob.get("id")
            if ident is None:
                continue
            out[int(ident)] = blob
    return out


def counts():
    """Row counts of the copied tables, for integrity checks against provenance.json."""
    sql = """
select 'question_candidates', count(*) from exam.question_candidates
union all select 'question_review_events', count(*) from exam.question_review_events
union all select 'answer_review_events', count(*) from exam.answer_review_events
union all select 'questions', count(*) from exam.questions
union all select 'question_options', count(*) from exam.question_options
union all select 'answers', count(*) from exam.answers
union all select 'formal_questions', count(*) from exam_staging.formal_questions
order by 1;
"""
    stdout, stderr = _run(sql)
    if not stdout.strip():
        print("psql returned nothing; stderr=%s" % (stderr or "")[:400])
    return [line.split("|") for line in stdout.splitlines() if "|" in line]


def status_report():
    """The distribution of review/quality states, as recorded."""
    sql = """
select coalesce(review_status,'-'), coalesce(quality_status,'-'), count(*)
from exam.question_candidates group by 1,2 order by 3 desc limit 12;
"""
    stdout, _stderr = _run(sql)
    return [line.split("|") for line in stdout.splitlines() if "|" in line]
