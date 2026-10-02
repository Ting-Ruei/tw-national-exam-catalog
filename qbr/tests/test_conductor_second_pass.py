# -*- coding: utf-8 -*-
"""The conductor second pass: the leader model's first measured job, and the record must be honest.

Contract under test (owner 2026-10-01「全做」)：
- 判分後寫出 advisory 行，**queue 的檔一個 byte 都不動**；
- CARE/DOUBT 的第二眼看得到的話要帶上那張裁片（`look_image: true`）；
- 指揮者回答不可用 ⇒ degraded 行，**不得預設 TRUST**（orchestrator.parse_judgement 的契約）；
- pin 與 `/v1/models` 不符 ⇒ 拒跑（exit 2），先把 `QBR_DGX_MODEL` 改對。
"""
import json
import os
import sys
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import engines, orchestrator  # noqa: E402
import conductor_second_pass  # noqa: E402

sys.path.insert(0, HERE)
from test_engines import _stub_engine_server  # noqa: E402

GLM = "GLM-5.3-Flash-EXL3"

_CANDIDATE = {
    "candidate_key": "moex:107100:305:33:1:question:q051",
    "question_number": "q051", "subject": "藥劑學與生物藥劑學", "paper": "115090:305",
    "stem": "某男性病人以靜脈注射投與抗生素300 mg……ke是多少h⁻¹？",
    "options": [{"key": "A", "text": "0.16"}, {"key": "B", "text": "0.34"}],
}

_FINDING_ROW = {
    "schema": "ai_findings v2", "candidate_key": _CANDIDATE["candidate_key"],
    "question_number": "q051", "paper": "115090:305", "subject": "藥劑學與生物藥劑學",
    "population": "category-scan",
    "finding": {"verdict": "DEFECT", "what": None,
                "where": "stem：紙本是 dDu/dt = 49e^-0.34t，抽取沒有公式", "fix": "補回公式",
                "confidence": 0.8},
    "crop": "review-ui/crops/1072_藥師(一)_藥劑學與生物藥劑學_q051.png",
}


def _triage_body(verdict, why="一句話", self_look=False):
    return {"verdict": verdict, "why": why, "self_look": self_look}


def _chat_payload(obj):
    return {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}],
            "model": GLM}


def _stub(responder):
    return _stub_engine_server(responder)


def _responder(scripts):
    """`scripts`: list of (matcher, (status, payload)) — matched in order on POST bodies."""
    def handle(method, path, body):
        if method == "GET":
            return 200, {"data": [{"id": GLM}]}
        text = body.decode("utf-8", "replace")
        for matcher, (status, payload) in scripts:
            if matcher in text:
                return status, payload
        return 500, {"error": "no canned answer"}
    return handle


def _write_fixture(tmp, crop=False):
    queue = os.path.join(tmp, "queue")
    os.makedirs(os.path.join(queue, "review-ui", "crops"), exist_ok=True)
    with open(conductor_second_pass.candidates_path(queue), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_CANDIDATE, ensure_ascii=False) + "\n")
    if crop:
        with open(os.path.join(queue, _FINDING_ROW["crop"]), "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")  # enough for crop_image_part to say there is a picture
    findings = os.path.join(tmp, "findings.jsonl")
    with open(findings, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_FINDING_ROW, ensure_ascii=False) + "\n")
    out = os.path.join(tmp, "second_pass.jsonl")
    return queue, findings, out


def _run(queue, findings, out, extra=None):
    argv = ["--findings", findings, "--queue", queue, "--out", out] + (extra or [])
    rc = conductor_second_pass.main(argv)
    rows = [json.loads(line) for line in open(out, encoding="utf-8")] if os.path.exists(out) else []
    return rc, rows


def _stubbed_endpoint_env(server):
    import contextlib

    @contextlib.contextmanager
    def env():
        previous = os.environ.get("QBR_ENGINE_DGX_FLASH_URL")
        os.environ["QBR_ENGINE_DGX_FLASH_URL"] = "http://127.0.0.1:%d" % server.server_port
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("QBR_ENGINE_DGX_FLASH_URL", None)
            else:
                os.environ["QBR_ENGINE_DGX_FLASH_URL"] = previous
    return env()


