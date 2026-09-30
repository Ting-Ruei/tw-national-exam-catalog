# -*- coding: utf-8 -*-
"""Render a question's text the way the **platform** will show it to a learner.

The designer's requirement (2026-09-28):

> 這題在考題平台會是以什麼方式被我看到，所以斜體、上下標都應該是直接呈現出來
> （我不應該看到 `<sup>` 這種東西）

That is a **contract, not a style preference**. The platform renders question text through
`safeHtml()` and wraps it in `.study-prose`:

| step | where |
|---|---|
| `DOMPurify.sanitize(normalized, {ALLOWED_TAGS: [... "sup", "sub", "em", "strong" ...]})` | `platform-app/frontend-next/lib/sanitize.ts` |
| `dangerouslySetInnerHTML={{__html: safeHtml(q.stem)}}` | `components/question/QuestionCard.tsx`, `OptionList.tsx` |
| `.study-prose sup, .study-prose sub { font-size: 0.78em }` | `app/globals.css` |

Until this module existed the review UI printed everything through `esc()`, so the designer read
`H<sub>2</sub>O` instead of H₂O — the UI was showing the *storage format*, not the question. A
reviewer who cannot see the rendered form cannot judge the thing that ships.

## Two rules this module is built on

**1. The allowlist is read from the platform's file, never copied.** A second list is a second thing
that can disagree, and the disagreement would be invisible until a learner saw something a reviewer
did not. `load_allowlist()` parses `lib/sanitize.ts` at call time. If that file moves or changes
shape, `platform_view` raises instead of falling back to a remembered list — a stale allowlist is
worse than none, because it looks like it works.

**2. `normalizeScientificMarkup` is reproduced exactly, including its order.** The platform turns
`$...$` LaTeX-ish spans and bare `_x` / `^x` into `<sub>`/`<sup>` *before* sanitizing. Rendering the
same string differently in the review UI would mean the reviewer approves a question that the learner
sees differently — the exact class of defect this whole project exists to catch.

Not reproduced: `isomorphic-dompurify` itself (a JS dependency). `sanitize()` below is a strict
allowlist implementation with the same tag/attribute lists. It is stricter than DOMPurify in that it
drops anything it does not positively recognise, which is the safe direction for a reviewer view.
"""
from __future__ import annotations

import html
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(HERE)
CATALOG = os.path.dirname(os.path.dirname(AGENT_DIR))
PLATFORM_ROOT = os.path.join(os.path.dirname(CATALOG), "platform-app", "frontend-next")

# The laptop layout resolves the platform by sitting beside it in the umbrella repo. The
# station's code tree is deployment-only (`~/qbr-review/code`) and carries **no** platform-app,
# so a station deployment that starts the sandbox points `PLATFORM_SANITIZE_TS` at the copy the
# deploy script ships into `~/qbr-review/assets/`. One file, one origin, no second allowlist.
SANITIZE_TS = os.environ.get("PLATFORM_SANITIZE_TS") \
    or os.path.join(PLATFORM_ROOT, "lib", "sanitize.ts")


def _parse_string_array(source: str, name: str) -> list[str]:
    """Pull `NAME: ["a", "b", ...]` out of the TypeScript source."""
    match = re.search(r"\b%s\s*:\s*\[(.*?)\]" % re.escape(name), source, re.S)
    if not match:
        raise RuntimeError(
            "could not find %s in %s. The platform's allowlist is the authority for how a question "
            "renders; if this file moved, point SANITIZE_TS at it rather than writing a second list."
            % (name, SANITIZE_TS)
        )
    return re.findall(r'"([^"]+)"', match.group(1))


def load_allowlist() -> tuple[set[str], set[str]]:
    """The platform's own tag/attribute allowlist, read from its source."""
    if not os.path.exists(SANITIZE_TS):
        raise RuntimeError(
            "the platform's sanitize.ts is not at %s. It defines which markup reaches a learner's "
            "screen; the review UI must render what the platform renders, so this is required "
            "rather than optional. Away from the laptop's repo layout (the station), point the "
            "PLATFORM_SANITIZE_TS environment variable at the deployed copy." % SANITIZE_TS
        )
    with open(SANITIZE_TS, encoding="utf-8") as handle:
        source = handle.read()
    tags = set(_parse_string_array(source, "ALLOWED_TAGS"))
    attrs = set(_parse_string_array(source, "ALLOWED_ATTR"))
    return tags, attrs


