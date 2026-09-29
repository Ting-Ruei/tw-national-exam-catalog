# Retired review workflow console

Status: retired and non-authoritative.

This document described an external staging console and model routes that are no
longer configured. Do not use its commands, provider names, endpoint variables,
workflow, database, or deployment assumptions.

The current interface baseline is `review_ui/v2.html`. Run it only through the
local review contract for the current task; keep review events append-only and
require a human decision for every accept/block/correction outcome.
