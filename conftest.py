"""Root conftest: shared fixtures for the Bridge test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
