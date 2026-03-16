"""Global test fixtures."""

import pytest

from skill_manager.core.discovery import invalidate_cache


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    invalidate_cache()
    yield
    invalidate_cache()
