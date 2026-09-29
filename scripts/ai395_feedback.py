"""Compatibility import for the retired review-feedback module.

New code must import :mod:`review_feedback`. This filename remains only so old
archived fixtures fail safely through the same implementation rather than
creating a second feedback contract.
"""

from review_feedback import *  # noqa: F401,F403