def test_a_trusted_triage_writes_one_advisory_row_and_touches_no_queue_file():
    import tempfile
    tmp = tempfile.mkdtemp()
    queue, findings, out = _write_fixture(tmp)
    candidates = conductor_second_pass.candidates_path(queue)
    before = open(candidates, "rb").read()
    server = _stub(_responder([("dispute_kinds", (200, _chat_payload(
        _triage_body("TRUST", self_look=False))))]))
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, findings, out)
    server.shutdown()
    assert rc == 0
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "conductor_second_pass v1"
    assert row["record"]["verdict"] == "TRUST"
    assert "second_look" not in row["record"]  # trusted: no second look was asked for
    assert row["served_model"] == GLM
    expected_sha8 = hashlib.sha256(open(findings, "rb").read()).hexdigest()[:8]
    assert row["source"]["findings_sha8"] == expected_sha8
    assert row["source"]["row"] == 1
    assert open(candidates, "rb").read() == before  # advisory only: the queue did not move


def test_a_doubt_verdict_second_look_carries_the_crop():
    import tempfile
    tmp = tempfile.mkdtemp()
    queue, findings, out = _write_fixture(tmp, crop=True)
    second_seen = []

    def handle(method, path, body):
        if method == "GET":
            return 200, {"data": [{"id": GLM}]}
        text = body.decode("utf-8", "replace")
        if "image_url" in text:
            second_seen.append(True)
            return 200, _chat_payload({"page_read": {"stem": "dDu/dt = 49e^-0.34t"},
                                       "disagrees_with_local": True, "why": "紙本有公式"})
        return 200, _chat_payload(_triage_body("DOUBT", self_look=False))  # self_look forced true

    server = _stub(handle)
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, findings, out)
    server.shutdown()
    assert rc == 0
    record = rows[0]["record"]
    assert record["verdict"] == "DOUBT"
    assert record["self_look"] is True   # a doubting verdict that does not look is corrected
    assert record["look_image"] is True
    assert second_seen == [True]         # the crop really was sent
    assert record["second_look"]["disagrees_with_local"] is True


def test_an_unusable_answer_is_a_degraded_row_never_a_defaulted_trust():
    import tempfile
    tmp = tempfile.mkdtemp()
    queue, findings, out = _write_fixture(tmp)
    server = _stub(_responder([("dispute_kinds", (200, _chat_payload({"answer": "我相信它"})))]))
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, findings, out)
    server.shutdown()
    assert rc == 0
    record = rows[0]["record"]
    assert "verdict" not in record         # THE negative control: no verdict is invented
    assert record["degraded"] is True
    assert "JSON" in (rows[0]["note"] or "")


def test_a_pin_that_disagrees_with_the_endpoint_refuses_the_run():
    import tempfile
    tmp = tempfile.mkdtemp()
    queue, findings, out = _write_fixture(tmp)
    server = _stub(lambda m, p, b: (200, {"data": [{"id": "next-leader-v2"}]}))
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, findings, out)
    server.shutdown()
    assert rc == 2                          # stopped before the first record is written
    assert rows == []                       # nothing was written under a lying pin


def test_with_evidence_mode_sends_the_pack_and_the_crop_in_one_call():
    import tempfile
    tmp = tempfile.mkdtemp()
    queue, findings, out = _write_fixture(tmp, crop=True)
    posts = []

    def handle(method, path, body):
        if method == "GET":
            return 200, {"data": [{"id": GLM}]}
        posts.append(body.decode("utf-8", "replace"))
        # The first and only POST must already carry the evidence: page transcription + the crop.
        assert "page_read" in posts[0] or '"transcription"' in posts[0]
        assert "image_url" in posts[0]
        return 200, _chat_payload(_triage_body("DOUBT", self_look=False))

    server = _stub(handle)
    with _stubbed_endpoint_env(server):
        rc, rows = _run(queue, findings, out, extra=["--with-evidence"])
    server.shutdown()
    assert rc == 0 and len(posts) == 1   # one call, no separate second look
    record = rows[0]["record"]
    assert record["verdict"] == "DOUBT"
    assert record["evidence_look"] is True


def test_the_conductor_lane_resolves_through_the_selector():
    # The env override must reach the named table (same precedence as every other lane), and an
    # unknown name must fail loudly rather than fall back silently.
    import contextlib

    @contextlib.contextmanager
    def env(name, value):
        previous = os.environ.get(name)
        os.environ[name] = value
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

    assert orchestrator.conductor_endpoint()["name"] == "GLM-5.3-Flash-EXL3"
    with env("QBR_CONDUCTOR_ENGINE", "occamy-6bit"):
        assert orchestrator.conductor_endpoint()["url"] == "http://127.0.0.1:18130"
    with env("QBR_CONDUCTOR_ENGINE", "no-such-lane"):
        try:
            orchestrator.conductor_endpoint()
            assert False, "an unknown lane must raise, not fall back"
        except KeyError:
            pass