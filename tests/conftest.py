"""Shared test fixtures."""

import pytest

import ratelimit


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """Throttle state is module-level, so one test must never spend another's budget."""
    ratelimit.reset()
    yield
    ratelimit.reset()
