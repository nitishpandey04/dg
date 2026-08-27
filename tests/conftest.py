"""Hypothesis settings profiles.

Default 'quick' keeps the whole suite snappy. Run deeper sessions with:

    DG_FUZZ_PROFILE=deep uv run pytest
"""

import os

from hypothesis import HealthCheck, settings

_SHRINK_SAFETY = [HealthCheck.data_too_large, HealthCheck.too_slow]

settings.register_profile(
    "quick",
    max_examples=50,
    stateful_step_count=40,
    deadline=None,
    suppress_health_check=_SHRINK_SAFETY,
)
settings.register_profile(
    "deep",
    max_examples=400,
    stateful_step_count=80,
    deadline=None,
    suppress_health_check=_SHRINK_SAFETY,
)

settings.load_profile(os.environ.get("DG_FUZZ_PROFILE", "quick"))