# Reproduced from `lib/sanitize.ts`. Same table, same order of application — the platform applies
# these before sanitizing, so a difference here is a difference in the rendered question.
GREEK_MAP = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "theta": "θ",
    "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ", "omega": "ω",
    "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Pi": "Π", "Sigma": "Σ", "Omega": "Ω",
}


def _normalize_inline_math(expr: str) -> str:
    out = expr.strip()
    out = re.sub(r"\\([A-Za-z]+)", lambda m: GREEK_MAP.get(m.group(1), m.group(0)), out)
    out = re.sub(r"([A-Za-z0-9)\]])\_\{([^{}]+)\}", r"\1<sub>\2</sub>", out)
    out = re.sub(r"([A-Za-z0-9)\]])\^\{([^{}]+)\}", r"\1<sup>\2</sup>", out)
    out = re.sub(r"([A-Za-z0-9)\]])\_([A-Za-z0-9+\-])", r"\1<sub>\2</sub>", out)
    out = re.sub(r"([A-Za-z0-9)\]])\^([A-Za-z0-9+\-])", r"\1<sup>\2</sup>", out)
    out = out.replace("\\times", "×").replace("\\cdot", "·")
    return out


def normalize_scientific_markup(raw: str) -> str:
    """Same as the platform's `normalizeScientificMarkup`."""
    out = re.sub(r"\$([^$]+)\$", lambda m: _normalize_inline_math(m.group(1)), raw)
    out = re.sub(r"\\([A-Za-z]+)", lambda m: GREEK_MAP.get(m.group(1), m.group(0)), out)
    return out


_TAG_RE = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)((?:\s+[^<>]*?)?)\s*(/?)\s*>")
_ATTR_RE = re.compile(r'([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*"([^"]*)"')
# Attributes whose value is a URL. `javascript:` and `data:` in an `href` are script execution in a
# reviewer's browser, so they are refused even though the platform's list allows the attribute name.
_URL_ATTRS = {"src", "href"}
_SAFE_URL = re.compile(r"^\s*(?:https?:|/|\.{0,2}/)",
                       re.I)
_VOID_TAGS = {"br", "img", "hr", "input", "col"}


def sanitize(raw: str) -> str:
    """Allowlist sanitizer with the platform's tag/attribute lists.

    Text outside a recognised tag is escaped. Any tag not on the platform's list is dropped (its
    text content survives, because dropping the content too would hide a question's words from the
    reviewer). Any attribute not on the platform's list is dropped; URL attributes must also be a
    plain http(s) or root-relative path.
    """
    tags, attrs = load_allowlist()
    out: list[str] = []
    last = 0
    for match in _TAG_RE.finditer(raw):
        out.append(html.escape(raw[last:match.start()], quote=False))
        last = match.end()
        closing, name, attr_text, self_closing = match.groups()
        name = name.lower()
        if name not in tags:
            continue  # not on the platform's list -> the tag itself does not render
        if closing:
            if name not in _VOID_TAGS:
                out.append("</%s>" % name)
            continue
        kept = []
        for key, value in _ATTR_RE.findall(attr_text or ""):
            key = key.lower()
            if key not in attrs:
                continue
            if key in _URL_ATTRS and not _SAFE_URL.match(value or ""):
                continue
            kept.append(' %s="%s"' % (key, html.escape(value, quote=True)))
        out.append("<%s%s%s>" % (name, "".join(kept), " /" if self_closing or name in _VOID_TAGS else ""))
    out.append(html.escape(raw[last:], quote=False))
    return "".join(out)


def as_platform_html(raw) -> str:
    """The string the platform will put on a learner's screen, for this question field.

    `None`/empty stay empty; anything else is normalized then sanitized, in that order (the platform
    normalizes first, so a `_x` it turns into `<sub>x</sub>` is then allowed through by name).
    """
    if raw is None:
        return ""
    text = str(raw)
    if not text:
        return ""
    return sanitize(normalize_scientific_markup(text))


def plain_text(raw) -> str:
    """The same content with markup removed — for search, diffing and log lines.

    Not a second definition of the question: it is the rendered form with tags stripped, so a
    character that only exists as markup (`<sub>`) never shows up as a difference in a text diff.
    """
    rendered = as_platform_html(raw)
    return html.unescape(re.sub(r"<[^>]+>", "", rendered))
