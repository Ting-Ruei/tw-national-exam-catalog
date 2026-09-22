# -*- coding: utf-8 -*-
"""A wrong thinking switch is accepted with HTTP 200 and silently ignored.

That is the whole reason this file exists. Measured on Splash (`8088`), the same question, three
requests:

    default                                        31.8s   reasoning_chars=612
    reasoning_effort: none                          6.7s   reasoning_chars=0
    chat_template_kwargs (enable_thinking: False)  31.5s   reasoning_chars=612

So there is no error to catch, no empty response, no exception - only a run that takes 4.7x as long
and a `reasoning` block nobody looked at. `reread.transcribe` had the MTPLX spelling hard-coded and
was pointed at Splash, which is exactly how the bug shipped.

Every test here is about there being **one** copy of the switch, stored with the engine it belongs
to, and about that copy being reachable by both callers. The negative controls are written so they
fire on the old arrangement (a per-module copy) rather than on a detail.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "src"))
sys.path.insert(0, os.path.join(PKG, "scripts"))

from qbr import engines  # noqa: E402


# ------------------------------------------------------------------ the switch is per engine

def test_every_engine_declares_exactly_one_way_to_stop_thinking():
    # Sending both spellings is how the ignored one hides: the engine takes the one it knows, ignores
    # the other, and the request still looks correct in a log. So it is one or the other, never both.
    for name, endpoint in engines.ENDPOINTS.items():
        has_reasoning = bool(endpoint.get("reasoning"))
        has_thinking = bool(endpoint.get("thinking"))
        assert has_reasoning != has_thinking, "%s declares %r/%r" % (
            name, endpoint.get("reasoning"), endpoint.get("thinking"))


def test_an_engine_with_no_switch_raises_rather_than_running_with_thinking_on():
    # The dangerous default is silence. An engine added later without deciding its switch must fail
    # loudly, because the failure mode it would otherwise have is invisible (above).
    with_switch = dict(engines.ENDPOINTS["splash"])
    bare = {"name": "x", "url": "http://x", "key": ""}
    try:
        engines.body_for(bare, [{"role": "user", "content": "hi"}], max_tokens=10)
    except ValueError as exc:
        assert "thinking" in str(exc)
    else:
        raise AssertionError("an engine with no thinking control was accepted")
    # And the control: with a switch it does not raise, so the test above is about the missing switch.
    assert engines.body_for(with_switch, [{"role": "user", "content": "hi"}], max_tokens=10)


def test_splash_is_asked_with_reasoning_effort_and_not_the_mtplx_spelling():
    # The measured pair: these two produce 6.7s and 31.5s on the same question, and the second one is
    # the one that `reread` used to send.
    body = engines.body_for(engines.ENDPOINTS["splash"], [{"role": "user", "content": "x"}],
                            max_tokens=10)
    assert body["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in body


def test_the_negative_control_the_mtplx_body_would_be_the_wrong_one_for_splash():
    # Negative control: the MTPLX body has the other field, so an assertion that Splash's body
    # carries `reasoning_effort` is a real statement about the table and not about any body at all.
    mtplx = engines.body_for(engines.ENDPOINTS["mtplx-35b"], [{"role": "user", "content": "x"}],
                             max_tokens=10)
    assert "reasoning_effort" not in mtplx
    assert mtplx["chat_template_kwargs"] == {"enable_thinking": False}


# ------------------------------------------------------------------ one copy, two callers

def test_reread_builds_its_request_from_the_shared_table_not_a_local_copy():
    # `reread` is a library module and cannot import a script (that would run the script's argparse
    # on import), so before this the switch was written out locally - a second copy, which is the
    # bug. It must now go through `engines`, which is the one copy.
    source = open(os.path.join(PKG, "src", "qbr", "reread.py"), encoding="utf-8").read()
    body = _function_body(source, "transcribe")
    assert "engines.body_for" in body
    assert "engines.endpoint_url" in body
    # And the wrong spelling must not appear in the request at all, not even in a literal.
    assert "chat_template_kwargs" not in body
    assert "enable_thinking" not in body
    assert "_thinking_off_body" not in body


def test_the_negative_control_a_local_switch_copy_would_be_found():
    # Negative control: the old shape really does contain the strings the test above forbids, so the
    # test is checking for a real thing. (This is the line that used to be in `reread.transcribe`.)
    old_body = ("    body = {}\n"
                "    body.update(vision._thinking_on_body() if think else vision._thinking_off_body())\n")
    assert "_thinking_off_body" in old_body
    source = open(os.path.join(PKG, "src", "qbr", "reread.py"), encoding="utf-8").read()
    assert "_thinking_off_body" not in _function_body(source, "transcribe")


def _function_body(source: str, name: str) -> str:
    """One function's live body, from a module's source.

    A whole-file search finds prose about a rule as readily as the rule itself - and this module's own
    docstrings discuss both spellings, because explaining why they must not be duplicated is the
    point. So the check has to read the **code**, which means cutting out the function and dropping
    its docstring (a string literal, not an instruction).
    """
    match = re.search(r"\ndef %s\(.*?(?=\ndef |\Z)" % name, source, re.S)
    assert match, "no function %s in the source" % name
    body = match.group(0)
    # Drop the docstring, which is prose about the rule and not the rule.
    body = re.sub(r'""".*?"""', "", body, count=1, flags=re.S)
    return "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith("#"))


def test_the_scripts_use_the_same_table_object_rather_than_a_retyped_copy():
    # `ask_about_blocks.ENDPOINTS` is what the corpus pass and every `--model` flag resolve through.
    # A retyped literal there would be a third copy that could drift from the table the transcription
    # uses, which is the whole failure this file is about.
    import ask_about_blocks
    assert ask_about_blocks.ENDPOINTS is engines.ENDPOINTS
    source = open(os.path.join(PKG, "scripts", "ask_about_blocks.py"), encoding="utf-8").read()
    # The request builder must not spell a switch itself; `engines.ask` applies it.
    body = _function_body(source, "ask")
    assert "engines.ask" in body
    assert "reasoning_effort" not in body
    assert "enable_thinking" not in body
    assert "chat_template_kwargs" not in body


def test_the_negative_control_a_retyped_table_would_contain_the_switch():
    # Negative control for the above: a retyped table contains the switch spelling, so "not in the
    # source" is a statement with content.
    retyped = 'ENDPOINTS = {"splash": {"url": "http://127.0.0.1:8088", "reasoning": "none"}}'
    assert "reasoning_effort" not in retyped  # the switch spelling is applied in body_for, not stored
    assert "reasoning" in retyped


# ------------------------------------------------------------------ the address is still a parameter

def test_a_port_remains_a_parameter_not_a_literal_in_the_request_builder():
    # "A port is a parameter" - the builder must take the URL from the endpoint it is given, so
    # pointing this at another host is a matter of the environment and not of editing the code.
    moved = {**engines.ENDPOINTS["splash"], "url": "http://192.168.10.90:9999"}
    assert engines.endpoint_url(moved["url"]).startswith("http://192.168.10.90:9999/v1/")
    body = engines.body_for(moved, [{"role": "user", "content": "x"}], max_tokens=10)
    assert body["model"] == engines.ENDPOINTS["splash"]["name"]


def test_reread_can_be_pointed_at_the_other_engine_without_keeping_the_wrong_switch():
    # A caller who moves `reread` to MTPLX must get MTPLX's switch. `QBR_REREAD_ENGINE` selects the
    # engine, so the switch travels with the name instead of being pinned to whatever the module was
    # first written against.
    import subprocess
    probe = (
        "import os, sys, json;"
        "sys.path.insert(0, os.path.join(os.getcwd(), 'src'));"
        "from qbr import engines, reread;"
        "e = reread.endpoint();"
        "print(json.dumps({'name': e['name'],"
        " 'body': engines.body_for(e, [{'role':'user','content':'x'}], max_tokens=10)}))"
    )
    env = {**os.environ, "QBR_REREAD_ENGINE": "mtplx-35b"}
    out = subprocess.run([sys.executable, "-c", probe], cwd=PKG, env=env,
                         capture_output=True, text=True, check=True).stdout
    body = __import__("json").loads(out)["body"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in body


def test_the_negative_control_the_default_engine_is_not_mtplx():
    # Negative control: if the default were still MTPLX the test above would pass for the wrong
    # reason (the switch would match without any engine selection happening).
    source = open(os.path.join(PKG, "src", "qbr", "reread.py"), encoding="utf-8").read()
    match = re.search(r'QBR_REREAD_ENGINE",\s*"([^"]+)"', source)
    assert match, "reread no longer names a default engine"
    assert match.group(1) == "splash"
