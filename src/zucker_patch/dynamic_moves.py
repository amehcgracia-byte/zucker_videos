"""Compatibility wrapper for the core dynamic motion planner.

The implementation lives in core so the packaged application can import it.
The src copy remains as the review/test entry point.
"""

from core.dynamic_moves import *  # noqa: F401,F403
