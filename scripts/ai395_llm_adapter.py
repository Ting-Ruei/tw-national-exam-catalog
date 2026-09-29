"""Retired model adapter; no external or host-specific provider is active."""

from __future__ import annotations


def _retired(*args, **kwargs):
    raise RuntimeError("retired: create a new approved local model contract before using an adapter")


build_request = _retired
build_native_request = _retired
build_packet = _retired
