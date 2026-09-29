"""Compatibility import for the retired audit-scope filename.

New workflows must import or execute :mod:`build_local_audit_scope`.
"""

from build_local_audit_scope import *  # noqa: F401,F403
